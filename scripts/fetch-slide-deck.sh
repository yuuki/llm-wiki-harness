#!/usr/bin/env bash
# fetch-slide-deck.sh — wiki-ingest-slides の PDF 取得・画像化ヘルパー
#
# スライド PDF の参照(PDF URL / 共有ページ URL / ローカル PDF パス)を解決し、
# .raw/slides/<slug>/ に PDF 原本、補助テキスト、全ページ PNG を生成する。
# wiki/ 配下は一切変更しない。
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

usage() {
  die "usage: fetch-slide-deck.sh [--force] <pdf-url|slide-page-url|local-pdf-path> [slug]"
}

FORCE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --)
      shift
      break
      ;;
    -*)
      usage
      ;;
    *)
      break
      ;;
  esac
done

[ "$#" -ge 1 ] || usage

INPUT="$1"
SLUG_ARG="${2:-}"
RAW_ROOT=".raw/slides"

command -v pdftotext >/dev/null 2>&1 || die "pdftotext が見つからない(poppler を導入: brew install poppler)"
command -v pdftoppm >/dev/null 2>&1 || die "pdftoppm が見つからない(poppler を導入: brew install poppler)"

sanitize_slug() {
  printf '%s' "$1" | tr ' ' '-' | tr -cd 'A-Za-z0-9._-'
}

basename_slug_from_url() {
  base="$(basename "${1%%\?*}")"
  base="${base%.pdf}"
  sanitize_slug "$base"
}

