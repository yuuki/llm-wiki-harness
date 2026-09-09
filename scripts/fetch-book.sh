#!/usr/bin/env bash
# fetch-book.sh — wiki-ingest-book の PDF 取得・章分割ヘルパー
#
# 書籍 PDF の参照(PDF URL / ローカル PDF パス)を解決し、
# .raw/books/<slug>/ に PDF 原本・全文テキスト・目次・章別テキストを生成する。
# --raw-root で出力先ルートを差し替えられる(wiki-ingest-thesis は .raw/theses を渡す)。
# 章境界は PyMuPDF の get_toc() から検出し、失敗時は本文の章見出し正規表現に
# フォールバックする。それでも検出できなければ chapters_count=0 を返す
# (呼び出し側がユーザーに章リストを確認し、--chapters で再実行する)。
# wiki/ 配下は一切変更しない。
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

usage() {
  die "usage: fetch-book.sh [--force] [--chapters \"1=12,2=30,...\"] [--raw-root .raw/books] <pdf-url|local-pdf-path> [slug]"
}

FORCE=0
CHAPTERS_ARG=""
RAW_ROOT_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --chapters)
      [ "$#" -ge 2 ] || usage
      CHAPTERS_ARG="$2"
      shift 2
      ;;
    --raw-root)
      [ "$#" -ge 2 ] || usage
      RAW_ROOT_ARG="$2"
      shift 2
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
RAW_ROOT="${RAW_ROOT_ARG:-.raw/books}"
case "$RAW_ROOT" in
  .raw/*) : ;;
  *) die "--raw-root は .raw/ 配下を指定する(例: .raw/theses): $RAW_ROOT" ;;
esac
RAW_ROOT="${RAW_ROOT%/}"

command -v pdftotext >/dev/null 2>&1 || die "pdftotext が見つからない(poppler を導入: brew install poppler)"
command -v uv >/dev/null 2>&1 || die "uv が見つからない(PyMuPDF の章検出に必要)"

sanitize_slug() {
  printf '%s' "$1" | tr ' ' '-' | tr -cd 'A-Za-z0-9._-'
}

LOCAL_PATH=""
PDF_URL=""
INPUT_URL=""

if [ -f "$INPUT" ] && printf '%s' "$INPUT" | grep -qiE '\.pdf$'; then
  LOCAL_PATH="$INPUT"
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  INPUT_URL="$INPUT"
  PDF_URL="$INPUT"
else
  die "入力を解釈できない(PDF URL・ローカル PDF パスのいずれか): $INPUT"
fi

if [ -n "$SLUG_ARG" ]; then
  SLUG="$(sanitize_slug "$SLUG_ARG")"
  [ "$SLUG" = "$SLUG_ARG" ] || die "slug に使えない文字が含まれる: $SLUG_ARG"
elif [ -n "$LOCAL_PATH" ]; then
  SLUG="$(sanitize_slug "$(basename "${LOCAL_PATH%.pdf}")")"
else
  base="$(basename "${PDF_URL%%\?*}")"
  SLUG="$(sanitize_slug "${base%.pdf}")"
fi
[ -n "$SLUG" ] || SLUG="book"

DEST_DIR="${RAW_ROOT}/${SLUG}"
CHAPTERS_DIR="${DEST_DIR}/chapters"
PDF="${DEST_DIR}/${SLUG}.pdf"
TXT="${DEST_DIR}/${SLUG}.txt"
TOC="${DEST_DIR}/toc.txt"

if [ "$FORCE" -ne 1 ] && [ -d "$DEST_DIR" ] && find "$DEST_DIR" -type f | grep -q .; then
  die "既存の ${DEST_DIR}/ にファイルがある。原本不変を守るため停止する。上書きする場合は --force を明示する。"
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fetch-book.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
WORK_PDF="${WORK_DIR}/${SLUG}.pdf"
WORK_TXT="${WORK_DIR}/${SLUG}.txt"
WORK_TOC="${WORK_DIR}/toc.txt"
WORK_CHAPTERS_DIR="${WORK_DIR}/chapters"
mkdir -p "$WORK_CHAPTERS_DIR"

if [ -n "$LOCAL_PATH" ]; then
  cp -f "$LOCAL_PATH" "$WORK_PDF"
else
  command -v curl >/dev/null 2>&1 || die "curl が見つからない"
  curl -fsSL --retry 3 --retry-delay 2 \
    -A "Mozilla/5.0 (compatible; wiki-ingest-book/1.0)" \
    -o "$WORK_PDF" "$PDF_URL" \
    || die "PDF ダウンロード失敗: $PDF_URL"
fi

sig="$(head -c 5 "$WORK_PDF" 2>/dev/null || true)"
case "$sig" in
  %PDF*) : ;;
  *) die "取得物が PDF ではない(先頭=$(printf '%q' "$sig")): ${PDF_URL:-$LOCAL_PATH}" ;;
esac

if ! pdftotext -layout "$WORK_PDF" "$WORK_TXT" 2>/dev/null; then
  err "warn: pdftotext がテキストを抽出できなかった(スキャン PDF の可能性)。空テキストで続行する。"
  : > "$WORK_TXT"
fi

PAGES=""
TITLE=""
YEAR_HINT=""
if command -v pdfinfo >/dev/null 2>&1; then
  info="$(pdfinfo "$WORK_PDF" 2>/dev/null || true)"
  PAGES="$(printf '%s\n' "$info" | awk -F': *' '/^Pages:/{print $2; exit}')"
  TITLE="$(printf '%s\n' "$info" | awk -F': *' '/^Title:/{print $2; exit}')"
  YEAR_HINT="$(printf '%s\n' "$info" | grep -E '^(CreationDate|ModDate):' | grep -oE '(19|20)[0-9]{2}' | head -n1 || true)"
fi

# 章検出: PyMuPDF get_toc() → 章見出し正規表現フォールバック。
# stdout に "CH|<num>|<start>|<end>|<title>" 行を出し、toc.txt に全アウトラインを書く。
CHAPTER_LINES="$(CHAPTERS_SPEC="$CHAPTERS_ARG" BOOK_PDF="$WORK_PDF" TOC_OUT="$WORK_TOC" \
  uv run --with pymupdf --cache-dir "${TMPDIR:-/tmp}/uv-cache" python3 - <<'PY'
import os, re, sys
import fitz

pdf_path = os.environ["BOOK_PDF"]
toc_out = os.environ["TOC_OUT"]
manual = os.environ.get("CHAPTERS_SPEC", "").strip()

doc = fitz.open(pdf_path)
n_pages = len(doc)
toc = doc.get_toc()  # [[level, title, page], ...]

strict_chapter_re = re.compile(r"^\s*(chapter\s+\d+\b|第\s*\d+\s*章)", re.IGNORECASE)
loose_chapter_re = re.compile(r"^\s*(chapter\s+\d+|第\s*\d+\s*章|\d+[\.\s]\s*\S)", re.IGNORECASE)

lines = []
lines.append("# fetch-book.sh が検出した PDF アウトライン(level|page|title)")
for level, title, page in toc:
    lines.append(f"outline|{level}|{page}|{title.strip()}")

chapters = []  # (num, start_page, title)

if manual:
    # "1=12,2=30" 形式: 章番号=開始ページ
    toc_by_page = {page: title.strip() for level, title, page in toc}
    for part in manual.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*=\s*(\d+)$", part)
        if not m:
            print(f"ERROR: --chapters の項目を解釈できない: {part}", file=sys.stderr)
            sys.exit(1)
        num, start = int(m.group(1)), int(m.group(2))
        if not (1 <= start <= n_pages):
            print(f"ERROR: 開始ページが範囲外: {part} (総ページ数 {n_pages})", file=sys.stderr)
            sys.exit(1)
        chapters.append((num, start, toc_by_page.get(start, f"Chapter {num}")))
    chapters.sort(key=lambda c: c[1])
else:
    # 章検出は3段階: (1) "Chapter N"/"第N章" の厳密パターンのみ level<=2 で優先的に試す。
    # 日本語技術書は節見出し(例: "1.2 なぜ...")が章と同じ outline level に並ぶことが多く、
    # 数字始まりの緩い正規表現だけに頼ると節まで章として誤検出する(実例: 8章のはずが63件検出)。
    # (2) 厳密パターンで1件も見つからない場合のみ、緩いパターンにフォールバックする。
    # (3) それでも見つからなければ level==1 全部。
    cands = [(t.strip(), p) for level, t, p in toc if level <= 2 and strict_chapter_re.match(t.strip())]
    detection_mode = "strict"
    if not cands:
        cands = [(t.strip(), p) for level, t, p in toc if level <= 2 and loose_chapter_re.match(t.strip())]
        detection_mode = "loose"
    if not cands:
        cands = [(t.strip(), p) for level, t, p in toc if level == 1]
        detection_mode = "level1-fallback"
    seen_pages = set()
    num = 0
    for title, page in sorted(cands, key=lambda c: c[1]):
        if page in seen_pages or page < 1:
            continue
        seen_pages.add(page)
        num += 1
        chapters.append((num, page, title))
    if detection_mode != "strict":
        print(f"WARN: 章検出は厳密パターン(Chapter N / 第N章)では0件だったため {detection_mode} にフォールバックした。誤検出(節を章として拾う等)の可能性があるため toc.txt と chapter_NN= 行を必ず突き合わせて確認すること。", file=sys.stderr)
    elif len(chapters) > 20:
        print(f"WARN: 検出章数が {len(chapters)} 件と多い。20章超の書籍は正常なこともあるが、節見出しの誤検出でないか toc.txt で必ず確認すること。", file=sys.stderr)

if not chapters and not manual:
    # フォールバック: アウトラインが無い PDF は本文ページ先頭の章見出しを走査する。
    # 誤検出対策: ページ先頭 5 行・80 文字以内のみ採用、章見出しが 3 行以上ある
    # ページ(目次)は除外、章番号ごとに初出ページを採り、ページ番号の単調増加を要求。
    head_re = re.compile(r"^(?:Chapter|CHAPTER)\s+(\d+)\b\s*(.*)$")
    head_re_ja = re.compile(r"^第\s*(\d+)\s*章\s*(.*)$")
    first_page = {}  # num -> (page_1indexed, title)
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if len(re.findall(r"^\s*(?:Chapter|CHAPTER)\s+\d+\b|^\s*第\s*\d+\s*章", text, re.MULTILINE)) >= 3:
            continue  # 目次・索引ページ
        head_lines = [l.strip() for l in text.splitlines() if l.strip()][:5]
        for l in head_lines:
            if len(l) > 80:
                continue
            m = head_re.match(l) or head_re_ja.match(l)
            if m:
                num = int(m.group(1))
                if num not in first_page:
                    first_page[num] = (i + 1, m.group(2).strip())
                break
    prev_page = 0
    for num in sorted(first_page):
        page, rest = first_page[num]
        if page <= prev_page:
            continue
        title = f"Chapter {num} {rest}".strip() if rest else f"Chapter {num}"
        chapters.append((num, page, title))
        prev_page = page
    if chapters:
        lines.append("#")
        lines.append("# (アウトラインなし: 本文ページ先頭の章見出し走査で検出した)")

lines.append("#")
lines.append("# 章として採用した境界(chapter|num|start|end|title)")
for i, (num, start, title) in enumerate(chapters):
    end = chapters[i + 1][1] - 1 if i + 1 < len(chapters) else n_pages
    lines.append(f"chapter|{num}|{start}|{end}|{title}")
    print(f"CH|{num}|{start}|{end}|{title}")

with open(toc_out, "w") as f:
    f.write("\n".join(lines) + "\n")

print(f"PAGES|{n_pages}")
PY
)" || die "PyMuPDF の章検出に失敗した"

PYMUPDF_PAGES="$(printf '%s\n' "$CHAPTER_LINES" | awk -F'|' '/^PAGES\|/{print $2; exit}')"
[ -n "$PAGES" ] || PAGES="$PYMUPDF_PAGES"

CHAPTERS_COUNT=0
CHAPTER_KV=""
while IFS='|' read -r tag num start end title; do
  [ "$tag" = "CH" ] || continue
  CHAPTERS_COUNT=$((CHAPTERS_COUNT + 1))
  padded="$(printf '%02d' "$num")"
  ch_txt="${WORK_CHAPTERS_DIR}/ch-${padded}.txt"
  pdftotext -layout -f "$start" -l "$end" "$WORK_PDF" "$ch_txt" 2>/dev/null \
    || die "章テキスト抽出に失敗: ch-${padded} (p.${start}-${end})"
  CHAPTER_KV="${CHAPTER_KV}chapter_${padded}=${start}-${end}|${title}
"
done <<EOF_CH
$CHAPTER_LINES
EOF_CH

if [ "$FORCE" -eq 1 ]; then
  rm -f "$PDF" "$TXT" "$TOC" "$CHAPTERS_DIR"/ch-*.txt 2>/dev/null || true
fi
mkdir -p "$CHAPTERS_DIR"
mv -f "$WORK_PDF" "$PDF"
mv -f "$WORK_TXT" "$TXT"
mv -f "$WORK_TOC" "$TOC"
find "$WORK_CHAPTERS_DIR" -maxdepth 1 -type f -name 'ch-*.txt' -exec mv -f {} "$CHAPTERS_DIR"/ \;

printf 'pdf=%s\n' "$PDF"
printf 'txt=%s\n' "$TXT"
printf 'slug=%s\n' "$SLUG"
printf 'pages=%s\n' "$PAGES"
printf 'title=%s\n' "$TITLE"
printf 'year_hint=%s\n' "$YEAR_HINT"
printf 'toc=%s\n' "$TOC"
printf 'chapters_dir=%s\n' "$CHAPTERS_DIR"
printf 'chapters_count=%s\n' "$CHAPTERS_COUNT"
if [ -n "$CHAPTER_KV" ]; then
  printf '%s' "$CHAPTER_KV"
fi
if [ -n "$INPUT_URL" ]; then
  printf 'url=%s\n' "$INPUT_URL"
fi
if [ "$CHAPTERS_COUNT" -eq 0 ]; then
  err "warn: 章境界を検出できなかった。toc.txt のアウトラインを確認し、--chapters \"1=<開始p>,2=<開始p>,...\" で再実行すること。"
fi
