#!/usr/bin/env bash
# prepare-slide-media.sh — wiki-ingest-slides の任意メディア準備ヘルパー
#
# ローカル音源、動画、または動画/音源 URL から、可能なら音声抽出と文字起こしを行う。
# 動画本体は vault に保存しない。音源は補助ソースとして保存してよい。
# wiki/ 配下は一切変更しない。
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }
die() { err "ERROR: $*"; exit 1; }

usage() {
  die "usage: prepare-slide-media.sh [--force] <slug> <media-file-or-url>"
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

[ "$#" -ge 2 ] || usage

SLUG_RAW="$1"
INPUT="$2"

sanitize_name() {
  printf '%s' "$1" | tr ' ' '-' | tr -cd 'A-Za-z0-9._-'
}

SLUG="$(sanitize_name "$SLUG_RAW")"
[ -n "$SLUG" ] || die "slug が空です"
[ "$SLUG" = "$SLUG_RAW" ] || die "slug に使えない文字が含まれる: $SLUG_RAW"

RAW_ROOT=".raw/slides"
DEST_DIR="${RAW_ROOT}/${SLUG}"
MEDIA_DIR="${DEST_DIR}/media"
TRANSCRIPT="${DEST_DIR}/transcript.md"
SOURCE_URL="${MEDIA_DIR}/media.url"

is_video_file() {
  printf '%s' "$1" | grep -qiE '\.(mp4|mov|mkv|webm|avi|m4v)$'
}

is_audio_file() {
  printf '%s' "$1" | grep -qiE '\.(m4a|mp3|wav|aac|flac|ogg)$'
}

is_direct_media_url() {
  printf '%s' "$1" | grep -qiE '\.(m4a|mp3|wav|aac|flac|ogg|mp4|mov|mkv|webm|avi|m4v)([?#].*)?$'
}

if [ "$FORCE" -ne 1 ] && { [ -f "$TRANSCRIPT" ] || { [ -d "$MEDIA_DIR" ] && find "$MEDIA_DIR" -type f | grep -q .; }; }; then
  die "既存の .raw/slides/${SLUG}/media または transcript がある。上書きする場合は --force を明示する。"
fi

mkdir -p "$MEDIA_DIR"
if [ "$FORCE" -eq 1 ]; then
  rm -f "$MEDIA_DIR"/* "$TRANSCRIPT" 2>/dev/null || true
fi

MEDIA=""
VIDEO_WORK=""
AUDIO=""
URL=""
TMP_DOWNLOAD=""

if [ -f "$INPUT" ]; then
  if is_audio_file "$INPUT"; then
    name="$(sanitize_name "$(basename "$INPUT")")"
    [ -n "$name" ] || name="media"
    MEDIA="${MEDIA_DIR}/${name}"
    cp -f "$INPUT" "$MEDIA"
  elif is_video_file "$INPUT"; then
    VIDEO_WORK="$INPUT"
  else
    die "ローカル入力が音源または動画拡張子ではない: $INPUT"
  fi
elif printf '%s' "$INPUT" | grep -qiE '^https?://'; then
  URL="$INPUT"
  printf '%s\n' "$URL" > "$SOURCE_URL"
  TMP_DOWNLOAD="$(mktemp -d "${TMPDIR:-/tmp}/prepare-slide-media.XXXXXX")"
  if is_direct_media_url "$INPUT"; then
    command -v curl >/dev/null 2>&1 || die "curl が見つからない"
    name="$(sanitize_name "$(basename "${INPUT%%\?*}")")"
    [ -n "$name" ] || name="media"
    curl -fsSL --retry 3 --retry-delay 2 \
      -A "Mozilla/5.0 (compatible; wiki-ingest-slides/1.0)" \
      -o "${TMP_DOWNLOAD}/${name}" "$INPUT" \
      || err "warn: 直接メディア URL を取得できなかった。URL のみ記録する: $INPUT"
    if [ -f "${TMP_DOWNLOAD}/${name}" ]; then
      if is_audio_file "${TMP_DOWNLOAD}/${name}"; then
        mv -f "${TMP_DOWNLOAD}/${name}" "${MEDIA_DIR}/${name}"
        MEDIA="${MEDIA_DIR}/${name}"
      elif is_video_file "${TMP_DOWNLOAD}/${name}"; then
        VIDEO_WORK="${TMP_DOWNLOAD}/${name}"
      fi
    fi
  elif command -v yt-dlp >/dev/null 2>&1; then
    if yt-dlp --no-playlist -o "${TMP_DOWNLOAD}/download.%(ext)s" "$INPUT" >/dev/null 2>&1; then
      downloaded="$(find "$TMP_DOWNLOAD" -maxdepth 1 -type f -name 'download.*' ! -name '*.srt' | head -n1 || true)"
      if [ -n "$downloaded" ]; then
        if is_audio_file "$downloaded"; then
          ext="${downloaded##*.}"
          MEDIA="${MEDIA_DIR}/download.${ext}"
          mv -f "$downloaded" "$MEDIA"
        elif is_video_file "$downloaded"; then
          VIDEO_WORK="$downloaded"
        fi
      fi
    else
      err "warn: yt-dlp で取得できなかった。URL のみ記録する: $INPUT"
    fi
  else
    err "warn: yt-dlp が無いため URL の保存だけ行う: $INPUT"
  fi
else
  die "入力を解釈できない(ローカル音源/動画ファイルまたは URL): $INPUT"
fi

if [ -n "$MEDIA" ]; then
  if is_audio_file "$MEDIA"; then
    AUDIO="$MEDIA"
  fi
fi

if [ -n "$VIDEO_WORK" ]; then
  if command -v ffmpeg >/dev/null 2>&1; then
    AUDIO="${MEDIA_DIR}/audio.m4a"
    ffmpeg -y -i "$VIDEO_WORK" -vn -acodec aac "$AUDIO" >/dev/null 2>&1 \
      || { err "warn: ffmpeg で音声抽出できなかった: $VIDEO_WORK"; AUDIO=""; }
  else
    err "warn: ffmpeg が無いため動画から音声抽出しない: $VIDEO_WORK"
  fi
fi

if [ -n "$AUDIO" ]; then
  if command -v whisper >/dev/null 2>&1; then
    tmp_dir="${MEDIA_DIR}/whisper"
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
    err "warn: whisper-cpp は環境ごとにモデル指定が必要なため自動実行しない。既存 transcript がある場合は .raw/slides/${SLUG}/transcript.md に手動配置する。"
  else
    err "warn: 文字起こしツールが無いため transcript を生成しない"
  fi
fi

[ -n "$TMP_DOWNLOAD" ] && rm -rf "$TMP_DOWNLOAD"

printf 'media_dir=%s\n' "$MEDIA_DIR"
printf 'media=%s\n' "$MEDIA"
printf 'audio=%s\n' "$AUDIO"
if [ -f "$TRANSCRIPT" ]; then
  printf 'transcript=%s\n' "$TRANSCRIPT"
else
  printf 'transcript=\n'
fi
printf 'url=%s\n' "$URL"
printf 'slug=%s\n' "$SLUG"
