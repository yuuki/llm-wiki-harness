#!/usr/bin/env bash
# prepare-video-source.sh — wiki-ingest-video の動画準備ヘルパー
#
# ローカル動画または動画 URL から、可能なら音声、代表フレーム、文字起こしを生成する。
# 動画本体は vault に保存しない。
# wiki/ 配下は一切変更しない。
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

usage() {
  die "usage: prepare-video-source.sh [--force] [--transcript transcript.md] <video-file-or-url> [slug]"
}

FORCE=0
TRANSCRIPT_INPUT=""
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
MEDIA_DIR="${DEST_DIR}/media"
FRAMES_DIR="${DEST_DIR}/frames"
METADATA="${DEST_DIR}/metadata.json"
SOURCE_URL="${DEST_DIR}/source.url"
mkdir -p "$MEDIA_DIR" "$FRAMES_DIR"

if [ "$FORCE" -ne 1 ] && find "$DEST_DIR" -type f | grep -q .; then
  die "既存の .raw/videos/${SLUG}/ にファイルがある。原本不変を守るため停止する。上書きする場合は --force を明示する。"
fi

if [ "$FORCE" -eq 1 ]; then
  rm -f "$MEDIA_DIR"/video.* "$DEST_DIR"/audio.m4a "$DEST_DIR"/transcript.md "$DEST_DIR"/source.url "$DEST_DIR"/metadata.json 2>/dev/null || true
  rm -f "$FRAMES_DIR"/frame-*.jpg 2>/dev/null || true
fi

VIDEO=""
VIDEO_WORK=""
AUDIO="${DEST_DIR}/audio.m4a"
TRANSCRIPT="${DEST_DIR}/transcript.md"
URL=""
TITLE=""
DURATION=""
DOWNLOAD_STATUS="not_requested"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_metadata() {
  {
    printf '{\n'
    printf '  "slug": "%s",\n' "$(json_escape "$SLUG")"
    printf '  "url": "%s",\n' "$(json_escape "$URL")"
    printf '  "title": "%s",\n' "$(json_escape "$TITLE")"
    printf '  "video": "%s",\n' "$(json_escape "$VIDEO")"
    printf '  "audio": "%s",\n' "$(json_escape "$AUDIO_OUT")"
    printf '  "transcript": "%s",\n' "$(json_escape "$TRANSCRIPT_OUT")"
    printf '  "frames_dir": "%s",\n' "$(json_escape "$FRAMES_DIR")"
    printf '  "frames": %s,\n' "${FRAMES:-0}"
    printf '  "duration": "%s",\n' "$(json_escape "$DURATION")"
    printf '  "download_status": "%s"\n' "$(json_escape "$DOWNLOAD_STATUS")"
    printf '}\n'
  } > "$METADATA"
}

if [ -f "$INPUT" ]; then
  is_video_file "$INPUT" || die "ローカル入力が動画拡張子ではない: $INPUT"
  VIDEO_WORK="$INPUT"
  DOWNLOAD_STATUS="local-reference"
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  URL="$INPUT"
  printf '%s\n' "$URL" > "$SOURCE_URL"
  if command -v yt-dlp >/dev/null 2>&1; then
    TITLE="$(yt-dlp --print title --no-playlist "$INPUT" 2>/dev/null | head -n1 || true)"
    tmp_download="$(mktemp -d "${TMPDIR:-/tmp}/prepare-video-source.XXXXXX")"
    # YouTube の既定抽出(web player_client)は形式によって 403 Forbidden を返すことがある。
    # その場合は android player_client への切り替えでダウンロードできることが多い
    # (https://github.com/yt-dlp/yt-dlp#extractor-args)。まず既定で試し、失敗したら
    # android client + 480p 以下のフォーマット指定で再試行する。
    if yt-dlp --no-playlist --write-sub --write-auto-sub --sub-lang "ja,en.*" --convert-subs srt \
      -o "${tmp_download}/video.%(ext)s" "$INPUT" >/dev/null 2>&1; then
      DOWNLOAD_STATUS="processed-transient"
    elif yt-dlp --no-playlist --write-sub --write-auto-sub --sub-lang "ja,en.*" --convert-subs srt \
      --extractor-args "youtube:player_client=android" -f "best[height<=480]/best" \
      -o "${tmp_download}/video.%(ext)s" "$INPUT" >/dev/null 2>&1; then
      DOWNLOAD_STATUS="processed-transient (player_client=android フォールバック)"
      err "warn: 既定の抽出は失敗したため player_client=android で再試行して成功した: $INPUT"
    else
      DOWNLOAD_STATUS="url-only"
    fi
    VIDEO_WORK="$(find "$tmp_download" -maxdepth 1 -type f -name 'video.*' ! -name '*.srt' | head -n1 || true)"
    if [ -z "$VIDEO_WORK" ]; then
      DOWNLOAD_STATUS="url-only"
      err "warn: yt-dlp で取得できなかった(既定・player_client=android とも失敗)。URL のみ記録する: $INPUT"
    fi
    subtitle="$(find "$tmp_download" -maxdepth 1 -type f -name '*.srt' | head -n1 || true)"
    if [ -n "$subtitle" ]; then
      {
        printf '# Transcript\n\n'
        printf 'Source: `%s`\n\n' "$URL"
        cat "$subtitle"
        printf '\n'
      } > "$TRANSCRIPT"
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
  {
    printf '# Transcript\n\n'
    printf 'Source: `%s`\n\n' "$TRANSCRIPT_INPUT"
    cat "$TRANSCRIPT_INPUT"
    printf '\n'
  } > "$TRANSCRIPT"
