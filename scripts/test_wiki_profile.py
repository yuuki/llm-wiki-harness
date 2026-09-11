#!/usr/bin/env python3
"""wiki-profile.py のテスト。

    python3 scripts/test_wiki_profile.py
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

PROFILE = """---
title: 研究関心プロファイル
生成日: 2026-07-08
---

# 研究関心プロファイル

前置き。

研究の背骨は 4 本柱である(`research/vision/x.md`)。
1. テレメトリワークロードのスケーリング(計測・保存・分析)
2. SRE エージェント
3. 自動スケーリング
4. AI インフラの可観測性

---

## コア関心(80+)

- **根本原因分析**。説明。
- **可観測性・テレメトリ工学**。説明。
- **eBPF**。説明。
- **時系列異常検知**。説明。

## 周辺関心(60-79)

- **障害の科学**。説明。

## 対象外(40 未満)

- **セキュリティ**(脆弱性検出)。説明。
- **MLOps**。説明。

## 方法論・視点の好み

- **実運用規模での評価を重視**。説明。
- 太字でない行は拾わない。

## 査読なし論文の例外基準

- 本文。
"""


def load_module():
    spec = importlib.util.spec_from_file_location("wiki_profile", SCRIPTS / "wiki-profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WikiProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "research/curation").mkdir(parents=True)
        os.environ["WIKI_VAULT_ROOT"] = str(self.root)
        self.m = load_module()

    def tearDown(self):
        os.environ.pop("WIKI_VAULT_ROOT", None)
        os.environ.pop("WIKI_PROFILE_PATH", None)
        self.tmp.cleanup()

    def run_cmd(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.m.main(list(argv))
        return code, buf.getvalue()

    def test_missing_profile_exits_3(self):
        self.assertEqual(self.run_cmd()[0], 3)

    def test_parse_and_render(self):
        (self.root / "research/curation/profile.md").write_text(PROFILE, encoding="utf-8")
        code, out = self.run_cmd("--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["generated"], "2026-07-08")
        self.assertEqual(d["pillars"], ["テレメトリワークロードのスケーリング", "SRE エージェント", "自動スケーリング", "AI インフラの可観測性"])
        self.assertEqual(d["core"], ["根本原因分析", "可観測性・テレメトリ工学", "eBPF", "時系列異常検知"])
        self.assertEqual(d["adjacent"], ["障害の科学"])
        self.assertEqual(d["out_of_scope"], ["セキュリティ", "MLOps"])
        self.assertEqual(d["method"], ["実運用規模での評価を重視"])
        code, text = self.run_cmd()
        lines = text.rstrip("\n").split("\n")
        self.assertLessEqual(len(lines), 40)
        self.assertIn("## 研究の背骨", text)
        self.assertIn("- 根本原因分析、可観測性・テレメトリ工学、eBPF", text)
        self.assertIn("- セキュリティ、MLOps", text)
        self.assertNotIn("査読なし", text)
        code, text = self.run_cmd("--lines", "12")
        self.assertLessEqual(len(text.rstrip("\n").split("\n")), 12)

    def test_fallback_wiki_meta(self):
        dest = self.root / "wiki/meta/profile.md"
        dest.parent.mkdir(parents=True)
        dest.write_text(PROFILE, encoding="utf-8")
        code, out = self.run_cmd("--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["core"][0], "根本原因分析")
        code, text = self.run_cmd()
        self.assertIn("wiki/meta/profile.md", text)

    def test_env_overrides_default(self):
        (self.root / "research/curation/profile.md").write_text(PROFILE, encoding="utf-8")
        custom = self.root / "elsewhere/interest.md"
        custom.parent.mkdir()
        custom.write_text(PROFILE.replace("eBPF", "カスタム軸"), encoding="utf-8")
        os.environ["WIKI_PROFILE_PATH"] = str(custom)
        code, out = self.run_cmd("--json")
        self.assertEqual(code, 0)
        self.assertIn("カスタム軸", json.loads(out)["core"])


if __name__ == "__main__":
    unittest.main()
