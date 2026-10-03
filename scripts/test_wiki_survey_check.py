#!/usr/bin/env python3
"""scripts/wiki-survey-check.py の単体試験。

一時ディレクトリに最小の vault を組み、`WIKI_VAULT_ROOT` で差し替えて走らせる。
実 wiki には触れない。

  python3 scripts/test_wiki_survey_check.py
"""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
CHECKER = SCRIPTS / "wiki-survey-check.py"


def _load():
    spec = importlib.util.spec_from_file_location("wiki_survey_check", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _load()

SOURCE_A = "@2026__arXiv__Alpha - A Study"
SOURCE_B = "@2025__OSDI__Beta - Another Study"
SOURCE_C = "@2024__NSDI__Gamma - Unused"

GOOD_PAGE = f"""---
address: c-000123
type: survey
title: "試験主題の教科書"
date: 2026-09-19 12:00
question: "試験主題の教科書を作成して"
answer_quality: solid
created: 2026-09-19
updated: 2026-09-19
tags:
  - 2026/09/19
  - test
status: developing
related:
  - "[[試験概念]]"
sources:
  - "[[{SOURCE_A}]]"
  - "[[{SOURCE_B}]]"
---

# 試験主題の教科書

導入である (Source: [[{SOURCE_A}]])。

## 第 1 章　座標系

軸を定義する。
二つ目の文である。
三つ目の文である (Source: [[{SOURCE_B}]])。

![[_attachments/alpha/fig01-arch.png]]

**図1-1**:構成図である。
本文の主張に対応する。
(Source: [[{SOURCE_A}]], 図 1)

| 文献 | 年 |
|---|---|
| [[{SOURCE_A}\\|Alpha]] | 2026 |

## 第 2 章　未解決の問い

- **残る空白。** 検証されていない (Source: [[{SOURCE_B}]])。

## 関連

- 概念: [[試験概念]]
- MOC: [[structures/000 Index.md]]
"""


class TempVault:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wiki" / "sources" / "_attachments" / "alpha").mkdir(parents=True)
        (self.root / "wiki" / "concepts").mkdir(parents=True)
        (self.root / "wiki" / "surveys").mkdir(parents=True)
        (self.root / "structures").mkdir(parents=True)
        for name in (SOURCE_A, SOURCE_B, SOURCE_C):
            (self.root / "wiki" / "sources" / f"{name}.md").write_text("# s\n", encoding="utf-8")
        (self.root / "wiki" / "concepts" / "試験概念.md").write_text("# c\n", encoding="utf-8")
        (self.root / "structures" / "000 Index.md").write_text("# i\n", encoding="utf-8")
        (self.root / "wiki" / "sources" / "_attachments" / "alpha" / "fig01-arch.png").write_bytes(b"png")

    def write_survey(self, name, text):
        p = self.root / "wiki" / "surveys" / f"{name}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def run(self, *args):
        env = dict(os.environ, WIKI_VAULT_ROOT=str(self.root))
        return subprocess.run(
            ["python3", str(CHECKER), *args], capture_output=True, text=True, env=env, cwd=str(self.root)
        )

    def check_json(self, page, only=None):
        args = ["check", str(page), "--json"]
        if only:
            args += ["--only", only]
        proc = self.run(*args)
        data = json.loads(proc.stdout)
        return proc.returncode, {(f["check"], f["level"]): f for f in data[0]["findings"]}

    def close(self):
        self.tmp.cleanup()


class WikiSurveyCheckTest(unittest.TestCase):
    def setUp(self):
        self.vault = TempVault()

    def tearDown(self):
        self.vault.close()

    def test_good_page_has_no_fail(self):
        page = self.vault.write_survey("good", GOOD_PAGE)
        code, findings = self.vault.check_json(page)
        self.assertEqual(code, CHECK.EXIT_OK, findings)
        levels = {lvl for (_, lvl) in findings}
        self.assertNotIn("FAIL", levels)
        self.assertIn(("wikilinks", "OK"), findings)
        self.assertIn(("images", "OK"), findings)
        self.assertIn(("figures", "OK"), findings)
        self.assertIn(("sources_sync", "OK"), findings)

    def test_escaped_pipe_in_table_resolves(self):
        # `[[@...\|略称]]` の逃がしパイプを target に含めない
        self.assertEqual(CHECK.link_target(f"{SOURCE_A}\\|Alpha"), SOURCE_A)
        self.assertEqual(CHECK.link_target("X|alias#anchor"), "X")

    def test_missing_wikilink_and_image_fail(self):
        text = GOOD_PAGE.replace("[[試験概念]]", "[[存在しない概念]]").replace(
            "fig01-arch.png", "fig99-missing.png"
        )
        page = self.vault.write_survey("bad-links", text)
        code, findings = self.vault.check_json(page, "wikilinks,images")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertIn(("wikilinks", "FAIL"), findings)
        self.assertTrue(any("存在しない概念" in loc for loc in findings[("wikilinks", "FAIL")]["locations"]))
        self.assertIn(("images", "FAIL"), findings)

    def test_source_link_without_at_is_not_resolved_to_papers(self):
        # `@` を落とした source リンクは papers/ 側へ誤解決するので、wiki 内では欠損として報告する
        (self.vault.root / "papers").mkdir()
        (self.vault.root / "papers" / "2026__arXiv__Alpha - A Study.md").write_text("# p\n", encoding="utf-8")
        text = GOOD_PAGE.replace("導入である (Source: [[", "導入である (Source: [[2026__arXiv__Alpha - A Study]]) [[")
        page = self.vault.write_survey("no-at", text)
        _, findings = self.vault.check_json(page, "wikilinks")
        self.assertIn(("wikilinks", "FAIL"), findings)

    def test_dashes_and_nakaguro(self):
        text = GOOD_PAGE.replace("軸を定義する。", "軸を定義する — 層別・リンク別に。マット・マッシーが述べた。")
        page = self.vault.write_survey("style", text)
        code, findings = self.vault.check_json(page, "dashes,nakaguro")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertIn(("dashes", "FAIL"), findings)
        self.assertIn(("nakaguro", "FAIL"), findings)  # 層別・リンク別(漢字の並列)
        self.assertIn(("nakaguro", "WARN"), findings)  # マット・マッシー(カタカナ人名)
        self.assertEqual(len(findings[("nakaguro", "FAIL")]["locations"]), 1)
        self.assertEqual(len(findings[("nakaguro", "WARN")]["locations"]), 1)

    def test_nakaguro_inside_wikilink_is_ignored(self):
        (self.vault.root / "wiki" / "concepts" / "Mellanox・NVIDIA.md").write_text("# c\n", encoding="utf-8")
        text = GOOD_PAGE.replace("軸を定義する。", "軸を定義する。[[Mellanox・NVIDIA]] を見よ。")
        page = self.vault.write_survey("link-nakaguro", text)
        _, findings = self.vault.check_json(page, "nakaguro,wikilinks")
        self.assertIn(("nakaguro", "OK"), findings)
        self.assertIn(("wikilinks", "OK"), findings)

    def test_llm_phrases(self):
        text = GOOD_PAGE.replace("軸を定義する。", "重要なのは軸である。極めて速い。")
        page = self.vault.write_survey("llm", text)
        code, findings = self.vault.check_json(page, "llm_phrases")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertIn(("llm_phrases", "FAIL"), findings)
        self.assertIn(("llm_phrases", "WARN"), findings)

    def test_single_source_runs_detected_as_warn(self):
        run = "\n\n".join(f"文 {i} である (Source: [[{SOURCE_A}]])。" for i in range(4))
        text = GOOD_PAGE.replace("軸を定義する。", run)
        page = self.vault.write_survey("runs", text)
        code, findings = self.vault.check_json(page, "single_source_runs")
        self.assertEqual(code, CHECK.EXIT_OK)  # WARN は終了コードを変えない
        self.assertIn(("single_source_runs", "WARN"), findings)

    def test_single_source_runs_ignores_mixed_sources(self):
        run = "\n\n".join(
            f"文 {i} である (Source: [[{SOURCE_A if i % 2 else SOURCE_B}]])。" for i in range(4)
        )
        text = GOOD_PAGE.replace("軸を定義する。", run)
        page = self.vault.write_survey("runs-mixed", text)
        _, findings = self.vault.check_json(page, "single_source_runs")
        self.assertIn(("single_source_runs", "OK"), findings)

    def test_figure_numbering(self):
        dup = GOOD_PAGE.replace("**図1-1**", "**図1-2**")  # 章内の最初の図が 2 番: 連番でない
        page = self.vault.write_survey("fig-gap", dup)
        code, findings = self.vault.check_json(page, "figures")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertIn(("figures", "FAIL"), findings)

    def test_figure_and_table_may_share_one_sequence(self):
        # 表3-1, 図3-2 のように出現順の 1 系列で採番するページを許す
        block = (
            "![[_attachments/alpha/fig01-arch.png]]\n\n**表1-1**:表である。\n(Source: [[%s]], 表 1)\n\n"
            "**図1-2**:図である。\n(Source: [[%s]], 図 2)\n"
        ) % (SOURCE_A, SOURCE_A)
        text = GOOD_PAGE.replace(
            "![[_attachments/alpha/fig01-arch.png]]\n\n**図1-1**:構成図である。\n本文の主張に対応する。\n(Source: [[%s]], 図 1)\n"
            % SOURCE_A,
            block,
        )
        page = self.vault.write_survey("fig-shared", text)
        _, findings = self.vault.check_json(page, "figures")
        self.assertNotIn(("figures", "FAIL"), findings)

    def test_duplicate_embed_fails_and_missing_locator_warns(self):
        text = GOOD_PAGE.replace(
            "(Source: [[%s]], 図 1)" % SOURCE_A,
            "(Source: [[%s]])\n\n![[_attachments/alpha/fig01-arch.png]]\n\n**図1-2**:二度目。\n(Source: [[%s]], 図 1)"
            % (SOURCE_A, SOURCE_A),
        )
        page = self.vault.write_survey("fig-dup", text)
        code, findings = self.vault.check_json(page, "figures")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertTrue(any("2 回貼られている" in loc for loc in findings[("figures", "FAIL")]["locations"]))
        self.assertTrue(any("原本の図表番号" in loc for loc in findings[("figures", "WARN")]["locations"]))

    def test_sources_sync(self):
        # 本文で C を引くが frontmatter に無い(FAIL)。frontmatter の B は本文で引いている。
        text = GOOD_PAGE.replace("導入である (Source: [[%s]])" % SOURCE_A, "導入である (Source: [[%s]])" % SOURCE_C)
        text = text.replace('  - "[[%s]]"\n' % SOURCE_A, '  - "[[wiki/sources/%s.md]]"\n' % SOURCE_A)  # パス形も同一視
        page = self.vault.write_survey("sync", text)
        code, findings = self.vault.check_json(page, "sources_sync")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertIn(("sources_sync", "FAIL"), findings)
        self.assertEqual(findings[("sources_sync", "FAIL")]["locations"], ["[[%s]]" % SOURCE_C])
        # A はパス形で列挙され、表のセルで引いているので「未引用」にならない
        self.assertNotIn(("sources_sync", "WARN"), findings)

    def test_frontmatter_rules(self):
        text = GOOD_PAGE.replace('title: "試験主題の教科書"', 'title: "試験主題の教科書 — 副題"').replace(
            'question: "試験主題の教科書を作成して"', 'question: ""'
        )
        page = self.vault.write_survey("fm", text)
        code, findings = self.vault.check_json(page, "frontmatter")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        msg = findings[("frontmatter", "FAIL")]["message"]
        self.assertIn("title", msg)
        self.assertIn("question", msg)

    def test_sections(self):
        text = GOOD_PAGE.replace(
            "## 第 2 章　未解決の問い",
            "## 第 1.5 章　空の節\n\n## 第 1.6 章　単一 NIC が全体を止める\n\n本文。\n\n### 発見 1　順序は原因を反映しない\n\n本文。\n\n## 第 2 章　未解決の問い",
        )
        page = self.vault.write_survey("sections", text)
        code, findings = self.vault.check_json(page, "sections")
        self.assertEqual(code, CHECK.EXIT_FAIL)
        self.assertTrue(any("空の節" in loc for loc in findings[("sections", "FAIL")]["locations"]))
        speech = findings[("sections", "WARN")]["locations"]
        self.assertTrue(any("止める" in loc for loc in speech))
        self.assertFalse(any("発見 1" in loc for loc in speech))  # 発見 N は設計上の文形見出し
        self.assertFalse(any("未解決の問い" in loc for loc in speech))  # 名詞「問い」

    def test_code_fence_is_excluded(self):
        text = GOOD_PAGE.replace("軸を定義する。", "軸を定義する。\n\n```bash\necho '—・重要なのは' [[fake]]\n```\n")
        page = self.vault.write_survey("fence", text)
        code, findings = self.vault.check_json(page, "dashes,nakaguro,llm_phrases,wikilinks")
        self.assertEqual(code, CHECK.EXIT_OK, findings)

    def test_reported_line_numbers_are_file_lines(self):
        text = GOOD_PAGE.replace("軸を定義する。", "軸を定義する — ここ。")
        page = self.vault.write_survey("lines", text)
        _, findings = self.vault.check_json(page, "dashes")
        loc = findings[("dashes", "FAIL")]["locations"][0]
        line_no = int(loc.split()[0][1:])
        file_lines = page.read_text(encoding="utf-8").split("\n")
        self.assertIn("—", file_lines[line_no - 1])

    def test_stats_lists_pages_and_min_sources(self):
        self.vault.write_survey("good", GOOD_PAGE)
        proc = self.vault.run("stats", "--json")
        self.assertEqual(proc.returncode, CHECK.EXIT_OK)
        rows = json.loads(proc.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sources"], 2)
        self.assertEqual(rows[0]["embeds"], 1)
        self.assertTrue(rows[0]["question"])
        text = self.vault.run("stats").stdout
        self.assertIn("ゲート下限", text)

    def test_unknown_check_name_is_usage_error(self):
        page = self.vault.write_survey("good", GOOD_PAGE)
        proc = self.vault.run("check", str(page), "--only", "nope")
        self.assertEqual(proc.returncode, CHECK.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
