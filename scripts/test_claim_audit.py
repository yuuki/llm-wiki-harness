#!/usr/bin/env python3
"""claim-audit.py の試験。

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
        [sys.executable, str(SCRIPTS / "claim-audit.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault),
        check=False,
    )


CONCEPT = """---
type: concept
title: "eBPF"
recompiled: 2026-09-02
---

# eBPF

## 定義

定義段落。

## verifier の保証範囲

- **verifier が保証するのは単体安全性であり、共存配備は保証範囲外である。**
  - 根拠: [[@2024__SIGCOMM__NetEdit]] — 百万台規模の共存にはライフサイクル管理層が別途要る
  - 留保: 条件付き
- **マップ値の競合は verifier の対象外であり、3 層の同期で埋める必要がある。**
  - 根拠: [[@2025__PhD__Telemetry - Chapter 3]] §3.3.2 — 3 層の同期
- **出典の無い命題は対象にならない。**

## 未解決の問い

- 問い。

## 未編纂の観察

- NetEdit は eBPF を百万台へ配る。(Source: [[@2024__SIGCOMM__NetEdit]])
- 出典無しの観察。

## 関連

## 出典
"""

SOURCE_NETEDIT = """---
type: source
title: "NetEdit"
sources:
  - "[[.raw/papers/netedit.pdf]]"
key_claims:
  - "eBPF プログラムの共存配備には verifier とは別にライフサイクル管理層が必要である"
---

# NetEdit

## 概要

NetEdit は Meta の百万台規模のサーバに eBPF ネットワーク機能を配るオーケストレーション基盤である。

## 主要主張

- verifier は単体プログラムの安全性しか見ないため、複数プログラムの共存はライフサイクル管理層が担う。
- 配備の段階的ロールアウトで障害を局所化する。
"""

RAW_TXT = """NetEdit: An Orchestration Platform

The BPF verifier checks a single program in isolation. Coexistence of many
programs on one host requires a separate lifecycle management layer.

Rollouts proceed in stages to contain failures.
"""


class ClaimAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel in ("wiki/concepts", "wiki/sources", "wiki/meta", ".vault-meta", ".raw/papers"):
            (self.vault / rel).mkdir(parents=True)
        self.write("wiki/concepts/eBPF.md", CONCEPT)
        self.write("wiki/sources/@2024__SIGCOMM__NetEdit.md", SOURCE_NETEDIT)
        self.write(".raw/papers/netedit.txt", RAW_TXT)

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

    def test_sample_extracts_propositions_with_sources_and_evidence(self):
        out = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        self.assertEqual(out["pages"], ["wiki/concepts/eBPF.md"])
        claims = out["claims"]
        # 出典の無い命題は対象外、受信箱は --include-inbox 無しでは対象外
        self.assertEqual(len(claims), 2)
        self.assertTrue(all(c["kind"] == "proposition" for c in claims))
        first = claims[0]
        self.assertEqual(first["section"], "verifier の保証範囲")
        self.assertEqual(first["sources"], ["@2024__SIGCOMM__NetEdit"])
        self.assertEqual(first["line"], 15)
        self.assertEqual(len(first["evidence_lines"]), 2)
        ev = first["evidence"][0]
        self.assertEqual(ev["status"], "ok")
        self.assertEqual(ev["page"], "wiki/sources/@2024__SIGCOMM__NetEdit.md")
        self.assertTrue(ev["page_excerpts"])
        self.assertIn("ライフサイクル管理層", ev["page_excerpts"][0]["text"])
        self.assertEqual(ev["raw_text"], ".raw/papers/netedit.txt")
        self.assertTrue(any("lifecycle" in x["text"] for x in ev["raw_excerpts"]))

        second = claims[1]
        self.assertEqual(second["evidence"][0]["status"], "source_missing")
        self.assertIsNone(second["evidence"][0]["page"])

    def test_lint_stub_source_is_flagged(self):
        self.write("wiki/sources/@2025__PhD__Telemetry - Chapter 3.md", """---
type: source
title: "Telemetry ch3"
---

# Telemetry ch3

