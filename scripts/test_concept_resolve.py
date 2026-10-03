#!/usr/bin/env python3
"""concept-resolve.py と concept compact / 新設ゲートの契約試験(§11)。

実 wiki は触らない。一時 vault を `WIKI_VAULT_ROOT` で指す。

  python3 -m unittest scripts/test_concept_resolve.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
RESOLVE_CLI = SCRIPTS / "concept-resolve.py"
WIKI_RESOLVE = SCRIPTS / "wiki-resolve.py"
PAGE_WRITE = SCRIPTS / "wiki-page-write.py"
CANDIDATES = SCRIPTS / "concept-candidates.py"

SEED_REJECTED = {
    "オブザーバビリティ || 可観測性": {
        "state": "rejected",
        "keep": "オブザーバビリティ",
        "drop": "可観測性",
        "surfaces_keep": ["オブザーバビリティ", "Observability"],
        "surfaces_drop": ["可観測性"],
        "reason": "同名異物。Kalman の出力からの状態再構成と運用テレメトリは別",
        "day": "2026-09-13",
    }
}

CONCEPT_WRITE = """---
type: concept
title: "試験概念"
date: 2026-06-01 12:00
created: 2026-06-01
updated: 2026-08-01
aliases:
  - "試験概念"
tags:
  - 2026/06/01
  - concept
status: developing
related: []
sources: []
---

# 試験概念

## 定義

試験用。
"""


def load_cr():
    spec = importlib.util.spec_from_file_location("concept_resolve_test", RESOLVE_CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def concept_page(title, aliases=(), extra_heading=None, sources=None):
    alias_block = ""
    if aliases:
        alias_block = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    srcs = sources or ["[[@2026__X__S]]"]
    src_block = "sources:\n" + "".join(f"  - \"{s}\"\n" for s in srcs)
    extra = ""
    if extra_heading:
        extra = f"\n## {extra_heading}\n\n主題節である。\n"
    return f"""---
type: concept
title: "{title}"
{alias_block}{src_block}updated: 2026-09-13
---

# {title}

## 定義

{title} の定義文。
{extra}
## 未編纂の観察

- 観察。

## 関連

- ソース: {srcs[0]}

## 出典

- {srcs[0]}
"""


def source_page(title):
    return f"""---
title: "{title}"
type: source
updated: 2026-09-13
---

# {title}

## 概要

{title} の本文。
"""


def entity_page(title, aliases=()):
    alias_block = ""
    if aliases:
        alias_block = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    return f"""---
title: "{title}"
type: entity
{alias_block}updated: 2026-09-13
---

# {title}

## 概要

