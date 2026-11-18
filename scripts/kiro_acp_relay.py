#!/usr/bin/env python3
"""Stdio ACP relay placed between Kiro CLI and the Kiro agent server (KAS).

Kiro CLI starts the server as `$KIRO_KAS_NODE_PATH <server.js> --transport=stdio ...`. run_kiro.py points that
variable at this file. The relay starts the real Node binary with the same arguments and forwards every JSON-RPC
line unchanged in both directions, except for two startup gates:

1. KAS builds `session/new` / `session/load` answers from a model catalog that loads asynchronously. While the
   catalog is empty the answer contains only a pinned, metadata-free model option. The CLI would then set the
   effort too early and fail. The relay holds such an answer until KAS pushes the complete catalog (a
   `config_option_update`), then releases the answer with the complete options. A catalog that is complete but
   lacks the target model or its effort level fails at once; it never waits and never retries.
2. `session/prompt` is forwarded only after the latest configuration reported by KAS shows the target model and
   effort as currentValue and no model/effort setter failed. Otherwise the prompt is answered with an error and
   never reaches KAS.

Nothing else is interpreted, so permission requests, authentication callbacks and tool calls pass through
untouched. Only stage names and the confirmed model/effort reach stderr and the status file; message bodies,
tokens and prompts are never logged.

Environment: KIRO_RELAY_REAL_NODE (required), KIRO_RELAY_MODEL, KIRO_RELAY_EFFORT, KIRO_RELAY_STATUS (JSON file),
KIRO_RELAY_TIMEOUT (seconds, default 60), KIRO_RELAY_CATALOG (optional file that receives the model/effort
catalog the server reported, for the panel). KIRO_RELAY_EFFORT set to the empty string selects a model WITHOUT
effort support: the gate then requires the catalog to say the model has none (no effort is ever forced).
"""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

DEFAULT_TIMEOUT = 60.0
GRACE = 5.0
EXIT_GATE = 70
NEW_METHODS = {"session/new", "session/load", "session/resume"}
MODEL_ID, EFFORT_ID = "model", "effortLevel"


def option_of(options, option_id, category):
    for item in options if isinstance(options, list) else []:
        if isinstance(item, dict) and (item.get("id") == option_id or item.get("category") == category):
            return item
    return None


def assess(options, model, effort):
    """Classify a configOptions list: ('ready',), ('pending',) or ('fail', reason).

    `effort` empty means the model must be one WITHOUT effort support (nothing is forced on it)."""
    selector = option_of(options, MODEL_ID, "model")
    if selector is None:
        return ("pending",)
    choices = [c for c in selector.get("options", []) if isinstance(c, dict)]
    target = next((c for c in choices if c.get("value") == model), None)
    meta = ((target or {}).get("_meta") or {}).get("kiro") if target else None
    meta = meta if isinstance(meta, dict) else None
    if len(choices) <= 1 and meta is None:
        return ("pending",)  # the pinned placeholder KAS shows while the catalog is still empty
    if target is None:
        return ("fail", "model_unavailable")
    if not effort:
        if meta is None or not isinstance(meta.get("hasEffort"), bool):
            return ("fail", "effort_unsupported")
        return ("fail", "effort_required") if meta["hasEffort"] else ("ready",)
    levels = (meta or {}).get("effortLevels")
    if not (meta or {}).get("hasEffort") or not isinstance(levels, list) or effort not in levels:
        return ("fail", "effort_unsupported")
    return ("ready",)


def confirmed(options, model, effort):
    # Current capability is required too: a metadata-free placeholder (or a catalog that stopped offering the
    # effort) with matching currentValues is not a confirmation.
    if assess(options, model, effort)[0] != "ready":
        return False
    selector = option_of(options, MODEL_ID, "model")
    if selector is None or selector.get("currentValue") != model:
        return False
    if not effort:
        return True  # a model without effort has no effort option to confirm
    level = option_of(options, EFFORT_ID, "thought_level")
    if level is None:
        return False
    values = [c.get("value") for c in level.get("options", []) if isinstance(c, dict)]
    return level.get("currentValue") == effort and effort in values


