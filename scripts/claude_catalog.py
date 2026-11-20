"""Read the current Claude API's model directory without an inference request.

Only configured public API credentials are reused. OAuth credential files and
keychain entries are deliberately outside this adapter's supported auth methods.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MODEL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@+*()-]{0,199}")
MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 20


class CatalogError(ValueError):
    pass


def _settings(path):
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise CatalogError("Claude 配置文件过大，无法读取模型目录")
        result = json.loads(raw)
        if not isinstance(result, dict): raise ValueError()
        return result
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise CatalogError("Claude 配置无法读取，请检查配置文件格式和权限") from None


def resolve_config(cwd=None, environment=None):
    """Resolve user/project/local settings, whose env overrides inherited values.

    The returned headers are private backend values and must never be serialized.
    No helper scripts, auth CLI commands, credential stores or network are invoked.
    """
    environment = dict(os.environ if environment is None else environment)
    home = Path(environment.get("HOME") or Path.home())
    config_dir = Path(environment.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    settings = _settings(config_dir / "settings.json")
    merged = dict(environment)
    source = "当前进程环境"
    relevant = {"ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}

    def apply(item, label):
        nonlocal source
        values = item.get("env") or {}
        if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
            raise CatalogError("Claude 配置的 env 必须是字符串映射")
        merged.update(values)
        if relevant.intersection(values): source = label

    apply(settings, "用户全局配置")
    # Matches the installed CLI: project and local settings use original cwd.
    if cwd is not None:
        root = Path(cwd).expanduser().resolve()
        for name in ("settings.json", "settings.local.json"):
            item = _settings(root / ".claude" / name)
            apply(item, "项目配置" if name == "settings.json" else "项目本地配置")
            settings.update(item)
    managed = Path("/Library/Application Support/ClaudeCode/managed-settings.json" if sys.platform == "darwin"
                   else "/etc/claude-code/managed-settings.json")
    policy = _settings(managed)
    apply(policy, "托管配置")
    if merged.get("ANTHROPIC_UNIX_SOCKET"):
        raise CatalogError("当前 Claude 使用 Unix Socket 连接，暂不支持模型目录读取")
    for key in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        if str(merged.get(key, "")).lower() in {"1", "true"}:
            raise CatalogError("当前 Claude 使用云平台认证，暂不支持读取该平台模型目录")
    base = merged.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
    if not isinstance(base, str): raise CatalogError("Claude API 地址格式无效")
    parsed = urlsplit(base)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CatalogError("Claude API 地址无效：需为不含账号、查询参数的 HTTP(S) 地址")
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    token, key = merged.get("ANTHROPIC_AUTH_TOKEN"), merged.get("ANTHROPIC_API_KEY")
    headers = {"anthropic-version": "2023-06-01", "Accept": "application/json"}
    if isinstance(token, str) and token:
        headers["Authorization"] = "Bearer " + token
    elif isinstance(key, str) and key:
        headers["x-api-key"] = key
    else:
        raise CatalogError("当前 Claude 未配置可复用的 API Key 或 Auth Token；订阅登录及 apiKeyHelper 暂不支持模型目录读取")
    for value in headers.values():
        if "\n" in value or "\r" in value: raise CatalogError("Claude API 认证配置格式无效")
    fingerprint = hashlib.sha256(json.dumps([base, headers], sort_keys=True).encode()).hexdigest()
    proxy = {scheme: merged.get(scheme.upper() + "_PROXY") or merged.get(scheme + "_proxy")
             for scheme in ("http", "https")}
    return {"base_url": base, "headers": headers, "cache_key": fingerprint, "source": source,
            "proxies": {k: v for k, v in proxy.items() if isinstance(v, str) and v}}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not send secrets to a URL chosen by an upstream response.


def fetch_json(request, timeout=10, proxies=None):
    opener = build_opener(NoRedirect(), ProxyHandler(proxies) if proxies else ProxyHandler())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES: raise CatalogError("模型目录响应过大")
    return json.loads(raw)


def _cache_path():
    config = Path(os.environ.get("CODER_AGENTS_CONFIG") or Path.home() / ".local/state/kiro-panel/config.json").expanduser()
    return config.parent / "claude-models.json"


def _effort(model):
    haiku = "haiku" in model.lower()
    return {"known": True, "supported": not haiku,
            "levels": [] if haiku else ["low", "medium", "high", "max"],
            "default": None if haiku else "medium", "source": "cli-options",
            "note": "思考强度来自 Claude CLI 配置选项，模型/API 是否支持由执行时校验。"}


class ModelCatalog:
    def __init__(self, fetch=None):
        self.fetch = fetch
        self.current = {}
        self.lock = threading.RLock()

    def _read_cache(self):
        try:
            raw = _cache_path().read_bytes()
            if len(raw) <= MAX_BYTES:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError): pass
        return {}

    def _save_cache(self, key, result):
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = self._read_cache()
        data[key] = result
        # Bound config changes retained on disk; no URL or auth is stored.
        data = dict(sorted(data.items(), key=lambda pair: pair[1].get("fetched_at", 0))[-20:])
        fd, temporary = tempfile.mkstemp(prefix=".claude-models-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream: json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def snapshot(self, force=False, cwd=None):
        with self.lock:
            return self._snapshot(force, cwd)

    def _snapshot(self, force, cwd):
        key = None
        result = {"provider": "claude", "source": "api-models", "status": "error", "models": [],
                  "default": {"available": False}}
        try:
            config = resolve_config(cwd)
            key = config["cache_key"]
            if not force:
                cached = self.current.get(key) or self._read_cache().get(key)
                if cached and isinstance(cached.get("models"), list):
                    return self._with_defaults(deepcopy(cached))
                return {**result, "status": "idle", "config_source": config["source"],
                        "message": "尚未读取当前 API 的模型目录，请点击“获取模型列表”。"}
            result["config_source"] = config["source"]
            base = config["base_url"]
            url = base + ("/models" if base.endswith("/v1") else "/v1/models")
            secrets = [value[7:] if value.startswith("Bearer ") else value
                       for name, value in config["headers"].items() if name.lower() in {"authorization", "x-api-key"}]
            seen, rows, after = set(), {}, None
            for _ in range(MAX_PAGES):
                query = {"limit": 100}
                if after: query["after_id"] = after
                request = Request(url + "?" + urlencode(query), headers=config["headers"])
                page = self.fetch(request, timeout=10) if self.fetch else fetch_json(request, proxies=config["proxies"])
                if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                    raise CatalogError("当前 API 未返回兼容的模型目录（需要 data 列表）")
                for item in page["data"]:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not MODEL_ID.fullmatch(item["id"]):
                        raise CatalogError("模型目录包含无效模型 ID")
                    model = item["id"]
                    name = item.get("display_name") or item.get("name") or model
                    if any(secret and secret in model for secret in secrets):
                        raise CatalogError("模型目录响应包含认证信息，已拒绝展示")
                    name = str(name)[:200]
                    for secret in secrets:
                        if secret: name = name.replace(secret, "[已隐藏]")
                    rows[model] = {"id": model, "name": name, "selectable": True,
                                   "rate_multiplier": None, "effort": _effort(model)}
                if len(rows) > 2000: raise CatalogError("模型目录条目过多")
                if not page.get("has_more"): break
                after = page.get("last_id")
                if not isinstance(after, str) or not after or after in seen:
                    raise CatalogError("模型目录分页无效，已停止读取")
                seen.add(after)
            else: raise CatalogError("模型目录分页超过上限")
            if not rows: raise CatalogError("当前 API 返回的模型目录为空")
            result.update(status="ok", models=list(rows.values()), fetched_at=time.time(),
                          message="模型来自当前 API 地址；思考强度为 CLI 配置选项，实际支持情况由执行时校验。")
            self.current[key] = deepcopy(result)
            try:
                self._save_cache(key, result)
            except OSError:
                result["cache_warning"] = "模型目录已读取，但本地缓存无法保存；本次会话仍可使用。"
            return self._with_defaults(result)
        except HTTPError as error:
            message = ({401: "API 认证失败，请检查 Claude 的 API 凭据", 403: "当前凭据无权读取模型目录",
                        404: "当前 API 地址不支持模型目录接口", 429: "模型目录请求被限流，请稍后重试"}.get(error.code)
                       or ("模型目录请求被重定向，未转发认证信息" if 300 <= error.code < 400 else "模型目录请求失败（HTTP %s）" % error.code))
        except (TimeoutError, URLError, OSError):
            message = "模型目录请求失败或超时，请检查当前 API 地址、网络与代理设置"
        except CatalogError as error:
            message = str(error)
        except (ValueError, TypeError, AttributeError):
            message = "模型目录响应格式无效，未使用固定模型列表替代"
        result.update(status="error", models=[], default={"available": False})
        result["error"] = message
        result["message"] = message
        if key is not None and force:
            self.current[key] = deepcopy(result)
            try: self._save_cache(key, result)
            except OSError: pass
        return result

    @staticmethod
    def _with_defaults(result):
        # Local import avoids a provider/connection registry import cycle.
        from cli_connections import Connections
        value = Connections().defaults("claude")
        match = next((item for item in result["models"] if item["id"] == value["model"]), None)
        result["default"] = {**value, "available": match is not None}
        return result
