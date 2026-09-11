#!/usr/bin/env bash
# fetch-paper-pdf.sh — wiki-ingest-paper の PDF 取得ヘルパー
#
# 論文の参照(arXiv URL / arXiv ID / 任意の PDF URL / ローカル PDF パス)を解決し、
# 不変原本として .raw/papers/ に PDF を配置、%PDF シグネチャで健全性を検証し、
# 取り込み用にプレーンテキスト(.txt)を抽出する。wiki/ 配下は一切変更しない。
#
# 使い方:
#   scripts/fetch-paper-pdf.sh <arxiv-url|arxiv-id|pdf-url|local-pdf-path> [slug]
#
# 入力の解決規則:
#   - https://arxiv.org/abs/<id> / .../pdf/<id> / 裸の <id>(例 2501.01234, 2501.01234v2)
#       -> https://arxiv.org/pdf/<id>.pdf からダウンロード。arxiv_html ヒントも併せて出力。
#   - その他の https URL                -> そのままダウンロード(.pdf でなくても可)。
#   - ローカル .pdf パス                 -> .raw/papers/ にコピー。
#
# 出力(stdout, key=value 形式。呼び出し側はこれをパースする):
#   pdf=<.raw/papers/ 配下の相対パス>
#   txt=<抽出テキストの相対パス>           # 取り込み時に Read する。直接 wiki に貼らない
#   arxiv_id=<id または空>
#   arxiv_html=<https://arxiv.org/html/<id> または空>   # 図表・キャプション抽出の候補(存在確認は呼び出し側)
#   year_hint=<arXiv ID から推定した発行年 または空>
#   pages=<ページ数 または空>
#   title=<pdfinfo が返したタイトル または空>          # arXiv では空のことが多い
#   slug=<PDF/テキストのファイル名(拡張子なし)>
#   images_dir=<.raw/papers/<slug>/images>
#   images_count=<掃除後の embedded 画像数>
#   image_manifest=<.raw/papers/<slug>/images/images.json>
#   figure_ids=<raw の一意な図表 ID 数。verify 失敗時は空>
#   figure_id_counts=<ID:回数 をカンマ区切り。verify 失敗時は空>
#   existing_sources=<同じ arXiv ID / DOI を持つ既存 wiki/sources ページ(; 区切り)または空>
#       # scripts/paper-ids.py check の結果。空でなければ新規ページを作らず既存ページの更新を検討する
#
# 環境変数:
#   FETCH_FAIL_IF_EXISTS=1  既存 source ページに一致したらダウンロード前に失敗(終了コード 4)
#
# 注意:
#   - ネットワーク取得を伴う。サンドボックス下では arxiv.org 等への接続が拒否されることがある。
#     その場合は接続エラーで失敗するので、呼び出し側はサンドボックス無効化で再実行する。
#   - .raw/ 配下の "原本" を生成するのはこのスクリプトの責務。既存の利用者投入ファイルは変更しない。
#   - 冪等: 同一 slug で再実行すると .raw/papers/ のコピーを上書きする(原本の再取得は許容)。
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

[ "$#" -ge 1 ] || die "usage: fetch-paper-pdf.sh <arxiv-url|arxiv-id|pdf-url|local-pdf-path> [slug]"

INPUT="$1"
SLUG_ARG="${2:-}"
DEST_DIR=".raw/papers"

command -v pdftotext >/dev/null 2>&1 || die "pdftotext が見つからない(poppler を導入: brew install poppler)"

mkdir -p "$DEST_DIR"

ARXIV_ID=""
ARXIV_HTML=""
YEAR_HINT=""
SRC_URL=""
LOCAL_PATH=""

# --- 入力種別の判定と URL 解決 ---------------------------------------------
sanitize_slug() {
  # 英数とハイフン・ドット・アンダースコアのみ残す
  printf '%s' "$1" | tr ' ' '-' | tr -cd 'A-Za-z0-9._-'
}

