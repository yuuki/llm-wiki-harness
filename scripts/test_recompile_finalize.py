#!/usr/bin/env python3
"""recompile-finalize.sh の試験。

WIKI_VAULT_ROOT で一時 vault(git リポジトリ)を指し、実 wiki には触れない。
2026-09-25 の事故(並行セッションの bm25 ロックで refresh が落ち、`set -e` で
キューの done が飛んだ)を、ロックを握ったまま確定して再現する。
"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from test_recompile_queue import concept  # noqa: E402


class RecompileFinalizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "wiki/concepts").mkdir(parents=True)
        (self.vault / ".vault-meta").mkdir()
        self.write("wiki/concepts/Big.md", concept("Big", inbox=12, topics=2))
        self.write("wiki/log.md", "## [2026-09-24] lint | old\n")
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.queue_cmd("refresh")
        self.queue_cmd("mark", "Big", "--state", "in_progress")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.write("wiki/concepts/Big.md", concept("Big", inbox=1, topics=3, recompiled="2026-09-25"))
        self.log_file = self.write("entry.md", "## [2026-09-25] recompile | Big\n- Page: [[Big]]\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def env(self, **extra):
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        env.update(extra)
        return env

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.vault, capture_output=True,
                              text=True, check=True).stdout

    def queue_cmd(self, *args):
        subprocess.run([sys.executable, str(SCRIPTS / "recompile-queue.py"), *args],
                       cwd=self.vault, env=self.env(), capture_output=True, check=True)

    def finalize(self, *args, **env):
        return subprocess.run(["bash", str(SCRIPTS / "recompile-finalize.sh"), *args],
                              cwd=self.vault, env=self.env(**env), capture_output=True,
                              text=True, check=False)

    def state(self):
        q = json.loads((self.vault / ".vault-meta/recompile-queue.json").read_text(encoding="utf-8"))
        return q["entries"]["wiki/concepts/Big.md"]["state"]

    def test_refresh_failure_still_marks_done_and_commits(self):
        lock = self.vault / ".vault-meta/.bm25.lock"
        fd = os.open(str(lock), os.O_CREAT | os.O_WRONLY, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            res = self.finalize("Big", "--log", str(self.log_file), BM25_LOCK_WAIT_SEC="0")
        finally:
            os.close(fd)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WARN: wiki-retrieve-refresh failed", res.stderr)
        self.assertEqual(self.state(), "done")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "wiki: recompile Big")
        committed = set(self.git("show", "--name-only", "--format=", "HEAD").split())
        self.assertEqual(committed, {"wiki/concepts/Big.md", "wiki/log.md",
                                     ".vault-meta/recompile-queue.json"})
        self.assertTrue((self.vault / "wiki/log.md").read_text(encoding="utf-8")
                        .startswith("## [2026-09-25] recompile | Big"))

    def test_commit_leaves_unrelated_changes_alone(self):
        self.write("wiki/concepts/Other.md", "unrelated\n")
        res = self.finalize("wiki/concepts/Big.md", "--log", str(self.log_file))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Other.md", self.git("status", "--porcelain"))

    def test_no_commit_updates_ledgers_only(self):
        res = self.finalize("Big", "--log", str(self.log_file), "--no-commit")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.state(), "done")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "base")

    def test_verdicts_and_resolve_are_recorded_in_the_same_commit(self):
        vpath = self.write("verdicts.json", json.dumps([
            {"id": "c1", "page": "wiki/concepts/Big.md", "line": 9, "claim": "命題 1。",
             "verdict": "not_in_source", "note": "数値が無い → 手当て: 削除"}], ensure_ascii=False))
        res = self.finalize("Big", "--log", str(self.log_file), "--verdicts", str(vpath),
                            "--resolve", "c1", "数値を削除した")
        self.assertEqual(res.returncode, 0, res.stderr)
        ledger = json.loads((self.vault / ".vault-meta/claim-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["verdicts"]["c1"]["resolved_note"], "数値を削除した")
        committed = set(self.git("show", "--name-only", "--format=", "HEAD").split())
        self.assertIn(".vault-meta/claim-audit.json", committed)
        self.assertTrue(any(p.startswith("wiki/meta/claim-audit-") for p in committed))
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no", "--", ".vault-meta", "wiki/meta").strip(), "")

    def test_resolve_without_verdicts_is_usage_error(self):
        res = self.finalize("Big", "--log", str(self.log_file), "--resolve", "c1", "x")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.state(), "in_progress")

    def test_missing_log_is_usage_error(self):
        res = self.finalize("Big")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.state(), "in_progress")


if __name__ == "__main__":
    unittest.main()
