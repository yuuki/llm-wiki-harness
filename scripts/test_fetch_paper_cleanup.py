#!/usr/bin/env python3
"""fetch-paper-pdf.sh が呼ぶ画像掃除の単体試験。

一時ディレクトリに page-render と embedded を置き、`--cleanup-images` を
呼んで page が消え embedded だけ残ることを確かめる。実 PDF は使わない。
実 vault の `.raw/` は触らない。

  python3 scripts/test_fetch_paper_cleanup.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
VERIFY = SCRIPTS / "wiki-verify-ingest.py"


class CleanupCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fetch-cleanup-")
        self.images = Path(self.tmp.name) / "images"
        self.images.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_fixture(self):
        (self.images / "page-001.png").write_bytes(b"page-render")
        (self.images / "image-001-001.png").write_bytes(b"embedded")
        manifest = {
            "pdf": "dummy.pdf",
            "images_dir": str(self.images),
            "pages": 1,
            "images_count": 2,
            "images": [
                {"file": "page-001.png", "kind": "page-render", "page": 1},
                {"file": "image-001-001.png", "kind": "embedded", "page": 1},
            ],
        }
        (self.images / "images.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    def run_cleanup(self, *args):
        return subprocess.run(
            [sys.executable, str(VERIFY), "--cleanup-images", str(self.images)]
            + list(args),
            capture_output=True, text=True)

    def test_cleanup_drops_page_renders_keeps_embedded(self):
        self.write_fixture()
        proc = self.run_cleanup()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "images_count=1")
        self.assertFalse((self.images / "page-001.png").exists())
        self.assertTrue((self.images / "image-001-001.png").exists())
        data = json.loads((self.images / "images.json").read_text(encoding="utf-8"))
        self.assertEqual(data["images_count"], 1)
        self.assertEqual([x["file"] for x in data["images"]], ["image-001-001.png"])
        self.assertEqual(data["pdf"], "dummy.pdf")

    def test_cleanup_drops_page_prefix_even_if_kind_is_wrong(self):
        (self.images / "page-002.png").write_bytes(b"x")
        (self.images / "image-002-001.png").write_bytes(b"y")
        (self.images / "images.json").write_text(json.dumps({
            "images_count": 2,
            "images": [
                {"file": "page-002.png", "kind": "embedded", "page": 2},
                {"file": "image-002-001.png", "kind": "embedded", "page": 2},
            ],
        }), encoding="utf-8")
        proc = self.run_cleanup()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.images / "images.json").read_text(encoding="utf-8"))
        self.assertEqual([x["file"] for x in data["images"]], ["image-002-001.png"])
        self.assertFalse((self.images / "page-002.png").exists())

    def test_cleanup_json_flag(self):
        self.write_fixture()
        proc = self.run_cleanup("--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"images_count": 1})

    def test_cleanup_missing_dir_is_usage_error(self):
        missing = self.images / "居ない"
        proc = subprocess.run(
            [sys.executable, str(VERIFY), "--cleanup-images", str(missing)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_cleanup_refuses_dir_symlink(self):
        self.write_fixture()
        link = Path(self.tmp.name) / "images-link"
        link.symlink_to(self.images)
        proc = subprocess.run(
            [sys.executable, str(VERIFY), "--cleanup-images", str(link)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertTrue((self.images / "page-001.png").exists())
        data = json.loads((self.images / "images.json").read_text(encoding="utf-8"))
        self.assertEqual(data["images_count"], 2)

    def test_cleanup_broken_json_keeps_file_deletes_pages(self):
        (self.images / "page-001.png").write_bytes(b"page-render")
        (self.images / "image-001-001.png").write_bytes(b"embedded")
        raw = "{not json"
        (self.images / "images.json").write_text(raw, encoding="utf-8")
        proc = self.run_cleanup()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            (self.images / "images.json").read_text(encoding="utf-8"), raw)
        self.assertFalse((self.images / "page-001.png").exists())
        self.assertTrue((self.images / "image-001-001.png").exists())
        self.assertEqual(proc.stdout.strip(), "images_count=1")

    def test_cleanup_array_root_not_overwritten(self):
        (self.images / "page-001.png").write_bytes(b"page-render")
        (self.images / "image-001-001.png").write_bytes(b"embedded")
        raw = json.dumps([{"file": "page-001.png", "kind": "page-render"}])
        (self.images / "images.json").write_text(raw, encoding="utf-8")
        proc = self.run_cleanup()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            (self.images / "images.json").read_text(encoding="utf-8"), raw)
        self.assertFalse((self.images / "page-001.png").exists())

    def test_cleanup_images_field_not_a_list(self):
        (self.images / "page-001.png").write_bytes(b"page-render")
        (self.images / "images.json").write_text(
            json.dumps({"images": 3, "pdf": "dummy.pdf"}), encoding="utf-8")
        proc = self.run_cleanup()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads((self.images / "images.json").read_text(encoding="utf-8"))
        self.assertEqual(data["images"], [])
        self.assertEqual(data["pdf"], "dummy.pdf")
        self.assertFalse((self.images / "page-001.png").exists())

    def test_cleanup_skips_page_symlink(self):
        target = Path(self.tmp.name) / "real-page.png"
        target.write_bytes(b"keep")
        (self.images / "page-001.png").symlink_to(target)
        (self.images / "image-001-001.png").write_bytes(b"embedded")
        (self.images / "images.json").write_text(json.dumps({
            "images": [
                {"file": "page-001.png", "kind": "page-render"},
                {"file": "image-001-001.png", "kind": "embedded"},
            ],
        }), encoding="utf-8")
        proc = self.run_cleanup()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.images / "page-001.png").is_symlink())
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main(verbosity=1)