extract_arxiv_id() {
  # 新形式 YYMM.NNNNN(vN 任意)。abs/ pdf/ URL や裸 ID から抜き出す。
  printf '%s' "$1" | grep -oE '[0-9]{4}\.[0-9]{4,5}(v[0-9]+)?' | head -n1
}

if [ -f "$INPUT" ] && printf '%s' "$INPUT" | grep -qiE '\.pdf$'; then
  # ローカル PDF
  LOCAL_PATH="$INPUT"
elif printf '%s' "$INPUT" | grep -qiE 'arxiv\.org' || printf '%s' "$INPUT" | grep -qE '^[0-9]{4}\.[0-9]{4,5}(v[0-9]+)?$'; then
  ARXIV_ID="$(extract_arxiv_id "$INPUT")"
  [ -n "$ARXIV_ID" ] || die "arXiv ID を抽出できなかった: $INPUT"
  SRC_URL="https://arxiv.org/pdf/${ARXIV_ID}.pdf"
  ARXIV_HTML="https://arxiv.org/html/${ARXIV_ID}"
  # YYMM の YY から発行年を推定(2007 以降の新形式を前提)
  YY="${ARXIV_ID:0:2}"
  case "$YY" in
    [0-9][0-9]) YEAR_HINT="20${YY}" ;;
  esac
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  SRC_URL="$INPUT"
else
  die "入力を解釈できない(arXiv URL/ID・PDF URL・ローカル PDF パスのいずれか): $INPUT"
fi

# --- slug 決定 -------------------------------------------------------------
if [ -n "$SLUG_ARG" ]; then
  SLUG="$(sanitize_slug "$SLUG_ARG")"
elif [ -n "$ARXIV_ID" ]; then
  SLUG="arxiv-$(sanitize_slug "$ARXIV_ID")"
elif [ -n "$LOCAL_PATH" ]; then
  SLUG="$(sanitize_slug "$(basename "${LOCAL_PATH%.pdf}")")"
else
  base="$(basename "${SRC_URL%%\?*}")"      # クエリ文字列除去
  base="${base%.pdf}"
  SLUG="$(sanitize_slug "$base")"
fi
[ -n "$SLUG" ] || SLUG="paper"

# --- 既存 source の照合(同じ論文の二重取り込みを防ぐ) -------------------------
# arXiv ID か URL(doi.org を含む)を paper-ids.py の索引に当てる。ローカル PDF は照合しない
# (題名しか手掛かりが無く、pdfinfo の題名は信用できない)。一致しても既定では取得を続け、
# existing_sources= で呼び出し側に知らせる。判断(既存ページを更新するか)は呼び出し側の責務。
EXISTING_SOURCES=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/paper-ids.py" ] && command -v python3 >/dev/null 2>&1; then
  REF="${ARXIV_ID:-${SRC_URL:-}}"
  if [ -n "$REF" ]; then
    EXISTING_SOURCES="$(python3 "$SCRIPT_DIR/paper-ids.py" check "$REF" --compact 2>/dev/null \
      | awk -F'\t' 'NR==1 { print $5 }' || true)"
  fi
  if [ -n "$EXISTING_SOURCES" ]; then
    err "warn: 同じ論文の source ページが既にある: $EXISTING_SOURCES"
    if [ "${FETCH_FAIL_IF_EXISTS:-0}" = "1" ]; then
      printf 'existing_sources=%s\n' "$EXISTING_SOURCES"
      err "ERROR: FETCH_FAIL_IF_EXISTS=1 のため取得を中止した"
      exit 4
    fi
  fi
fi

PDF="${DEST_DIR}/${SLUG}.pdf"
TXT="${DEST_DIR}/${SLUG}.txt"
WORK_PDF="$(mktemp "${TMPDIR:-/tmp}/fetch-paper-pdf.XXXXXX.pdf")"
trap 'rm -f "$WORK_PDF"' EXIT

# --- 取得 -------------------------------------------------------------------
if [ -n "$LOCAL_PATH" ]; then
  cp -f "$LOCAL_PATH" "$WORK_PDF"
