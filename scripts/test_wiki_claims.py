#!/usr/bin/env python3
"""wiki_claims.extract_claims の試験。実 wiki は触らない。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

PAGE = """---
type: concept
title: "X"
---

# X

## 定義

定義段落。

## 性質

- **[[H]] は [[I]] と対になり [[@2022__Z__S3]] は出典である。** (Source: [[@2022__Z__S3]])
  - 根拠: [[@2022__Z__S3]] — 本文の [[Ghost]] は targets に入れない
  - 反証: [[Nope]]
- **出典の無い命題は対象にならない。**

## 未編纂の観察

- [[H]] への言及。(Source: [[@2022__Z__S3]])
- 出典無しの観察。

## 出典
"""


class WikiClaimsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        (self.vault / "wiki/concepts").mkdir(parents=True)
        self.rel = "wiki/concepts/X.md"
        (self.vault / self.rel).write_text(PAGE, encoding="utf-8")
        if "wiki_claims" in sys.modules:
            del sys.modules["wiki_claims"]
        import wiki_claims
        self.wc = wiki_claims

    def tearDown(self):
        self.tmp.cleanup()

    def test_targets_are_non_at_links_on_bold_only(self):
        claims = self.wc.extract_claims(self.rel)
        self.assertEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(c["kind"], "proposition")
        self.assertEqual(c["section"], "性質")
        self.assertEqual(c["targets"], ["H", "I"])
        self.assertEqual(c["sources"], ["@2022__Z__S3"])
        self.assertNotIn("Ghost", c["targets"])
        self.assertNotIn("Nope", c["targets"])
        self.assertTrue(all(not t.startswith("@") for t in c["targets"]))

    def test_inbox_excluded_unless_flag(self):
        claims = self.wc.extract_claims(self.rel, include_inbox=True)
        kinds = [c["kind"] for c in claims]
        self.assertEqual(kinds.count("observation"), 1)
        obs = next(c for c in claims if c["kind"] == "observation")
        self.assertEqual(obs["targets"], [])
        self.assertIn("@2022__Z__S3", obs["sources"])

    def test_text_kwarg_does_not_reread_disk(self):
        alt = PAGE.replace("[[H]] は [[I]]", "[[Only]] は [[Here]]")
        (self.vault / self.rel).write_text(
            PAGE.replace("## 性質", "## 性質\n\n- **[[Disk]] だけ。** (Source: [[@2022__Z__S3]])\n"),
            encoding="utf-8",
        )
        claims = self.wc.extract_claims(self.rel, text=alt)
        self.assertEqual(claims[0]["targets"], ["Only", "Here"])
        self.assertNotIn("Disk", claims[0]["targets"])


if __name__ == "__main__":
    unittest.main()
