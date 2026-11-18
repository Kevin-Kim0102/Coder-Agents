"""Point an old kiro-coding install at this repository's code, reversibly.

`link` renames the old install's scripts/ and assets/ into a fresh backup directory inside the old install
(nothing is deleted) and puts directory symlinks to this repository's scripts/ and assets/ in their place, so the
existing systemd ExecStart and Codex skill paths keep working while the running code comes from one place.
SKILL.md, settings.json, agents/, config, registry and history are never touched. Any failure rolls back.
`unlink` is the reverse. Both refuse while a Kiro task is active."""
import json
import os
from pathlib import Path
import secrets
import time

from . import ROOT, service

NAMES = ("scripts", "assets")
MANIFEST = "MANIFEST.json"
BACKUP_PREFIX = "backup-coder-agents-"


def default_legacy():
    return Path.home() / ".codex/skills/kiro-coding"


def _link_target(name):
    return Path(ROOT) / name


def _state(legacy, name):
    """ok (symlink to this repo), todo (real directory), or a refusal reason."""
    path = legacy / name
    target = _link_target(name)
    if path.is_symlink():
        current = Path(os.path.realpath(path))
        if current == Path(os.path.realpath(target)):
            return "ok", None
        return "refuse", f"{path} 已是指向 {current} 的符号链接，不是本仓库的 {target}，拒绝处理"
    if not path.exists():
        return "refuse", f"{path} 不存在"
    if not path.is_dir():
        return "refuse", f"{path} 不是目录"
    return "todo", None


def plan_link(legacy=None, config_file=None):
    legacy = Path(legacy) if legacy else default_legacy()
    config_file = Path(config_file) if config_file else service.config_path()
    blockers, steps = [], {}
    for name in NAMES:  # the new code must exist before any old code is moved away
        if not (_link_target(name) / ("watch_kiro.py" if name == "scripts" else "viewer.html")).is_file():
            blockers.append(f"本仓库缺少 {_link_target(name)} 的运行文件")
    if not legacy.is_dir():
        blockers.append(f"旧安装目录不存在：{legacy}")
    else:
        if Path(os.path.realpath(legacy)) == Path(os.path.realpath(ROOT)) or Path(os.path.realpath(ROOT)) in Path(
                os.path.realpath(legacy)).parents:
            blockers.append("旧安装目录就是（或位于）本仓库内，拒绝")
        for name in NAMES:
            steps[name], reason = _state(legacy, name)
            if reason:
                blockers.append(reason)
    try:
        service.ensure_no_active_tasks(config_file)
    except PermissionError as error:
        blockers.append(str(error))
    todo = [n for n in NAMES if steps.get(n) == "todo"]
    return {"legacy": str(legacy), "config": str(config_file), "steps": steps, "todo": todo, "blockers": blockers,
            "already_linked": not blockers and not todo}


