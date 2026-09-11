#!/usr/bin/env python3
"""contradiction-index.py の試験。

WIKI_VAULT_ROOT で一時 vault を指し、実 wiki には触れない。
"""

import json
import os
import re
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
        [sys.executable, str(SCRIPTS / "contradiction-index.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault),
        check=False,
    )


CONCEPT_A = """---
type: concept
title: "TTXメトリクス"
---

# TTXメトリクス

## 定義

定義。

## 数値の条件

> [!contradiction] 49%・50%・64% という数値の条件が食い違う
> 発表は「MTTR が 10% 以上改善」と紹介するが、一次ソース [[@2021__OReilly__Incident Metrics in SRE]] では「15 分以上の改善」である。
> 引用時は条件を明示する必要がある。要追加検証。

## 未編纂の観察

- 観察。
"""

CONCEPT_B = """---
type: concept
title: "ストラグラー"
---

# ストラグラー

## 主因

> [!contradiction]
> OSDI 論文はハードウェアではなく計算オペレーションが主因だとする([[@2024__OSDI__Straggler A]])。
> TPDS 論文はハードウェア個体差だけでも減速するとする([[@2025__TPDS__Straggler B]])。
> 問いの立て方が異なるため矛盾しない。
"""

SOURCE_OREILLY = """---
type: source
title: "Incident Metrics in SRE"
---

# Incident Metrics in SRE

> [!contradiction] 二次発表との数値条件の違い
> [[TTXメトリクス]] が引く SRE Kaigi 発表は閾値を相対 10% とするが、本書は絶対 15 分である。

## 主要主張

- 主張。
"""

SOURCE_OSDI = """---
type: source
title: "Straggler A"
---

# Straggler A

本文。callout は持たない。
"""

SURVEY = """---
type: survey
title: "サーベイ"
---

# サーベイ

> [!contradiction] サーベイ側の転記
> [[ストラグラー]] の整理を転記する。(status: explained)
"""


class ContradictionIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel in ("wiki/concepts", "wiki/sources", "wiki/entities", "wiki/surveys", "wiki/meta", ".vault-meta"):
            (self.vault / rel).mkdir(parents=True)
        self.write("wiki/concepts/TTXメトリクス.md", CONCEPT_A)
        self.write("wiki/concepts/ストラグラー.md", CONCEPT_B)
        self.write("wiki/sources/@2021__OReilly__Incident Metrics in SRE.md", SOURCE_OREILLY)
        self.write("wiki/sources/@2024__OSDI__Straggler A.md", SOURCE_OSDI)
        self.write("wiki/surveys/サーベイ.md", SURVEY)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def records(self):
        res = run_script([], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_collects_every_callout_with_section_title_links_and_status(self):
        recs = self.records()
        self.assertEqual(len(recs), 4)
        by_host = {r["host"]: r for r in recs}

        a = by_host["wiki/concepts/TTXメトリクス.md"]
        self.assertEqual(a["host_type"], "concept")
        self.assertEqual(a["section"], "数値の条件")
        self.assertEqual(a["title"], "49%・50%・64% という数値の条件が食い違う")
        self.assertEqual(a["sources"], ["@2021__OReilly__Incident Metrics in SRE"])
        self.assertEqual(a["pages"], [])
        self.assertEqual(a["status"], "open")
        self.assertEqual(a["status_hint"], "要追加検証")
        self.assertIn("引用時は条件を明示する", a["body"])

        b = by_host["wiki/concepts/ストラグラー.md"]
        self.assertEqual(b["title"], "")
        self.assertEqual(b["status"], "explained")
        self.assertEqual(sorted(b["sources"]), ["@2024__OSDI__Straggler A", "@2025__TPDS__Straggler B"])

        s = by_host["wiki/sources/@2021__OReilly__Incident Metrics in SRE.md"]
        self.assertEqual(s["host_type"], "source")
        self.assertEqual(s["pages"], ["TTXメトリクス"])
        self.assertEqual(s["status"], "open")

        v = by_host["wiki/surveys/サーベイ.md"]
        self.assertEqual(v["status"], "explained")
        self.assertEqual(v["status_hint"], "(status: explained)")

    def test_ids_are_stable_and_sorted_by_host_then_line(self):
        recs = self.records()
        self.assertEqual([r["id"] for r in recs], ["x-0001", "x-0002", "x-0003", "x-0004"])
        hosts = [r["host"] for r in recs]
        self.assertEqual(hosts, sorted(hosts))

    def test_one_sided_reports_missing_counterpart_but_not_reciprocal_pairs(self):
        res = run_script(["--one-sided"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        sided = json.loads(res.stdout)
        pairs = {(s["host"], s["party"]) for s in sided}
        # TTX ⇄ OReilly は双方向(source 側が TTX へ戻るリンクを持つ)なので候補にならない
        self.assertNotIn(("wiki/concepts/TTXメトリクス.md", "wiki/sources/@2021__OReilly__Incident Metrics in SRE.md"), pairs)
        self.assertNotIn(("wiki/sources/@2021__OReilly__Incident Metrics in SRE.md", "wiki/concepts/TTXメトリクス.md"), pairs)
        # ストラグラー → OSDI source は片側のみ
        self.assertIn(("wiki/concepts/ストラグラー.md", "wiki/sources/@2024__OSDI__Straggler A.md"), pairs)
        # 存在しない TPDS source は相手頁として数えない
        self.assertFalse(any("TPDS" in s["party"] for s in sided))
        # survey は保持頁として数えない
        self.assertFalse(any(s["host"].startswith("wiki/surveys/") for s in sided))
        self.assertEqual(sided[0]["party_type"], "source")

    def test_query_is_and_match_over_title_body_and_links(self):
        res = run_script(["--query", "MTTR", "15"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        hits = json.loads(res.stdout)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["host"], "wiki/concepts/TTXメトリクス.md")
        self.assertIn("excerpt", hits[0])
        self.assertNotIn("body", hits[0])

        res = run_script(["--query", "ストラグラー"], self.vault)
        hits = json.loads(res.stdout)
        # 保持頁名(concept)とサーベイ本文の wikilink の両方に一致する
        self.assertEqual({h["host"] for h in hits}, {"wiki/concepts/ストラグラー.md", "wiki/surveys/サーベイ.md"})

        res = run_script(["--query", "存在しない語"], self.vault)
        self.assertEqual(json.loads(res.stdout), [])

    def test_types_filter_restricts_scan(self):
        res = run_script(["--types", "sources"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        recs = json.loads(res.stdout)
        self.assertEqual({r["host_type"] for r in recs}, {"source"})

        res = run_script(["--types", "bogus"], self.vault)
        self.assertEqual(res.returncode, 2)

    def test_write_generates_index_and_sidecar(self):
        res = run_script(["--write"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["counts"]["callouts"], 4)

        index = (self.vault / "wiki/meta/contradictions.md").read_text(encoding="utf-8")
        self.assertTrue(index.startswith("---\ntype: meta\n"))
        self.assertIn("# 矛盾索引", index)
        self.assertIn("## 未決着(推定)", index)
        self.assertIn("## 説明済み(推定)", index)
        self.assertIn("## 片側のみの候補", index)
        self.assertIn("[[TTXメトリクス]]", index)
        self.assertIn("[[@2021__OReilly__Incident Metrics in SRE]]", index)
        self.assertIn("callout 4 件 / 保持頁 4 頁", index)

        sidecar = json.loads((self.vault / ".vault-meta/contradictions.json").read_text(encoding="utf-8"))
        self.assertEqual(len(sidecar["records"]), 4)
        self.assertEqual(sidecar["counts"]["one_sided"], len(sidecar["one_sided"]))
        self.assertIn("body", sidecar["records"][0])

    def test_write_preserves_created_date_on_regeneration(self):
        res = run_script(["--write"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        path = self.vault / "wiki/meta/contradictions.md"
        # created を過去日に差し替えて再生成し、created が保持されることを確認する
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^created: .*$", "created: 2020-01-01", text, count=1, flags=re.M)
        path.write_text(text, encoding="utf-8")
        res = run_script(["--write"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("created: 2020-01-01", path.read_text(encoding="utf-8"))

    def test_summary_and_mutually_exclusive_flags(self):
        res = run_script(["--summary"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        counts = json.loads(res.stdout)
        self.assertEqual(counts["callouts"], 4)
        self.assertEqual(counts["open"], 2)
        self.assertEqual(counts["explained"], 2)
        self.assertEqual(counts["by_type"]["concept"], 2)

        res = run_script(["--summary", "--write"], self.vault)
        self.assertEqual(res.returncode, 2)

    def test_nested_callout_inside_parked_block_is_indexed(self):
        self.write("wiki/concepts/インシデント管理.md", """---
type: concept
title: "インシデント管理"
---

# インシデント管理

## 未編纂の観察

> [!note]- 編纂前の観察 2026-09
> - 観察 1。
>
> > [!contradiction] ICS 由来モデルの妥当性
> > 一方は有効とし([[@2016__OReilly__SRE Book - Chapter 14 Managing Incidents]])、他方は単純化しすぎだと批判する。
> > 一次研究は未取り込みであり、検証できていない。
>
> - 観察 2。
""")
        recs = [r for r in self.records() if r["host"] == "wiki/concepts/インシデント管理.md"]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertTrue(r["nested"])
        self.assertEqual(r["title"], "ICS 由来モデルの妥当性")
        self.assertEqual(r["sources"], ["@2016__OReilly__SRE Book - Chapter 14 Managing Incidents"])
        # 深さ 1 に戻った「観察 2」は本文に含まれない
        self.assertNotIn("観察 2", r["body"])
        self.assertIn("単純化しすぎ", r["body"])

    def test_path_qualified_and_primary_layer_links(self):
        self.write("wiki/entities/Book.md", """---
type: entity
title: "Book"
---

# Book

> [!contradiction] 章間の相違
> [[wiki/entities/Book|Book]] の 7 章と [[papers/2020__X__Y|一次ノート]] は食い違う。[[_attachments/x/fig.png]] も参照。[[@2020__OReilly__Book - Chapter 7]]。
""")
        recs = self.records()
        e = [r for r in recs if r["host"] == "wiki/entities/Book.md"][0]
        self.assertEqual(e["pages"], ["Book"])
        self.assertEqual(e["sources"], ["@2020__OReilly__Book - Chapter 7"])


if __name__ == "__main__":
    unittest.main()