else
  command -v curl >/dev/null 2>&1 || die "curl が見つからない"
  # arXiv は UA 無しだと弾くことがある。リダイレクト追従・リトライ付き。
  curl -fsSL --retry 3 --retry-delay 2 \
    -A "Mozilla/5.0 (compatible; wiki-ingest-paper/1.0)" \
    -o "$WORK_PDF" "$SRC_URL" \
    || die "ダウンロード失敗: $SRC_URL (サンドボックス下ならネットワーク拒否の可能性。サンドボックス無効化で再実行)"
fi

# --- 検証(%PDF シグネチャ) ------------------------------------------------
sig="$(head -c 5 "$WORK_PDF" 2>/dev/null || true)"
case "$sig" in
  %PDF*) : ;;
  *)
    # HTML エラーページ/ペイウォール等を掴んだ場合
    die "取得物が PDF ではない(先頭=$(printf '%q' "$sig"))。URL がペイウォール/HTML の可能性: ${SRC_URL:-$LOCAL_PATH}"
    ;;
esac
mv -f "$WORK_PDF" "$PDF"
trap - EXIT

# --- テキスト抽出 -----------------------------------------------------------
# -layout で段組・表の見かけを保つ。失敗しても致命にしない(画像のみ PDF など)。
if ! pdftotext -layout "$PDF" "$TXT" 2>/dev/null; then
  err "warn: pdftotext がテキストを抽出できなかった(スキャン PDF の可能性)。OCR は別途検討。"
  : > "$TXT"
fi

# --- 画像抽出 ---------------------------------------------------------------
IMAGES_DIR="${DEST_DIR}/${SLUG}/images"
IMAGE_MANIFEST="${IMAGES_DIR}/images.json"
IMAGES_COUNT="0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_EXTRACTOR="${SCRIPT_DIR}/extract-paper-images.mjs"

command -v node >/dev/null 2>&1 || die "node が見つからない(pdf.js 画像抽出に必要)"
[ -f "$IMAGE_EXTRACTOR" ] || die "画像抽出ヘルパーが見つからない: $IMAGE_EXTRACTOR"

image_report="$(node "$IMAGE_EXTRACTOR" "$PDF" "$IMAGES_DIR")" \
  || die "pdf.js 画像抽出に失敗: $PDF"
IMAGES_COUNT="$(printf '%s\n' "$image_report" | awk -F= '/^images_count=/{print $2; exit}')"
[ -n "$IMAGES_COUNT" ] || IMAGES_COUNT="0"

# この実行が作った images dir だけ掃除する。利用者が置いた別原本は触らない。
# ディレクトリがシンボリックリンクなら辿らず、警告だけ出して掃除しない。
if [ -L "$IMAGES_DIR" ]; then
  err "warn: images_dir がシンボリックリンクなので掃除しない: $IMAGES_DIR"
elif [ -d "$IMAGES_DIR" ]; then
  cleaned=""
  if [ -f "${SCRIPT_DIR}/wiki-verify-ingest.py" ]; then
    cleaned="$(python3 "${SCRIPT_DIR}/wiki-verify-ingest.py" --cleanup-images "$IMAGES_DIR" 2>/dev/null || true)"
  fi
  if ! printf '%s\n' "$cleaned" | grep -q '^images_count='; then
    cleaned="$(python3 - "$IMAGES_DIR" <<'PY'
import json, os, sys
from pathlib import Path

def islink(p):
    p = Path(p)
    return p.is_symlink() or os.path.islink(str(p))

def regular(p):
    p = Path(p)
    return (not islink(p)) and p.is_file()

