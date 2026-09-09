#!/usr/bin/env python3
"""Tests for wiki token-discipline helper scripts.

Uses a TemporaryDirectory vault via WIKI_VAULT_ROOT. Does not touch the real wiki.
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


class WikiTokenScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        for rel in ("wiki/entities", "wiki/concepts", "wiki/sources"):
            (self.vault / rel).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_resolve_exact_alias_empty(self):
        self.write("wiki/entities/Hellerstein.md", """---
type: entity
title: "Hellerstein"
aliases:
  - "Joe Hellerstein"
role: database researcher
---

# Hellerstein

UC Berkeley.
""")
        self.write("wiki/concepts/RDMA.md", """---
type: concept
title: "RDMA"
aliases:
  - "Remote Direct Memory Access"
---

# RDMA

## 定義

Remote memory access without CPU involvement.
""")
        self.write("wiki/concepts/_index.md", """---
type: meta
title: index
---

# Concepts
""")

        exact = run_script("wiki-resolve.py", ["Hellerstein", "--type", "entity", "--top", "8"], self.vault)
        self.assertEqual(exact.returncode, 0, exact.stderr)
        payload = json.loads(exact.stdout)
        self.assertEqual(payload["query"], "Hellerstein")
        self.assertEqual(payload["type"], "entity")
        self.assertEqual(len(payload["candidates"]), 1)
        hit = payload["candidates"][0]
        self.assertEqual(hit["path"], "wiki/entities/Hellerstein.md")
        self.assertEqual(hit["match"], "filename")
        self.assertEqual(hit["type"], "entity")
        self.assertIn("database researcher", hit["one_liner"])

        alias = run_script(
            "wiki-resolve.py",
            ["Remote Direct Memory Access", "--type", "concept", "--top", "8"],
            self.vault,
        )
        self.assertEqual(alias.returncode, 0, alias.stderr)
        alias_payload = json.loads(alias.stdout)
        self.assertGreaterEqual(len(alias_payload["candidates"]), 1)
        self.assertEqual(alias_payload["candidates"][0]["path"], "wiki/concepts/RDMA.md")
        self.assertEqual(alias_payload["candidates"][0]["match"], "alias")
        self.assertIn("Remote Direct Memory Access", alias_payload["candidates"][0]["aliases"])

        empty = run_script(
            "wiki-resolve.py",
            ["zzz-no-such-page-xyz", "--type", "any", "--top", "3"],
            self.vault,
        )
        self.assertEqual(empty.returncode, 0, empty.stderr)
        empty_payload = json.loads(empty.stdout)
        self.assertEqual(empty_payload["candidates"], [])

        fat = "---\ntype: concept\ntitle: \"FatPage\"\nrole: keep-role\nrelated:\n"
        fat += "".join(f"  - \"[[Pad{i:04d}-{'x' * 40}]]\"\n" for i in range(80))
        fat += "---\n\n# FatPage\n\nbody line that must not be required\n"
        self.assertGreater(len(fat.encode("utf-8")), 2048)
        self.write("wiki/concepts/FatPage.md", fat)
        fat_res = run_script("wiki-resolve.py", ["FatPage", "--type", "concept"], self.vault)
        self.assertEqual(fat_res.returncode, 0, fat_res.stderr)
        fat_hit = json.loads(fat_res.stdout)["candidates"][0]
        self.assertNotEqual(fat_hit["one_liner"], "---")
        self.assertEqual(fat_hit["one_liner"], "keep-role")

    def test_excerpt_tail_keeps_last_n_bullets(self):
        self.write("wiki/concepts/TailDemo.md", """---
type: concept
title: "TailDemo"
related:
  - "[[Alpha]]"
  - "[[Bravo]]"
  - "[[Charlie]]"
  - "[[Delta]]"
  - "[[Echo]]"
  - "[[Foxtrot]]"
  - "[[Golf]]"
  - "[[Hotel]]"
  - "[[India]]"
sources:
  - "[[@one]]"
  - "[[@two]]"
  - "[[@three]]"
  - "[[@four]]"
  - "[[@five]]"
  - "[[@six]]"
  - "[[@seven]]"
  - "[[@eight]]"
  - "[[@nine]]"
---

# TailDemo

## 定義

A definition line.

## 横断的知見

> [!note] keep this callout
### keep this heading
- old-1
- old-2
- old-3
- keep-a
- keep-b

## 未解決の問い

