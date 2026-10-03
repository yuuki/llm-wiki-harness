#!/usr/bin/env python3
"""wiki-survey-recommend.py のテスト。

    python3 scripts/test_wiki_survey_recommend.py
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
    spec = importlib.util.spec_from_file_location(
        "wiki_survey_recommend", SCRIPTS / "wiki-survey-recommend.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def concept(title, sources, *, aliases=None, inbox=None, unresolved=None, related=None):
    lines = ["---", "type: concept", f'title: "{title}"']
    if aliases:
        lines.append("aliases:")
        for a in aliases:
            lines.append(f'  - "{a}"')
    if related:
        lines.append("related:")
        for r in related:
            lines.append(f'  - "[[{r}]]"')
    lines.append("sources:")
    for s in sources:
        lines.append(f'  - "[[{s}]]"')
    lines.extend(["---", "", f"# {title}", "", "## 定義", "", "定義。", ""])
    if unresolved:
        lines.extend(["## 未解決の問い", ""])
        for q in unresolved:
            lines.append(f"- {q}")
        lines.append("")
    if inbox:
        heading = "横断的知見" if inbox.get("legacy") else "未編纂の観察"
        lines.extend([f"## {heading}", ""])
        for i in range(inbox["n"]):
            lines.append(f"- 観察 {i+1}。")
        lines.append("")
    return "\n".join(lines) + "\n"


def survey(title, question, *, related=None, aliases=None, sources=None):
    lines = ["---", "type: survey", f'title: "{title}"', f'question: "{question}"']
    if aliases:
        lines.append("aliases:")
        for a in aliases:
            lines.append(f'  - "{a}"')
    if related:
        lines.append("related:")
        for r in related:
            lines.append(f'  - "[[{r}]]"')
    lines.append("sources:")
    for s in sources or ["[[@s01]]"]:
        lines.append(f'  - "{s}"' if s.startswith('"') else f'  - "{s}"')
    lines.extend(["---", "", f"# {title}", ""])
    return "\n".join(lines) + "\n"


PROFILE = """---
title: 研究関心プロファイル
生成日: 2026-09-11
---

# 研究関心プロファイル

研究の背骨は 4 本柱である。
1. テレメトリワークロードのスケーリング
2. SRE エージェント

## コア関心

- **根本原因分析**。説明。

## 周辺関心

- **障害の科学**。説明。

## 対象外

- **セキュリティ**。説明。

## 方法論・視点の好み

