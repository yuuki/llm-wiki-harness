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
if [ "${1:-}" = "--print" ]; then
  printf 'Fake Video Title\n'
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
path="${out//%(ext)s/mp4}"
mkdir -p "$(dirname "$path")"
printf 'downloaded video\n' > "$path"
printf '1\n00:00:00,000 --> 00:00:01,000\ncaption\n' > "${path%.*}.en.srt"
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
  PATH="$TEST_PATH" bash prepare-video-source.sh talk.mp4 sample-video >/tmp/prepare-video-source.out

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

  PATH="$TEST_PATH" bash prepare-video-source.sh https://example.test/watch?v=abc123 sample-url >/tmp/prepare-video-source-url.out

  assert_no_files ".raw/videos/sample-url/media/video.*"
  assert_file ".raw/videos/sample-url/audio.m4a"
  assert_file ".raw/videos/sample-url/frames/frame-001.jpg"
  assert_file ".raw/videos/sample-url/source.url"
  grep -q '"video": ""' ".raw/videos/sample-url/metadata.json" || fail "metadata video field should be empty"
}

run_prepare_slide_media_local_video_does_not_save_video() {
  local work="$TMP_ROOT/slide-media"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-slide-media.sh" "$work/"
  cd "$work"

  printf 'fake video\n' > talk.mp4
  PATH="$TEST_PATH" bash prepare-slide-media.sh slide-slug talk.mp4 >/tmp/prepare-slide-media.out

  assert_no_files ".raw/slides/slide-slug/media/*.mp4"
  assert_file ".raw/slides/slide-slug/media/audio.m4a"
}

run_prepare_slide_media_url_video_does_not_save_video() {
  local work="$TMP_ROOT/slide-media-url"
  mkdir -p "$work"
  cp "$ROOT/scripts/prepare-slide-media.sh" "$work/"
  cd "$work"

  PATH="$TEST_PATH" bash prepare-slide-media.sh slide-url https://example.test/watch?v=abc123 >/tmp/prepare-slide-media-url.out

  assert_no_files ".raw/slides/slide-url/media/*.mp4"
  assert_file ".raw/slides/slide-url/media/audio.m4a"
  assert_file ".raw/slides/slide-url/media/media.url"
}

run_prepare_video_source_local_does_not_save_video
run_prepare_video_source_url_does_not_save_video
run_prepare_slide_media_local_video_does_not_save_video
run_prepare_slide_media_url_video_does_not_save_video

printf 'media helpers do not save video files\n'
