#!/usr/bin/env python3
"""allocate-address.sh の採番ゲート試験。

採番は WIKI_ALLOCATE_OK=1 のときだけ通る。--peek / --rebuild は環境変数無しで
動く。実 vault のカウンタは触らない。

  python3 scripts/test_allocate_address.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REAL = SCRIPTS / "allocate-address.sh"


class AllocateGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="alloc-gate-")
        self.vault = Path(self.tmp.name)
        (self.vault / "scripts").mkdir()
        (self.vault / "wiki").mkdir()
        (self.vault / ".vault-meta").mkdir()
        self.script = self.vault / "scripts" / "allocate-address.sh"
        shutil.copy2(str(REAL), str(self.script))
        self.script.chmod(0o755)
        self.counter = self.vault / ".vault-meta" / "address-counter.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def run_alloc(self, *args, env_ok=None):
        env = os.environ.copy()
        env.pop("WIKI_ALLOCATE_OK", None)
        if env_ok is not None:
            env["WIKI_ALLOCATE_OK"] = env_ok
        return subprocess.run(
            ["bash", str(self.script)] + list(args),
            cwd=str(self.vault),
            env=env,
            capture_output=True,
            text=True,
        )

    def test_allocate_without_env_is_refused(self):
        proc = self.run_alloc()
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn("wiki-page-write.py", proc.stderr)
        self.assertFalse(self.counter.exists())

    def test_allocate_with_ok_reserves(self):
        proc = self.run_alloc(env_ok="1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "c-000001")
        self.assertEqual(self.counter.read_text(encoding="utf-8").strip(), "2")

    def test_peek_does_not_need_env(self):
        self.counter.write_text("7\n", encoding="utf-8")
        peek = self.run_alloc("--peek")
        self.assertEqual(peek.returncode, 0, peek.stderr)
        self.assertEqual(peek.stdout.strip(), "7")
        refused = self.run_alloc()
        self.assertEqual(refused.returncode, 4, refused.stderr)
        self.assertEqual(self.counter.read_text(encoding="utf-8").strip(), "7")

    def test_empty_ok_is_refused(self):
        proc = self.run_alloc(env_ok="")
        self.assertEqual(proc.returncode, 4, proc.stderr)

    def test_page_write_sets_the_gate(self):
        """wiki-page-write.py が本物の allocate-address.sh を呼べること。"""
        (self.vault / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
        lock = SCRIPTS / "wiki-lock.sh"
        shutil.copy2(str(lock), str(self.vault / "scripts" / "wiki-lock.sh"))
        (self.vault / "scripts" / "wiki-lock.sh").chmod(0o755)
        page = """---
type: source
title: "採番ゲート"
date: 2026-09-05 21:00
created: 2026-09-05
updated: 2026-09-05
tags:
  - 2026/09/05
  - source
status: seed
related: []
sources: []
source_type: paper
---

# 採番ゲート
"""
        spec = self.vault / "spec.json"
        spec.write_text(
            '[{"path": "wiki/sources/@2026__TEST__Alloc.md", "content": %s}]\n'
            % json.dumps(page),
            encoding="utf-8")
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        env.pop("WIKI_ALLOCATE_OK", None)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "wiki-page-write.py"),
             "--batch", str(spec)],
            cwd=str(self.vault),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        written = (self.vault / "wiki/sources/@2026__TEST__Alloc.md").read_text(
            encoding="utf-8")
        self.assertIn("address: c-000001", written)


if __name__ == "__main__":
    unittest.main(verbosity=1)