- **実運用規模での評価を重視**。説明。
"""

CLUSTERS = {
    "built_at": "2026-09-16T11:17:04",
    "topology_sha256": "abc",
    "clusters": [
        {
            "id": 0,
            "label": "根本原因分析 / 異常検知",
            "size": 3,
            "top_members": [
                {
                    "path": "wiki/concepts/根本原因分析.md",
                    "title": "根本原因分析",
                    "in_count": 10,
                    "type": "concept",
                }
            ],
        },
        {
            "id": 11,
            "label": "サービスレベル目標 / エラーバジェット",
            "size": 2,
            "top_members": [],
        },
    ],
    "membership": {
        "wiki/concepts/根本原因分析.md": {
            "id": 0, "type": "concept", "title": "根本原因分析", "in_count": 10,
        },
        "wiki/concepts/サービスレベル目標.md": {
            "id": 11, "type": "concept", "title": "サービスレベル目標", "in_count": 4,
        },
    },
}


class WikiSurveyRecommendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wiki/concepts").mkdir(parents=True)
        (self.root / "wiki/surveys").mkdir(parents=True)
        (self.root / "research/curation").mkdir(parents=True)
        (self.root / ".vault-meta").mkdir(parents=True)
        fifteen = [f"@s{i:02d}" for i in range(1, 16)]
        fourteen = [f"@t{i:02d}" for i in range(1, 15)]
        (self.root / "wiki/concepts/根本原因分析.md").write_text(
            concept("根本原因分析", fifteen + ["@s01"], unresolved=["軸は何か。", "緩和は前提か。"]),
            encoding="utf-8",
        )
        (self.root / "wiki/concepts/薄い概念.md").write_text(
            concept("薄い概念", ["@a", "@b", "@c"]), encoding="utf-8",
        )
        (self.root / "wiki/concepts/分散トレーシング.md").write_text(
            concept("分散トレーシング", fourteen, inbox={"n": 6, "legacy": True}),
            encoding="utf-8",
        )
        (self.root / "wiki/concepts/グレイ障害.md").write_text(
            concept("グレイ障害", [f"@g{i:02d}" for i in range(1, 10)]), encoding="utf-8",
        )
        (self.root / "wiki/surveys/根本原因分析の教科書.md").write_text(
            survey("根本原因分析の教科書", "RCA の教科書を作成して", sources=["[[@s01]]", "[[@s02]]"]),
            encoding="utf-8",
        )
        (self.root / "research/curation/profile.md").write_text(PROFILE, encoding="utf-8")
        (self.root / ".vault-meta/clusters.json").write_text(
            json.dumps(CLUSTERS, ensure_ascii=False), encoding="utf-8"
        )
        (self.root / "wiki/concepts/_index.md").write_text("# ignore\n", encoding="utf-8")
        os.environ["WIKI_VAULT_ROOT"] = str(self.root)
        self.m = load_module()

    def tearDown(self):
        os.environ.pop("WIKI_VAULT_ROOT", None)
        self.tmp.cleanup()

    def collect(self, **kwargs):
        return self.m.collect(self.root, **kwargs)

    def test_counts_unique_sources_not_related(self):
        payload = self.collect()
        rca = next(c for c in payload["dense"] if c["title"] == "根本原因分析")
        self.assertEqual(rca["n_sources"], 15)
        self.assertEqual(rca["n_documents"], 15)
        self.assertEqual(rca["n_unresolved"], 2)
        self.assertNotIn("related", rca)

    def test_does_not_use_excerpt_cap(self):
        payload = self.collect()
        rca = next(c for c in payload["dense"] if c["title"] == "根本原因分析")
        self.assertGreater(rca["n_sources"], 8)

    def test_bands_and_skips_index(self):
        payload = self.collect()
        titles = {c["title"] for c in payload["dense"] + payload["near"]}
        self.assertIn("根本原因分析", titles)
        self.assertIn("分散トレーシング", titles)
        self.assertIn("グレイ障害", titles)
        self.assertNotIn("薄い概念", titles)
        self.assertEqual(payload["counts"]["concepts"], 4)
        self.assertEqual(payload["counts"]["ge_min"], 2)
        self.assertEqual(payload["counts"]["near"], 1)
        self.assertTrue(all(not c["path"].endswith("_index.md") for c in payload["dense"]))

    def test_survey_hits_and_compile_debt(self):
        payload = self.collect()
        rca = next(c for c in payload["dense"] if c["title"] == "根本原因分析")
        debt = next(c for c in payload["dense"] if c["title"] == "分散トレーシング")
        self.assertEqual(rca["survey_hits"], ["wiki/surveys/根本原因分析の教科書.md"])
        self.assertGreaterEqual(debt["inbox_bullets"], 5)
        self.assertEqual(debt["topic_sections"], 0)
        self.assertTrue(debt["legacy_heading"])
        self.assertIn("分散トレーシング", {c["title"] for c in payload["compile_debt"]})
        self.assertEqual(payload["surveys"][0]["question"], "RCA の教科書を作成して")
        self.assertEqual(payload["surveys"][0]["n_sources"], 2)

    def test_profile_and_clusters_without_reading_catalogs(self):
        payload = self.collect()
        self.assertEqual(payload["profile"]["pillars"][0], "テレメトリワークロードのスケーリング")
        self.assertIn("根本原因分析", payload["profile"]["core"])
        self.assertEqual(payload["clusters"][0]["label"], "根本原因分析 / 異常検知")
        self.assertEqual(payload["clusters"][0]["top"], ["根本原因分析"])
        self.assertEqual(payload["gates"]["min_sources"], 13)
        rca = next(c for c in payload["dense"] if c["title"] == "根本原因分析")
        self.assertEqual(rca["cluster_id"], 0)
        self.assertEqual(rca["cluster_label"], "根本原因分析 / 異常検知")
        self.assertTrue(rca["on_backbone"])
        self.assertEqual(rca["profile_fit"], "core")

    def test_cli_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.m.main([])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["counts"]["surveys"], 1)
        self.assertIn("ge_min", payload["counts"])
        self.assertNotIn("ge13", payload["counts"])

    def test_missing_clusters_is_not_fatal(self):
        (self.root / ".vault-meta/clusters.json").unlink()
        payload = self.collect()
        self.assertEqual(payload["clusters"], [])
        self.assertIn("clusters_missing", payload["warnings"])
        rca = next(c for c in payload["dense"] if c["title"] == "根本原因分析")
        self.assertFalse(rca["on_backbone"])

    def test_slo_alias_hits_sli_slo_title(self):
        (self.root / "wiki/concepts/サービスレベル目標.md").write_text(
            concept("サービスレベル目標", [f"@x{i:02d}" for i in range(13)], aliases=["SLO", "SLI"]),
            encoding="utf-8",
        )
        (self.root / "wiki/surveys/SLI-SLO教科書.md").write_text(
            survey("SLI/SLO 教科書 — 基礎から応用まで", "SRE の SLI/SLO に関する教科書",
                   related=["サービスレベル目標"]),
            encoding="utf-8",
        )
        payload = self.collect()
        slo = next(c for c in payload["dense"] if c["title"] == "サービスレベル目標")
        self.assertEqual(slo["survey_hits"], ["wiki/surveys/SLI-SLO教科書.md"])
        self.assertEqual(slo["cluster_id"], 11)
        self.assertEqual(slo["cluster_label"], "サービスレベル目標 / エラーバジェット")

    def test_incident_synonym_and_related(self):
        (self.root / "wiki/concepts/インシデント管理.md").write_text(
            concept("インシデント管理", [f"@i{i:02d}" for i in range(13)]),
            encoding="utf-8",
        )
        (self.root / "wiki/surveys/インシデント対応の教科書.md").write_text(
            survey("インシデント対応の教科書", "SREのインシデント対応を体系化せよ",
                   related=["インシデント管理"]),
            encoding="utf-8",
        )
        payload = self.collect()
        row = next(c for c in payload["dense"] if c["title"] == "インシデント管理")
        self.assertEqual(row["survey_hits"], ["wiki/surveys/インシデント対応の教科書.md"])

    def test_rdma_watch_vs_general_rdma(self):
        (self.root / "wiki/concepts/RDMAネットワーク監視.md").write_text(
            concept("RDMAネットワーク監視", [f"@r{i:02d}" for i in range(13)],
                    aliases=["RDMA network monitoring"]),
            encoding="utf-8",
        )
        (self.root / "wiki/concepts/RDMA.md").write_text(
            concept("RDMA", [f"@d{i:02d}" for i in range(13)]),
            encoding="utf-8",
        )
        (self.root / "wiki/surveys/RDMAネットワークモニタリングの教科書.md").write_text(
            survey("RDMAネットワークモニタリングの教科書", "RDMAネットワークモニタリングについて",
                   related=["RDMAネットワーク監視", "RDMA"],
                   aliases=["RoCE監視の教科書", "RDMA network monitoring textbook"]),
            encoding="utf-8",
        )
        payload = self.collect()
        watch = next(c for c in payload["dense"] if c["title"] == "RDMAネットワーク監視")
        general = next(c for c in payload["dense"] if c["title"] == "RDMA")
        path = "wiki/surveys/RDMAネットワークモニタリングの教科書.md"
        self.assertEqual(watch["survey_hits"], [path])
        self.assertEqual(general["survey_hits"], [])
        self.assertEqual(general["survey_near"], [path])

    def test_chapter_fold_uses_documents_for_gate(self):
        chapters = [
            f"@1991__Wiley__The Art of Computer Systems Performance Analysis - Chapter {n} X"
            for n in range(14, 24)
        ] + [
            "@2012__Wiley__Practical Reliability Engineering - Chapter 11 Design of Experiments",
            "@2022__OReillyJapan__カオスエンジニアリング - Chapter 3 原則の全体像",
            "@2022__OReillyJapan__カオスエンジニアリング - Chapter 12 実験の選択",
        ]
        (self.root / "wiki/concepts/実験計画法.md").write_text(
            concept("実験計画法", chapters), encoding="utf-8",
        )
        payload = self.collect()
        self.assertNotIn("実験計画法", {c["title"] for c in payload["dense"]})
        payload = self.collect(near_min=2)
        row = next(c for c in payload["near"] if c["title"] == "実験計画法")
        self.assertEqual(row["n_sources"], 13)
        self.assertEqual(row["n_documents"], 3)

    def test_llm_training_related_is_near_not_hit(self):
        (self.root / "wiki/concepts/LLM学習モニタリング.md").write_text(
            concept("LLM学習モニタリング", [f"@m{i:02d}" for i in range(13)]),
            encoding="utf-8",
        )
        (self.root / "wiki/surveys/LLM学習インフラ実運用の教科書.md").write_text(
            survey("LLM学習インフラ実運用の教科書", "LLM学習インフラの実運用に関する教科書",
                   related=["LLM学習モニタリング", "GPUクラスタ運用"]),
            encoding="utf-8",
        )
        payload = self.collect()
        row = next(c for c in payload["dense"] if c["title"] == "LLM学習モニタリング")
        path = "wiki/surveys/LLM学習インフラ実運用の教科書.md"
        self.assertEqual(row["survey_hits"], [])
        self.assertEqual(row["survey_near"], [path])

    def test_missing_sources_and_profile(self):
        (self.root / "wiki/concepts/欠落.md").write_text(
            "---\ntype: concept\ntitle: 欠落\n---\n# 欠落\n", encoding="utf-8",
        )
        (self.root / "research/curation/profile.md").unlink()
        payload = self.collect()
        self.assertIn("profile_missing", payload["warnings"])
        self.assertTrue(all(c["title"] != "欠落" for c in payload["dense"] + payload["near"]))
        self.assertEqual(next(c for c in payload["dense"] if c["title"] == "根本原因分析")["profile_fit"], "unknown")

    def test_ge_min_follows_min_sources_flag(self):
        payload = self.collect(min_sources=20, near_min=8)
        self.assertEqual(payload["gates"]["min_sources"], 20)
        self.assertEqual(payload["counts"]["ge_min"], 0)
        self.assertEqual(payload["counts"]["near"], 3)

    def test_question_exclusion_is_not_identity(self):
        (self.root / "wiki/concepts/ポストモーテム.md").write_text(
            concept("ポストモーテム", [f"@p{i:02d}" for i in range(13)]),
            encoding="utf-8",
        )
        (self.root / "wiki/surveys/インシデント対応の教科書.md").write_text(
            survey("インシデント対応の教科書",
                   "SREのインシデント対応を体系化せよ。ポストモーテムは除外する。",
                   related=["インシデント管理"]),
            encoding="utf-8",
        )
        (self.root / "wiki/surveys/ポストモーテムの教科書.md").write_text(
            survey("ポストモーテムの教科書",
                   "ポストモーテムとインシデント分析に関する教科書を構成せよ。"),
            encoding="utf-8",
        )
        payload = self.collect()
        row = next(c for c in payload["dense"] if c["title"] == "ポストモーテム")
        self.assertEqual(row["survey_hits"], ["wiki/surveys/ポストモーテムの教科書.md"])
        self.assertNotIn("wiki/surveys/インシデント対応の教科書.md", row["survey_hits"])

    def test_clusters_stale_when_concept_newer(self):
        clusters = self.root / ".vault-meta/clusters.json"
        os.utime(clusters, (1_700_000_000, 1_700_000_000))
        newest = self.root / "wiki/concepts/根本原因分析.md"
        os.utime(newest, (1_800_000_000, 1_800_000_000))
        payload = self.collect()
        self.assertIn("clusters_stale", payload["warnings"])


if __name__ == "__main__":
    unittest.main()