- q-old-1
- q-old-2
- q-keep
""")
        proc = run_script(
            "wiki-excerpt.py",
            ["wiki/concepts/TailDemo.md", "--tail", "2"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("title: TailDemo", out)
        self.assertIn("related:", out)
        self.assertIn("[[Hotel]]", out)
        self.assertNotIn("[[India]]", out)
        self.assertIn("[[@eight]]", out)
        self.assertNotIn("[[@nine]]", out)
        self.assertIn("> [!note] keep this callout", out)
        self.assertIn("### keep this heading", out)
        self.assertIn("- keep-a", out)
        self.assertIn("- keep-b", out)
        self.assertNotIn("- old-1", out)
        self.assertNotIn("- old-2", out)
        self.assertNotIn("- old-3", out)
        self.assertIn("- q-keep", out)
        self.assertNotIn("- q-old-1", out)
        self.assertIn("A definition line.", out)

    def test_excerpt_missing_file_exits_3(self):
        proc = run_script("wiki-excerpt.py", ["wiki/concepts/Missing.md"], self.vault)
        self.assertEqual(proc.returncode, 3)

    def test_excerpt_inbox_alias_and_nested_tail(self):
        self.write("wiki/concepts/Inbox.md", """---
type: concept
title: "Inbox"
---

# Inbox

## 定義

def.

## verifier の保証範囲

- **claim one is true**: body of claim one.
  - 根拠: [[@a]] — evidence a
  - 反証: [[@b]] — counter b
- **claim two is true**
  - 根拠: [[@c]] — evidence c

## 未解決の問い

- q1

## 未編纂の観察

- old observation
  - 根拠: [[@old]] — nested line of old observation
- new observation
  - 根拠: [[@new]] — nested line of new observation
""")
        # Legacy name resolves to the new inbox heading; nested lines drop with their parent.
        proc = run_script(
            "wiki-excerpt.py",
            ["wiki/concepts/Inbox.md", "--sections", "定義,横断的知見", "--tail", "1"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("## 未編纂の観察", proc.stdout)
        self.assertNotIn("## 横断的知見", proc.stdout)
        self.assertIn("- new observation", proc.stdout)
        self.assertIn("nested line of new observation", proc.stdout)
        self.assertNotIn("- old observation", proc.stdout)
        self.assertNotIn("nested line of old observation", proc.stdout)
        # Topic sections are not tail-trimmed and are not in the default set.
        default = run_script("wiki-excerpt.py", ["wiki/concepts/Inbox.md"], self.vault)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertNotIn("## verifier の保証範囲", default.stdout)
        self.assertIn("## 未編纂の観察", default.stdout)
        self.assertIn("## 未解決の問い", default.stdout)

    def test_excerpt_outline(self):
        self.write("wiki/concepts/Shape.md", """---
type: concept
title: "Shape"
---

# Shape

## 定義

definition prose that must not appear in the outline.

## 子概念

- [[KidA]]
- [[KidB]]

## 成熟モデルの 2 軸

- **導入軸と洗練軸は独立である**: long body text that must not appear.
  - 根拠: [[@x]] — hidden evidence
### 洗練軸の 5 段階
- **ゲームデーから自動化まで 5 段階**: more hidden body.
> [!contradiction] hidden callout body

## 前史

prose only paragraph one.
prose only paragraph two.

## 未解決の問い

- q1
- q2

## 横断的知見

- legacy obs 1
- legacy obs 2
- legacy obs 3
""")
        proc = run_script("wiki-excerpt.py", ["wiki/concepts/Shape.md", "--outline"], self.vault)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("# outline: wiki/concepts/Shape.md", out)
        self.assertIn("## 定義\n", out)
        self.assertIn("## 子概念  (2 件)", out)
        self.assertIn("## 成熟モデルの 2 軸  (2 命題)", out)
        self.assertIn("- 導入軸と洗練軸は独立である\n", out)
        self.assertIn("### 洗練軸の 5 段階", out)
        self.assertIn("- ゲームデーから自動化まで 5 段階\n", out)
        self.assertIn("> [!contradiction]\n", out)
        self.assertIn("## 前史  (散文 2 行)", out)
        self.assertIn("## 未解決の問い  (2 件)", out)
        self.assertIn("## 横断的知見 (旧名; = 未編纂の観察)  (3 件)", out)
        self.assertNotIn("definition prose", out)
        self.assertNotIn("long body text", out)
        self.assertNotIn("hidden evidence", out)
        self.assertNotIn("hidden callout body", out)
        self.assertNotIn("legacy obs 1", out)

    def test_concept_stats_compile_debt(self):
        def page(name, inbox_heading, n_inbox, topics):
            topic_blocks = "".join(f"\n## topic {i}\n\n- **claim {i}**\n" for i in range(topics))
            inbox = "\n".join(f"- obs {i}\n  - 根拠: [[@s{i}]] — nested" for i in range(n_inbox))
            self.write(f"wiki/concepts/{name}.md", f"""---
