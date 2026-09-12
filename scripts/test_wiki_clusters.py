#!/usr/bin/env python3
"""wiki-clusters.py の試験。一時 vault。実 wiki には触れない。"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def import_mod(vault):
    os.environ["WIKI_VAULT_ROOT"] = str(vault)
    spec = importlib.util.spec_from_file_location("wiki_clusters", SCRIPTS / "wiki-clusters.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def page(kind, title, body, status=None, aliases=None, extra_fm=""):
    fm = ["---", f"type: {kind}", f'title: "{title}"']
    if status:
        fm.append(f"status: {status}")
    if aliases:
        fm.append("aliases:")
        fm += [f'  - "{a}"' for a in aliases]
    if extra_fm:
        fm.append(extra_fm.rstrip())
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {title}\n\n{body}\n"


class ClusterFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name).resolve()
        for sub in ("wiki/concepts", "wiki/sources", "wiki/entities", "wiki/questions",
                    "wiki/surveys", ".vault-meta"):
            (self.vault / sub).mkdir(parents=True)
        self.mod = import_mod(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_ok(self, *args):
        res = run_script("wiki-clusters.py", list(args), self.vault)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res

    def clique(self, names, prefix="wiki/concepts"):
        for name in names:
            others = " ".join(f"[[{n}]]" for n in names if n != name)
            self.write(f"{prefix}/{name}.md", page("concept", name, others))

    def test_two_cliques_one_bridge(self):
        self.clique(["A1", "A2", "A3"])
        self.clique(["B1", "B2", "B3"])
        a1 = (self.vault / "wiki/concepts/A1.md")
        a1.write_text(a1.read_text(encoding="utf-8").replace("[[A2]]", "[[A2]] [[B1]]"), encoding="utf-8")
        payload = self.mod.build_payload(self.vault)
        ids = {p.split("/")[-1][:-3]: rec["id"] for p, rec in payload["membership"].items()}
        self.assertEqual(len(set(ids.values())), 2)
        self.assertEqual(len({ids["A1"], ids["A2"], ids["A3"]}), 1)
        self.assertEqual(len({ids["B1"], ids["B2"], ids["B3"]}), 1)
        self.assertNotEqual(ids["A1"], ids["B1"])
        catalog, _, directed = self.mod.scan_vault(self.vault)
        _nodes, edges, _ic, _dr = self.mod.build_backbone(catalog, directed)
        cross = [(a, b) for (a, b), _w in edges.items()
                 if payload["membership"][a]["id"] != payload["membership"][b]["id"]]
        self.assertEqual(len(cross), 1)
        ends = {Path(cross[0][0]).stem, Path(cross[0][1]).stem}
        self.assertEqual(ends, {"A1", "B1"})

    def test_net_delta_q_nonneg_and_matches_recompute(self):
        self.clique(["A1", "A2", "A3"])
        self.clique(["B1", "B2", "B3"])
        self.clique(["C1", "C2", "C3"])
        (self.vault / "wiki/concepts/A1.md").write_text(
            (self.vault / "wiki/concepts/A1.md").read_text(encoding="utf-8").replace("[[A2]]", "[[A2]] [[B1]]"),
            encoding="utf-8")
        (self.vault / "wiki/concepts/B1.md").write_text(
            (self.vault / "wiki/concepts/B1.md").read_text(encoding="utf-8").replace("[[B2]]", "[[B2]] [[C1]]"),
            encoding="utf-8")
        catalog, _, directed = self.mod.scan_vault(self.vault)
        nodes, edges, _ic, _dr = self.mod.build_backbone(catalog, directed)
        trace = {}
        self.mod.run_louvain(nodes, edges, seed=self.mod.LOUVAIN_SEED, trace=trace)
        for net in trace.get("nets", []):
            self.assertGreaterEqual(net, -self.mod.Q_EPS)
        self.assertGreater(trace.get("adopted_moves", 0), 0)
        self.assertGreater(trace.get("stay_c0", 0), 0)
        self.assertGreater(trace["stay_c0"] + trace.get("stay_nonpos", 0), trace["adopted_moves"])
        for q_acc, q_full in trace.get("q_pairs", []):
            self.assertAlmostEqual(q_acc, q_full, places=9)
        if "q_end" in trace:
            self.assertAlmostEqual(trace["q_end"], trace["q_recomputed"], places=9)

    def test_triple_link_weight_three(self):
        self.write("wiki/concepts/X.md", page("concept", "X", "[[Y]] [[Y]] [[Y]]"))
        self.write("wiki/concepts/Y.md", page("concept", "Y", "see X"))
        catalog, _, directed = self.mod.scan_vault(self.vault)
        self.assertEqual(directed[("wiki/concepts/X.md", "wiki/concepts/Y.md")], 3)
        _nodes, edges, in_count, _dr = self.mod.build_backbone(catalog, directed)
        self.assertEqual(edges[("wiki/concepts/X.md", "wiki/concepts/Y.md")], 3)
        self.assertEqual(in_count["wiki/concepts/X.md"], 3)
        self.assertEqual(in_count["wiki/concepts/Y.md"], 3)

    def test_alias_does_not_create_edge(self):
        self.write("wiki/concepts/Real.md", page("concept", "Real", "no links", aliases=["Nick"]))
        self.write("wiki/concepts/Other.md", page("concept", "Other", "see [[Nick]]"))
        catalog, _, directed = self.mod.scan_vault(self.vault)
        self.assertEqual(dict(directed), {})
        _n, edges, _i, dropped = self.mod.build_backbone(catalog, directed)
        self.assertEqual(edges, {})
        self.assertEqual(dropped["wiki/concepts/Other.md"], "isolated")

    def test_surveys_excluded(self):
        self.clique(["A1", "A2", "A3"])
        self.write("wiki/surveys/S.md", page("survey", "S", "[[A1]] [[A2]] [[A3]]"))
        payload = self.mod.build_payload(self.vault)
        self.assertNotIn("wiki/surveys/S.md", payload["catalog"])
        self.assertNotIn("wiki/surveys/S.md", payload["membership"])

    def test_in_count_ignores_seed_inbound(self):
        self.write("wiki/concepts/Hub.md", page("concept", "Hub", "body"))
        self.write("wiki/concepts/Peer.md", page("concept", "Peer", "[[Hub]]"))
        self.write("wiki/entities/Author.md", page("entity", "Author", "[[Hub]] [[Hub]]", status="seed"))
        catalog, _, directed = self.mod.scan_vault(self.vault)
        nodes, edges, in_count, dropped = self.mod.build_backbone(catalog, directed)
        self.assertEqual(dropped["wiki/entities/Author.md"], "seed")
        self.assertEqual(in_count["wiki/concepts/Hub.md"], 1)
        self.assertNotIn("wiki/entities/Author.md", nodes)

    def test_lookup_missing_is_exit_3(self):
        self.clique(["A1", "A2", "A3"])
        self.run_ok("build")
        res = run_script("wiki-clusters.py", ["lookup", "NoSuchPage"], self.vault)
        self.assertEqual(res.returncode, 3)

    def test_lookup_deleted_catalog_path_is_exit_3(self):
        self.clique(["A1", "A2", "A3"])
        self.run_ok("build")
        (self.vault / "wiki/concepts/A1.md").unlink()
        for arg in ("wiki/concepts/A1.md", "A1", "[[A1]]"):
            res = run_script("wiki-clusters.py", ["lookup", arg], self.vault)
            self.assertEqual(res.returncode, 3, arg)

    def test_lookup_seed_reason(self):
        self.clique(["A1", "A2", "A3"])
        self.write("wiki/entities/Author.md", page("entity", "Author", "[[A1]]", status="seed"))
        self.run_ok("build")
        res = self.run_ok("lookup", "Author")
        data = json.loads(res.stdout)
        self.assertFalse(data["on_backbone"])
        self.assertEqual(data["reason"], "seed")

    def test_title_change_keeps_hash(self):
        self.clique(["A1", "A2", "A3"])
        first = self.mod.build_payload(self.vault)
        self.write("wiki/concepts/A1.md", page("concept", "Renamed", "[[A2]] [[A3]]"))
        second = self.mod.build_payload(self.vault)
        self.assertEqual(first["topology_sha256"], second["topology_sha256"])

    def test_duplicate_basename_first_wins(self):
        self.write("wiki/sources/Clash.md", page("source", "ClashSrc", "[[A1]] [[A2]]"))
        self.write("wiki/concepts/Clash.md", page("concept", "ClashCon", "[[A1]]"))
        self.write("wiki/concepts/A1.md", page("concept", "A1", "[[A2]] [[Clash]]"))
        self.write("wiki/concepts/A2.md", page("concept", "A2", "[[A1]]"))
        catalog, by_name, directed = self.mod.scan_vault(self.vault)
        self.assertEqual(by_name["Clash"], "wiki/sources/Clash.md")
        self.assertIn(("wiki/concepts/A1.md", "wiki/sources/Clash.md"), directed)
        self.assertNotIn(("wiki/concepts/A1.md", "wiki/concepts/Clash.md"), directed)

    def test_same_topology_stable_and_hierarchy(self):
        self.clique(["A1", "A2", "A3"])
        self.clique(["B1", "B2", "B3"])
        self.clique(["C1", "C2", "C3"])
        (self.vault / "wiki/concepts/A1.md").write_text(
            (self.vault / "wiki/concepts/A1.md").read_text(encoding="utf-8").replace("[[A2]]", "[[A2]] [[B1]]"),
            encoding="utf-8")
        (self.vault / "wiki/concepts/B1.md").write_text(
            (self.vault / "wiki/concepts/B1.md").read_text(encoding="utf-8").replace("[[B2]]", "[[B2]] [[C1]]"),
            encoding="utf-8")
        a = self.mod.build_payload(self.vault)
        b = self.mod.build_payload(self.vault)
        self.assertEqual(
            {p: rec["id"] for p, rec in a["membership"].items()},
            {p: rec["id"] for p, rec in b["membership"].items()},
        )
        catalog, _, directed = self.mod.scan_vault(self.vault)
        nodes, edges, _ic, _dr = self.mod.build_backbone(catalog, directed)
        trace = {}
        self.mod.run_louvain(nodes, edges, seed=self.mod.LOUVAIN_SEED, trace=trace)
        self.assertTrue(trace.get("aggregated"))

    def test_label_prefers_concept_over_heavier_source(self):
        self.write("wiki/sources/@S.md", page("source", "HeavySource", "[[C1]] [[C2]] [[C3]] [[C4]] [[C5]]"))
        for i in range(1, 6):
            links = " ".join(f"[[C{j}]]" for j in range(1, 6) if j != i)
            self.write(f"wiki/concepts/C{i}.md", page("concept", f"C{i}", f"{links} [[@S]]"))
        payload = self.mod.build_payload(self.vault)
        label = payload["clusters"][0]["label"]
        self.assertNotIn("HeavySource", label)

    def test_members_default_top_20(self):
        names = [f"N{i:02d}" for i in range(25)]
        for name in names:
            others = " ".join(f"[[{n}]]" for n in names if n != name)
            self.write(f"wiki/concepts/{name}.md", page("concept", name, others))
        self.run_ok("build")
        res = self.run_ok("members", "0")
        rows = json.loads(res.stdout)
        self.assertEqual(len(rows), 20)
        res_all = self.run_ok("members", "0", "--top", "0")
        self.assertEqual(len(json.loads(res_all.stdout)), 25)

    def test_assign_help_forbids_survey(self):
        res = run_script("wiki-clusters.py", ["assign", "--help"], self.vault)
        self.assertEqual(res.returncode, 0)
        self.assertIn("wiki-survey は呼ぶな", res.stdout)

    def test_assign_unassigned(self):
        self.clique(["A1", "A2", "A3"])
        self.write("wiki/concepts/Lonely.md", page("concept", "Lonely", "isolated"))
        self.run_ok("build")
        pop = self.vault / "pop.txt"
        pop.write_text("wiki/concepts/A1.md\nwiki/concepts/Lonely.md\nwiki/surveys/nope.md\n", encoding="utf-8")
        res = self.run_ok("assign", "--pages", str(pop))
        data = json.loads(res.stdout)
        self.assertTrue(any(p == "wiki/concepts/A1.md" for c in data["clusters"] for p in c["paths"]))
        self.assertIn("wiki/concepts/Lonely.md", data["unassigned"])
        self.assertIn("wiki/surveys/nope.md", data["unassigned"])

    def test_empty_list_ok(self):
        self.write("wiki/concepts/Lonely.md", page("concept", "Lonely", "isolated"))
        self.run_ok("build")
        res = self.run_ok("list")
        self.assertEqual(json.loads(res.stdout), [])

    def test_lookup_unbuilt_is_exit_3(self):
        res = run_script("wiki-clusters.py", ["lookup", "A1"], self.vault)
        self.assertEqual(res.returncode, 3)

    def test_doctor_surveys_do_not_stale_clusters(self):
        self.clique(["A1", "A2", "A3"])
        self.run_ok("build")
        run_script("wiki-graph.py", ["build"], self.vault)
        clusters = self.vault / ".vault-meta/clusters.json"
        graph = self.vault / ".vault-meta/graph.json"
        old = time.time() - 120
        os.utime(clusters, (old, old))
        os.utime(graph, (old, old))
        for p in (self.vault / "wiki/concepts").glob("*.md"):
            os.utime(p, (old - 60, old - 60))
        survey = self.write("wiki/surveys/New.md", page("survey", "New", "fresh"))
        now = time.time()
        os.utime(survey, (now, now))
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        spec = importlib.util.spec_from_file_location("wiki_doctor", SCRIPTS / "wiki-doctor.py")
        doc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doc)
        ctx = {
            "pages": list(doc.wiki_pages()),
            "cluster_pages": list(doc.cluster_pages()),
            "address_pages": list(doc.address_pages()),
        }
        rep = doc.Report()
        doc.check_derived_caches(rep, ctx)
        by = {r["check"]: r for r in rep.rows}
        detail = by["derived_caches"]["detail"]
        self.assertNotIn("clusters.json より新しい", detail)
        self.assertIn("graph.json より新しい", detail)
        cli = run_script("wiki-doctor.py", ["--only", "derived_caches", "--json"], self.vault)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        cli_detail = next(r["detail"] for r in json.loads(cli.stdout)["rows"] if r["check"] == "derived_caches")
        self.assertNotIn("clusters.json より新しい", cli_detail)
        self.assertIn("graph.json より新しい", cli_detail)

    def test_doctor_missing_clusters_is_warn(self):
        self.clique(["A1", "A2", "A3"])
        self.run_ok("build")
        (self.vault / ".vault-meta/clusters.json").unlink()
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        spec = importlib.util.spec_from_file_location("wiki_doctor", SCRIPTS / "wiki-doctor.py")
        doc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doc)
        ctx = {
            "pages": list(doc.wiki_pages()),
            "cluster_pages": list(doc.cluster_pages()),
            "address_pages": list(doc.address_pages()),
        }
        rep = doc.Report()
        doc.check_derived_caches(rep, ctx)
        by = {r["check"]: r for r in rep.rows}
        self.assertEqual(by["derived_caches"]["status"], "WARN")
        self.assertIn("clusters.json 無し", by["derived_caches"]["detail"])
        self.assertIn("wiki-clusters.py build", by["derived_caches"]["fix"])
        cli = run_script("wiki-doctor.py", ["--only", "derived_caches", "--json"], self.vault)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        row = next(r for r in json.loads(cli.stdout)["rows"] if r["check"] == "derived_caches")
        self.assertEqual(row["status"], "WARN")
        self.assertIn("clusters.json 無し", row["detail"])
        self.assertIn("wiki-clusters.py build", row["fix"])


if __name__ == "__main__":
    unittest.main()
