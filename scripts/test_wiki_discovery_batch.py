#!/usr/bin/env python3
"""wiki-resolve.py と wiki-excerpt.py の一括モードの回帰テスト。

単一クエリ・単一ページの出力は従来と同一であること(後方互換)と、複数クエリ・
複数ページを 1 回の呼び出しで処理できること、索引の構築が 1 回で済むことを固定する。
一時 vault を `WIKI_VAULT_ROOT` で指し、実 vault には一切触れない。

実行: python3 scripts/test_wiki_discovery_batch.py
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
RESOLVE = SCRIPTS / "wiki-resolve.py"
EXCERPT = SCRIPTS / "wiki-excerpt.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def concept_page(title, aliases=(), extra_sections=True):
    alias_block = ""
    if aliases:
        alias_block = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    body = f"""---
title: "{title}"
type: concept
address: c-000001
{alias_block}domain: network
sources:
  - "[[@2025__arXiv__Sample Paper]]"
related:
  - "[[RDMA]]"
updated: 2026-09-04
---

# {title}

## 定義

{title} の定義文。一文で述べる。
"""
    if not extra_sections:
        return body
    return body + """
## 子概念

- [[子概念A]]
- [[子概念B]]

## 未解決の問い

- 問い 1
- 問い 2

## 未編纂の観察

- **観察 1** — 本文 (Source: [[@2025__arXiv__Sample Paper]])
- **観察 2** — 本文 (Source: [[@2025__arXiv__Sample Paper]])
- **観察 3** — 本文 (Source: [[@2025__arXiv__Sample Paper]])

## 関連

- [[RDMA]]

## 出典

- [[@2025__arXiv__Sample Paper]]
"""


def entity_page(title, aliases=(), entity_type="person"):
    alias_block = ""
    if aliases:
        alias_block = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    return f"""---
title: "{title}"
type: entity
entity_type: {entity_type}
address: e-000001
{alias_block}role: "{title} の役割"
first_mentioned: "[[@2025__arXiv__Sample Paper]]"
updated: 2026-09-04
---

# {title}

## 概要

{title} の概要。

## 関連ソース

- [[@2025__arXiv__Sample Paper]]
"""


def make_vault(root):
    """テスト用の最小 vault を作る。"""
    write(root / "wiki" / "concepts" / "RDMA.md", concept_page("RDMA", ["Remote Direct Memory Access"]))
    write(root / "wiki" / "concepts" / "RDMA監視.md", concept_page("RDMA監視"))
    write(root / "wiki" / "concepts" / "RDMA輻輳制御.md", concept_page("RDMA輻輳制御"))
    write(root / "wiki" / "concepts" / "RDMAネットワーク.md", concept_page("RDMAネットワーク"))
    write(root / "wiki" / "concepts" / "RoCE.md", concept_page("RoCE", ["RDMA over Converged Ethernet"]))
    write(root / "wiki" / "concepts" / "異常検知.md", concept_page("異常検知"))
    write(root / "wiki" / "entities" / "Joseph L. Hellerstein.md",
          entity_page("Joseph L. Hellerstein", ["Hellerstein"]))
    write(root / "wiki" / "entities" / "Meta FAIR.md",
          entity_page("Meta FAIR", ["FAIR"], entity_type="organization"))
    # 別名が他クエリの部分文字列になる罠。"Hellerstein" で当ててはならない。
    write(root / "wiki" / "entities" / "Pinjia He.md", entity_page("Pinjia He", ["He"]))
    write(root / "wiki" / "sources" / "@2025__arXiv__Sample Paper.md", """---
title: "Sample Paper"
type: source
address: s-000001
updated: 2026-09-04
---

# @2025__arXiv__Sample Paper

## 概要