概要。
"""


def default_table():
    cr = load_cr()
    return json.loads(json.dumps(cr.DEFAULT_TABLE))


def ledger_doc(decisions=None):
    table = default_table()
    return {
        "version": 1,
        "updated_at": "2026-09-13T00:00:00",
        "dialects": table["dialects"],
        "dialect_groups": table["dialect_groups"],
        "suffixes": table["suffixes"],
        "decisions": decisions if decisions is not None else {},
    }


class ConceptResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for sub in ("wiki/concepts", "wiki/entities", "wiki/sources",
                    "wiki/questions", "wiki/surveys", "wiki/asks", "wiki/briefs",
                    ".vault-meta/locks"):
            (self.vault / sub).mkdir(parents=True, exist_ok=True)
        (self.vault / "scripts").mkdir(exist_ok=True)
        shutil.copy2(SCRIPTS / "wiki-lock.sh", self.vault / "scripts" / "wiki-lock.sh")
        write(self.vault / ".vault-meta" / "concept-merges.json",
              json.dumps(ledger_doc({}), ensure_ascii=False, indent=1))
        self.env = dict(os.environ, WIKI_VAULT_ROOT=str(self.vault),
                        WIKI_LOCK_VAULT=str(self.vault))
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        self.cr = load_cr()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("WIKI_VAULT_ROOT", None)

    def put(self, rel, text):
        write(self.vault / rel, text)

    def run_cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, env=self.env, cwd=str(REPO), check=False,
        )

    def cr_cli(self, *args):
        return self.run_cli(RESOLVE_CLI, *args)

    def scan_rows(self, min_tier="weak"):
        proc = self.cr_cli("scan", "--min-tier", min_tier)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["pairs"]

    def lookup(self, name):
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        return self.cr.lookup_one(name)

    def write_ledger(self, decisions):
        write(self.vault / ".vault-meta" / "concept-merges.json",
              json.dumps(ledger_doc(decisions), ensure_ascii=False, indent=1))

    def write_candidates(self, name, aliases=None):
        data = {
            "version": 1,
            "candidates": {
                name: {
                    "name": name,
                    "aliases": aliases or [],
                    "sources": ["@2026__X__A", "@2026__Y__B"],
                    "state": "pending",
                    "first_seen": "2026-09-01",
                    "last_seen": "2026-09-02",
                    "mentions": [],
                }
            },
        }
        write(self.vault / ".vault-meta" / "concept-candidates.json",
              json.dumps(data, ensure_ascii=False, indent=1))

    def test_01_title_fold_is_strong(self):
        self.put("wiki/concepts/Foo-Bar.md", concept_page("Foo-Bar"))
        self.put("wiki/concepts/Foo Bar.md", concept_page("Foo Bar"))
        rows = self.scan_rows()
        keys = {r["key"] for r in rows}
        self.assertIn(self.cr.pair_key("Foo-Bar", "Foo Bar"), keys)
        row = next(r for r in rows if r["key"] == self.cr.pair_key("Foo-Bar", "Foo Bar"))
        self.assertEqual(row["tier"], "strong")

    def test_02_observability_shared_alias_then_rejected(self):
        self.put("wiki/concepts/オブザーバビリティ.md",
                 concept_page("オブザーバビリティ", ["Observability"]))
        self.put("wiki/concepts/可観測性.md",
                 concept_page("可観測性", ["Observability"]))
        rows = self.scan_rows()
        key = self.cr.pair_key("オブザーバビリティ", "可観測性")
        self.assertIn(key, {r["key"] for r in rows})
        self.assertEqual(next(r for r in rows if r["key"] == key)["tier"], "strong")
        self.write_ledger(SEED_REJECTED)
        rows = self.scan_rows()
        self.assertNotIn(key, {r["key"] for r in rows})

    def test_03_lookup_own_page_and_shared_alias_none(self):
        self.put("wiki/concepts/オブザーバビリティ.md",
                 concept_page("オブザーバビリティ", ["Observability"]))
        self.put("wiki/concepts/可観測性.md",
                 concept_page("可観測性", ["Observability"]))
        self.write_ledger(SEED_REJECTED)
        hit = self.lookup("可観測性")
        self.assertEqual(hit["status"], "HIT")
        self.assertEqual(hit["via"], "page")
        self.assertEqual(hit["path"], "wiki/concepts/可観測性.md")
        shared = self.lookup("Observability")
        self.assertEqual(shared["status"], "NONE")
        self.assertIsNone(shared["path"])

    def test_04_and_05_concept_compact_no_substring(self):
        self.put("wiki/concepts/オブザーバビリティ.md",
                 concept_page("オブザーバビリティ", ["Observability"]))
        self.put("wiki/concepts/可観測性.md",
                 concept_page("可観測性", ["Observability"]))
        self.put("wiki/concepts/CI-CDオブザーバビリティ.md",
                 concept_page("CI-CDオブザーバビリティ"))
        proc = self.run_cli(WIKI_RESOLVE, "--type", "concept", "--compact",
                            "Observability", "可観測性")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        obs = lines[0].split("\t")
        self.assertEqual(obs[:3], ["Observability", "concept", "NONE"])
        self.assertNotIn("CI-CD", obs[3])
        own = lines[1].split("\t")
        self.assertEqual(own[:3], ["可観測性", "concept", "HIT"])
        self.assertEqual(own[3], "concepts/可観測性.md(via=page)")
        self.assertNotIn("CI-CD", own[3])
        self.assertNotIn("オブザーバビリティ.md", own[3])

    def test_06_alarm_correlation_medium_and_gate(self):
        self.put("wiki/concepts/アラーム相関.md", concept_page("アラーム相関"))
        self.put("wiki/concepts/アラート相関.md", concept_page("アラート相関"))
        rows = self.scan_rows("medium")
        key = self.cr.pair_key("アラーム相関", "アラート相関")
        row = next(r for r in rows if r["key"] == key)
        self.assertEqual(row["tier"], "medium")
        own = self.lookup("アラーム相関")
        self.assertEqual((own["status"], own["via"]), ("HIT", "page"))
        for q in ("Alarm Correlation", "Alarm Correlations", "Alarm Correlation."):
            hit = self.lookup(q)
            self.assertEqual(hit["status"], "NONE", q)
            pages = (hit.get("dialect_gate") or {}).get("pages") or []
            self.assertEqual(len(pages), 2, q)
        compact = self.run_cli(
            WIKI_RESOLVE, "--type", "concept", "--compact", "Alarm Correlation")
        fields = compact.stdout.splitlines()[0].split("\t")
        self.assertEqual(fields[:3], ["Alarm Correlation", "concept", "NONE"])
        self.assertEqual(fields[3], "dialect-gate:2 pages")

    def test_07_page_less_alias_only(self):
        self.put("wiki/concepts/アラート管理.md", concept_page("アラート管理"))
        hit = self.lookup("アラーム管理")
        self.assertEqual(hit["status"], "NONE")
        self.assertEqual(len((hit.get("dialect_gate") or {}).get("pages") or []), 1)
        self.write_candidates("アラーム管理")
        rows = self.scan_rows("medium")
        row = next(r for r in rows if r["suggest_drop"] == "アラーム管理")
        self.assertEqual(row["tier"], "medium")
        self.assertTrue(row["page_less"])
        self.assertEqual(row["suggest_keep"], "アラート管理")
        proc = self.cr_cli("plan", "--keep", "アラート管理", "--drop", "アラーム管理")
        plan = json.loads(proc.stdout)
        self.assertEqual(plan["merge_kind"], "alias-only")
        self.assertFalse(plan["drop_has_topics"])
        compact = self.run_cli(
            WIKI_RESOLVE, "--type", "concept", "--compact", "アラーム管理")
        fields = compact.stdout.splitlines()[0].split("\t")
        self.assertEqual(fields[:3], ["アラーム管理", "concept", "NONE"])
        self.assertEqual(fields[3], "dialect-gate:1 pages")

    def test_08_merged_lookup_and_blocked(self):
        self.put("wiki/concepts/アラート管理.md", concept_page("アラート管理"))
        self.put("wiki/concepts/アラーム管理.md", concept_page("アラーム管理"))
        proc = self.cr_cli("decide", "--keep", "アラート管理", "--drop", "アラーム管理", "--merged")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        own = self.lookup("アラーム管理")
        self.assertEqual((own["status"], own["via"]), ("HIT", "page"))
        pending = self.lookup("Alarm Management")
        self.assertEqual(pending["status"], "NONE")
        self.assertNotEqual(pending.get("via"), "merged")
        compact_pending = self.run_cli(
            WIKI_RESOLVE, "--type", "concept", "--compact", "Alarm Management")
        self.assertEqual(
            compact_pending.stdout.splitlines()[0].split("\t")[3],
            "dialect-gate:2 pages")
        (self.vault / "wiki/concepts/アラーム管理.md").unlink()
        for q in ("アラーム管理", "Alarm Management"):
            hit = self.lookup(q)
            self.assertEqual(hit["status"], "HIT", q)
            self.assertEqual(hit["via"], "merged", q)
            self.assertEqual(hit["path"], "wiki/concepts/アラート管理.md", q)
            self.assertTrue(self.cr.is_create_blocked(q), q)
        compact = self.run_cli(
            WIKI_RESOLVE, "--type", "concept", "--compact", "Alarm Management")
        self.assertEqual(compact.returncode, 0, compact.stderr)
        self.assertEqual(
            compact.stdout.splitlines()[0].split("\t")[3],
            "concepts/アラート管理.md(via=merged)")

    def test_09_rejected_surfaces_and_covers(self):
        self.put("wiki/concepts/アラート管理.md",
                 concept_page("アラート管理", ["Alert Management", "Alarm Management"]))
        proc = self.cr_cli(
            "decide", "--keep", "アラート管理", "--drop", "アラーム管理", "--rejected",
            "--surface-keep", "アラート管理", "--surface-keep", "Alert Management",
            "--surface-drop", "アラーム管理", "--surface-drop", "Alarm Management",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.put("wiki/concepts/アラート管理.md", concept_page("アラート管理"))
        self.assertFalse(self.cr.is_create_blocked("Alarm Management"))
        self.assertFalse(self.cr.is_create_blocked("アラーム管理"))
        self.assertTrue(self.cr.is_create_blocked("Alert Management"))
        self.assertTrue(self.cr.rejected_covers("Alarm Management", "アラート管理"))
        self.assertFalse(self.cr.rejected_covers("Alert Management", "アラート管理"))

    def test_19_surface_intersection_and_keep_wins(self):
        self.put("wiki/concepts/Keep.md", concept_page("Keep", ["Shared"]))
        self.put("wiki/concepts/Drop.md", concept_page("Drop", ["Shared"]))
        proc = self.cr_cli(
            "decide", "--keep", "Keep", "--drop", "Drop", "--rejected",
            "--surface-keep", "Same", "--surface-drop", "Same",
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        plan = json.loads(self.cr_cli("plan", "--keep", "Keep", "--drop", "Drop").stdout)
        self.assertIn("Shared", plan["surfaces_keep"])
        self.assertNotIn("Shared", plan["surfaces_drop"])

    def test_11_promote_blocked_and_observability_create(self):
        self.put("wiki/concepts/オブザーバビリティ.md",
                 concept_page("オブザーバビリティ", ["Observability"]))
        self.put("wiki/concepts/可観測性.md",
                 concept_page("可観測性", ["Observability"]))
        self.write_candidates("Observability")
        proc = self.run_cli(CANDIDATES, "promote", "Observability",
                            "--page", "wiki/concepts/オブザーバビリティ.md")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("alias-collision:", proc.stderr)
        ledger = json.loads((self.vault / ".vault-meta" / "concept-candidates.json").read_text())
        self.assertEqual(ledger["candidates"]["Observability"]["state"], "pending")
        proc = self.run_cli(PAGE_WRITE, "wiki/concepts/Observability.md",
                            "--no-allocate", "--content-file",
                            str(self._content(CONCEPT_WRITE)))
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("alias-collision:", proc.stdout + proc.stderr)
        self.assertFalse((self.vault / "wiki/concepts/Observability.md").is_file())

    def test_12_short_shared_alias_not_strong(self):
        self.put("wiki/concepts/Alpha.md", concept_page("Alpha", ["dp"]))
        self.put("wiki/concepts/Beta.md", concept_page("Beta", ["dp"]))
        self.put("wiki/concepts/Gamma.md", concept_page("Gamma", ["RLHF"]))
        self.put("wiki/concepts/Delta.md", concept_page("Delta", ["RLHF"]))
        rows = self.scan_rows("strong")
        keys = {r["key"] for r in rows}
        self.assertNotIn(self.cr.pair_key("Alpha", "Beta"), keys)
        self.assertNotIn(self.cr.pair_key("Gamma", "Delta"), keys)

    def test_13_shared_source_only_is_not_a_pair(self):
        src = "[[@2026__Same__Paper]]"
        self.put("wiki/concepts/量子もつれ.md", concept_page("量子もつれ", sources=[src]))
        self.put("wiki/concepts/ロードバランサ.md", concept_page("ロードバランサ", sources=[src]))
        rows = self.scan_rows()
        keys = {r["key"] for r in rows}
        self.assertNotIn(self.cr.pair_key("量子もつれ", "ロードバランサ"), keys)

    def test_14_plan_linkers_include_path_and_caret(self):
        self.put("wiki/concepts/DropStem.md", concept_page("DropStem"))
        self.put("wiki/concepts/KeepStem.md", concept_page("KeepStem"))
        self.put("wiki/sources/@2026__L__A.md",
                 source_page("A") + "\n[[DropStem]]\n")
        self.put("wiki/sources/@2026__L__B.md",
                 source_page("B") + "\n[[wiki/concepts/DropStem]]\n")
        self.put("wiki/sources/@2026__L__C.md",
                 source_page("C") + "\n[[DropStem^block]]\n")
        plan = json.loads(self.cr_cli("plan", "--keep", "KeepStem", "--drop", "DropStem").stdout)
        self.assertIn("wiki/sources/@2026__L__A.md", plan["linkers"])
        self.assertIn("wiki/sources/@2026__L__B.md", plan["linkers"])
        self.assertIn("wiki/sources/@2026__L__C.md", plan["linkers"])

    def test_15_query_decorrelation_does_not_eat_suffix(self):
        table = default_table()
        key = self.cr.dialect_key("クエリ非相関化", table)
        self.assertEqual(key, self.cr.fold("クエリ非相関化"))
        self.assertNotEqual(key, self.cr.dialect_key("アラーム相関", table))
        self.assertFalse(key.endswith("correlation"))

    def test_16_plan_reports_kind_and_topics(self):
        self.put("wiki/concepts/Keep.md", concept_page("Keep"))
        self.put("wiki/concepts/Inbox.md", concept_page("Inbox"))
        self.put("wiki/concepts/Topic.md", concept_page("Topic", extra_heading="分割方式"))
        inbox = json.loads(self.cr_cli("plan", "--keep", "Keep", "--drop", "Inbox").stdout)
        self.assertEqual(inbox["merge_kind"], "inbox-append")
        self.assertFalse(inbox["drop_has_topics"])
        topic = json.loads(self.cr_cli("plan", "--keep", "Keep", "--drop", "Topic").stdout)
        self.assertEqual(topic["merge_kind"], "recompile-keep")
        self.assertTrue(topic["drop_has_topics"])
        none = json.loads(self.cr_cli("plan", "--keep", "Keep", "--drop", "頁無し").stdout)
        self.assertEqual(none["merge_kind"], "alias-only")
        self.assertFalse(none["drop_has_topics"])

    def test_17_source_compact_keeps_substring(self):
        self.put("wiki/sources/@2025__arXiv__Sample Paper.md", source_page("Sample Paper"))
        proc = self.run_cli(WIKI_RESOLVE, "--type", "source", "--compact", "Sample")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = proc.stdout.splitlines()[0].split("\t")
        self.assertEqual(fields[:3], ["Sample", "source", "HIT"])
        self.assertIn("substring", fields[3])

    def test_18_any_observability_concept_row_is_none(self):
        self.put("wiki/concepts/オブザーバビリティ.md",
                 concept_page("オブザーバビリティ", ["Observability"]))
        self.put("wiki/concepts/可観測性.md",
                 concept_page("可観測性", ["Observability"]))
        self.put("wiki/entities/Observability Inc.md",
                 entity_page("Observability Inc", ["Observability"]))
        proc = self.run_cli(WIKI_RESOLVE, "--type", "any", "Observability")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        concept_hits = [c for c in payload["candidates"] if c["type"] == "concept"]
        self.assertEqual(concept_hits, [])
        entity_hits = [c for c in payload["candidates"] if c["type"] == "entity"]
        self.assertTrue(entity_hits)

    def test_seed_pair_key_and_no_import_cycle(self):
        self.assertEqual(self.cr.pair_key("オブザーバビリティ", "可観測性"),
                         "オブザーバビリティ || 可観測性")
        src = RESOLVE_CLI.read_text(encoding="utf-8")
        imports = [ln for ln in src.splitlines()
                   if ln.startswith("import ") or ln.startswith("from ")]
        joined = "\n".join(imports)
        self.assertNotIn("wiki-resolve", joined)
        self.assertNotIn("concept-candidates", joined)
        self.assertNotIn("wiki-page-write", joined)

    def test_dialect_key_examples(self):
        table = default_table()
        self.assertEqual(self.cr.dialect_key("アラーム相関", table), "alertcorrelation")
        self.assertEqual(self.cr.dialect_key("Alarm Correlation.", table), "alertcorrelation")
        self.assertEqual(self.cr.dialect_key("Alarm Correlations", table), "alertcorrelation")
        self.assertEqual(self.cr.dialect_key("Alarm Management", table), "alertmanagement")
        with self.assertRaises(TypeError):
            self.cr.dialect_key("x", None)

    def _content(self, text, name="page.md"):
        path = self.vault / name
        path.write_text(text, encoding="utf-8")
        return path


class ConceptPageWriteGateTest(unittest.TestCase):
    """§11.10: rejected 後の新設。write helpers 側からも同じ状態を固定する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for sub in ("wiki/concepts", "wiki/entities", "wiki/sources",
                    ".vault-meta/locks", "scripts"):
            (self.vault / sub).mkdir(parents=True, exist_ok=True)
        shutil.copy2(SCRIPTS / "wiki-lock.sh", self.vault / "scripts" / "wiki-lock.sh")
        self.env = dict(os.environ, WIKI_VAULT_ROOT=str(self.vault),
                        WIKI_LOCK_VAULT=str(self.vault))
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        write(self.vault / "wiki/concepts/アラート管理.md", concept_page("アラート管理"))
        cr = load_cr()
        write(self.vault / ".vault-meta" / "concept-merges.json",
              json.dumps(ledger_doc({
                  cr.pair_key("アラート管理", "アラーム管理"): {
                      "state": "rejected",
                      "keep": "アラート管理",
                      "drop": "アラーム管理",
                      "surfaces_keep": ["アラート管理", "Alert Management"],
                      "surfaces_drop": ["アラーム管理", "Alarm Management"],
                      "reason": "試験",
                      "day": "2026-09-13",
                  }
              }), ensure_ascii=False, indent=1))
        self.body = self.vault / "body.md"
        self.body.write_text(CONCEPT_WRITE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("WIKI_VAULT_ROOT", None)

    def write_cli(self, rel, *extra):
        return subprocess.run(
            [sys.executable, str(PAGE_WRITE), rel, "--no-allocate",
             "--content-file", str(self.body), *extra],
            capture_output=True, text=True, env=self.env, cwd=str(REPO), check=False,
        )

    def test_10_rejected_drop_surfaces_can_be_created(self):
        ok_a = self.write_cli("wiki/concepts/Alarm Management.md")
        self.assertEqual(ok_a.returncode, 0, ok_a.stdout + ok_a.stderr)
        self.assertTrue((self.vault / "wiki/concepts/Alarm Management.md").is_file())
        ok_b = self.write_cli("wiki/concepts/アラーム管理.md")
        self.assertEqual(ok_b.returncode, 0, ok_b.stdout + ok_b.stderr)
        blocked = self.write_cli("wiki/concepts/Alert Management.md")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertTrue(any(tok in (blocked.stdout + blocked.stderr)
                            for tok in ("dialect-gate:", "alias-collision:", "merged-drop:")))
        self.assertFalse((self.vault / "wiki/concepts/Alert Management.md").is_file())
        forced = self.write_cli("wiki/concepts/Alert Management.md", "--force")
        self.assertNotEqual(forced.returncode, 0)
        self.assertFalse((self.vault / "wiki/concepts/Alert Management.md").is_file())


if __name__ == "__main__":
    unittest.main()
