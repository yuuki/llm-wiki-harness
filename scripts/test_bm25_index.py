#!/usr/bin/env python3
"""bm25-index.py のロック待ちと SQLite 鏡像の回帰テスト。

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


class Bm25SqliteMirrorTest(unittest.TestCase):
    """SQLite 鏡像が JSON と同じ点数を返し、古い鏡像は使われないこと。"""

    TEXTS = [
        "分散 トレーシング の サンプリング は tail latency を 見逃す",
        "根本原因分析 の 評価 ベンチマーク と tail latency",
        "KV キャッシュ の 圧縮 と 量子化",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        for i, text in enumerate(self.TEXTS, 1):
            d = self.vault / ".vault-meta" / "chunks" / f"c-00000{i}"
            d.mkdir(parents=True)
            for j in range(2):
                (d / f"chunk-00{j}.json").write_text(json.dumps({
                    "page_address": f"c-00000{i}", "chunk_index": j,
                    "contextualized_text": text if j == 0 else text + " 補足",
                }, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        proc = subprocess.run([sys.executable, str(SCRIPT), "build"],
                              capture_output=True, text=True, env=env, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.mod = self.load_module()

    def tearDown(self):
        self._tmp.cleanup()

    def load_module(self):
        import importlib.util
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        spec = importlib.util.spec_from_file_location("bm25_index_t", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_build_writes_mirror_with_identical_scores(self):
        m = self.mod
        self.assertTrue(m.SQLITE_PATH.is_file())
        js, sq = m.load_index(), m.load_sqlite_index()
        self.assertIsNotNone(sq)
        for q in ("tail latency", "キャッシュ 圧縮", "根本原因分析", "存在しない語"):
            self.assertEqual(m.query(q, 10, index=js), m.query(q, 10, index=sq), q)
        self.assertEqual(m.best_chunk("補足", "c-000002", index=js),
                         m.best_chunk("補足", "c-000002", index=sq))
        self.assertEqual(len(js["vocab"]), len(sq["vocab"]))

    def test_stale_mirror_falls_back_to_json(self):
        m = self.mod
        data = json.loads(m.INDEX_PATH.read_text(encoding="utf-8"))
        data["updated_at"] = "2099-01-01T00:00:00Z"
        m.INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(m.load_sqlite_index())
        self.assertNotIn("_sqlite", m.load_fast_index())


if __name__ == "__main__":
    unittest.main()