概要。
""")
    # カタログ類。resolve は候補に含めてはならない。
    write(root / "wiki" / "index.md", "# index\n")
    write(root / "wiki" / "concepts" / "_index.md", "# concepts index\n- [[RDMA]]\n")


class BatchTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wiki-batch-"))
        make_vault(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_script(self, script, *args):
        env = dict(os.environ)
        env["WIKI_VAULT_ROOT"] = str(self.tmp)
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), check=False,
        )
        return proc

    def resolve(self, *args):
        return self.run_script(RESOLVE, *args)

    def excerpt(self, *args):
        return self.run_script(EXCERPT, *args)


class ResolveBatchTest(BatchTestBase):
    def test_single_query_keeps_legacy_json_shape(self):
        proc = self.resolve("RDMA", "--type", "concept")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(set(payload.keys()), {"query", "type", "candidates"})
        self.assertEqual(payload["query"], "RDMA")
        self.assertEqual(payload["type"], "concept")
        first = payload["candidates"][0]
        self.assertEqual(set(first.keys()),
                         {"path", "title", "type", "match", "score", "aliases", "one_liner"})
        self.assertEqual(first["path"], "wiki/concepts/RDMA.md")
        self.assertNotIn("_index.md", proc.stdout)

    def test_single_query_stderr_is_legacy_line(self):
        proc = self.resolve("RDMA", "--type", "concept", "--top", "3")
        self.assertIn("wiki-resolve: 3 candidate(s) for 'RDMA'", proc.stderr)

    def test_multi_query_wraps_results(self):
        proc = self.resolve("RDMA", "異常検知", "--type", "concept")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(list(payload.keys()), ["results"])
        self.assertEqual([r["query"] for r in payload["results"]], ["RDMA", "異常検知"])
        for result in payload["results"]:
            self.assertEqual(set(result.keys()), {"query", "type", "candidates"})
            self.assertEqual(result["type"], "concept")

    def test_multi_query_stderr_is_one_summary_line(self):
        proc = self.resolve("entity:Hellerstein", "concept:RDMA", "concept:存在しない語", "--quiet")
        self.assertEqual(proc.stderr, "")
        proc = self.resolve("entity:Hellerstein", "concept:RDMA", "concept:存在しない語")
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        self.assertEqual(lines, ["wiki-resolve: 3 queries, 2 hit, 1 none"])

    def test_batch_answers_exactly_what_single_runs_answer(self):
        """一括の候補集合は 1 クエリずつ実行した結果と一致する(recall を広げない)。"""
        queries = [("RDMA", "concept"), ("Hellerstein", "entity"),
                   ("Meta FAIR", "entity"), ("異常検知", "concept")]
        batch = json.loads(self.resolve(*[f"{t}:{q}" for q, t in queries], "--quiet").stdout)
        for (name, type_key), result in zip(queries, batch["results"]):
            single = json.loads(self.resolve(name, "--type", type_key, "--quiet").stdout)
            self.assertEqual(result["candidates"], single["candidates"], name)

    def test_alias_shorter_than_query_needs_the_query_in_the_page(self):
        proc = self.resolve("entity:Hellerstein", "entity:Pinjia He", "--compact", "--quiet")
        first = proc.stdout.splitlines()[0]
        self.assertIn("Joseph L. Hellerstein.md", first)
        self.assertNotIn("Pinjia He.md", first)

    def test_query_prefix_overrides_type(self):
        proc = self.resolve("entity:Hellerstein", "concept:RDMA", "--type", "source")
        payload = json.loads(proc.stdout)
        first, second = payload["results"]
        self.assertEqual((first["query"], first["type"]), ("Hellerstein", "entity"))
        self.assertEqual(first["candidates"][0]["path"], "wiki/entities/Joseph L. Hellerstein.md")
        self.assertEqual((second["query"], second["type"]), ("RDMA", "concept"))
        for cand in second["candidates"]:
            self.assertTrue(cand["path"].startswith("wiki/concepts/"), cand["path"])

    def test_unknown_prefix_stays_part_of_query(self):
        proc = self.resolve("Re: Zero", "--type", "concept")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["query"], "Re: Zero")
        self.assertEqual(payload["type"], "concept")
        self.assertEqual(payload["candidates"], [])

    def test_any_prefix_widens_one_query(self):
        proc = self.resolve("any:RDMA", "--type", "entity")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["type"], "any")
        self.assertIn("wiki/concepts/RDMA.md", [c["path"] for c in payload["candidates"]])

    def test_names_file_skips_blanks_and_comments(self):
        names = self.tmp / "names.txt"
        write(names, "# コメント行\n\nconcept:RDMA\n\n  # 字下げコメント\nentity:Hellerstein\n")
        proc = self.resolve("--names-file", str(names))
        payload = json.loads(proc.stdout)
        self.assertEqual([r["query"] for r in payload["results"]], ["RDMA", "Hellerstein"])
        self.assertEqual([r["type"] for r in payload["results"]], ["concept", "entity"])

    def test_names_file_appends_to_positional(self):
        names = self.tmp / "names2.txt"
        write(names, "異常検知\n")
        proc = self.resolve("RDMA", "--names-file", str(names), "--type", "concept")
        payload = json.loads(proc.stdout)
        self.assertEqual([r["query"] for r in payload["results"]], ["RDMA", "異常検知"])

    def test_compact_line_shape_and_default_top(self):
        proc = self.resolve("concept:RDMA", "concept:存在しない語", "--compact")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        fields = lines[0].split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[0], "RDMA")
        self.assertEqual(fields[1], "concept")
        self.assertEqual(fields[2], "HIT")
        cands = fields[3].split("; ")
        self.assertEqual(len(cands), 3, "--compact の既定 top は 3")
        self.assertEqual(cands[0], "concepts/RDMA.md(filename 1.00)")
        self.assertNotIn("one_liner", proc.stdout)
        self.assertNotIn("aliases", proc.stdout)
        miss = lines[1].split("\t")
        self.assertEqual(miss[:3], ["存在しない語", "concept", "NONE"])
        self.assertEqual(miss[3], "")

    def test_compact_honours_explicit_top(self):
        proc = self.resolve("concept:RDMA", "--compact", "--top", "5")
        cands = proc.stdout.splitlines()[0].split("\t")[3].split("; ")
        self.assertEqual(len(cands), 5)

    def test_default_top_is_still_eight(self):
        proc = self.resolve("RDMA", "--type", "concept")
        payload = json.loads(proc.stdout)
        self.assertEqual(len(payload["candidates"]), 5, "RDMA に当たるページは 5 枚")

    def test_paths_only(self):
        proc = self.resolve("concept:RDMA", "concept:存在しない語", "--paths-only", "--top", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), [
            "# RDMA",
            "wiki/concepts/RDMA.md",
            "wiki/concepts/RoCE.md",
            "# 存在しない語",
        ])

    def test_min_score_drops_weak_candidates(self):
        loose = json.loads(self.resolve("RDMA", "--type", "concept").stdout)
        self.assertIn("substring", [c["match"] for c in loose["candidates"]])
        strict = json.loads(self.resolve("RDMA", "--type", "concept", "--min-score", "0.9").stdout)
        self.assertNotIn("substring", [c["match"] for c in strict["candidates"]])
        self.assertTrue(strict["candidates"])

    def test_compact_and_paths_only_are_exclusive(self):
        proc = self.resolve("RDMA", "--compact", "--paths-only")
        self.assertEqual(proc.returncode, 2)

    def test_names_file_utf8_bom_and_yaml_quotes(self):
        names = self.tmp / "names-bom.txt"
        body = (
            "# BOM 付きコメント\n"
            'entity:"Hellerstein"\n'
            "concept:'RDMA'\n"
            '"entity:Meta FAIR"\n'
        )
        names.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        proc = self.resolve("--names-file", str(names), "--compact")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = [ln.split("\t") for ln in proc.stdout.splitlines()]
        self.assertEqual([r[:3] for r in rows], [
            ["Hellerstein", "entity", "HIT"],
            ["RDMA", "concept", "HIT"],
            ["Meta FAIR", "entity", "HIT"],
        ])
        self.assertIn("Joseph L. Hellerstein.md", rows[0][3])
        self.assertIn("RDMA.md", rows[1][3])
        self.assertIn("Meta FAIR.md", rows[2][3])

    def test_positional_leading_whitespace_prefix(self):
        proc = self.resolve("  entity:Hellerstein", "--compact", "--quiet")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = proc.stdout.splitlines()[0].split("\t")
        self.assertEqual(fields[:3], ["Hellerstein", "entity", "HIT"])

    def test_bm25_skip_logged_once_for_batch(self):
        """索引なし vault では skip をバッチ先頭で 1 行だけ出す(クエリごとではない)。"""
        proc = self.resolve("RDMA", "異常検知", "--bm25")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        skips = [ln for ln in proc.stderr.splitlines()
                 if "wiki-resolve: --bm25 skipped" in ln]
        self.assertEqual(skips, ["wiki-resolve: --bm25 skipped (retrieve.py or index missing)"])

    def test_usage_errors(self):
        self.assertEqual(self.resolve().returncode, 2)
        self.assertEqual(self.resolve("RDMA", "--top", "0").returncode, 2)
        empty = self.resolve("entity:")
        self.assertEqual(empty.returncode, 2)
        self.assertIn("NAME is empty", empty.stderr)


class ResolveIndexReuseTest(unittest.TestCase):
    """索引(ディレクトリ走査と alias 走査)がクエリ数に関係なく 1 回だけであること。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wiki-batch-idx-"))
        make_vault(cls.tmp)
        spec = importlib.util.spec_from_file_location("wiki_resolve_mod", RESOLVE)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.mod.VAULT_ROOT = cls.tmp.resolve()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_main(self, argv):
        mod = self.mod
        scans = []
        alias_scans = []
        real_scan = mod.scan_dir_md
        real_rg = mod.rg_alias_files

        def counting_scan(rel_dir):
            scans.append(rel_dir)
            return real_scan(rel_dir)

        def counting_rg(queries, rel_dirs):
            alias_scans.append(tuple(queries))
            return real_rg(queries, rel_dirs)

        mod.scan_dir_md = counting_scan
        mod.rg_alias_files = counting_rg
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = mod.main(argv + ["--quiet"])
        finally:
            mod.scan_dir_md = real_scan
            mod.rg_alias_files = real_rg
        self.assertEqual(code, 0)
        return scans, alias_scans

    def test_one_query_scans_each_dir_once(self):
        scans, alias_scans = self.run_main(["RDMA", "--type", "concept", "--compact"])
        self.assertEqual(scans, ["wiki/concepts"])
        self.assertEqual(len(alias_scans), 1)

    def test_fifty_queries_reuse_the_same_index(self):
        argv = [f"concept:q{i}" for i in range(48)] + ["concept:RDMA", "entity:Hellerstein"]
        scans, alias_scans = self.run_main(argv + ["--compact"])
        self.assertEqual(sorted(scans), ["wiki/concepts", "wiki/entities"],
                         "走査はディレクトリごとに 1 回だけ")
        self.assertEqual(len(alias_scans), 1, "alias 走査はバッチ全体で 1 回")
        self.assertEqual(len(alias_scans[0]), 50, "1 回の走査に全クエリを渡す")


