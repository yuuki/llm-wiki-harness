#!/usr/bin/env python3
"""scripts/crop-paper-figures.py の幾何・検索語の回帰テスト。

PyMuPDF は要らない。実 PDF は開かない。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MODULE_PATH = SCRIPTS / "crop-paper-figures.py"


def load_mod():
    spec = importlib.util.spec_from_file_location("crop_paper_figures", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = load_mod()


class CaptionQueryTests(unittest.TestCase):
    def test_figure_queries_use_colon_or_period(self):
        qs = C.caption_queries(2, table=False)
        self.assertIn("Figure 2:", qs)
        self.assertIn("Fig. 2:", qs)
        joined = " ".join(qs)
        self.assertNotIn("Figure 2 ", joined + "X")
        self.assertTrue(all(not q.endswith(" ") for q in qs))

    def test_body_mention_is_not_a_query(self):
        qs = C.caption_queries(2, table=False)
        self.assertFalse(any(q == "Figure 2 " or q == "Figure 2" for q in qs))

    def test_table_queries_reject_summarizes_pattern(self):
        qs = C.caption_queries(2, table=True)
        self.assertIn("Table 2:", qs)
        self.assertNotIn("Table 2 ", qs)


class CaptionLineGrowTests(unittest.TestCase):
    def test_two_column_same_baseline_stays_on_caption_side(self):
        # STRATUS: 右段キャプション。左段本文は同じ y でも捨てる。
        cap = (395.1, 298.8, 430.4, 308.6)
        frags = [
            (107.5, 298.8, 300.0, 308.6),
            (395.1, 298.8, 504.0, 308.6),
        ]
        x0, y0, x1, y1 = C.grow_caption_line(*cap, frags, 612.0)
        self.assertGreater(x0, 300)
        self.assertLess(x1, 520)
        self.assertAlmostEqual(y0, 298.8)

    def test_centered_caption_grows_across_mid(self):
        # OpenRCA: "Figure 3:" の直後にキャプション本文が続く。
        cap = (180.3, 485.5, 216.5, 495.5)
        frags = [
            (180.3, 485.5, 216.5, 495.5),
            (220.0, 485.5, 431.7, 495.5),
        ]
        x0, _y0, x1, _y1 = C.grow_caption_line(*cap, frags, 612.0)
        self.assertLess(x0, 190)
        self.assertGreater(x1, 420)

    def test_gutter_gap_stops_growth(self):
        cap = (49.0, 451.3, 70.0, 459.2)
        frags = [
            (49.0, 451.3, 300.0, 459.2),
            (312.0, 454.2, 563.0, 464.1),
        ]
        x0, _y0, x1, _y1 = C.grow_caption_line(*cap, frags, 612.0)
        self.assertLess(x1, 308)
        self.assertGreater(x1, 290)
        self.assertLess(x0, 55)


class FullWidthTests(unittest.TestCase):
    def test_column_caption_is_not_full_width(self):
        self.assertFalse(C.caption_is_full_width(312.0, 500.0, 612.0))

    def test_centered_caption_is_full_width(self):
        self.assertTrue(C.caption_is_full_width(180.3, 431.7, 612.0))

    def test_clamp_left_column(self):
        x0, x1 = C.clamp_column(40.0, 400.0, 180.0, 612.0, False)
        self.assertLess(x1, 306)

    def test_clamp_full_width(self):
        x0, x1 = C.clamp_column(40.0, 400.0, 180.0, 612.0, True)
        self.assertEqual(x0, 44.0)
        self.assertEqual(x1, 568.0)


class NameAndSizeTests(unittest.TestCase):
    def test_skip_slide_and_page_dumps(self):
        self.assertTrue(C.is_skip_name("slide-overview.png"))
        self.assertTrue(C.is_skip_name("fig-page003-01.png"))
        self.assertTrue(C.is_skip_name("poster.png"))
        self.assertFalse(C.is_skip_name("fig01-architecture.png"))

    def test_table_min_height_avoids_caption_only(self):
        self.assertGreaterEqual(C.min_render_height(True), 200)
        self.assertLess(C.min_render_height(False), 200)

    def test_acm_fullpage_size_flags_review(self):
        self.assertTrue(C.looks_like_body_leak(1286, 874))
        self.assertFalse(C.looks_like_body_leak(1236, 726))
        self.assertFalse(C.looks_like_body_leak(371, 239))

    def test_parse_id(self):
        kind, num, name = C.parse_id("fig:3:fig03-workflow.png")
        self.assertEqual(kind.lower()[:3], "fig")
        self.assertEqual(num, 3)
        self.assertEqual(name, "fig03-workflow.png")
        kind, num, name = C.parse_id("tab:2:table2-results.png")
        self.assertEqual(num, 2)
        self.assertTrue(kind.lower().startswith("tab"))


if __name__ == "__main__":
    unittest.main()
