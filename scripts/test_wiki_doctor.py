#!/usr/bin/env python3
"""wiki-doctor.py と wiki-context-pack.py の単体テスト(一時 vault、外部プロセスは最小限)。"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def import_script(name, vault):
    os.environ["WIKI_VAULT_ROOT"] = str(vault)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_vault(root):
    for sub in ("wiki/sources", "wiki/concepts", ".vault-meta/locks", ".vault-meta/chunks/c-000001",
                ".vault-meta/bm25", ".raw/papers"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "wiki/sources/@2026__X__Alpha.md").write_text(
        "---\ntitle: Alpha\ntype: source\naddress: c-000001\n---\n# Alpha\n\n## 概要\n\n**命題 A**: 転送より再計算が速い。(Source: [[Alpha]])\n",
        encoding="utf-8")
    (root / "wiki/concepts/Beta.md").write_text(
        "---\ntitle: Beta\ntype: concept\naddress: c-000001\n---\n# Beta\n\n## 定義\n\n本文。\n", encoding="utf-8")
    (root / ".vault-meta/address-counter.txt").write_text("1\n", encoding="utf-8")
    (root / ".vault-meta/chunks/c-000001/chunk-000.json").write_text(
        json.dumps({"page_path": "wiki/sources/Gone.md", "chunk_index": 0}), encoding="utf-8")
    (root / ".vault-meta/bm25/index.json").write_text(
        json.dumps({"schema_version": 1, "updated_at": "2020-01-01T00:00:00Z", "docs": {}}), encoding="utf-8")
    lock = root / ".vault-meta/locks/deadbeef.lock"
    lock.write_text("999999 0 wiki/concepts/Beta.md\n", encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock, (old, old))
    (root / ".raw/.manifest.json").write_text(json.dumps({".raw/papers/missing.pdf": {"hash": "x"}}), encoding="utf-8")
    (root / ".raw/papers/unregistered.pdf").write_bytes(b"%PDF")
    (root / ".vault-meta/recompile-queue.json").write_text("{not json", encoding="utf-8")
    (root / "wiki/hot.md").write_text("# hot\n" + "## e\n\n本文\n" * 7, encoding="utf-8")
    (root / "wiki/log.md").write_text("stray line\n## [2026-01-01] x\n", encoding="utf-8")
    (root / "wiki/sources/junk.md.tmp").write_text("x", encoding="utf-8")


class DoctorTest(unittest.TestCase):
    def test_detects_each_failure_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            make_vault(root)
            doc = import_script("wiki-doctor", root)
            ctx = {"pages": list(doc.wiki_pages()),
                   "cluster_pages": list(doc.cluster_pages()),
                   "address_pages": list(doc.address_pages())}
            rep = doc.Report()
            for name, fn in doc.CHECKS:
                if name in ("ollama", "git_dirty_wiki"):
                    continue
                fn(rep, ctx)
            by = {r["check"]: r for r in rep.rows}
            self.assertEqual(by["address_counter"]["status"], "FAIL")  # 重複 c-000001
            self.assertEqual(by["stale_chunks"]["status"], "WARN")
            self.assertIn("Gone.md", by["stale_chunks"]["detail"])
            self.assertEqual(by["unchunked_pages"]["status"], "WARN")
            self.assertEqual(by["bm25_schema"]["status"], "WARN")
            self.assertEqual(by["bm25_freshness"]["status"], "WARN")
            self.assertEqual(by["locks"]["status"], "WARN")
            self.assertEqual(by["manifest"]["status"], "WARN")
            self.assertIn("未登録原本 1 件", by["manifest"]["detail"])
            self.assertEqual(by["ledgers"]["status"], "FAIL")
            self.assertEqual(by["hot_window"]["status"], "WARN")
            self.assertEqual(by["log_head"]["status"], "WARN")
            self.assertEqual(by["tmp_residue"]["status"], "WARN")
            self.assertEqual(by["auto_commit_disabled"]["status"], "WARN")
            self.assertTrue(all(r["fix"] for r in rep.rows if r["status"] in ("WARN", "FAIL") and r["check"] != "log_head"))

    def test_clean_vault_is_ok_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            make_vault(root)
            # 直す
            (root / "wiki/concepts/Beta.md").write_text(
                "---\ntitle: Beta\ntype: concept\naddress: c-000002\n---\n# Beta\n", encoding="utf-8")
            (root / ".vault-meta/address-counter.txt").write_text("2\n", encoding="utf-8")
            (root / ".vault-meta/recompile-queue.json").write_text("{}", encoding="utf-8")
            (root / ".vault-meta/locks/deadbeef.lock").unlink()
            (root / "wiki/sources/junk.md.tmp").unlink()
            (root / ".vault-meta/auto-commit.disabled").write_text("", encoding="utf-8")
            doc = import_script("wiki-doctor", root)
            rc = doc.main(["--json", "--only", "address_counter,ledgers,locks,tmp_residue,auto_commit_disabled"])
            self.assertEqual(rc, 0)

    def test_address_scan_includes_companion_pages(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            make_vault(root)
            (root / "wiki/concepts/Beta.md").write_text(
                "---\ntitle: Beta\ntype: concept\naddress: c-000002\n---\n# Beta\n",
                encoding="utf-8")
            (root / "wiki/briefs").mkdir(parents=True, exist_ok=True)
            (root / "wiki/briefs/@2026__X__Alpha.md").write_text(
                "---\ntitle: 紹介\ntype: brief\naddress: c-000001\n---\n# 紹介\n",
                encoding="utf-8")
            doc = import_script("wiki-doctor", root)
            ctx = {"pages": list(doc.wiki_pages()),
                   "cluster_pages": list(doc.cluster_pages()),
                   "address_pages": list(doc.address_pages())}
            self.assertFalse(any(p.name == "@2026__X__Alpha.md" and "briefs" in p.parts
                                 for p in ctx["pages"]))
            self.assertTrue(any("briefs" in p.parts for p in ctx["address_pages"]))
            rep = doc.Report()
            doc.check_address_counter(rep, ctx)
            by = {r["check"]: r for r in rep.rows}
            self.assertEqual(by["address_counter"]["status"], "FAIL")
            self.assertIn("重複", by["address_counter"]["detail"])

    def test_unknown_check_is_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            make_vault(root)
            doc = import_script("wiki-doctor", root)
            self.assertEqual(doc.main(["--only", "nope"]), 2)


class CompanionIndexTest(unittest.TestCase):
    def test_collect_pages_skips_asks_and_briefs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            for sub in ("wiki/sources", "wiki/asks", "wiki/briefs"):
                (root / sub).mkdir(parents=True, exist_ok=True)
            (root / "wiki/sources/@2026__X__A.md").write_text("# A\n", encoding="utf-8")
            (root / "wiki/asks/@2026__X__A.md").write_text("# ask\n", encoding="utf-8")
            (root / "wiki/briefs/@2026__X__A.md").write_text("# brief\n", encoding="utf-8")
            prefix = import_script("contextual-prefix", root)
            got = {p.relative_to(root).as_posix() for p in prefix.collect_pages("--all")}
            self.assertEqual(got, {"wiki/sources/@2026__X__A.md"})
            self.assertTrue(prefix.is_companion_page(root / "wiki/briefs/@2026__X__A.md"))
            self.assertFalse(prefix.is_companion_page(root / "wiki/sources/@2026__X__A.md"))

    def test_tiling_exclude_lists_include_companions(self):
        for name in ("tiling-check.py", "boundary-score.py"):
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn('"brief"', text)
            self.assertIn('"ask"', text)
            self.assertIn("wiki/briefs/", text)
            self.assertIn("wiki/asks/", text)


class ContextPackTest(unittest.TestCase):
    def test_pinned_pages_fill_then_outline_then_omit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            make_vault(root)
            big = "---\ntitle: Gamma\ntype: concept\naddress: c-000003\n---\n# Gamma\n\n## 定義\n\n" + ("長い本文。" * 400) + "\n"
            (root / "wiki/concepts/Gamma.md").write_text(big, encoding="utf-8")
            env = dict(os.environ, WIKI_VAULT_ROOT=str(root))
            out = root / "pack.md"
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "wiki-context-pack.py"), "再計算と転送", "--no-retrieve",
                 "--budget-tokens", "900", "--per-page", "400",
                 "--pages", "wiki/sources/@2026__X__Alpha.md", "wiki/concepts/Gamma.md", "wiki/concepts/Beta.md",
                 "--exclude", "wiki/concepts/Beta.md", "--out", str(out), "--json"],
                capture_output=True, text=True, env=env, cwd=str(root), timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertLessEqual(summary["used"], 900)
            names = [Path(x["page"]).name for x in summary["included"]]
            self.assertIn("@2026__X__Alpha.md", names)
            self.assertNotIn("Beta.md", names + [Path(x["page"]).name for x in summary["outlined"] + summary["omitted"]])
            text = out.read_text(encoding="utf-8")
            self.assertIn("# コンテキスト束: 再計算と転送", text)
            self.assertIn("### 概要", text)  # 抜粋の見出しは 1 段下がる
            self.assertNotIn("\n# Alpha\n", text)  # ページ自身の h1 は落ちる
            self.assertIn("消費", text)

    def test_empty_candidates_exit_3(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            make_vault(root)
            env = dict(os.environ, WIKI_VAULT_ROOT=str(root))
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "wiki-context-pack.py"), "x", "--no-retrieve", "--pages", "wiki/none.md"],
                capture_output=True, text=True, env=env, cwd=str(root), timeout=60)
            self.assertEqual(proc.returncode, 3)


if __name__ == "__main__":
    unittest.main()
