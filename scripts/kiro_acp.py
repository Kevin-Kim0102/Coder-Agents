#!/usr/bin/env python3
"""Minimal, read-only ACP (JSON-RPC over stdio) client for Kiro's agent server (KAS).

Used by the panel for two things that never send a model prompt (so nothing is billed as a model call):
`_kiro/account/getUsage` (account credits) and a catalog probe (`session/new|load` configOptions).

Credentials: KAS is started with `--auth=acp-callback`; when it asks for a token (`_kiro/auth/getAccessToken`)
the token comes from `kiro-cli chat _ get-kas-token` and is handed straight to the server over the pipe.
It is never logged, never returned to callers and never written to disk by this module. Server stderr is
discarded, and every error text goes through `scrub()`.
"""
import glob
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

AUTH_METHOD = "_kiro/auth/getAccessToken"
SERVER_GLOB = "kas/*/node_modules/@kiro/agent/dist/server/acp-server.js"


class AcpError(Exception):
    """A failure with a stable machine code and a message that is safe to show (already scrubbed)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = scrub(message)


_SECRETS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)((?:access|refresh|id)?_?token\"?\s*[:=]\s*\"?)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.?[A-Za-z0-9_-]*"),
    re.compile(r"[A-Za-z0-9+/_=-]{48,}"),
]


def scrub(text):
    """Remove anything that looks like a credential from a diagnostic string."""
    text = str(text)
    text = _SECRETS[0].sub(r"\1[已隐藏]", text)
    text = _SECRETS[1].sub(r"\1[已隐藏]", text)
    text = _SECRETS[2].sub("[已隐藏]", text)
    text = _SECRETS[3].sub("[已隐藏]", text)
    return text[:600]


def data_directory(environment):
    """Kiro's platform data root, honoring an explicit XDG override."""
    if environment.get("XDG_DATA_HOME"):
        return Path(environment["XDG_DATA_HOME"]) / "kiro-cli"
    home = Path(environment.get("HOME") or Path.home())
    base = home / "Library/Application Support" if sys.platform == "darwin" else home / ".local/share"
    return base / "kiro-cli"


def find_server_js(environment=None):
    """Path of the installed KAS entry point (newest installed version), or None."""
    environment = os.environ if environment is None else environment
    explicit = environment.get("KIRO_KAS_SERVER_JS")
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    data = data_directory(environment)
    found = sorted(glob.glob(str(data / SERVER_GLOB)), key=lambda p: (os.path.getmtime(p), p))
    return found[-1] if found else None


def server_command(environment):
    """argv that starts KAS on stdio with host-mediated auth, or raises AcpError."""
    node = environment.get("KIRO_RELAY_REAL_NODE") or environment.get("KIRO_KAS_NODE_PATH")
    if not node:
        node = str(data_directory(environment) / "node")
    server = find_server_js(environment)
    if not os.access(node, os.X_OK):
        raise AcpError("kas_missing", "找不到 Kiro 的 Node 运行时，无法读取账户信息")
    if not server:
        raise AcpError("kas_missing", "找不到已安装的 Kiro agent 服务（acp-server.js）")
    return [node, "--experimental-wasm-modules", server, "--transport=stdio", "--auth=acp-callback"]


def find_cli(environment):
    """kiro-cli location: KIRO_CLI, then PATH, then ~/.local/bin (a systemd user service has a short PATH)."""
    import shutil
    explicit = environment.get("KIRO_CLI")
    if explicit:
        return explicit
    return shutil.which("kiro-cli", path=environment.get("PATH")) or str(Path.home() / ".local/bin/kiro-cli")


def cli_token(environment, timeout=30):
    """Token record from `kiro-cli chat _ get-kas-token` (the CLI's own refresh path). Never logged."""
    binary = find_cli(environment)
    try:
        done = subprocess.run([binary, "chat", "_", "get-kas-token"], env=environment, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise AcpError("auth_failed", "找不到 kiro-cli，无法获取登录凭据")
    except subprocess.TimeoutExpired:
        raise AcpError("timeout", "获取 Kiro 登录凭据超时，请检查网络或代理")
    if done.returncode:
        raise AcpError("auth_failed", "无法获取 Kiro 登录凭据，请在终端运行 kiro-cli login 后重试")
    try:
        data = json.loads(done.stdout)["data"]
        if not isinstance(data, dict) or not data.get("accessToken"):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise AcpError("auth_failed", "Kiro 登录凭据的格式无法识别，请重新登录")
    return data


class AcpClient:
    """One short-lived server process. Single consumer: call request()/wait_notification() from one thread."""

    def __init__(self, command, environment, token_provider=None, clock=time.monotonic):
        self.command = list(command)
        self.environment = environment
        self.token_provider = token_provider or (lambda: cli_token(environment))
        self.clock = clock
        self.process = None
        self.lines = queue.Queue()
        self.notifications = []
        self.next_id = 0
        self.auth_requests = 0
        self.auth_error = None

    def start(self):
        try:
            self.process = subprocess.Popen(self.command, env=self.environment, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise AcpError("kas_missing", "无法启动 Kiro agent 服务：" + (error.strerror or str(error)))
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        try:
            for raw in self.process.stdout:
                try:
                    self.lines.put(json.loads(raw))
                except ValueError:
                    continue
        finally:
            self.lines.put(None)

    def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise AcpError("server_exited", "Kiro agent 服务意外退出")

    def pump(self, deadline):
        """Next message from the server, with host callbacks answered; None on timeout."""
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            try:
                message = self.lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if message is None:
                raise AcpError("server_exited", "Kiro agent 服务在应答前退出")
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if method and "id" in message:  # server -> host request
                self.answer_request(message)
                continue
            if method:
                self.notifications.append(message)
                del self.notifications[:-200]
            return message

    def answer_request(self, message):
        if message.get("method") == AUTH_METHOD:
            self.auth_requests += 1
            try:
                token = self.token_provider()
            except AcpError as error:
                self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": error.message}})
                self.auth_error = error
                return
            self.send({"jsonrpc": "2.0", "id": message["id"], "result": token})
            return
        self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}})

    def request(self, method, params, timeout=30):
        self.next_id += 1
        request_id = self.next_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = self.clock() + timeout
        while True:
            message = self.pump(deadline)
            if message is None:
                raise AcpError("timeout", "等待 Kiro 服务响应超时")
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message["error"] if isinstance(message["error"], dict) else {}
                    if self.auth_error is not None:
                        raise self.auth_error
                    raise AcpError("request_failed", "Kiro 服务返回错误：" + str(error.get("message", "未知错误")))
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def wait_notification(self, predicate, timeout):
        """First already-seen or future notification satisfying `predicate`, else None."""
        for message in self.notifications:
            if predicate(message):
                return message
        deadline = self.clock() + timeout
        while True:
            message = self.pump(deadline)
            if message is None:
                return None
            if "method" in message and predicate(message):
                return message

    def initialize(self, timeout=30):
        return self.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                           "clientInfo": {"name": "kiro-panel", "version": "1"}}, timeout)

    def close(self):
        process = self.process
        if not process:
            return
        for stream in (process.stdin,):
            try:
                stream.close()
            except OSError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        try:
            process.stdout.close()
        except OSError:
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
