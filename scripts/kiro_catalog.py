#!/usr/bin/env python3
"""Account model catalog for the panel.

Two real sources, nothing hard-coded:
* model IDs and display data: `kiro-cli chat --v3 --list-models --format json` (what this account may use);
* effort support per model: the ACP `configOptions` of a Kiro session (`_meta.kiro.hasEffort/effortLevels/
  defaultEffortLevel`). The relay stores a snapshot after every run; when no snapshot exists the panel reads one
  through a single reusable probe session (`session/load`, never a prompt).

`validate()` is the backend's authority over what a browser may ask for. A model whose effort support is not
known is listed but not selectable, so an unsupported option is never offered.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

DEFAULT_MODEL = "claude-sonnet-5.5"
DEFAULT_EFFORT = "medium"
MODEL_ID = re.compile(r"[A-Za-z0-9._:-]{1,80}")
EFFORT_ID = re.compile(r"[A-Za-z0-9_-]{1,16}")
STATE_DIR = Path.home() / ".local/state/kiro-panel"
CATALOG_TTL = 300
PROBE_RETRY = 600


class ConfigError(ValueError):
    """The requested model/effort cannot be used. `code` is stable; the text is shown to the user."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def parse_list_models(text):
    """Models of the account from `--list-models --format json` output; malformed entries are skipped."""
    data = json.loads(text)
    models = []
    for item in data.get("models", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get("model_id"), str) or not MODEL_ID.fullmatch(item["model_id"]):
            continue
        number = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None
        models.append({"id": item["model_id"], "name": str(item.get("model_name") or item["model_id"])[:80],
                       "description": str(item.get("description") or "")[:200],
                       "context_window_tokens": number(item.get("context_window_tokens")),
                       "rate_multiplier": number(item.get("rate_multiplier")),
                       "rate_unit": str(item.get("rate_unit") or "")[:16]})
    return models


def parse_config_options(options):
    """{model id: {has_effort, levels, default}} from ACP configOptions, or {} while the catalog is incomplete."""
    selector = next((o for o in options if isinstance(o, dict) and (o.get("id") == "model" or o.get("category") == "model")),
                    None) if isinstance(options, list) else None
    choices = [c for c in (selector or {}).get("options", []) if isinstance(c, dict) and isinstance(c.get("value"), str)]
    if len(choices) <= 1 and not any(((c.get("_meta") or {}).get("kiro")) for c in choices):
        return {}  # the metadata-free placeholder KAS shows while its catalog is still loading
    result = {}
    for choice in choices:
        meta = (choice.get("_meta") or {}).get("kiro")
        if not isinstance(meta, dict) or not isinstance(meta.get("hasEffort"), bool):
            continue
        levels = [v for v in meta.get("effortLevels") or [] if isinstance(v, str) and EFFORT_ID.fullmatch(v)]
        if meta["hasEffort"] and not levels:
            continue  # claims effort but names no level: not usable, treat as unknown
        default = meta.get("defaultEffortLevel")
        result[choice["value"]] = {"has_effort": meta["hasEffort"], "levels": levels if meta["hasEffort"] else [],
                                   "default": default if meta["hasEffort"] and default in levels else None}
    return result


class EffortCache:
    """Effort support snapshots, as JSON next to the panel's other state. Written by the relay and the probe."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            models = data["models"]
        except (OSError, ValueError, KeyError, TypeError):
            return {}
        clean = {}
        for model, info in models.items() if isinstance(models, dict) else []:
            if not (isinstance(model, str) and MODEL_ID.fullmatch(model) and isinstance(info, dict)
                    and isinstance(info.get("has_effort"), bool) and isinstance(info.get("levels"), list)):
                continue
            levels = [v for v in info["levels"] if isinstance(v, str) and EFFORT_ID.fullmatch(v)]
            default = info.get("default")
            clean[model] = {"has_effort": info["has_effort"], "levels": levels,
                            "default": default if default in levels else None}
        return clean

    def save(self, models, source):
        write_snapshot(self.path, models, source)


def write_snapshot(path, models, source):
    """Atomic 0600 write; best effort callers ignore OSError."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp%d" % os.getpid())
    temporary.write_text(json.dumps({"version": 1, "saved_at": time.time(), "source": source, "models": models}))
    temporary.chmod(0o600)
    temporary.replace(path)


