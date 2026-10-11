#!/usr/bin/env python3
"""dense-index.py と retrieve.py の密ベクトル経路・複数問い統合(--also)の試験。

WIKI_VAULT_ROOT で一時 vault を指し、実 wiki には触れない。ollama は呼ばず、
rerank モジュールの ollama_alive / embed_many を語の有無で決まる偽の埋め込みに差し替える。
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

# 偽の埋め込みの次元。同じ次元の語は「意味が近い」とみなす(BM25 では一致しない言い換え)
DIMS = (("optical", "photonic", "光スイッチ"), ("rdma", "infiniband"), ("garden", "庭"))


def fake_embed_many(url, model, texts):
    out = []
    for t in texts:
        low = t.lower()
        out.append([1.0 if any(w in low for w in dim) else 0.0 for dim in DIMS] + [0.01])
    return out


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def patch_rerank(mod):
    mod.ollama_alive = lambda url: (True, [mod.DEFAULT_MODEL])
    mod.embed_many = fake_embed_many
    mod.embed_one = lambda url, model, text: fake_embed_many(url, model, [text])[0]
    return mod


def page(title, address, body):
    return f'---\ntype: concept\ntitle: "{title}"\naddress: {address}\n---\n\n# {title}\n\n{body}\n'


class DenseFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "wiki/concepts").mkdir(parents=True)
        (self.vault / ".vault-meta").mkdir()
        self.write("wiki/concepts/A.md", page("A", "c-000001", "RDMA verbs queue pair completion."))
        self.write("wiki/concepts/B.md", page("B", "c-000002", "InfiniBand fabric transport for RDMA."))
        self.write("wiki/concepts/C.md", page("C", "c-000003", "Photonic interconnect wavelength switching."))
        self.write("wiki/concepts/E.md", page("E", "c-000005", "Unrelated isolated page about gardening."))
        self.refresh()
        self.old_root = os.environ.get("WIKI_VAULT_ROOT")
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        self.dense = load("dense_index", "dense-index.py")
        rr = patch_rerank(load("rerank", "rerank.py"))
        self.dense.load_rerank = lambda: rr

    def tearDown(self):
        if self.old_root is None:
            os.environ.pop("WIKI_VAULT_ROOT", None)
        else:
            os.environ["WIKI_VAULT_ROOT"] = self.old_root
        self.tmp.cleanup()

    def write(self, rel, text):
        (self.vault / rel).write_text(text, encoding="utf-8")

    def refresh(self):
        env = dict(os.environ, WIKI_VAULT_ROOT=str(self.vault))
        for args in (["contextual-prefix.py", "--all", "--no-llm"], ["bm25-index.py", "build"]):
            res = subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]],
                                 capture_output=True, text=True, env=env, cwd=str(self.vault))
            self.assertEqual(res.returncode, 0, res.stderr)

    def build(self):
        self.assertEqual(self.dense.build(), 0)

    def searcher(self):
        retrieve = load("retrieve", "retrieve.py")
        s = retrieve.Searcher()
        patch_rerank(s.reranker)
        return s


class DenseIndexTest(DenseFixture):
    def test_build_then_rebuild_reuses_unchanged_pages(self):
        self.build()
        idx = self.dense.load_index(self.dense.load_rerank().DEFAULT_MODEL)
        self.assertEqual(sorted(e["path"] for e in idx[0]["pages"]),
                         [f"wiki/concepts/{n}.md" for n in "ABCE"])
        self.assertEqual(len(idx[1]), 4 * idx[0]["dim"])

        calls = []
        rr = self.dense.load_rerank()
        rr.embed_many = lambda url, model, texts: calls.append(len(texts)) or fake_embed_many(url, model, texts)
        self.build()
        self.assertEqual(calls, [])  # 本文が変わっていなければ埋め込み直さない

        self.write("wiki/concepts/E.md", page("E", "c-000005", "Rose garden pruning schedule."))
        self.refresh()
        self.build()
        self.assertEqual(calls, [1])

    def test_misaligned_vectors_are_rejected(self):
        self.build()
        model = self.dense.load_rerank().DEFAULT_MODEL
        _, vec_path = self.dense.index_paths(model)
        vec_path.write_bytes(vec_path.read_bytes()[:-4])
        self.assertIsNone(self.dense.load_index(model))

    def test_query_ranks_nearest_page_first(self):
        self.build()
        idx = self.dense.load_index(self.dense.load_rerank().DEFAULT_MODEL)
        hits = self.dense.query(idx, fake_embed_many(None, None, ["光スイッチ"])[0], top_k=2)
        self.assertEqual(hits[0][1]["path"], "wiki/concepts/C.md")


class RetrieveDenseChannelTest(DenseFixture):
    QUERY = "光スイッチ RDMA"  # C は BM25 では当たらない(英語の Photonic のみ)

    def test_dense_adds_lexically_distant_page(self):
        self.build()
        out = self.searcher().search(self.QUERY, top=10, no_graph=True, explain=True)
        pages = {c["page_path"]: c for c in out["candidates"]}
        self.assertIn("wiki/concepts/C.md", pages)
        self.assertEqual(pages["wiki/concepts/C.md"]["channels"], ["dense"])
        self.assertIn("dense_score", pages["wiki/concepts/C.md"])
        self.assertTrue(out["strategy"].endswith("+dense"))
        self.assertNotIn("channels", pages["wiki/concepts/A.md"])  # BM25 で既にある頁は足さない
        # 密ベクトル経路が動いたときの並びは BM25・ページ密・rerank の 3 順位の RRF
        scores = [c["fused_score"] for c in out["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_absent_index_and_no_dense_leave_output_unchanged(self):
        baseline = self.searcher().search(self.QUERY, top=10, no_graph=True)
        self.assertNotIn("wiki/concepts/C.md", [c["page_path"] for c in baseline["candidates"]])
        self.build()
        self.assertEqual(self.searcher().search(self.QUERY, top=10, no_graph=True, no_dense=True), baseline)
        self.assertNotIn("fused_score", baseline["candidates"][0])

    def test_no_rerank_skips_dense(self):
        self.build()
        out = self.searcher().search(self.QUERY, top=10, no_graph=True, no_rerank=True)
        self.assertNotIn("wiki/concepts/C.md", [c["page_path"] for c in out["candidates"]])


class RetrieveMultiQueryTest(DenseFixture):
    def run_multi(self, queries, **kw):
        retrieve = load("retrieve", "retrieve.py")
        s = retrieve.Searcher()
        patch_rerank(s.reranker)
        return retrieve, retrieve.search_multi(s, queries, top=10, no_graph=True, **kw)

    def test_reformulation_adds_page_but_original_hits_stay_ahead(self):
        _, out = self.run_multi(["RDMA verbs queue pair", "Photonic wavelength switching"])
        pages = [c["page_path"] for c in out["candidates"]]
        self.assertIn("wiki/concepts/C.md", pages)  # 言い換えでだけ当たる頁
        self.assertEqual(pages[0], "wiki/concepts/A.md")  # 元の問いの 1 位は押し出されない
        rows = {c["page_path"]: c for c in out["candidates"]}
        self.assertEqual(rows["wiki/concepts/C.md"]["matched_queries"], [1])
        self.assertIn(0, rows["wiki/concepts/A.md"]["matched_queries"])
        self.assertEqual(out["also"], ["Photonic wavelength switching"])
        self.assertTrue(out["strategy"].endswith("+multi2"))
        scores = [c["multi_score"] for c in out["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_original_weight_beats_a_single_reformulation(self):
        retrieve, out = self.run_multi(["Photonic wavelength switching", "RDMA verbs queue pair"])
        self.assertEqual(out["candidates"][0]["page_path"], "wiki/concepts/C.md")
        self.assertGreater(retrieve.MULTI_ORIG_WEIGHT, 1.0)


if __name__ == "__main__":
    unittest.main()