class ExcerptBatchTest(BatchTestBase):
    PAGE_A = "wiki/concepts/RDMA.md"
    PAGE_B = "wiki/concepts/異常検知.md"
    PAGE_MISSING = "wiki/concepts/存在しないページ.md"

    def test_single_page_output_is_unchanged_by_batching(self):
        single = self.excerpt(self.PAGE_A, "--outline")
        self.assertEqual(single.returncode, 0, single.stderr)
        self.assertNotIn("=== ", single.stdout)
        self.assertIn(f"wiki-excerpt: {self.PAGE_A} outline ~", single.stderr)

        multi = self.excerpt(self.PAGE_A, self.PAGE_B, "--outline")
        self.assertEqual(multi.returncode, 0, multi.stderr)
        head, _, _tail = multi.stdout.partition(f"=== {self.PAGE_B} ===")
        first_line, _, first_block = head.partition("\n")
        self.assertEqual(first_line, f"=== {self.PAGE_A} ===")
        self.assertEqual(first_block.rstrip("\n"), single.stdout.rstrip("\n"))

    def test_single_page_sections_output_is_unchanged_by_batching(self):
        args = ("--sections", "定義,子概念,未解決の問い,未編纂の観察", "--tail", "2")
        single = self.excerpt(self.PAGE_A, *args)
        multi = self.excerpt(self.PAGE_A, self.PAGE_B, *args)
        head, _, _tail = multi.stdout.partition(f"=== {self.PAGE_B} ===")
        _, _, first_block = head.partition("\n")
        self.assertEqual(first_block.rstrip("\n"), single.stdout.rstrip("\n"))

    def test_multi_page_separators_and_summary(self):
        proc = self.excerpt(self.PAGE_A, self.PAGE_B, "--outline")
        heads = [ln for ln in proc.stdout.splitlines() if ln.startswith("=== ")]
        self.assertEqual(heads, [f"=== {self.PAGE_A} ===", f"=== {self.PAGE_B} ==="])
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^wiki-excerpt: 2 pages ~\d+ tokens$")

    def test_quiet_suppresses_summary(self):
        proc = self.excerpt(self.PAGE_A, self.PAGE_B, "--outline", "--quiet")
        self.assertEqual(proc.stderr, "")

    def test_missing_page_marks_line_and_exits_three(self):
        proc = self.excerpt(self.PAGE_A, self.PAGE_MISSING, self.PAGE_B, "--outline")
        self.assertEqual(proc.returncode, 3)
        self.assertIn(f"=== {self.PAGE_MISSING} === (missing)", proc.stdout)
        self.assertIn(f"=== {self.PAGE_B} ===", proc.stdout)
        self.assertIn("# outline: wiki/concepts/異常検知.md", proc.stdout)
        self.assertIn("1 missing", proc.stderr)

    def test_single_missing_page_keeps_legacy_error(self):
        proc = self.excerpt(self.PAGE_MISSING)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout, "")
        self.assertIn(f"ERR: missing file: {self.PAGE_MISSING}", proc.stderr)

    def test_hash_separator_args_are_ignored(self):
        """`# <query>` 区切りを混ぜても欠落扱いにせず、実パスの excerpt を出す。"""
        proc = self.excerpt(self.PAGE_A, "# RDMA", "--sections", "定義")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("(missing)", proc.stdout)
        self.assertNotIn("=== ", proc.stdout)
        self.assertIn("## 定義", proc.stdout)
        self.assertIn("RDMA の定義文", proc.stdout)

    def test_only_hash_args_is_usage_error(self):
        proc = self.excerpt("# RDMA")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no page path given", proc.stderr)

    def test_total_budget_skips_remaining_pages(self):
        proc = self.excerpt(self.PAGE_A, self.PAGE_B, "--outline", "--total-budget", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"=== {self.PAGE_A} ===", proc.stdout)
        self.assertIn(f"=== {self.PAGE_B} === (skipped: total budget)", proc.stdout)
        self.assertNotIn("# outline: wiki/concepts/異常検知.md", proc.stdout)
        self.assertIn("1 skipped", proc.stderr)

    def test_total_budget_missing_page_is_missing_not_skipped(self):
        """予算超過後でも欠落ページは (missing) + exit 3。"""
        proc = self.excerpt(
            self.PAGE_A, self.PAGE_MISSING, self.PAGE_B,
            "--outline", "--total-budget", "1",
        )
        self.assertEqual(proc.returncode, 3)
        self.assertIn(f"=== {self.PAGE_MISSING} === (missing)", proc.stdout)
        self.assertIn(f"=== {self.PAGE_B} === (skipped: total budget)", proc.stdout)
        self.assertIn("1 missing", proc.stderr)
        self.assertIn("1 skipped", proc.stderr)

    def test_total_budget_large_keeps_every_page(self):
        proc = self.excerpt(self.PAGE_A, self.PAGE_B, "--outline", "--total-budget", "100000")
        self.assertNotIn("skipped", proc.stdout)
        self.assertIn("# outline: wiki/concepts/異常検知.md", proc.stdout)

    def test_fm_keys_narrows_frontmatter(self):
        proc = self.excerpt(self.PAGE_A, "--sections", "定義", "--fm-keys", "title,aliases")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("title: RDMA", proc.stdout)
        self.assertIn("aliases:", proc.stdout)
        self.assertNotIn("type: concept", proc.stdout)
        self.assertNotIn("domain:", proc.stdout)
        self.assertNotIn("sources:", proc.stdout)
        self.assertNotIn("related:", proc.stdout)

    def test_fm_keys_unknown_warns(self):
        proc = self.excerpt(self.PAGE_A, "--sections", "定義", "--fm-keys", "title,notakey")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("wiki-excerpt: unknown --fm-keys: notakey", proc.stderr)
        self.assertIn("title: RDMA", proc.stdout)

    def test_fm_keys_none_omits_frontmatter(self):
        proc = self.excerpt(self.PAGE_A, "--sections", "定義", "--fm-keys", "none")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(proc.stdout.startswith("---"))
        self.assertNotIn("title:", proc.stdout)
        self.assertIn("## 定義", proc.stdout)

    def test_default_frontmatter_unchanged(self):
        proc = self.excerpt(self.PAGE_A, "--sections", "定義")
        self.assertTrue(proc.stdout.startswith("---\n"))
        self.assertIn("domain: network", proc.stdout)
        self.assertIn("sources:", proc.stdout)

    def test_usage_errors(self):
        self.assertEqual(self.excerpt().returncode, 2)
        self.assertEqual(self.excerpt(self.PAGE_A, "--tail", "-1").returncode, 2)
        self.assertEqual(self.excerpt(self.PAGE_A, "--total-budget", "0").returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