def fetch_list_models(environment, binary=None, timeout=60):
    import kiro_acp
    binary = binary or kiro_acp.find_cli(environment)
    try:
        done = subprocess.run([binary, "chat", "--v3", "--list-models", "--format", "json"], env=environment,
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ConfigError("catalog_unavailable", "找不到 kiro-cli，无法读取账户的模型列表")
    except subprocess.TimeoutExpired:
        raise ConfigError("catalog_unavailable", "读取模型列表超时，请检查网络或代理后重试")
    if done.returncode:
        raise ConfigError("catalog_unavailable", "kiro-cli 未能返回模型列表（退出码 %d），请检查登录和网络" % done.returncode)
    try:
        models = parse_list_models(done.stdout)
    except (ValueError, AttributeError):
        raise ConfigError("catalog_unavailable", "模型列表的格式无法识别")
    if not models:
        raise ConfigError("catalog_unavailable", "账户的模型列表为空")
    return models


def probe_efforts(environment, state_dir, command=None, token_provider=None, timeout=40):
    """Effort support read from a Kiro session's configOptions through one reusable probe session.

    `session/load` of the panel's own probe session is preferred; only if none exists (or it cannot be loaded) is
    one `session/new` created. No prompt is ever sent. Returns {model: {...}}."""
    import kiro_acp
    state_dir = Path(state_dir)
    cwd = state_dir / "probe-cwd"
    cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
    record_path = state_dir / "catalog-probe.json"
    try:
        saved = json.loads(record_path.read_text())
        session_id = saved["session_id"] if saved.get("cwd") == str(cwd) and isinstance(saved.get("session_id"), str) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        session_id = None
    command = command or kiro_acp.server_command(environment)
    models, error = {}, None
    with kiro_acp.AcpClient(command, environment, token_provider) as client:
        client.initialize()
        result = None
        if session_id:
            try:
                result = client.request("session/load", {"sessionId": session_id, "cwd": str(cwd), "mcpServers": []}, 30)
            except kiro_acp.AcpError as failure:
                if failure.code != "request_failed":
                    raise
                error = failure
        if result is None:
            result = client.request("session/new", {"cwd": str(cwd), "mcpServers": []}, 30)
            session_id = result.get("sessionId") or session_id
            try:
                write_json_private(record_path, {"session_id": session_id, "cwd": str(cwd)})
            except OSError:
                pass
        models = parse_config_options(result.get("configOptions"))
        if not models:
            note = client.wait_notification(lambda m: m.get("method") == "session/update" and parse_config_options(
                ((m.get("params") or {}).get("update") or {}).get("configOptions")), timeout)
            if note:
                models = parse_config_options(note["params"]["update"]["configOptions"])
        try:
            client.request("session/close", {"sessionId": session_id}, 5)
        except kiro_acp.AcpError:
            pass
    if not models:
        raise ConfigError("catalog_unavailable", "Kiro 的模型强度信息暂未加载完成，请稍后重试")
    return models


def write_json_private(path, data):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(data))
    temporary.chmod(0o600)
    temporary.replace(path)


