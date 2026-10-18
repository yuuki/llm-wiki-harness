#!/usr/bin/env bash
# pdf-ocr.sh — PDF の各ページを画像にして Apple Vision で OCR し、ページ区切りつきテキストを書く。
#
# pdftotext の抽出が壊れている論文(テキスト層が無い・数字が字形ごと抜ける)の読む入力を作る。
# fetch-paper-pdf.sh が text_quality != ok のときに呼ぶ。単体でも呼べる。
#
# 使い方:
#   scripts/pdf-ocr.sh <pdf> <out.txt>
#
# 出力ファイル: ページごとの OCR テキストをフォームフィード(\f)で区切る(pdftotext と同じ形)。
#   rg の行から何ページ目かを数えられるようにするため。
#
# 注意:
#   - Vision はサンドボックス内では CRImageReaderError で失敗する。サンドボックス無効化で実行する。
#   - 初回は scripts/vision-ocr.swift を swiftc でビルドし、$TMPDIR にキャッシュする(数秒)。
#   - 300 dpi で描画する。英文の文字誤り率はおよそ 0.2〜0.3%(数字の誤読はゼロではない)。
#
# 終了コード: 0 成功 / 1 失敗(出力ファイルは作らない)
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

[ "$#" -eq 2 ] || die "usage: pdf-ocr.sh <pdf> <out.txt>"
PDF="$1"
OUT="$2"
[ -f "$PDF" ] || die "PDF が無い: $PDF"
command -v pdftoppm >/dev/null 2>&1 || die "pdftoppm が見つからない(brew install poppler)"
command -v swiftc >/dev/null 2>&1 || die "swiftc が見つからない(Xcode Command Line Tools が要る)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/vision-ocr.swift"
HASH="$(shasum -a 256 "$SRC" | cut -c1-12)"
BIN="${TMPDIR:-/tmp}/vision-ocr-${HASH}"
if [ ! -x "$BIN" ]; then
  swiftc -parse-as-library -O "$SRC" -o "$BIN.tmp" >&2 || die "vision-ocr のビルドに失敗した"
  mv -f "$BIN.tmp" "$BIN"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pdf-ocr.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

pdftoppm -r 300 -png "$PDF" "$WORK/p" || die "pdftoppm がページを描画できなかった"
# pdftoppm はページ数に応じて p-1.png / p-01.png / p-001.png と桁を揃える。名前順 = ページ順。
pages=()
while IFS= read -r f; do pages+=("$f"); done < <(find "$WORK" -name 'p-*.png' | sort)
[ "${#pages[@]}" -gt 0 ] || die "描画されたページが無い"

# Vision はまれに一時的に失敗する(26 本の一括処理で 1 本、再実行で通った)ので 1 回だけやり直す。
"$BIN" --langs en-US,ja-JP "${pages[@]}" > "$WORK/ocr.jsonl" \
  || "$BIN" --langs en-US,ja-JP "${pages[@]}" > "$WORK/ocr.jsonl" \
  || die "Vision の OCR に失敗した(サンドボックス内なら無効化して再実行する)"

python3 - "$WORK/ocr.jsonl" "${#pages[@]}" > "$WORK/out.txt" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
if len(rows) != int(sys.argv[2]):
    sys.exit("OCR の結果がページ数と合わない: %d / %s" % (len(rows), sys.argv[2]))
sys.stdout.write("".join(r["text"].rstrip("\n") + "\n\f" for r in rows))
PY
[ -s "$WORK/out.txt" ] || die "OCR の結果が空"
mv -f "$WORK/out.txt" "$OUT"
printf 'ocr_pages=%s\n' "${#pages[@]}"
