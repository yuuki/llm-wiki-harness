#!/usr/bin/env python3
"""claim-audit.py の試験。

WIKI_VAULT_ROOT で一時 vault を指し、実 wiki には触れない。
"""

import importlib.util
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

    def test_path_form_source_links_count_as_sources(self):
        # `[[wiki/sources/@X|表示]]` はファイル名だけの `[[@X]]` と同じ出典として扱う
        concept = CONCEPT.replace("[[@2024__SIGCOMM__NetEdit]] — 百万台",
                                  "[[wiki/sources/@2024__SIGCOMM__NetEdit|NetEdit]] — 百万台")
        self.write("wiki/concepts/eBPF.md", concept)
        out = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        first = out["claims"][0]
        self.assertEqual(first["sources"], ["@2024__SIGCOMM__NetEdit"])
        self.assertFalse(first.get("unverifiable"))
        self.assertEqual(first["evidence"][0]["status"], "ok")
        self.assertEqual(first["evidence"][0]["page"], "wiki/sources/@2024__SIGCOMM__NetEdit.md")

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

    def test_tab_indented_evidence_lines_are_counted(self):
        self.write("wiki/concepts/Tab.md", "---\ntitle: Tab\n---\n\n# Tab\n\n## 主題\n\n"
                   "- **タブ字下げの命題である。**\n\t- 根拠: [[@2024__SIGCOMM__NetEdit]] — 共存配備\n"
                   "\t- 反証: [[@2024__SIGCOMM__NetEdit]] — 段階的ロールアウト\n\t- 留保: 条件付き\n\n## 出典\n")
        out = json.loads(self.run_ok("sample", "--pages", "Tab", "--per-page", "10").stdout)
        (c,) = out["claims"]
        self.assertEqual(len(c["evidence_lines"]), 3)
        self.assertEqual(c["sources"], ["@2024__SIGCOMM__NetEdit"])

    def test_formatting_only_rewording_keeps_the_verdict(self):
        out = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        c0 = out["claims"][0]
        vpath = self.vault / "verdicts.json"
        vpath.write_text(json.dumps([{"id": c0["id"], "page": c0["page"], "line": c0["line"], "claim": c0["claim"],
                                      "sources": c0["sources"], "verdict": "supported"}], ensure_ascii=False),
                         encoding="utf-8")
        self.run_ok("record", "--verdicts", str(vpath))
        page = self.vault / "wiki/concepts/eBPF.md"
        # 句読点と空白だけを変える → 別 id だが判定済みとみなして出さない
        page.write_text(CONCEPT.replace("単体安全性であり、共存配備は保証範囲外である。",
                                        "単体安全性であり 共存配備は保証範囲外である"), encoding="utf-8")
        again = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        self.assertNotIn("verifier が保証する", " ".join(c["claim"] for c in again["claims"]))
        self.assertNotEqual(c0["id"], json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10",
                                                             "--reaudit").stdout)["claims"][0]["id"])
        # 語を変えた命題は前の判定が保証しないので再び出す
        page.write_text(CONCEPT.replace("単体安全性であり", "単体の型安全性であり"), encoding="utf-8")
        again = json.loads(self.run_ok("sample", "--pages", "eBPF", "--per-page", "10").stdout)
        self.assertIn("単体の型安全性", " ".join(c["claim"] for c in again["claims"]))

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


