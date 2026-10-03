#!/usr/bin/env bash
# prepare-video-source.sh — wiki-ingest-video の動画準備ヘルパー
#
# ローカル動画または動画 URL から、可能なら音声、代表フレーム、文字起こしを生成する。
# 動画本体は vault に保存しない。wiki/ 配下は一切変更しない。
#
# URL 入力は 3 段に分けて取得する(1 段の失敗で他を巻き込まないため):
#   1. メタデータ(--dump-single-json)
#   2. 字幕(言語コードを完全一致で 1 つだけ。手動字幕を自動字幕より優先)
#   3. 動画(字幕は取らない)
# 字幕と動画を 1 回の yt-dlp で取ると、`en.*` のような正規表現が自動翻訳字幕
# (en-ja 等)まで拾って HTTP 429 を受け、動画ごと中断する。
#
# スライドの多い講演では、全長を等分した代表フレームは話者の映像ばかりになりやすい。
# そこで vault 外の作業ディレクトリに、一定間隔の候補フレームとコンタクトシートも残す
# (candidates_dir / contact_sheets)。エージェントはシートでスライドを探し、
# 選んだ候補を frames/ へ追加する。
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

usage() {
  die "usage: prepare-video-source.sh [--force] [--transcript transcript.md] [--candidate-interval SEC] <video-file-or-url> [slug]"
}

FORCE=0
TRANSCRIPT_INPUT=""
CANDIDATE_INTERVAL=20
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --transcript)
      [ "$#" -ge 2 ] || usage
      TRANSCRIPT_INPUT="$2"
      shift 2
      ;;
    --candidate-interval)
      [ "$#" -ge 2 ] || usage
      CANDIDATE_INTERVAL="$2"
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
printf '%s' "$CANDIDATE_INTERVAL" | grep -qE '^[0-9]+$' || die "--candidate-interval は正の整数秒: $CANDIDATE_INTERVAL"
[ "$CANDIDATE_INTERVAL" -gt 0 ] || die "--candidate-interval は正の整数秒: $CANDIDATE_INTERVAL"

INPUT="$1"
SLUG_ARG="${2:-}"
RAW_ROOT=".raw/videos"

sanitize_name() {
  printf '%s' "$1" | tr ' ' '-' | tr -cd 'A-Za-z0-9._-'
}

slug_from_url() {
  youtube_id="$(printf '%s' "$1" | sed -nE 's#.*[?&]v=([A-Za-z0-9_-]{6,}).*#\1#p; s#.*youtu\.be/([A-Za-z0-9_-]{6,}).*#\1#p' | head -n1)"
  if [ -n "$youtube_id" ]; then
    printf 'youtube-%s' "$youtube_id"
  elif command -v yt-dlp >/dev/null 2>&1; then
    yt-dlp --print id --no-playlist "$1" 2>/dev/null | head -n1 | tr -cd 'A-Za-z0-9._-' || true
  else
    host_path="$(printf '%s' "$1" | sed -E 's#^https?://##; s#[?#].*$##' | tr '/:' '--')"
    hash="$(printf '%s' "$1" | cksum | awk '{print $1}')"
    printf '%s-%s' "$host_path" "$hash" | tr ' ' '-' | tr -cd 'A-Za-z0-9._-'
  fi
}

is_video_file() {
  printf '%s' "$1" | grep -qiE '\.(mp4|mov|mkv|webm|avi|m4v)$'
}

# YouTube の URL は app= や si= などの追跡用パラメータを落とした正規形で記録する。
canonical_url() {
  youtube_id="$(printf '%s' "$1" | sed -nE 's#.*[?&]v=([A-Za-z0-9_-]{6,}).*#\1#p; s#.*youtu\.be/([A-Za-z0-9_-]{6,}).*#\1#p' | head -n1)"
  if [ -n "$youtube_id" ]; then
    printf 'https://www.youtube.com/watch?v=%s' "$youtube_id"
  else
    printf '%s' "$1"
  fi
}

