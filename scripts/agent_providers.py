"""Small provider registry; Claude configuration is not an account model catalog."""
import re
import shutil

from kiro_catalog import ConfigError

PROVIDERS = {"kiro": {"id": "kiro", "name": "Kiro CLI", "binary": "kiro-cli", "runner": "run_kiro.py"},
             "claude": {"id": "claude", "name": "Claude Code", "binary": "claude", "runner": "run_claude.py"}}


def provider(name):
    if not isinstance(name, str) or name not in PROVIDERS:
        raise ValueError("未知后端：" + str(name))
    return PROVIDERS[name]


def inventory():
    return [{"id": p["id"], "name": p["name"], "available": bool(shutil.which(p["binary"]))}
            for p in PROVIDERS.values()]


def default_provider():
    return "claude" if shutil.which("claude") else "kiro"


from claude_catalog import ModelCatalog, MODEL_ID


class ClaudeModels(ModelCatalog):
    """Current API catalog; syntax checks do not themselves initiate requests."""

    def validate(self, model, effort, cwd=None, check_catalog=False):
        if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
            raise ConfigError("invalid_model", "Claude 模型名称无效")
        if effort not in (None, "low", "medium", "high", "max"):
            raise ConfigError("invalid_effort", "Claude 思考强度无效")
        if "haiku" in model.lower() and effort is not None:
            raise ConfigError("effort_unsupported", "Haiku 请使用 --no-effort")
        if check_catalog:
            cached = self.snapshot(cwd=cwd)
            if cached.get("status") != "ok":
                raise ConfigError("catalog_unavailable", "请先读取当前 API 的模型目录")
            if model not in {item["id"] for item in cached["models"]}:
                raise ConfigError("model_unavailable", "当前 API 的模型目录中没有所选模型，请刷新目录并重新选择")
        return model, effort


def unsupported_usage():
    return {"status": "unsupported", "provider": "claude", "data": None, "refreshing": False,
            "message": "Claude Code 暂不提供账户额度查询；任务用量见每轮摘要。"}
