#!/usr/bin/env python3
"""paper-ids.py のテスト。一時 vault(WIKI_VAULT_ROOT)で走らせる。

    python3 scripts/test_paper_ids.py
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page(title, source_type="paper", url="", extra_fm="", body=""):
    return (
        "---\n"
        f'address: "c-000001"\n'
        "type: source\n"
        f'title: "{title}"\n'
        f"source_type: {source_type}\n"
        f'url: "{url}"\n'
        f"{extra_fm}"
        "date: 2026-01-01 10:00\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "tags:\n  - 2026/01/01\n"
        "status: developing\n"
        "---\n\n"
        f"# {title}\n\n{body}\n"
    )


class PaperIdsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wiki/sources").mkdir(parents=True)
        (self.root / ".vault-meta").mkdir()
        os.environ["WIKI_VAULT_ROOT"] = str(self.root)
        self.m = load_module("paper_ids", "paper-ids.py")
        self.src = self.root / "wiki/sources"

    def tearDown(self):
        os.environ.pop("WIKI_VAULT_ROOT", None)
        self.tmp.cleanup()

    def write(self, name, text):
        (self.src / f"{name}.md").write_text(text, encoding="utf-8")

    def run_cmd(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.m.main(list(argv))
        return code, buf.getvalue()

    # --- 正規化 -------------------------------------------------------------
    def test_normalizers(self):
        self.assertEqual(self.m.norm_arxiv("https://arxiv.org/abs/2405.16444v2"), "2405.16444")
        self.assertEqual(self.m.norm_arxiv("arXiv:2406.07976"), "2406.07976")
        self.assertIsNone(self.m.norm_arxiv("2026-07-01"))
        self.assertEqual(self.m.norm_doi("https://doi.org/10.1016/0167-9236(94)90040-4"), "10.1016/0167-9236(94)90040-4")
        self.assertEqual(self.m.norm_doi("(see https://doi.org/10.1145/3637528.3672051)"), "10.1145/3637528.3672051")
        self.assertEqual(self.m.norm_doi("https://doi.org/10.1145/3377811.3380377?x=1"), "10.1145/3377811.3380377")
        self.assertEqual(self.m.norm_doi("DOI:10.14778/3538598.3538602."), "10.14778/3538598.3538602")
        self.assertEqual(self.m.norm_doi("10.5281/zenodo.1183752(公開データ)"), "10.5281/zenodo.1183752")
        self.assertEqual(self.m.title_key("Attention Is All You Need"), "attentionisallyouneed")
        self.assertEqual(self.m.doc_key("wiki/sources/@2024__X__T - Chapter 3 Foo.md"), "@2024__X__T")
        self.assertEqual(self.m.doc_key("wiki/sources/@2025__TOS__Lustre - Appendix A History.md"), "@2025__TOS__Lustre")

    # --- 導出の優先順と本文の扱い ----------------------------------------------
    def test_derive_priority_and_body_rules(self):
        self.write("@2025__EuroSys__A", page("A", url="https://arxiv.org/abs/2405.16444v1",
                                             body="see https://doi.org/10.1145/3689031.3696098 and https://doi.org/10.5281/zenodo.1"))
        self.write("@2024__KDD__B", page("B", url="https://doi.org/10.1145/3637528.3672051",
                                         extra_fm='sources:\n  - "[[.raw/papers/arxiv-2408.13729.pdf]]"\n'))
        # 書籍の章: 本文に論文リンクが 1 つだけあっても章自身の ID にしない
        self.write("@2025__Gihyo__Book - Chapter 3 Attention", page("Book ch3", source_type="book",
                                                                    body="原論文 https://arxiv.org/abs/1706.03762"))
        # 本文に 2 種類の arXiv リンク → 曖昧なので採らない
        self.write("@2026__X__C", page("C", body="https://arxiv.org/abs/1111.11111 https://arxiv.org/abs/2222.22222"))
        recs = {Path(r["path"]).stem: r for r in self.m.scan()}
        a, b, book, c = recs["@2025__EuroSys__A"], recs["@2024__KDD__B"], recs["@2025__Gihyo__Book - Chapter 3 Attention"], recs["@2026__X__C"]
        self.assertEqual((a["arxiv"], a["arxiv_from"]), ("2405.16444", "url"))
        self.assertEqual((a["doi"], a["doi_from"]), ("10.1145/3689031.3696098", "body"))  # zenodo は除外
        self.assertEqual((b["doi"], b["doi_from"]), ("10.1145/3637528.3672051", "url"))
        self.assertEqual((b["arxiv"], b["arxiv_from"]), ("2408.13729", "sources"))
        self.assertIsNone(book["arxiv"])
        self.assertIsNone(c["arxiv"])

    # --- check / dupes / 章畳み込み --------------------------------------------
    def test_check_and_dupes_fold_chapters(self):
        self.write("@2017__NeurIPS__Attention Is All You Need", page("Attention Is All You Need", url="https://arxiv.org/abs/1706.03762"))
        self.write("@2026__30papers__Attention Is All You Need", page("Attention Is All You Need", url="https://arxiv.org/pdf/1706.03762v7"))
        for n in (1, 2):
            self.write(f"@2024__TMLR__Survey - Chapter {n} Part", page(f"Survey - Chapter {n} Part", url="https://arxiv.org/abs/2312.03863"))
        code, out = self.run_cmd("check", "1706.03762", "https://arxiv.org/abs/2312.03863v2", "9999.99999", "--compact")
        self.assertEqual(code, 0)
        lines = out.strip().split("\n")
        self.assertIn("HIT", lines[0]) and self.assertIn("@2017__NeurIPS__", lines[0])
        self.assertIn("HIT", lines[1])
        self.assertIn("NONE", lines[2])
        code, _ = self.run_cmd("check", "1706.03762", "--fail-on-match")
        self.assertEqual(code, self.m.EXIT_MATCH)
        code, out = self.run_cmd("dupes")
        d = json.loads(out)
        self.assertIn("1706.03762", d["arxiv"])          # 2 文書 → 重複
        self.assertNotIn("2312.03863", d["arxiv"])       # 章分割は 1 文書 → 重複でない
        self.assertIn("attentionisallyouneed", d["title"])
        # 索引は check が書き、ディレクトリ mtime より古くなれば作り直す
        self.assertTrue((self.root / ".vault-meta/paper-ids.json").is_file())
        report = self.run_cmd("report")[1]
        self.assertIn("## Paper IDs", report)
        self.assertIn("`1706.03762`", report)

    def test_whole_plus_chapters(self):
        self.write("@2024__arXiv__Survey", page("Survey", url="https://arxiv.org/abs/2404.14294"))
        self.write("@2024__arXiv__Survey - Chapter 1 Intro", page("Survey - Chapter 1 Intro", url="https://arxiv.org/abs/2404.14294"))
        self.write("@2024__arXiv__Survey - Chapter 2 Body", page("Survey - Chapter 2 Body", url="https://arxiv.org/abs/2404.14294"))
        self.assertEqual(self.m.whole_plus_chapters(self.m.scan()), [("@2024__arXiv__Survey", 2)])
        self.assertIn("1 枚ものと章分割の併存: 1 文書", self.run_cmd("report")[1])

    # --- backfill ------------------------------------------------------------
    def test_backfill_writes_after_url_without_bumping_updated(self):
        self.write("@2025__EuroSys__A", page("A", url="https://arxiv.org/abs/2405.16444",
                                             body="https://doi.org/10.1145/3689031.3696098"))
        self.write("@2024__KDD__B", page("B", url="https://doi.org/10.1145/3637528.3672051", extra_fm='doi: "10.1145/3637528.3672051"\n'))
        code, out = self.run_cmd("backfill", "--include-dirty")
        d = json.loads(out)
        self.assertTrue(d["dry_run"])
        self.assertEqual((d["pages"], d["arxiv_added"], d["doi_added"]), (1, 1, 1))
        text_before = (self.src / "@2025__EuroSys__A.md").read_text(encoding="utf-8")
        code, out = self.run_cmd("backfill", "--write", "--include-dirty", "--no-index")
        self.assertEqual(json.loads(out)["written"], 1)
        text = (self.src / "@2025__EuroSys__A.md").read_text(encoding="utf-8")
        self.assertIn('url: "https://arxiv.org/abs/2405.16444"\narxiv_id: "2405.16444"\ndoi: "10.1145/3689031.3696098"\n', text)
        self.assertIn("updated: 2026-01-01", text)
        self.assertEqual(text.split("---")[2], text_before.split("---")[2])  # 本文は不変
        # 2 回目は何も書かない(冪等)
        code, out = self.run_cmd("backfill", "--write", "--include-dirty", "--no-index")
        self.assertEqual(json.loads(out)["written"], 0)
        # B は既に doi を持つので対象外のまま
        self.assertEqual((self.src / "@2024__KDD__B.md").read_text(encoding="utf-8").count("doi:"), 1)


if __name__ == "__main__":
    unittest.main()