if [ -n "$SLUG_ARG" ]; then
  SLUG="$(sanitize_name "$SLUG_ARG")"
elif [ -f "$INPUT" ]; then
  SLUG="$(sanitize_name "$(basename "${INPUT%.*}")")"
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  SLUG="$(slug_from_url "$INPUT")"
else
  die "入力を解釈できない(ローカル動画ファイルまたは URL): $INPUT"
fi
[ -n "$SLUG" ] || SLUG="video"

DEST_DIR="${RAW_ROOT}/${SLUG}"
FRAMES_DIR="${DEST_DIR}/frames"
METADATA="${DEST_DIR}/metadata.json"
SOURCE_URL="${DEST_DIR}/source.url"

if [ "$FORCE" -ne 1 ] && [ -d "$DEST_DIR" ] && find "$DEST_DIR" -type f | grep -q .; then
  die "既存の .raw/videos/${SLUG}/ にファイルがある。原本不変を守るため停止する。上書きする場合は --force を明示する。"
fi
mkdir -p "$FRAMES_DIR"

if [ "$FORCE" -eq 1 ]; then
  rm -f "$DEST_DIR"/media/video.* "$DEST_DIR"/audio.m4a "$DEST_DIR"/transcript.md "$DEST_DIR"/source.url "$DEST_DIR"/metadata.json 2>/dev/null || true
  rmdir "$DEST_DIR"/media 2>/dev/null || true
  rm -f "$FRAMES_DIR"/frame-*.jpg 2>/dev/null || true
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/prepare-video-source.XXXXXX")"
INFO_JSON="${WORK}/info.json"
CANDIDATES_DIR="${WORK}/candidates"
SHEETS_DIR="${WORK}/contact-sheets"

VIDEO_WORK=""
AUDIO="${DEST_DIR}/audio.m4a"
TRANSCRIPT="${DEST_DIR}/transcript.md"
URL=""
TITLE=""
DURATION=""
DOWNLOAD_STATUS="not_requested"
TRANSCRIPT_SOURCE="none"
SUB_CHOICE=""

# 動画拡張子のファイルだけを返す。字幕(.vtt/.srt)を動画と取り違えない。
find_video_in() {
  find "$1" -maxdepth 1 -type f -name 'video.*' | while read -r f; do
    if is_video_file "$f"; then printf '%s\n' "$f"; fi
  done | head -n1
}

# info.json から字幕を 1 つ選ぶ。出典として使うため、翻訳ではなく動画の原語を優先する。
# 原語は info の language、無ければ自動字幕の <lang>-orig から推定する。
# 順序: 原語の手動字幕 → 原語系の手動字幕(en-US 等)→ 自動字幕の原語(<lang>-orig)
#       → 原語の自動字幕 → ja / en の手動字幕 → ja / en の自動字幕。
# 出力は "manual:<code>" か "auto:<code>"。
choose_subtitle() {
  python3 - "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
manual = [k for k in (d.get("subtitles") or {}) if k != "live_chat"]
auto = list((d.get("automatic_captions") or {}))
orig_auto = [k for k in auto if k.endswith("-orig")]
lang = (d.get("language") or "").split("-")[0]
if not lang and orig_auto:
    lang = orig_auto[0][: -len("-orig")].split("-")[0]
def pick(kind, cands):
    for c in cands:
        if c:
            print(f"{kind}:{c}")
            sys.exit(0)
if lang:
    pick("manual", [lang if lang in manual else ""])
    pick("manual", [k for k in manual if k.split("-")[0] == lang])
pick("auto", orig_auto[:1])
if lang:
    pick("auto", [lang if lang in auto else ""])
pick("manual", [p for p in ("ja", "en") if p in manual])
pick("auto", [p for p in ("ja", "en") if p in auto])
PY
}

