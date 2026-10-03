#!/usr/bin/env bash
# Verifies media helpers keep derived artifacts but do not persist video files.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/media-helper-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_file() {
  [ -f "$1" ] || fail "expected file: $1"
}

assert_no_files() {
  local pattern="$1"
  if compgen -G "$pattern" >/dev/null; then
    fail "unexpected files matched: $pattern"
  fi
}

BIN_DIR="$TMP_ROOT/bin"
mkdir -p "$BIN_DIR"
TEST_PATH="$BIN_DIR:/bin:/usr/bin"

cat > "$BIN_DIR/ffprobe" <<'EOF'
#!/usr/bin/env bash
printf '120.0\n'
EOF
chmod +x "$BIN_DIR/ffprobe"

cat > "$BIN_DIR/ffmpeg" <<'EOF'
#!/usr/bin/env bash
out="${@: -1}"
mkdir -p "$(dirname "$out")"
printf 'derived\n' > "$out"
EOF
chmod +x "$BIN_DIR/ffmpeg"

cat > "$BIN_DIR/yt-dlp" <<'EOF'
#!/usr/bin/env bash
# 実際の yt-dlp を模す: メタデータ・字幕・動画を別々の呼び出しで返す。
# FAKE_VIDEO_FAIL=1 のときは、字幕ファイルだけ残して動画取得に失敗する(HTTP 429 で中断した状態)。
args=" $* "
if [ "${1:-}" = "--print" ]; then
  printf 'Fake Video Title\n'
  exit 0
fi
if [[ "$args" == *" --dump-single-json "* ]]; then
  printf '%s\n' '{"title": "Fake \"Quoted\" Title", "uploader": "Fake Channel", "upload_date": "20260924", "duration": 120, "subtitles": {"en": []}, "automatic_captions": {"en-orig": [], "en-ja": []}, "chapters": [{"start_time": 0, "title": "Intro"}]}'
  exit 0
fi

out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    out="$2"
    break
  fi
  shift
done

[ -n "$out" ] || exit 1
if [[ "$args" == *" --skip-download "* ]]; then
  [[ "$args" == *" --sub-langs en "* ]] || exit 1
  printf 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\ncaption\n' > "${out//%(ext)s/en.vtt}"
  exit 0
fi
if [ "${FAKE_VIDEO_FAIL:-0}" = "1" ]; then
  printf 'WEBVTT\n' > "${out//%(ext)s/en-orig.vtt}"
  exit 1
fi
path="${out//%(ext)s/mp4}"
mkdir -p "$(dirname "$path")"
printf 'downloaded video\n' > "$path"
EOF
chmod +x "$BIN_DIR/yt-dlp"

cat > "$BIN_DIR/whisper" <<'EOF'
#!/usr/bin/env bash
out_dir="."
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output_dir" ]; then
    out_dir="$2"
    break
  fi
  shift
done
mkdir -p "$out_dir"
printf 'transcript\n' > "$out_dir/transcript.txt"
EOF
chmod +x "$BIN_DIR/whisper"

run_prepare_video_source_local_does_not_save_video() {
  local work="$TMP_ROOT/video-source"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-video-source.sh" "$work/"
  cd "$work"

  printf 'fake video\n' > talk.mp4
  PATH="$TEST_PATH" bash prepare-video-source.sh talk.mp4 sample-video >"$TMP_ROOT"/prepare-video-source.out

  assert_no_files ".raw/videos/sample-video/media/video.*"
  assert_file ".raw/videos/sample-video/audio.m4a"
  assert_file ".raw/videos/sample-video/frames/frame-001.jpg"
  assert_file ".raw/videos/sample-video/metadata.json"
  grep -q '"video": ""' ".raw/videos/sample-video/metadata.json" || fail "metadata video field should be empty"
}

run_prepare_video_source_url_does_not_save_video() {
  local work="$TMP_ROOT/video-source-url"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-video-source.sh" "$work/"
  cd "$work"

  PATH="$TEST_PATH" bash prepare-video-source.sh https://example.test/watch?v=abc123 sample-url >"$TMP_ROOT"/prepare-video-source-url.out

  assert_no_files ".raw/videos/sample-url/media/video.*"
  assert_file ".raw/videos/sample-url/audio.m4a"
  assert_file ".raw/videos/sample-url/frames/frame-001.jpg"
  assert_file ".raw/videos/sample-url/source.url"
  grep -q '"video": ""' ".raw/videos/sample-url/metadata.json" || fail "metadata video field should be empty"
  grep -q 'caption' ".raw/videos/sample-url/transcript.md" || fail "manual subtitles should become transcript"
  grep -q '"transcript_source": "manual-subtitles:en"' ".raw/videos/sample-url/metadata.json" || fail "manual subtitles should be preferred"
  python3 -m json.tool ".raw/videos/sample-url/metadata.json" >/dev/null || fail "metadata should be valid JSON even with a quoted title"
  [ ! -d ".raw/videos/sample-url/media" ] || fail "media dir should not be created"
}

# 回帰: 動画取得が失敗して字幕ファイルだけ残っても、字幕を動画と取り違えない。
run_prepare_video_source_subtitle_is_not_video() {
  local work="$TMP_ROOT/video-source-subonly"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-video-source.sh" "$work/"
  cd "$work"

  FAKE_VIDEO_FAIL=1 PATH="$TEST_PATH" bash prepare-video-source.sh https://example.test/watch?v=abc123 sample-subonly >"$TMP_ROOT"/prepare-video-source-subonly.out 2>/dev/null

  assert_no_files ".raw/videos/sample-subonly/audio.m4a"
  assert_no_files ".raw/videos/sample-subonly/frames/frame-*.jpg"
  assert_file ".raw/videos/sample-subonly/transcript.md"
  grep -q '"download_status": "url-only"' ".raw/videos/sample-subonly/metadata.json" || fail "status should be url-only"
}

run_prepare_slide_media_local_video_does_not_save_video() {
  local work="$TMP_ROOT/slide-media"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-slide-media.sh" "$work/"
  cd "$work"

  printf 'fake video\n' > talk.mp4
  PATH="$TEST_PATH" bash prepare-slide-media.sh slide-slug talk.mp4 >"$TMP_ROOT"/prepare-slide-media.out

  assert_no_files ".raw/slides/slide-slug/media/*.mp4"
  assert_file ".raw/slides/slide-slug/media/audio.m4a"
}

run_prepare_slide_media_url_video_does_not_save_video() {
  local work="$TMP_ROOT/slide-media-url"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-slide-media.sh" "$work/"
  cd "$work"

  PATH="$TEST_PATH" bash prepare-slide-media.sh slide-url https://example.test/watch?v=abc123 >"$TMP_ROOT"/prepare-slide-media-url.out

  assert_no_files ".raw/slides/slide-url/media/*.mp4"
  assert_file ".raw/slides/slide-url/media/audio.m4a"
  assert_file ".raw/slides/slide-url/media/media.url"
}

run_prepare_video_source_local_does_not_save_video
run_prepare_video_source_url_does_not_save_video
run_prepare_video_source_subtitle_is_not_video
run_prepare_slide_media_local_video_does_not_save_video
run_prepare_slide_media_url_video_does_not_save_video

printf 'media helpers do not save video files\n'
