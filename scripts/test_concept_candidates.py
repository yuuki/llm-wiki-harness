#!/usr/bin/env python3
"""concept-candidates.py と wiki-resolve.py の台帳ヒントの試験。

    python3 -m unittest scripts/test_concept_candidates.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
LEDGER = SCRIPTS / "concept-candidates.py"
RESOLVE = SCRIPTS / "wiki-resolve.py"


class ConceptCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "wiki/concepts").mkdir(parents=True)
        (self.vault / "wiki/sources").mkdir(parents=True)
        (self.vault / "wiki/entities").mkdir(parents=True)
        (self.vault / ".vault-meta").mkdir()
        (self.vault / "wiki/concepts/既存概念.md").write_text(
            "---\ntype: concept\ntitle: \"既存概念\"\naliases:\n  - Existing\n---\n\n# 既存概念\n", encoding="utf-8")
        self.env = dict(os.environ, WIKI_VAULT_ROOT=str(self.vault))

    def tearDown(self):
        self.tmp.cleanup()

    def run_ok(self, script, *args, expect=0):
        proc = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                              env=self.env, cwd=str(self.vault))
        self.assertEqual(proc.returncode, expect, proc.stderr)
        return proc

    def ledger(self, *args):
        return json.loads(self.run_ok(LEDGER, *args).stdout)

    def test_add_counts_documents_not_chapters_and_reports_ready(self):
        out = self.ledger("add", "--name", "フローレットスイッチング", "--source", "[[@2026__arXiv__A]]",
                          "--reason", "上限超過", "--date", "2026-09-01")
        self.assertTrue(out["added"])
        self.assertEqual(out["source_count"], 1)
        self.assertFalse(out["ready"])
        # 同じ言及は二重に積まない
        out = self.ledger("add", "--name", "フローレットスイッチング", "--source", "@2026__arXiv__A",
                          "--reason", "上限超過", "--date", "2026-09-01")
        self.assertFalse(out["added"])
        self.assertEqual(out["mentions"], 1)
        # 同じ書籍の 2 章は 1 文書
        self.ledger("add", "--name", "flowlet switching", "--alias", "フローレットスイッチング",
                    "--source", "@2025__Book__B - Chapter 3 Load Balancing", "--date", "2026-09-02")
        out = self.ledger("add", "--name", "フローレットスイッチング",
                          "--source", "@2025__Book__B - Chapter 7 ECMP", "--date", "2026-09-03")
        self.assertEqual(len(out["sources"]), 3)
        self.assertEqual(out["source_count"], 2)
        self.assertTrue(out["ready"])
        self.assertIn("flowlet switching", out["aliases"])
        # 別名でも同じ候補に着く
        show = self.ledger("show", "flowlet switching")
        self.assertEqual(show["name"], "フローレットスイッチング")
        self.assertEqual(len(show["mention_log"]), 3)
        ready = self.ledger("list", "--ready")
        self.assertEqual([r["name"] for r in ready["candidates"]], ["フローレットスイッチング"])

    def test_promote_reject_reopen_and_exists_as(self):
        self.ledger("add", "--name", "既存概念", "--source", "@2026__X__S1", "--date", "2026-09-01")
        rows = self.ledger("list")["candidates"]
        self.assertEqual(rows[0]["exists_as"], "wiki/concepts/既存概念.md")
        report = self.run_ok(LEDGER, "report").stdout
        self.assertIn("promote し忘れ", report)
        self.assertIn("## Concept Candidates", report)
        out = self.ledger("promote", "既存概念", "--page", "wiki/concepts/既存概念.md")
        self.assertEqual(out["state"], "promoted")
        self.assertEqual(out["promoted_to"], "wiki/concepts/既存概念.md")
        self.run_ok(LEDGER, "promote", "既存概念", "--page", "wiki/concepts/無い.md", expect=3)
        self.ledger("add", "--name", "捨てる候補", "--source", "@2026__X__S1", "--date", "2026-09-01")
        self.run_ok(LEDGER, "reject", "捨てる候補", expect=2)  # reason 必須
        out = self.ledger("reject", "捨てる候補", "--reason", "単一ソースで閉じる用語")
        self.assertEqual(out["state"], "rejected")
        # rejected は ready にならない
        self.ledger("add", "--name", "捨てる候補", "--source", "@2026__Y__S2", "--date", "2026-09-02")
        self.assertEqual(self.ledger("list", "--ready")["count"], 0)
        out = self.ledger("reopen", "捨てる候補")
        self.assertEqual(out["state"], "pending")
        self.assertTrue(out["ready"])
        self.run_ok(LEDGER, "show", "無い名前", expect=3)

    def test_seed_from_log_is_idempotent_and_skips_non_ingest(self):
        log_text = "\n".join([
            "## [2026-09-06] ingest-paper | Network Load Balancing",
            "- Source: [[@2026__arXiv__NLB]]",
            "- Deferred: [[フローレットスイッチング]], [[データセンターネットワークロードバランシング]]",
            "## [2026-09-05] tooling | グラフ路",
            "- Deferred: 2 ホップ既定化は効果を測ってから。",
            "## [2026-09-04] ingest-book | 本",
            "- Source: [[@2025__Book__B - Chapter 3 X]], [[@2025__Book__B - Chapter 4 Y]]",
            "- Deferred: フローレットスイッチング, 既存概念 — 上限超過のため保留。",
            "## [2026-09-03] ingest-paper | なし例",
            "- Deferred: なし(上限内)",
            "",
        ])
        (self.vault / "wiki/log.md").write_text(log_text, encoding="utf-8")
        dry = self.ledger("seed-from-log", "--dry-run")
        names = sorted(it["name"] for it in dry["items"])
        # tooling の Deferred と「なし」と既存 concept 同名は入らない
        self.assertEqual(names, ["データセンターネットワークロードバランシング", "フローレットスイッチング", "フローレットスイッチング"])
        out = self.ledger("seed-from-log")
        self.assertEqual(out["added"], 3)
        self.assertEqual(out["candidates"], 2)
        again = self.ledger("seed-from-log")
        self.assertEqual(again["added"], 0)
        fl = self.ledger("show", "フローレットスイッチング")
        self.assertEqual(fl["source_count"], 2)  # 論文 1 + 書籍 1(章 2 つは 1 文書)
        self.assertTrue(fl["ready"])
        report = self.run_ok(LEDGER, "report").stdout
        self.assertIn("新設を検討する候補(1 件)", report)
        self.assertIn("フローレットスイッチング", report)

    def test_resolve_hint_appears_only_without_page_hit(self):
        self.ledger("add", "--name", "フローレットスイッチング", "--alias", "flowlet",
                    "--source", "@2026__arXiv__A", "--source", "@2025__Book__B - Chapter 3", "--date", "2026-09-01")
        proc = self.run_ok(RESOLVE, 'concept:"フローレットスイッチング"', "--compact", "--quiet")
        line = proc.stdout.strip()
        self.assertTrue(line.endswith("\tNONE\tledger:フローレットスイッチング(2 docs, pending)"), line)
        proc = self.run_ok(RESOLVE, "flowlet", "--quiet")
        data = json.loads(proc.stdout)
        self.assertEqual(data["candidates"], [])
        self.assertEqual(data["candidate_ledger"]["source_count"], 2)
        self.assertTrue(data["candidate_ledger"]["ready"])
        # entity 指定では出ない。ページに当たるときも出ない
        proc = self.run_ok(RESOLVE, 'entity:"フローレットスイッチング"', "--quiet")
        self.assertNotIn("candidate_ledger", json.loads(proc.stdout))
        proc = self.run_ok(RESOLVE, 'concept:"既存概念"', "--quiet")
        data = json.loads(proc.stdout)
        self.assertTrue(data["candidates"])
        self.assertNotIn("candidate_ledger", data)


if __name__ == "__main__":
    unittest.main()
