#!/usr/bin/env python3
"""wiki-manifest.py と、wiki-doctor.py の旧形式 manifest の扱いの単体テスト。"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def import_script(name, vault):
    os.environ["WIKI_VAULT_ROOT"] = str(vault)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        (self.root / ".raw/papers").mkdir(parents=True)
        (self.root / ".vault-meta/locks").mkdir(parents=True)
        self.manifest = self.root / ".raw/.manifest.json"
        self.env = dict(os.environ, WIKI_VAULT_ROOT=str(self.root))
        self.cmd = [sys.executable, str(SCRIPTS / "wiki-manifest.py")]

    def tearDown(self):
        self.td.cleanup()

    def run_cli(self, *args):
        return subprocess.run(self.cmd + list(args), env=self.env, capture_output=True, text=True)

    def test_normalize_moves_nested_entries_and_resolves_conflicts(self):
        mod = import_script("wiki-manifest", self.root)
        data = {
            ".raw/papers/a.pdf": {"ingested_at": "2026-09-01"},
            ".raw/papers/b.pdf": {"ingested_at": "2026-09-20"},
            "address_map": {"wiki/concepts/X.md": "c-000001"},
            "sources": {
                ".raw/papers/a.pdf": {"ingested_at": "2026-09-10"},   # 新しいので sources 側が勝つ
                ".raw/papers/b.pdf": {"ingested_at": "2026-09-20"},   # 同じなら最上位を残す
                ".raw/papers/c.pdf": {"ingested_at": "2026-09-12"},
                "note-only": {"ingested_at": "2026-09-12"},           # .raw/ で始まらないので残す
            },
        }
        out, moved, conflicts = mod.normalize(data)
        self.assertEqual(out[".raw/papers/a.pdf"]["ingested_at"], "2026-09-10")
        self.assertEqual(out[".raw/papers/b.pdf"]["ingested_at"], "2026-09-20")
        self.assertIn(".raw/papers/c.pdf", out)
        self.assertEqual(out["sources"], {"note-only": {"ingested_at": "2026-09-12"}})
        self.assertEqual(out["address_map"], {"wiki/concepts/X.md": "c-000001"})
        self.assertEqual(moved, 2)
        self.assertEqual(sorted(conflicts), [(".raw/papers/a.pdf", "sources"), (".raw/papers/b.pdf", "top")])
        self.assertIn("sources", data)  # 入力は変更しない

    def test_get_reads_both_shapes_and_reports_absence(self):
        self.manifest.write_text(json.dumps(
            {".raw/papers/a.pdf": {"hash": "x"}, "sources": {".raw/papers/c.pdf": {"hash": "y"}}}), encoding="utf-8")
        self.assertEqual(json.loads(self.run_cli("get", ".raw/papers/a.pdf").stdout), {"hash": "x"})
        self.assertEqual(json.loads(self.run_cli("get", ".raw/papers/c.pdf").stdout), {"hash": "y"})
        self.assertEqual(self.run_cli("get", ".raw/papers/none.pdf").returncode, 3)

    def test_put_writes_top_level_merges_fields_and_lifts_nested(self):
        self.manifest.write_text(json.dumps(
            {"address_map": {"p": "c-000001"},
             "sources": {".raw/papers/c.pdf": {"hash": "y", "note": "keep"}}}, indent=2), encoding="utf-8")
        r = self.run_cli("put", ".raw/papers/c.pdf", "--json", '{"hash": "z", "ingested_at": "2026-10-03"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(data[".raw/papers/c.pdf"], {"hash": "z", "note": "keep", "ingested_at": "2026-10-03"})
        self.assertNotIn("sources", data)                       # 空になった sources は消える
        self.assertEqual(data["address_map"], {"p": "c-000001"})  # 他の最上位キーは触らない
        self.assertEqual(self.run_cli("put", "papers/c.pdf", "--json", "{}").returncode, 1)  # `.raw/` で始まらない

    def test_put_creates_manifest_when_missing(self):
        r = self.run_cli("put", ".raw/papers/new.pdf", "--json", '{"hash": "n"}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.manifest.read_text(encoding="utf-8")), {".raw/papers/new.pdf": {"hash": "n"}})

    def test_normalize_cli_dry_run_then_write_is_idempotent(self):
        self.manifest.write_text(json.dumps(
            {".raw/papers/a.pdf": {"hash": "x"}, "sources": {".raw/papers/c.pdf": {"hash": "y"}}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        before = self.manifest.read_text(encoding="utf-8")
        self.assertEqual(self.run_cli("normalize").returncode, 0)
        self.assertEqual(self.manifest.read_text(encoding="utf-8"), before)  # dry-run は書かない
        self.assertEqual(self.run_cli("normalize", "--write").returncode, 0)
        self.assertEqual(set(json.loads(self.manifest.read_text(encoding="utf-8"))),
                         {".raw/papers/a.pdf", ".raw/papers/c.pdf"})
        self.assertIn("変更なし", self.run_cli("normalize", "--write").stdout)

    def test_doctor_counts_nested_entries_as_registered(self):
        (self.root / ".raw/papers/c.pdf").write_bytes(b"%PDF")
        self.manifest.write_text(json.dumps({"sources": {".raw/papers/c.pdf": {"hash": "y"}}}), encoding="utf-8")
        doc = import_script("wiki-doctor", self.root)
        rep = doc.Report()
        doc.check_manifest(rep, {})
        row = rep.rows[0]
        self.assertEqual(row["status"], "WARN")
        self.assertIn("未登録原本 0 件", row["detail"])
        self.assertIn("旧形式 1 件", row["detail"])
        self.assertIn("wiki-manifest.py normalize", row["fix"])


if __name__ == "__main__":
    unittest.main()