class ModelCatalog:
    def __init__(self, lister, cache, prober=None, clock=time.time, ttl=CATALOG_TTL):
        self.lister, self.cache, self.prober, self.clock, self.ttl = lister, cache, prober, clock, ttl
        self.lock = threading.Lock()
        self.current = None
        self.fetched_at = 0.0
        self.probe_retry_at = 0.0
        self.error = None
        self.efforts = None

    def snapshot(self, force=False):
        with self.lock:
            now = self.clock()
            if self.current is not None and not force and now - self.fetched_at < self.ttl:
                return self.view(now)
            try:
                models = self.lister()
                self.current, self.fetched_at, self.error = models, now, None
            except ConfigError as error:
                self.error = str(error)
                if self.current is None:
                    return self.view(now)
            efforts = self.cache.load()
            if self.prober and any(m["id"] not in efforts for m in self.current) and now >= self.probe_retry_at:
                try:
                    efforts.update(self.prober())
                    self.cache.save(efforts, "probe")
                except (ConfigError, OSError, ValueError) as error:
                    self.probe_retry_at = now + PROBE_RETRY
                    self.error = self.error or ("读取模型强度信息失败：" + str(error))
                except Exception as error:  # an AcpError or anything unexpected must not break the panel
                    self.probe_retry_at = now + PROBE_RETRY
                    self.error = self.error or ("读取模型强度信息失败：" + str(getattr(error, "message", error)))
            self.efforts = efforts
            return self.view(now)

    def view(self, now):
        efforts = self.efforts if self.efforts is not None else self.cache.load()
        models = []
        for item in self.current or []:
            info = efforts.get(item["id"])
            entry = dict(item)
            if info is None:
                entry.update(effort={"known": False, "supported": None, "levels": [], "default": None}, selectable=False,
                             note="尚未读取该模型的思考强度信息")
            else:
                entry.update(effort={"known": True, "supported": info["has_effort"], "levels": list(info["levels"]),
                                     "default": info["default"]}, selectable=True, note="")
            models.append(entry)
        by_id = {m["id"]: m for m in models}
        target = by_id.get(DEFAULT_MODEL)
        usable = bool(target and target["selectable"] and target["effort"]["supported"]
                      and DEFAULT_EFFORT in target["effort"]["levels"])
        status = "error" if self.current is None else ("stale" if self.error else "ok")
        return {"status": status, "models": models, "error": self.error,
                "fetched_at": self.fetched_at or None,
                "default": {"model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT, "available": usable}}

    def validate(self, model, effort):
        """(model, effort-or-None) the account really offers, else ConfigError. `effort` None means 'none'."""
        if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
            raise ConfigError("model_invalid", "模型标识无效")
        if effort is not None and (not isinstance(effort, str) or not EFFORT_ID.fullmatch(effort)):
            raise ConfigError("effort_invalid", "思考强度标识无效")
        snapshot = self.snapshot()
        if snapshot["status"] == "error":
            raise ConfigError("catalog_unavailable", snapshot["error"] or "无法读取账户的模型目录")
        entry = next((m for m in snapshot["models"] if m["id"] == model), None)
        if entry is None:
            raise ConfigError("model_unavailable", "当前账户的模型目录中没有 %s，未发送消息" % model)
        if not entry["selectable"]:
            raise ConfigError("effort_unknown", "尚未读取到 %s 的思考强度信息，请稍后重试" % model)
        info = entry["effort"]
        if info["supported"]:
            if effort is None:
                raise ConfigError("effort_required", "%s 需要选择思考强度（%s）" % (model, "、".join(info["levels"])))
            if effort not in info["levels"]:
                raise ConfigError("effort_unsupported", "%s 不支持思考强度 %s（可选：%s）" % (model, effort, "、".join(info["levels"])))
        elif effort is not None:
            raise ConfigError("effort_not_supported", "%s 不支持思考强度，请不要指定强度" % model)
        return model, effort


def default_catalog(environment, state_dir=STATE_DIR):
    state_dir = Path(state_dir)
    return ModelCatalog(lambda: fetch_list_models(environment), EffortCache(state_dir / "model-catalog.json"),
                        lambda: probe_efforts(environment, state_dir))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Print the account model catalog (read-only; may run one probe session)")
    parser.add_argument("--state-dir", type=Path, default=STATE_DIR)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    import run_kiro
    catalog = default_catalog(run_kiro.kiro_environment(), args.state_dir)
    print(json.dumps(catalog.snapshot(force=args.refresh), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
