#!/usr/bin/env python3
"""scripts/wiki-page-write.py と scripts/wiki-append.py の単体試験。

一時ディレクトリに最小の vault(`wiki/{concepts,entities,sources}`・
`.vault-meta/`)を作り、`WIKI_VAULT_ROOT` でそこを指させて実行する。
実 vault のページは一切触らない。address 採番は一時 vault 側に置いた
モックの `scripts/allocate-address.sh` で確認する。

  python3 scripts/test_wiki_write_helpers.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PAGE_WRITE = SCRIPTS / "wiki-page-write.py"
APPEND = SCRIPTS / "wiki-append.py"
LOCK_SH = SCRIPTS / "wiki-lock.sh"

MOCK_ALLOCATOR = """#!/usr/bin/env bash
set -euo pipefail
COUNTER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.vault-meta/mock-counter.txt"
[ -f "$COUNTER" ] || echo 1 > "$COUNTER"
n="$(cat "$COUNTER")"
echo $((n + 1)) > "$COUNTER"
printf 'c-%06d\\n' "$n"
"""

SOURCE_PAGE = """---
type: source
title: "試験用ソース"
date: 2026-09-04 10:00
created: 2026-09-04
updated: 2026-09-04
tags:
  - 2026/09/04
  - source
status: seed
related: []
sources: []
source_type: paper
---

# 試験用ソース

本文。
"""

CONCEPT_PAGE = """---
type: concept
title: "試験概念"
date: 2026-06-01 12:00
created: 2026-06-01
updated: 2026-08-01
aliases:
  - "試験概念"
tags:
  - 2026/06/01
  - concept
  - distributed
status: developing
related:
  - "[[別概念]]"
sources:
  - "[[@2026__X__既存ソース]]"
complexity: intermediate
domain: "distributed systems"
---

# 試験概念

## 定義

これは試験用の概念である。

## 分割方式の分類

- **命題である**: 根拠を伴う。
  - 根拠: [[@2026__X__既存ソース]] — 一行

## 未解決の問い

- 既存の問い。解決したら落とす。

## 未編纂の観察

- **既存の観察**: 既存ソースの突き合わせ。(Source: [[@2026__X__既存ソース]])

## 関連

- ソース: [[@2026__X__既存ソース]]
- 概念: [[別概念]]

## 出典

- [[@2026__X__既存ソース]](既存の根拠)
"""

# 実ページに近いフィクスチャ。インライン配列の frontmatter、旧名の受信箱、
# 主題節の中の `###` 小見出し・callout・閉じたコードフェンスを含む。
REALISTIC_CONCEPT_PAGE = """---
type: concept
title: "整合性モデル"
date: 2026-05-02 09:30
created: 2026-05-02
updated: 2026-07-11
aliases:
  - "整合性モデル"
tags: [2026/05/02, concept, consistency]
status: developing
related:
  - "[[線形化可能性]]"
sources: ["[[@2026__A__基礎]]"]
complexity: advanced
domain: "distributed systems"
---

# 整合性モデル

## 定義

読み書きの見え方に関する契約である。

### 線形化可能性

> [!note] 用語
> 単一のコピーがあるかのように見える最も強い保証を指す。
> `## 見出しに見える行` を含めても節にはならない。

擬似コードでは次のようになる。

```python
def read(key):
    # ## これは節ではない
    return quorum_read(key)
```

- **命題である**: 強い保証は待ち時間を増やす。
  - 根拠: [[@2026__A__基礎]] — 定理 2

## 横断的知見

- **既存の観察**: 旧名の受信箱に既に入っている。(Source: [[@2026__A__基礎]])

## 未解決の問い

- 因果整合性の実装費用はどこまで下がるか。

## 関連

- ソース: [[@2026__A__基礎]]

## 出典

