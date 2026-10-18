#!/usr/bin/env python3
"""pdf-reading-order.py の並べ替え規則の単体試験。PyMuPDF は要らない。実 PDF は開かない。

  python3 scripts/test_pdf_reading_order.py
"""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pdf_reading_order", Path(__file__).resolve().parent / "pdf-reading-order.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

W = 612.0  # US Letter の幅。段は 54–300 と 312–558


def texts(blocks):
    return [b[4] for b in MOD.order_blocks(blocks, W)]


class OrderCase(unittest.TestCase):
    def test_two_columns_read_left_then_right(self):
        blocks = [
            (54, 72, 558, 90, "title"),
            (312, 100, 558, 300, "right-1"),
            (54, 100, 300, 200, "abstract"),
            (54, 210, 300, 400, "intro-left"),
            (312, 310, 558, 500, "right-2"),
        ]
        self.assertEqual(texts(blocks), ["title", "abstract", "intro-left", "right-1", "right-2"])

    def test_full_width_figure_splits_bands(self):
        blocks = [
            (54, 72, 300, 200, "L-top"),
            (312, 72, 558, 200, "R-top"),
            (54, 210, 558, 400, "figure caption"),
            (54, 410, 300, 700, "L-bottom"),
            (312, 410, 558, 700, "R-bottom"),
        ]
        self.assertEqual(texts(blocks), ["L-top", "R-top", "figure caption", "L-bottom", "R-bottom"])

    def test_single_column_is_top_to_bottom(self):
        blocks = [
            (72, 300, 540, 400, "para-2"),
            (72, 100, 200, 120, "1 Introduction"),
            (72, 130, 540, 290, "para-1"),
        ]
        self.assertEqual(texts(blocks), ["1 Introduction", "para-1", "para-2"])

    def test_ligatures_are_expanded_but_superscripts_kept(self):
        out = MOD.page_text([(54, 72, 558, 90, "e\ufb03ciency of 10\u2079 ops")], W)
        self.assertEqual(out, "efficiency of 10\u2079 ops")

    def test_control_chars_become_replacement_so_pages_stay_separated(self):
        out = MOD.page_text([(54, 72, 558, 90, "shuffles \x02.\x07 TB\x0c")], W)
        self.assertEqual(out, "shuffles \ufffd.\ufffd TB\ufffd")
        self.assertNotIn("\f", out)

    def test_superscript_words_get_caret(self):
        words = [(100, 90, 110, 100, "10"), (110.5, 87, 114, 94, "3"), (117, 90, 134, 100, "ops")]
        self.assertEqual(MOD.line_text(words), "10^3 ops")

    def test_abutting_glyph_words_are_joined(self):
        words = [(100, 90, 105, 100, "0"), (105.2, 90, 107, 100, "."), (107.1, 90, 125, 100, "0058"),
                 (128, 90, 132, 100, ".")]
        self.assertEqual(MOD.line_text(words), "0.0058 .")

    def test_spaced_math_italic_is_not_superscript(self):
        words = [(100, 90, 120, 100, "into"), (124, 87, 130, 94, "\U0001d440"), (133, 90, 150, 100, "parts")]
        self.assertEqual(MOD.line_text(words), "into \U0001d440 parts")

    def test_parse_bbox_layout_orders_columns(self):
        x = ('<html xmlns="http://www.w3.org/1999/xhtml"><body><doc><page width="612" height="792"><flow>'
             '<block xMin="312" yMin="100" xMax="558" yMax="120"><line xMin="312" yMin="100" xMax="558" yMax="120">'
             '<word xMin="312" yMin="100" xMax="400" yMax="120">right</word></line></block>'
             '<block xMin="54" yMin="100" xMax="300" yMax="120"><line xMin="54" yMin="100" xMax="300" yMax="120">'
             '<word xMin="54" yMin="100" xMax="100" yMax="120">left</word>'
             '<word xMin="104" yMin="100" xMax="150" yMax="120">col</word></line></block>'
             '</flow></page></doc></body></html>')
        width, blocks = MOD.parse_bbox_layout(x)[0]
        self.assertEqual(MOD.page_text(blocks, width), "left col\n\nright")

if __name__ == "__main__":
    unittest.main()