このページは wiki-lint の自動修正で作成した source stub である。
""")
        out = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        second = out["claims"][1]
        self.assertEqual(second["evidence"][0]["status"], "stub")
        self.assertEqual(second["evidence"][0]["page"], "wiki/sources/@2025__PhD__Telemetry - Chapter 3.md")

    def test_include_inbox_adds_sourced_observations_only(self):
        out = json.loads(self.run_ok("sample", "--pages", "wiki/concepts/eBPF.md", "--per-page", "10", "--include-inbox").stdout)
        kinds = [c["kind"] for c in out["claims"]]
        self.assertEqual(kinds.count("observation"), 1)
        obs = [c for c in out["claims"] if c["kind"] == "observation"][0]
        self.assertEqual(obs["section"], "未編纂の観察")
        self.assertIn("百万台", obs["claim"])

    def test_record_writes_ledger_and_report_and_sample_skips_audited(self):
        out = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        c0, c1 = out["claims"]
        verdicts = [
            {"id": c0["id"], "page": c0["page"], "line": c0["line"], "claim": c0["claim"], "sources": c0["sources"],
             "verdict": "supported", "note": ""},
            {"id": c1["id"], "page": c1["page"], "line": c1["line"], "claim": c1["claim"], "sources": c1["sources"],
             "verdict": "source_missing", "note": "source ページ未作成"},
            {"id": "zzz", "verdict": "bogus"},
        ]
        vpath = self.vault / "verdicts.json"
        vpath.write_text(json.dumps(verdicts, ensure_ascii=False), encoding="utf-8")
        rec = json.loads(self.run_ok("record", "--verdicts", str(vpath), "--by", "human").stdout)
        self.assertEqual(rec["accepted"], 2)
        self.assertEqual(rec["rejected"], [{"id": "zzz", "verdict": "bogus"}])

        ledger = json.loads((self.vault / ".vault-meta/claim-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(set(ledger["verdicts"]), {c0["id"], c1["id"]})
        self.assertEqual(ledger["verdicts"][c1["id"]]["by"], "human")

        report = (self.vault / rec["report"]).read_text(encoding="utf-8")
        self.assertTrue(report.startswith("---\ntype: meta\n"))
        self.assertIn("# Claim Audit:", report)
        self.assertIn("supported 1", report)
        self.assertIn("source_missing 1", report)
        self.assertIn("`source_missing` — source ページ未作成", report)
        self.assertIn("[[eBPF]] L15", report)

        # 判定済みは次の sample から除かれる
        again = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        self.assertEqual(again["claims"], [])
        again = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10", "--reaudit").stdout)
        self.assertEqual(len(again["claims"]), 2)

        # 再判定は履歴を残す
        verdicts[0]["verdict"] = "unclear"
        vpath.write_text(json.dumps(verdicts[:1], ensure_ascii=False), encoding="utf-8")
        self.run_ok("record", "--verdicts", str(vpath))
        ledger = json.loads((self.vault / ".vault-meta/claim-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["verdicts"][c0["id"]]["verdict"], "unclear")
        self.assertEqual(ledger["verdicts"][c0["id"]]["history"][0]["verdict"], "supported")

    def test_report_summarises_ledger(self):
        self.assertIn("Claims audited (cumulative): 0", self.run_ok("report").stdout)
        out = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        c0 = out["claims"][0]
        vpath = self.vault / "v.json"
        vpath.write_text(json.dumps([{**{k: c0[k] for k in ("id", "page", "line", "claim", "sources")},
                                      "verdict": "not_in_source", "note": "数値が無い"}]), encoding="utf-8")
        self.run_ok("record", "--verdicts", str(vpath))
        rep = self.run_ok("report").stdout
        self.assertIn("not_in_source 1", rep)
        self.assertIn("Problem rate: 1/1", rep)
        self.assertIn("[[eBPF]] (1)", rep)
        self.assertIn("Unresolved findings: 1", rep)

        res = run_script(["resolve", "nope"], self.vault)
        self.assertEqual(res.returncode, 3)
        out = json.loads(self.run_ok("resolve", c0["id"], "--note", "留保を付けた").stdout)
        self.assertTrue(out["ok"])
        self.assertNotIn("Unresolved findings", self.run_ok("report").stdout)
        ledger = json.loads((self.vault / ".vault-meta/claim-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["verdicts"][c0["id"]]["resolved_note"], "留保を付けた")

    def test_recompiled_and_random_modes(self):
        self.write("wiki/concepts/Plain.md", "---\ntype: concept\ntitle: Plain\n---\n\n# Plain\n\n## 定義\n\n定義。\n")
        out = json.loads(self.run_ok("sample", "--recompiled", "--limit", "3").stdout)
        self.assertEqual(out["pages"], ["wiki/concepts/eBPF.md"])
        out = json.loads(self.run_ok("sample", "--random", "2", "--seed", "1").stdout)
        self.assertEqual(sorted(out["pages"]), ["wiki/concepts/Plain.md", "wiki/concepts/eBPF.md"])
        res = run_script(["sample"], self.vault)
        self.assertEqual(res.returncode, 2)
        res = run_script(["sample", "--pages", "Nope"], self.vault)
        self.assertEqual(res.returncode, 3)
        res = run_script(["record", "--verdicts", str(self.vault / "missing.json")], self.vault)
        self.assertEqual(res.returncode, 3)


if __name__ == "__main__":
    unittest.main()