class Relay:
    def __init__(self, argv):
        env = os.environ
        self.real_node = env.get("KIRO_RELAY_REAL_NODE", "")
        self.model = env.get("KIRO_RELAY_MODEL", "claude-sonnet-5.5")
        self.effort = env.get("KIRO_RELAY_EFFORT", "medium")
        self.status_path = env.get("KIRO_RELAY_STATUS")
        self.catalog_path = env.get("KIRO_RELAY_CATALOG")
        self.catalog_key = None
        try:
            self.timeout = float(env.get("KIRO_RELAY_TIMEOUT", DEFAULT_TIMEOUT))
        except ValueError:
            self.timeout = DEFAULT_TIMEOUT
        self.argv = argv
        self.cv = threading.Condition()
        self.to_cli, self.to_kas = queue.Queue(), queue.Queue()
        self.tracked = {}      # request id -> (method, params-session-id) of CLI requests whose answer matters
        self.configs = {}      # sessionId -> latest configOptions reported by KAS
        self.holds = []        # answers to session/new|load held back: dicts
        self.backlog = []      # KAS notifications queued behind a held answer
        self.sets = {}         # in-flight set_config_option id -> (sessionId, configId)
        self.set_failed = None
        self.prompts = []      # prompts waiting for the gate: dicts
        self.prompt_forwarded = False
        self.confirmed_logged = False
        self.status = {"state": "starting", "prompt_forwarded": False, "confirmed": False,
                       "model": self.model, "effort": self.effort}
        self.child = None
        self.stopping = False
        self.exit_code = None
        self.sigint_at = None

    # ---- diagnostics -------------------------------------------------------------------------------
    def log(self, text):
        try:
            sys.stderr.write("[kiro-relay] " + text + "\n")
            sys.stderr.flush()
        except OSError:
            pass

    def write_status(self, **changes):
        self.status.update(changes)
        self.status["updated"] = time.time()
        if not self.status_path:
            return
        try:
            tmp = self.status_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as stream:
                json.dump(self.status, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.status_path)
        except OSError:
            pass

    def persist_catalog(self, options):
        """Best effort: store the complete model/effort catalog the server reported (never any message body)."""
        if not self.catalog_path or not isinstance(options, list):
            return
        try:
            import kiro_catalog
            models = kiro_catalog.parse_config_options(options)
            key = json.dumps(models, sort_keys=True)
            if models and key != self.catalog_key:
                kiro_catalog.write_snapshot(self.catalog_path, models, "relay")
                self.catalog_key = key
        except Exception:
            pass

    # ---- output ------------------------------------------------------------------------------------
    @staticmethod
    def encode(message):
        return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"

    def respond(self, request_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        self.to_cli.put(self.encode(message))

    def fail_request(self, request_id, reason, text):
        self.respond(request_id, error={"code": -32000, "message": "kiro-relay: " + text, "data": {"reason": reason}})

    def newer(self, existing, options):
        """The options to keep when a session/new|load answer arrives while `existing` is already stored.

        The answer can be older than a config_option_update that overtook it; a placeholder (pending) answer
        must never replace what the server already reported as a real catalog.
        """
        if isinstance(options, list) and isinstance(existing, list) \
                and assess(options, self.model, self.effort)[0] == "pending" \
                and assess(existing, self.model, self.effort)[0] != "pending":
            return existing
        return options

    # ---- gate evaluation (call with self.cv held) --------------------------------------------------
    def fatal_locked(self, reason, text):
        if self.status.get("state") != "failed":
            self.write_status(state="failed", reason=reason)
            self.log(f"startup stopped ({reason}): {text}; the task prompt was not forwarded")
        self.stopping = True  # run() closes the server's stdin and ends it after a grace period

    def evaluate_locked(self):
        now = time.monotonic()
        for hold in list(self.holds):
            options = self.configs.get(hold["sid"]) if hold["sid"] else None
            verdict = assess(options if options is not None else hold["options"], self.model, self.effort)
            if verdict[0] == "ready":
                result = hold["result"]
                if options is not None and options is not hold["options"]:
                    result = dict(result, configOptions=options)
                self.holds.remove(hold)
                # the released answer already carries the latest configuration: queued (older) config
                # notifications for this session must not push the CLI back to a placeholder
                self.backlog = [(line, owner) for line, owner in self.backlog if owner is None or owner != hold["sid"]]
                self.to_cli.put(self.encode({"jsonrpc": "2.0", "id": hold["id"], "result": result}))
                self.write_status(state="catalog_ready")
                self.log("model catalog ready" + (" (after waiting)" if hold["waited"] else ""))
            elif verdict[0] == "fail":
                self.holds.remove(hold)
                self.fail_request(hold["id"], verdict[1], "target model/effort cannot be selected in this catalog")
                self.fatal_locked(verdict[1], f"{self.model}/{self.effort} cannot be selected")
            elif now >= hold["deadline"]:
                self.holds.remove(hold)
                self.fail_request(hold["id"], "catalog_timeout", "model catalog did not become ready in time")
                self.fatal_locked("catalog_timeout", f"model catalog not ready after {self.timeout:g}s")
        if not self.holds and self.backlog:
            for line, _ in self.backlog:
                self.to_cli.put(line)
            self.backlog = []
        for prompt in list(self.prompts):
            sid = prompt["sid"]
            if self.stopping:  # CLI gone or startup failed: a held prompt must never be forwarded now
                self.prompts.remove(prompt)
                if self.status.get("state") == "failed":
                    self.fail_request(prompt["id"], self.status.get("reason", "failed"), "startup failed; prompt not forwarded")
            elif self.set_failed:
                self.prompts.remove(prompt)
                self.fail_request(prompt["id"], "set_failed", "model/effort selection was rejected")
                self.fatal_locked("set_failed", f"setting {self.set_failed} was rejected")
            elif any(s == sid for s, _ in self.sets.values()) or self.holds:
                if now >= prompt["deadline"]:
                    self.prompts.remove(prompt)
                    self.fail_request(prompt["id"], "confirm_timeout", "model/effort were not confirmed in time")
                    self.fatal_locked("confirm_timeout", "model/effort confirmation timed out")
            elif confirmed(self.configs.get(sid), self.model, self.effort):
                self.prompts.remove(prompt)
                self.forward_prompt_locked(prompt)
            elif assess(self.configs.get(sid), self.model, self.effort)[0] == "fail":
                verdict = assess(self.configs.get(sid), self.model, self.effort)
                self.prompts.remove(prompt)
                self.fail_request(prompt["id"], verdict[1], "the server no longer offers the target model/effort")
                self.fatal_locked(verdict[1], f"{self.model}/{self.effort} is no longer offered")
            elif now >= prompt["deadline"]:
                self.prompts.remove(prompt)
                self.fail_request(prompt["id"], "confirm_timeout", "model/effort were not confirmed in time")
                self.fatal_locked("confirm_timeout", "model/effort were not confirmed by the server")
        self.cv.notify_all()

    def forward_prompt_locked(self, prompt):
        self.prompt_forwarded = True
        # Persist before forwarding: if this process dies afterwards, the runner must assume the prompt was sent.
        self.write_status(state="prompt_forwarded", prompt_forwarded=True, confirmed=True)
        if not self.confirmed_logged:
            self.confirmed_logged = True
            self.log(f"model={self.model} effort={self.effort} confirmed by the server; forwarding the prompt")
        self.to_kas.put(prompt["raw"])

    # ---- CLI -> KAS --------------------------------------------------------------------------------
    def from_cli(self, raw):
        try:
            message = json.loads(raw)
        except ValueError:
            message = None
        with self.cv:
            if message is None:
                if b"session/prompt" in raw:  # unparsable but may hide a prompt: never forward it
                    self.log("malformed message mentioning a prompt was dropped")
                    return
                self.to_kas.put(raw)
                return
            if isinstance(message, list):
                if b"session/prompt" in raw:
                    self.log("batched message mentioning a prompt was dropped")
                    return
                self.to_kas.put(raw)
                return
            method = message.get("method") if isinstance(message, dict) else None
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            request_id = message.get("id")
            if method == "session/prompt" and "id" in message:
                if self.status.get("state") == "failed":
                    self.fail_request(request_id, self.status.get("reason", "failed"), "startup failed; prompt not forwarded")
                    return
                self.prompts.append({"id": request_id, "sid": params.get("sessionId"), "raw": raw,
                                     "deadline": time.monotonic() + self.timeout})
                self.evaluate_locked()
                return
            if method == "session/prompt":  # a notification form cannot be gated or answered
                self.log("prompt notification dropped")
                return
            if method == "session/cancel":
                held = [p for p in self.prompts if p["sid"] == params.get("sessionId")]
                for prompt in held:
                    self.prompts.remove(prompt)
                    self.respond(prompt["id"], result={"stopReason": "cancelled"})
                if held:
                    return
            if method == "session/set_config_option" and "id" in message:
                self.sets[request_id] = (params.get("sessionId"), params.get("configId"))
            if method in NEW_METHODS and "id" in message:
                self.tracked[request_id] = (method, params.get("sessionId"))
            self.to_kas.put(raw)

    # ---- KAS -> CLI --------------------------------------------------------------------------------
    def from_kas(self, raw):
        try:
            message = json.loads(raw)
        except ValueError:
            message = None
        with self.cv:
            if not isinstance(message, dict):
                self.pass_or_queue_locked(raw)
                return
            method = message.get("method")
            if method is None and "id" in message:
                self.on_response_locked(message, raw)
                return
            if method == "session/update":
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                update = params.get("update") if isinstance(params.get("update"), dict) else {}
                if update.get("sessionUpdate") == "config_option_update" and isinstance(update.get("configOptions"), list):
                    self.configs[params.get("sessionId")] = update["configOptions"]
                    self.persist_catalog(update["configOptions"])
                    self.pass_or_queue_locked(raw, params.get("sessionId"))
                else:
                    self.pass_or_queue_locked(raw)
                self.evaluate_locked()
                return
            if "id" in message:  # KAS -> CLI request (permissions, auth callbacks, ...): never delayed
                self.to_cli.put(raw)
            else:
                self.pass_or_queue_locked(raw)

    def pass_or_queue_locked(self, raw, config_owner=None):
        if self.holds:
            self.backlog.append((raw, config_owner))
        else:
            self.to_cli.put(raw)

    def on_response_locked(self, message, raw):
        request_id = message.get("id")
        result = message.get("result")
        if request_id in self.sets:
            sid, config_id = self.sets.pop(request_id)
            if "error" in message:
                if config_id in (MODEL_ID, EFFORT_ID):
                    self.set_failed = str(config_id)
                    code = message["error"].get("code") if isinstance(message["error"], dict) else None
                    self.log(f"setting {config_id} was rejected by the server (code {code})")
            elif isinstance(result, dict) and isinstance(result.get("configOptions"), list):
                self.configs[sid] = result["configOptions"]
            self.to_cli.put(raw)
            self.evaluate_locked()
            return
        if request_id in self.tracked:
            method, requested = self.tracked.pop(request_id)
            if "error" in message or not isinstance(result, dict):
                self.to_cli.put(raw)
                return
            sid = result.get("sessionId") or requested
            options = result.get("configOptions")
            if isinstance(options, list):
                chosen = self.newer(self.configs.get(sid), options)
                if chosen is not options:  # a complete config_option_update overtook this older answer
                    result = dict(result, configOptions=chosen)
                    raw = self.encode(dict(message, result=result))
                    options = chosen
                self.configs[sid] = options
                self.persist_catalog(options)
            current = self.configs.get(sid)
            verdict = assess(current, self.model, self.effort)
            if verdict[0] == "ready":
                self.write_status(state="catalog_ready")
                self.to_cli.put(raw)
                self.log("model catalog ready")
            elif verdict[0] == "fail":
                self.fail_request(request_id, verdict[1], "target model/effort cannot be selected in this catalog")
                self.fatal_locked(verdict[1], f"{self.model}/{self.effort} cannot be selected")
            else:
                self.holds.append({"id": request_id, "sid": sid, "result": result, "options": options,
                                   "deadline": time.monotonic() + self.timeout, "waited": True})
                self.write_status(state="waiting_catalog")
                self.log("model catalog is not ready; holding the session answer")
            self.cv.notify_all()
            return
        self.to_cli.put(raw)

    # ---- plumbing ----------------------------------------------------------------------------------
    def terminate_child(self):
        child = self.child
        if child and child.poll() is None:
            try:
                child.terminate()
            except OSError:
                pass

    def writer(self, source, sink_name):
        while True:
            item = source.get()
            if item is None:
                return
            try:
                if sink_name == "cli":
                    sys.stdout.buffer.write(item)
                    sys.stdout.buffer.flush()
                else:
                    self.child.stdin.write(item)
                    self.child.stdin.flush()
            except (OSError, ValueError):
                if sink_name == "kas":
                    return
                # CLI is gone: keep draining so the reader never blocks, but write nothing.
                while source.get() is not None:
                    pass
                return

    def read_cli(self):
        stream = sys.stdin.buffer
        try:
            while True:
                raw = stream.readline()
                if not raw:
                    break
                self.from_cli(raw if raw.endswith(b"\n") else raw + b"\n")
        except (OSError, ValueError):
            pass
        finally:
            with self.cv:
                self.stopping = True
                self.evaluate_locked()
            self.to_kas.put(None)

    def read_kas(self):
        try:
            while True:
                raw = self.child.stdout.readline()
                if not raw:
                    break
                self.from_kas(raw if raw.endswith(b"\n") else raw + b"\n")
        except (OSError, ValueError):
            pass

    def watchdog(self):
        with self.cv:
            while self.exit_code is None:
                self.cv.wait(timeout=0.2)
                if self.holds or self.prompts:
                    self.evaluate_locked()
                if self.sigint_at and time.monotonic() >= self.sigint_at:
                    self.sigint_at = None
                    self.terminate_child()

    def run(self):
        if not self.real_node or not os.access(self.real_node, os.X_OK):
            self.log("KIRO_RELAY_REAL_NODE is missing or not executable")
            self.write_status(state="failed", reason="no_node", final=True)
            return 127
        env = {k: v for k, v in os.environ.items() if not k.startswith("KIRO_RELAY_")}
        env["KIRO_KAS_NODE_PATH"] = self.real_node

        def die_with_parent():
            try:
                import ctypes
                ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
            except Exception:
                pass

        self.child = subprocess.Popen([self.real_node, *self.argv], env=env, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, preexec_fn=die_with_parent)
        self.write_status(state="starting", pid=self.child.pid)
        self.log("started the agent server")

        def on_term(signum, frame):
            self.stopping = True
            self.terminate_child()

        def on_int(signum, frame):
            self.sigint_at = time.monotonic() + GRACE  # let the CLI wind down first; stdin EOF also ends us

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGHUP, on_term)
        signal.signal(signal.SIGINT, on_int)

        threads = [threading.Thread(target=f, daemon=True) for f in (self.read_cli, self.read_kas, self.watchdog)]
        cli_writer = threading.Thread(target=self.writer, args=(self.to_cli, "cli"), daemon=True)
        kas_writer = threading.Thread(target=self.writer, args=(self.to_kas, "kas"), daemon=True)
        for thread in [cli_writer, kas_writer, *threads]:
            thread.start()
        reader_kas = threads[1]
        # Wait for the CLI side to close or the server to exit.
        while self.child.poll() is None:
            if self.stopping:
                try:
                    self.child.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    self.child.wait(timeout=GRACE)
                except subprocess.TimeoutExpired:
                    self.terminate_child()
                    try:
                        self.child.wait(timeout=GRACE)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                break
            time.sleep(0.05)
        self.child.wait()
        reader_kas.join(timeout=10)  # trailing output the server wrote just before exiting
        with self.cv:
            for line, _ in self.backlog:
                self.to_cli.put(line)
            self.backlog = []
            pending = [hold["id"] for hold in self.holds] + list(self.tracked)
            for request_id in pending:
                self.fail_request(request_id, "server_exited", "agent server exited during startup")
            self.tracked = {}
            if not self.prompt_forwarded and not self.stopping and self.status.get("state") != "failed":
                self.write_status(state="failed", reason="server_exited")
            self.holds = []
            for prompt in self.prompts:
                self.fail_request(prompt["id"], "server_exited", "agent server exited before the prompt was sent")
            self.prompts = []
            self.exit_code = 0
            self.cv.notify_all()
        self.to_cli.put(None)
        cli_writer.join(timeout=10)
        if self.status.get("state") == "failed" and self.status.get("reason") not in (None, "server_exited"):
            self.write_status(final=True)
            return EXIT_GATE
        code = self.child.returncode
        self.write_status(exited=code, final=True)
        return code if code >= 0 else 128 - code


def main():
    code = Relay(sys.argv[1:]).run()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    # Leave without interpreter shutdown: the stdin reader thread may still be blocked inside readline().
    os._exit(code & 0xFF)


if __name__ == "__main__":
    main()
