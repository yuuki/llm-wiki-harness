#!/usr/bin/env python3
"""scripts/contact-sheet.py の回帰テスト。

小さな PNG は手書きで作り、本スクリプトは subprocess で呼ぶ。
描画関数を直接 import して叩かない(システム python 3.9 の再実行経路を含む)。
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


VAULT = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).resolve().with_name("contact-sheet.py")
SYSTEM_PY = "/usr/bin/python3"


def write_png(path, width, height, rgb=(0, 128, 255)):
    """最小限の RGB PNG を Pillow 無しで書く。"""

    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    rows = []
    for _y in range(height):
        rows.append(b"\x00" + bytes(rgb) * width)
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    blob = b"\x89PNG\r\n\x1a\n"
    blob += chunk(b"IHDR", ihdr)
    blob += chunk(b"IDAT", zlib.compress(raw, 9))
    blob += chunk(b"IEND", b"")
    path.write_bytes(blob)


def run_script(args, env=None, timeout=180):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged.setdefault("WIKI_VAULT_ROOT", str(VAULT))
    return subprocess.run(
        [SYSTEM_PY, str(SCRIPT)] + list(args),
        cwd=str(VAULT),
        capture_output=True,
        text=True,
        env=merged,
        timeout=timeout,
    )


def parse_json(proc):
    text = proc.stdout.strip()
    if not text:
        raise AssertionError("stdout が空: stderr=%r" % proc.stderr)
    line = text.splitlines()[-1]
    return json.loads(line)


def fill_images(directory, count, prefix="image-"):
    paths = []
    for i in range(1, count + 1):
        path = directory / ("%s%03d.png" % (prefix, i))
        write_png(path, 8, 8, (i * 17 % 256, 80, 160))
        paths.append(path)
    return paths


class ContactSheetTests(unittest.TestCase):
    def test_twelve_images_one_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 12)
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 12)
            self.assertEqual(len(payload["sheets"]), 1)
            self.assertEqual(payload["unreadable"], [])
            self.assertEqual(payload["cell"], 320)
            self.assertEqual(payload["cols"], 4)
            self.assertTrue(Path(payload["sheets"][0]).is_file())

    def test_thirteen_images_two_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 13)
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 13)
            self.assertEqual(len(payload["sheets"]), 2)
            for sheet in payload["sheets"]:
                self.assertTrue(Path(sheet).is_file())

    def test_manifest_skips_page_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            embedded = src / "image-001.png"
            page = src / "page-001.png"
            write_png(embedded, 8, 8, (10, 20, 30))
            write_png(page, 8, 8, (200, 10, 10))
            manifest = src / "images.json"
            manifest.write_text(
                json.dumps(
                    {
                        "images_dir": str(src),
                        "images": [
                            {
                                "file": "image-001.png",
                                "page": 3,
                                "kind": "embedded",
                            },
                            {
                                "file": "page-001.png",
                                "page": 1,
                                "kind": "page-render",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            proc = run_script(
                ["--manifest", str(manifest), "--out", str(out), "--index-txt"]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(len(payload["sheets"]), 1)
            index = (out / "index.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index), 1)
            self.assertIn("image-001.png", index[0])
            self.assertNotIn("page-001.png", index[0])
            self.assertTrue(index[0].endswith("\t3") or index[0].endswith("3"))

    def test_out_under_raw_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "vault"
            (fake / ".raw" / "papers").mkdir(parents=True)
            src = Path(tmp) / "src"
            src.mkdir()
            fill_images(src, 1)
            out = fake / ".raw" / "papers" / "_cs-should-not-write"
            proc = run_script(
                ["--dir", str(src), "--out", str(out)],
                env={"WIKI_VAULT_ROOT": str(fake)},
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse(out.exists())
            self.assertIn(".raw", proc.stderr)

    def test_unreadable_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            good = fill_images(src, 2)
            broken = src / "image-003.png"
            broken.write_bytes(b"not-a-png")
            before = {p: p.read_bytes() for p in good}
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 3)
            self.assertEqual(len(payload["unreadable"]), 1)
            self.assertTrue(
                any(Path(p).name == "image-003.png" for p in payload["unreadable"])
            )
            self.assertEqual(len(payload["sheets"]), 1)
            self.assertGreater(Path(payload["sheets"][0]).stat().st_size, 100)
            self.assertTrue(Path(payload["sheets"][0]).read_bytes().startswith(b"\x89PNG"))
            for path, blob in before.items():
                self.assertEqual(path.read_bytes(), blob)

    def test_originals_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            paths = fill_images(src, 3)
            before = {p: p.read_bytes() for p in paths}
            mtimes = {p: p.stat().st_mtime_ns for p in paths}
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for path in paths:
                self.assertEqual(path.read_bytes(), before[path])
                self.assertEqual(path.stat().st_mtime_ns, mtimes[path])

    def test_index_txt_line_count_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 5)
            proc = run_script(
                ["--dir", str(src), "--out", str(out), "--index-txt"]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            lines = (out / "index.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), payload["count"])
            self.assertTrue(lines[0].startswith("sheet-001 r1c1\t"))

    def test_zero_images_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse(out.exists())

    def test_quiet_paths_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 1)
            proc = run_script(["--dir", str(src), "--out", str(out), "--quiet"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)
            self.assertFalse(proc.stdout.lstrip().startswith("{"))
            self.assertTrue(Path(lines[0]).is_file())
            with self.assertRaises(json.JSONDecodeError):
                json.loads(proc.stdout)

    def test_symlink_sheet_does_not_overwrite_victim(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            out.mkdir()
            fill_images(src, 1)
            victim = Path(tmp) / "victim.bin"
            blob = b"KEEP-ME-UNCHANGED"
            victim.write_bytes(blob)
            sheet = out / "sheet-001.png"
            sheet.symlink_to(victim)
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(victim.read_bytes(), blob)
            self.assertTrue(sheet.is_symlink())
            self.assertEqual(sheet.resolve(), victim.resolve())
            payload = parse_json(proc)
            self.assertTrue(payload.get("warnings"))

    def test_symlink_index_does_not_overwrite_victim(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            out.mkdir()
            fill_images(src, 1)
            victim = Path(tmp) / "victim-index.bin"
            blob = b"INDEX-VICTIM"
            victim.write_bytes(blob)
            (out / "index.txt").symlink_to(victim)
            proc = run_script(
                ["--dir", str(src), "--out", str(out), "--index-txt"]
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(victim.read_bytes(), blob)
            self.assertTrue((out / "index.txt").is_symlink())
            payload = parse_json(proc)
            self.assertTrue(payload.get("warnings"))
            self.assertEqual(len(payload["sheets"]), 1)

    def test_all_includes_page_render_for_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            write_png(src / "image-001.png", 8, 8, (1, 2, 3))
            write_png(src / "page-001.png", 8, 8, (4, 5, 6))
            without = run_script(["--dir", str(src), "--out", str(out / "a")])
            self.assertEqual(without.returncode, 0, without.stderr)
            self.assertEqual(parse_json(without)["count"], 1)
            with_all = run_script(
                ["--dir", str(src), "--out", str(out / "b"), "--all"]
            )
            self.assertEqual(with_all.returncode, 0, with_all.stderr)
            self.assertEqual(parse_json(with_all)["count"], 2)

    def test_all_includes_page_render_for_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            write_png(src / "image-001.png", 8, 8, (1, 2, 3))
            write_png(src / "page-001.png", 8, 8, (4, 5, 6))
            manifest = src / "images.json"
            manifest.write_text(
                json.dumps(
                    {
                        "images_dir": str(src),
                        "images": [
                            {
                                "file": "image-001.png",
                                "page": 2,
                                "kind": "embedded",
                            },
                            {
                                "file": "page-001.png",
                                "page": 1,
                                "kind": "page-render",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            proc = run_script(
                [
                    "--manifest",
                    str(manifest),
                    "--out",
                    str(out),
                    "--all",
                    "--index-txt",
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 2)
            names = (out / "index.txt").read_text(encoding="utf-8")
            self.assertIn("image-001.png", names)
            self.assertIn("page-001.png", names)

    def test_glob_includes_page_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            write_png(src / "image-001.png", 8, 8, (1, 2, 3))
            write_png(src / "page-001.png", 8, 8, (4, 5, 6))
            proc = run_script(
                ["--glob", str(src / "*.png"), "--out", str(out), "--index-txt"]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 2)
            names = (out / "index.txt").read_text(encoding="utf-8")
            self.assertIn("page-001.png", names)

    def test_reexec_flag_does_not_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 1)
            proc = run_script(
                ["--dir", str(src), "--out", str(out)],
                env={"CONTACT_SHEET_REEXEC": "1"},
            )
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("CONTACT_SHEET_REEXEC", proc.stderr)
            self.assertFalse(out.exists() or (out.is_dir() and any(out.iterdir())))

    def test_japanese_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            write_png(src / "図-概要.png", 8, 8, (9, 10, 11))
            proc = run_script(
                ["--glob", str(src / "*.png"), "--out", str(out), "--index-txt"]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = parse_json(proc)
            self.assertEqual(payload["count"], 1)
            index = (out / "index.txt").read_text(encoding="utf-8")
            self.assertIn("図-概要.png", index)

    def test_out_symlink_to_raw_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "vault"
            raw = fake / ".raw"
            raw.mkdir(parents=True)
            src = Path(tmp) / "src"
            src.mkdir()
            fill_images(src, 1)
            out_link = Path(tmp) / "out-as-raw"
            out_link.symlink_to(raw)
            proc = run_script(
                ["--dir", str(src), "--out", str(out_link)],
                env={"WIKI_VAULT_ROOT": str(fake)},
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn(".raw", proc.stderr)
            self.assertEqual(list(raw.iterdir()), [])

    def test_count_over_24_warns_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 25)
            proc = run_script(["--dir", str(src), "--out", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(
                "contact-sheet: 25 images -> 3 sheets; Read only what you need",
                proc.stderr,
            )
            self.assertEqual(parse_json(proc)["count"], 25)

    def test_limits_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "out"
            src.mkdir()
            fill_images(src, 1)
            for extra in (["--cell", "1025"], ["--cols", "9"], ["--per-sheet", "49"]):
                proc = run_script(
                    ["--dir", str(src), "--out", str(out)] + extra
                )
                self.assertEqual(proc.returncode, 2, extra)
                self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