type: concept
title: "{name}"
---

# {name}

## 定義

d.
{topic_blocks}
## 未解決の問い

- q

## {inbox_heading}

{inbox}
""")

        page("Heavy", "未編纂の観察", 6, 2)
        page("LegacyOrphan", "横断的知見", 16, 0)
        page("LightOrphan", "横断的知見", 4, 0)
        page("Compiled", "未編纂の観察", 0, 3)

        proc = run_script(
            "wiki-concept-stats.py",
            ["--compile-debt", "--min-inbox", "5", "--min-orphan-inbox", "15"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = json.loads(proc.stdout)
        by_path = {r["path"]: r for r in rows}
        self.assertEqual(
            [r["path"] for r in rows],
            ["wiki/concepts/LegacyOrphan.md", "wiki/concepts/Heavy.md"],
        )
        heavy = by_path["wiki/concepts/Heavy.md"]
        self.assertEqual(heavy["inbox_bullets"], 6)
        self.assertEqual(heavy["topic_sections"], 2)
        self.assertFalse(heavy["legacy_heading"])
        legacy = by_path["wiki/concepts/LegacyOrphan.md"]
        self.assertEqual(legacy["inbox_bullets"], 16)
        self.assertEqual(legacy["topic_sections"], 0)
        self.assertTrue(legacy["legacy_heading"])

        # Orphan threshold: 14 legacy bullets with no topic section is not yet debt at 15.
        page("AlmostOrphan", "横断的知見", 14, 0)
        again = run_script(
            "wiki-concept-stats.py",
            ["--compile-debt", "--min-inbox", "50", "--min-orphan-inbox", "15"],
            self.vault,
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        paths = [r["path"] for r in json.loads(again.stdout)]
        self.assertEqual(paths, ["wiki/concepts/LegacyOrphan.md"])

        bad = run_script(
            "wiki-concept-stats.py", ["--compile-debt", "--promote-children"], self.vault
        )
        self.assertEqual(bad.returncode, 2)

    def test_prepend_hot_trims_max_entries_and_budget(self):
        old = "".join(
            f"## 2026-01-0{i} | old-{i}\n- Focus: f{i}\n- Key insight: k{i}\n\n"
            for i in range(1, 4)
        )
        self.write("wiki/hot.md", old)
        newest = "## 2026-08-30 | newest\n- Focus: now\n- Key insight: fresh\n"
        proc = run_script(
            "wiki-catalog.py",
            ["prepend-hot", "--text", newest, "--max-entries", "2", "--budget-tokens", "2000"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "prepend-hot")
        self.assertEqual(payload["path"], "wiki/hot.md")
        text = (self.vault / "wiki/hot.md").read_text(encoding="utf-8")
        self.assertIn("## 2026-08-30 | newest", text)
        self.assertIn("## 2026-01-01 | old-1", text)
        self.assertNotIn("## 2026-01-03 | old-3", text)
        self.assertEqual(text.count("## "), 2)

        huge = (
            "## 2026-08-31 | huge\n"
            "- Focus: keep-focus\n"
            "- Key insight: keep-insight\n"
            "- Extra: " + ("x" * 9000) + "\n"
        )
        budget = run_script(
            "wiki-catalog.py",
            ["prepend-hot", "--text", huge, "--max-entries", "5", "--budget-tokens", "50"],
            self.vault,
        )
        self.assertEqual(budget.returncode, 0, budget.stderr)
        trimmed = (self.vault / "wiki/hot.md").read_text(encoding="utf-8")
        self.assertIn("## 2026-08-31 | huge", trimmed)
        self.assertIn("Focus: keep-focus", trimmed)
        self.assertIn("Key insight: keep-insight", trimmed)
        self.assertNotIn("Extra:", trimmed)
        self.assertNotIn("## 2026-08-30 | newest", trimmed)

    def test_add_catalog_line_alpha_insert_and_dedup(self):
        self.write("wiki/concepts/_index.md", """---
