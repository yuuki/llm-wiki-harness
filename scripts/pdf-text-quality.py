#!/usr/bin/env python3
"""pdf-text-quality.py — pdftotext の抽出テキストが本文を読む入力に使えるかを判定する。

wiki-ingest-paper は `.raw/papers/<slug>.txt` だけを読んで source ページを書く。
抽出が壊れていると、LLM は欠けた部分を推測で埋める(数字が消えた VL2 では
原文の 2.7 TB が 2.1TB になった。テキスト層の無い BPF は訓練知識から書かれた)。
ここでは壊れ方を 3 つに分けて検出し、fetch-paper-pdf.sh が OCR に回すかを決める。

  none        1 ページあたりの非空白文字が 50 未満。テキスト層が無い(スキャン PDF)
  sparse      同 500 未満。一部のページにしかテキストが無い、または断片だけ
  glyph-loss  私用領域(PUA)の字形か置換文字(U+FFFD)が非空白文字の 0.2% 以上。
              数字や記号がフォントの都合で抜けている(旧式の学会 PDF に多い)
  ok          上のどれでもない

しきい値は 2026-10-12 に `.raw/papers/*.txt` 2,488 本の分布で決めた(中央値は
4,162 字/ページ。glyph-loss は約 30 本)。PUA のうち U+F8E0–F8FF は数式の括弧片で
害が小さいので数えない。

入力は `pdftotext -layout` の出力(fetch-paper-pdf.sh では `<slug>.layout.txt`)とする。しきい値は
この出力の分布で決めた。PyMuPDF で解読したテキストを入れると、図の中の対応表の無い字形まで
置換文字になり、本文が無事でも glyph-loss に振れる(250 本の標本で 7 本)。

`--pdf` を渡すと、1 ページ目が横長(スライド)なら sparse と判定しない。スライドは 1 ページの
文字が少ないのが正常で、OCR しても文字はほとんど増えなかった(MLSys・NSDI のスライド 4 本で確認)。

使い方:
  python3 scripts/pdf-text-quality.py .raw/papers/<slug>.layout.txt [--pdf .raw/papers/<slug>.pdf]

出力(stdout, key=value):
  text_quality=ok|sparse|none|glyph-loss
  text_pages=<ページ数(フォームフィード区切り)>
  text_chars_per_page=<1 ページあたりの非空白文字数>
  text_pua_ratio=<PUA の割合>
  text_replacement_ratio=<置換文字の割合>

終了コード: 0 判定できた / 2 使い方の誤り・読めない
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

NONE_CPP = 50
SPARSE_CPP = 500
GLYPH_RATIO = 0.002

PUA_RE = re.compile("[\ue000-\uf8df]")
SPACE_RE = re.compile(r"\s")


def is_landscape(pdf: str) -> bool:
    """1 ページ目が横長か。スライド(16:9・4:3)は 1 ページの文字が少ないのが正常なので sparse にしない。"""
    try:
        out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+)", out, re.M)
    return bool(m) and float(m.group(1)) > float(m.group(2))


def assess(text: str, slides: bool = False) -> dict:
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    n_pages = max(1, len(pages))
    chars = len(SPACE_RE.sub("", text))
    cpp = chars // n_pages
    pua = len(PUA_RE.findall(text)) / max(chars, 1)
    rep = text.count("\ufffd") / max(chars, 1)
    if cpp < NONE_CPP:
        quality = "none"
    elif cpp < SPARSE_CPP and not slides:
        quality = "sparse"
    elif pua >= GLYPH_RATIO or rep >= GLYPH_RATIO:
        quality = "glyph-loss"
    else:
        quality = "ok"
    return {
        "text_quality": quality,
        "text_pages": n_pages,
        "text_chars_per_page": cpp,
        "text_pua_ratio": round(pua, 4),
        "text_replacement_ratio": round(rep, 4),
    }


def main(argv: list[str]) -> int:
    args = argv[1:]
    pdf = None
    if len(args) == 3 and args[1] == "--pdf":
        pdf = args[2]
        args = args[:1]
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: pdf-text-quality.py <extracted.txt> [--pdf <pdf>]", file=sys.stderr)
        return 2
    try:
        text = Path(args[0]).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for key, value in assess(text, slides=bool(pdf) and is_landscape(pdf)).items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