- [[@2026__A__基礎]](定理 2)
"""


def run(script, args, vault, stdin=None):
    env = os.environ.copy()
    env["WIKI_VAULT_ROOT"] = str(vault)
    env["WIKI_LOCK_VAULT"] = str(vault)
    return subprocess.run(
        [sys.executable, str(script)] + args,
        cwd=str(vault), capture_output=True, text=True, env=env,
        input=stdin, check=False,
    )


def json_lines(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            out.append(json.loads(line))
    return out


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        for sub in ("wiki/concepts", "wiki/entities", "wiki/sources",
                    ".vault-meta/locks", "scripts", ".raw"):
            (self.vault / sub).mkdir(parents=True, exist_ok=True)
        # ロックは実 vault と同じ wiki-lock.sh を通す(排他の互換を保つため)
        shutil.copy2(str(LOCK_SH), str(self.vault / "scripts" / "wiki-lock.sh"))
        self.addCleanup(self.tmp.cleanup)

    def install_allocator(self, executable=True):
        path = self.vault / "scripts" / "allocate-address.sh"
        path.write_text(MOCK_ALLOCATOR, encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def write_page(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read_page(self, rel):
        return (self.vault / rel).read_text(encoding="utf-8")

    def content_file(self, text, name="content.md"):
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def spec_file(self, data, name="spec.json"):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def lock_files(self):
        """`.vault-meta/locks/` に残っているロックファイル名。"""
        locks = self.vault / ".vault-meta" / "locks"
        if not locks.is_dir():
            return []
        return sorted(p.name for p in locks.glob("*.lock"))

    def lock_name(self, rel):
        return hashlib.sha1(rel.encode("utf-8")).hexdigest() + ".lock"

    def read_only_dir(self, rel):
        """そのディレクトリを書けなくする(片付けは自動で戻す)。"""
        path = self.vault / rel
        path.chmod(0o555)
        self.addCleanup(path.chmod, 0o755)
        return path

    def hold_lock(self, rel):
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        env["WIKI_LOCK_VAULT"] = str(self.vault)
        proc = subprocess.run(
            ["bash", str(self.vault / "scripts" / "wiki-lock.sh"), "acquire", rel],
            capture_output=True, text=True, env=env, check=False)
        self.assertEqual(0, proc.returncode, proc.stderr)

        def _release():
            subprocess.run(
                ["bash", str(self.vault / "scripts" / "wiki-lock.sh"), "release", rel],
                capture_output=True, text=True, env=env, check=False)

        self.addCleanup(_release)


class PageWriteTest(VaultCase):
    def test_creates_page_and_allocates_address(self):
        self.install_allocator()
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__A.md",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual("created", result["action"])
        self.assertEqual("c-000001", result["address"])
        text = self.read_page("wiki/sources/@2026__T__A.md")
        self.assertEqual("---", text.split("\n")[0])
        self.assertEqual("address: c-000001", text.split("\n")[1])

    def test_reads_content_from_stdin(self):
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__B.md", "--no-allocate"],
                   self.vault, stdin=SOURCE_PAGE)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("# 試験用ソース", self.read_page("wiki/sources/@2026__T__B.md"))

    def test_refuses_existing_page_then_force_overwrites(self):
        self.write_page("wiki/sources/@2026__T__C.md", "既存\n")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__C.md", "--no-allocate",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertIn("既存ファイル", json_lines(proc.stdout)[0]["error"])
        self.assertEqual("既存\n", self.read_page("wiki/sources/@2026__T__C.md"))

        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__C.md", "--no-allocate", "--force",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("overwritten", json_lines(proc.stdout)[0]["action"])

    def test_explicit_address_wins_over_allocation(self):
        self.install_allocator()
        proc = run(PAGE_WRITE, ["wiki/entities/E.md", "--address", "c-009999",
                                "--content-file", self.content_file(
                                    SOURCE_PAGE.replace("type: source", "type: entity"))],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("c-009999", json_lines(proc.stdout)[0]["address"])
        self.assertIn("address: c-009999", self.read_page("wiki/entities/E.md"))

    def test_rejects_malformed_address(self):
        proc = run(PAGE_WRITE, ["wiki/entities/E2.md", "--address", "42",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertIn("--address", json_lines(proc.stdout)[0]["error"])

    def test_no_allocate_skips_address(self):
        self.install_allocator()
        proc = run(PAGE_WRITE, ["wiki/entities/E3.md", "--no-allocate",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIsNone(json_lines(proc.stdout)[0]["address"])
        self.assertNotIn("address:", self.read_page("wiki/entities/E3.md"))

    def test_never_allocates_from_another_vault(self):
        """一時 vault に allocator が無いとき、本体リポジトリの採番器へ落ちない。

        allocate-address.sh は自分の位置からカウンタのパスを決めるため、
        フォールバックすると無関係な vault のカウンタを消費してしまう。
        """
        self.assertFalse((self.vault / "scripts" / "allocate-address.sh").exists())
        before = (SCRIPTS.parent / ".vault-meta" / "address-counter.txt")
        snapshot = before.read_text(encoding="utf-8") if before.is_file() else None
        proc = run(PAGE_WRITE, ["wiki/entities/NoAlloc.md",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertIsNone(result["address"])
        self.assertTrue(any("DragonScale" in w for w in result["warnings"]))
        if snapshot is not None:
            self.assertEqual(snapshot, before.read_text(encoding="utf-8"))

    def test_dragonscale_disabled_when_allocator_not_executable(self):
        self.install_allocator(executable=False)
        proc = run(PAGE_WRITE, ["wiki/entities/E4.md",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertIsNone(result["address"])
        self.assertTrue(any("採番" in w for w in result["warnings"]))

    def test_keeps_address_already_in_content(self):
        self.install_allocator()
        body = SOURCE_PAGE.replace("---\ntype: source", "---\naddress: c-000777\ntype: source", 1)
        proc = run(PAGE_WRITE, ["wiki/entities/E5.md", "--content-file", self.content_file(body)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read_page("wiki/entities/E5.md")
        self.assertEqual(1, text.count("address:"))
        self.assertIn("address: c-000777", text)

    def test_validation_failure_does_not_write(self):
        broken = SOURCE_PAGE.replace("  - 2026/09/04\n", "")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__D.md", "--no-allocate",
                                "--content-file", self.content_file(broken)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        result = json_lines(proc.stdout)[0]
        self.assertTrue(any("日付タグ" in v for v in result["violations"]))
        self.assertFalse((self.vault / "wiki/sources/@2026__T__D.md").exists())

    def test_missing_required_key_is_violation(self):
        broken = SOURCE_PAGE.replace("updated: 2026-09-04\n", "")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__E.md", "--no-allocate",
                                "--content-file", self.content_file(broken)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertTrue(any("updated" in v for v in json_lines(proc.stdout)[0]["violations"]))

    def test_two_h1_is_violation(self):
        broken = SOURCE_PAGE + "\n# もう一つの H1\n"
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__F.md", "--no-allocate",
                                "--content-file", self.content_file(broken)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertTrue(any("H1" in v for v in json_lines(proc.stdout)[0]["violations"]))

    def test_book_source_requires_publish_false(self):
        book = SOURCE_PAGE.replace("source_type: paper", "source_type: book")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__OReilly__Bk - Chapter 1 X.md",
                                "--no-allocate", "--content-file", self.content_file(book)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertTrue(any("publish: false" in v
                            for v in json_lines(proc.stdout)[0]["violations"]))

        ok = book.replace("source_type: book", "source_type: book\npublish: false")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__OReilly__Bk - Chapter 1 X.md",
                                "--no-allocate", "--content-file", self.content_file(ok)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_no_check_downgrades_violation_to_warning(self):
        broken = SOURCE_PAGE.replace("  - 2026/09/04\n", "")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__G.md", "--no-allocate", "--no-check",
                                "--content-file", self.content_file(broken)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertTrue(any("--no-check" in w for w in result["warnings"]))
        self.assertTrue((self.vault / "wiki/sources/@2026__T__G.md").exists())

    def test_rejects_catalog_paths(self):
        for rel in ("wiki/index.md", "wiki/hot.md", "wiki/log.md",
                    "wiki/concepts/_index.md"):
            proc = run(PAGE_WRITE, [rel, "--no-allocate", "--force",
                                    "--content-file", self.content_file(SOURCE_PAGE)],
                       self.vault)
            self.assertEqual(65, proc.returncode, rel)
            self.assertIn("wiki-catalog.py", json_lines(proc.stdout)[0]["error"])

    def test_rejects_path_traversal_and_non_wiki(self):
        for rel in ("wiki/../notes/x.md", "notes/x.md", "wiki/concepts/x.txt"):
            proc = run(PAGE_WRITE, [rel, "--no-allocate",
                                    "--content-file", self.content_file(SOURCE_PAGE)],
                       self.vault)
            self.assertEqual(65, proc.returncode, rel)

    def test_lock_contention_reports_75(self):
        self.hold_lock("wiki/sources/@2026__T__H.md")
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__H.md", "--no-allocate",
                                "--lock-wait-sec", "1",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(75, proc.returncode)
        self.assertIn("ロック", json_lines(proc.stdout)[0]["error"])
        self.assertFalse((self.vault / "wiki/sources/@2026__T__H.md").exists())

    def test_batch_continues_after_failure(self):
        self.install_allocator()
        self.write_page("wiki/entities/Dup.md", "既存\n")
        spec = self.spec_file([
            {"path": "wiki/sources/@2026__T__I.md", "content_file": self.content_file(SOURCE_PAGE)},
            {"path": "wiki/entities/Dup.md", "content": SOURCE_PAGE},
            {"path": "wiki/concepts/New.md", "content": CONCEPT_PAGE},
        ])
        proc = run(PAGE_WRITE, ["--batch", spec], self.vault)
        self.assertEqual(65, proc.returncode)
        results = json_lines(proc.stdout)
        self.assertEqual(3, len(results))
        self.assertEqual("created", results[0]["action"])
        self.assertIn("error", results[1])
        self.assertEqual("created", results[2]["action"])
        self.assertEqual("既存\n", self.read_page("wiki/entities/Dup.md"))

    def test_batch_lock_failure_yields_75_and_continues(self):
        self.hold_lock("wiki/entities/Held.md")
        spec = self.spec_file([
            {"path": "wiki/entities/Held.md", "content": SOURCE_PAGE},
            {"path": "wiki/concepts/After.md", "content": CONCEPT_PAGE},
        ])
        proc = run(PAGE_WRITE, ["--batch", spec, "--no-allocate", "--lock-wait-sec", "1"],
                   self.vault)
        self.assertEqual(75, proc.returncode)
        results = json_lines(proc.stdout)
        self.assertIn("error", results[0])
        self.assertEqual("created", results[1]["action"])

    def test_record_address_map(self):
        self.install_allocator()
        (self.vault / ".raw" / ".manifest.json").write_text(
            json.dumps({"sources": {"a": 1}}), encoding="utf-8")
        proc = run(PAGE_WRITE, ["wiki/concepts/Mapped.md", "--record-address-map",
                                "--content-file", self.content_file(CONCEPT_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        manifest = json.loads((self.vault / ".raw" / ".manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({"wiki/concepts/Mapped.md": "c-000001"}, manifest["address_map"])
        self.assertEqual({"a": 1}, manifest["sources"])

    def test_address_map_not_recorded_by_default(self):
        self.install_allocator()
        run(PAGE_WRITE, ["wiki/concepts/Unmapped.md",
                         "--content-file", self.content_file(CONCEPT_PAGE)], self.vault)
        self.assertFalse((self.vault / ".raw" / ".manifest.json").exists())

    def test_quiet_prints_path_only(self):
        proc = run(PAGE_WRITE, ["wiki/sources/@2026__T__J.md", "--no-allocate", "--quiet",
                                "--content-file", self.content_file(SOURCE_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("wiki/sources/@2026__T__J.md", proc.stdout.strip())

    def test_missing_frontmatter_is_error(self):
        proc = run(PAGE_WRITE, ["wiki/concepts/NoFm.md", "--no-allocate",
                                "--content-file", self.content_file("# 見出しだけ\n")],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertIn("frontmatter", json_lines(proc.stdout)[0]["error"])


    def test_concurrent_create_lets_exactly_one_win(self):
        """同一の未存在パスへ同時に書くと 1 つが成功し、もう 1 つは 65。"""
        env = os.environ.copy()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        env["WIKI_LOCK_VAULT"] = str(self.vault)
        for trial in range(10):
            rel = "wiki/sources/@2026__P__並行%02d.md" % trial
            bodies = {}
            procs = []
            for tag in ("A", "B"):
                text = SOURCE_PAGE.replace("本文。", "本文 %s。" % tag)
                bodies[tag] = text
                spec = self.content_file(text, name="race-%02d-%s.md" % (trial, tag))
                procs.append((tag, subprocess.Popen(
                    [sys.executable, str(PAGE_WRITE), rel, "--no-allocate",
                     "--lock-wait-sec", "1", "--content-file", spec],
                    cwd=str(self.vault), env=env, text=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
            outcome = {}
            for tag, proc in procs:
                out, err = proc.communicate()
                outcome[tag] = (proc.returncode, out, err)
            codes = sorted(code for code, _, _ in outcome.values())
            self.assertEqual([0, 65], codes, outcome)
            winner = [t for t, (code, _, _) in outcome.items() if code == 0][0]
            loser = [t for t, (code, _, _) in outcome.items() if code == 65][0]
            self.assertEqual("created", json_lines(outcome[winner][1])[0]["action"])
            self.assertIn("既存ファイル", json_lines(outcome[loser][1])[0]["error"])
            self.assertEqual(bodies[winner], self.read_page(rel))
            self.assertEqual([], self.lock_files())

    def test_concurrent_create_does_not_orphan_address(self):
        """負けた側は採番しない(孤児アドレスをカウンタに残さない)。"""
        self.install_allocator()
        self.write_page("wiki/concepts/先着.md", CONCEPT_PAGE)
        proc = run(PAGE_WRITE, ["wiki/concepts/先着.md",
                                "--content-file", self.content_file(CONCEPT_PAGE)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        counter = self.vault / ".vault-meta" / "mock-counter.txt"
        self.assertFalse(counter.exists(), "拒否経路で採番器を呼んでいる")

    def test_validation_failure_does_not_allocate(self):
        self.install_allocator()
        broken = CONCEPT_PAGE.replace("# 試験概念", "本文だけ")
        proc = run(PAGE_WRITE, ["wiki/concepts/H1無し.md",
                                "--content-file", self.content_file(broken)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertFalse((self.vault / ".vault-meta" / "mock-counter.txt").exists())

    def test_batch_with_duplicate_path_rejects_the_second(self):
        rel = "wiki/concepts/重複.md"
        spec = self.spec_file([
            {"path": rel, "content": CONCEPT_PAGE},
            {"path": rel, "content": CONCEPT_PAGE.replace("試験概念", "後着")},
        ])
        proc = run(PAGE_WRITE, ["--batch", spec, "--no-allocate"], self.vault)
        self.assertEqual(65, proc.returncode)
        results = json_lines(proc.stdout)
        self.assertEqual(2, len(results))
        self.assertEqual("created", results[0]["action"])
        self.assertIn("既存ファイル", results[1]["error"])
        text = self.read_page(rel)
        self.assertIn("# 試験概念", text)
        self.assertNotIn("後着", text)

    def test_write_failure_leaves_no_lock_file(self):
        self.read_only_dir("wiki/concepts")
        rel = "wiki/concepts/書けない.md"
        proc = run(PAGE_WRITE, [rel, "--no-allocate",
                                "--content-file", self.content_file(CONCEPT_PAGE)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertIn("書き込みに失敗した", json_lines(proc.stdout)[0]["error"])
        self.assertNotIn(self.lock_name(rel), self.lock_files())
        self.assertEqual([], self.lock_files())

    def test_symlink_out_of_wiki_is_a_path_error(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.vault / "wiki" / "escape").symlink_to(str(outside),
                                                    target_is_directory=True)
        proc = run(PAGE_WRITE, ["wiki/escape/逃走.md", "--no-allocate",
                                "--content-file", self.content_file(CONCEPT_PAGE)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertIn("シンボリックリンク", json_lines(proc.stdout)[0]["error"])
        self.assertFalse((outside / "逃走.md").exists())

    def test_unterminated_frontmatter_says_so(self):
        broken = "---\ntype: concept\ntitle: \"閉じ無し\"\n\n# 閉じ無し\n"
        proc = run(PAGE_WRITE, ["wiki/concepts/閉じ無し.md", "--no-allocate",
                                "--content-file", self.content_file(broken)],
                   self.vault)
        self.assertEqual(65, proc.returncode)
        self.assertIn("閉じ `---` が無い", json_lines(proc.stdout)[0]["error"])

    def test_lock_wait_zero_still_acquires_a_free_lock(self):
        proc = run(PAGE_WRITE, ["wiki/concepts/待ち0.md", "--no-allocate",
                                "--lock-wait-sec", "0",
                                "--content-file", self.content_file(CONCEPT_PAGE)],
                   self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("created", json_lines(proc.stdout)[0]["action"])


class AppendTest(VaultCase):
    REL = "wiki/concepts/試験概念.md"

    def setUp(self):
        VaultCase.setUp(self)
        self.write_page(self.REL, CONCEPT_PAGE)

    def append(self, args, rel=None):
        return run(APPEND, ["--page", rel or self.REL, "--date", "2026-09-04"] + args,
                   self.vault)

    def section_of(self, text, name):
        lines = text.split("\n")
        out = []
        inside = False
        for line in lines:
            if line.startswith("## "):
                inside = line[3:].strip() == name
                continue
            if inside:
                out.append(line)
        return "\n".join(out)

    def test_observation_appended_to_inbox_tail(self):
        proc = self.append(["--observation", "[分割方式の分類] 新しい観察(Source: [[@2026__Y__新]])"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertTrue(result["changed"])
        self.assertEqual(1, result["added"]["observations"])
        inbox = self.section_of(self.read_page(self.REL), "未編纂の観察")
        self.assertIn("新しい観察", inbox)
        self.assertLess(inbox.index("既存の観察"), inbox.index("新しい観察"))

    def test_updated_and_date_tag_touched(self):
        self.append(["--observation", "観察 A"])
        text = self.read_page(self.REL)
        self.assertIn("updated: 2026-09-04", text)
        self.assertNotIn("updated: 2026-08-01", text)
        self.assertIn("  - 2026/09/04", text)
        # date:(時刻付き)は触らない
        self.assertIn("date: 2026-06-01 12:00", text)
        # 日付タグは先頭の日付タグ群の直後に入る(conventions §2 ルール 1)
        tags = text.split("tags:\n")[1].split("status:")[0].split("\n")
        self.assertEqual("  - 2026/06/01", tags[0])
        self.assertEqual("  - 2026/09/04", tags[1])

    def test_legacy_inbox_heading_is_used(self):
        legacy = CONCEPT_PAGE.replace("## 未編纂の観察", "## 横断的知見")
        self.write_page(self.REL, legacy)
        self.append(["--observation", "旧名への追記"])
        text = self.read_page(self.REL)
        self.assertNotIn("## 未編纂の観察", text)
        self.assertIn("旧名への追記", self.section_of(text, "横断的知見"))

    def test_inbox_created_before_related(self):
        stripped = CONCEPT_PAGE.replace(
            "## 未編纂の観察\n\n"
            "- **既存の観察**: 既存ソースの突き合わせ。(Source: [[@2026__X__既存ソース]])\n\n", "")
        self.write_page(self.REL, stripped)
        proc = self.append(["--observation", "新設先への観察"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read_page(self.REL)
        self.assertLess(text.index("## 未編纂の観察"), text.index("## 関連"))
        self.assertIn("新設先への観察", self.section_of(text, "未編纂の観察"))

    def test_questions_section_created_before_inbox(self):
        stripped = CONCEPT_PAGE.replace(
            "## 未解決の問い\n\n- 既存の問い。解決したら落とす。\n\n", "")
        self.write_page(self.REL, stripped)
        self.append(["--question", "新設された問い"])
        text = self.read_page(self.REL)
        self.assertLess(text.index("## 未解決の問い"), text.index("## 未編纂の観察"))

    def test_question_add_and_remove(self):
        proc = self.append(["--question", "追加された問い",
                            "--remove-question-containing", "解決したら落とす"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual(1, result["added"]["questions"])
        self.assertEqual(1, result["removed_questions"])
        section = self.section_of(self.read_page(self.REL), "未解決の問い")
        self.assertIn("追加された問い", section)
        self.assertNotIn("既存の問い", section)

    def test_related_line_extended_and_deduped(self):
        proc = self.append(["--related-source", "[[@2026__Y__新]]",
                            "--related-source", "[[@2026__X__既存ソース]]",
                            "--related-entity", "[[新エンティティ]]"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual(2, result["added"]["related"])
        self.assertEqual(1, result["skipped_duplicate"])
        section = self.section_of(self.read_page(self.REL), "関連")
        self.assertIn("- ソース: [[@2026__X__既存ソース]] / [[@2026__Y__新]]", section)
        self.assertIn("- エンティティ: [[新エンティティ]]", section)

    def test_sources_section_dedupes_by_link(self):
        proc = self.append(["--source-entry", "[[@2026__Y__新]](新しい根拠)",
                            "--source-entry", "[[@2026__X__既存ソース]](重複)"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual(1, result["added"]["sources_section"])
        self.assertEqual(1, result["skipped_duplicate"])
        section = self.section_of(self.read_page(self.REL), "出典")
        self.assertIn("- [[@2026__Y__新]](新しい根拠)", section)
        self.assertEqual(1, section.count("@2026__X__既存ソース"))

    def test_frontmatter_sources_appended_with_existing_quoting(self):
        proc = self.append(["--frontmatter-source", "[[@2026__Y__新]]",
                            "--frontmatter-source", "[[@2026__X__既存ソース]]"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual(1, result["added"]["frontmatter_sources"])
        text = self.read_page(self.REL)
        self.assertIn('sources:\n  - "[[@2026__X__既存ソース]]"\n  - "[[@2026__Y__新]]"\n', text)

    def test_frontmatter_sources_converts_empty_list(self):
        self.write_page(self.REL, CONCEPT_PAGE.replace(
            'sources:\n  - "[[@2026__X__既存ソース]]"\n', "sources: []\n"))
        self.append(["--frontmatter-source", "[[@2026__Y__新]]"])
        self.assertIn('sources:\n  - "[[@2026__Y__新]]"\n', self.read_page(self.REL))

    def test_idempotent_second_run_is_noop(self):
        args = ["--observation", "冪等の観察", "--question", "冪等の問い",
                "--related-source", "[[@2026__Y__新]]",
                "--source-entry", "[[@2026__Y__新]](根拠)",
                "--frontmatter-source", "[[@2026__Y__新]]"]
        self.assertEqual(0, self.append(args).returncode)
        first = self.read_page(self.REL)
        proc = self.append(args)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertFalse(result["changed"])
        self.assertEqual(5, result["skipped_duplicate"])
        self.assertEqual(first, self.read_page(self.REL))

    def test_topic_section_never_rewritten(self):
        before = self.section_of(self.read_page(self.REL), "分割方式の分類")
        self.append(["--observation", "観察", "--question", "問い",
                     "--related-concept", "[[追加概念]]"])
        self.assertEqual(before, self.section_of(self.read_page(self.REL), "分割方式の分類"))

    def test_section_append_requires_existing_section(self):
        proc = self.append(["--section", "関連ソース", "--section-line", "- [[@X]] — 要旨"])
        self.assertEqual(65, proc.returncode)
        self.assertIn("関連ソース", json_lines(proc.stdout)[0]["error"])
        self.assertEqual(CONCEPT_PAGE, self.read_page(self.REL))

    def test_section_append_into_entity_section(self):
        rel = "wiki/entities/Hub.md"
        self.write_page(rel, CONCEPT_PAGE.replace("## 定義", "## 関連ソース", 1))
        proc = self.append(["--section", "関連ソース", "--section-line", "- [[@X]] — 要旨"],
                           rel=rel)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, json_lines(proc.stdout)[0]["added"]["section_append"])
        self.assertIn("- [[@X]] — 要旨", self.section_of(self.read_page(rel), "関連ソース"))

    def test_code_fence_heading_is_not_a_section(self):
        fenced = CONCEPT_PAGE.replace(
            "## 定義\n\nこれは試験用の概念である。\n",
            "## 定義\n\n```markdown\n## 未編纂の観察\n- 例示であって節ではない\n```\n")
        self.write_page(self.REL, fenced)
        self.append(["--observation", "本物の受信箱へ"])
        text = self.read_page(self.REL)
        self.assertIn("- 例示であって節ではない\n```", text)
        inbox = text.split("## 未編纂の観察\n")[-1].split("## 関連")[0]
        self.assertIn("本物の受信箱へ", inbox)

    def test_line_limit_warning(self):
        padded = CONCEPT_PAGE.replace(
            "## 関連", "\n".join("- 埋め草 %d" % n for n in range(320)) + "\n\n## 関連", 1)
        self.write_page(self.REL, padded)
        proc = self.append(["--observation", "厚いページへの追記"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertTrue(result["changed"])
        self.assertTrue(any("目安" in w for w in result["warnings"]))
        self.assertIn("厚いページへの追記", self.read_page(self.REL))

    def test_dry_run_shows_diff_without_writing(self):
        proc = self.append(["--dry-run", "--observation", "dry-run の観察"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("+- dry-run の観察", proc.stdout)
        self.assertIn("--- a/" + self.REL, proc.stdout)
        self.assertTrue(json_lines(proc.stdout)[0]["changed"])
        self.assertEqual(CONCEPT_PAGE, self.read_page(self.REL))

    def test_crlf_and_missing_trailing_newline(self):
        self.write_page(self.REL, CONCEPT_PAGE.rstrip("\n").replace("\n", "\r\n"))
        proc = self.append(["--observation", "CRLF の観察"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        raw = (self.vault / self.REL).read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn("CRLF の観察", raw.decode("utf-8"))

    def test_missing_page_and_missing_frontmatter(self):
        proc = self.append([], rel="wiki/concepts/無い.md")
        self.assertEqual(65, proc.returncode)
        self.assertIn("ページが無い", json_lines(proc.stdout)[0]["error"])

        self.write_page("wiki/concepts/NoFm.md", "# 見出しだけ\n\n## 未編纂の観察\n")
        proc = self.append(["--observation", "x"], rel="wiki/concepts/NoFm.md")
        self.assertEqual(65, proc.returncode)
        self.assertIn("frontmatter", json_lines(proc.stdout)[0]["error"])

    def test_lock_contention_reports_75(self):
        self.hold_lock(self.REL)
        proc = self.append(["--lock-wait-sec", "1", "--observation", "ロック中の観察"])
        self.assertEqual(75, proc.returncode)
        self.assertIn("ロック", json_lines(proc.stdout)[0]["error"])
        self.assertEqual(CONCEPT_PAGE, self.read_page(self.REL))

    def test_batch_applies_multiple_pages(self):
        other = "wiki/concepts/別概念.md"
        self.write_page(other, CONCEPT_PAGE.replace("試験概念", "別概念"))
        spec = self.spec_file([
            {"page": self.REL, "date": "2026-09-04",
             "observations": ["一括の観察 1", "一括の観察 2"],
             "questions_add": ["一括の問い"],
             "related": {"ソース": ["[[@2026__Z__新]]"]},
             "sources_section": [{"link": "[[@2026__Z__新]]", "note": "根拠"}],
             "frontmatter_sources": ["[[@2026__Z__新]]"]},
            {"page": other, "date": "2026-09-04", "observations": ["別ページの観察"]},
            {"page": "wiki/concepts/無い.md", "observations": ["失敗する観察"]},
        ])
        proc = run(APPEND, ["--batch", spec], self.vault)
        self.assertEqual(65, proc.returncode)
        results = json_lines(proc.stdout)
        self.assertEqual(3, len(results))
        self.assertEqual(2, results[0]["added"]["observations"])
        self.assertTrue(results[1]["changed"])
        self.assertIn("error", results[2])
        self.assertIn("一括の観察 2", self.read_page(self.REL))
        self.assertIn("- [[@2026__Z__新]](根拠)", self.read_page(self.REL))
        self.assertIn("別ページの観察", self.read_page(other))

    def test_empty_operation_leaves_page_untouched(self):
        proc = self.append([])
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertFalse(json_lines(proc.stdout)[0]["changed"])
        self.assertEqual(CONCEPT_PAGE, self.read_page(self.REL))

    def test_empty_bullet_placeholder_is_replaced(self):
        self.write_page(self.REL, CONCEPT_PAGE.replace(
            "- [[@2026__X__既存ソース]](既存の根拠)", "- "))
        self.append(["--source-entry", "[[@2026__Y__新]](根拠)"])
        section = self.section_of(self.read_page(self.REL), "出典")
        self.assertNotIn("\n- \n", section)
        self.assertIn("- [[@2026__Y__新]](根拠)", section)

    def test_blank_line_kept_before_following_heading(self):
        """空の節へ追記しても、次の `## ` 見出しの直前の空行を潰さない。"""
        empty = CONCEPT_PAGE.replace(
            "- **既存の観察**: 既存ソースの突き合わせ。(Source: [[@2026__X__既存ソース]])\n\n",
            "").replace("- ソース: [[@2026__X__既存ソース]]\n- 概念: [[別概念]]\n\n", "")
        self.write_page(self.REL, empty)
        proc = self.append(["--observation", "空節への観察", "--related-source", "[[@新]]"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read_page(self.REL)
        self.assertIn("- 空節への観察\n\n## 関連", text)
        self.assertIn("- ソース: [[@新]]\n\n## 出典", text)

    def test_blank_line_kept_when_section_is_created(self):
        stripped = CONCEPT_PAGE.replace(
            "## 未編纂の観察\n\n"
            "- **既存の観察**: 既存ソースの突き合わせ。(Source: [[@2026__X__既存ソース]])\n\n", "")
        self.write_page(self.REL, stripped)
        self.append(["--observation", "新設節への観察"])
        self.assertIn("## 未編纂の観察\n\n- 新設節への観察\n\n## 関連",
                      self.read_page(self.REL))

    def test_final_section_append_keeps_trailing_newline(self):
        self.append(["--source-entry", "[[@2026__Y__新]](末尾節への追記)"])
        text = self.read_page(self.REL)
        self.assertTrue(text.endswith("(末尾節への追記)\n"))

    # ── インライン配列の frontmatter ───────────────────────────────────
    def raw_section(self, text, name):
        """`## name` から次の `## ` の直前までを改行込みでそのまま返す。"""
        head = "\n## %s\n" % name
        start = text.index(head)
        return text[start:text.index("\n## ", start + len(head))]

    def test_inline_tags_expanded_to_block_list(self):
        page = CONCEPT_PAGE.replace(
            "tags:\n  - 2026/06/01\n  - concept\n  - distributed\n",
            "tags: [2026/06/01, concept, distributed]\n")
        self.write_page(self.REL, page)
        proc = self.append(["--observation", "インライン tags への追記"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read_page(self.REL)
        self.assertNotIn("tags: [", text)
        tags = text.split("tags:\n")[1].split("status:")[0].rstrip("\n").split("\n")
        self.assertEqual(["  - 2026/06/01", "  - 2026/09/04",
                          "  - concept", "  - distributed"], tags)

    def test_inline_sources_expanded_to_block_list(self):
        page = CONCEPT_PAGE.replace(
            'sources:\n  - "[[@2026__X__既存ソース]]"\n',
            'sources: ["[[@2026__X__既存ソース]]"]\n')
        self.write_page(self.REL, page)
        proc = self.append(["--frontmatter-source", "[[@2026__X__既存ソース]]",
                            "--frontmatter-source", "[[@2026__Y__新]]"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual(1, result["added"]["frontmatter_sources"])
        self.assertEqual(1, result["skipped_duplicate"])
        text = self.read_page(self.REL)
        self.assertNotIn("sources: [", text)
        self.assertIn('sources:\n  - "[[@2026__X__既存ソース]]"\n'
                      '  - "[[@2026__Y__新]]"\n', text)

    def test_inline_list_split_respects_commas_inside_links(self):
        page = CONCEPT_PAGE.replace(
            'sources:\n  - "[[@2026__X__既存ソース]]"\n',
            'sources: ["[[@2003__CFS__1,000 node clusters]]", "[[@2026__X__既存ソース]]"]\n')
        self.write_page(self.REL, page)
        proc = self.append(["--frontmatter-source", "[[@2026__Y__新]]"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read_page(self.REL)
        self.assertIn('sources:\n  - "[[@2003__CFS__1,000 node clusters]]"\n'
                      '  - "[[@2026__X__既存ソース]]"\n  - "[[@2026__Y__新]]"\n', text)

    def test_broken_inline_tags_is_an_error_without_writing(self):
        page = CONCEPT_PAGE.replace(
            "tags:\n  - 2026/06/01\n  - concept\n  - distributed\n",
            "tags: [2026/06/01, concept\n")
        self.write_page(self.REL, page)
        proc = self.append(["--observation", "壊れた tags への追記"])
        self.assertEqual(65, proc.returncode)
        self.assertIn("インライン配列として解析できない",
                      json_lines(proc.stdout)[0]["error"])
        self.assertEqual(page, self.read_page(self.REL))

    def test_broken_inline_sources_is_an_error_without_writing(self):
        page = CONCEPT_PAGE.replace(
            'sources:\n  - "[[@2026__X__既存ソース]]"\n',
            'sources: ["[[@2026__X__既存ソース]"\n')
        self.write_page(self.REL, page)
        proc = self.append(["--frontmatter-source", "[[@2026__Y__新]]"])
        self.assertEqual(65, proc.returncode)
        self.assertIn("インライン配列として解析できない",
                      json_lines(proc.stdout)[0]["error"])
        self.assertEqual(page, self.read_page(self.REL))

    # ── 例外の隔離と改行の保存 ─────────────────────────────────────────
    def test_unterminated_fence_fails_only_that_page(self):
        broken = CONCEPT_PAGE.replace(
            "## 未編纂の観察\n\n"
            "- **既存の観察**: 既存ソースの突き合わせ。(Source: [[@2026__X__既存ソース]])\n\n",
            "").replace("これは試験用の概念である。",
                        "```python\nx = 1  # 閉じ忘れたフェンス\n")
        broken_rel = "wiki/concepts/フェンス閉じ忘れ.md"
        self.write_page(broken_rel, broken)
        spec = self.spec_file([
            {"page": broken_rel, "date": "2026-09-04", "observations": ["落ちる観察"]},
            {"page": self.REL, "date": "2026-09-04", "observations": ["後続の観察"]},
        ])
        proc = run(APPEND, ["--batch", spec], self.vault)
        self.assertEqual(65, proc.returncode)
        results = json_lines(proc.stdout)
        self.assertEqual(2, len(results))
        self.assertIn("フェンス", results[0]["error"])
        self.assertEqual(broken, self.read_page(broken_rel))
        self.assertTrue(results[1]["changed"])
        self.assertIn("後続の観察", self.read_page(self.REL))
        self.assertEqual([], self.lock_files())

    def test_mixed_crlf_keeps_each_line_ending(self):
        mixed = CONCEPT_PAGE.replace("## 定義\n", "## 定義\r\n")
        self.write_page(self.REL, mixed)
        proc = self.append(["--observation", "混在改行の観察"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        raw = (self.vault / self.REL).read_bytes()
        self.assertEqual(1, raw.count(b"\r"))
        self.assertIn("## 定義\r\n".encode("utf-8"), raw)
        self.assertIn("- 混在改行の観察\n".encode("utf-8"), raw)
        self.assertIn("updated: 2026-09-04\n".encode("utf-8"), raw)

    def test_write_failure_leaves_no_lock_file(self):
        self.read_only_dir("wiki/concepts")
        proc = self.append(["--observation", "書けない観察"])
        self.assertEqual(65, proc.returncode)
        self.assertIn("書き込みに失敗した", json_lines(proc.stdout)[0]["error"])
        self.assertNotIn(self.lock_name(self.REL), self.lock_files())
        self.assertEqual([], self.lock_files())

    def test_batch_with_duplicate_page_applies_once(self):
        spec = self.spec_file([
            {"page": self.REL, "date": "2026-09-04", "observations": ["重複バッチの観察"]},
            {"page": self.REL, "date": "2026-09-04", "observations": ["重複バッチの観察"]},
        ])
        proc = run(APPEND, ["--batch", spec], self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        results = json_lines(proc.stdout)
        self.assertEqual(2, len(results))
        self.assertTrue(results[0]["changed"])
        self.assertFalse(results[1]["changed"])
        self.assertEqual(1, results[1]["skipped_duplicate"])
        self.assertEqual(1, self.read_page(self.REL).count("重複バッチの観察"))

    def test_questions_remove_ignores_other_sections(self):
        """他の節にだけある文字列を渡しても、どの節も消えない。"""
        proc = self.append(["--remove-question-containing", "既存の観察"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json_lines(proc.stdout)[0]
        self.assertEqual(0, result["removed_questions"])
        self.assertFalse(result["changed"])
        self.assertEqual(CONCEPT_PAGE, self.read_page(self.REL))

    def test_lock_wait_zero_still_acquires_a_free_lock(self):
        proc = self.append(["--lock-wait-sec", "0", "--observation", "待ち 0 の観察"])
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("待ち 0 の観察", self.read_page(self.REL))

    # ── 実ページに近いフィクスチャ ─────────────────────────────────────
    def test_realistic_page_keeps_topic_sections_verbatim(self):
        rel = "wiki/concepts/整合性モデル.md"
        self.write_page(rel, REALISTIC_CONCEPT_PAGE)
        before = self.raw_section(REALISTIC_CONCEPT_PAGE, "定義")
        proc = run(APPEND, ["--page", rel, "--date", "2026-09-04",
                            "--observation", "現実的ページへの観察",
                            "--question", "現実的ページの問い",
                            "--related-concept", "[[線形化可能性]]",
                            "--source-entry", "[[@2026__B__新]](新根拠)",
                            "--frontmatter-source", "[[@2026__B__新]]"], self.vault)
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read_page(rel)
        # 主題節は 1 バイトも変わらない(`###`・callout・フェンスを含む)
        self.assertEqual(before, self.raw_section(text, "定義"))
        self.assertIn("### 線形化可能性", text)
        self.assertIn("> [!note] 用語", text)
        self.assertIn("    # ## これは節ではない", text)
        # 旧名の受信箱を使い、`## 未編纂の観察` を新設しない
        self.assertNotIn("## 未編纂の観察", text)
        self.assertIn("現実的ページへの観察", self.raw_section(text, "横断的知見"))
        self.assertIn("現実的ページの問い", self.raw_section(text, "未解決の問い"))
        # インライン配列はブロックリストへ展開される
        self.assertNotIn("tags: [", text)
        self.assertNotIn("sources: [", text)
        self.assertIn("tags:\n  - 2026/05/02\n  - 2026/09/04\n"
                      "  - concept\n  - consistency\n", text)
        self.assertIn('sources:\n  - "[[@2026__A__基礎]]"\n  - "[[@2026__B__新]]"\n', text)
        self.assertIn("updated: 2026-09-04", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