def load_module():
    spec = importlib.util.spec_from_file_location("claim_audit", SCRIPTS / "claim-audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NUM_CONCEPT = """---
type: concept
title: "集合通信"
---

# 集合通信

## 異種クラスタでの集合通信

- **異種 GPU クラスタでは帯域の違いを織り込んだスケジュール合成が要る。**
  - 根拠: [[@2026__NSDI__HeteroX]] — 32 GPU の異種クラスタで NCCL 比 2.1 倍
  - 根拠: [[@2026__NSDI__HeteroX]] — 起動遅延は約 200µs に収まる(§3.2)
  - 留保: 再検証 2026-09-25 — 99 は照合しない
- **Lumina の遅延は 150µs である。**
  - 根拠: [[@2026__NSDI__HeteroX]] — 本文

## 未解決の問い

## 未編纂の観察

## 関連

## 出典
"""

NUM_SOURCE = """---
type: source
title: "HeteroX"
sources:
  - "[[.raw/papers/hx.pdf]]"
key_claims:
  - "64 GPU の異種クラスタで NCCL 比 2.1 倍"
---

# HeteroX

## 概要

64 GPU の異種クラスタで NCCL 比 2.1 倍速い。

(Figure 3. 帯域は 17.5 GB/s で頭打ち。)

## 論文情報

- 2031 年刊

## 出典

- Hei et al., Version 9.9 (2026)
"""

NUM_RAW = """HeteroX

We evaluate on a heterogeneous cluster of 64 GPUs and achieve a 2.13x speedup over NCCL.

Section 3.2 shows the launch latency stays within about 200 us for two tiers.
"""


class NumberCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel in ("wiki/concepts", "wiki/sources", "wiki/meta", ".vault-meta", ".raw/papers"):
            (self.vault / rel).mkdir(parents=True)
        (self.vault / "wiki/concepts/集合通信.md").write_text(NUM_CONCEPT, encoding="utf-8")
        (self.vault / "wiki/sources/@2026__NSDI__HeteroX.md").write_text(NUM_SOURCE, encoding="utf-8")
        (self.vault / ".raw/papers/hx.txt").write_text(NUM_RAW, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_number_mentions_normalises_and_skips_identifiers(self):
        mod = load_module()
        got = dict(mod.number_mentions(
            "約200µs、F1 0.48、76%、10K 件、1,000 万台、§3.3.2、H100 を 64 GPU、"
            "[[@2024__X]] 2026-09-25 p.4-5 doi 10.1145/3600006 arXiv 2605.04333"))
        # §3.3.2 のような節番号は照合する側では数値として読まない
        self.assertEqual(set(got), {"200", "0.48", "76%", "10K", "1,000 万", "64"})
        self.assertIn("0.76", got["76%"])
        self.assertIn("10000", got["10K"])
        self.assertIn("10000000", got["1,000 万"])
        self.assertEqual(dict(mod.number_mentions("3.3.2 版", source_side=True))["3.3.2"], {"s:3.3.2"})
        # 出典側は丸めと英語の数詞・桁語を許す
        keys = mod.number_keys("F1 of 0.4837, two tiers, 10 million logs")
        self.assertTrue({"0.48", "0.5", "2", "10000000"} <= keys)

    def test_leading_zero_less_decimals_match_their_zero_form(self):
        mod = load_module()
        # 古い論文の抽出テキストは先頭の 0 を省く(.5、.98977)。ページ側の 0.5 と同じ値として照らす
        keys = mod.number_keys("p = .5 and q = .26, reliability .98977")
        self.assertTrue({"0.5", "0.26", "0.98977"} <= keys)
        got = dict(mod.number_mentions("確率 0.5 と .26"))
        self.assertEqual(got[".26"], {"0.26"})
        self.assertEqual(got["0.5"], {"0.5"})
        # 節番号・略記・省略記号の後の数字は小数として読まない
        lits = [lit for lit, _ in mod.number_mentions("Fig.5、§4.5、v1.2 と ...3 件、4.5 倍")]
        self.assertNotIn(".5", lits)
        self.assertNotIn(".2", lits)
        self.assertNotIn(".3", lits)
        self.assertEqual(lits.count("4.5"), 1, "§4.5 は節番号として読まず、4.5 倍だけを読む")

    def test_sample_packet_reports_missing_and_raw_only_numbers(self):
        out = json.loads(run_script(["sample", "--pages", "集合通信", "--per-page", "10"], self.vault).stdout)
        first, second = out["claims"]
        nums = first["numbers"]
        missing = {(m["where"], m["number"]) for m in nums["missing"]}
        # 32 GPU はページにも raw にも無い(正しくは 64)。留保行の数値は照合しない。
        self.assertEqual(missing, {("根拠[0]", "32")})
        self.assertTrue(nums["missing"][0]["raw_available"])
        # 200µs は source ページに無く raw にだけある。出典に無いと誤判定しないための印。
        # §3.2 は節番号なので数値として照合しない
        raw_only = {m["number"] for m in nums["raw_only"]}
        self.assertEqual(raw_only, {"200"})
        self.assertEqual({m["number"] for m in second["numbers"]["missing"]}, {"150"})

    def test_year_months_section_and_figure_numbers_are_not_numbers(self):
        mod = load_module()
        got = [lit for lit, _ in mod.number_mentions(
            "2025-11 に公開、2025/11 版、§3.2.3、Section 7.4、Sec. 2.1、Fig. 5、Figure 12、Table 2、Eq. (3)、"
            "図 3、表 4-1、付録 B.2、第 3 章、3.2 節、§4.2–4.5 で 64 GPU、3 節構成の 12 項目、発表 2 件")]
        self.assertEqual(got, ["64", "3", "12", "2"])
        # 出典側は従来どおり読む(文献表の刊行年や章番号の相互参照に使う)
        self.assertIn("3.2.3", [lit for lit, _ in mod.number_mentions("§3.2.3", source_side=True)])
        res = run_script(["numbers", "--pages", "集合通信"], self.vault)
        self.assertNotIn('"3.2"', res.stdout)

    def test_abbreviated_year_ranges_are_expanded(self):
        mod = load_module()
        got = [lit for lit, _ in mod.number_mentions("1984〜86 年、2019-21 年度、2024-05 に、1999〜2003 年")]
        # 省略した後ろの年は展開して照らす。年月(2024-05)は展開すると前の年より前になるので展開せず、
        # 年月として数値に読まない
        self.assertEqual(got, ["1984", "1986", "2019", "2021", "1999", "2003"])
        spec = [{"page": "wiki/concepts/集合通信.md",
                 "observations": ["HeteroX は 2019〜21 年の運用を振り返る(Source: [[@2026__NSDI__HeteroX]])"]}]
        (self.vault / ".raw/papers/hx.txt").write_text(NUM_RAW + "\nDeployed from 2019 to 2021.\n",
                                                           encoding="utf-8")
        path = self.vault / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        res = run_script(["numbers", "--spec", str(path)], self.vault)
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_numbers_pages_exit_code(self):
        res = run_script(["numbers", "--pages", "集合通信"], self.vault)
        self.assertEqual(res.returncode, 1, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(len(out["claims"]), 2)
        fixed = NUM_CONCEPT.replace("32 GPU", "64 GPU").replace("150µs", "200µs")
        (self.vault / "wiki/concepts/集合通信.md").write_text(fixed, encoding="utf-8")
        res = run_script(["numbers", "--pages", "集合通信"], self.vault)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(json.loads(res.stdout)["claims"][0]["missing"], [])

    def test_numbers_spec_checks_observations_before_append(self):
        spec = [{"page": "wiki/concepts/集合通信.md",
                 "observations": ["[異種] HeteroX は 32 GPU で評価した(Source: [[@2026__NSDI__HeteroX]])",
                                  "[異種] HeteroX は 64 GPU で 2.1 倍(Source: [[@2026__NSDI__HeteroX]])",
                                  "出典の無い 7 件の観察"],
                 "questions_add": ["128 GPU でも成り立つか"]}]
        path = self.vault / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        res = run_script(["numbers", "--spec", str(path)], self.vault)
        self.assertEqual(res.returncode, 1)
        out = json.loads(res.stdout)
        self.assertEqual(out["unsourced_lines"], 2)
        self.assertEqual([m["number"] for r in out["lines"] for m in r["missing"]], ["32"])
        self.assertEqual(out["lines"][0]["missing"][0]["where"], "observations[0]")

    def test_numbers_source_page_skips_bibliography_and_separates_captions(self):
        res = run_script(["numbers", "--source-page", "wiki/sources/@2026__NSDI__HeteroX.md"], self.vault)
        # キャプションだけの食い違いは終了 0(画像から読んだ数値は抽出テキストで確かめられない)
        self.assertEqual(res.returncode, 0, res.stdout)
        out = json.loads(res.stdout)
        self.assertEqual(out["raw_text"], [".raw/papers/hx.txt"])
        # 2.1 は 2.13 の丸めとして通る。書誌節(2031、9.9)は照合しない。キャプションは別枠。
        self.assertEqual(out["missing"], [])
        self.assertEqual([m["number"] for m in out["caption_missing"]], ["17.5"])
        italic = NUM_SOURCE.replace("(Figure 3. 帯域は 17.5 GB/s で頭打ち。)", "*Figure 3: 帯域は 17.5 GB/s で頭打ち。*")
        (self.vault / "wiki/sources/@2026__NSDI__HeteroX.md").write_text(italic, encoding="utf-8")
        out = json.loads(run_script(["numbers", "--source-page", "wiki/sources/@2026__NSDI__HeteroX.md"], self.vault).stdout)
        self.assertEqual([m["number"] for m in out["caption_missing"]], ["17.5"])
        text = NUM_SOURCE.replace("64 GPU の異種クラスタで NCCL 比 2.1 倍速い。", "48 GPU で速い。")
        (self.vault / "wiki/sources/@2026__NSDI__HeteroX.md").write_text(text, encoding="utf-8")
        res = run_script(["numbers", "--source-page", "wiki/sources/@2026__NSDI__HeteroX.md"], self.vault)
        self.assertEqual(res.returncode, 1)
        self.assertEqual([m["number"] for m in json.loads(res.stdout)["missing"]], ["48"])

    def test_key_claims_marked_as_visually_read_go_to_caption_missing(self):
        text = NUM_SOURCE.replace('  - "64 GPU の異種クラスタで NCCL 比 2.1 倍"', '  - "64 GPU の異種クラスタで NCCL 比 2.1 倍、帯域 17.5 GB/s(図 3 を目視)"')
        (self.vault / "wiki/sources/@2026__NSDI__HeteroX.md").write_text(text, encoding="utf-8")
        out = json.loads(run_script(["numbers", "--source-page", "wiki/sources/@2026__NSDI__HeteroX.md"], self.vault).stdout)
        self.assertEqual([(m["line"], m["number"]) for m in out["caption_missing"] if m["line"] == "key_claims"], [("key_claims", "17.5")])
        self.assertEqual(out["missing"], [])

    def test_stray_backtick_in_raw_does_not_hide_following_lines(self):
        mod = load_module()
        raw = "the \u2113 glyph became ` here\n" + "filler\n" * 5 + "we trained on 11,940 hours\nand ` again"
        keys = {k for _, ks in mod.number_mentions(raw, source_side=True) for k in ks}
        self.assertIn("11940", keys)

    def test_visually_read_lines_cover_marked_lines_and_adjacent_tables(self):
        mod = load_module()
        lines = ["本文 0.5", "表 2 の画像を目視して読んだ値:", "| a | 1.98 |", "| b | 9.07 |", "", "注記の無い表:", "| c | 3.3 |"]
        self.assertEqual(mod.visually_read_lines(lines), {2, 3, 4})

    def test_frame_and_slide_captions_count_as_captions(self):
        mod = load_module()
        for line in ("frame-031(12:34): 評価ボードは 92% を示す", "スライド 7: 4.6 倍", "*Figure 3: 17.5 GB/s*"):
            self.assertTrue(mod.CAPTION_RE.match(line.strip()), line)

    def test_apostrophe_and_spaced_thousands_on_source_side(self):
        mod = load_module()
        keys = {k for _, ks in mod.number_mentions("rate 1’735’000 pps on 16, 384 devices, +1, 399%", source_side=True) for k in ks}
        self.assertIn("1735000", keys)
        self.assertIn("16384", keys)
        self.assertIn("384", keys)
        self.assertTrue({"1399%", "13.99"} & keys, keys)

    def test_space_separated_thousands_and_spaced_decimals_on_source_side(self):
        mod = load_module()
        keys = {k for _, ks in mod.number_mentions("190 202 spans; 1. 46GB/s and 7. 7%", source_side=True) for k in ks}
        self.assertIn("190202", keys)
        self.assertIn("1.46", keys)
        self.assertTrue({"7.7%", "0.077"} & keys, keys)

    def test_oku_man_compound_and_english_tens(self):
        mod = load_module()
        page = {k for _, ks in mod.number_mentions("約 3 億 7000 万通") for k in ks}
        self.assertIn("370000000", page)
        self.assertIn("111000000", {k for _, ks in mod.number_mentions("1 億 1,100 万件") for k in ks})
        raw = mod.number_keys("370 million emails; we sampled fifty experiments and twenty-five scripts for over two hundred students and more than two billion lines")
        self.assertTrue({"370000000", "50", "25", "200", "2000000000"} <= raw, raw)

    def test_line_references_are_not_numbers(self):
        mod = load_module()
        self.assertEqual([lit for lit, _ in mod.number_mentions("4 原則で構成される(l.544–577、ll. 12-14、ch-09.md:919-967)")], ["4"])
        self.assertEqual([lit for lit, _ in mod.number_mentions("GPT-4 は mid-2014 に")], ["2014"])
        self.assertEqual([lit for lit, _ in mod.number_mentions("共生(p.171-173, 180-184)。p.5, 3 倍")], ["3 倍"[:1]])
        self.assertIn("0.33", {k for _, ks in mod.number_mentions("順位相関 0・33-0・42", source_side=True) for k in ks})
        self.assertIn("0.632", {k for _, ks in mod.number_mentions("about 0:632 at 16:45", source_side=True) for k in ks})
        self.assertEqual([lit for lit, _ in mod.number_mentions("神話は p.5〜9、p.27〜p.33、スライド 12〜15 と slide 7 で 3 倍")], ["3"])

    def test_month_names_and_url_dates_on_source_side(self):
        mod = load_module()
        keys = mod.number_keys("Published November 9 (see Nov 2014) at https://example.com/2022/11/post.html; you may go")
        self.assertTrue({"11", "2022"} <= keys, keys)
        self.assertNotIn("5", keys)

    def test_url_then_fullwidth_paren_and_raw_code_spans(self):
        mod = load_module()
        keys = mod.number_keys("記事(https://example.com/a)で 5,000万 人。計算は `2.5ms * 4` で 963.53 tok/s")
        self.assertTrue({"50000000", "2.5", "963", "964"} <= keys, keys)
        self.assertEqual([lit for lit, _ in mod.number_mentions("2. 手順は `sleep 10` で 3 倍")], ["3 倍"[:1]])

    def test_kanji_positional_digits_on_source_side(self):
        mod = load_module()
        raw = "一八三〇年には国内総生産の八二%、年率〇・五三%で、一人当たり"
        keys = {k for _, ks in mod.number_mentions(raw, source_side=True) for k in ks}
        self.assertTrue({"1830", "82%", "0.53%"} <= keys or {"1830", "0.82", "0.0053"} <= keys, keys)
        self.assertNotIn("1", keys)

    def test_unicode_superscripts_do_not_merge_into_numbers(self):
        self.assertEqual(load_module().caret_superscripts("2\u2070\u301c2\u00b9\u00b2 KiB"), "2^0\u301c2^12 KiB")

    def test_layout_and_ocr_siblings_are_raw_text(self):
        (self.vault / ".raw/papers").mkdir(parents=True, exist_ok=True)
        (self.vault / ".raw/papers/hx.txt").write_text("64 GPU, 2.1x\n", encoding="utf-8")
        (self.vault / ".raw/papers/hx.layout.txt").write_text("table 17.5\n", encoding="utf-8")
        (self.vault / ".raw/papers/hx.ocr.txt").write_text("48 GPU\n", encoding="utf-8")
        page = "wiki/sources/@2026__NSDI__HeteroY.md"
        (self.vault / page).write_text(NUM_SOURCE.replace("[[.raw/papers/hx.pdf]]", "[[.raw/papers/hx.txt]]")
                                       .replace("64 GPU の異種クラスタで NCCL 比 2.1 倍速い。", "48 GPU で 17.5。"), encoding="utf-8")
        out = json.loads(run_script(["numbers", "--source-page", page], self.vault).stdout)
        self.assertEqual(len(out["raw_text"]), 3)
        self.assertEqual(out["missing"], [])

    def test_chapter_raw_includes_toc(self):
        for rel in (".raw/books/b/chapters",):
            (self.vault / rel).mkdir(parents=True)
        (self.vault / ".raw/books/b/chapters/ch-54.txt").write_text("Authentication is hard.\n", encoding="utf-8")
        (self.vault / ".raw/books/b/toc.txt").write_text("53 Security\n54 Authentication\n57 Distributed\n",
                                                         encoding="utf-8")
        (self.vault / "wiki/sources/@2023__Book__B - Chapter 54.md").write_text(
            "---\ntype: source\nsources:\n  - \"[[.raw/books/b/chapters/ch-54.txt]]\"\n---\n\n# B\n\n"
            "## 概要\n\nセキュリティ部(第 53〜57 章)に属する。\n", encoding="utf-8")
        res = run_script(["numbers", "--source-page", "wiki/sources/@2023__Book__B - Chapter 54.md"], self.vault)
        self.assertEqual(res.returncode, 0, res.stdout)


    def test_pdf_only_chapter_uses_toc_and_full_text(self):
        # 章の原本が PDF だけでも、目次と全文テキスト(章の外の文献表を含む)に照らす
        (self.vault / ".raw/theses/s/chapters").mkdir(parents=True)
        (self.vault / ".raw/theses/s/chapters/ch-17.pdf").write_bytes(b"%PDF")
        (self.vault / ".raw/theses/s/toc.txt").write_text("17.5.1 Fault-prone modules\n", encoding="utf-8")
        (self.vault / ".raw/theses/s/s.txt").write_text("[119] Pitakrat et al. 2018. Hora.\n", encoding="utf-8")
        (self.vault / "wiki/sources/@2021__TIST__S - Chapter 17.md").write_text(
            "---\ntype: source\nsources:\n  - \"[[.raw/theses/s/chapters/ch-17.pdf]]\"\n---\n\n# S\n\n"
            "## 概要\n\n§17.5.1 で HORA(2018)を比べる。HORA(2016)ではない 1999 年。\n", encoding="utf-8")
        res = run_script(["numbers", "--source-page", "wiki/sources/@2021__TIST__S - Chapter 17.md"], self.vault)
        out = json.loads(res.stdout)
        self.assertEqual(out["raw_text"], [".raw/theses/s/toc.txt", ".raw/theses/s/s.txt"])
        self.assertEqual([m["number"] for m in out["missing"]], ["2016", "1999"])


    def test_author_year_citations_and_cited_source_years_are_not_flagged(self):
        mod = load_module()
        got = [lit for lit, _ in mod.number_mentions("Salfner+ 2010、Zhang et al. 2024、Cook (1998) と 2016 年に 64 GPU")]
        self.assertEqual(got, ["2016", "64"])
        # 行が引く出典名の年(@2026__)は刊行年として通す。引かない年は missing に残る
        spec = [{"page": "wiki/concepts/集合通信.md",
                 "observations": ["2026 年の HeteroX は 64 GPU で評価した(Source: [[@2026__NSDI__HeteroX]])",
                                  "2016 年の HeteroX(Source: [[@2026__NSDI__HeteroX]])"]}]
        path = self.vault / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        out = json.loads(run_script(["numbers", "--spec", str(path)], self.vault).stdout)
        self.assertEqual([m["number"] for r in out["lines"] for m in r["missing"]], ["2016"])


if __name__ == "__main__":
    unittest.main()
