#!/usr/bin/env python3
"""wiki-related.py の試験。一時 vault は実 wiki に触れない。固定種は実グラフがあるときだけ。"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REAL_VAULT = SCRIPTS.parent
GRAPH_REL = ".vault-meta/graph.json"
CLUSTERS_REL = ".vault-meta/clusters.json"

SEED1 = "wiki/concepts/根本原因分析.md"
SEED2 = "wiki/concepts/異常検知.md"
SEED3 = "wiki/concepts/KVキャッシュ管理.md"
SEED4 = "wiki/concepts/PagedAttention.md"
SEED5 = "wiki/concepts/スキーマ発展.md"
SEED6 = "wiki/concepts/バイナリエンコーディング.md"
SEED7 = "wiki/concepts/ストレージQoS.md"
SEED8 = "wiki/concepts/ブロックレベル差分同期.md"
SEED9 = "wiki/concepts/SRE.md"
SEED10 = "wiki/concepts/分散トレーシング.md"
SEED11 = "wiki/sources/@2023__SOSP__Efficient Memory Management for Large Language Model Serving with PagedAttention.md"
SEED12 = "wiki/sources/@2025__arXiv__LMCache - An Efficient KV Cache Layer for Enterprise-Scale LLM Inference.md"
SEED13 = "wiki/questions/KVキャッシュ転送コストは再計算に比べて無視できるか.md"
SEED14 = "wiki/questions/agentic時代のSLI-SLO運用.md"
SEED15 = "wiki/surveys/KVキャッシュ管理の教科書.md"
SEED16 = "wiki/surveys/ポストモーテムの教科書.md"
SEED17 = "wiki/asks/@2026__MLSys2026__Beyond the Buzz - A Pragmatic Exploration of Prefill-Decode Disaggregation in Large Scale Inference.md"
SEED18 = "wiki/briefs/@2026__MLSys2026__Beyond the Buzz - A Pragmatic Exploration of Prefill-Decode Disaggregation in Large Scale Inference.md"
SEED19 = "wiki/entities/3GPP.md"
SEED20 = "wiki/entities/OpenTelemetry.md"
CANARY = "wiki/sources/@2018__acmqueue__Canary Analysis Service.md"


def import_mod(vault):
    os.environ["WIKI_VAULT_ROOT"] = str(vault)
    spec = importlib.util.spec_from_file_location("wiki_related", SCRIPTS / "wiki-related.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_script(args, vault):
    env = os.environ.copy()
    env["WIKI_VAULT_ROOT"] = str(vault)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "wiki-related.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault),
        check=False,
    )


def write_page(vault, rel, typ, title, body="", related=(), sources=(), status=None):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"type: {typ}", f'title: "{title}"']
    if status:
        fm.append(f"status: {status}")
    if related:
        fm.append("related:")
        fm += [f'  - "[[{r}]]"' for r in related]
    if sources:
        fm.append("sources:")
        fm += [f'  - "[[{s}]]"' for s in sources]
    fm.append("---")
    path.write_text("\n".join(fm) + f"\n\n# {title}\n\n{body}\n", encoding="utf-8")


def node(name, typ):
    return {"name": name, "type": typ}


class RelatedUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name).resolve()
        for sub in ("wiki/concepts", "wiki/sources", "wiki/entities",
                    "wiki/questions", "wiki/surveys", "wiki/asks", "wiki/briefs",
                    ".vault-meta"):
            (self.vault / sub).mkdir(parents=True)
        self.mod = import_mod(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def related(self, seed, graph, clusters=None, records=None):
        kwargs = {
            "seed": seed,
            "graph": graph,
            "clusters": clusters or {},
            "root": self.vault,
        }
        if records is not None:
            kwargs["records"] = records
        return self.mod.related_for(**kwargs)

    def paths(self, payload, role):
        return [c["page_path"] for c in payload["roles"][role]]

    def omit(self, payload, role):
        return [o["reason"] for o in payload["omitted"] if o["role"] == role]

    def test_usage_and_no_query_flag(self):
        res = run_script(["--help"], self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("--query", res.stdout)
        res = run_script([], self.vault)
        self.assertEqual(res.returncode, 2)

    def test_missing_graph_node_is_exit_3(self):
        write_page(self.vault, "wiki/asks/Foo.md", "ask", "Foo")
        graph = {"built_at": "2026-09-13T00:00:00", "nodes": {}, "adj": {}}
        code, payload = self.related("wiki/asks/Foo.md", graph)
        self.assertEqual(code, 3)
        self.assertEqual(payload["roles"], {})
        self.assertEqual(payload["omitted"][0]["reason"], "not-a-graph-node")
        res = run_script(["wiki/asks/Foo.md"], self.vault)
        self.assertEqual(res.returncode, 3)
        out = json.loads(res.stdout)
        self.assertEqual(out["omitted"][0]["reason"], "not-a-graph-node")

    def test_does_not_normalize_stem_to_source(self):
        write_page(self.vault, "wiki/sources/@X.md", "source", "X")
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {"wiki/sources/@X.md": node("@X", "source")},
            "adj": {"wiki/sources/@X.md": {}},
        }
        code, payload = self.related("wiki/asks/@X.md", graph)
        self.assertEqual(code, 3)
        self.assertEqual(payload["omitted"][0]["reason"], "not-a-graph-node")

    def test_definition_related_order_and_cluster_refill(self):
        write_page(self.vault, "wiki/concepts/Seed.md", "concept", "SeedPage",
                   related=["Parent", "Hub", "Peer", "Leaf"])
        for name in ("Parent", "Hub", "Peer", "Leaf"):
            write_page(self.vault, f"wiki/concepts/{name}.md", "concept", name)
        write_page(self.vault, "wiki/sources/@S.md", "source", "S")
        seed = "wiki/concepts/Seed.md"
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {
                seed: node("Seed", "concept"),
                "wiki/concepts/Parent.md": node("Parent", "concept"),
                "wiki/concepts/Hub.md": node("Hub", "concept"),
                "wiki/concepts/Peer.md": node("Peer", "concept"),
                "wiki/concepts/Leaf.md": node("Leaf", "concept"),
                "wiki/sources/@S.md": node("@S", "source"),
            },
            "adj": {
                seed: {
                    "wiki/concepts/Parent.md": 1.0,
                    "wiki/concepts/Hub.md": 1.0,
                    "wiki/concepts/Peer.md": 1.0,
                    "wiki/concepts/Leaf.md": 1.0,
                    "wiki/sources/@S.md": 1.0,
                },
                "wiki/concepts/Parent.md": {seed: 1.0},
                "wiki/concepts/Hub.md": {seed: 1.0},
                "wiki/concepts/Peer.md": {seed: 1.0},
                "wiki/concepts/Leaf.md": {seed: 1.0},
                "wiki/sources/@S.md": {seed: 1.0},
            },
        }
        clusters = {"membership": {
            seed: {"id": 1, "in_count": 1},
            "wiki/concepts/Parent.md": {"id": 1, "in_count": 50},
            "wiki/concepts/Hub.md": {"id": 1, "in_count": 40},
            "wiki/concepts/Peer.md": {"id": 1, "in_count": 30},
            "wiki/concepts/Leaf.md": {"id": 1, "in_count": 5},
        }}
        code, payload = self.related(seed, graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "definition"),
                         ["wiki/concepts/Parent.md", "wiki/concepts/Hub.md"])
        self.assertEqual(self.paths(payload, "cluster"),
                         ["wiki/concepts/Peer.md", "wiki/concepts/Leaf.md"])
        self.assertEqual(self.paths(payload, "evidence"), ["wiki/sources/@S.md"])
        self.assertIn("no-callout", self.omit(payload, "contradiction"))
        self.assertNotIn("lexical", payload["roles"])
        self.assertNotIn("open_question", payload["roles"])

    def test_two_hop_definition_and_empty_cluster_residual(self):
        write_page(self.vault, "wiki/concepts/Near.md", "concept", "Near",
                   related=["Far"])
        write_page(self.vault, "wiki/concepts/Far.md", "concept", "Far")
        write_page(self.vault, "wiki/concepts/Mid.md", "entity", "Mid")
        seed = "wiki/concepts/Near.md"
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {
                seed: node("Near", "concept"),
                "wiki/concepts/Far.md": node("Far", "concept"),
                "wiki/entities/Mid.md": node("Mid", "entity"),
            },
            "adj": {
                seed: {"wiki/concepts/Far.md": 1.5, "wiki/entities/Mid.md": 1.0},
                "wiki/concepts/Far.md": {seed: 1.5, "wiki/concepts/Hop.md": 1.0},
                "wiki/concepts/Hop.md": {"wiki/concepts/Far.md": 1.0},
                "wiki/entities/Mid.md": {seed: 1.0},
            },
        }
        graph["nodes"]["wiki/concepts/Hop.md"] = node("Hop", "concept")
        write_page(self.vault, "wiki/concepts/Hop.md", "concept", "Hop")
        clusters = {"membership": {
            seed: {"id": 3, "in_count": 1},
            "wiki/concepts/Far.md": {"id": 3, "in_count": 10},
            "wiki/concepts/Hop.md": {"id": 3, "in_count": 8},
        }}
        code, payload = self.related(seed, graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "definition"),
                         ["wiki/concepts/Far.md", "wiki/concepts/Hop.md"])
        hop = payload["roles"]["definition"][1]
        self.assertEqual(len(hop["via"]), 2)
        self.assertEqual(hop["via"][0]["to"], "wiki/concepts/Far.md")
        self.assertEqual(hop["via"][1]["to"], "wiki/concepts/Hop.md")
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("no-nearby-cluster-peer", self.omit(payload, "cluster"))

    def _hub_graph(self, hub_titles):
        write_page(self.vault, "wiki/concepts/Leaf.md", "concept", "Leaf")
        write_page(self.vault, "wiki/concepts/Def1.md", "concept", "Def1")
        write_page(self.vault, "wiki/concepts/Def2.md", "concept", "Def2")
        write_page(self.vault, "wiki/entities/Mid.md", "entity", "Mid")
        hubs = []
        for stem, title in hub_titles:
            rel = f"wiki/concepts/{stem}.md"
            write_page(self.vault, rel, "concept", title)
            hubs.append(rel)
        seed = "wiki/concepts/Leaf.md"
        mid = "wiki/entities/Mid.md"
        d1, d2 = "wiki/concepts/Def1.md", "wiki/concepts/Def2.md"
        nodes = {
            seed: node("Leaf", "concept"),
            d1: node("Def1", "concept"),
            d2: node("Def2", "concept"),
            mid: node("Mid", "entity"),
        }
        adj = {
            seed: {d1: 1.0, d2: 1.0, mid: 1.0},
            d1: {seed: 1.0},
            d2: {seed: 1.0},
            mid: {seed: 1.0},
        }
        for rel, (stem, title) in zip(hubs, hub_titles):
            nodes[rel] = node(stem, "concept")
            adj[rel] = {mid: 1.0}
            adj[mid][rel] = 1.0
        graph = {"built_at": "2026-09-13T00:00:00", "nodes": nodes, "adj": adj}
        return seed, graph, hubs

    def test_single_distant_label_is_kept(self):
        seed, graph, hubs = self._hub_graph([("HubA", "HubA")])
        clusters = {
            "clusters": [{"id": 7, "label": "HubA / HubB"}],
            "membership": {
                seed: {"id": 7, "in_count": 1},
                hubs[0]: {"id": 7, "in_count": 80},
            },
        }
        code, payload = self.related(seed, graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "definition"), [
            "wiki/concepts/Def1.md", "wiki/concepts/Def2.md",
        ])
        self.assertEqual(self.paths(payload, "cluster"), hubs)

    def test_survey_and_seed_entity_omit_cluster_without_lookup(self):
        write_page(self.vault, "wiki/surveys/Book.md", "survey", "Book")
        write_page(self.vault, "wiki/entities/Org.md", "entity", "Org", status="seed")
        write_page(self.vault, "wiki/concepts/C.md", "concept", "C")
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {
                "wiki/surveys/Book.md": node("Book", "survey"),
                "wiki/entities/Org.md": node("Org", "entity"),
                "wiki/concepts/C.md": node("C", "concept"),
            },
            "adj": {
                "wiki/surveys/Book.md": {"wiki/concepts/C.md": 1.0},
                "wiki/entities/Org.md": {"wiki/concepts/C.md": 1.0},
                "wiki/concepts/C.md": {
                    "wiki/surveys/Book.md": 1.0,
                    "wiki/entities/Org.md": 1.0,
                },
            },
        }
        clusters = {"membership": {"wiki/concepts/C.md": {"id": 1, "in_count": 1}}}
        code, payload = self.related("wiki/surveys/Book.md", graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("not-on-backbone", self.omit(payload, "cluster"))
        code, payload = self.related("wiki/entities/Org.md", graph, clusters)
        self.assertEqual(code, 0)
        self.assertIn("seed", self.omit(payload, "cluster"))

    def test_short_title_skips_name_match(self):
        write_page(self.vault, "wiki/concepts/SRE.md", "concept", "SRE",
                   sources=["@2018__acmqueue__Canary Analysis Service",
                            "@2016__OReilly__SRE Book - Chapter 1 Introduction"])
        write_page(self.vault, "wiki/sources/@2018__acmqueue__Canary Analysis Service.md",
                   "source", "Canary Analysis Service")
        write_page(self.vault, "wiki/sources/@2016__OReilly__SRE Book - Chapter 1 Introduction.md",
                   "source", "Site Reliability Engineering - Chapter 1: Introduction")
        seed = "wiki/concepts/SRE.md"
        canary = "wiki/sources/@2018__acmqueue__Canary Analysis Service.md"
        book = "wiki/sources/@2016__OReilly__SRE Book - Chapter 1 Introduction.md"
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {
                seed: node("SRE", "concept"),
                canary: node("@2018__acmqueue__Canary Analysis Service", "source"),
                book: node("@2016__OReilly__SRE Book - Chapter 1 Introduction", "source"),
            },
            "adj": {seed: {canary: 1.0, book: 1.0}, canary: {seed: 1.0}, book: {seed: 1.0}},
        }
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "evidence")[0], canary)

    def test_distant_label_dump_is_omitted(self):
        seed, graph, hubs = self._hub_graph([("HubA", "HubA"), ("HubB", "HubB")])
        clusters = {
            "clusters": [{"id": 7, "label": "HubA / HubB"}],
            "membership": {
                seed: {"id": 7, "in_count": 1},
                hubs[0]: {"id": 7, "in_count": 80},
                hubs[1]: {"id": 7, "in_count": 70},
            },
        }
        code, payload = self.related(seed, graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "definition"), [
            "wiki/concepts/Def1.md", "wiki/concepts/Def2.md",
        ])
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("no-nearby-cluster-peer", self.omit(payload, "cluster"))

    def test_distant_label_head_skips_to_next_when_residual_gt_2(self):
        seed, graph, hubs = self._hub_graph([
            ("HubA", "HubA"), ("HubB", "HubB"), ("Peer", "Peer"),
        ])
        clusters = {
            "clusters": [{"id": 7, "label": "HubA / HubB"}],
            "membership": {
                seed: {"id": 7, "in_count": 1},
                hubs[0]: {"id": 7, "in_count": 80},
                hubs[1]: {"id": 7, "in_count": 70},
                hubs[2]: {"id": 7, "in_count": 5},
            },
        }
        code, payload = self.related(seed, graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "cluster"), [hubs[2]])
        self.assertNotIn("no-nearby-cluster-peer", self.omit(payload, "cluster"))

    def test_contradiction_host_and_no_query_leak(self):
        write_page(self.vault, "wiki/concepts/Host.md", "concept", "Host")
        write_page(self.vault, "wiki/concepts/Peer.md", "concept", "Peer")
        write_page(self.vault, "wiki/concepts/DevOps.md", "concept", "DevOps")
        seed = "wiki/concepts/Host.md"
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {
                seed: node("Host", "concept"),
                "wiki/concepts/Peer.md": node("Peer", "concept"),
                "wiki/concepts/DevOps.md": node("DevOps", "concept"),
            },
            "adj": {seed: {"wiki/concepts/Peer.md": 1.0}, "wiki/concepts/Peer.md": {seed: 1.0}},
        }
        records = [
            {"host": seed, "pages": ["Peer"], "status": "open"},
            {"host": "wiki/concepts/DevOps.md", "pages": [], "status": "open",
             "title": "Host の誕生年", "body": "Host という語が本文にある"},
        ]
        code, payload = self.related(seed, graph, records=records)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])
        self.assertNotIn("wiki/concepts/DevOps.md", self.paths(payload, "contradiction"))

    def test_no_opponent_reason(self):
        write_page(self.vault, "wiki/concepts/Host.md", "concept", "Host")
        seed = "wiki/concepts/Host.md"
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {seed: node("Host", "concept")},
            "adj": {seed: {}},
        }
        records = [{"host": seed, "pages": ["Ghost"], "status": "open"}]
        code, payload = self.related(seed, graph, records=records)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), [])
        self.assertIn("no-opponent", self.omit(payload, "contradiction"))
        self.assertNotIn("no-callout", self.omit(payload, "contradiction"))

    def test_explicit_empty_records_skips_collect(self):
        write_page(self.vault, "wiki/concepts/Host.md", "concept", "Host")
        write_page(self.vault, "wiki/concepts/Peer.md", "concept", "Peer",
                   body="> [!contradiction] x\n> [[Host]]\n")
        seed = "wiki/concepts/Host.md"
        graph = {
            "built_at": "2026-09-13T00:00:00",
            "nodes": {
                seed: node("Host", "concept"),
                "wiki/concepts/Peer.md": node("Peer", "concept"),
            },
            "adj": {seed: {}, "wiki/concepts/Peer.md": {}},
        }
        code, payload = self.related(seed, graph, records=[])
        self.assertEqual(code, 0)
        self.assertIn("no-callout", self.omit(payload, "contradiction"))
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

    def test_sidecar_freshness(self):
        write_page(self.vault, "wiki/concepts/Host.md", "concept", "Host")
        write_page(self.vault, "wiki/concepts/Peer.md", "concept", "Peer",
                   body="> [!contradiction] x\n> [[Host]]\n")
        seed = "wiki/concepts/Host.md"
        graph = {
            "built_at": "2026-09-13T18:00:00",
            "nodes": {
                seed: node("Host", "concept"),
                "wiki/concepts/Peer.md": node("Peer", "concept"),
            },
            "adj": {seed: {}, "wiki/concepts/Peer.md": {}},
        }
        sidecar = self.vault / ".vault-meta" / "contradictions.json"

        sidecar.write_text(json.dumps({
            "generated_at": "2026-09-13T19:00:00",
            "records": [],
        }), encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertIn("no-callout", self.omit(payload, "contradiction"))

        sidecar.write_text(json.dumps({
            "generated_at": "2026-09-13T19:00:00",
            "records": [{"host": seed, "pages": ["Peer"], "status": "open"}],
        }), encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text("{", encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text(json.dumps({
            "generated_at": "2026-09-10T00:00:00",
            "records": [],
        }), encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text("[]", encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text('"x"', encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text(json.dumps({
            "generated_at": "2099-01-01T00:00:00",
            "records": [1],
        }), encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text(json.dumps({
            "generated_at": "2099-01-01T00:00:00",
            "records": [{"host": seed, "pages": ["Peer"], "status": "open"}, "x"],
        }), encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.write_text(json.dumps({
            "generated_at": 2099,
            "records": [{"host": seed, "pages": ["Peer"], "status": "open"}],
        }), encoding="utf-8")
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

        sidecar.unlink()
        code, payload = self.related(seed, graph)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), ["wiki/concepts/Peer.md"])

    def test_collect_uses_root_not_import_vault(self):
        real = REAL_VAULT
        if not (real / GRAPH_REL).is_file():
            self.skipTest("実グラフが無い")
        seed = "wiki/concepts/根本原因分析.md"
        graph = json.loads((real / GRAPH_REL).read_text(encoding="utf-8"))
        if seed not in graph.get("nodes", {}):
            self.skipTest("種が無い")
        code, payload = self.mod.related_for(seed, graph=graph, root=real)
        self.assertEqual(code, 0)
        self.assertNotIn("no-callout", self.omit(payload, "contradiction"))

        isolated = "wiki/concepts/OnlyHere.md"
        write_page(self.vault, isolated, "concept", "OnlyHere")
        tiny = {
            "built_at": "2099-01-01T00:00:00",
            "nodes": {isolated: node("OnlyHere", "concept")},
            "adj": {isolated: {}},
        }
        code, payload = self.mod.related_for(isolated, graph=tiny, root=self.vault)
        self.assertEqual(code, 0)
        self.assertIn("no-callout", self.omit(payload, "contradiction"))

    def test_distant_label_uses_page_title_not_stem(self):
        seed, graph, hubs = self._hub_graph([("HubA", "表示A"), ("HubB", "表示B")])
        clusters = {
            "clusters": [{"id": 7, "label": "表示A / 表示B"}],
            "membership": {
                seed: {"id": 7, "in_count": 1},
                hubs[0]: {"id": 7, "in_count": 80},
                hubs[1]: {"id": 7, "in_count": 70},
            },
        }
        code, payload = self.related(seed, graph, clusters)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("no-nearby-cluster-peer", self.omit(payload, "cluster"))


class RelatedCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name).resolve()
        for sub in ("wiki/concepts", "wiki/sources", "wiki/entities",
                    "wiki/questions", "wiki/surveys", ".vault-meta"):
            (self.vault / sub).mkdir(parents=True)
        self.mod = import_mod(self.vault)
        self.cache = self.mod.cache_mod

    def tearDown(self):
        self.tmp.cleanup()

    def dump(self, seed, address="c-000042", built="2026-09-13T18:00:00", topo="sha-a"):
        write_page(self.vault, seed, "concept", "Seed")
        write_page(self.vault, "wiki/concepts/Peer.md", "concept", "Peer")
        graph = {
            "built_at": built,
            "nodes": {
                seed: {"name": "Seed", "type": "concept", "address": address},
                "wiki/concepts/Peer.md": {"name": "Peer", "type": "concept", "address": "c-000043"},
            },
            "adj": {
                seed: {"wiki/concepts/Peer.md": 1.0},
                "wiki/concepts/Peer.md": {seed: 1.0},
            },
        }
        clusters = {
            "topology_sha256": topo,
            "clusters": [],
            "membership": {},
        }
        (self.vault / GRAPH_REL).write_text(json.dumps(graph), encoding="utf-8")
        (self.vault / CLUSTERS_REL).write_text(json.dumps(clusters), encoding="utf-8")
        return graph, clusters

    def test_injected_graph_does_not_write_cache(self):
        seed = "wiki/concepts/Seed.md"
        graph, _ = self.dump(seed)
        code, _ = self.mod.related_for(seed, graph=graph, clusters={}, records=[], root=self.vault)
        self.assertEqual(code, 0)
        self.assertFalse((self.vault / ".vault-meta" / "related").exists())

    def test_disk_path_writes_address_key_and_hits(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed, address="c-000042")
        code, first = self.mod.related_for(seed, root=self.vault)
        self.assertEqual(code, 0)
        path = self.vault / ".vault-meta" / "related" / "c-000042.json"
        self.assertTrue(path.is_file())
        env = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(env["seed"], seed)
        self.assertEqual(env["topology_sha256"], "sha-a")
        env["roles"]["definition"] = []
        path.write_text(json.dumps(env), encoding="utf-8")
        code, second = self.mod.related_for(seed, root=self.vault)
        self.assertEqual(code, 0)
        self.assertEqual(second["roles"]["definition"], [])
        self.assertEqual(set(second), {"seed", "roles", "omitted"})

    def test_missing_address_uses_synthetic_key(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed, address=None)
        code, _ = self.mod.related_for(seed, root=self.vault)
        self.assertEqual(code, 0)
        key = self.cache.synthetic_address(seed)
        self.assertTrue(key.startswith("syn-"))
        self.assertTrue((self.vault / ".vault-meta" / "related" / f"{key}.json").is_file())

    def test_stale_topology_wipes_directory(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed)
        self.mod.related_for(seed, root=self.vault)
        other = self.vault / ".vault-meta" / "related" / "c-000099.json"
        other.write_text(json.dumps({
            "topology_sha256": "sha-a",
            "graph_built_at": "2026-09-13T18:00:00",
            "seed": "wiki/concepts/Other.md",
            "roles": {},
            "omitted": [],
        }), encoding="utf-8")
        clusters = json.loads((self.vault / CLUSTERS_REL).read_text(encoding="utf-8"))
        clusters["topology_sha256"] = "sha-b"
        (self.vault / CLUSTERS_REL).write_text(json.dumps(clusters), encoding="utf-8")
        self.mod.related_for(seed, root=self.vault)
        self.assertFalse(other.is_file())
        self.assertTrue((self.vault / ".vault-meta" / "related" / "c-000042.json").is_file())

    def test_stale_graph_built_at_wipes(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed)
        self.mod.related_for(seed, root=self.vault)
        graph = json.loads((self.vault / GRAPH_REL).read_text(encoding="utf-8"))
        graph["built_at"] = "2026-09-14T00:00:00"
        (self.vault / GRAPH_REL).write_text(json.dumps(graph), encoding="utf-8")
        folder = self.vault / ".vault-meta" / "related"
        self.assertTrue(any(folder.iterdir()))
        self.mod.related_for(seed, root=self.vault)
        env = json.loads((folder / "c-000042.json").read_text(encoding="utf-8"))
        self.assertEqual(env["graph_built_at"], "2026-09-14T00:00:00")

    def test_seed_mismatch_does_not_serve_or_overwrite(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed)
        path = self.vault / ".vault-meta" / "related" / "c-000042.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "topology_sha256": "sha-a",
            "graph_built_at": "2026-09-13T18:00:00",
            "seed": "wiki/concepts/Other.md",
            "roles": {"definition": [{"page_path": "wiki/concepts/Stolen.md"}]},
            "omitted": [],
        }), encoding="utf-8")
        code, payload = self.mod.related_for(seed, root=self.vault)
        self.assertEqual(code, 0)
        self.assertNotEqual(
            [c.get("page_path") for c in payload["roles"].get("definition", [])],
            ["wiki/concepts/Stolen.md"],
        )
        env = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(env["seed"], "wiki/concepts/Other.md")

    def test_syn_frontmatter_address_is_not_a_key(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed, address="syn-000000")
        self.mod.related_for(seed, root=self.vault)
        self.assertFalse((self.vault / ".vault-meta" / "related" / "syn-000000.json").exists())
        key = self.cache.synthetic_address(seed)
        self.assertTrue((self.vault / ".vault-meta" / "related" / f"{key}.json").is_file())

    def test_escaped_page_path_is_dropped_on_read(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed)
        path = self.vault / ".vault-meta" / "related" / "c-000042.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "topology_sha256": "sha-a",
            "graph_built_at": "2026-09-13T18:00:00",
            "generation": 0,
            "seed": seed,
            "roles": {
                "definition": [{"page_path": "../../etc/passwd", "via": [{}], "why": ""}],
            },
            "omitted": [],
        }), encoding="utf-8")
        code, payload = self.mod.related_for(seed, root=self.vault)
        self.assertEqual(code, 0)
        self.assertEqual(payload["roles"]["definition"], [])
        for cand in payload["roles"].get("definition", []):
            self.assertFalse(str(cand.get("absolute_path", "")).endswith("passwd"))

    def test_use_cache_true_does_not_write_injected_graph(self):
        seed = "wiki/concepts/Seed.md"
        graph, _ = self.dump(seed)
        self.mod.related_for(
            seed, graph=graph, clusters={}, records=[], root=self.vault, use_cache=True,
        )
        self.assertFalse((self.vault / ".vault-meta" / "related").exists())

    def test_unsafe_address_falls_back_to_synthetic(self):
        seed = "wiki/concepts/Seed.md"
        self.dump(seed, address="../escape")
        self.mod.related_for(seed, root=self.vault)
        self.assertFalse((self.vault / ".vault-meta" / "related" / "../escape.json").exists())
        key = self.cache.synthetic_address(seed)
        self.assertTrue((self.vault / ".vault-meta" / "related" / f"{key}.json").is_file())

    def test_wipe_wins_over_stale_generation(self):
        seed = "wiki/concepts/Seed.md"
        graph, clusters = self.dump(seed)
        gen = self.cache.read_generation(self.vault)
        self.cache.wipe_related_cache(self.vault)
        wrote = self.cache.write_related_cache(
            self.vault, "c-000042", seed, self.cache.cache_epoch(graph, clusters),
            {"seed": seed, "roles": {}, "omitted": []}, gen,
        )
        self.assertFalse(wrote)
        self.assertFalse((self.vault / ".vault-meta" / "related" / "c-000042.json").is_file())

    def test_exit_3_does_not_write_cache(self):
        self.dump("wiki/concepts/Seed.md")
        code, _ = self.mod.related_for("wiki/asks/Missing.md", root=self.vault)
        self.assertEqual(code, 3)
        folder = self.vault / ".vault-meta" / "related"
        self.assertFalse(folder.exists() or (folder.is_dir() and any(folder.iterdir())))

    def test_refresh_wipe_conditions(self):
        spec = importlib.util.spec_from_file_location(
            "wiki_retrieve_refresh", SCRIPTS / "wiki-retrieve-refresh.py",
        )
        refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(refresh)
        self.assertTrue(refresh.should_wipe_related(True, False))
        self.assertTrue(refresh.should_wipe_related(False, True))
        self.assertTrue(refresh.should_wipe_related(None, True))
        self.assertFalse(refresh.should_wipe_related(False, False))
        self.assertFalse(refresh.should_wipe_related(None, False))
        self.assertFalse(refresh.should_wipe_related(None, None))
        marker = self.vault / ".vault-meta" / "related" / "c-000042.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="utf-8")
        refresh.wipe_related_after_rebuild(self.vault)
        self.assertFalse(marker.is_file())

    def test_claude_md_forbids_reading_related_dir(self):
        text = (REAL_VAULT / "templates/wiki-CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(".vault-meta/related/", text)
        self.assertIn("Read するな", text)


def live_ready():
    return (REAL_VAULT / GRAPH_REL).is_file() and (REAL_VAULT / CLUSTERS_REL).is_file()


@unittest.skipUnless(live_ready(), "実グラフが無い")
class RelatedLiveSeeds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["WIKI_VAULT_ROOT"] = str(REAL_VAULT)
        cls.mod = import_mod(REAL_VAULT)
        cls.graph = cls.mod.graph_mod.load_graph(REAL_VAULT / GRAPH_REL)
        cls.clusters = cls.mod.clusters_mod.load_clusters(REAL_VAULT / CLUSTERS_REL) or {}
        cls.records = cls.mod.load_contradiction_records(cls.graph, REAL_VAULT)

    def go(self, seed):
        return self.mod.related_for(
            seed, graph=self.graph, clusters=self.clusters,
            records=self.records, root=REAL_VAULT,
        )

    def paths(self, payload, role):
        return [c["page_path"] for c in payload["roles"].get(role, [])]

    def omit(self, payload, role):
        return [o["reason"] for o in payload["omitted"] if o["role"] == role]

    def cli(self, seed):
        res = run_script([seed], REAL_VAULT)
        payload = json.loads(res.stdout) if res.stdout.strip() else {}
        return res.returncode, payload

    def test_cli_has_no_query(self):
        res = run_script(["--help"], REAL_VAULT)
        self.assertEqual(res.returncode, 0)
        self.assertNotIn("--query", res.stdout)

    def test_cli_frozen_locks(self):
        code, payload = self.cli(SEED4)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "evidence")[0], SEED11)
        self.assertEqual(self.paths(payload, "definition"), [
            "wiki/concepts/KVキャッシュ管理.md",
            "wiki/concepts/LLM推論.md",
        ])
        self.assertEqual(self.paths(payload, "cluster"), [
            "wiki/concepts/Prefill-Decode分離.md",
            "wiki/concepts/KVキャッシュ量子化.md",
        ])
        self.assertNotIn("lexical", payload["roles"])
        self.assertNotIn("open_question", payload["roles"])

        code, payload = self.cli(SEED5)
        self.assertEqual(code, 0)
        self.assertIn("wiki/concepts/バイナリエンコーディング.md", self.paths(payload, "definition"))
        self.assertNotEqual(self.paths(payload, "cluster"), ["wiki/concepts/分散コンセンサス.md"])
        self.assertNotIn("seed", self.omit(payload, "cluster"))

        code, payload = self.cli(SEED8)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "definition"), [
            "wiki/concepts/ファイルレベル同期.md",
            "wiki/concepts/ライブアップグレード.md",
        ])
        via = payload["roles"]["definition"][1]["via"]
        self.assertEqual(len(via), 2)
        self.assertEqual(via[0]["to"], "wiki/concepts/ファイルレベル同期.md")
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("no-nearby-cluster-peer", self.omit(payload, "cluster"))
        self.assertNotIn("seed", self.omit(payload, "cluster"))

        code, payload = self.cli(SEED9)
        self.assertEqual(code, 0)
        self.assertNotIn("wiki/concepts/DevOps.md", self.paths(payload, "contradiction"))
        self.assertEqual(self.paths(payload, "evidence")[0], CANARY)

        code, payload = self.cli(SEED10)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), [])
        self.assertIn("no-callout", self.omit(payload, "contradiction"))

        for seed in (SEED15, SEED16):
            code, payload = self.cli(seed)
            self.assertEqual(code, 0, seed)
            self.assertEqual(self.paths(payload, "cluster"), [])
            self.assertIn("not-on-backbone", self.omit(payload, "cluster"))

        for seed in (SEED17, SEED18):
            code, payload = self.cli(seed)
            self.assertEqual(code, 3, seed)
            self.assertEqual(payload["roles"], {})
            self.assertEqual(payload["omitted"][0]["reason"], "not-a-graph-node")

        code, payload = self.cli(SEED19)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("seed", self.omit(payload, "cluster"))

        code, payload = self.cli(SEED20)
        self.assertEqual(code, 0)
        self.assertNotIn("seed", self.omit(payload, "cluster"))
        self.assertEqual(self.paths(payload, "cluster"), [
            "wiki/concepts/オブザーバビリティ.md",
            "wiki/concepts/GPU観測性.md",
        ])

    def test_hub_collapse_does_not_empty_large_residual(self):
        code, payload = self.go("wiki/concepts/AIゲートウェイ.md")
        self.assertEqual(code, 0)
        cluster = self.paths(payload, "cluster")
        self.assertTrue(cluster, cluster)
        self.assertIn("wiki/concepts/Prefill-Decode分離.md", cluster)

        code, payload = self.go("wiki/concepts/アーキテクチャスタイル.md")
        self.assertEqual(code, 0)
        self.assertIn(
            "wiki/concepts/モジュール性.md",
            self.paths(payload, "cluster"),
        )

        code, payload = self.go("wiki/concepts/信頼性を持つ大規模ログ収集アーキテクチャ.md")
        self.assertEqual(code, 0)
        self.assertIn(
            "wiki/concepts/分散トレーシング.md",
            self.paths(payload, "cluster"),
        )

    def test_remaining_seeds_exit_0(self):
        for seed in (SEED1, SEED2, SEED3, SEED6, SEED7, SEED11, SEED12, SEED13, SEED14):
            code, payload = self.go(seed)
            self.assertEqual(code, 0, seed)
            self.assertNotIn("lexical", payload["roles"])
            self.assertNotIn("open_question", payload["roles"])

    def test_asks_and_briefs_exit_3(self):
        for seed in (SEED17, SEED18):
            code, payload = self.go(seed)
            self.assertEqual(code, 3, seed)
            self.assertEqual(payload["roles"], {})
            self.assertEqual(payload["omitted"][0]["reason"], "not-a-graph-node")

    def test_surveys_omit_cluster(self):
        for seed in (SEED15, SEED16):
            code, payload = self.go(seed)
            self.assertEqual(code, 0, seed)
            self.assertEqual(self.paths(payload, "cluster"), [])
            self.assertIn("not-on-backbone", self.omit(payload, "cluster"))

    def test_seed_entity_and_otel(self):
        code, payload = self.go(SEED19)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("seed", self.omit(payload, "cluster"))
        code, payload = self.go(SEED20)
        self.assertEqual(code, 0)
        self.assertNotIn("seed", self.omit(payload, "cluster"))
        self.assertEqual(self.paths(payload, "cluster"), [
            "wiki/concepts/オブザーバビリティ.md",
            "wiki/concepts/GPU観測性.md",
        ])

    def test_paged_attention_lock(self):
        code, payload = self.go(SEED4)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "evidence")[0], SEED11)
        self.assertEqual(self.paths(payload, "definition"), [
            "wiki/concepts/KVキャッシュ管理.md",
            "wiki/concepts/LLM推論.md",
        ])
        self.assertEqual(self.paths(payload, "cluster"), [
            "wiki/concepts/Prefill-Decode分離.md",
            "wiki/concepts/KVキャッシュ量子化.md",
        ])
        self.assertNotIn("lexical", payload["roles"])
        self.assertNotIn("open_question", payload["roles"])

    def test_schema_evolution_lock(self):
        code, payload = self.go(SEED5)
        self.assertEqual(code, 0)
        self.assertIn("wiki/concepts/バイナリエンコーディング.md", self.paths(payload, "definition"))
        cluster = self.paths(payload, "cluster")
        self.assertNotEqual(cluster, ["wiki/concepts/分散コンセンサス.md"])
        self.assertNotIn("seed", self.omit(payload, "cluster"))

    def test_block_sync_two_hop(self):
        code, payload = self.go(SEED8)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "definition"), [
            "wiki/concepts/ファイルレベル同期.md",
            "wiki/concepts/ライブアップグレード.md",
        ])
        via = payload["roles"]["definition"][1]["via"]
        self.assertEqual(len(via), 2)
        self.assertEqual(via[0]["to"], "wiki/concepts/ファイルレベル同期.md")
        self.assertEqual(self.paths(payload, "cluster"), [])
        self.assertIn("no-nearby-cluster-peer", self.omit(payload, "cluster"))
        self.assertNotIn("seed", self.omit(payload, "cluster"))

    def test_sre_and_tracing_contradiction(self):
        code, payload = self.go(SEED9)
        self.assertEqual(code, 0)
        self.assertNotIn("wiki/concepts/DevOps.md", self.paths(payload, "contradiction"))
        self.assertEqual(self.paths(payload, "evidence")[0], CANARY)
        code, payload = self.go(SEED10)
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(payload, "contradiction"), [])
        self.assertIn("no-callout", self.omit(payload, "contradiction"))


@unittest.skipUnless(live_ready(), "実グラフが無い")
class RelatedLiveGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["WIKI_VAULT_ROOT"] = str(REAL_VAULT)
        cls.mod = import_mod(REAL_VAULT)
        cls.graph = cls.mod.graph_mod.load_graph(REAL_VAULT / GRAPH_REL)
        cls.clusters = cls.mod.clusters_mod.load_clusters(REAL_VAULT / CLUSTERS_REL) or {}
        cls.records = cls.mod.load_contradiction_records(cls.graph, REAL_VAULT)
        concepts = sorted(
            p.relative_to(REAL_VAULT).as_posix()
            for p in (REAL_VAULT / "wiki/concepts").glob("*.md")
            if not p.name.startswith("_")
        )
        ranked = sorted(
            concepts,
            key=lambda p: hashlib.sha1(f"20260913\n{p}".encode("utf-8")).hexdigest(),
        )
        cls.sample = ranked[:100]

    def go(self, seed):
        return self.mod.related_for(
            seed, graph=self.graph, clusters=self.clusters,
            records=self.records, root=REAL_VAULT,
        )

    def test_sample_gates(self):
        nodes = self.graph["nodes"]
        adj = self.graph.get("adj") or {}
        ev_ok = ev_den = def_ok = def_den = 0
        empty_via = 0
        hub_fail = 0
        hub_den = 0
        title_leak = 0
        clusters_by_id = {c["id"]: c for c in (self.clusters.get("clusters") or [])}
        membership = self.clusters.get("membership") or {}
        for seed in self.sample:
            if seed not in nodes:
                continue
            code, payload = self.go(seed)
            self.assertEqual(code, 0, seed)
            d1 = [p for p in (adj.get(seed) or {}) if (nodes.get(p) or {}).get("type") == "concept"]
            d1s = [p for p in (adj.get(seed) or {}) if (nodes.get(p) or {}).get("type") == "source"]
            text = (REAL_VAULT / seed).read_text(encoding="utf-8") if (REAL_VAULT / seed).is_file() else ""
            fm, _ = self.mod.graph_mod.split_frontmatter(text)
            has_fm_src = bool(self.mod.graph_mod.fm_list_links(fm, "sources"))
            if has_fm_src or d1s:
                ev_den += 1
                if payload["roles"]["evidence"]:
                    ev_ok += 1
            d2_has = False
            for mid in adj.get(seed) or {}:
                for dst in adj.get(mid) or {}:
                    if dst != seed and dst not in (adj.get(seed) or {}) and (
                        nodes.get(dst) or {}
                    ).get("type") == "concept":
                        d2_has = True
                        break
            if d1 or (len(d1) <= 1 and d2_has):
                def_den += 1
                if payload["roles"]["definition"]:
                    def_ok += 1
            for role in payload["roles"].values():
                for cand in role:
                    if not cand.get("via"):
                        empty_via += 1
            cluster = payload["roles"]["cluster"]
            if cluster:
                hub_den += 1
                cid = (membership.get(seed) or {}).get("id")
                label = (clusters_by_id.get(cid) or {}).get("label") or ""
                parts = [p.strip() for p in label.split(" / ") if p.strip()]
                titles = []
                dists = []
                cache = {}
                for cand in cluster:
                    titles.append(self.mod.page_title(
                        cand["page_path"], self.graph, REAL_VAULT, cache,
                    ))
                    hop = (cand.get("via") or [{}])[-1].get("hops")
                    dists.append(hop)
                if (
                    len(cluster) == 2
                    and set(titles) == set(parts)
                    and all(d == 2 for d in dists)
                ):
                    hub_fail += 1
            stem = Path(seed).stem
            for cand in payload["roles"]["contradiction"]:
                page = cand["page_path"]
                ok = False
                for rec in self.records:
                    if rec.get("host") == seed and (
                        Path(page).stem in (rec.get("pages") or []) or page in (rec.get("pages") or [])
                    ):
                        ok = True
                    if rec.get("host") == page and stem in (rec.get("pages") or []):
                        ok = True
                if not ok:
                    title_leak += 1
        self.assertGreaterEqual(ev_ok / ev_den, 0.95, f"evidence {ev_ok}/{ev_den}")
        self.assertGreaterEqual(def_ok / def_den, 0.95, f"definition {def_ok}/{def_den}")
        self.assertEqual(empty_via, 0)
        self.assertEqual(hub_fail, 0, f"hub collapse {hub_fail}/{hub_den}")
        self.assertEqual(title_leak, 0)


if __name__ == "__main__":
    unittest.main()
