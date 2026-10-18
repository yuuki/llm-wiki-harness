#!/usr/bin/env python3
"""pdf-text-quality.py の判定の単体試験。合成テキストだけを使い、実 PDF は読まない。

  python3 scripts/test_pdf_text_quality.py
"""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pdf_text_quality", Path(__file__).resolve().parent / "pdf-text-quality.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

PAGE = ("Our prototype shuffles 2.7 TB of data among 75 servers in 395 seconds. " * 30)


def doc(*pages):
    return "".join(p + "\n\f" for p in pages)


class AssessCase(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(MOD.assess(doc(PAGE, PAGE))["text_quality"], "ok")

    def test_none_for_scanned(self):
        r = MOD.assess(doc("", "", ""))
        self.assertEqual(r["text_quality"], "none")
        self.assertEqual(r["text_pages"], 3)

    def test_sparse_for_fragments(self):
        self.assertEqual(MOD.assess(doc("trendy hardware such as CCD memories " * 10, "", ""))["text_quality"], "sparse")

    def test_slides_are_not_sparse_but_can_be_none(self):
        thin = doc("XProf Exec Summary Challenge Lightweight profiling " * 4, "Solution " * 20)
        self.assertEqual(MOD.assess(thin)["text_quality"], "sparse")
        self.assertEqual(MOD.assess(thin, slides=True)["text_quality"], "ok")
        self.assertEqual(MOD.assess(doc("", ""), slides=True)["text_quality"], "none")

    def test_glyph_loss_for_pua_digits(self):
        lost = PAGE.replace("2", "\uf642").replace("7", "\uf647")
        self.assertEqual(MOD.assess(doc(lost, lost))["text_quality"], "glyph-loss")

    def test_glyph_loss_for_replacement_chars(self):
        lost = PAGE.replace("75", "\ufffd\ufffd")
        self.assertEqual(MOD.assess(doc(lost, lost))["text_quality"], "glyph-loss")

    def test_math_bracket_pieces_are_not_glyph_loss(self):
        brackets = PAGE + "\uf8ee\uf8f9\uf8ef" * 5
        self.assertEqual(MOD.assess(doc(brackets, PAGE))["text_quality"], "ok")


if __name__ == "__main__":
    unittest.main()
