#!/usr/bin/env python3
"""Per-run managed temp directories for Kiro runs, with an explicit accept/cleanup lifecycle.

Layout and ownership
  <cwd>/work/tmp/<run_id>/            the run's TMPDIR/TMP/TEMP (and optional pytest basetemp), mode 0700
  <cwd>/work/tmp/<run_id>/.kiro-runner-temp.json   ownership marker written by the runner (random token)
  <run dir>/state.json  ["temp"]      path, run id, sha256(token), lifecycle status (never the token itself)

Lifecycle: retained -> accepted -> cleaned. The runner never deletes anything: a successful run keeps its temp
directory until the caller (Codex after its own acceptance, or a person in the panel) accepts and cleans it. A
failed or interrupted run keeps the scene; it can be accepted and cleaned after it has been re-tested.

Cleanup is not a generic delete. It removes exactly one directory, and only when ALL of these hold; if any is
uncertain it refuses:
  * the run directory's state.json names this temp directory, which sits at <state cwd>/work/tmp/<state id>;
  * no path component is a symlink and the directory is not a mount point;
  * the marker is a regular file whose token hashes to the value recorded in state.json;
  * the temp directory neither contains nor lies inside the run (log) directory or its parent;
  * the run reached a terminal status and was accepted;
  * no process that belongs to the run is alive (the runner or kiro-cli by verified pid, any descendant, any member
    of the runner's own process group, any process whose environment carries the run's temp id, whose cwd or open
    files are inside the directory). A runner that exited does not prove its children exited.
Run logs, Kiro sessions and conversation history are never touched; deletion uses fd-relative unlink/rmdir that
never follows symlinks and never leaves the verified directory's device.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys

MARKER = ".kiro-runner-temp.json"
ACTIVE = {"checking", "running"}
TERMINAL = {"completed", "failed", "cancelled"}
DEFAULT_MIN_FREE_MB = 512
EXIT_REFUSED = 2


class TempError(Exception):
    """Refusal or failure. `code` is stable; the message is user-facing (Chinese first, English after)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---- /proc helpers -----------------------------------------------------------------------------------
def read_stat(pid, proc="/proc"):
    """{ppid, pgrp, session, state, start} of a process, or None if it is gone or unreadable."""
    try:
        text = Path(proc, str(pid), "stat").read_text()
        rest = text[text.rindex(")") + 2:].split()
        return {"state": rest[0], "ppid": int(rest[1]), "pgrp": int(rest[2]), "session": int(rest[3]), "start": int(rest[19])}
    except (OSError, ValueError, IndexError):
        return None


