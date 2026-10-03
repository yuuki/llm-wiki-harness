#!/usr/bin/env python3
"""bm25-index.py のロック待ちの回帰テスト。

WIKI_VAULT_ROOT に一時 vault を渡し、本物の wiki には触らない。
"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "bm25-index.py"


class Bm25LockWaitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        chunk_dir = self.vault / ".vault-meta" / "chunks" / "c-000001"
        chunk_dir.mkdir(parents=True)
        (chunk_dir / "chunk-000.json").write_text(json.dumps({
            "page_address": "c-000001",
            "chunk_index": 0,
            "contextualized_text": "再編纂キュー の ロック 待ち",
        }, ensure_ascii=False), encoding="utf-8")
        self.lock_path = self.vault / ".vault-meta" / ".bm25.lock"

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, wait_sec):
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        env["BM25_LOCK_WAIT_SEC"] = str(wait_sec)
        return subprocess.run([sys.executable, str(SCRIPT), "build"],
                              capture_output=True, text=True, env=env, check=False)

    def hold_lock(self):
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_waits_for_held_lock_then_builds(self):
        fd = self.hold_lock()
        timer = threading.Timer(1.0, lambda: os.close(fd))
        timer.start()
        try:
            proc = self.build(wait_sec=30)
        finally:
            timer.join()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("waiting", proc.stderr)
        self.assertTrue((self.vault / ".vault-meta" / "bm25" / "index.json").is_file())

    def test_gives_up_after_wait_deadline(self):
        fd = self.hold_lock()
        try:
            started = time.monotonic()
            proc = self.build(wait_sec=1)
            elapsed = time.monotonic() - started
        finally:
            os.close(fd)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("could not acquire bm25 lock", proc.stderr)
        self.assertGreaterEqual(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