type: meta
title: concepts
---

# Concepts

## 現行コンセプトカタログ

- [[Alpha]]
- [[Charlie]]
""")
        first = run_script(
            "wiki-catalog.py",
            [
                "add-catalog-line",
                "--file", "wiki/concepts/_index.md",
                "--section", "現行コンセプトカタログ",
                "--line", "- [[Bravo]]",
            ],
            self.vault,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        text = (self.vault / "wiki/concepts/_index.md").read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.startswith("- [[")]
        self.assertEqual(lines, ["- [[Alpha]]", "- [[Bravo]]", "- [[Charlie]]"])

        dup = run_script(
            "wiki-catalog.py",
            [
                "add-catalog-line",
                "--file", "wiki/concepts/_index.md",
                "--section", "現行コンセプトカタログ",
                "--line", "- [[Alpha|display]]",
            ],
            self.vault,
        )
        self.assertEqual(dup.returncode, 0, dup.stderr)
        again = (self.vault / "wiki/concepts/_index.md").read_text(encoding="utf-8")
        self.assertEqual(again.count("[[Alpha"), 1)
        self.assertEqual(again, text)

    def test_concept_stats_promote_children_once(self):
        self.write("wiki/concepts/ChildA.md", """---
type: concept
title: "ChildA"
---

# ChildA

## 定義

child a
""")
        self.write("wiki/concepts/ChildB.md", """---
type: concept
title: "ChildB"
---

# ChildB

## 定義

child b
""")
        padding = "x" * 400
        self.write("wiki/concepts/Parent.md", f"""---
type: concept
title: "Parent"
related:
  - "[[ChildB]]"
  - "[[ChildA]]"
  - "[[@2024__Skip__Me]]"
  - "[[MissingChild]]"
---

# Parent

## 定義

{padding}

## 横断的知見

- insight must stay put
""")
        args = [
            "--json",
            "--min-bytes", "200",
            "--min-lines", "50",
            "--promote-children",
            "--limit", "20",
        ]
        first = run_script("wiki-concept-stats.py", args, self.vault)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("updated 1 file(s)", first.stderr)
        rows = json.loads(first.stdout)
        parent = next(r for r in rows if r["path"] == "wiki/concepts/Parent.md")
        self.assertTrue(parent["has_child_section"])
        self.assertEqual(parent["child_candidates"], ["ChildA", "ChildB"])

        body = (self.vault / "wiki/concepts/Parent.md").read_text(encoding="utf-8")
        self.assertEqual(body.count("## 子概念"), 1)
        def_at = body.index("## 定義")
        child_at = body.index("## 子概念")
        insight_at = body.index("## 横断的知見")
        self.assertLess(def_at, child_at)
        self.assertLess(child_at, insight_at)
        self.assertIn("- [[ChildA]]", body)
        self.assertIn("- [[ChildB]]", body)
        self.assertIn("- insight must stay put", body)
        child_block = body[child_at:insight_at]
        self.assertLess(child_block.index("ChildA"), child_block.index("ChildB"))

        second = run_script("wiki-concept-stats.py", args, self.vault)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("updated 0 file(s)", second.stderr)
        self.assertEqual((self.vault / "wiki/concepts/Parent.md").read_text(encoding="utf-8").count("## 子概念"), 1)

    def test_promote_skips_hub_sized_related(self):
        padding = "x" * 400
        self.write("wiki/concepts/Hub.md", f"""---
type: concept
title: "Hub"
related:
  - "[[FatPeer]]"
  - "[[ChildOnly]]"
---

# Hub

## 定義

{padding}
""")
        self.write("wiki/concepts/FatPeer.md", f"""---
type: concept
title: "FatPeer"
related: []
---

# FatPeer

{padding}
""")
        self.write("wiki/concepts/ChildOnly.md", """---
type: concept
title: "ChildOnly"
related:
  - "[[Unrelated]]"
---

# ChildOnly
""")
        proc = run_script(
            "wiki-concept-stats.py",
            ["--min-bytes", "200", "--min-lines", "50", "--promote-children"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = (self.vault / "wiki/concepts/Hub.md").read_text(encoding="utf-8")
        self.assertIn("- [[ChildOnly]]", body)
        self.assertNotIn("- [[FatPeer]]", body)

    def test_thin_reciprocal_stays_child(self):
        padding = "x" * 400
        self.write("wiki/concepts/Hub.md", f"""---
