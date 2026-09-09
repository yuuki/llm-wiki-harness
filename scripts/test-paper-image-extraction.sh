#!/usr/bin/env bash
# Verifies paper PDF image extraction keeps raw artifacts and fetch output keys.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/paper-image-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_file() {
  [ -f "$1" ] || fail "expected file: $1"
}

assert_dir() {
  [ -d "$1" ] || fail "expected directory: $1"
}

create_vector_pdf() {
  local out="$1"
  python3 - "$out" <<'PY'
from pathlib import Path
import sys

out = Path(sys.argv[1])
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 4 0 R >>",
]
stream = b"0.1 0.4 0.8 rg\n20 20 160 120 re\nf\n"
objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))

data = bytearray(b"%PDF-1.4\n")
offsets = [0]
for index, obj in enumerate(objects, start=1):
    offsets.append(len(data))
    data.extend(b"%d 0 obj\n%s\nendobj\n" % (index, obj))
xref = len(data)
data.extend(b"xref\n0 %d\n" % (len(objects) + 1))
data.extend(b"0000000000 65535 f \n")
for offset in offsets[1:]:
    data.extend(b"%010d 00000 n \n" % offset)
data.extend(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
out.write_bytes(data)
PY
}

create_rgb_image_pdf() {
  local out="$1"
  python3 - "$out" <<'PY'
from pathlib import Path
import sys
import zlib

out = Path(sys.argv[1])
rgb = bytes([
    255, 0, 0,     0, 255, 0,
    0, 0, 255,     255, 255, 0,
])
image = zlib.compress(rgb)
content = b"q\n80 0 0 80 20 20 cm\n/Im1 Do\nQ\n"
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 120] /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>",
    b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream" % (len(image), image),
    b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
]

data = bytearray(b"%PDF-1.4\n")
offsets = [0]
for index, obj in enumerate(objects, start=1):
    offsets.append(len(data))
    data.extend(b"%d 0 obj\n%s\nendobj\n" % (index, obj))
xref = len(data)
data.extend(b"xref\n0 %d\n" % (len(objects) + 1))
data.extend(b"0000000000 65535 f \n")
for offset in offsets[1:]:
    data.extend(b"%010d 00000 n \n" % offset)
data.extend(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
out.write_bytes(data)
PY
}

run_extract_paper_images_renders_vector_pages() {
  local work="$TMP_ROOT/extract"
  mkdir -p "$work"
  cd "$work"

  create_vector_pdf "vector.pdf"
  node "$ROOT/scripts/extract-paper-images.mjs" "vector.pdf" ".raw/papers/vector/images" > extract.out

  assert_dir ".raw/papers/vector/images"
  assert_file ".raw/papers/vector/images/images.json"
  assert_file ".raw/papers/vector/images/page-001.png"
  grep -q '^images_dir=.raw/papers/vector/images$' extract.out || fail "missing images_dir output"
  grep -q '^image_manifest=.raw/papers/vector/images/images.json$' extract.out || fail "missing image_manifest output"
  grep -q '"kind": "page-render"' ".raw/papers/vector/images/images.json" || fail "missing page-render manifest entry"
}

run_extract_paper_images_extracts_rgb_xobject() {
  local work="$TMP_ROOT/rgb"
  mkdir -p "$work"
  cd "$work"

  create_rgb_image_pdf "rgb.pdf"
  node "$ROOT/scripts/extract-paper-images.mjs" "rgb.pdf" ".raw/papers/rgb/images" > extract.out

  assert_file ".raw/papers/rgb/images/images.json"
  assert_file ".raw/papers/rgb/images/image-001-001.png"
  grep -q '"kind": "embedded"' ".raw/papers/rgb/images/images.json" || fail "missing embedded manifest entry"
  grep -q '"width": 2' ".raw/papers/rgb/images/images.json" || fail "missing embedded width"
  grep -q '"height": 2' ".raw/papers/rgb/images/images.json" || fail "missing embedded height"
}

run_fetch_paper_pdf_reports_image_outputs() {
  local work="$TMP_ROOT/fetch"
  mkdir -p "$work/scripts"
  cp "$ROOT/scripts/fetch-paper-pdf.sh" "$work/scripts/"
  cp "$ROOT/scripts/extract-paper-images.mjs" "$work/scripts/"
  [ ! -d "$ROOT/node_modules" ] || ln -s "$ROOT/node_modules" "$work/node_modules"
  cd "$work"

  create_vector_pdf "vector.pdf"
  bash scripts/fetch-paper-pdf.sh "vector.pdf" "vector-paper" > fetch.out

  assert_file ".raw/papers/vector-paper.pdf"
  assert_file ".raw/papers/vector-paper.txt"
  assert_file ".raw/papers/vector-paper/images/images.json"
  assert_file ".raw/papers/vector-paper/images/page-001.png"
  grep -q '^images_dir=.raw/papers/vector-paper/images$' fetch.out || fail "fetch output missing images_dir"
  grep -q '^image_manifest=.raw/papers/vector-paper/images/images.json$' fetch.out || fail "fetch output missing image_manifest"
  grep -q '^images_count=' fetch.out || fail "fetch output missing images_count"
}

run_extract_paper_images_renders_vector_pages
run_extract_paper_images_extracts_rgb_xobject
run_fetch_paper_pdf_reports_image_outputs

printf 'paper image extraction tests passed\n'
