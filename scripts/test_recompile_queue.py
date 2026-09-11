#!/usr/bin/env python3
"""recompile-queue.py の試験。

WIKI_VAULT_ROOT で一時 vault を指し、実 wiki には触れない。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run_script(args, vault):
    env = os.environ.copy()
    env["WIKI_VAULT_ROOT"] = str(vault)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "recompile-queue.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault),
        check=False,
    )


def concept(title, inbox, topics=1, recompiled=None):
    fm = ["---", "type: concept", f'title: "{title}"']
    if recompiled:
        fm.append(f"recompiled: {recompiled}")
    fm.append("---")
    body = [f"# {title}", "", "## 定義", "", "定義。", ""]
    for i in range(topics):
        body += [f"## 主題 {i + 1}", "", f"- **命題 {i + 1}。**", "  - 根拠: [[@2020__X__Y]] — 一行", ""]
    body += ["## 未編纂の観察", ""]
    body += [f"- 観察 {i + 1}。(Source: [[@2020__X__Y]])" for i in range(inbox)]
    body += ["", "## 関連", "", "## 出典", ""]
    return "\n".join(fm + body) + "\n"


class RecompileQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "wiki/concepts").mkdir(parents=True)
        (self.vault / ".vault-meta").mkdir()
        self.write("wiki/concepts/Big.md", concept("Big", inbox=12, topics=2))
        self.write("wiki/concepts/Mid.md", concept("Mid", inbox=6))
        self.write("wiki/concepts/Small.md", concept("Small", inbox=2))
        self.write("wiki/concepts/Orphan.md", concept("Orphan", inbox=16, topics=0))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_ok(self, *args):
        res = run_script(list(args), self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res

    def queue(self):
        return json.loads((self.vault / ".vault-meta/recompile-queue.json").read_text(encoding="utf-8"))

    def test_refresh_adds_debt_pages_as_pending_sorted_by_inbox(self):
        out = json.loads(self.run_ok("refresh").stdout)
        self.assertEqual(out["debt_pages"], 3)
        self.assertEqual(sorted(out["added"]), ["wiki/concepts/Big.md", "wiki/concepts/Mid.md", "wiki/concepts/Orphan.md"])
        self.assertEqual(out["counts"]["pending"], 3)
        self.assertEqual(out["waiting_bullets"], 12 + 6 + 16)
        q = self.queue()
        self.assertEqual(list(q["entries"]), ["wiki/concepts/Orphan.md", "wiki/concepts/Big.md", "wiki/concepts/Mid.md"])
        self.assertNotIn("wiki/concepts/Small.md", q["entries"])
        self.assertEqual(q["entries"]["wiki/concepts/Big.md"]["topic_sections"], 2)

    def test_refresh_resolves_pages_whose_debt_disappeared_and_reenters_done_pages(self):
        self.run_ok("refresh")
        # Mid が再編纂されて受信箱が 1 件に減った
        self.write("wiki/concepts/Mid.md", concept("Mid", inbox=1, recompiled="2026-09-11"))
        out = json.loads(self.run_ok("refresh").stdout)
        self.assertEqual(out["resolved"], ["wiki/concepts/Mid.md"])
        mid = self.queue()["entries"]["wiki/concepts/Mid.md"]
        self.assertEqual(mid["state"], "done")
        self.assertEqual(mid["recompiled"], "2026-09-11")
        self.assertEqual(mid["attempts"][-1]["by"], "refresh")
        # ingest が積み増して再び負債になった
        self.write("wiki/concepts/Mid.md", concept("Mid", inbox=7, recompiled="2026-09-11"))
        out = json.loads(self.run_ok("refresh").stdout)
        self.assertEqual(out["reentered"], ["wiki/concepts/Mid.md"])
        self.assertEqual(self.queue()["entries"]["wiki/concepts/Mid.md"]["state"], "pending")

    def test_mark_records_attempts_and_blocks_after_repeated_rejections(self):
        self.run_ok("refresh")
        res = run_script(["mark", "Big", "--state", "rejected"], self.vault)
        self.assertEqual(res.returncode, 2, "rejected には reason が必須")

        out = json.loads(self.run_ok("mark", "Big", "--state", "rejected", "--reason", "命題が観察の 1:1 言い換え", "--by", "human").stdout)
        self.assertEqual(out["state"], "rejected")
        self.assertEqual(out["rejection_reasons"], ["命題が観察の 1:1 言い換え"])

        self.run_ok("mark", "wiki/concepts/Big.md", "--state", "rejected", "--reason", "出典を 2 件落とした", "--by", "human")
        out = json.loads(self.run_ok("mark", "[[Big]]", "--state", "rejected", "--reason", "節が 7 を超えた", "--by", "human").stdout)
        self.assertEqual(out["state"], "blocked")
        self.assertEqual(out["rejections"], 3)

        nxt = json.loads(self.run_ok("next", "--limit", "10").stdout)
        self.assertNotIn("wiki/concepts/Big.md", [n["path"] for n in nxt])
        nxt = json.loads(self.run_ok("next", "--limit", "10", "--include-blocked").stdout)
        self.assertIn("wiki/concepts/Big.md", [n["path"] for n in nxt])

        out = json.loads(self.run_ok("reopen", "Big").stdout)
        self.assertEqual(out["state"], "pending")
        self.assertEqual(out["attempts"][-1]["state"], "reopened")

    def test_next_orders_in_progress_then_pending_by_inbox_and_carries_reasons(self):
        self.run_ok("refresh")
        self.run_ok("mark", "Mid", "--state", "in_progress")
        self.run_ok("mark", "Orphan", "--state", "rejected", "--reason", "主題が立たない", "--by", "human")
        nxt = json.loads(self.run_ok("next", "--limit", "3").stdout)
        self.assertEqual([n["path"] for n in nxt],
                         ["wiki/concepts/Mid.md", "wiki/concepts/Big.md", "wiki/concepts/Orphan.md"])
        self.assertEqual(nxt[2]["rejection_reasons"], ["主題が立たない"])
        self.assertEqual(nxt[0]["state"], "in_progress")

    def test_mark_done_and_skipped_are_kept_by_refresh(self):
        self.run_ok("refresh")
        self.run_ok("mark", "Orphan", "--state", "skipped", "--reason", "親子化を先にやる")
        out = json.loads(self.run_ok("mark", "Big", "--state", "done").stdout)
        self.assertIsNotNone(out["resolved_at"])
        self.run_ok("refresh")
        q = self.queue()["entries"]
        self.assertEqual(q["wiki/concepts/Orphan.md"]["state"], "skipped")
        # Big の受信箱はまだ 12 件なので、done は refresh で pending に戻る(再入)
        self.assertEqual(q["wiki/concepts/Big.md"]["state"], "pending")

    def test_show_and_missing_page(self):
        self.run_ok("refresh")
        out = json.loads(self.run_ok("show", "Big").stdout)
        self.assertEqual(out["inbox_bullets"], 12)
        res = run_script(["show", "Nope"], self.vault)
        self.assertEqual(res.returncode, 3)
        res = run_script(["mark", "Nope", "--state", "done"], self.vault)
        self.assertEqual(res.returncode, 3)

    def test_report_is_markdown_with_counts_top_and_blocked(self):
        self.run_ok("refresh")
        for reason in ("理由 1", "理由 2", "理由 3"):
            self.run_ok("mark", "Mid", "--state", "rejected", "--reason", reason, "--by", "human")
        report = self.run_ok("report").stdout
        self.assertTrue(report.startswith("## Recompile Queue\n"))
        self.assertIn("pending 2", report)
        self.assertIn("blocked 1", report)
        self.assertIn("[[Orphan]](16 観察 / 0 主題節, pending)", report)
        self.assertIn("Blocked(人間の reopen 待ち)", report)
        self.assertIn("[[Mid]] — 却下 3 回: 理由 1 / 理由 2 / 理由 3", report)

    def test_missing_page_is_flagged_not_dropped(self):
        self.run_ok("refresh")
        (self.vault / "wiki/concepts/Mid.md").unlink()
        self.run_ok("refresh")
        mid = self.queue()["entries"]["wiki/concepts/Mid.md"]
        self.assertTrue(mid.get("missing"))
        self.assertEqual(mid["state"], "pending")
        nxt = json.loads(self.run_ok("next", "--limit", "10").stdout)
        self.assertNotIn("wiki/concepts/Mid.md", [n["path"] for n in nxt])
        self.assertIn("Missing pages still in queue: 1", self.run_ok("report").stdout)


if __name__ == "__main__":
    unittest.main()
