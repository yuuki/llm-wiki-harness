#!/usr/bin/env python3
"""scripts/wiki-extract-check.py の単体試験。

一時ファイルだけを使い、実 wiki / `.raw` には触れない。

  python3 scripts/test_wiki_extract_check.py
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
CHECKER = SCRIPTS / "wiki-extract-check.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("wiki_extract_check", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _load_checker()


def valid_extract(quotes: str = "- 短い引用 (p.1)\n") -> str:
    parts = [
        "# extract ch-01 試験章",
        "",
        "## 一行要約",
        "",
        "試験用の一行要約である。",
        "",
    ]
    for title in CHECK.REQUIRED_H2:
        if title == "一行要約":
            continue
        parts.append("## " + title)
        parts.append("")
        if title == "引用(locator 付き)":
            parts.append(quotes.rstrip("\n"))
        else:
            parts.append("該当なし")
        parts.append("")
    return "\n".join(parts)


def run_checker(paths, extra=None):
    cmd = ["python3", str(CHECKER)]
    if extra:
        cmd.extend(extra)
    cmd.extend(str(p) for p in paths)
    return subprocess.run(cmd, capture_output=True, text=True)


class WikiExtractCheckTest(unittest.TestCase):
    def test_valid_extract_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ch-01.md"
            path.write_text(valid_extract(), encoding="utf-8")
            result = run_checker([path], extra=["--forbid-raw"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_missing_headings_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ch-02.md"
            path.write_text("# 違う見出し\n\n本文だけ\n", encoding="utf-8")
            result = run_checker([path])
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING", result.stdout)
        self.assertIn("# extract ch-NN <章題>", result.stdout)
        self.assertIn("## 一行要約", result.stdout)
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_quote_over_40_percent_is_quote_heavy(self):
        fat_quote = ("逐語の再掲。" * 80) + " (p.9)\n"
        text = valid_extract(quotes=fat_quote)
        self.assertGreater(CHECK._quote_ratio(text), 0.40)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ch-03.md"
            path.write_text(text, encoding="utf-8")
            result = run_checker([path])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("QUOTE-HEAVY", result.stdout)
        self.assertIn(str(path), result.stdout)

    def test_multiple_files_mixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "ch-01.md"
            bad = Path(tmp) / "ch-04.md"
            missing = Path(tmp) / "no-such.md"
            good.write_text(valid_extract(), encoding="utf-8")
            bad.write_text("# extract ch-04 欠落章\n\n## 一行要約\n\nだけ\n", encoding="utf-8")
            result = run_checker([good, bad, missing])
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ch-01.md", result.stdout)
        self.assertIn("MISSING", result.stdout)
        self.assertIn("ch-04.md", result.stdout)
        self.assertIn("ファイルが無い", result.stdout)

    def test_forbid_raw_detects_raw_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / ".raw" / "books" / "x" / "extracts"
            raw_dir.mkdir(parents=True)
            path = raw_dir / "ch-01.md"
            path.write_text(valid_extract(), encoding="utf-8")
            result = run_checker([path], extra=["--forbid-raw"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FORBID-RAW", result.stdout)
        self.assertIn(".raw", result.stdout)

    def test_empty_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ch-empty.md"
            path.write_text("", encoding="utf-8")
            result = run_checker([path])
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING", result.stdout)
        self.assertIn("# extract ch-NN <章題>", result.stdout)

    def test_no_args_is_usage_error(self):
        result = subprocess.run(
            ["python3", str(CHECKER)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr)

    def test_fullwidth_parens_heading_is_missing(self):
        text = valid_extract().replace(
            "## 引用(locator 付き)",
            "## 引用（locator 付き）",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ch-fw.md"
            path.write_text(text, encoding="utf-8")
            result = run_checker([path])
        self.assertEqual(result.returncode, 1)
        self.assertIn("## 引用(locator 付き)", result.stdout)

    def test_h1_without_title_is_missing(self):
        text = valid_extract().replace(
            "# extract ch-01 試験章",
            "# extract ch-01",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ch-notitle.md"
            path.write_text(text, encoding="utf-8")
            result = run_checker([path])
        self.assertEqual(result.returncode, 1)
        self.assertIn("# extract ch-NN <章題>", result.stdout)


if __name__ == "__main__":
    unittest.main()