# VTT/SRT を 1 分単位の時刻つき Markdown に変換する。自動字幕の重複行(ロールアップ)も落とす。
subs_to_markdown() {
  python3 - "$1" "$2" "$3" <<'PY'
import re, sys
path, label, url = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8", errors="replace").read().replace("\r", "")
out = ["# Transcript", "", f"- Source: {label}"]
if url:
    out.append(f"- URL: {url}")
out.append("")
cur, buf, last = None, [], None
for blk in re.split(r"\n\s*\n", text):
    lines = [l for l in blk.strip().split("\n") if l.strip()]
    idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
    if idx is None:
        continue
    start = lines[idx].split("-->")[0].strip().replace(",", ".")
    parts = start.split(":")
    try:
        if len(parts) == 3:
            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            secs = int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        continue
    for raw in lines[idx + 1:]:
        line = re.sub(r"<[^>]+>", "", raw).strip()
        if not line or line == last:
            continue
        last = line
        minute = int(secs // 60)
        if minute != cur:
            if buf:
                out += [f"[{cur // 60:02d}:{cur % 60:02d}:00] " + " ".join(buf), ""]
            cur, buf = minute, []
        buf.append(line)
if buf:
    out.append(f"[{cur // 60:02d}:{cur % 60:02d}:00] " + " ".join(buf))
print("\n".join(out))
PY
}

if [ -f "$INPUT" ]; then
  is_video_file "$INPUT" || die "ローカル入力が動画拡張子ではない: $INPUT"
  VIDEO_WORK="$INPUT"
  DOWNLOAD_STATUS="local-reference"
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  URL="$(canonical_url "$INPUT")"
  printf '%s\n' "$URL" > "$SOURCE_URL"
  if command -v yt-dlp >/dev/null 2>&1; then
    # 1. メタデータ
    if yt-dlp --dump-single-json --skip-download --no-playlist "$URL" > "$INFO_JSON" 2>/dev/null; then
      TITLE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("title") or "")' "$INFO_JSON" 2>/dev/null || true)"
    else
      rm -f "$INFO_JSON"
      TITLE="$(yt-dlp --print title --no-playlist "$URL" 2>/dev/null | head -n1 || true)"
    fi

    # 2. 字幕(完全一致の言語コード 1 つ。失敗しても動画取得は続ける)
    [ -f "$INFO_JSON" ] && SUB_CHOICE="$(choose_subtitle "$INFO_JSON" || true)"
    if [ -n "$SUB_CHOICE" ]; then
      kind="${SUB_CHOICE%%:*}"
      lang="${SUB_CHOICE#*:}"
      flag="--write-subs"
      [ "$kind" = "auto" ] && flag="--write-auto-subs"
      yt-dlp --skip-download --no-playlist "$flag" --sub-langs "$lang" --sub-format vtt --ignore-errors \
        -o "${WORK}/sub.%(ext)s" "$URL" >/dev/null 2>&1 || err "warn: 字幕の取得に失敗した($SUB_CHOICE)"
      sub_file="$(find "$WORK" -maxdepth 1 -type f \( -name 'sub.*.vtt' -o -name 'sub.*.srt' \) | head -n1 || true)"
      if [ -n "$sub_file" ]; then
        label="YouTube 手動字幕($lang)"
        [ "$kind" = "auto" ] && label="YouTube 自動字幕($lang)"
        subs_to_markdown "$sub_file" "$label" "$URL" > "$TRANSCRIPT"
        TRANSCRIPT_SOURCE="${kind}-subtitles:${lang}"
      fi
    fi

    # 3. 動画(字幕は取らない)。既定 client → android → web_embedded の順に試す。
    fmt='bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]/bv*[height<=480]+ba/b'
    for client in "" android web_embedded; do
      extra=()
      [ -n "$client" ] && extra=(--extractor-args "youtube:player_client=${client}")
      if yt-dlp --no-playlist --no-write-subs --no-write-auto-subs -f "$fmt" --merge-output-format mp4 \
        ${extra[@]+"${extra[@]}"} -o "${WORK}/video.%(ext)s" "$URL" >/dev/null 2>&1; then
        VIDEO_WORK="$(find_video_in "$WORK" || true)"
        if [ -n "$VIDEO_WORK" ]; then
          if [ -z "$client" ]; then
            DOWNLOAD_STATUS="processed-transient"
          else
            DOWNLOAD_STATUS="processed-transient (player_client=${client} フォールバック)"
            err "warn: 既定の抽出が失敗したため player_client=${client} で取得した: $URL"
          fi
          break
        fi
      fi
    done
    if [ -z "$VIDEO_WORK" ]; then
      DOWNLOAD_STATUS="url-only"
      err "warn: yt-dlp で動画を取得できなかった(既定・android・web_embedded とも失敗)。字幕とメタデータだけ記録する: $URL"
    fi
  else
    DOWNLOAD_STATUS="url-only"
    err "warn: yt-dlp が無いため URL の保存だけ行う: $INPUT"
  fi
