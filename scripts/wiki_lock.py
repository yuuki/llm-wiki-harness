#!/usr/bin/env python3
"""Advisory page lock used by wiki-catalog.py and wiki-concept-stats.py.

Prefers `scripts/wiki-lock.sh` (age-based lockfiles) so catalog writes
cannot forget the vault iron rule. Falls back to fcntl if the helper is
missing. Honors WIKI_VAULT_ROOT / WIKI_LOCK_VAULT so tests never lock
the real vault.
"""

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_SCRIPT = Path(__file__).resolve().parent / "wiki-lock.sh"
LOCK_WAIT_SEC = 60
LOCK_POLL_SEC = 0.2


def vault_root():
    return Path(
        os.environ.get("WIKI_VAULT_ROOT")
        or os.environ.get("WIKI_LOCK_VAULT")
        or Path(__file__).resolve().parent.parent
    ).resolve()


def _env():
    root = str(vault_root())
    env = os.environ.copy()
    env["WIKI_VAULT_ROOT"] = root
    env["WIKI_LOCK_VAULT"] = root
    return env


def _fcntl_lock(rel_path):
    import fcntl
    import hashlib

    root = vault_root()
    lock_dir = root / ".vault-meta" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    lock_path = lock_dir / f"{digest}.fcntl"
    fh = lock_path.open("a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def acquire(rel_path):
    if not LOCK_SCRIPT.is_file():
        return _fcntl_lock(rel_path)
    deadline = time.time() + LOCK_WAIT_SEC
    last = None
    while time.time() < deadline:
        proc = subprocess.run(
            ["bash", str(LOCK_SCRIPT), "acquire", rel_path],
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        if proc.returncode == 0:
            return "wiki-lock"
        last = proc
        if proc.returncode == 75:
            time.sleep(LOCK_POLL_SEC)
            continue
        err = (proc.stderr or "").strip()
        raise RuntimeError(
            f"wiki-lock acquire failed ({proc.returncode}) for {rel_path}: {err}"
        )
    err = ((last.stderr if last else "") or "").strip()
    raise RuntimeError(
        f"wiki-lock timeout after {LOCK_WAIT_SEC}s for {rel_path}: {err}"
    )


def release(rel_path, token):
    if token == "wiki-lock":
        subprocess.run(
            ["bash", str(LOCK_SCRIPT), "release", rel_path],
            capture_output=True,
            text=True,
            env=_env(),
            check=False,
        )
        return
    if token is not None:
        import fcntl
        try:
            fcntl.flock(token.fileno(), fcntl.LOCK_UN)
        finally:
            token.close()


@contextmanager
def page_lock(rel_path):
    token = acquire(rel_path)
    try:
        yield
    finally:
        release(rel_path, token)