discover_pdf_url() {
  html="$1"
  page_url="$2"
  grep -Eoi 'https?://[^"'\''<> ]+\.pdf([?][^"'\''<> ]*)?' "$html" | head -n1 && return 0
  rel="$(grep -Eoi 'href=["'\''][^"'\'']+\.pdf([?][^"'\'']*)?["'\'']' "$html" | sed -E 's/^href=["'\'']([^"'\'']+)["'\'']$/\1/' | head -n1 || true)"
  if [ -n "$rel" ]; then
    case "$rel" in
      http*) printf '%s\n' "$rel" ;;
      /*)
        origin="$(printf '%s' "$page_url" | sed -E 's#^(https?://[^/]+).*#\1#')"
        printf '%s%s\n' "$origin" "$rel"
        ;;
      *)
        base="$(printf '%s' "$page_url" | sed -E 's#[^/]*$##')"
        printf '%s%s\n' "$base" "$rel"
        ;;
    esac
  fi
}

INPUT_URL=""
PDF_URL=""
LOCAL_PATH=""

if [ -f "$INPUT" ] && printf '%s' "$INPUT" | grep -qiE '\.pdf$'; then
  LOCAL_PATH="$INPUT"
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  INPUT_URL="$INPUT"
  if printf '%s' "$INPUT" | grep -qiE '\.pdf([?#].*)?$'; then
    PDF_URL="$INPUT"
  else
    command -v curl >/dev/null 2>&1 || die "curl が見つからない"
    tmp_html="$(mktemp "${TMPDIR:-/tmp}/fetch-slide-deck.XXXXXX.html")"
    curl -fsSL --retry 3 --retry-delay 2 \
      -A "Mozilla/5.0 (compatible; wiki-ingest-slides/1.0)" \
      -o "$tmp_html" "$INPUT" \
      || die "共有ページ取得失敗: $INPUT"
    PDF_URL="$(discover_pdf_url "$tmp_html" "$INPUT" | head -n1 || true)"
    rm -f "$tmp_html"
    [ -n "$PDF_URL" ] || die "共有ページ内に PDF URL を発見できなかった: ${INPUT}。SlideShare 等ボット対策のあるページは curl で本文を取得できないことがある(参照: SKILL.md の「SlideShare 等ボット対策ページのフォールバック」)。PDF URL を直接指定して再実行するか、フォールバック手順を使うこと。"
  fi
else
  die "入力を解釈できない(PDF URL・共有ページ URL・ローカル PDF パスのいずれか): $INPUT"
fi

if [ -n "$SLUG_ARG" ]; then
  SLUG="$(sanitize_slug "$SLUG_ARG")"
  [ "$SLUG" = "$SLUG_ARG" ] || die "slug に使えない文字が含まれる: $SLUG_ARG"
elif [ -n "$LOCAL_PATH" ]; then
  SLUG="$(sanitize_slug "$(basename "${LOCAL_PATH%.pdf}")")"
else
  SLUG="$(basename_slug_from_url "$PDF_URL")"
fi
[ -n "$SLUG" ] || SLUG="slides"

DEST_DIR="${RAW_ROOT}/${SLUG}"
PAGES_DIR="${DEST_DIR}/pages"
PDF="${DEST_DIR}/${SLUG}.pdf"
TXT="${DEST_DIR}/${SLUG}.txt"

if [ "$FORCE" -ne 1 ] && [ -d "$DEST_DIR" ] && find "$DEST_DIR" -type f | grep -q .; then
  die "既存の .raw/slides/${SLUG}/ にファイルがある。原本不変を守るため停止する。上書きする場合は --force を明示する。"
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fetch-slide-deck.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
WORK_PAGES_DIR="${WORK_DIR}/pages"
WORK_PDF="${WORK_DIR}/${SLUG}.pdf"
WORK_TXT="${WORK_DIR}/${SLUG}.txt"
mkdir -p "$WORK_PAGES_DIR"

if [ -n "$LOCAL_PATH" ]; then
  cp -f "$LOCAL_PATH" "$WORK_PDF"
else
  command -v curl >/dev/null 2>&1 || die "curl が見つからない"
  curl -fsSL --retry 3 --retry-delay 2 \
    -A "Mozilla/5.0 (compatible; wiki-ingest-slides/1.0)" \
    -o "$WORK_PDF" "$PDF_URL" \
    || die "PDF ダウンロード失敗: $PDF_URL"
fi

sig="$(head -c 5 "$WORK_PDF" 2>/dev/null || true)"
case "$sig" in
  %PDF*) : ;;
  *) die "取得物が PDF ではない(先頭=$(printf '%q' "$sig")): ${PDF_URL:-$LOCAL_PATH}" ;;
esac

if ! pdftotext -layout "$WORK_PDF" "$WORK_TXT" 2>/dev/null; then
  err "warn: pdftotext がテキストを抽出できなかった。画像主体スライドとして続行する。"
  : > "$WORK_TXT"
fi

PAGES=""
TITLE=""
if command -v pdfinfo >/dev/null 2>&1; then
  info="$(pdfinfo "$WORK_PDF" 2>/dev/null || true)"
  PAGES="$(printf '%s\n' "$info" | awk -F': *' '/^Pages:/{print $2; exit}')"
  TITLE="$(printf '%s\n' "$info" | awk -F': *' '/^Title:/{print $2; exit}')"
fi

if [ -n "$PAGES" ] && printf '%s' "$PAGES" | grep -qE '^[0-9]+$'; then
  i=1
  while [ "$i" -le "$PAGES" ]; do
    prefix="${WORK_PAGES_DIR}/render-${i}"
    pdftoppm -png -r 180 -f "$i" -l "$i" "$WORK_PDF" "$prefix" >/dev/null 2>&1 \
      || die "ページ画像化に失敗: page $i"
    rendered="$(find "$WORK_PAGES_DIR" -maxdepth 1 -type f -name "render-${i}-*.png" | head -n1)"
    [ -n "$rendered" ] || die "ページ画像が生成されなかった: page $i"
    mv -f "$rendered" "$(printf '%s/page-%03d.png' "$WORK_PAGES_DIR" "$i")"
    i=$((i + 1))
  done
else
  pdftoppm -png -r 180 "$WORK_PDF" "${WORK_PAGES_DIR}/page" >/dev/null 2>&1 \
    || die "ページ画像化に失敗"
  i=1
  find "$WORK_PAGES_DIR" -maxdepth 1 -type f -name 'page-*.png' | sort -V | while IFS= read -r rendered; do
    target="$(printf '%s/page-%03d.png' "$WORK_PAGES_DIR" "$i")"
    [ "$rendered" = "$target" ] || mv -f "$rendered" "$target"
    i=$((i + 1))
  done
  PAGES="$(find "$WORK_PAGES_DIR" -maxdepth 1 -type f -name 'page-*.png' | wc -l | tr -d ' ')"
fi

[ "$PAGES" != "0" ] || die "ページ画像が 0 件。PDF を確認してください。"

if [ "$FORCE" -eq 1 ]; then
  rm -f "$PDF" "$TXT" "$PAGES_DIR"/page-*.png "$PAGES_DIR"/render-*.png 2>/dev/null || true
fi
mkdir -p "$PAGES_DIR"
mv -f "$WORK_PDF" "$PDF"
mv -f "$WORK_TXT" "$TXT"
find "$WORK_PAGES_DIR" -maxdepth 1 -type f -name 'page-*.png' -exec mv -f {} "$PAGES_DIR"/ \;

printf 'pdf=%s\n' "$PDF"
printf 'txt=%s\n' "$TXT"
printf 'pages_dir=%s\n' "$PAGES_DIR"
printf 'pages=%s\n' "$PAGES"
printf 'title=%s\n' "$TITLE"
printf 'slug=%s\n' "$SLUG"
if [ -n "$INPUT_URL" ]; then
  printf 'url=%s\n' "$INPUT_URL"
fi