type: concept
title: "Hub"
related:
  - "[[FatPeer]]"
  - "[[ThinBack]]"
---

# Hub

## 定義

{padding}
""")
        self.write("wiki/concepts/FatPeer.md", f"""---
type: concept
title: "FatPeer"
related:
  - "[[Hub]]"
---

# FatPeer

{padding}
""")
        self.write("wiki/concepts/ThinBack.md", """---
type: concept
title: "ThinBack"
related:
  - "[[Hub]]"
---

# ThinBack
""")
        proc = run_script(
            "wiki-concept-stats.py",
            ["--min-bytes", "200", "--min-lines", "50", "--promote-children"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = (self.vault / "wiki/concepts/Hub.md").read_text(encoding="utf-8")
        self.assertIn("- [[ThinBack]]", body)
        self.assertNotIn("- [[FatPeer]]", body)

    def test_excerpt_budget_and_empty_body(self):
        long_bullets = "\n".join(f"- bullet-{i} " + ("漢" * 80) for i in range(20))
        self.write("wiki/concepts/LongHub.md", f"""---
type: concept
title: "LongHub"
---

# LongHub

## 定義

first paragraph stays.

second paragraph of definition should drop under a tight budget.

## 横断的知見

{long_bullets}
""")
        tight = run_script(
            "wiki-excerpt.py",
            ["wiki/concepts/LongHub.md", "--tail", "15", "--budget-tokens", "200"],
            self.vault,
        )
        self.assertEqual(tight.returncode, 0, tight.stderr)
        self.assertLessEqual(max(1, len(tight.stdout) // 3), 280)
        self.assertIn("excerpt-truncated", tight.stdout)
        self.assertNotIn("second paragraph of definition", tight.stdout)

        empty = run_script("wiki-excerpt.py", ["wiki/concepts/MissingNo.md"], self.vault)
        self.assertEqual(empty.returncode, 3)
        self.write("wiki/concepts/Bare.md", "# Bare\n\nno sections here\n")
        bare = run_script("wiki-excerpt.py", ["wiki/concepts/Bare.md"], self.vault)
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertIn("---", bare.stdout)

    def test_resolve_at_source_filename(self):
        self.write(
            "wiki/sources/@2024__SIGCOMM__Congestion.md",
            """---
type: source
title: "Congestion Control for RDMA"
---

# Congestion
""",
        )
        proc = run_script(
            "wiki-resolve.py",
            ["@2024__SIGCOMM__Congestion", "--type", "source", "--top", "3"],
            self.vault,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        hits = json.loads(proc.stdout)["candidates"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["path"], "wiki/sources/@2024__SIGCOMM__Congestion.md")

    def test_concurrent_prepend_log_keeps_both(self):
        self.write("wiki/log.md", "## [2026-01-01] seed\n- old\n")
        procs = []
        for i in (1, 2):
            procs.append(
                run_script(
                    "wiki-catalog.py",
                    ["prepend-log", "--text", f"## [2026-08-30] concurrent-{i}\n- n={i}\n"],
                    self.vault,
                )
            )
        # Sequential calls still take the lock; overlap via threads:
        import threading
        self.write("wiki/log.md", "## [2026-01-01] seed\n- old\n")
        results = []

        def worker(n):
            results.append(
                run_script(
                    "wiki-catalog.py",
                    ["prepend-log", "--text", f"## [2026-08-30] concurrent-{n}\n- n={n}\n"],
                    self.vault,
                )
            )

        threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(p.returncode == 0 for p in results), [p.stderr for p in results])
        text = (self.vault / "wiki/log.md").read_text(encoding="utf-8")
        self.assertIn("concurrent-1", text)
        self.assertIn("concurrent-2", text)
        self.assertIn("seed", text)

    def test_refresh_nonzero_when_child_fails(self):
        proc = run_script(
            "wiki-retrieve-refresh.py",
            ["--pages", "wiki/concepts/DoesNotExist.md"],
            self.vault,
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("bm25_ok", proc.stdout)
        self.assertIn("is not a readable file", proc.stderr)

    def test_refresh_accepts_vault_relative_page(self):
        self.write("wiki/concepts/Ok.md", "# Ok\n\nbody for chunking.\n")
        proc = run_script(
            "wiki-retrieve-refresh.py",
            ["--pages", "wiki/concepts/Ok.md"],
            self.vault,
        )
        self.assertNotIn("resolves outside the vault", proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["bm25_ok"])


if __name__ == "__main__":
    unittest.main()
