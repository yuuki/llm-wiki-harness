#!/usr/bin/env python3
"""paper-refs.py の試験。vault は触らず一時ディレクトリを WIKI_VAULT_ROOT にする。

    python3 -m unittest scripts/test_paper_refs.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

LEDGER_REL = ".vault-meta/paper-ref-candidates.json"


def _fm(stem, source_type="paper", sources=None, arxiv=None, doi=None, extra=""):
    srcs = sources if sources is not None else [f"[[.raw/papers/{stem}.txt]]"]
    lines = [
        "---",
        "type: source",
        f'title: "{stem}"',
        f"source_type: {source_type}",
        "sources:",
    ]
    for s in srcs:
        lines.append(f'  - "{s}"')
    if arxiv:
        lines.append(f'arxiv_id: "{arxiv}"')
    if doi:
        lines.append(f'doi: "{doi}"')
    if extra:
        lines.append(extra.rstrip())
    lines.append("---")
    lines.append("")
    lines.append(f"# {stem}")
    lines.append("")
    return "\n".join(lines)


class PaperRefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "wiki/sources").mkdir(parents=True)
        (self.vault / ".raw/papers").mkdir(parents=True)
        (self.vault / ".raw/books").mkdir(parents=True)
        (self.vault / ".vault-meta").mkdir()
        self.env = dict(os.environ, WIKI_VAULT_ROOT=str(self.vault))
        os.environ["WIKI_VAULT_ROOT"] = str(self.vault)
        spec = importlib.util.spec_from_file_location("paper_ids", SCRIPTS / "paper-ids.py")
        paper_ids = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(paper_ids)
        spec = importlib.util.spec_from_file_location("paper_refs", SCRIPTS / "paper-refs.py")
        paper_refs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(paper_refs)
        self.pr = paper_refs
        self.ids = paper_ids

    def tearDown(self):
        self.tmp.cleanup()

    def write_paper(self, stem, txt, source_type="paper", sources=None, arxiv=None, doi=None, extra=""):
        if sources is None and source_type == "paper":
            sources = [f"[[.raw/papers/{stem}.txt]]"]
        page = self.vault / "wiki/sources" / f"{stem}.md"
        page.write_text(_fm(stem, source_type, sources, arxiv, doi, extra), encoding="utf-8")
        if txt is not None and source_type == "paper":
            raw = self.vault / ".raw/papers" / f"{stem}.txt"
            if sources:
                for s in sources:
                    if ".raw/papers/" in s and s.endswith(".txt]]") or (s.endswith(".txt") and ".raw/papers/" in s):
                        rel = s.replace("[[", "").replace("]]", "").strip('"')
                        if rel.startswith(".raw/papers/"):
                            raw = self.vault / rel
                            break
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(txt, encoding="utf-8")
        return page

    def write_index(self, arxiv=None, doi=None):
        payload = {
            "version": 1,
            "arxiv": arxiv or {},
            "doi": doi or {},
            "title": {},
        }
        (self.vault / ".vault-meta/paper-ids.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def ledger(self):
        path = self.vault / LEDGER_REL
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def run_cli(self, *args, expect=0):
        import subprocess

        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "paper-refs.py"), *args],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=str(self.vault),
        )
        self.assertEqual(proc.returncode, expect, proc.stderr + proc.stdout)
        return proc

    def extract(self, stem, **kwargs):
        ns = type("NS", (), {"source": stem, "dry_run": False, "date": None})()
        for k, v in kwargs.items():
            setattr(ns, k, v)
        return self.pr.cmd_extract(ns)

    def report(self, limit=30, write=False):
        ns = type("NS", (), {"limit": limit, "write": write})()
        from io import StringIO
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = self.pr.cmd_report(ns)
        finally:
            sys.stdout = old
        return rc, buf.getvalue()

    # 1
    def test_01_last_heading_only_ignores_inline_references(self):
        stem = "@2026__arXiv__LastHeading"
        body = (
            "See the references in Table 1 for prior work arXiv:1111.11111.\n"
            "References\n"
            "[1] Real. arXiv:2222.22222\n"
        )
        self.write_paper(stem, body)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()
        self.assertIn("arxiv:2222.22222", data["candidates"])
        self.assertNotIn("arxiv:1111.11111", data["candidates"])

    # 2
    def test_02_heading_in_first_20_percent_still_harvests(self):
        stem = "@2026__arXiv__EarlyHeading"
        early = "References\n[1] Early. arXiv:3333.33333\nAppendix\n"
        filler = ("lorem ipsum dolor sit amet " * 80 + "\n") * 40
        self.write_paper(stem, early + filler)
        self.assertLess(len(early) / len(early + filler), 0.20)
        self.assertEqual(self.extract(stem), 0)
        self.assertIn("arxiv:3333.33333", self.ledger()["candidates"])

    # 3
    def test_03_heading_variants_are_headings(self):
        variants = {
            "@2026__V__Numbered": "10. References\n[1] A. arXiv:4444.44441\n",
            "@2026__V__Spaced": "R EFERENCES\n[1] B. arXiv:4444.44442\n",
            "@2026__V__FF": "\fReferences [19] C. arXiv:4444.44443\n",
            "@2026__V__CJK": "♦ 参 考 文 献 ♦\n[1] D. arXiv:4444.44444\n",
            "@2026__V__NBSP": "\xa0References\n[1] E. arXiv:4444.44445\n",
            "@2026__V__Ideo": "♦\u3000参考文献\u3000♦\n[1] F. arXiv:4444.44446\n",
            "@2026__V__TrailNBSP": "References\xa0\n[1] G. arXiv:4444.44447\n",
        }
        for stem, txt in variants.items():
            self.write_paper(stem, txt)
            self.assertEqual(self.extract(stem), 0, stem)
        keys = self.ledger()["candidates"].keys()
        self.assertIn("arxiv:4444.44441", keys)
        self.assertIn("arxiv:4444.44442", keys)
        self.assertIn("arxiv:4444.44443", keys)
        self.assertIn("arxiv:4444.44444", keys)
        self.assertIn("arxiv:4444.44445", keys)
        self.assertIn("arxiv:4444.44446", keys)
        self.assertIn("arxiv:4444.44447", keys)

    # 4 — 失敗必須: 目次だけを見出しにしたら落ちる
    def test_04_toc_references_is_not_heading(self):
        stem = "@2015__Book__TOC"
        self.write_paper(stem, "References ..... 83\n[1] Should not harvest. arXiv:5555.55555\n")
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()
        self.assertTrue(data is None or "arxiv:5555.55555" not in data.get("candidates", {}))

    # 5 — 失敗必須: 4 識別子が 1 件に潰れたら落ちる
    def test_05_two_column_does_not_merge_arxiv_and_doi(self):
        stem = "@2026__arXiv__TwoCol"
        txt = (
            "References [19] A. arXiv:6666.66661 doi.org/10.1000/aaa "
            "B. arXiv:6666.66662 https://doi.org/10.1000/bbb\n"
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        keys = set(self.ledger()["candidates"])
        self.assertEqual(len(keys), 4, keys)
        self.assertEqual(
            keys,
            {"arxiv:6666.66661", "arxiv:6666.66662", "doi:10.1000/aaa", "doi:10.1000/bbb"},
        )

    # 6
    def test_06_stamp_three_lines_dropped_preprint_and_comma_kept(self):
        stem = "@2026__arXiv__Stamp"
        stamp = "arXiv:7777.77771v1 [cs.DC] 1 Jan 2024\n"
        body = (
            stamp * 3
            + "arXiv:7777.77772v1 [cs.LG] 2 Jan 2024\n"
            + "arXiv:7777.77772 [cs.LG] 3 Jan 2024\n"
            + "References\n"
            "[1] Header. arXiv:7777.77771\n"
            "[2] Two stamps only. arXiv:7777.77772\n"
            "[3] arXiv preprint arXiv:7777.77773\n"
            "[3] arXiv preprint arXiv:7777.77773\n"
            "[3] arXiv preprint arXiv:7777.77773\n"
            "[4] Comma stamp arXiv:7777.77774, 2024.\n"
        )
        self.write_paper(stem, body)
        self.assertEqual(self.extract(stem), 0)
        keys = set(self.ledger()["candidates"])
        self.assertNotIn("arxiv:7777.77771", keys)
        self.assertIn("arxiv:7777.77772", keys)
        self.assertIn("arxiv:7777.77773", keys)
        self.assertIn("arxiv:7777.77774", keys)

    # 7
    def test_07_self_ids_from_derive_and_slug_dropped(self):
        stem = "@2026__arXiv__Self"
        self.write_paper(
            stem,
            "References\n[1] Self arXiv:8888.88881 also doi 10.1000/self and other arXiv:8888.88882\n",
            sources=["[[.raw/papers/arxiv-8888.88881.txt]]"],
            arxiv="8888.88881",
            doi="10.1000/self",
        )
        (self.vault / ".raw/papers/arxiv-8888.88881.txt").write_text(
            "References\n[1] Self arXiv:8888.88881 also doi 10.1000/self and other arXiv:8888.88882\n",
            encoding="utf-8",
        )
        self.assertEqual(self.extract(stem), 0)
        keys = set(self.ledger()["candidates"])
        self.assertNotIn("arxiv:8888.88881", keys)
        self.assertNotIn("doi:10.1000/self", keys)
        self.assertIn("arxiv:8888.88882", keys)

    # 8
    def test_08_zenodo_doi_dropped(self):
        stem = "@2026__arXiv__Zenodo"
        self.write_paper(
            stem,
            "References\n[1] Data https://doi.org/10.5281/zenodo.123456 and arXiv:9999.99991\n",
        )
        self.assertEqual(self.extract(stem), 0)
        keys = set(self.ledger()["candidates"])
        self.assertTrue(all(not k.startswith("doi:10.5281/zenodo") for k in keys))
        self.assertIn("arxiv:9999.99991", keys)

    # 9
    def test_09_reextract_is_idempotent_except_last_seen(self):
        stem = "@2026__arXiv__Idem"
        self.write_paper(stem, "References\n[1] X. arXiv:1010.10101\n")
        self.assertEqual(self.extract(stem, date="2026-09-01"), 0)
        first = self.ledger()["candidates"]["arxiv:1010.10101"]
        self.assertEqual(len(first["mentions"]), 1)
        self.assertEqual(first["last_seen"], "2026-09-01")
        self.assertEqual(self.extract(stem, date="2026-09-15"), 0)
        again = self.ledger()["candidates"]["arxiv:1010.10101"]
        self.assertEqual(len(again["mentions"]), 1)
        self.assertEqual(len(again["citing"]), 1)
        self.assertEqual(again["last_seen"], "2026-09-15")
        self.assertEqual(self.pr.demand_of(again), 1)

    # 10
    def test_10_ready_needs_two_docs_and_readable_index(self):
        a = "@2026__arXiv__CiteA"
        b = "@2026__arXiv__CiteB"
        txt = "References\n[1] Shared. arXiv:1212.12121\n"
        self.write_paper(a, txt)
        self.write_paper(b, txt)
        self.assertEqual(self.extract(a), 0)
        rc, payload = self.pr.run_extract(b)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["ready"], 0)
        rc, body = self.report()
        self.assertEqual(rc, 0)
        self.assertIn("paper-ids-index: missing", body)
        self.assertNotIn("arxiv:1212.12121", body)
        self.write_index()
        rc, payload = self.pr.run_extract(b)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["ready"], 1)
        rc, body = self.report()
        self.assertEqual(rc, 0)
        self.assertIn("arxiv:1212.12121", body)
        self.assertNotIn("paper-ids-index: missing", body)
        solo = "@2026__arXiv__Solo"
        self.write_paper(solo, "References\n[1] Only one. arXiv:1313.13131\n")
        self.assertEqual(self.extract(solo), 0)
        listed = json.loads(self._stdout(self.pr.cmd_list, ready=True, state=None, limit=None))
        keys = [r["key"] for r in listed["candidates"]]
        self.assertIn("arxiv:1212.12121", keys)
        self.assertNotIn("arxiv:1313.13131", keys)

    def _stdout(self, fn, **kwargs):
        from io import StringIO

        ns = type("NS", (), kwargs)()
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = fn(ns)
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0, buf.getvalue())
        return buf.getvalue()

    # 11
    def test_11_hit_pending_is_not_ready_no_forgotten_section(self):
        a = "@2026__arXiv__HitA"
        b = "@2026__arXiv__HitB"
        txt = "References\n[1] Known. arXiv:1414.14141\n"
        self.write_paper(a, txt)
        self.write_paper(b, txt)
        self.write_index(arxiv={"1414.14141": ["wiki/sources/@2020__arXiv__Already.md"]})
        # 書き込み経路が HIT なら ingested になる。pending のまま HIT を試すため台帳を直書きする
        self.assertEqual(self.extract(a), 0)
        data = self.ledger()
        data["candidates"]["arxiv:1414.14141"]["state"] = "pending"
        data["candidates"]["arxiv:1414.14141"]["citing"] = [a, b]
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        rc, body = self.report()
        self.assertEqual(rc, 0)
        self.assertNotIn("arxiv:1414.14141", body)
        self.assertNotIn("し忘れ", body)

    # 12
    def test_12_write_path_hit_ingested_reject_stays(self):
        a = "@2026__arXiv__W1"
        b = "@2026__arXiv__W2"
        txt = "References\n[1] Hit. arXiv:1515.15151\n"
        self.write_paper(a, txt)
        self.write_paper(b, txt)
        self.write_index(arxiv={"1515.15151": ["wiki/sources/@2019__arXiv__Old.md"]})
        self.assertEqual(self.extract(a), 0)
        self.assertEqual(self.ledger()["candidates"]["arxiv:1515.15151"]["state"], "ingested")
        ns = type("NS", (), {"key": "arxiv:1515.15151", "reason": "基盤すぎる"})()
        self.assertEqual(self.pr.cmd_reject(ns), 0)
        self.assertEqual(self.extract(b), 0)
        entry = self.ledger()["candidates"]["arxiv:1515.15151"]
        self.assertEqual(entry["state"], "rejected")
        self.assertEqual(len(entry["mentions"]), 2)

    # 13
    def test_13_mark_ingested_closes_current_keys_only(self):
        stem = "@2026__arXiv__Mark"
        self.write_paper(stem, "References\n[1] Other. arXiv:1616.16161\n", arxiv="1616.16162", doi="10.1000/mark")
        # 台帳に doi と別 arxiv を先に置く
        self.write_index()
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()
        data["candidates"]["arxiv:1616.16162"] = self.pr.new_entry("arxiv", "1616.16162", "@other", "2026-01-01")
        data["candidates"]["doi:10.1000/mark"] = self.pr.new_entry("doi", "10.1000/mark", "@other", "2026-01-01")
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        ns = type("NS", (), {"page": f"wiki/sources/{stem}.md"})()
        self.assertEqual(self.pr.cmd_mark_ingested(ns), 0)
        data = self.ledger()
        self.assertEqual(data["candidates"]["arxiv:1616.16162"]["state"], "ingested")
        self.assertEqual(data["candidates"]["doi:10.1000/mark"]["state"], "ingested")
        self.assertEqual(data["candidates"]["arxiv:1616.16161"]["state"], "pending")
        missing = type("NS", (), {"page": "wiki/sources/@no-such.md"})()
        self.assertEqual(self.pr.cmd_mark_ingested(missing), 3)
        # 一致キーも台帳も無いページは空台帳を作らない
        lonely = "@2026__arXiv__LonelyMark"
        self.write_paper(lonely, "References\nno ids\n", arxiv="1919.00001")
        (self.vault / LEDGER_REL).unlink(missing_ok=True)
        self.assertEqual(self.pr.cmd_mark_ingested(type("NS", (), {"page": f"wiki/sources/{lonely}.md"})()), 0)
        self.assertFalse((self.vault / LEDGER_REL).is_file())

    # 14
    def test_14_reopen_rejected_only(self):
        stem = "@2026__arXiv__Reopen"
        self.write_paper(stem, "References\n[1] X. arXiv:1717.17171\n")
        self.write_index()
        self.assertEqual(self.extract(stem), 0)
        key = "arxiv:1717.17171"
        self.assertEqual(self.pr.cmd_reject(type("NS", (), {"key": key, "reason": "no"})()), 0)
        self.assertEqual(self.pr.cmd_reopen(type("NS", (), {"key": key})()), 0)
        self.assertEqual(self.ledger()["candidates"][key]["state"], "pending")
        self.assertEqual(self.pr.cmd_reopen(type("NS", (), {"key": key})()), 2)
        # ingested は reopen できない
        self.assertEqual(self.pr.cmd_reject(type("NS", (), {"key": key, "reason": "again"})()), 0)
        data = self.ledger()
        data["candidates"][key]["state"] = "ingested"
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        self.assertEqual(self.pr.cmd_reopen(type("NS", (), {"key": key})()), 2)

    # 15
    def test_15_report_shows_ledger_title_omits_after_30(self):
        self.write_index()
        data = self.pr.empty_ledger()
        for i in range(31):
            key = f"arxiv:1800.{i:05d}"
            ident = f"1800.{i:05d}"
            entry = self.pr.new_entry("arxiv", ident, "@A", "2026-01-01")
            entry["citing"] = ["@A", "@B"]
            entry["title"] = "Stored Ledger Title Fragment"
            data["candidates"][key] = entry
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        rc, body = self.report(limit=30)
        self.assertEqual(rc, 0)
        self.assertIn("Stored Ledger Title Fragment", body)
        self.assertIn("omitted: 1", body)
        self.assertIn("## Paper Ref Candidates", body)
        self.assertIn("需要", body)
        for word in self.pr.EVAL_FORBIDDEN:
            self.assertNotIn(word, body)

    # 16
    def test_16_extract_does_not_rewrite_page_raw_or_index(self):
        stem = "@2026__arXiv__NoWrite"
        page = self.write_paper(stem, "References\n[1] X. arXiv:1919.19191\n")
        raw = self.vault / f".raw/papers/{stem}.txt"
        idx = self.vault / ".vault-meta/paper-ids.json"
        self.write_index()
        before = {p: p.read_bytes() for p in (page, raw, idx)}
        mtimes = {p: p.stat().st_mtime_ns for p in (page, raw, idx)}
        time.sleep(0.02)
        self.assertEqual(self.extract(stem), 0)
        for p in (page, raw, idx):
            self.assertEqual(p.read_bytes(), before[p])
            self.assertEqual(p.stat().st_mtime_ns, mtimes[p])
        self.assertFalse((self.vault / "wiki/meta/paper-ref-candidates.md").is_file())

    # 17
    def test_17_no_network(self):
        stem = "@2026__arXiv__NoNet"
        self.write_paper(stem, "References\n[1] X. arXiv:2020.20202\n")
        self.write_index()

        def boom(*a, **k):
            raise AssertionError("socket opened")

        with mock.patch("socket.socket", side_effect=boom), mock.patch(
            "socket.create_connection", side_effect=boom
        ):
            self.assertEqual(self.extract(stem), 0)
            self.report()
            self.report(write=True)
            self.pr.cmd_list(type("NS", (), {"ready": False, "state": None, "limit": None})())

    # 18
    def test_18_acquire_once_per_extract_backfill_skips_lock(self):
        a = "@2026__arXiv__L1"
        b = "@2026__arXiv__L2"
        self.write_paper(a, "References\n[1] A. arXiv:2121.21211\n")
        self.write_paper(b, "References\n[1] B. arXiv:2121.21212\n")
        self.write_index()
        calls = []

        def fake_acquire():
            calls.append("acquire")
            return 0

        releases = []

        def fake_release():
            releases.append("release")

        self.pr.acquire_ledger = fake_acquire
        self.pr.release_ledger = fake_release
        self.assertEqual(self.extract(a), 0)
        self.assertEqual(calls.count("acquire"), 1)

        calls.clear()
        rc = self.pr.cmd_backfill(type("NS", (), {"limit": None, "dry_run": False})())
        self.assertEqual(rc, 0)
        self.assertEqual(calls.count("acquire"), 2, calls)

        calls.clear()

        def held():
            calls.append("acquire")
            return 75

        self.pr.acquire_ledger = held
        t0 = time.monotonic()
        rc, payload = self.pr.run_extract(a)
        elapsed = time.monotonic() - t0
        self.assertEqual(rc, 75)
        self.assertIsNone(payload)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(calls.count("acquire"), 1)

        # backfill 中の 75 は skipped_lock で止まらない
        seq = iter([75, 0])

        def mixed():
            calls.append("acquire")
            return next(seq)

        calls.clear()
        self.pr.acquire_ledger = mixed
        from io import StringIO

        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = self.pr.cmd_backfill(type("NS", (), {"limit": None, "dry_run": False})())
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        stats = json.loads(buf.getvalue())
        self.assertEqual(stats["skipped_lock"], 1)
        self.assertEqual(stats["extracted"], 1)

        def fail_page_lock(*a, **k):
            raise AssertionError("page_lock called")

        import wiki_lock

        with mock.patch.object(wiki_lock, "page_lock", fail_page_lock):
            self.pr.acquire_ledger = fake_acquire
            self.assertEqual(self.extract(b), 0)

    # 19
    def test_19_corrupt_ledger_exits_1_and_does_not_empty(self):
        stem = "@2026__arXiv__Corrupt"
        self.write_paper(stem, "References\n[1] X. arXiv:2323.23231\n")
        path = self.vault / LEDGER_REL
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.extract(stem), 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")
        rc, _ = self.report()
        self.assertEqual(rc, 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    # 20
    def test_20_article_and_books_exit_3_backfill_counts_skips(self):
        art = "@2026__Blog__Article"
        self.write_paper(art, None, source_type="article", sources=["[[.raw/articles/x.md]]"])
        book = "@2024__OReilly__Book - Chapter 1"
        (self.vault / "wiki/sources" / f"{book}.md").write_text(
            _fm(book, "book", ["[[.raw/books/foo/chapters/ch-01.txt]]"]),
            encoding="utf-8",
        )
        (self.vault / ".raw/books/foo/chapters").mkdir(parents=True)
        (self.vault / ".raw/books/foo/chapters/ch-01.txt").write_text("References\narXiv:0000.00000\n", encoding="utf-8")
        self.assertEqual(self.extract(art), 3)
        self.assertEqual(self.extract(book), 3)
        paper = "@2026__arXiv__Ok"
        self.write_paper(paper, "References\n[1] X. arXiv:2424.24241\n")
        from io import StringIO

        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = self.pr.cmd_backfill(type("NS", (), {"limit": None, "dry_run": False})())
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        stats = json.loads(buf.getvalue())
        self.assertGreaterEqual(stats["skipped_not_paper"] + stats["skipped_no_txt"], 1)
        self.assertGreaterEqual(stats["extracted"], 1)

    # 21
    def test_21_old_arxiv_not_harvested(self):
        stem = "@2026__arXiv__OldId"
        self.write_paper(stem, "References\n[1] Old https://arxiv.org/abs/cs/0003075 and arXiv:cs/0003075\n")
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()
        self.assertTrue(data is None or data.get("candidates") == {})

    # 22
    def test_22_load_or_build_index_and_page_lock_are_fatal(self):
        stem = "@2026__arXiv__NoHelpers"
        self.write_paper(stem, "References\n[1] X. arXiv:2525.25251\n")
        self.write_index()

        def boom_index(*a, **k):
            raise AssertionError("load_or_build_index called")

        def boom_lock(*a, **k):
            raise AssertionError("page_lock called")

        with mock.patch.object(self.ids, "load_or_build_index", boom_index):
            import wiki_lock

            with mock.patch.object(wiki_lock, "page_lock", boom_lock):
                self.assertEqual(self.extract(stem), 0)
                self.report()

        src = (SCRIPTS / "paper-refs.py").read_text(encoding="utf-8")
        self.assertNotIn("load_or_build_index(", src)
        self.assertNotIn("from wiki_lock", src)
        self.assertNotIn("page_lock(", src)

    # 23
    def test_23_acknowledgements_ends_region(self):
        stem = "@2026__arXiv__Ack"
        self.write_paper(
            stem,
            "References\n[1] In. arXiv:2626.26261\nAcknowledgements\nThanks. arXiv:2626.26262\n",
        )
        self.assertEqual(self.extract(stem), 0)
        keys = set(self.ledger()["candidates"])
        self.assertIn("arxiv:2626.26261", keys)
        self.assertNotIn("arxiv:2626.26262", keys)

    # 24
    def test_24_references_and_notes_is_not_heading(self):
        stem = "@2026__arXiv__Notes"
        self.write_paper(
            stem,
            "References and Notes\n[1] Should not. arXiv:2727.27271\n",
        )
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()
        self.assertTrue(data is None or "arxiv:2727.27271" not in data.get("candidates", {}))

    # 25
    def test_25_single_id_item_gets_snippet_short_is_null(self):
        stem = "@2026__arXiv__TitleOne"
        self.write_paper(
            stem,
            "References\n"
            "[19] Foo Bar title here. arXiv:2800.28001\n"
            "[20] Ab. arXiv:2800.28002\n",
        )
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        title = data["arxiv:2800.28001"]["title"]
        self.assertIsNotNone(title)
        self.assertNotIn("2800.28001", title)
        self.assertNotIn("[19]", title)
        self.assertNotIn("arxiv.org", title.lower())
        self.assertIn("Foo Bar title here", title)
        self.assertIsNone(data["arxiv:2800.28002"]["title"])

    def test_25b_later_single_item_fills_empty_title(self):
        stem = "@2026__arXiv__TitleFill"
        self.write_paper(
            stem,
            "References [19] Ab. arXiv:3400.34001 doi.org/10.1000/eee\n"
            "[20] Foo Bar title here xx. arXiv:3400.34001\n",
        )
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        self.assertIn("Foo Bar title here", data["arxiv:3400.34001"]["title"] or "")
        self.assertIsNone(data["doi:10.1000/eee"]["title"])

    def test_25c_snippet_drops_bare_id_and_https_leftover(self):
        stem = "@2026__arXiv__TitleStrip"
        self.write_paper(
            stem,
            "References\n"
            "[19] Foo Bar title here. https://doi.org/10.1000/fff\n"
            "[20] Foo Bar title here. 3500.35001\n",
        )
        self.assertEqual(self.extract(stem), 0)
        doi_title = self.ledger()["candidates"]["doi:10.1000/fff"]["title"]
        ax_title = self.ledger()["candidates"]["arxiv:3500.35001"]["title"]
        self.assertIsNotNone(doi_title)
        self.assertNotIn("https", doi_title.lower())
        self.assertIsNotNone(ax_title)
        self.assertNotIn("3500.35001", ax_title)

    # 26
    def test_26_multi_id_item_gets_no_title(self):
        stem = "@2026__arXiv__TitleMany"
        self.write_paper(
            stem,
            "References [19] A. arXiv:2900.29001 doi.org/10.1000/ccc "
            "B. arXiv:2900.29002\n",
        )
        self.assertEqual(self.extract(stem), 0)
        for key in self.ledger()["candidates"]:
            self.assertIsNone(self.ledger()["candidates"][key]["title"], key)

    # 27
    def test_27_existing_title_not_overwritten(self):
        stem = "@2026__arXiv__TitleKeep"
        self.write_paper(stem, "References\n[1] First stored title fragment. arXiv:3000.30001\n")
        self.assertEqual(self.extract(stem), 0)
        first = self.ledger()["candidates"]["arxiv:3000.30001"]["title"]
        raw = self.vault / f".raw/papers/{stem}.txt"
        raw.write_text("References\n[1] Second different leftover text xx. arXiv:3000.30001\n", encoding="utf-8")
        self.assertEqual(self.extract(stem), 0)
        self.assertEqual(self.ledger()["candidates"]["arxiv:3000.30001"]["title"], first)
        self.assertNotEqual(first, "Second different leftover text xx")

    # 28
    def test_28_report_does_not_invent_title_or_eval(self):
        self.write_index()
        data = self.pr.empty_ledger()
        entry = self.pr.new_entry("arxiv", "3100.31001", "@A", "2026-01-01")
        entry["citing"] = ["@A", "@B"]
        entry["title"] = None
        data["candidates"]["arxiv:3100.31001"] = entry
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        rc, body = self.report()
        self.assertEqual(rc, 0)
        self.assertIn("arxiv:3100.31001", body)
        self.assertIn("需要 2", body)
        self.assertNotIn("Unknown", body)
        self.assertNotIn("Untitled", body)
        for word in self.pr.EVAL_FORBIDDEN:
            self.assertNotIn(word, body)

    # 29
    def test_29_report_write_only_with_flag(self):
        self.write_index()
        data = self.pr.empty_ledger()
        entry = self.pr.new_entry("arxiv", "3200.32001", "@A", "2026-01-01")
        entry["citing"] = ["@A", "@B"]
        data["candidates"]["arxiv:3200.32001"] = entry
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        page = self.vault / "wiki/meta/paper-ref-candidates.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        rc, body = self.report(write=False)
        self.assertEqual(rc, 0)
        self.assertFalse(page.is_file())
        page.write_text("---\ncreated: 2020-01-01\n---\nold\n", encoding="utf-8")
        rc, body = self.report(write=True)
        self.assertEqual(rc, 0)
        text = page.read_text(encoding="utf-8")
        self.assertIn("## Paper Ref Candidates", text)
        self.assertIn(body.strip().splitlines()[0], text)
        self.assertIn("created: 2020-01-01", text)
        self.assertIn("手で本文を育てない", text)

    # 30
    def test_30_write_acquire_once_75_leaves_file(self):
        self.write_index()
        data = self.pr.empty_ledger()
        entry = self.pr.new_entry("arxiv", "3300.33001", "@A", "2026-01-01")
        entry["citing"] = ["@A", "@B"]
        data["candidates"]["arxiv:3300.33001"] = entry
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        page = self.vault / "wiki/meta/paper-ref-candidates.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        calls = []

        def held():
            calls.append("acquire")
            return 75

        page.write_text("KEEP\n", encoding="utf-8")
        before = page.read_text(encoding="utf-8")
        self.pr.acquire_page = held
        rc, body = self.report(write=True)
        self.assertEqual(rc, 75)
        self.assertEqual(calls.count("acquire"), 1)
        self.assertEqual(page.read_text(encoding="utf-8"), before)
        self.assertIn("## Paper Ref Candidates", body)

        def fail_lock(*a, **k):
            raise AssertionError("page_lock called")

        import wiki_lock

        with mock.patch.object(wiki_lock, "page_lock", fail_lock):
            self.pr.acquire_page = lambda: 0
            self.pr.release_page = lambda: None
            rc, _ = self.report(write=True)
            self.assertEqual(rc, 0)
            self.assertTrue(page.is_file())

    def _role_txt(self, body, refs):
        return f"{body.rstrip()}\nReferences\n{refs}"

    # 31
    def test_31_body_cite_maps_to_bib_number(self):
        stem = "@2026__arXiv__RoleMap"
        txt = self._role_txt(
            "2 Related Work\nSee the method in [1].\n",
            "[1] Foo Bar title here. arXiv:4100.41001\n[1] ignored duplicate. arXiv:4100.41099\n",
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        self.assertEqual(data["arxiv:4100.41001"]["roles"][stem]["related_work"], 1)
        self.assertEqual(data["arxiv:4100.41099"].get("roles") or {}, {})

    # 32
    def test_32_no_heading_and_author_year_give_no_roles(self):
        stem = "@2026__arXiv__NoRole"
        txt = "Vaswani et al. (2017) showed this.\nReferences\n[1] Foo Bar title here. arXiv:4200.42001\n"
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        entry = self.ledger()["candidates"]["arxiv:4200.42001"]
        self.assertEqual(entry.get("roles") or {}, {})
        stem2 = "@2026__arXiv__NoRefsHeading"
        self.write_paper(stem2, "No bibliography heading. arXiv:4200.42999\nSee [1].\n")
        self.assertEqual(self.extract(stem2), 0)
        self.assertNotIn("arxiv:4200.42999", self.ledger()["candidates"])
        self.assertEqual(self.ledger()["candidates"]["arxiv:4200.42001"].get("roles") or {}, {})

    # 33
    def test_33_reextract_replaces_role_counts(self):
        stem = "@2026__arXiv__RoleIdem"
        txt = self._role_txt("2 Related Work\n[1] and [1].\n", "[1] Foo Bar title here. arXiv:4300.43001\n")
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        self.assertEqual(self.ledger()["candidates"]["arxiv:4300.43001"]["roles"][stem]["related_work"], 2)
        self.assertEqual(self.extract(stem), 0)
        self.assertEqual(self.ledger()["candidates"]["arxiv:4300.43001"]["roles"][stem]["related_work"], 2)

    # 34
    def test_34_roles_do_not_change_ready(self):
        stem = "@2026__arXiv__RoleReady"
        self.write_index()
        cites = "see [1]\n" * 5
        txt = self._role_txt("2 Related Work\n" + cites, "[1] Foo Bar title here. arXiv:4400.44001\n")
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        entry = self.ledger()["candidates"]["arxiv:4400.44001"]
        self.assertGreaterEqual(entry["roles"][stem]["related_work"], 5)
        self.assertFalse(self.pr.is_ready(entry, self.pr.read_paper_ids_index(), "arxiv:4400.44001"))
        path = self.vault / ".vault-meta/paper-ids.json"
        path.unlink()
        rc, body = self.report()
        self.assertEqual(rc, 0)
        self.assertIn("paper-ids-index: missing", body)
        self.assertNotIn("4400.44001", body)

    # 35
    def test_35_two_column_heading_and_toc(self):
        stem = "@2026__arXiv__RoleTwoCol"
        txt = (
            "Related Work ..... 5\n"
            "see [1] before real heading\n"
            "2    Related Work          other column continues here\n"
            "see [1] after heading\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4500.45001\n"
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        roles = self.ledger()["candidates"]["arxiv:4500.45001"]["roles"][stem]
        self.assertEqual(roles.get("related_work"), 1)
        self.assertEqual(roles.get("passing"), 1)

        stem_ja = "@2026__arXiv__RoleJaCol"
        txt_ja = (
            "関連研究  右カラムには識別子ではない文章が続く\n"
            "see [1]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4500.45002\n"
        )
        self.write_paper(stem_ja, txt_ja)
        self.assertEqual(self.extract(stem_ja), 0)
        self.assertEqual(
            self.ledger()["candidates"]["arxiv:4500.45002"]["roles"][stem_ja].get("related_work"),
            1,
        )

        stem_sp = "@2026__arXiv__RoleJaSpaced"
        txt_sp = (
            "関 連 研 究  右カラムの続き\n"
            "see [1]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4500.45003\n"
        )
        self.write_paper(stem_sp, txt_sp)
        self.assertEqual(self.extract(stem_sp), 0)
        self.assertEqual(
            self.ledger()["candidates"]["arxiv:4500.45003"]["roles"][stem_sp].get("related_work"),
            1,
        )

    # 36
    def test_36_running_header_unnumbered_ignored(self):
        stem = "@2026__arXiv__RoleHeader"
        txt = (
            "Related Work\nRelated Work\nRelated Work\n"
            "see [1] still passing\n"
            "2 Related Work\n"
            "see [1] now related\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4600.46001\n"
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        roles = self.ledger()["candidates"]["arxiv:4600.46001"]["roles"][stem]
        self.assertEqual(roles.get("passing"), 1)
        self.assertEqual(roles.get("related_work"), 1)

        stem2 = "@2026__arXiv__RoleNumHeader"
        txt2 = (
            "2 Related Work\nsee [1] first\n"
            "2 Related Work\n"
            "5 Experiments\nsee [1] mid\n"
            "2 Related Work\nsee [1] after rerun\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4600.46002\n"
        )
        self.write_paper(stem2, txt2)
        self.assertEqual(self.extract(stem2), 0)
        roles2 = self.ledger()["candidates"]["arxiv:4600.46002"]["roles"][stem2]
        self.assertEqual(roles2.get("related_work"), 1)
        self.assertEqual(roles2.get("experiment"), 2)

        stem3 = "@2026__arXiv__RoleDotNum"
        txt3 = (
            "2. Related Work\nsee [1] a\n"
            "2 Related Work\nsee [1] still related\n"
            "3 Methods\nsee [1] method\n"
            "2 Related Work\nsee [1] stays method\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4600.46003\n"
        )
        self.write_paper(stem3, txt3)
        self.assertEqual(self.extract(stem3), 0)
        roles3 = self.ledger()["candidates"]["arxiv:4600.46003"]["roles"][stem3]
        self.assertEqual(roles3.get("related_work"), 2)
        self.assertEqual(roles3.get("method"), 2)

    # 37
    def test_37_range_expand_cap(self):
        stem = "@2026__arXiv__RoleRange"
        refs = "".join(f"[{i}] Foo Bar title {i:02d} xx. arXiv:4700.47{i:03d}\n" for i in (1, 2, 3, 30))
        txt = self._role_txt("2 Related Work\nsee [1-3] and also [1-30].\n", refs)
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        self.assertEqual(data["arxiv:4700.47001"]["roles"][stem]["related_work"], 1)
        self.assertEqual(data["arxiv:4700.47002"]["roles"][stem]["related_work"], 1)
        self.assertEqual(data["arxiv:4700.47003"]["roles"][stem]["related_work"], 1)
        self.assertEqual(data["arxiv:4700.47030"].get("roles") or {}, {})
        self.assertEqual(self.pr.parse_cite_nums("0-3"), [1, 2, 3])

        stem_ap = "@2026__arXiv__RoleAppendix"
        txt_ap = (
            "2 Related Work\nsee [2]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4700.47101\n"
            "Appendix\n"
            "[2] Other paper title xx. arXiv:4700.47102\n"
        )
        self.write_paper(stem_ap, txt_ap)
        self.assertEqual(self.extract(stem_ap), 0)
        data_ap = self.ledger()["candidates"]
        self.assertNotIn("arxiv:4700.47102", data_ap)
        self.assertEqual(data_ap["arxiv:4700.47101"].get("roles") or {}, {})
        self.assertNotIn(2, self.pr.bib_number_map(self.pr.find_ref_region(txt_ap)))

        stem_case = "@2026__arXiv__RoleLower"
        txt_case = (
            "a method\nsee [1]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4700.47201\n"
        )
        self.write_paper(stem_case, txt_case)
        self.assertEqual(self.extract(stem_case), 0)
        roles_case = self.ledger()["candidates"]["arxiv:4700.47201"]["roles"].get(stem_case) or {}
        self.assertNotIn("method", roles_case)

        stem_dot = "@2026__arXiv__RoleTrailingDot"
        txt_dot = (
            "2. Related Work.\nsee [1]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:4700.47301\n"
        )
        self.write_paper(stem_dot, txt_dot)
        self.assertEqual(self.extract(stem_dot), 0)
        self.assertEqual(
            self.ledger()["candidates"]["arxiv:4700.47301"]["roles"][stem_dot].get("related_work"),
            1,
        )

        stem_dbl = "@2026__arXiv__RoleDoubleRefs"
        txt_dbl = (
            "2 Related Work\nsee [1]\n"
            "References\n"
            "[1] Main bib item xx. arXiv:1111.11111\n"
            "Appendix\n"
            "References\n"
            "[1] Appendix bib xx. arXiv:2222.22222\n"
        )
        self.write_paper(stem_dbl, txt_dbl)
        self.assertEqual(self.extract(stem_dbl), 0)
        data_dbl = self.ledger()["candidates"]
        self.assertNotIn("arxiv:1111.11111", data_dbl)
        self.assertEqual(data_dbl["arxiv:2222.22222"].get("roles") or {}, {})

    # 38
    def test_38_multi_id_item_shares_role(self):
        stem = "@2026__arXiv__RoleMulti"
        txt = self._role_txt(
            "3 Methods\nwe follow [1].\n",
            "[1] Foo Bar title here. arXiv:4800.48001 doi:10.1000/role-multi\n",
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        self.assertEqual(data["arxiv:4800.48001"]["roles"][stem]["method"], 1)
        self.assertEqual(data["doi:10.1000/role-multi"]["roles"][stem]["method"], 1)
        self.assertEqual(len([k for k in data if k.startswith("arxiv:") or k.startswith("doi:")]), 2)

    # 39
    def test_39_report_uses_ledger_roles_only(self):
        self.write_index()
        data = self.pr.empty_ledger()
        a = self.pr.new_entry("arxiv", "4900.49001", "@A", "2026-01-01")
        a["citing"] = ["@A", "@B"]
        a["roles"] = {"@A": {"related_work": 2, "contrast": 9, "bogus": 4}}
        data["candidates"]["arxiv:4900.49001"] = a
        b = self.pr.new_entry("arxiv", "4900.49002", "@A", "2026-01-01")
        b["citing"] = ["@A", "@B"]
        b["roles"] = {}
        data["candidates"]["arxiv:4900.49002"] = b
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        rc, body = self.report()
        self.assertEqual(rc, 0)
        self.assertIn("関連研究 2、対比 9", body)
        self.assertNotIn("contrast", body)
        self.assertNotIn("bogus", body)
        line_b = [ln for ln in body.splitlines() if "4900.49002" in ln][0]
        self.assertNotIn("関連研究", line_b)
        self.assertNotIn("その他", line_b)
        for word in self.pr.EVAL_FORBIDDEN:
            self.assertNotIn(word, body)

    # 40
    def test_40_roles_keep_lock_and_no_network(self):
        stem = "@2026__arXiv__RoleLock"
        txt = self._role_txt("2 Related Work\n[1]\n", "[1] Foo Bar title here. arXiv:5000.50001\n")
        self.write_paper(stem, txt)
        calls = []
        real = self.pr.acquire_named

        def wrap(rel):
            calls.append(rel)
            return real(rel)

        self.pr.acquire_named = wrap
        self.pr.acquire_ledger = lambda: wrap(self.pr.LEDGER_REL)
        opened = []

        class BoomSocket:
            def __init__(self, *a, **k):
                opened.append(a)
                raise AssertionError("socket opened")

        with mock.patch.object(socket, "socket", BoomSocket):
            self.assertEqual(self.extract(stem), 0)
        self.assertEqual(calls.count(self.pr.LEDGER_REL), 1)

        def fail_lock(*a, **k):
            raise AssertionError("page_lock called")

        import wiki_lock

        with mock.patch.object(wiki_lock, "page_lock", fail_lock):
            self.assertEqual(self.extract(stem), 0)

    # 41
    def test_41_unlike_adds_contrast_and_section(self):
        stem = "@2026__arXiv__ContrastUnlike"
        txt = self._role_txt(
            "2 Related Work\nUnlike and in contrast [1, 2]\n",
            "[1] Unlike Foo Bar title here. arXiv:6100.61001\n"
            "[2] Baz Qux title here. arXiv:6100.61002\n",
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        r1 = data["arxiv:6100.61001"]["roles"][stem]
        r2 = data["arxiv:6100.61002"]["roles"][stem]
        self.assertEqual(r1.get("related_work"), 1)
        self.assertEqual(r1.get("contrast"), 1)
        self.assertEqual(r2.get("related_work"), 1)
        self.assertEqual(r2.get("contrast"), 1)

    # 42
    def test_42_plain_see_has_no_contrast(self):
        stem = "@2026__arXiv__ContrastSee"
        txt = self._role_txt("See [1]\n", "[1] Foo Bar title here. arXiv:6200.62001\n")
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        roles = self.ledger()["candidates"]["arxiv:6200.62001"]["roles"][stem]
        self.assertEqual(roles.get("passing"), 1)
        self.assertNotIn("contrast", roles)

    # 43
    def test_43_closed_list_rejects_lookalikes(self):
        denies = (
            "however",
            "but",
            "unlikely",
            "compared to",
            "compared with",
            "compared against",
            "instead of",
            "outperform",
            "better than",
            "に対する",
            "比べて",
            "比較して",
            "funlike" + ("." * 74),
            "within contrast" + ("." * 69),
            "2unlike",
            "_unlike",
        )
        for i, phrase in enumerate(denies, start=1):
            ev = self._role_txt(
                f"{phrase} [1]\n",
                "[1] Foo Bar title here. arXiv:6300.63001\n",
            )
            roles = self.pr.harvest_roles(ev).get(("arxiv", "6300.63001"), {})
            self.assertNotIn("contrast", roles, phrase)

        stem = "@2026__arXiv__ContrastLookalike"
        txt = self._role_txt(
            "however [1]\ncompared to [2]\nunlikely [3]\nに対する [4]\n",
            "[1] Foo Bar title here. arXiv:6300.63001\n"
            "[2] Foo Bar title here. arXiv:6300.63002\n"
            "[3] Foo Bar title here. arXiv:6300.63003\n"
            "[4] Foo Bar title here. arXiv:6300.63004\n",
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        for key in (
            "arxiv:6300.63001",
            "arxiv:6300.63002",
            "arxiv:6300.63003",
            "arxiv:6300.63004",
        ):
            self.assertNotIn("contrast", data[key]["roles"][stem], key)

    # 44
    def test_44_japanese_contrast_phrases(self):
        stem = "@2026__arXiv__ContrastJa"
        txt = self._role_txt(
            "に対して [1]\nとは異なり [2]\n",
            "[1] Foo Bar title here. arXiv:6400.64001\n"
            "[2] Foo Bar title here. arXiv:6400.64002\n",
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        data = self.ledger()["candidates"]
        self.assertEqual(data["arxiv:6400.64001"]["roles"][stem].get("contrast"), 1)
        self.assertEqual(data["arxiv:6400.64002"]["roles"][stem].get("contrast"), 1)

    # 45
    def test_45_window_is_before_not_after(self):
        stem_after = "@2026__arXiv__ContrastAfter"
        txt_after = self._role_txt("[1] unlike\n", "[1] Foo Bar title here. arXiv:6500.65001\n")
        self.write_paper(stem_after, txt_after)
        self.assertEqual(self.extract(stem_after), 0)
        self.assertNotIn(
            "contrast",
            self.ledger()["candidates"]["arxiv:6500.65001"]["roles"][stem_after],
        )

        stem_cross = "@2026__arXiv__ContrastCross"
        txt_cross = self._role_txt("Unlike\n[1]\n", "[1] Foo Bar title here. arXiv:6500.65002\n")
        self.write_paper(stem_cross, txt_cross)
        self.assertEqual(self.extract(stem_cross), 0)
        self.assertEqual(
            self.ledger()["candidates"]["arxiv:6500.65002"]["roles"][stem_cross].get("contrast"),
            1,
        )

        miss = self.pr.harvest_roles(
            self._role_txt("Unlike" + ("." * 75) + "[1]\n", "[1] Foo Bar title here. arXiv:6500.65003\n")
        )
        self.assertNotIn("contrast", miss.get(("arxiv", "6500.65003"), {}))
        hit = self.pr.harvest_roles(
            self._role_txt("Unlike" + ("." * 74) + "[1]\n", "[1] Foo Bar title here. arXiv:6500.65004\n")
        )
        self.assertEqual(hit[("arxiv", "6500.65004")].get("contrast"), 1)

    # 46
    def test_46_contrast_does_not_make_demand_one_ready(self):
        stem = "@2026__arXiv__ContrastReady"
        self.write_index()
        cites = "Unlike [1]\n" * 5
        txt = self._role_txt(cites, "[1] Foo Bar title here. arXiv:6600.66001\n")
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        entry = self.ledger()["candidates"]["arxiv:6600.66001"]
        self.assertGreaterEqual(entry["roles"][stem]["contrast"], 5)
        self.assertFalse(self.pr.is_ready(entry, self.pr.read_paper_ids_index(), "arxiv:6600.66001"))

    # 47
    def test_47_reextract_replaces_contrast(self):
        stem = "@2026__arXiv__ContrastReplace"
        txt = self._role_txt("Unlike [1]\n", "[1] Foo Bar title here. arXiv:6700.67001\n")
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        self.assertEqual(self.extract(stem), 0)
        roles = self.ledger()["candidates"]["arxiv:6700.67001"]["roles"][stem]
        self.assertEqual(roles.get("contrast"), 1)
        self.assertEqual(roles.get("passing"), 1)

    # 48
    def test_48_report_shows_ledger_contrast_only(self):
        self.write_index()
        data = self.pr.empty_ledger()
        a = self.pr.new_entry("arxiv", "6800.68001", "@A", "2026-01-01")
        a["citing"] = ["@A", "@B"]
        a["roles"] = {"@A": {"contrast": 3}}
        data["candidates"]["arxiv:6800.68001"] = a
        b = self.pr.new_entry("arxiv", "6800.68002", "@A", "2026-01-01")
        b["citing"] = ["@A", "@B"]
        data["candidates"]["arxiv:6800.68002"] = b
        (self.vault / LEDGER_REL).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        rc, body = self.report()
        self.assertEqual(rc, 0)
        line_a = [ln for ln in body.splitlines() if "6800.68001" in ln][0]
        line_b = [ln for ln in body.splitlines() if "6800.68002" in ln][0]
        self.assertIn("対比 3", line_a)
        self.assertNotIn("関連研究", line_a)
        self.assertNotIn("contrast", body)
        self.assertNotIn("対比", line_b)
        for word in self.pr.EVAL_FORBIDDEN:
            self.assertNotIn(word, body)

    # 49
    def test_49_skipped_bib_unlike_does_not_enter_window(self):
        stem = "@2026__arXiv__ContrastSkipBib"
        txt = (
            "2 Related Work\n"
            "see [1]\n"
            "References\n"
            "[1] Unlike Foo Bar title here. arXiv:6900.69001\n"
            "Appendix\n"
            "see [1]\n"
            "References\n"
            "[1] Unlike Foo Bar title here. arXiv:6900.69001\n"
        )
        self.write_paper(stem, txt)
        self.assertEqual(self.extract(stem), 0)
        roles = self.ledger()["candidates"]["arxiv:6900.69001"]["roles"][stem]
        self.assertEqual(roles.get("related_work"), 2)
        self.assertNotIn("contrast", roles)

        stem_rst = "@2026__arXiv__ContrastSkipReset"
        txt_rst = (
            "Unlike [1]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:6900.69002\n"
            "Appendix\n"
            "see [1]\n"
            "References\n"
            "[1] Foo Bar title here. arXiv:6900.69002\n"
        )
        self.write_paper(stem_rst, txt_rst)
        self.assertEqual(self.extract(stem_rst), 0)
        roles_rst = self.ledger()["candidates"]["arxiv:6900.69002"]["roles"][stem_rst]
        self.assertEqual(roles_rst.get("contrast"), 1)
        self.assertEqual(roles_rst.get("passing"), 2)

    # 50
    def test_50_contrast_keeps_lock_and_no_network(self):
        stem = "@2026__arXiv__ContrastLock"
        txt = self._role_txt("Unlike [1]\n", "[1] Foo Bar title here. arXiv:7000.70001\n")
        self.write_paper(stem, txt)
        calls = []
        real = self.pr.acquire_named

        def wrap(rel):
            calls.append(rel)
            return real(rel)

        self.pr.acquire_named = wrap
        self.pr.acquire_ledger = lambda: wrap(self.pr.LEDGER_REL)
        opened = []

        class BoomSocket:
            def __init__(self, *a, **k):
                opened.append(a)
                raise AssertionError("socket opened")

        with mock.patch.object(socket, "socket", BoomSocket):
            self.assertEqual(self.extract(stem), 0)
        self.assertEqual(calls.count(self.pr.LEDGER_REL), 1)
        self.assertEqual(
            self.ledger()["candidates"]["arxiv:7000.70001"]["roles"][stem].get("contrast"),
            1,
        )

        def fail_lock(*a, **k):
            raise AssertionError("page_lock called")

        import wiki_lock

        with mock.patch.object(wiki_lock, "page_lock", fail_lock):
            self.assertEqual(self.extract(stem), 0)


if __name__ == "__main__":
    unittest.main()
