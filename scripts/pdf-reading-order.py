#!/usr/bin/env python3
"""pdf-reading-order.py — 二段組みを読み順に並べたテキストを PDF から取り出す。

`pdftotext -layout` は見た目の配置を保つので、二段組みでは左右の段の行が 1 行に並ぶ
(2026-10-12 の調査で `.raw/papers/*.txt` 2,480 本の中央値 53% の行が左右混在だった)。
LLM はそれを頭の中で左右に分けて読むしかなく、隣の段の数値を文に結び付ける取り違えが起きる。
`pdftotext` の既定モードは段落を保つが、段落の順序を誤ることがある(ATC 2016 の
RDMA 設計指針では abstract の第 1 段落が序論の後ろに回った)。

文字の解読は poppler(`pdftotext -bbox-layout`)に任せ、並べ替えだけをここで行う。
PyMuPDF で解読すると、poppler が読める数字を置換文字にするフォントがある
(hadzilacos_ps の箇条番号)。品質判定(pdf-text-quality.py)も poppler の出力で行うので、
解読器を 1 つにそろえる。

並べ替えの規則:
  1. ページ中央の縦線をまたぐブロックは「全幅」(題名・全幅の図・単段組みの段落)。
  2. 全幅ブロックで縦方向の帯に区切り、各帯の中では左段を上から、次に右段を上から並べる。
単段組みのページでは本文ブロックがほぼ全幅になるので、上から下への順になる。
上付きの語(行の中で背が低く、上にずれている語)は前の語に ^ でつなぐ。つながないと図の目盛り
10³ が 10 3 か 103 になり、存在しない数値と区別できない。
合字(ﬁ ﬂ ﬀ ﬃ ﬄ ﬅ ﬆ)は展開する。展開しないと `rg efficiency` が当たらない。
NFKC は掛けない(上付きの 10⁹ が 109 になり、存在しない数値を作るため)。

使い方:
  python3 scripts/pdf-reading-order.py <pdf> <out.txt> [--first N] [--last M]

出力: ページごとのテキストをフォームフィード(\\f)で区切る(pdftotext と同じ形)。
終了コード: 0 成功 / 1 失敗(出力ファイルは作らない) / 2 使い方の誤り
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
})
# 改行とタブ以外の制御文字。\x0c はページ区切りと衝突するので U+FFFD に置き換え、
# pdf-text-quality.py の glyph-loss 判定に掛かるようにする。
CONTROL = {c: "�" for c in range(32) if chr(c) not in "\n\t"}
# 中央の縦線からこの幅(pt)以上はみ出したら、その線をまたぐとみなす。段間の余白より小さく取る。
STRADDLE_PT = 10.0
NS = "{http://www.w3.org/1999/xhtml}"
XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def order_blocks(blocks, page_width):
    """blocks: (x0, y0, x1, y1, text) の列。読み順に並べ替えた列を返す。"""
    mid = page_width / 2.0

    def kind(b):
        x0, _, x1, _, _ = b
        if x0 < mid - STRADDLE_PT and x1 > mid + STRADDLE_PT:
            return "full"
        return "L" if (x0 + x1) / 2.0 < mid else "R"

    out, band = [], []

    def flush():
        out.extend(sorted((b for b in band if kind(b) == "L"), key=lambda b: (b[1], b[0])))
        out.extend(sorted((b for b in band if kind(b) == "R"), key=lambda b: (b[1], b[0])))
        band.clear()

    for b in sorted(blocks, key=lambda b: (b[1], b[0])):
        if kind(b) == "full":
            flush()
            out.append(b)
        else:
            band.append(b)
    flush()
    return out


def page_text(blocks, page_width):
    # 置換は strip より前に行う。\x0c は空白扱いで、ブロック端の欠落が黙って消えるため。
    parts = [b[4].translate(CONTROL).strip() for b in order_blocks(blocks, page_width)]
    return "\n\n".join(p for p in parts if p).translate(LIGATURES)


def line_text(words):
    """words: (x0, y0, x1, y1, text) の列(1 行分)。上付きの語は前の語に ^ でつなぎ、他は空白で連結する。"""
    if not words:
        return ""
    heights = sorted(w[3] - w[1] for w in words)
    h = heights[len(heights) // 2]
    base = max(w[3] for w in words if (w[3] - w[1]) >= 0.8 * h)
    out, prev_x1 = [], None
    for x0, y0, x1, y1, text in sorted(words, key=lambda w: w[0]):
        gap = None if prev_x1 is None else x0 - prev_x1
        # 上付きは直前の語に隙間なく接する。斜体の数式文字(𝑀・𝑘)は背と位置がずれても隙間があるので除く。
        if out and gap < 0.15 * h and (y1 - y0) < 0.8 * h and y1 < base - 0.2 * h:
            out[-1] += "^" + text
        # 数式では poppler が 1 字ずつ語にする。隙間がほぼ 0 なら空白を入れない(0 . 0058 → 0.0058)。
        elif out and gap < 0.1 * h:
            out[-1] += text
        else:
            out.append(text)
        prev_x1 = x1
    return " ".join(out)


def _box(el):
    return tuple(float(el.get(k)) for k in ("xMin", "yMin", "xMax", "yMax"))


def parse_bbox_layout(xhtml):
    """pdftotext -bbox-layout の XHTML を、ページごとの (幅, [ブロック]) に直す。"""
    if isinstance(xhtml, bytes):
        xhtml = xhtml.decode("utf-8", "replace")
    # 解読できない字形を poppler は制御文字のまま出すことがあり、XML として不正になる。U+FFFD に置き換える。
    root = ET.fromstring(XML_INVALID.sub("\ufffd", xhtml))
    pages = []
    for page in root.iter(NS + "page"):
        blocks = []
        for block in page.iter(NS + "block"):
            lines = []
            for line in block.iter(NS + "line"):
                words = [(*_box(w), w.text or "") for w in line.iter(NS + "word")]
                lines.append(line_text(words))
            blocks.append((*_box(block), "\n".join(lines)))
        pages.append((float(page.get("width")), blocks))
    return pages


def extract(pdf, first=None, last=None):
    cmd = ["pdftotext", "-bbox-layout"]
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    res = subprocess.run(cmd + [str(pdf), "-"], capture_output=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode("utf-8", "replace").strip()[:200])
    pages = parse_bbox_layout(res.stdout)
    return "".join(page_text(blocks, width) + "\n\f" for width, blocks in pages)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--first", type=int)
    ap.add_argument("--last", type=int)
    args = ap.parse_args(argv)
    try:
        text = extract(args.pdf, args.first, args.last)
    except Exception as exc:  # 壊れた PDF・XHTML。呼び出し側は pdftotext の既定モードに落とす
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