def apply_link(plan, _fail_after=None):
    """Rename then symlink each directory still to do. _fail_after (tests): raise after that many completed operations."""
    if plan["blockers"]:
        raise PermissionError("; ".join(plan["blockers"]))
    legacy = Path(plan["legacy"])
    fresh = plan_link(legacy, plan["config"])  # never trust the earlier plan
    if fresh["blockers"]:
        raise PermissionError("; ".join(fresh["blockers"]))
    if not fresh["todo"]:
        return None
    backup = legacy / f"{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    done, count = [], 0  # ("renamed"|"linked", name)

    def tick():
        nonlocal count
        count += 1
        if _fail_after is not None and count > _fail_after:
            raise OSError("injected failure")

    try:
        backup.mkdir(mode=0o700)  # unique: refuses if it already exists
        done.append(("backup", None))
        manifest = {"legacy": str(legacy), "repo_root": str(ROOT), "moved": fresh["todo"]}
        (backup / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        for name in fresh["todo"]:
            tick()
            os.rename(legacy / name, backup / name)
            done.append(("renamed", name))
            tick()
            os.symlink(_link_target(name), legacy / name, target_is_directory=True)
            done.append(("linked", name))
    except BaseException as error:
        problems = _rollback(legacy, backup, done)
        suffix = f"；回滚时还有问题：{'; '.join(problems)}" if problems else "，已回滚，旧安装保持原状"
        raise OSError(f"迁移失败：{error}{suffix}") from error
    return backup


def _rollback(legacy, backup, done):
    problems = []
    for kind, name in reversed(done):
        try:
            if kind == "linked":
                if (legacy / name).is_symlink():
                    (legacy / name).unlink()
            elif kind == "renamed":
                if not (legacy / name).exists() and not (legacy / name).is_symlink():
                    os.rename(backup / name, legacy / name)
            else:
                (backup / MANIFEST).unlink(missing_ok=True)
                backup.rmdir()  # only succeeds when empty; a backup that still holds data is kept
        except OSError as error:
            problems.append(f"{kind} {name or ''}: {error}（备份保留在 {backup}）")
    return problems


def find_backups(legacy):
    return sorted(p for p in Path(legacy).glob(BACKUP_PREFIX + "*") if (p / MANIFEST).is_file())


def plan_unlink(legacy=None, backup=None, config_file=None):
    legacy = Path(legacy) if legacy else default_legacy()
    config_file = Path(config_file) if config_file else service.config_path()
    blockers, moves = [], []
    backups = find_backups(legacy) if legacy.is_dir() else []
    if backup:
        chosen = Path(backup)
    elif len(backups) == 1:
        chosen = backups[0]
    else:
        chosen = None
        blockers.append(f"找到 {len(backups)} 个备份目录，请用 --backup 指定其一：" + ", ".join(map(str, backups)))
    if chosen is not None:
        try:
            names = json.loads((chosen / MANIFEST).read_text())["moved"]
        except (OSError, ValueError, KeyError, TypeError):
            names = []
            blockers.append(f"{chosen} 不是有效的备份目录")
        for name in names:
            link = legacy / name
            if name not in NAMES:
                blockers.append(f"备份清单含未知名称 {name}")
            elif not link.is_symlink() or Path(os.path.realpath(link)) != Path(os.path.realpath(_link_target(name))):
                blockers.append(f"{link} 不是指向本仓库 {name} 的符号链接，拒绝回滚")
            elif not (chosen / name).is_dir():
                blockers.append(f"备份中缺少 {chosen / name}")
            else:
                moves.append(name)
    try:
        service.ensure_no_active_tasks(config_file)
    except PermissionError as error:
        blockers.append(str(error))
    return {"legacy": str(legacy), "config": str(config_file), "backup": str(chosen) if chosen else None,
            "moves": moves, "blockers": blockers}


def apply_unlink(plan):
    if plan["blockers"]:
        raise PermissionError("; ".join(plan["blockers"]))
    fresh = plan_unlink(plan["legacy"], plan["backup"], plan["config"])
    if fresh["blockers"]:
        raise PermissionError("; ".join(fresh["blockers"]))
    legacy, backup, restored = Path(fresh["legacy"]), Path(fresh["backup"]), []
    try:
        for name in fresh["moves"]:
            (legacy / name).unlink()
            try:
                os.rename(backup / name, legacy / name)
            except OSError:
                os.symlink(_link_target(name), legacy / name, target_is_directory=True)
                raise
            restored.append(name)
    except OSError as error:
        for name in restored:  # put the links back so the install is not left half restored
            os.rename(legacy / name, backup / name)
            os.symlink(_link_target(name), legacy / name, target_is_directory=True)
        raise OSError(f"回滚失败：{error}，已恢复为迁移后的符号链接状态") from error
    os.rename(backup / MANIFEST, backup / "MANIFEST.restored.json")  # marks the backup used; nothing is deleted
    return backup
