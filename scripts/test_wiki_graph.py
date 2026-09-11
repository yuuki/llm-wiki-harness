#!/usr/bin/env python3
"""wiki-graph.py と retrieve.py のグラフ第 3 路の試験。

WIKI_VAULT_ROOT で一時 vault を指し、実 wiki には触れない。retrieve の試験は
contextual-prefix.py --all --no-llm と bm25-index.py build で一時 vault に索引を作る。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run_script(name, args, vault):
    env = os.environ.copy()
    env["WIKI_VAULT_ROOT"] = str(vault)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault),
        check=False,
    )


def page(kind, title, address, body, sources=(), related=()):
    fm = ["---", f"type: {kind}", f'title: "{title}"', f"address: {address}"]
    if related:
        fm.append("related:")
        fm += [f'  - "[[{r}]]"' for r in related]
    if sources:
        fm.append("sources:")
        fm += [f'  - "[[{s}]]"' for s in sources]
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {title}\n\n{body}\n"


class GraphFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel in ("wiki/concepts", "wiki/sources", "wiki/entities", ".vault-meta"):
            (self.vault / rel).mkdir(parents=True)
        # A ⇄ B は相互リンク、A → C は片方向、C と D は共通出典、E は孤立
        self.write("wiki/concepts/A.md", page("concept", "A", "c-000001",
                   "RDMA verbs queue pair completion. see [[B]] and [[C]].", sources=["@2020__X__S1"]))
        self.write("wiki/concepts/B.md", page("concept", "B", "c-000002",
                   "InfiniBand fabric transport. back to [[A]].", sources=["@2020__X__S1"]))
        self.write("wiki/concepts/C.md", page("concept", "C", "c-000003",
                   "Photonic interconnect wavelength switching for datacenters.", sources=["@2021__Y__S2"]))
        self.write("wiki/entities/D.md", page("entity", "D", "c-000004",
                   "A vendor of optical circuit switches.", sources=["@2021__Y__S2"]))
        self.write("wiki/concepts/E.md", page("concept", "E", "c-000005", "Unrelated isolated page about gardening."))
        # F は address 無し(合成 address で chunk される)。G は著者(person)。どちらも A から片方向リンク
        self.write("wiki/concepts/F.md", "---\ntype: concept\ntitle: \"F\"\n---\n\n# F\n\nOptical circuit switch reconfiguration latency.\n")
        self.write("wiki/entities/G.md", "---\ntype: entity\ntitle: \"G\"\naddress: c-000008\nentity_type: person\n---\n\n# G\n\nAn author who wrote about switching fabrics.\n")
        (self.vault / "wiki/concepts/A.md").write_text(
            (self.vault / "wiki/concepts/A.md").read_text(encoding="utf-8").replace("and [[C]].", "and [[C]] and [[F]] and [[G]]."),
            encoding="utf-8")
        self.write("wiki/sources/@2020__X__S1.md", page("source", "S1", "c-000006", "Source one body."))
        self.write("wiki/sources/@2021__Y__S2.md", page("source", "S2", "c-000007", "Source two body."))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_ok(self, name, *args):
        res = run_script(name, list(args), self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res

    def graph(self):
        return json.loads((self.vault / ".vault-meta/graph.json").read_text(encoding="utf-8"))


class WikiGraphTest(GraphFixture):
    def test_build_links_reciprocal_bonus_and_cocitation(self):
        out = json.loads(self.run_ok("wiki-graph.py", "build").stdout)
        self.assertEqual(out["nodes"], 9)
        g = self.graph()
        adj = g["adj"]
        a, b, c, d = "wiki/concepts/A.md", "wiki/concepts/B.md", "wiki/concepts/C.md", "wiki/entities/D.md"
        # A ⇄ B: link 1.0 + 相互 0.5 + 共通出典 S1(引く頁 2 → 1/log2(4) = 0.5)
        self.assertAlmostEqual(adj[a][b], 2.0, places=6)
        self.assertAlmostEqual(adj[b][a], adj[a][b])
        # A → C: 片方向 link のみ
        self.assertAlmostEqual(adj[a][c], 1.0, places=6)
        # C – D: 共通出典 S2 のみ
        self.assertAlmostEqual(adj[c][d], 0.5, places=6)
        # frontmatter sources も link 辺になる(A → S1)。source 同士に共通出典の辺は無い。E は孤立
        self.assertAlmostEqual(adj[a]["wiki/sources/@2020__X__S1.md"], 1.0, places=6)
        self.assertNotIn("wiki/sources/@2021__Y__S2.md", adj["wiki/sources/@2020__X__S1.md"])
        self.assertEqual(adj["wiki/concepts/E.md"], {})
        self.assertEqual(g["stats"]["isolated"], 1)
        self.assertEqual(g["nodes"][a]["address"], "c-000001")
        self.assertEqual(g["nodes"][d]["type"], "entity")
        self.assertEqual(g["nodes"]["wiki/entities/G.md"]["subtype"], "person")
        self.assertIsNone(g["nodes"][a]["subtype"])

    def test_neighbors_ranks_by_damped_weight_and_hops(self):
        self.run_ok("wiki-graph.py", "build")
        out = json.loads(self.run_ok("wiki-graph.py", "neighbors", "A", "--top", "5").stdout)
        names = [n["name"] for n in out["neighbors"]]
        self.assertEqual(names[0], "B")
        self.assertIn("C", names)
        self.assertNotIn("D", names)  # 1 hop では届かない
        out2 = json.loads(self.run_ok("wiki-graph.py", "neighbors", "wiki/concepts/A.md", "--hops", "2").stdout)
        self.assertIn("D", [n["name"] for n in out2["neighbors"]])
        res = run_script("wiki-graph.py", ["neighbors", "Nope"], self.vault)
        self.assertEqual(res.returncode, 3)

    def test_stats_requires_graph(self):
        res = run_script("wiki-graph.py", ["stats"], self.vault)
        self.assertEqual(res.returncode, 3)
        self.run_ok("wiki-graph.py", "build")
        self.assertEqual(json.loads(self.run_ok("wiki-graph.py", "stats").stdout)["nodes"], 9)


class RetrieveGraphChannelTest(GraphFixture):
    def setUp(self):
        super().setUp()
        self.run_ok("contextual-prefix.py", "--all", "--no-llm")
        self.run_ok("bm25-index.py", "build")

    def retrieve(self, *args):
        res = self.run_ok("retrieve.py", "RDMA verbs queue pair", "--no-rerank", "--top", "5", *args)
        return res.stdout

    def test_absent_and_empty_graph_leave_output_byte_identical(self):
        baseline = self.retrieve()
        self.assertNotIn("graph", baseline)
        # 空グラフ(辺なし)
        (self.vault / ".vault-meta/graph.json").write_text(
            json.dumps({"version": 1, "nodes": {}, "adj": {}, "stats": {}}), encoding="utf-8")
        self.assertEqual(self.retrieve(), baseline)
        # 実グラフでも --no-graph なら同一
        self.run_ok("wiki-graph.py", "build")
        self.assertEqual(self.retrieve("--no-graph"), baseline)

    def test_graph_adds_lexically_distant_neighbour_and_boosts_overlap(self):
        baseline = json.loads(self.retrieve())
        base_pages = [c["page_path"] for c in baseline["candidates"]]
        self.assertIn("wiki/concepts/A.md", base_pages)
        self.assertNotIn("wiki/concepts/C.md", base_pages)  # 語彙が合わないので BM25 では出ない

        self.run_ok("wiki-graph.py", "build")
        out = json.loads(self.retrieve("--explain"))
        pages = [c["page_path"] for c in out["candidates"]]
        self.assertIn("wiki/concepts/C.md", pages)
        c = next(c for c in out["candidates"] if c["page_path"] == "wiki/concepts/C.md")
        self.assertEqual(c["channels"], ["graph"])
        self.assertEqual(c["bm25_score"], 0.0)
        self.assertGreater(c["graph_score"], 0)
        self.assertTrue(c["absolute_path"].endswith("wiki/concepts/C.md"))
        self.assertTrue(out["strategy"].endswith("+graph"))
        self.assertTrue(out["explain"]["graph"]["active"])
        self.assertIn("wiki/concepts/C.md", out["explain"]["graph"]["added"])
        # address 無しの F も合成 address 経由で chunk を見つけて加わる
        self.assertIn("wiki/concepts/F.md", pages)
        # 著者 G は既定では入らず、--graph-people で入る
        self.assertNotIn("wiki/entities/G.md", pages)
        people = json.loads(self.retrieve("--graph-people", "--graph-top", "8"))
        self.assertIn("wiki/entities/G.md", [c["page_path"] for c in people["candidates"]])
        # 孤立ページ E はグラフでも出ない
        self.assertNotIn("wiki/concepts/E.md", pages)
        # BM25 上位(A)は残る
        self.assertEqual(pages[0], "wiki/concepts/A.md")

    def test_graph_top_zero_adds_nothing_but_can_still_boost(self):
        self.run_ok("wiki-graph.py", "build")
        out = json.loads(self.retrieve("--graph-top", "0", "--explain"))
        self.assertEqual(out["explain"]["graph"]["added"], [])
        self.assertNotIn("wiki/concepts/C.md", [c["page_path"] for c in out["candidates"]])


if __name__ == "__main__":
    unittest.main()