fi

if [ -n "$VIDEO_WORK" ] && command -v ffprobe >/dev/null 2>&1; then
  DURATION="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VIDEO_WORK" 2>/dev/null || true)"
fi

if [ -n "$VIDEO_WORK" ] && command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -y -i "$VIDEO_WORK" -vn -acodec aac "$AUDIO" >/dev/null 2>&1 \
    || { err "warn: ffmpeg で音声抽出できなかった: $VIDEO_WORK"; AUDIO=""; }

  rm -f "$FRAMES_DIR"/frame-*.jpg 2>/dev/null || true
  if [ -n "$DURATION" ] && printf '%s' "$DURATION" | grep -qE '^[0-9]+([.][0-9]+)?$'; then
    awk -v d="$DURATION" 'BEGIN { n = d < 600 ? 6 : 12; for (i = 1; i <= n; i++) printf "%.3f\n", (d * i) / (n + 1) }' |
      awk '{ printf "%03d %.3f\n", NR, $1 }' |
      while read -r idx ts; do
        ffmpeg -y -ss "$ts" -i "$VIDEO_WORK" -frames:v 1 -q:v 2 "${FRAMES_DIR}/frame-${idx}.jpg" >/dev/null 2>&1 || true
      done
  else
    ffmpeg -y -i "$VIDEO_WORK" -vf fps=1/120 -q:v 2 "${FRAMES_DIR}/frame-%03d.jpg" >/dev/null 2>&1 \
      || err "warn: 代表フレームを抽出できなかった: $VIDEO_WORK"
  fi
elif [ -n "$VIDEO_WORK" ]; then
  err "warn: ffmpeg が無いため音声抽出と代表フレーム抽出を行わない: $VIDEO_WORK"
fi

if [ -n "$AUDIO" ] && [ -f "$AUDIO" ]; then
  if command -v whisper >/dev/null 2>&1; then
    tmp_dir="${DEST_DIR}/whisper"
    mkdir -p "$tmp_dir"
    if whisper "$AUDIO" --output_format txt --output_dir "$tmp_dir" >/dev/null 2>&1; then
      txt="$(find "$tmp_dir" -maxdepth 1 -type f -name '*.txt' | head -n1 || true)"
      if [ -n "$txt" ]; then
        {
          printf '# Transcript\n\n'
          printf 'Source: `%s`\n\n' "$AUDIO"
          cat "$txt"
          printf '\n'
        } > "$TRANSCRIPT"
      fi
    else
      err "warn: whisper による文字起こしに失敗した"
    fi
  elif command -v whisper-cpp >/dev/null 2>&1; then
    err "warn: whisper-cpp は環境ごとにモデル指定が必要なため自動実行しない"
  else
    err "warn: 文字起こしツールが無いため transcript を生成しない"
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
write_metadata

[ -n "${tmp_download:-}" ] && rm -rf "$tmp_download"

printf 'video=%s\n' "$VIDEO"
printf 'audio=%s\n' "$AUDIO_OUT"
printf 'transcript=%s\n' "$TRANSCRIPT_OUT"
printf 'frames_dir=%s\n' "$FRAMES_DIR"
printf 'frames=%s\n' "$FRAMES"
printf 'duration=%s\n' "$DURATION"
printf 'title=%s\n' "$TITLE"
printf 'url=%s\n' "$URL"
printf 'metadata=%s\n' "$METADATA"
printf 'slug=%s\n' "$SLUG"
