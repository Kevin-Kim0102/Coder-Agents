"""Durable task sources and verified cross-platform task lifecycle.

Unknown legacy identities stay active: a missing optional fingerprint is never permission
for killing a process. No model calls, task retries, or working-tree mutations occur here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
import fcntl
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ACTIVE = {'checking', 'running'}


def now():
    return datetime.now(timezone.utc).isoformat()


def _atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


@contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def index_path():
    config = Path(os.environ.get('CODER_AGENTS_CONFIG') or Path.home() / '.local/state/kiro-panel/config.json').expanduser()
    return config.parent / 'task-sources.json'


def registered_sources():
    try:
        data = json.loads(index_path().read_text())
    except FileNotFoundError:
        return []
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('sources'), list):
        raise ValueError('任务登记索引格式无效')
    rows = data['sources']
    if any(not isinstance(row, dict) or set(row) != {'cwd', 'root'} or
           any(not isinstance(row[key], str) or not Path(row[key]).is_absolute() for key in ('cwd', 'root')) for row in rows):
        raise ValueError('任务登记路径格式无效')
    return rows


def register(cwd, root):
    row = {'cwd': str(Path(cwd).expanduser().resolve()), 'root': str(Path(root).expanduser().resolve())}
    path = index_path()
    with _locked(path.with_suffix('.lock')):
        rows = [item for item in registered_sources() if item['root'] != row['root']]
        rows.append(row)
        _atomic(path, {'version': 1, 'sources': rows})
    return row


@lru_cache(maxsize=1)
def boot_identity():
    try:
        if sys.platform.startswith('linux'):
            return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if sys.platform == 'darwin':
            output = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, timeout=2, check=True)
            return output.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


class _ProcBSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ('flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid', 'svuid', 'svgid', 'rfu')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'pjobc', 'e_tdev', 'e_tpgid', 'nice')]
    _fields_ += [('start_sec', ctypes.c_uint64), ('start_usec', ctypes.c_uint64)]


def process_identity(pid):
    """Strong PID birth fingerprint, or None if unavailable. Never parse command lines."""
    if type(pid) is not int or pid <= 0: return None
    try:
        if sys.platform.startswith('linux'):
            raw = Path('/proc') / str(pid) / 'stat'
            values = raw.read_text().rsplit(')', 1)[1].split()
            if values[0] == 'Z': return None
            return {'pid': pid, 'start': values[19], 'boot': boot_identity(), 'pgid': int(values[2])}
        if sys.platform == 'darwin':
            info = _ProcBSDInfo()
            lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            size = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
            if size != ctypes.sizeof(info) or info.status == 5: return None
            return {'pid': pid, 'start': str(info.start_sec) + ':' + str(info.start_usec),
                    'boot': boot_identity(), 'pgid': info.pgid}
    except (OSError, ValueError, IndexError):
        return None
    return None


def _mac_zombie(pid):
    # libproc may return no BSD info for a zombie even though kill(pid, 0)
    # succeeds until the parent reaps it. ps can report that specific state.
    try:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                                capture_output=True, text=True, timeout=1)
        return result.returncode == 0 and result.stdout.strip().startswith('Z')
    except (OSError, subprocess.SubprocessError):
        return False


def group_members(pgid):
    """Return current group member fingerprints, or None when enumeration is uncertain.

    Membership alone does not authorize signals: callers may signal only fingerprints
    captured while the recorded leader was still verified alive.
    """
    if type(pgid) is not int or pgid <= 0: return None
    try:
        if sys.platform.startswith('linux'):
            pids = [int(path.name) for path in Path('/proc').iterdir() if path.name.isdigit()]
        elif sys.platform == 'darwin':
            lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
            size = lib.proc_listpids(2, pgid, None, 0)
            if size < 0: return None
            capacity = max(64, size // ctypes.sizeof(ctypes.c_int) + 32)
            values = (ctypes.c_int * capacity)()
            used = lib.proc_listpids(2, pgid, values, ctypes.sizeof(values))
            if used < 0 or used >= ctypes.sizeof(values): return None
            pids = [values[i] for i in range(used // ctypes.sizeof(ctypes.c_int)) if values[i] > 0]
        else:
            return None
        members = []
        for pid in pids:
            if sys.platform.startswith('linux'):
                try:
                    values = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
                    if int(values[2]) != pgid or values[0] == 'Z': continue
                except FileNotFoundError: continue
                except (OSError, ValueError, IndexError): return None
            identity = process_identity(pid)
            if identity is not None and identity.get('pgid') == pgid:
                members.append(identity)
            elif identity is None:
                try: os.kill(pid, 0)
                except ProcessLookupError: continue
                except PermissionError: return None
                if sys.platform == 'darwin':
                    info = _ProcBSDInfo()
                    size = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
                    if size == ctypes.sizeof(info) and info.status == 5 or _mac_zombie(pid): continue
                # A visible group member with an unreadable birth fingerprint is
                # unknown, never proof that the whole group has exited.
                return None
        return members
    except (OSError, ValueError):
        return None


def _group_remaining(child):
    if not isinstance(child, dict) or child.get('pgid') != child.get('pid'):
        return None
    return group_members(child['pgid'])


def identity_status(identity):
    """alive/dead/reused/previous_boot/unknown; unknown never authorizes a signal."""
    if not isinstance(identity, dict) or not identity.get('start') or type(identity.get('pid')) is not int:
        return 'unknown'
    boot = boot_identity()
    if boot and identity.get('boot') and boot != identity['boot']: return 'previous_boot'
    if not boot or not identity.get('boot'): return 'unknown'
    current = process_identity(identity['pid'])
    if current:
        return 'alive' if current['start'] == identity['start'] else 'reused'
    try:
        os.kill(identity['pid'], 0)
    except ProcessLookupError:
        return 'dead'
    except PermissionError:
        return 'unknown'
    # A zombie has no work left but kill(0) may still succeed until its parent reaps it.
    if sys.platform == 'darwin' and _mac_zombie(identity['pid']): return 'dead'
    if sys.platform.startswith('linux'):
        try:
            if (Path('/proc') / str(identity['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z': return 'dead'
        except (OSError, IndexError): pass
    return 'unknown'


def _finish(directory, state, reason):
    updated = dict(state, status='cancelled', exit_code=130, finished_at=now(), interruption_reason=reason)
    updated['diagnostic'] = {'category': 'interrupted', 'message': '任务已中断，请先检查已有改动，再决定是否继续。',
                             'next_step': '由主 Agent 检查工作目录和已有结果后安排后续任务。', 'detail': reason}
    _atomic(Path(directory) / 'state.json', updated)
    fd = os.open(Path(directory) / 'stderr.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as stream: stream.write('\n任务已中断：' + reason + '；请先检查已有改动，不要自动重试。\n')
    return updated


def recover(directory, state):
    """Reconcile positively dead tasks. Live/unknown children prevent a false completion."""
    if state.get('status') not in ACTIVE: return state
    identity = state.get('runner_identity')
    if not identity and type(state.get('runner_pid')) is int:
        pid = state['runner_pid']
        try: os.kill(pid, 0)
        except ProcessLookupError:
            identity = {'pid': pid, 'start': 'legacy-absent', 'boot': boot_identity()}
        except PermissionError: pass
    status = identity_status(identity)
    if status not in {'dead', 'reused', 'previous_boot'}: return state
    child = state.get('child_identity')
    if status != 'previous_boot' and child:
        if identity_status(child) in {'alive', 'unknown'}: return state
        # Leader death does not prove its tools exited. A reused process-group ID
        # is deliberately conservative too; it never authorizes killing that group.
        members = _group_remaining(child)
        if members is None or members: return state
    directory = Path(directory)
    with _locked(directory / '.task.lock'):
        try: current = json.loads((directory / 'state.json').read_text())
        except FileNotFoundError: return state
        if current.get('status') not in ACTIVE: return current
        if current.get('runner_identity') != state.get('runner_identity'): return current
        return _finish(directory, current, 'previous_boot' if status == 'previous_boot' else 'runner_exited')


def _signal(identity, signum, group=False):
    if identity_status(identity) != 'alive': return False
    if group:
        if identity.get('pgid') != identity['pid']: raise ValueError('任务子进程组身份无法确认')
        os.killpg(identity['pid'], signum)
    else:
        os.kill(identity['pid'], signum)
    return True


def stop(directory, timeout=4):
    """Stop one verified runner and the members of its verified child group."""
    directory = Path(directory).expanduser().resolve(strict=True)
    state = recover(directory, json.loads((directory / 'state.json').read_text()))
    if state.get('status') not in ACTIVE: return state
    runner = state.get('runner_identity'); child = state.get('child_identity')
    status = identity_status(runner)
    if status == 'unknown': raise ValueError('无法确认任务进程身份，未停止任何进程')
    # Capture fingerprints BEFORE TERM: the group leader may exit immediately,
    # leaving a tool which ignores TERM. Never signal a reused group by PGID alone.
    members = []
    if child:
        if identity_status(child) == 'alive':
            members = _group_remaining(child)
            if members is None: raise ValueError('无法确认任务子进程组成员，未停止任何进程')
        else:
            remaining = _group_remaining(child)
            if remaining is None or remaining:
                raise ValueError('任务子进程组仍有进程，但身份无法确认；未停止其他进程')
    try:
        if status == 'alive': _signal(runner, signal.SIGTERM)
        if child: _signal(child, signal.SIGTERM, group=True)
    except ProcessLookupError: pass
    deadline = time.monotonic() + timeout
    tracked = [identity for identity in [runner, child, *members] if identity]
    while time.monotonic() < deadline:
        if all(identity_status(identity) != 'alive' for identity in tracked): break
        time.sleep(.05)
    # Verified fingerprints survive the leader's exit; signal individually rather
    # than sending SIGKILL to a PGID that could since have been reused.
    for identity in tracked:
        try: _signal(identity, signal.SIGKILL)
        except ProcessLookupError: pass
    deadline = time.monotonic() + min(1, max(.1, timeout))
    while time.monotonic() < deadline and any(identity_status(identity) == 'alive' for identity in tracked):
        time.sleep(.02)
    with _locked(directory / '.task.lock'):
        current = json.loads((directory / 'state.json').read_text())
        # Preserve a new invocation if the directory was concurrently replaced.
        if current.get('runner_identity') != state.get('runner_identity'):
            raise ValueError('任务记录已变化，请刷新后再检查')
        remaining = _group_remaining(child) if child else []
        if identity_status(runner) == 'alive' or remaining is None or remaining:
            raise ValueError('停止信号已发送，但尚无法确认所有任务进程结束，请稍后检查')
        if current.get('status') not in ACTIVE: return current
        return _finish(directory, current, 'user_stopped')


def read(directory):
    directory = Path(directory).expanduser().resolve(strict=True)
    state = recover(directory, json.loads((directory / 'state.json').read_text()))
    result = ''
    path = directory / 'result.txt'
    if path.is_file():
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 131072))
            result = stream.read().decode('utf-8', errors='replace')
    elif (directory / 'events.jsonl').is_file():
        path = directory / 'events.jsonl'
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 2097152))
            for line in stream.read().decode('utf-8', errors='replace').splitlines():
                try:
                    event = json.loads(line)
                    if event.get('type') == 'runFinished':
                        result = str((event.get('data') or {}).get('finalText') or result)
                except (ValueError, AttributeError): pass
    return {**state, 'run_dir': str(directory), 'result': result}


def list_tasks():
    rows = []
    for source in registered_sources():
        root = Path(source['root'])
        if not root.is_dir(): continue
        for directory in root.iterdir():
            if directory.is_dir() and not directory.is_symlink() and (directory / 'state.json').is_file():
                try: rows.append(read(directory))
                except (OSError, ValueError): continue
    return sorted(rows, key=lambda state: state.get('started_at', ''), reverse=True)


def stop_process_group(process):
    """Runner-owned group cleanup; also reaches tool descendants after leader exit."""
    if process is None: return
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=2)
    except subprocess.TimeoutExpired: pass
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    process.wait()