d = Path(sys.argv[1])
try:
    if islink(d):
        print("warn: images_dir がシンボリックリンクなので掃除しない: %s" % d,
              file=sys.stderr)
        raise SystemExit(0)
    if not d.is_dir():
        raise SystemExit(0)
    manifest = d / "images.json"
    data = None
    if regular(manifest):
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print("warn: images.json が壊れている: %s" % exc, file=sys.stderr)
            parsed = None
        if parsed is not None and not isinstance(parsed, dict):
            print("warn: images.json の根が dict でない", file=sys.stderr)
            parsed = None
        if isinstance(parsed, dict):
            images = parsed.get("images")
            if not isinstance(images, list):
                images = []
            kept = []
            for x in images:
                if not isinstance(x, dict):
                    continue
                if x.get("kind") == "page-render" or str(x.get("file", "")).startswith("page-"):
                    continue
                kept.append(x)
            parsed["images"] = kept
            parsed["images_count"] = len(kept)
            data = parsed
    for p in d.glob("page-*.png"):
        if regular(p):
            try:
                p.unlink()
            except OSError:
                pass
    if data is not None and not islink(manifest):
        try:
            manifest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError as exc:
            print("warn: images.json を書けない: %s" % exc, file=sys.stderr)
            data = None
    if data is not None:
        print("images_count=%d" % data["images_count"])
    else:
        print("images_count=%d" % sum(1 for p in d.glob("image-*.png") if regular(p)))
except Exception as exc:
    print("warn: 画像掃除に失敗した: %s" % exc, file=sys.stderr)
PY
)" || true
  fi
  counted="$(printf '%s\n' "$cleaned" | awk -F= '/^images_count=/{print $2; exit}')"
  if [ -n "$counted" ]; then
    IMAGES_COUNT="$counted"
  fi
fi
[ -n "$IMAGES_COUNT" ] || IMAGES_COUNT="0"

# 図表 ID 一覧は任意。verify が無い・失敗しても fetch 自体は成功させる。
FIGURE_IDS=""
FIGURE_ID_COUNTS=""
if [ -f "${SCRIPT_DIR}/wiki-verify-ingest.py" ] && [ -s "$TXT" ]; then
  fig_out="$(python3 "${SCRIPT_DIR}/wiki-verify-ingest.py" --list-figure-ids "$TXT" 2>/dev/null || true)"
  if [ -n "$fig_out" ]; then
    FIGURE_IDS="$(printf '%s\n' "$fig_out" | sed -n 's/^figure_ids=\([0-9][0-9]*\).*/\1/p' | tail -n1)"
    FIGURE_ID_COUNTS="$(printf '%s\n' "$fig_out" | awk -F'\t' 'NF==2 {printf "%s%s:%s", sep, $1, $2; sep=","}')"
  fi
fi

# --- メタ情報 ---------------------------------------------------------------
PAGES=""
TITLE=""
if command -v pdfinfo >/dev/null 2>&1; then
  info="$(pdfinfo "$PDF" 2>/dev/null || true)"
  PAGES="$(printf '%s\n' "$info" | awk -F': *' '/^Pages:/{print $2; exit}')"
  TITLE="$(printf '%s\n' "$info" | awk -F': *' '/^Title:/{print $2; exit}')"
fi

# --- レポート ---------------------------------------------------------------
printf 'pdf=%s\n'        "$PDF"
printf 'txt=%s\n'        "$TXT"
printf 'arxiv_id=%s\n'   "$ARXIV_ID"
printf 'arxiv_html=%s\n' "$ARXIV_HTML"
printf 'year_hint=%s\n'  "$YEAR_HINT"
printf 'pages=%s\n'      "$PAGES"
printf 'title=%s\n'      "$TITLE"
printf 'slug=%s\n'       "$SLUG"
printf 'images_dir=%s\n' "$IMAGES_DIR"
printf 'images_count=%s\n' "$IMAGES_COUNT"
printf 'image_manifest=%s\n' "$IMAGE_MANIFEST"
printf 'figure_ids=%s\n' "$FIGURE_IDS"
printf 'figure_id_counts=%s\n' "$FIGURE_ID_COUNTS"
printf 'existing_sources=%s\n' "$EXISTING_SOURCES"
