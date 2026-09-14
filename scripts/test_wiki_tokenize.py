#!/usr/bin/env python3
"""wiki_tokenize と BM25 和文 2-gram の試験。一時 vault は実 wiki に触れない。"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def load_mod(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TokenizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tok = load_mod("wiki_tokenize_ut", "wiki_tokenize.py")

    def t(self, text):
        return self.tok.tokenize(text)

    def test_english_words_and_stopwords(self):
        self.assertEqual(self.t("PagedAttention"), ["pagedattention"])
        self.assertIn("well-formed", self.t("a well-formed user's cache"))
        self.assertIn("user's", self.t("a well-formed user's cache"))
        self.assertNotIn("the", self.t("the cache"))
        self.assertNotIn("a", self.t("a cache"))

    def test_japanese_clause_is_not_one_token(self):
        clause = "分散トレーシングの定義はこれである"
        tokens = self.t(clause)
        self.assertGreater(len(tokens), 1)
        self.assertNotIn(clause, tokens)
        self.assertNotIn(clause.casefold(), tokens)

    def test_japanese_query_overlaps_longer_sentence(self):
        q = set(self.t("分散トレーシング"))
        doc = set(self.t("LLM推論における分散トレーシングの実装"))
        self.assertTrue(q)
        self.assertTrue(q & doc)

    def test_mixed_latin_and_cjk(self):
        tokens = self.t("KVキャッシュ管理")
        self.assertIn("kv", tokens)
        self.assertIn("キャ", tokens)
        self.assertIn("管理", tokens)
        self.assertNotIn("kvキャッシュ管理", tokens)
        self.assertNotIn("llm推論における分散トレーシングの実装",
                         self.t("LLM推論における分散トレーシングの実装"))

    def test_nfkc_fullwidth_latin(self):
        self.assertEqual(self.t("ＫＶ"), self.t("KV"))

    def test_single_cjk_is_kept(self):
        self.assertEqual(self.t("木"), ["木"])

    def test_digit_kanji_overlaps_containing_sentence(self):
        q = set(self.t("3台"))
        doc = set(self.t("専用の3台のクライアントと1台のサーバからなる。"))
        self.assertIn("3", q)
        self.assertIn("台", q)
        self.assertIn("3台", q)
        self.assertTrue({"3", "3台"} <= doc)
        self.assertTrue(q & doc)
        self.assertEqual(self.t("３台"), self.t("3台"))

    def test_thirteen_does_not_emit_three_bridge(self):
        q = set(self.t("3台"))
        doc = set(self.t("13台のGPU"))
        self.assertIn("13台", doc)
        self.assertNotIn("3台", doc)
        self.assertNotIn("3", doc)
        self.assertEqual(q & doc, {"台"})

    def test_latin_prefix_digits_still_bridge_to_cjk(self):
        q = set(self.t("10台"))
        doc = set(self.t("Spine10台とLeaf20台"))
        self.assertIn("spine10", doc)
        self.assertTrue({"10", "10台"} <= q)
        self.assertTrue({"10", "10台"} <= doc)
        self.assertTrue({"10", "10台"} <= q & doc)
        oss = set(self.t("OSS3台"))
        self.assertTrue({"3", "3台"} <= oss & set(self.t("3台")))
        self.assertIn("h100", self.t("H100"))
        self.assertIn("h100", self.t("H100台"))
        self.assertTrue({"1000", "1000台"} <= set(self.t("1,000台")) & set(self.t("1000台")))

    def test_iteration_mark_stays_in_cjk_run(self):
        tokens = self.t("人々")
        self.assertIn("人々", tokens)
        self.assertTrue(set(self.t("人々")) & set(self.t("多くの人々が使う")))

    def test_old_regex_would_glue_japanese(self):
        import re
        old = re.compile(r"\w[\w'\-]*", re.UNICODE)
        text = "分散トレーシングの定義"
        self.assertEqual(old.findall(text), [text])
        self.assertGreater(len(self.t(text)), 1)

    @unittest.skipUnless(
        (Path(__file__).resolve().parent / "curation" / "pref-index.py").is_file(),
        "vault-only pref-index.py is absent",
    )
    def test_pref_index_shares_the_same_function(self):
        pref = load_mod("pref_index_tok", Path("curation") / "pref-index.py")
        text = "LLM推論における分散トレーシングの実装"
        self.assertEqual(pref.tokenize(text), self.t(text))
        self.assertTrue(set(pref.tokenize("分散トレーシング")) & set(self.t(text)))


class Bm25JapaneseQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name).resolve()
        chunks = self.vault / ".vault-meta" / "chunks" / "c-000001"
        chunks.mkdir(parents=True)
        (chunks / "chunk-000.json").write_text(json.dumps({
            "page_address": "c-000001",
            "chunk_index": 0,
            "contextualized_text": "LLM推論における分散トレーシングの実装と観測。",
        }), encoding="utf-8")
        (self.vault / ".vault-meta" / "chunks" / "c-000002").mkdir()
        (self.vault / ".vault-meta" / "chunks" / "c-000002" / "chunk-000.json").write_text(
            json.dumps({
                "page_address": "c-000002",
                "chunk_index": 0,
                "contextualized_text": "専用の3台のクライアントと1台のサーバからなる。",
            }), encoding="utf-8")
        (self.vault / ".vault-meta" / "chunks" / "c-000003").mkdir()
        (self.vault / ".vault-meta" / "chunks" / "c-000003" / "chunk-000.json").write_text(
            json.dumps({
                "page_address": "c-000003",
                "chunk_index": 0,
                "contextualized_text": "Clos は Spine10台と Leaf20台で組む。",
            }), encoding="utf-8")
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        sys.modules.pop("wiki_tokenize", None)
        sys.modules.pop("bm25_index_ja", None)
        self.bm25 = load_mod("bm25_index_ja", "bm25-index.py")
        self.bm25.VAULT_ROOT = self.vault
        self.bm25.META_DIR = self.vault / ".vault-meta"
        self.bm25.CHUNKS_DIR = self.vault / ".vault-meta" / "chunks"
        self.bm25.BM25_DIR = self.vault / ".vault-meta" / "bm25"
        self.bm25.INDEX_PATH = self.bm25.BM25_DIR / "index.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_japanese_query_hits_clause_inside_sentence(self):
        idx = self.bm25.build_index()
        self.assertEqual(idx["schema_version"], self.bm25.SCHEMA_VERSION)
        self.assertEqual(idx["tokenize"], self.bm25.TOKENIZE_ID)
        hits = self.bm25.query("分散トレーシング", top_k=5, index=idx)
        self.assertEqual(hits[0]["chunk_id"], "c-000001:0")
        self.assertGreater(hits[0]["score"], 0)

    def test_digit_kanji_query_hits_containing_sentence(self):
        idx = self.bm25.build_index()
        hits = self.bm25.query("3台", top_k=5, index=idx)
        self.assertEqual(hits[0]["chunk_id"], "c-000002:0")
        self.assertGreater(hits[0]["score"], 0)

    def test_latin_prefix_digit_query_hits_containing_sentence(self):
        idx = self.bm25.build_index()
        hits = self.bm25.query("10台", top_k=5, index=idx)
        self.assertEqual(hits[0]["chunk_id"], "c-000003:0")
        self.assertGreater(hits[0]["score"], 0)

    def test_unrelated_english_query_misses(self):
        idx = self.bm25.build_index()
        hits = self.bm25.query("optical circuit switch gardening", top_k=5, index=idx)
        self.assertEqual(hits, [])

    def test_load_index_warns_on_old_schema_but_still_loads(self):
        import io
        from contextlib import redirect_stderr
        self.bm25.BM25_DIR.mkdir(parents=True, exist_ok=True)
        self.bm25.INDEX_PATH.write_text(json.dumps({
            "schema_version": 1,
            "params": {"k1": 1.5, "b": 0.75},
            "doc_count": 0,
            "avg_dl": 1.0,
            "vocab": {},
            "docs": {},
        }), encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            idx = self.bm25.load_index()
        self.assertEqual(idx["schema_version"], 1)
        self.assertIn("分かちが古い", err.getvalue())

    def test_load_index_silent_on_current_schema(self):
        import io
        from contextlib import redirect_stderr
        idx = self.bm25.build_index()
        self.bm25.write_index(idx)
        err = io.StringIO()
        with redirect_stderr(err):
            loaded = self.bm25.load_index()
        self.assertEqual(loaded["schema_version"], idx["schema_version"])
        self.assertEqual(loaded["tokenize"], idx["tokenize"])
        self.assertEqual(loaded["schema_version"], self.bm25.SCHEMA_VERSION)
        self.assertEqual(loaded["tokenize"], self.bm25.TOKENIZE_ID)
        self.assertNotIn("分かちが古い", err.getvalue())

    def test_load_index_warns_on_schema_2_old_tokenize(self):
        import io
        from contextlib import redirect_stderr
        self.bm25.BM25_DIR.mkdir(parents=True, exist_ok=True)
        self.bm25.INDEX_PATH.write_text(json.dumps({
            "schema_version": 2,
            "tokenize": "cjk-bigram-v1",
            "params": {"k1": 1.5, "b": 0.75},
            "doc_count": 0,
            "avg_dl": 1.0,
            "vocab": {},
            "docs": {},
        }), encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            idx = self.bm25.load_index()
        self.assertEqual(idx["schema_version"], 2)
        self.assertIn("分かちが古い", err.getvalue())


if __name__ == "__main__":
    unittest.main()
