#!/usr/bin/env python3
"""bib-lookup.py のテスト。HTTP は差し替える。

    python3 scripts/test_bib_lookup.py
"""

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bib_lookup", SCRIPTS / "bib-lookup.py")
bl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bl)


def run(argv, responder):
    calls = []

    def fake(url):
        calls.append(url)
        return responder(url)

    old = bl.http_json
    bl.http_json = fake
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = bl.main(argv)
    finally:
        bl.http_json = old
    return code, buf.getvalue(), calls


class Helpers(unittest.TestCase):
    def test_norm_doi(self):
        self.assertEqual(bl.norm_doi("https://doi.org/10.1145/ABC."), "10.1145/abc")

    def test_inverted_abstract(self):
        self.assertEqual(bl.abstract_from_inverted({"b": [1], "a": [0], "c": [2]}), "a b c")
        self.assertEqual(bl.abstract_from_inverted(None), "")

    def test_strip_jats(self):
        self.assertEqual(bl.strip_jats("<jats:p>a &amp; b</jats:p>"), "a & b")


class Commands(unittest.TestCase):
    def test_retracted_reads_frontmatter_and_batches(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "wiki" / "sources"
            d.mkdir(parents=True)
            for i in range(51):
                (d / f"@p{i}.md").write_text(f'---\ndoi: "10.1000/x{i}"\n---\nbody doi: 10.9/no\n')
            (d / "@nodoi.md").write_text("---\ntitle: a\n---\n")
            os.environ["WIKI_VAULT_ROOT"] = t

            def resp(url):
                if "x3" in url:
                    return {"results": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/x3", "title": "T"}]}
                return {"results": []}

            code, out, calls = run(["retracted"], resp)
            del os.environ["WIKI_VAULT_ROOT"]
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)  # 51 件 → 50 + 1
        self.assertIn("checked=51 retracted=1", out)
        self.assertIn("RETRACTED\t10.1000/x3\twiki/sources/@p3.md", out)

    def test_pair_finds_arxiv_in_locations(self):
        def resp(url):
            return {"id": "https://openalex.org/W9", "locations": [
                {"landing_page_url": "https://doi.org/10.1007/x"},
                {"landing_page_url": "http://arxiv.org/abs/2407.20018"}]}
        code, out, _ = run(["pair", "10.1007/X"], resp)
        self.assertEqual(code, 0)
        self.assertIn("arxiv_id=2407.20018", out)

    def test_pair_without_arxiv_exits_none(self):
        code, out, _ = run(["pair", "10.1/a"], lambda u: {"id": "W1", "locations": []})
        self.assertEqual(code, bl.EXIT_NONE)

    def test_cites_both_builds_and_filter(self):
        def resp(url):
            if "filter=cites" in url:
                return {"meta": {"count": 1}, "results": [{"id": "W5", "doi": None, "title": "X",
                        "publication_year": 2026, "primary_location": None}]}
            return {"id": "https://openalex.org/W77"}
        code, out, calls = run(["cites-both", "10.1/a", "W2"], resp)
        self.assertEqual(code, 0)
        self.assertIn("cites:W77,cites:W2", calls[-1].replace("%2C", ","))
        self.assertIn("count=1", out)

    def test_cites_both_needs_two(self):
        code, _, _ = run(["cites-both", "W1"], lambda u: {})
        self.assertEqual(code, bl.EXIT_USAGE)

    def test_refs_counts_doi(self):
        resp = lambda u: {"message": {"reference": [{"DOI": "10.1/A"}, {"key": "k"}]}}
        code, out, _ = run(["refs", "10.1/p"], resp)
        self.assertIn("references=2\twith_doi=1", out)
        self.assertIn("10.1/a", out)

    def test_abstracts(self):
        resp = lambda u: {"results": [{"doi": "https://doi.org/10.1/a", "abstract_inverted_index": {"hi": [0]}}]}
        code, out, _ = run(["abstracts", "10.1/a"], resp)
        self.assertEqual(code, 0)
        self.assertIn('"abstract": "hi"', out)

    def test_population_sorted_desc(self):
        resp = lambda u: {"group_by": [{"key": "2024", "count": 2}, {"key": "2026", "count": 5}]}
        code, out, _ = run(["population", "log parsing"], resp)
        self.assertIn("total=7", out)
        self.assertLess(out.index("2026"), out.index("2024\t"))

    def test_meta_fields(self):
        resp = lambda u: {"message": {"DOI": "10.1/a", "title": ["T"], "issued": {"date-parts": [[2023, 12]]},
                          "container-title": ["V"], "abstract": "<jats:p>abs</jats:p>",
                          "author": [{"given": "A", "family": "B"}],
                          "updated-by": [{"type": "retraction"}]}}
        code, out, _ = run(["meta", "10.1/a"], resp)
        self.assertIn('"year": 2023', out)
        self.assertIn('"abstract": "abs"', out)
        self.assertIn("retraction", out)

    def test_budget_exit_code(self):
        def resp(url):
            raise bl.BudgetExhausted("x")
        code, _, _ = run(["search", "q"], resp)
        self.assertEqual(code, bl.EXIT_BUDGET)

    def test_not_found(self):
        def resp(url):
            raise bl.NotFound(url)
        code, _, _ = run(["pair", "10.1/a"], resp)
        self.assertEqual(code, bl.EXIT_NONE)


if __name__ == "__main__":
    unittest.main()
