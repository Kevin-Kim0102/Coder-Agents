"""Temporary directories that are never inside this checkout (a TMPDIR below the repo would make fixtures look like Git
worktrees and trip the legacy-install 'inside the repository' refusal)."""
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def base():
    for candidate in (tempfile.gettempdir(), "/tmp", "/var/tmp"):
        real = Path(candidate).resolve()
        if real != ROOT and ROOT not in real.parents and real.is_dir():
            return str(real)
    raise RuntimeError("no temporary directory outside the repository is available")


def mkdtemp(prefix):
    return tempfile.mkdtemp(prefix=prefix, dir=base())


def TemporaryDirectory(prefix):
    return tempfile.TemporaryDirectory(prefix=prefix, dir=base())
