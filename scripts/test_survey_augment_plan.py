#!/usr/bin/env python3
"""survey-augment-plan.py のテスト。

    python3 scripts/test_survey_augment_plan.py
"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def load_module():
    spec = importlib.util.spec_from_file_location("survey_augment_plan", SCRIPTS / "survey-augment-plan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_module()


def page(fm_lines, body=""):
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


def links(key, items):
    if not items:
        return []
    return [f"{key}:"] + [f'  - "[[{i}]]"' for i in items]


def survey(title, sources, *, created="2026-09-01", aliases=None, related=None, body=""):
    fm = ["type: survey", f'title: "{title}"', f"created: {created}", "updated: 2026-10-18"]
    if aliases:
        fm += ["aliases:"] + [f'  - "{a}"' for a in aliases]
    fm += links("related", related)
    fm += links("sources", sources)
    return page(fm, body)


def source(created, *, tags=None, related=None, address=None):
    fm = ["type: source", f"created: {created}"]
    if address:
        fm.append(f"address: {address}")
    fm += ["tags:", "  - 2026/10/01", "  - source"] + [f"  - {t}" for t in (tags or [])]
    fm += links("related", related)
    return page(fm, "# body\n")


def concept(sources):
    return page(["type: concept"] + links("sources", sources))


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("wiki/surveys", "wiki/sources", "wiki/concepts", "wiki/entities", "wiki/log"):
            (self.root / d).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class NameTest(unittest.TestCase):
    def test_title_and_basename_share_key(self):
        self.assertEqual(M.norm_name("SLI/SLO の教科書"), M.norm_name("SLI-SLO教科書"))

    def test_heading_drops_trailing_notes(self):
        self.assertEqual(M.heading_page_key("SLI/SLO の教科書(未収録の 15 本の増補)"), M.norm_name("SLI-SLO教科書"))
        self.assertEqual(M.heading_page_key("A教科書 (deep mode)"), M.norm_name("A教科書"))

    def test_bulk_augment_heading_is_substantive(self):
        # SKILL.md の一括増補 手順 5 が書く見出し。保守エントリとして除かれてはならない。
        self.assertIsNone(M.MAINTENANCE_RE.search("SLI-SLO教科書(一括増補)"))
        self.assertEqual(M.heading_page_key("SLI-SLO教科書(一括増補)"), M.norm_name("SLI-SLO教科書"))


class LastAugmentTest(VaultCase):
    def test_picks_latest_substantive_entry_across_rotated_logs(self):
        self.write("wiki/surveys/SLI-SLO教科書.md", survey("SLI/SLO の教科書", [], aliases=["SLI-SLO教科書"]))
        self.write("wiki/log/2026-09.md", "## [2026-09-25] wiki-survey | SLI/SLO の教科書(改稿)\n")
        self.write("wiki/log.md", "\n".join([
            "## [2026-10-17] wiki-survey | 全サーベイの見取り図を追加、再作成",
            "## [2026-10-16] wiki-survey | SLI/SLO の教科書(図表転載)",
            "## [2026-10-15] wiki-survey-recommend | SLI/SLO の教科書",
            "## [2026-10-09] query-deep | SLI-SLO教科書",
            "## [2026-10-10] ingest-paper | SLI/SLO の教科書",
        ]) + "\n")
        surveys = M.scan_surveys(self.root)
        last, unmatched = M.last_augment_dates(self.root, surveys)
        self.assertEqual(last["wiki/surveys/SLI-SLO教科書.md"]["date"], "2026-10-09")
        self.assertEqual(unmatched, [])

    def test_unmatched_wiki_survey_heading_is_reported(self):
        self.write("wiki/surveys/A.md", survey("A", []))
        self.write("wiki/log.md", "## [2026-10-01] wiki-survey | 存在しないページ\n")
        _, unmatched = M.last_augment_dates(self.root, M.scan_surveys(self.root))
        self.assertEqual(unmatched, ["[2026-10-01] wiki-survey | 存在しないページ"])


class PlanTest(VaultCase):
    def setUp(self):
        super().setUp()
        # 既存の収録 source。concept X と Y にぶら下がる。
        self.write("wiki/sources/old-a.md", source("2026-09-01", tags=["slo"]))
        self.write("wiki/sources/old-b.md", source("2026-09-01", tags=["rdma"]))
        self.write("wiki/concepts/X.md", concept(["old-a", "new-shared", "new-a"]))
        self.write("wiki/concepts/Y.md", concept(["old-b", "new-shared"]))
        # 新 source
        self.write("wiki/sources/new-a.md", source("2026-10-05", tags=["slo"], address="c-1"))
        self.write("wiki/sources/new-shared.md", source("2026-10-06"))
        self.write("wiki/sources/new-unrelated.md", source("2026-10-06", tags=["ecology"]))
        self.write("wiki/sources/new-but-included.md", source("2026-10-07", tags=["slo"], related=["X"]))
        self.write("wiki/sources/too-old.md", source("2026-09-15", tags=["slo"]))
        self.write("wiki/surveys/SLO.md", survey("SLO", ["old-a"], body="本文 [[new-but-included]]\n"))
        self.write("wiki/surveys/RDMA.md", survey("RDMA", ["old-b"], created="2026-09-02"))
        self.write("wiki/log.md", "## [2026-10-01] wiki-survey | SLO(増補)\n")

    def plan(self, **kw):
        return M.build_plan(self.root, check_index=False, **kw)

    def pages(self, plan):
        return {Path(p["path"]).stem: p for p in plan["pages"]}

    def test_candidates_respect_cutoff_inclusion_and_relevance(self):
        p = self.pages(self.plan())
        self.assertEqual(p["SLO"]["cutoff"], "2026-10-01")
        self.assertEqual(p["SLO"]["cutoff_basis"], "log")
        names = [c["source"] for c in p["SLO"]["candidates"]]
        self.assertIn("new-a", names)
        self.assertIn("new-shared", names)
        self.assertNotIn("new-unrelated", names)
        self.assertNotIn("new-but-included", names)
        self.assertNotIn("too-old", names)

    def test_falls_back_to_created_without_log(self):
        p = self.pages(self.plan())
        self.assertEqual(p["RDMA"]["cutoff_basis"], "created")
        self.assertEqual(p["RDMA"]["cutoff"], "2026-09-02")
        self.assertEqual([c["source"] for c in p["RDMA"]["candidates"]], ["new-shared"])

    def test_shared_and_reverse_table(self):
        plan = self.plan()
        shared = {r["source"]: [Path(s["survey"]).stem for s in r["surveys"]] for r in plan["shared"]}
        self.assertEqual(set(shared), {"new-shared"})
        self.assertEqual(set(shared["new-shared"]), {"SLO", "RDMA"})
        rev = {r["source"] for r in plan["reverse"]}
        self.assertEqual(rev, {"new-a", "new-shared"})

    def test_top_k_trims_both_tables(self):
        plan = self.plan(top_k=1)
        self.assertEqual(plan["shared"], [])
        p = self.pages(plan)
        total = sum(len(x["candidates"]) for x in p.values())
        self.assertEqual(total, 2)

    def test_pages_without_candidates_are_skipped(self):
        p = self.pages(self.plan(min_score=1.01))
        self.assertEqual({x["action"] for x in p.values()}, {"skip"})

    def test_since_overrides_log(self):
        p = self.pages(self.plan(since="2026-10-06"))
        self.assertEqual(p["SLO"]["cutoff_basis"], "since")
        self.assertEqual(p["SLO"]["candidates"], [])
        p = self.pages(self.plan(since="2026-10-06", include_same_day=True))
        self.assertEqual([c["source"] for c in p["SLO"]["candidates"]], ["new-shared"])

    def test_main_writes_json_and_summary(self):
        old = os.environ.get("WIKI_VAULT_ROOT")
        os.environ["WIKI_VAULT_ROOT"] = str(self.root)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = M.main(["--no-index-check", "--out-dir", ".vault-meta/survey-augment"])
        finally:
            if old is None:
                del os.environ["WIKI_VAULT_ROOT"]
            else:
                os.environ["WIKI_VAULT_ROOT"] = old
        self.assertEqual(rc, 0)
        out = self.root / ".vault-meta/survey-augment"
        data = json.loads((out / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["pages"]), 2)
        self.assertIn("共有 source", (out / "plan.md").read_text(encoding="utf-8"))
        self.assertIn("一括増補の計画", buf.getvalue())

    def test_bad_since_is_usage_error(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(M.main(["--since", "10/01", "--no-write"]), M.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
