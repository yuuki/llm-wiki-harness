#!/usr/bin/env python3
"""wiki-extract-check.py — 章抽出ファイルの必須見出し検査。

book / thesis の読み手(extract)が書いた `<scratch>/extracts/ch-NN.md` を、
書き手を起動する前にオーケストレータが 1 回かける。ページは書かない。
`WIKI_VAULT_ROOT` は不要(スクラッチ上のファイルを直接見る)。

使い方:
  python3 scripts/wiki-extract-check.py --forbid-raw /tmp/book-<slug>/extracts/ch-01.md
  python3 scripts/wiki-extract-check.py --forbid-raw /tmp/book-<slug>/extracts/ch-*.md

終了コード:
  0 — 欠落なし、引用節 40% 以下、(--forbid-raw 時は resolve 後に .raw 成分なし)
  1 — MISSING / QUOTE-HEAVY / FORBID-RAW / ファイルが無い
  2 — 使い方の誤り
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# スキーマ正本: .claude/skills/wiki-ingest-book/references/extract-schema.md
REQUIRED_H1_RE = re.compile(r"^# extract ch-\d{2}\s+\S+")
REQUIRED_H1_LABEL = "# extract ch-NN <章題>"

REQUIRED_H2 = (
    "一行要約",
    "主要主張",
    "実践的指針",
    "登場 entity",
    "登場 concept",
    "図表",
    "引用(locator 付き)",
    "未解決の問い(章が投げたままのもの)",
    "書き手への注意",
)

QUOTE_HEADING = "引用(locator 付き)"
QUOTE_HEAVY_RATIO = 0.40

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _h2_title(line: str) -> str | None:
    if not line.startswith("## "):
        return None
    return line[3:].strip()


def _quote_ratio(text: str) -> float | None:
    """引用節(見出し行を含む)がファイル全体に占める割合。見出しが無ければ None。"""
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if _h2_title(line.rstrip("\n")) == QUOTE_HEADING:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    section = "".join(lines[start:end])
    if not text:
        return None
    return len(section) / len(text)


def has_raw_component(path: Path) -> bool:
    """resolve 後のパス成分に `.raw` があれば真。"""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return ".raw" in resolved.parts


def check_text(text: str, label: str) -> list[str]:
    """欠落と QUOTE-HEAVY を 1 行 1 件で返す。"""
    errors: list[str] = []

    h1_ok = any(REQUIRED_H1_RE.match(line) for line in text.splitlines())
    found_h2 = set()
    for line in text.splitlines():
        title = _h2_title(line)
        if title is not None:
            found_h2.add(title)

    absent = []
    if not h1_ok:
        absent.append(REQUIRED_H1_LABEL)
    for title in REQUIRED_H2:
        if title not in found_h2:
            absent.append("## " + title)
    if absent:
        errors.append("MISSING {}: {}".format(label, ", ".join(absent)))

    ratio = _quote_ratio(text)
    if ratio is not None and ratio > QUOTE_HEAVY_RATIO:
        errors.append("QUOTE-HEAVY {}: {:.0%}".format(label, ratio))
    return errors


def check_path(path: Path, forbid_raw: bool = False) -> list[str]:
    label = str(path)
    if forbid_raw and has_raw_component(path):
        return ["FORBID-RAW {}: .raw 成分がある".format(label)]
    if not path.is_file():
        return ["MISSING {}: ファイルが無い".format(label)]
    text = path.read_text(encoding="utf-8", errors="replace")
    return check_text(text, label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="章抽出ファイルの必須見出しを検査する。"
    )
    parser.add_argument(
        "extracts",
        nargs="+",
        help="extract ファイル(複数可)",
    )
    parser.add_argument(
        "--forbid-raw",
        action="store_true",
        help="resolve 後のパスに .raw 成分があれば FORBID-RAW で落とす",
    )
    args = parser.parse_args(argv)

    any_error = False
    for raw in args.extracts:
        for line in check_path(Path(raw), forbid_raw=args.forbid_raw):
            print(line)
            any_error = True
    return EXIT_ERROR if any_error else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
