#!/usr/bin/env bash
# pdf-text.sh — PDF から読む用のテキストを取り出す(fetch-paper-pdf.sh / fetch-book.sh が使う)。
#
# 第一経路は scripts/pdf-reading-order.py(poppler の語座標から、二段組みを左段→右段の読み順に並べる)。
# それが失敗したときだけ `pdftotext`(既定モード)に落とす。`-layout` は使わない。
# -layout は二段組みの左右の行を 1 行に並べ、LLM が隣の段の数値を文に結び付ける原因になる。
# 表の行を見たいときのために、別ファイルへ -layout 版を作るのは呼び出し側の判断である。
#
# 使い方:
#   scripts/pdf-text.sh <pdf> <out.txt> [first last]
#
# 出力(stdout): text_mode=reading-order|pdftotext
# 終了コード: 0 成功 / 1 どちらの経路でも取り出せなかった(out は空ファイルにしない)
set -uo pipefail

[ "$#" -eq 2 ] || [ "$#" -eq 4 ] || { echo "usage: pdf-text.sh <pdf> <out.txt> [first last]" >&2; exit 1; }
PDF="$1"; OUT="$2"; FIRST="${3:-}"; LAST="${4:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

range_py=(); range_pt=()
if [ -n "$FIRST" ]; then
  range_py=(--first "$FIRST" --last "$LAST")
  range_pt=(-f "$FIRST" -l "$LAST")
fi

if python3 "${SCRIPT_DIR}/pdf-reading-order.py" "$PDF" "$OUT" ${range_py[@]+"${range_py[@]}"} 2>/dev/null; then
  echo "text_mode=reading-order"
  exit 0
fi

echo "warn: 読み順に取り出せなかった。pdftotext(既定モード)に落とす。" >&2
if pdftotext ${range_pt[@]+"${range_pt[@]}"} "$PDF" "$OUT" 2>/dev/null; then
  echo "text_mode=pdftotext"
  exit 0
fi
exit 1
