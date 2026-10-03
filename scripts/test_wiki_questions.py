#!/usr/bin/env python3
"""wiki_questions の試験。問い節の項目が問いか、紛れた命題かの判定。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from wiki_questions import is_asserted_proposition, is_question_like, is_stray_assertion  # noqa: E402


class WikiQuestionsTest(unittest.TestCase):
    def test_questions_and_open_points_pass(self):
        for text in (
            "- **遅延モデルは QUIC でも成り立つか**: 背景(Source: [[@2026__X__Y]])",
            "- **前処理による因果経路の欠落**: どの程度失わせるか。評価が必要である。(Source: [[@2022__X__Y]])",
            "- LagRCA と PCTS の直接比較は未実施である(Source: [[@2025__X__Y]])",
            "- 背景を述べる文である。QUIC 普及後の代替は何か。([[@2026__X__Y]])",
            "- コミュニティ検出の信頼できるベンチマークは未整備である(出典: [[@2010__X__Y]])",
        ):
            self.assertTrue(is_question_like(text), text)
            self.assertFalse(is_stray_assertion(text), text)

    def test_cited_assertions_are_stray(self):
        text = "- **LatentScope は潜在変数問題を実用化した**: RCD が解けなかった問題を扱う(Source: [[@2026__X__Y]])"
        self.assertTrue(is_stray_assertion(text))
        self.assertTrue(is_asserted_proposition(text))
        plain = "- PetShop は正常期間で全手法が捏造を示す(Source: [[@2026__X__Y]])"
        self.assertTrue(is_stray_assertion(plain))
        self.assertFalse(is_asserted_proposition(plain), "太字の頭が無ければ拒否せず警告に留める")

    def test_leading_assertion_with_open_tail_is_stray(self):
        for text in (
            "- **LatentScope は潜在変数問題を実用化した**: 本番での効果は未解明である(Source: [[@2026__X__Y]])",
            "- 1T 級 MoE では 220 対 540 TFLOP/s/GPU の差が出た(Source: [[@2025__X__Y]])。差の原因は未解明である。",
        ):
            self.assertTrue(is_stray_assertion(text), text)

    def test_source_as_subject_assertion_is_stray(self):
        # 出典を主語にして報告の動詞で言い切る文は、後ろに問いや未解決点が続いても断定である
        for text in (
            "- Gandalf([[@2020__NSDI__Gandalf]])は 1 日に複数回デプロイするクラスタが約 45% と報告するが、"
            "この頻度で帰責が成り立つかは未検証である。",
            "- Gandalf([[@2020__NSDI__Gandalf]])は約 45% と報告する。他のクラウドでも同じ頻度か?",
            "- [[wiki/sources/@2020__NSDI__Gandalf|Gandalf]] が 1 日数百件のロールアウトを示した。代替は何か。",
            "- **帰責の粒度**: Gandalf(Source: [[@2020__NSDI__Gandalf]])は時空間相関で犯人を特定した。"
            "完了済み変更への適用は未検証である。",
        ):
            self.assertTrue(is_stray_assertion(text), text)
            self.assertFalse(is_asserted_proposition(text), "太字の頭で言い切っていなければ追記は拒まない")

    def test_source_subject_in_subordinate_or_negative_clause_stays_question(self):
        # 出典を主語にしても、動詞が連体・従属で続く形と、報告していないことを述べる形は問いのまま
        for text in (
            "- [[@2020__NSDI__Gandalf|Gandalf]] が示した 45% は他社でも成り立つか。",
            "- Gandalf([[@2020__NSDI__Gandalf]])が指摘するように、完了済み変更への対応は未検証である。",
            "- Gandalf([[@2020__NSDI__Gandalf]])と HALO の比較は未検証である。",
            "- Gandalf([[@2020__NSDI__Gandalf]])は本番の頻度を報告していない。",
        ):
            self.assertFalse(is_stray_assertion(text), text)

    def test_open_points_with_citation_stay_questions(self):
        for text in (
            "- **本番での再現は未検証である**: 背景(Source: [[@2026__X__Y]])",
            "- 背景の事実を述べた(Source: [[@2026__X__Y]])。本番で成り立つかは未検証である。代替は何か。",
        ):
            self.assertFalse(is_stray_assertion(text), text)

    def test_uncited_items_are_never_stray(self):
        self.assertFalse(is_stray_assertion("- **X は Y を実現した**: 説明"))


class ConceptStatsQuestionDebtTest(unittest.TestCase):
    def test_question_assertions_count_toward_compile_debt(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "wiki/concepts").mkdir(parents=True)
            stray = "\n".join(f"- **手法 {i} は精度を改善した**: 説明(Source: [[@2026__X__S{i}]])" for i in range(5))
            (vault / "wiki/concepts/P.md").write_text(
                f"---\ntitle: P\n---\n\n## 定義\n\nd.\n\n## 主題\n\n- **命題。**\n\n## 未解決の問い\n\n{stray}\n"
                "- 残る問いは何か。\n\n## 未編纂の観察\n\n", encoding="utf-8")
            env = dict(os.environ, WIKI_VAULT_ROOT=str(vault))
            proc = subprocess.run([sys.executable, str(SCRIPTS / "wiki-concept-stats.py"), "--compile-debt",
                                   "--page", "P"], capture_output=True, text=True, env=env, check=False)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            row = json.loads(proc.stdout)
            self.assertEqual(row["inbox_bullets"], 0)
            self.assertEqual(row["question_assertions"], 5)
            self.assertTrue(row["in_debt"])


if __name__ == "__main__":
    unittest.main()
