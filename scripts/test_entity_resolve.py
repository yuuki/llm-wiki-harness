#!/usr/bin/env python3
"""entity-resolve.py のテスト。一時 vault(WIKI_VAULT_ROOT)で走らせる。

    python3 scripts/test_entity_resolve.py
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


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def entity(title, etype, aliases=(), related=()):
    al = "".join(f'  - "{a}"\n' for a in aliases)
    rel = "".join(f'  - "{r}"\n' for r in related)
    return (
        "---\n"
        'address: "c-000001"\n'
        "type: entity\n"
        f'title: "{title}"\n'
        f"entity_type: {etype}\n"
        f"aliases:\n{al}" if aliases else
        "---\n"
        'address: "c-000001"\n'
        "type: entity\n"
        f'title: "{title}"\n'
        f"entity_type: {etype}\n"
    ) + (f"related:\n{rel}" if related else "") + (
        "date: 2026-01-01 10:00\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
        "tags:\n  - 2026/01/01\nstatus: seed\n---\n\n"
        f"# {title}\n\n本文。\n"
    )


class EntityResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wiki/entities").mkdir(parents=True)
        (self.root / "wiki/concepts").mkdir(parents=True)
        (self.root / ".vault-meta").mkdir()
        os.environ["WIKI_VAULT_ROOT"] = str(self.root)
        self.m = load_module("entity_resolve", "entity-resolve.py")

    def tearDown(self):
        os.environ.pop("WIKI_VAULT_ROOT", None)
        self.tmp.cleanup()

    def write(self, name, text, sub="wiki/entities"):
        (self.root / sub / f"{name}.md").write_text(text, encoding="utf-8")

    def run_cmd(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.m.main(list(argv))
        return code, buf.getvalue()

    def pairs(self, **kw):
        code, out = self.run_cmd("scan", *[f"--{k.replace('_', '-')}" if v is True else f"--{k.replace('_', '-')}={v}" for k, v in kw.items()])
        self.assertEqual(code, 0)
        d = json.loads(out)
        return {(p["a"]["title"], p["b"]["title"]): p for p in d["items"]}

    def find(self, pairs, x, y):
        return pairs.get((x, y)) or pairs.get((y, x))

    def test_tiers(self):
        # strong: 正規化一致、和英 alias、法人格
        self.write("Intel Corporation", entity("Intel Corporation", "organization"))
        self.write("Intel", entity("Intel", "organization"))
        self.write("Yuuki Tsubouchi", entity("Yuuki Tsubouchi", "person", aliases=["坪内佑樹"]))
        self.write("坪内佑樹", entity("坪内佑樹", "person"))
        # 部分名 alias(姓だけ)と `Y. Li` 型は無視 → 同姓の別人はつながらない
        self.write("Ying Li", entity("Ying Li", "person", aliases=["Li", "Y. Li"]))
        self.write("Yichen Li", entity("Yichen Li", "person", aliases=["Li", "Y. Li"]))
        # medium: イニシャル対フル、ミドルネーム、姓名順、語順、括弧略称、短い略称の衝突
        self.write("J. Dean", entity("J. Dean", "person"))
        self.write("Jeff Dean", entity("Jeff Dean", "person"))
        self.write("Hongqiang Harry Liu", entity("Hongqiang Harry Liu", "person"))
        self.write("Hongqiang Liu", entity("Hongqiang Liu", "person"))
        self.write("Massachusetts Institute of Technology (MIT)", entity("Massachusetts Institute of Technology (MIT)", "organization"))
        self.write("MIT", entity("MIT", "organization"))
        self.write("Karlsruhe Institute of Technology", entity("Karlsruhe Institute of Technology", "organization", aliases=["KIT"]))
        self.write("金沢工業大学", entity("金沢工業大学", "organization", aliases=["KIT"]))
        # ハイフン名は割らない → I-Ting と Inhwan はつながらない
        self.write("I-Ting Angelina Lee", entity("I-Ting Angelina Lee", "person"))
        self.write("Inhwan Lee", entity("Inhwan Lee", "person"))
        # weak: 綴りゆれ
        self.write("Northeastern University (USA)", entity("Northeastern University (USA)", "organization"))
        self.write("Northeastern University", entity("Northeastern University", "organization"))
        # 型が違う対は出さない
        self.write("Meta", entity("Meta", "organization"))
        self.write("Meta (concept person)", entity("Meta", "person"))

        p = self.pairs()
        self.assertEqual(self.find(p, "Intel Corporation", "Intel")["tier"], "strong")
        self.assertEqual(self.find(p, "Yuuki Tsubouchi", "坪内佑樹")["tier"], "strong")
        self.assertIsNone(self.find(p, "Ying Li", "Yichen Li"))
        self.assertEqual(self.find(p, "J. Dean", "Jeff Dean")["tier"], "medium")
        self.assertEqual(self.find(p, "Hongqiang Harry Liu", "Hongqiang Liu")["tier"], "medium")
        self.assertEqual(self.find(p, "Massachusetts Institute of Technology (MIT)", "MIT")["tier"], "medium")
        self.assertEqual(self.find(p, "Karlsruhe Institute of Technology", "金沢工業大学")["tier"], "medium")
        self.assertIsNone(self.find(p, "I-Ting Angelina Lee", "Inhwan Lee"))
        self.assertEqual(self.find(p, "Northeastern University (USA)", "Northeastern University")["tier"], "weak")
        self.assertIsNone(self.find(p, "Meta", "Meta"))
        # min-tier と type の絞り込み
        self.assertNotIn("weak", {x["tier"] for x in self.pairs(min_tier="medium").values()})
        self.assertTrue(all("person" in (x["a"]["entity_type"], x["b"]["entity_type"]) for x in self.pairs(type="person").values()))

    def test_ledger_and_plan(self):
        self.write("Intel Corporation", entity("Intel Corporation", "organization", aliases=["Intel Corp."], related=["[[x86]]"]))
        self.write("Intel", entity("Intel", "organization", related=["[[Xeon]]"]))
        self.write("Foo", "---\ntype: concept\ntitle: Foo\n---\n\n[[Intel Corporation]] が作った。\n", sub="wiki/concepts")
        code, out = self.run_cmd("plan", "--keep", "Intel", "--drop", "Intel Corporation")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(plan["aliases_to_add"], ["Intel Corporation", "Intel Corp."])
        self.assertEqual(plan["related_to_add"], ["[[x86]]"])
        if plan["pages_linking_to_drop"]:  # rg が無い環境では空
            self.assertEqual(plan["pages_linking_to_drop"], ["wiki/concepts/Foo.md"])
        # rejected → scan / report から消える。--all で戻る
        self.assertEqual(len(self.pairs()), 1)
        code, _ = self.run_cmd("decide", "--keep", "Intel", "--drop", "Intel Corporation", "--rejected", "--reason", "試験")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pairs()), 0)
        self.assertEqual(len(self.pairs(all=True)), 1)
        # merged だが drop が残っている → report に「統合が終わっていない」
        self.run_cmd("decide", "--keep", "Intel", "--drop", "Intel Corporation", "--merged")
        report = self.run_cmd("report")[1]
        self.assertIn("統合が終わっていない", report)
        self.assertIn("[[Intel]] ← [[Intel Corporation]]", report)
        (self.root / "wiki/entities/Intel Corporation.md").unlink()
        self.assertNotIn("統合が終わっていない", self.run_cmd("report")[1])
        ledger = json.loads((self.root / ".vault-meta/entity-merges.json").read_text(encoding="utf-8"))
        self.assertEqual(list(ledger["decisions"].values())[0]["state"], "merged")


if __name__ == "__main__":
    unittest.main()