def scope_identity(proc="/proc"):
    """(boot id, PID namespace link) of the process looking at `proc`; None for a part it cannot read."""
    try:
        boot = Path(proc, "sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot = None
    try:
        namespace = os.readlink(os.path.join(proc, "self", "ns", "pid"))
    except OSError:
        namespace = None
    return boot, namespace


def my_identity():
    """Identity values the runner records about itself, including WHERE its pid is meaningful (boot + PID namespace)."""
    info = read_stat(os.getpid()) or {}
    boot, namespace = scope_identity()
    return {"runner_pid": os.getpid(), "runner_start_ticks": info.get("start"), "pgid": os.getpgrp(), "sid": os.getsid(0),
            "uid": os.getuid(), "boot_id": boot, "pid_ns": namespace}


def require_same_scope(state, proc="/proc"):
    """Pids are only comparable inside one boot and one PID namespace. If the run's scope was not recorded or differs
    from the scope this process sees, nothing about its processes can be proven: refuse (fail closed)."""
    boot, namespace = scope_identity(proc)
    recorded = (state.get("boot_id"), state.get("pid_ns"))
    if None in recorded:
        raise TempError("namespace_unknown", "这次运行没有记录启动周期/PID 命名空间，无法证明进程范围，已拒绝清理。请在启动该任务的环境中"
                        "（面板服务本身，或同一终端/沙箱）重新执行，或先人工确认后手动处理 / the run recorded no boot/PID namespace; cleanup refused")
    if (boot, namespace) != recorded:
        raise TempError("namespace_mismatch", f"该任务运行在另一个 PID 命名空间或另一次开机中（记录 {recorded[1]}，当前 {namespace}），"
                        "当前环境看不到它的进程，不能凭 pid 不存在判断已退出，已拒绝清理。请在启动它的环境中执行：用面板的“验收并清理”按钮"
                        "（由面板服务清理它自己启动的任务），或在同一终端/沙箱里运行 kiro_temp.py / "
                        "different PID namespace or boot: run the cleanup where the task ran (the panel service, same terminal/sandbox)")


def proc_available(proc="/proc"):
    return os.path.isdir(os.path.join(proc, "self")) and read_stat("self", proc) is not None


# ---- creation ----------------------------------------------------------------------------------------
def check_real_dir(path, what):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TempError("unsafe_path", f"{what} 不是普通目录（可能是符号链接或文件），已拒绝：{path} / {what} is not a plain directory")
    return True


def min_free_mb(environment=None, configured=None):
    environment = os.environ if environment is None else environment
    value = environment.get("KIRO_TEMP_MIN_FREE_MB", configured if configured is not None else DEFAULT_MIN_FREE_MB)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_MIN_FREE_MB


def create_run_temp(cwd, run_id, run_dir, agent_id=None, free_mb=DEFAULT_MIN_FREE_MB):
    """Create <cwd>/work/tmp/<run_id> with an ownership marker. Raises TempError with a clear message."""
    cwd = Path(os.path.realpath(cwd))
    run_dir = Path(os.path.realpath(run_dir))
    work, base = cwd / "work", cwd / "work" / "tmp"
    try:
        for directory in (work, base):
            if not check_real_dir(directory, str(directory)):
                directory.mkdir(mode=0o700)
            if Path(os.path.realpath(directory)) != directory:
                raise TempError("unsafe_path", f"目录越出预期位置，已拒绝：{directory} / path escapes its expected location")
        free = shutil.disk_usage(base).free // (1024 * 1024)
        if free < free_mb:
            raise TempError("disk_space", f"磁盘空间不足：{base} 所在分区仅剩 {free} MB，至少需要 {free_mb} MB，未启动 Kiro、"
                            f"消息未发送 / Not enough free disk space ({free} MB < {free_mb} MB); Kiro was not started")
        target = base / run_id
        if run_dir == target or target in run_dir.parents or run_dir in target.parents or run_dir.parent in (target, base):
            raise TempError("unsafe_path", "临时目录不能与运行日志目录重叠，已拒绝 / temp directory must not overlap the run log directory")
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise TempError("exists", f"临时目录已存在，已拒绝复用：{base / run_id} / temp directory already exists")
    except OSError as error:
        raise TempError("create_failed", f"无法创建临时目录 {base}：{error.strerror or error} / cannot create the temp directory")
    token = secrets.token_hex(16)
    marker = {"schema": 1, "owner": "kiro-runner", "run_id": run_id, "agent_id": agent_id, "temp_path": str(target),
              "run_dir": str(run_dir), "cwd": str(cwd), "token": token, "created_at": now_iso()}
    descriptor = os.open(target / MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(marker, stream)
    return {"path": str(target), "run_id": run_id, "token": token, "token_sha256": sha256(token), "created_at": marker["created_at"],
            "free_mb_at_start": free, "status": "retained"}


def public_temp(info):
    """The part of a temp record that goes into state.json (no raw token)."""
    return {k: v for k, v in info.items() if k != "token"}


def temp_environment(environment, info, pytest_basetemp=False):
    """Child environment whose temp locations all lead into the managed directory."""
    path = info["path"]
    result = dict(environment)
    for name in ("TMPDIR", "TMP", "TEMP", "KIRO_RUN_TEMP", "PYTEST_DEBUG_TEMPROOT"):
        result[name] = path
    result["KIRO_RUN_TEMP_ID"] = run_tag(info)
    if pytest_basetemp:
        options = result.get("PYTEST_ADDOPTS", "")
        if "--basetemp" not in options:
            result["PYTEST_ADDOPTS"] = (options + " --basetemp=" + os.path.join(path, "pytest")).strip()
    return result


def run_tag(info):
    return f"{info['run_id']}:{info['token_sha256'][:16]}"


# ---- ownership verification --------------------------------------------------------------------------
def load_state(run_dir):
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise TempError("no_run", f"运行目录不存在：{run_dir} / run directory not found")
    real = Path(os.path.realpath(run_dir))
    path = real / "state.json"
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise ValueError
        state = json.loads(path.read_text())
        if not isinstance(state, dict):
            raise ValueError
    except (OSError, ValueError):
        raise TempError("no_state", f"读不到有效的 state.json，无法确认归属：{path} / cannot read a valid state.json")
    return real, state


def verify_owned(run_dir):
    """(run dir, state, temp path, marker) when this run provably owns its temp directory, else TempError."""
    real, state = load_state(run_dir)
    record = state.get("temp")
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        raise TempError("no_temp", "这次运行没有受管理的临时目录 / this run has no managed temp directory")
    run_id = state.get("id")
    if not isinstance(run_id, str) or run_id != real.name or record.get("run_id") != run_id:
        raise TempError("not_owned", "运行记录与目录名不一致，无法确认归属 / run id mismatch")
    path = Path(record["path"])
    cwd = state.get("cwd")
    if not (path.is_absolute() and isinstance(cwd, str) and os.path.isabs(cwd)):
        raise TempError("not_owned", "临时目录路径不是绝对路径 / temp path is not absolute")
    expected = Path(os.path.realpath(cwd)) / "work" / "tmp" / run_id
    if ".." in path.parts or path != expected or os.path.realpath(path) != str(path):
        raise TempError("not_owned", f"临时目录不在项目的 work/tmp/<run_id> 之下，或路径含符号链接/穿越，已拒绝：{path} / "
                        "temp directory is not at <cwd>/work/tmp/<run_id> or the path involves a symlink or traversal")
    for ancestor in (path.parent.parent, path.parent):
        check_real_dir(ancestor, str(ancestor))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise TempError("missing", f"临时目录已不存在：{path} / temp directory no longer exists")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TempError("unsafe_path", f"临时目录是符号链接或不是目录，已拒绝：{path} / temp path is a symlink or not a directory")
    if os.path.ismount(path) or info.st_dev != os.lstat(path.parent).st_dev:
        raise TempError("unsafe_path", f"临时目录是挂载点，已拒绝：{path} / temp directory is a mount point")
    if path == real or path in real.parents or real in path.parents or real.parent == path or path in real.parent.parents:
        raise TempError("not_owned", "临时目录与运行日志目录重叠，已拒绝 / temp directory overlaps the run log directory")
    try:
        marker_path = path / MARKER
        if not stat.S_ISREG(os.lstat(marker_path).st_mode):
            raise ValueError
        marker = json.loads(marker_path.read_text())
    except (OSError, ValueError):
        raise TempError("not_owned", f"缺少有效的归属标记，已拒绝：{path} / ownership marker missing or invalid")
    if not (isinstance(marker, dict) and marker.get("owner") == "kiro-runner" and marker.get("run_id") == run_id
            and marker.get("temp_path") == str(path) and marker.get("run_dir") == str(real)
            and isinstance(marker.get("token"), str) and sha256(marker["token"]) == record.get("token_sha256")):
        raise TempError("not_owned", "归属标记与运行记录不匹配，已拒绝 / ownership marker does not match the run record")
    return real, state, path, marker, info


# ---- process association -----------------------------------------------------------------------------
def associated_processes(path, state, record, proc="/proc", own=None):
    """Live processes that belong to this run, or TempError when that cannot be answered with certainty.

    Fails closed: a different PID namespace/boot, a missing /proc, an unreadable /proc entry of a process owned by the
    same user, or an unreadable fd table all refuse instead of being ignored. Processes of other users are never the
    run's children and are skipped when unreadable."""
    if not proc_available(proc):
        raise TempError("no_proc", "无法读取 /proc，不能可靠判断是否仍有子进程，已拒绝 / cannot inspect processes reliably")
    require_same_scope(state, proc)
    own = {os.getpid()} if own is None else own
    me = os.getuid()
    tag = ("KIRO_RUN_TEMP_ID=" + run_tag(record)).encode()
    prefix = str(path)
    inside = lambda target: target == prefix or target.startswith(prefix + os.sep)
    table, owners = {}, {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            owners[int(name)] = os.stat(os.path.join(proc, name)).st_uid
        except FileNotFoundError:
            continue
        except OSError:
            raise TempError("proc_unreadable", f"无法读取进程 {name} 的信息，不能证明与本任务无关，已拒绝 / cannot read process {name}")
        info = read_stat(name, proc)
        if info is None:
            if os.path.isdir(os.path.join(proc, name)):
                if owners[int(name)] == me:
                    raise TempError("proc_unreadable", f"无法读取进程 {name} 的状态，已拒绝 / cannot read the state of process {name}")
            continue
        if info["state"] not in ("Z", "X"):
            table[int(name)] = info
    roots = set()
    for pid_key, ticks_key in (("runner_pid", "runner_start_ticks"), ("pid", "pid_start_ticks")):
        pid, ticks = state.get(pid_key), state.get(ticks_key)
        if isinstance(pid, int) and pid in table and (ticks is None or table[pid]["start"] == ticks):
            roots.add(pid)
    leader = state.get("runner_pid") if state.get("pgid") == state.get("runner_pid") else None
    found = []
    for pid, info in table.items():
        if pid in own:
            continue
        reason = None
        if pid in roots:
            reason = "运行器或 kiro-cli 仍在运行"
        elif leader is not None and info["pgrp"] == leader:
            reason = "属于运行器的进程组"
        else:
            parent, hops = info["ppid"], 0
            while parent in table and hops < 64 and reason is None:
                reason = "运行器的后代进程" if parent in roots else None
                parent, hops = table[parent]["ppid"], hops + 1
        mine = owners.get(pid) == me
        unreadable = None
        if reason is None:
            try:
                if tag in Path(proc, str(pid), "environ").read_bytes().split(b"\0"):
                    reason = "环境变量带有本次运行的临时目录标识"
            except FileNotFoundError:
                continue
            except OSError:
                unreadable = "environ"
        if reason is None:
            try:
                if inside(os.readlink(os.path.join(proc, str(pid), "cwd"))):
                    reason = "工作目录在临时目录内"
            except FileNotFoundError:
                if not os.path.isdir(os.path.join(proc, str(pid))):
                    continue
            except OSError:
                unreadable = unreadable or "cwd"
        if reason is None:
            try:
                for entry in os.listdir(os.path.join(proc, str(pid), "fd")):
                    try:
                        target = os.readlink(os.path.join(proc, str(pid), "fd", entry))
                    except FileNotFoundError:
                        continue
                    except OSError:
                        unreadable = unreadable or "fd"
                        continue
                    if inside(target):
                        reason = "打开了临时目录内的文件"
                        break
            except FileNotFoundError:
                if not os.path.isdir(os.path.join(proc, str(pid))):
                    continue
            except OSError:
                unreadable = unreadable or "fd"
        if reason:
            found.append({"pid": pid, "reason": reason})
        elif unreadable and mine:
            raise TempError("proc_unreadable", f"同一用户的进程 {pid} 的 {unreadable} 无法读取，不能证明它与本任务无关，已拒绝清理。"
                            "请在拥有足够权限的环境（面板服务本身或启动该任务的终端）中执行 / cannot prove process "
                            f"{pid} is unrelated (its {unreadable} is unreadable)")
    return found


# ---- actions -----------------------------------------------------------------------------------------
def write_state(run_dir, state):
    temporary = Path(run_dir) / "state.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(Path(run_dir) / "state.json")


def terminal_or_dead(state, path, record, proc, allow_dead_runner):
    status = state.get("status")
    if status in TERMINAL:
        return
    if status in ACTIVE and allow_dead_runner:
        require_same_scope(state, proc)  # a pid that is missing in ANOTHER namespace proves nothing
        runner, ticks = state.get("runner_pid"), state.get("runner_start_ticks")
        info = read_stat(runner, proc) if isinstance(runner, int) else None
        if isinstance(runner, int) and ticks is not None and (info is None or info["start"] != ticks):
            return  # the runner is provably gone; the process check below still has to pass
        raise TempError("active", "运行器仍存活或无法确认已退出，已拒绝 / the runner may still be alive")
    raise TempError("active", f"任务状态为 {status}，尚未结束，已拒绝；结束后再验收清理 / the run has not finished ({status})")


def accept(run_dir, proc="/proc", allow_dead_runner=False, by=None):
    """Record that the run's result was reviewed. Does not delete anything."""
    real, state, path, marker, _ = verify_owned(run_dir)
    record = state["temp"]
    if record.get("status") == "cleaned":
        return {"run_id": state["id"], "status": "cleaned", "path": str(path)}
    terminal_or_dead(state, path, record, proc, allow_dead_runner)
    if record.get("status") != "accepted":
        record.update(status="accepted", accepted_at=now_iso(), accepted_by=by or "cli")
        write_state(real, state)
    return {"run_id": state["id"], "status": "accepted", "path": str(path)}


def clean_tree(path, expected):
    """Delete the verified directory's contents and the directory itself, never following symlinks."""
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        top = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError:
        os.close(parent)
        raise TempError("unsafe_path", "无法安全打开临时目录 / cannot open the temp directory safely")
    try:
        info = os.fstat(top)
        if (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
            raise TempError("unsafe_path", "临时目录在检查后被替换，已拒绝 / the directory changed after verification")
        remove_contents(top, info.st_dev)
    finally:
        os.close(top)
    try:
        os.rmdir(path.name, dir_fd=parent)
    finally:
        os.close(parent)


def remove_contents(directory_fd, device):
    with os.scandir(directory_fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.lstat(name, dir_fd=directory_fd)
        if stat.S_ISDIR(info.st_mode):
            if info.st_dev != device:
                raise TempError("unsafe_path", "临时目录内含其他文件系统的挂载，已拒绝 / nested mount inside the temp directory")
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                if info.st_mode & 0o700 != 0o700:
                    os.fchmod(child, 0o700)
                remove_contents(child, device)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)  # files and symlinks (the link itself, never its target)


def cleanup(run_dir, accept_now=False, proc="/proc", allow_dead_runner=False, by=None):
    """Delete this run's managed temp directory if every safety condition holds; otherwise raise TempError."""
    real, state, path, marker, info = verify_owned(run_dir)
    record = state["temp"]
    terminal_or_dead(state, path, record, proc, allow_dead_runner)
    if accept_now:
        accept(real, proc, allow_dead_runner, by)
        real, state, path, marker, info = verify_owned(run_dir)
        record = state["temp"]
    if record.get("status") != "accepted":
        raise TempError("not_accepted", "尚未验收：任务成功后临时目录会保留，请先验收（accept）再清理；失败或中断的现场也需复测验收后才能清理 / "
                        "not accepted yet: accept the result before cleaning up")
    live = associated_processes(path, state, record, proc)
    if live:
        detail = "；".join(f"进程 {p['pid']}（{p['reason']}）" for p in live[:5])
        raise TempError("processes_alive", f"仍有关联进程在运行，已拒绝清理：{detail} / associated processes are still running")
    real, state, path, marker, info = verify_owned(run_dir)  # last check right before deleting
    clean_tree(path, info)
    record = state["temp"]
    record.update(status="cleaned", cleaned_at=now_iso())
    write_state(real, state)
    return {"run_id": state["id"], "status": "cleaned", "path": str(path)}


def summarize(state):
    """Cheap, read-only view of a run's temp record for the panel (no process scan, no token)."""
    record = state.get("temp") if isinstance(state, dict) else None
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return None
    done = state.get("status") in TERMINAL
    return {"path": record["path"], "status": record.get("status") or "retained",
            "accepted_at": record.get("accepted_at"), "cleaned_at": record.get("cleaned_at"),
            "can_clean": done and record.get("status") != "cleaned",
            "finished_ok": state.get("status") == "completed" and state.get("exit_code") == 0}


def inspect(run_dir, proc="/proc"):
    """Full read-only report for the CLI: ownership, status, associated processes."""
    try:
        real, state, path, marker, _ = verify_owned(run_dir)
    except TempError as error:
        return {"owned": False, "code": error.code, "message": str(error)}
    record = state["temp"]
    try:
        live = associated_processes(path, state, record, proc)
        process_error = None
    except TempError as error:
        live, process_error = [], str(error)
    return {"owned": True, "run_id": state["id"], "run_status": state.get("status"), "exit_code": state.get("exit_code"),
            "temp_status": record.get("status"), "path": str(path), "accepted_at": record.get("accepted_at"),
            "processes": live, "process_check_error": process_error}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Accept or clean a Kiro run's managed temp directory (only that directory).")
    parser.add_argument("action", choices=["status", "accept", "cleanup"])
    parser.add_argument("run_dir", type=Path, help="the run's directory (contains state.json), e.g. <project>/work/kiro-runs/<id>")
    parser.add_argument("--accept", action="store_true", help="cleanup: accept the result and clean in one step")
    parser.add_argument("--allow-dead-runner", action="store_true",
                        help="treat a run stuck in 'running' as finished when its runner is provably gone (processes are still checked)")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            result = inspect(args.run_dir)
        elif args.action == "accept":
            result = accept(args.run_dir, allow_dead_runner=args.allow_dead_runner, by="cli")
        else:
            result = cleanup(args.run_dir, accept_now=args.accept, allow_dead_runner=args.allow_dead_runner, by="cli")
    except TempError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return EXIT_REFUSED
    print(json.dumps(result if args.action == "status" else {"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