else
  die "入力を解釈できない(ローカル動画ファイルまたは URL): $INPUT"
fi

if [ -n "$TRANSCRIPT_INPUT" ]; then
  [ -f "$TRANSCRIPT_INPUT" ] || die "指定された transcript が見つからない: $TRANSCRIPT_INPUT"
  case "$TRANSCRIPT_INPUT" in
    *.vtt|*.srt) subs_to_markdown "$TRANSCRIPT_INPUT" "提供された字幕ファイル" "$URL" > "$TRANSCRIPT" ;;
    *)
      {
        printf '# Transcript\n\n'
        printf -- '- Source: 提供された transcript\n\n'
        cat "$TRANSCRIPT_INPUT"
        printf '\n'
      } > "$TRANSCRIPT"
      ;;
  esac
  TRANSCRIPT_SOURCE="provided"
fi

if [ -n "$VIDEO_WORK" ] && command -v ffprobe >/dev/null 2>&1; then
  DURATION="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VIDEO_WORK" 2>/dev/null || true)"
fi
if [ -z "$DURATION" ] && [ -f "$INFO_JSON" ]; then
  DURATION="$(python3 -c 'import json,sys;v=json.load(open(sys.argv[1])).get("duration");print(v if v is not None else "")' "$INFO_JSON" 2>/dev/null || true)"
fi

FRAME_TIMES="${WORK}/frame-times.tsv"
: > "$FRAME_TIMES"
CANDIDATES=0
SHEETS=0
if [ -n "$VIDEO_WORK" ] && command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -y -i "$VIDEO_WORK" -vn -acodec aac "$AUDIO" >/dev/null 2>&1 \
    || { err "warn: ffmpeg で音声抽出できなかった: $VIDEO_WORK"; AUDIO=""; }

  rm -f "$FRAMES_DIR"/frame-*.jpg 2>/dev/null || true
  if [ -n "$DURATION" ] && printf '%s' "$DURATION" | grep -qE '^[0-9]+([.][0-9]+)?$'; then
    # 代表フレーム: 2 分に 1 枚を目安に 6〜30 枚。各区間の中点を取る。
    awk -v d="$DURATION" 'BEGIN { n = int(d / 120 + 0.5); if (n < 6) n = 6; if (n > 30) n = 30;
      for (i = 1; i <= n; i++) printf "%03d %.3f\n", i, d * (i - 0.5) / n }' |
      while read -r idx ts; do
        ffmpeg -y -ss "$ts" -i "$VIDEO_WORK" -frames:v 1 -q:v 2 "${FRAMES_DIR}/frame-${idx}.jpg" >/dev/null 2>&1 || true
        printf 'frame-%s\t%s\n' "$idx" "$ts" >> "$FRAME_TIMES"
      done

    # 候補フレーム(vault 外): 一定間隔の原寸フレームと 4x4 のコンタクトシート。
    mkdir -p "$CANDIDATES_DIR" "$SHEETS_DIR"
    ffmpeg -y -i "$VIDEO_WORK" -vf "fps=1/${CANDIDATE_INTERVAL}" -q:v 3 "${CANDIDATES_DIR}/raw-%05d.jpg" >/dev/null 2>&1 \
      || err "warn: 候補フレームを抽出できなかった: $VIDEO_WORK"
    # fps フィルタの n 枚目は (n-1)*間隔 秒付近。ファイル名に時刻 HHMMSS を入れ、番号の読み違いを防ぐ。
    while read -r f; do
      [ -n "$f" ] || continue
      n="$(basename "$f" .jpg)"; n="${n#raw-}"
      printf '%s' "$n" | grep -qE '^[0-9]+$' || continue
      n=$(( 10#$n - 1 ))
      t=$(( n * CANDIDATE_INTERVAL ))
      mv "$f" "$(printf '%s/cand-%02d%02d%02d.jpg' "$CANDIDATES_DIR" $(( t / 3600 )) $(( t % 3600 / 60 )) $(( t % 60 )))"
    done < <(find "$CANDIDATES_DIR" -maxdepth 1 -type f -name 'raw-*.jpg' | sort)
    CANDIDATES="$(find "$CANDIDATES_DIR" -maxdepth 1 -type f -name 'cand-*.jpg' | wc -l | tr -d ' ')"
    if [ "$CANDIDATES" -gt 0 ]; then
      # シートは cand-*.jpg を時刻順に 16 枚ずつ、左上から行優先で並べる。対応表は sheets.tsv。
      ls "$CANDIDATES_DIR"/cand-*.jpg | sort | awk -v dir="$SHEETS_DIR" '{
          s = int((NR - 1) / 16); list = dir "/sheet-" sprintf("%03d", s) ".txt"
          print "file '\''" $0 "'\''" >> list
          n = split($0, p, "/"); printf "sheet-%03d.jpg\t%d\t%s\n", s, (NR - 1) % 16 + 1, p[n] >> (dir "/sheets.tsv")
        }'
      for list in "$SHEETS_DIR"/sheet-*.txt; do
        [ -f "$list" ] || continue
        ffmpeg -y -f concat -safe 0 -i "$list" -vf "scale=320:-2,tile=4x4" -frames:v 1 -update 1 \
          "${list%.txt}.jpg" >/dev/null 2>&1 || err "warn: コンタクトシートを作れなかった: $list"
        rm -f "$list"
      done
      SHEETS="$(find "$SHEETS_DIR" -maxdepth 1 -type f -name 'sheet-*.jpg' | wc -l | tr -d ' ')"
    fi
  else
    ffmpeg -y -i "$VIDEO_WORK" -vf fps=1/120 -q:v 2 "${FRAMES_DIR}/frame-%03d.jpg" >/dev/null 2>&1 \
      || err "warn: 代表フレームを抽出できなかった: $VIDEO_WORK"
  fi
elif [ -n "$VIDEO_WORK" ]; then
  err "warn: ffmpeg が無いため音声抽出と代表フレーム抽出を行わない: $VIDEO_WORK"
fi

if [ "$TRANSCRIPT_SOURCE" = "none" ] && [ -n "$AUDIO" ] && [ -f "$AUDIO" ]; then
  if command -v whisper >/dev/null 2>&1; then
    whisper_dir="${WORK}/whisper"
    mkdir -p "$whisper_dir"
    if whisper "$AUDIO" --output_format txt --output_dir "$whisper_dir" >/dev/null 2>&1; then
      txt="$(find "$whisper_dir" -maxdepth 1 -type f -name '*.txt' | head -n1 || true)"
      if [ -n "$txt" ]; then
        {
          printf '# Transcript\n\n'
          printf -- '- Source: whisper による文字起こし\n\n'
          cat "$txt"
          printf '\n'
        } > "$TRANSCRIPT"
        TRANSCRIPT_SOURCE="whisper"
      fi
    else
      err "warn: whisper による文字起こしに失敗した"
    fi
  elif command -v whisper-cpp >/dev/null 2>&1; then
    err "warn: whisper-cpp は環境ごとにモデル指定が必要なため自動実行しない"
  else
    err "warn: 字幕も文字起こしツールも無いため transcript を生成しない"
  fi
fi

FRAMES="$(find "$FRAMES_DIR" -maxdepth 1 -type f -name 'frame-*.jpg' | wc -l | tr -d ' ')"
if [ -n "$VIDEO_WORK" ] && [ "$FRAMES" = "0" ]; then
  err "warn: 代表フレームが 0 件。映像確認が必要な主張は未確認として扱う。"
fi
AUDIO_OUT=""
[ -n "$AUDIO" ] && [ -f "$AUDIO" ] && AUDIO_OUT="$AUDIO"
TRANSCRIPT_OUT=""
[ -f "$TRANSCRIPT" ] && TRANSCRIPT_OUT="$TRANSCRIPT"

M_SLUG="$SLUG" M_URL="$URL" M_TITLE="$TITLE" M_AUDIO="$AUDIO_OUT" M_TRANSCRIPT="$TRANSCRIPT_OUT" \
M_TRANSCRIPT_SOURCE="$TRANSCRIPT_SOURCE" M_FRAMES_DIR="$FRAMES_DIR" M_FRAMES="$FRAMES" \
M_DURATION="$DURATION" M_STATUS="$DOWNLOAD_STATUS" \
python3 - "$METADATA" "$INFO_JSON" "$FRAME_TIMES" <<'PY'
import json, os, sys
meta_path, info_path, times_path = sys.argv[1:4]
e = os.environ
info = {}
if os.path.exists(info_path):
    try:
        info = json.load(open(info_path))
    except Exception:
        info = {}
def hms(s):
    s = int(float(s)); return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"
frame_times = {}
for line in open(times_path):
    name, ts = line.rstrip("\n").split("\t")
    frame_times[name] = hms(ts)
up = info.get("upload_date") or ""
meta = {
    "slug": e["M_SLUG"],
    "url": e["M_URL"],
    "title": info.get("title") or e["M_TITLE"],
    "uploader": info.get("uploader") or info.get("channel") or "",
    "upload_date": f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else "",
    "video": "",
    "audio": e["M_AUDIO"],
    "transcript": e["M_TRANSCRIPT"],
    "transcript_source": e["M_TRANSCRIPT_SOURCE"],
    "frames_dir": e["M_FRAMES_DIR"],
    "frames": int(e["M_FRAMES"] or 0),
    "frame_times": frame_times,
    "duration": e["M_DURATION"],
    "chapters": [{"start": hms(c.get("start_time", 0)), "title": c.get("title", "")} for c in (info.get("chapters") or [])],
    "download_status": e["M_STATUS"],
}
with open(meta_path, "w") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

# 動画本体と中間物は破棄する。候補フレームとシートだけは vault 外に残す。
find "$WORK" -maxdepth 1 -type f -delete 2>/dev/null || true
rm -rf "${WORK}/whisper"
CANDIDATES_OUT=""
SHEETS_OUT=""
if [ "$CANDIDATES" -gt 0 ]; then
  CANDIDATES_OUT="$CANDIDATES_DIR"
  [ "$SHEETS" -gt 0 ] && SHEETS_OUT="$SHEETS_DIR"
else
  rm -rf "$WORK"
fi

printf 'video=\n'
printf 'audio=%s\n' "$AUDIO_OUT"
printf 'transcript=%s\n' "$TRANSCRIPT_OUT"
printf 'transcript_source=%s\n' "$TRANSCRIPT_SOURCE"
printf 'frames_dir=%s\n' "$FRAMES_DIR"
printf 'frames=%s\n' "$FRAMES"
printf 'candidates_dir=%s\n' "$CANDIDATES_OUT"
printf 'candidates=%s\n' "$CANDIDATES"
printf 'contact_sheets=%s\n' "$SHEETS_OUT"
printf 'duration=%s\n' "$DURATION"
printf 'title=%s\n' "$TITLE"
printf 'url=%s\n' "$URL"
printf 'metadata=%s\n' "$METADATA"
printf 'slug=%s\n' "$SLUG"
