#!/usr/bin/env python3
"""scripts/wiki-verify-ingest.py の単体試験。

一時ディレクトリに `git init` した最小の vault を作り、`WIKI_VAULT_ROOT` で
そこを指させて実行する。実 vault のページ・`.raw/`・`.vault-meta/` は一切
触らない。git の書き込み操作(`add` / `commit`)も一時リポジトリの中だけで行う。

  python3 scripts/test_wiki_verify_ingest.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
VERIFY = SCRIPTS / "wiki-verify-ingest.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

DEFAULT_FM = [
    ("address", "c-000001"),
    ("type", "source"),
    ("title", '"試験ページ"'),
    ("date", "2026-09-04 10:00"),
    ("created", "2026-09-04"),
    ("updated", "2026-09-04"),
    ("status", "developing"),
    ("source_type", "book"),
    ("publish", "false"),
]


def page(body="本文。", h1="試験ページ", tags=("2026/09/04", "source"),
         drop=(), **overrides):
    """frontmatter + H1 + 本文の素朴なページを組み立てる。"""
    lines = ["---"]
    keys = [k for k, _ in DEFAULT_FM]
    for key, value in DEFAULT_FM:
        if key in drop:
            continue
        lines.append("%s: %s" % (key, overrides.get(key, value)))
    for key, value in overrides.items():
        if key not in keys:
            lines.append("%s: %s" % (key, value))
    if "tags" not in drop:
        lines.append("tags:")
        lines.extend("  - %s" % t for t in tags)
    lines.append("---")
    lines.append("")
    if h1 is not None:
        lines.append("# %s" % h1)
        lines.append("")
    lines.append(body)
    return "\n".join(lines) + "\n"


class VerifyCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="verify-vault-")).resolve()
        for rel in ("wiki/sources", "wiki/entities", "wiki/concepts",
                    "wiki/sources/_attachments/bookslug", "papers", "notes"):
            (self.vault / rel).mkdir(parents=True, exist_ok=True)
        try:
            self.git("init", "-q")
            self.git_ok = True
        except (subprocess.CalledProcessError, OSError):
            # サンドボックスが hook の書き込みを拒むと init が落ちる。git に
            # 依存しない検査は素の vault でも回せるので、そちらだけ通す。
            self.git_ok = False

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    # -------------------------------------------------------- 小道具

    def require_git(self):
        if not self.git_ok:
            self.skipTest("一時リポジトリの git init に失敗した(サンドボックス)")

    def env(self):
        env = dict(os.environ)
        env.update(GIT_ENV)
        # init に失敗したときに親ディレクトリのリポジトリを拾わせない。
        env["GIT_CEILING_DIRECTORIES"] = str(self.vault.parent)
        return env

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=str(self.vault),
                              env=self.env(), capture_output=True,
                              text=True, check=True)

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_verify(self, *args):
        env = self.env()
        env["WIKI_VAULT_ROOT"] = str(self.vault)
        proc = subprocess.run([sys.executable, str(VERIFY)] + list(args),
                              cwd=str(self.vault), env=env,
                              capture_output=True, text=True)
        return proc

    def ids(self, proc):
        """標準出力の所見行から検査 ID を取り出す。"""
        out = []
        for line in proc.stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[0] in ("ERROR", "WARN", "INFO"):
                # パスが無い所見は `SEV ID: 本文` なので末尾のコロンを落とす。
                out.append((parts[0], parts[1].rstrip(":")))
        return out

    def summary(self, proc):
        line = [x for x in proc.stdout.splitlines() if "=" in x and " " in x][-1]
        return dict(kv.split("=", 1) for kv in line.split(" "))

    def assertHas(self, proc, severity, check):
        self.assertIn((severity, check), self.ids(proc),
                      msg="stdout=%s\nstderr=%s" % (proc.stdout, proc.stderr))

    def assertLacks(self, proc, check):
        self.assertNotIn(check, [c for _, c in self.ids(proc)],
                         msg="stdout=%s" % proc.stdout)

    # -------------------------------------------------------- 正常系

    def test_clean_book_chapter_passes(self):
        self.write("wiki/sources/@2026__Pub__本 - Chapter 1 序.md",
                   page(body="本文。\n" * 120))
        proc = self.run_verify("--pages",
                               "wiki/sources/@2026__Pub__本 - Chapter 1 序.md")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.ids(proc), [])
        self.assertEqual(self.summary(proc)["pages"], "1")

    def test_no_target_is_error(self):
        proc = self.run_verify("--glob", "wiki/sources/存在しない*")
        self.assertEqual(proc.returncode, 1)
        self.assertHas(proc, "ERROR", "TARGET")

    def test_usage_error_without_target_selector(self):
        proc = self.run_verify("--expect-count", "3")
        self.assertEqual(proc.returncode, 2)

    def test_usage_error_on_bad_date(self):
        proc = self.run_verify("--pages", "wiki/sources/x.md", "--date", "2026/09/04")
        self.assertEqual(proc.returncode, 2)

    # ------------------------------------------------ frontmatter 検査

    def test_missing_address_on_new_page_is_error(self):
        self.write("wiki/sources/a.md", page(drop=("address",)))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-ADDR")

    def test_missing_address_on_tracked_page_is_warning(self):
        self.require_git()
        self.write("wiki/sources/a.md", page(drop=("address",)))
        self.git("add", "wiki/sources/a.md")
        self.git("commit", "-q", "-m", "seed")
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "WARN", "FM-ADDR")
        self.assertEqual(proc.returncode, 0)

    def test_malformed_address_is_error(self):
        self.write("wiki/sources/a.md", page(address="c-12"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-ADDR")

    def test_unknown_type_is_error(self):
        self.write("wiki/sources/a.md", page(type="sauce"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-TYPE")

    def test_ask_and_brief_types_are_known(self):
        (self.vault / "wiki/asks").mkdir(parents=True, exist_ok=True)
        (self.vault / "wiki/briefs").mkdir(parents=True, exist_ok=True)
        self.write("wiki/asks/@2026__X__Y.md",
                   page(type="ask", title='"Q&A"', h1="Q&A", address="c-000010",
                        tags=("2026/09/04", "ask"),
                        drop=("source_type", "publish")))
        self.write("wiki/briefs/@2026__X__Y.md",
                   page(type="brief", title='"紹介"', h1="紹介", address="c-000011",
                        tags=("2026/09/04", "brief"),
                        drop=("source_type", "publish")))
        for rel in ("wiki/asks/@2026__X__Y.md", "wiki/briefs/@2026__X__Y.md"):
            proc = self.run_verify("--pages", rel)
            self.assertLacks(proc, "FM-TYPE")
            self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_brief_frontmatter_link_is_checked(self):
        """本番形: source と同 stem のパス修飾。stem フォールバックでは死なない。"""
        self.write("wiki/sources/@2026__X__Y.md",
                   page(title='"Y"', h1="Y",
                        brief='"[[wiki/briefs/@2026__X__Y|紹介文]]"'))
        proc = self.run_verify("--pages", "wiki/sources/@2026__X__Y.md")
        self.assertHas(proc, "ERROR", "LINK")
        (self.vault / "wiki/briefs").mkdir(parents=True, exist_ok=True)
        self.write("wiki/briefs/@2026__X__Y.md",
                   page(type="brief", title='"紹介"', h1="紹介",
                        address="c-000011", tags=("2026/09/04", "brief"),
                        drop=("source_type", "publish")))
        ok = self.run_verify("--pages", "wiki/sources/@2026__X__Y.md")
        self.assertLacks(ok, "LINK")
        self.assertLacks(ok, "SELF-REF")

    def test_missing_core_keys(self):
        self.write("wiki/sources/a.md",
                   page(drop=("title", "date", "created", "updated", "status")))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        got = {c for _, c in self.ids(proc)}
        self.assertLessEqual({"FM-TITLE", "FM-DATE", "FM-CREATED",
                              "FM-UPDATED", "FM-STATUS"}, got)

    def test_date_without_time_is_error(self):
        self.write("wiki/sources/a.md", page(date="2026-09-04"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-DATE")

    def test_tags_head_must_be_date_tag(self):
        self.write("wiki/sources/a.md", page(tags=("source", "2026/09/04")))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-TAG")

    def test_missing_tags_block(self):
        self.write("wiki/sources/a.md", page(drop=("tags",)))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-TAG")

    def test_date_option_requires_matching_tag(self):
        self.write("wiki/sources/a.md", page(tags=("2026/09/03", "source")))
        proc = self.run_verify("--pages", "wiki/sources/a.md", "--date", "2026-09-04")
        self.assertHas(proc, "ERROR", "FM-TAGDATE")
        ok = self.run_verify("--pages", "wiki/sources/a.md", "--date", "2026-09-03")
        self.assertLacks(ok, "FM-TAGDATE")

    def test_book_source_needs_publish_false(self):
        self.write("wiki/sources/a.md", page(drop=("publish",)))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-PUBLISH")

    def test_thesis_source_should_not_carry_publish_false(self):
        self.write("wiki/sources/a.md", page(source_type="thesis"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "WARN", "FM-PUBLISH")
        self.assertEqual(proc.returncode, 0)

    def test_thesis_without_publish_is_clean(self):
        self.write("wiki/sources/a.md",
                   page(source_type="thesis", drop=("publish",)))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertLacks(proc, "FM-PUBLISH")

    def test_source_needs_source_type(self):
        self.write("wiki/sources/a.md",
                   page(drop=("source_type", "publish")))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-STYPE")

    def test_entity_needs_entity_type(self):
        self.write("wiki/entities/Foo.md",
                   page(type="entity", title='"Foo"', h1="Foo",
                        tags=("2026/09/04", "entity"),
                        drop=("source_type", "publish")))
        proc = self.run_verify("--pages", "wiki/entities/Foo.md")
        self.assertHas(proc, "ERROR", "FM-ETYPE")

    def test_concept_needs_complexity_and_domain(self):
        self.write("wiki/concepts/概念.md",
                   page(type="concept", title='"概念"', h1="概念",
                        tags=("2026/09/04", "concept"),
                        drop=("source_type", "publish")))
        proc = self.run_verify("--pages", "wiki/concepts/概念.md")
        self.assertHas(proc, "ERROR", "FM-CFIELD")

    def test_broken_frontmatter(self):
        self.write("wiki/sources/a.md", "# 見出しだけ\n\n本文。\n")
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "FM-PARSE")

    # ------------------------------------------------------------ H1

    def test_missing_h1(self):
        self.write("wiki/sources/a.md", page(h1=None))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "H1-COUNT")

    def test_two_h1(self):
        self.write("wiki/sources/a.md", page(body="# もう 1 つ\n\n本文。"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "H1-COUNT")

    def test_h1_in_code_fence_is_ignored(self):
        self.write("wiki/sources/a.md",
                   page(body="```markdown\n# 雛形の見出し\n```\n\n本文。"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertLacks(proc, "H1-COUNT")

    def test_h1_title_mismatch_is_warning(self):
        self.write("wiki/sources/a.md", page(h1="ちがう見出し"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "WARN", "H1-TITLE")
        self.assertEqual(proc.returncode, 0)

    # -------------------------------------------------- wikilink 解決

    def test_link_resolution_forms(self):
        self.write("wiki/entities/Target Page.md", "本文\n")
        self.write("papers/2020__X__論文.md", "本文\n")
        body = "\n".join([
            "- [[Target Page]]",
            "- [[target page|別名]]",
            "- [[Target Page#節見出し]]",
            "- [[Target Page#^blockid]]",
            "- [[2020__X__論文]]",
            "- [[papers/2020__X__論文|パス修飾]]",
        ])
        self.write("wiki/sources/a.md", page(body=body))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertLacks(proc, "LINK")
        self.assertEqual(self.summary(proc)["unresolved_links"], "0")

    def test_unresolved_link_is_error(self):
        self.write("wiki/sources/a.md", page(body="- [[存在しないページ]]"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "LINK")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.summary(proc)["unresolved_links"], "1")

    def test_frontmatter_wikilink_is_checked(self):
        self.write("wiki/sources/a.md",
                   page(related='\n  - "[[居ないハブ]]"'))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "LINK")

    def test_link_in_code_fence_is_ignored(self):
        self.write("wiki/sources/a.md",
                   page(body="```\n[[雛形のリンク]]\n```\n\n`[[行内コード]]`"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertLacks(proc, "LINK")

    def test_navigation_line_gets_its_own_id(self):
        self.write("wiki/sources/@2026__Pub__本 - Chapter 2 次.md", page())
        body = "> 前: [[@2026__Pub__本 - Chapter 2 つぎ]] | 次: [[@2026__Pub__本 - Chapter 2 次]]"
        self.write("wiki/sources/a.md", page(body=body))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "NAV-LINK")
        self.assertLacks(proc, "LINK")

    def test_table_escaped_alias_resolves(self):
        """表の中の `[[Target\\|alias]]` は alias 区切りであってパスではない。"""
        self.write("wiki/entities/Target Page.md", "本文\n")
        (self.vault / "wiki/sources/_attachments/bookslug/f.png").write_bytes(b"x")
        body = "\n".join([
            "| 列 | 値 |",
            "| --- | --- |",
            "| a | [[Target Page\\|別名]] |",
            "",
            "![[_attachments/bookslug/f.png\\|300]]",
        ])
        self.write("wiki/sources/a.md", page(body=body))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertLacks(proc, "LINK")
        self.assertLacks(proc, "EMBED")
        self.assertEqual(self.summary(proc)["unresolved_embeds"], "0")

    def test_title_wikilink_is_not_link_checked(self):
        """frontmatter で解決を見るのは related / sources / asks / brief。title は見ない。"""
        self.write("wiki/sources/a.md", page(title='"[[居ないページ]] の話"'))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertLacks(proc, "LINK")

    def test_sources_wikilink_is_link_checked(self):
        self.write("wiki/sources/a.md", page(sources='\n  - "[[居ない原本]]"'))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "LINK")

    def test_extensionless_raw_path_resolves(self):
        """`[[.raw/…/ch-01]]` は `.txt` `.pdf` を補って実パスで解決する。"""
        self.write(".raw/books/slug/chapters/ch-01.txt", "原本\n")
        self.write(".raw/books/slug/slug.pdf", "%PDF\n")
        self.write("wiki/sources/a.md", page(body="\n".join([
            "- [[.raw/books/slug/chapters/ch-01]]",
            "- [[.raw/books/slug/slug]]",
            "- [[.raw/books/slug/chapters/ch-99]]",
        ])))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertEqual(self.summary(proc)["unresolved_links"], "1")

    def test_self_reference_is_warning(self):
        self.write("wiki/sources/自分.md",
                   page(title='"自分"', h1="自分", body="- [[自分]] を参照"))
        proc = self.run_verify("--pages", "wiki/sources/自分.md")
        self.assertHas(proc, "WARN", "SELF-REF")
        self.assertEqual(proc.returncode, 0)

    # ----------------------------------------------- 埋め込みと attachment

    def test_embed_resolution_and_count(self):
        (self.vault / "wiki/sources/_attachments/bookslug/ch01-fig1.png").write_bytes(b"x")
        (self.vault / "wiki/sources/_attachments/bookslug/ch01-fig2.png").write_bytes(b"x")
        body = "\n".join([
            "![[_attachments/bookslug/ch01-fig1.png]]",
            "![[ch01-fig2.png]]",
        ])
        self.write("wiki/sources/a.md", page(body=body))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--attachments-slug", "bookslug")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(self.summary(proc)["embeds"], "2")
        self.assertEqual(self.summary(proc)["unused_attachments"], "0")

    def test_unresolved_embed_is_error(self):
        self.write("wiki/sources/a.md",
                   page(body="![[_attachments/bookslug/居ない.png]]"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "EMBED")
        self.assertEqual(proc.returncode, 1)

    def test_unused_attachment_is_warning(self):
        (self.vault / "wiki/sources/_attachments/bookslug/使う.png").write_bytes(b"x")
        (self.vault / "wiki/sources/_attachments/bookslug/余り.png").write_bytes(b"x")
        self.write("wiki/sources/a.md",
                   page(body="![[_attachments/bookslug/使う.png]]"))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--attachments-slug", "bookslug")
        self.assertHas(proc, "WARN", "ATTACH-UNUSED")
        self.assertEqual(self.summary(proc)["unused_attachments"], "1")
        self.assertEqual(proc.returncode, 0)

    def test_markdown_image_form(self):
        (self.vault / "wiki/sources/_attachments/bookslug/図 1.png").write_bytes(b"x")
        self.write("wiki/sources/a.md", page(body="\n".join([
            "![図](_attachments/bookslug/%E5%9B%B3%201.png)",
            "![外部](https://example.invalid/x.png)",
            "![欠落](_attachments/bookslug/no.png)",
        ])))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--attachments-slug", "bookslug")
        self.assertHas(proc, "ERROR", "EMBED")
        self.assertEqual(self.summary(proc)["embeds"], "2")
        self.assertEqual(self.summary(proc)["unused_attachments"], "0")

    def test_missing_attachment_dir(self):
        self.write("wiki/sources/a.md", page())
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--attachments-slug", "居ない")
        self.assertHas(proc, "ERROR", "ATTACH-DIR")

    # -------------------------------------------------------- 分量と節

    def test_length_warnings(self):
        self.write("wiki/sources/short.md", page(body="短い。"))
        short = self.run_verify("--pages", "wiki/sources/short.md")
        self.assertHas(short, "WARN", "LEN-MIN")

        self.write("wiki/sources/long.md",
                   page(address="c-000002", body="行。\n" * 320))
        long_ = self.run_verify("--pages", "wiki/sources/long.md")
        self.assertHas(long_, "WARN", "LEN-MAX")
        self.assertEqual(long_.returncode, 0)

    def test_book_source_floor_is_thirty_lines(self):
        """書籍の章は短くてよい(30〜50 行)。paper / thesis は 100 行のまま。"""
        body = "行。\n" * 40
        self.write("wiki/sources/book.md", page(body=body))
        ok = self.run_verify("--pages", "wiki/sources/book.md")
        self.assertLacks(ok, "LEN-MIN")

        self.write("wiki/sources/paper.md",
                   page(address="c-000002", source_type="paper",
                        drop=("publish",), body=body))
        warned = self.run_verify("--pages", "wiki/sources/paper.md")
        self.assertHas(warned, "WARN", "LEN-MIN")

    def test_min_lines_option_overrides_floor(self):
        self.write("wiki/sources/book.md", page(body="行。\n" * 40))
        warned = self.run_verify("--pages", "wiki/sources/book.md",
                                 "--min-lines", "200")
        self.assertHas(warned, "WARN", "LEN-MIN")
        ok = self.run_verify("--pages", "wiki/sources/book.md", "--min-lines", "0")
        self.assertLacks(ok, "LEN-MIN")

    def test_source_verification_section_is_forbidden(self):
        self.write("wiki/sources/a.md",
                   page(body="## 検証パス\n\n- 全主張を照合した。"))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "SEC-VERIFY")

    def test_concept_generic_section_names(self):
        concept = dict(type="concept", title='"概念"', h1="概念",
                       complexity="intermediate", domain='"distributed"',
                       tags=("2026/09/04", "concept"),
                       drop=("source_type", "publish"))
        self.write("wiki/concepts/概念.md",
                   page(body="## 横断的知見\n\n- 旧名は許す。\n\n## 未編纂の観察\n\n- 受信箱。",
                        **concept))
        ok = self.run_verify("--pages", "wiki/concepts/概念.md")
        self.assertLacks(ok, "SEC-NAME")

        self.write("wiki/concepts/概念2.md",
                   page(body="## まとめ\n\n- 汎用の受け皿。", **dict(concept, title='"概念2"', h1="概念2")))
        bad = self.run_verify("--pages", "wiki/concepts/概念2.md")
        self.assertHas(bad, "ERROR", "SEC-NAME")

    def test_concept_section_name_matrix(self):
        """禁止語を含むだけの主題名(`## 観察可能性`)を落とさない。"""
        concept = dict(type="concept", complexity="intermediate",
                       domain='"distributed"', tags=("2026/09/04", "concept"),
                       drop=("source_type", "publish"))
        cases = [
            ("## 観察可能性", False), ("## まとめ買い", False),
            ("## 観察可能性の設計", False), ("## 横断的知見", False),
            ("### 知見", False),
            ("## 補足事項", False),
            ("## 知見", True), ("## 考察", True), ("## まとめ", True),
            ("## 知見と考察", True), ("## 横断的観察", True),
            ("## 知見(まとめ)", True), ("## 知見(考察)", True),
            ("## 知見: まとめ", True),
        ]
        for index, (heading, is_error) in enumerate(cases):
            with self.subTest(heading=heading):
                name = "概念%02d" % index
                self.write("wiki/concepts/%s.md" % name,
                           page(address="c-%06d" % (100 + index),
                                title='"%s"' % name, h1=name,
                                body="%s\n\n- 本文。" % heading, **concept))
                proc = self.run_verify("--pages", "wiki/concepts/%s.md" % name)
                if is_error:
                    self.assertHas(proc, "ERROR", "SEC-NAME")
                else:
                    self.assertLacks(proc, "SEC-NAME")

    # ---------------------------------------------------------- 重複 address

    def test_duplicate_address(self):
        self.write("wiki/sources/a.md", page(title='"a"', h1="a"))
        self.write("wiki/entities/b.md",
                   page(type="entity", entity_type="book", title='"b"', h1="b",
                        tags=("2026/09/04", "entity"),
                        drop=("source_type", "publish")))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertHas(proc, "ERROR", "ADDR-DUP")

    def test_duplicate_address_with_quotes(self):
        """引用符の有無で表記が揺れても同じ address として突き合わせる。"""
        for kind, (mine, theirs) in {
            "both": ('"c-000001"', '"c-000001"'),
            "mixed": ("c-000001", '"c-000001"'),
            "mixed-rev": ('"c-000001"', "c-000001"),
        }.items():
            with self.subTest(kind=kind):
                self.write("wiki/sources/a.md",
                           page(address=mine, title='"a"', h1="a"))
                self.write("wiki/entities/b.md",
                           page(address=theirs, type="entity",
                                entity_type="book", title='"b"', h1="b",
                                tags=("2026/09/04", "entity"),
                                drop=("source_type", "publish")))
                proc = self.run_verify("--pages", "wiki/sources/a.md")
                self.assertHas(proc, "ERROR", "ADDR-DUP")

    def test_unique_addresses_are_clean(self):
        self.write("wiki/sources/a.md",
                   page(address='"c-000001"', title='"a"', h1="a",
                        body="本文。\n" * 40))
        self.write("wiki/entities/b.md",
                   page(address="c-000002", type="entity", entity_type="book",
                        title='"b"', h1="b", tags=("2026/09/04", "entity"),
                        drop=("source_type", "publish")))
        proc = self.run_verify("--pages", "wiki/sources/a.md")
        self.assertLacks(proc, "ADDR-DUP")

    # -------------------------------------------------- related と図表 ID

    def test_require_related(self):
        self.write("wiki/entities/書名.md", "本文\n")
        self.write("wiki/sources/a.md",
                   page(related='\n  - "[[書名]]"', body="本文。\n" * 40))
        ok = self.run_verify("--pages", "wiki/sources/a.md",
                             "--require-related", "[[書名]]")
        self.assertEqual(ok.returncode, 0, ok.stdout)

        self.write("wiki/sources/b.md",
                   page(address="c-000002", title='"b"', h1="b",
                        body="本文。\n" * 40))
        bad = self.run_verify("--pages", "wiki/sources/b.md",
                              "--require-related", "[[書名]]")
        self.assertHas(bad, "ERROR", "FM-RELATED")

    def test_require_related_accepts_path_and_alias(self):
        self.write("wiki/entities/書名.md", "本文\n")
        self.write("wiki/sources/a.md",
                   page(related='\n  - "[[wiki/entities/書名|書名]]"',
                        body="本文。\n" * 40))
        ok = self.run_verify("--pages", "wiki/sources/a.md",
                             "--require-related", "書名")
        self.assertLacks(ok, "FM-RELATED")

    def test_figure_ids_from_raw(self):
        raw = self.vault / "raw.txt"
        raw.write_text("Figure 1 は…。Fig. 2a と Table 3 を参照。図 4-1 も。\n",
                       encoding="utf-8")
        self.write("wiki/sources/a.md", page(body="\n".join([
            "Figure 1 の説明。", "Figure 2a の説明。", "図 4-1 の説明。",
        ])))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--figure-ids-from", str(raw))
        self.assertHas(proc, "WARN", "FIG-MISSING")
        self.assertIn("Table 3", proc.stdout)
        self.assertEqual(self.summary(proc)["figure_ids"], "4")
        self.assertEqual(self.summary(proc)["figure_ids_missing"], "1")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_require_related_inline_array(self):
        """`related: ["[[A]]", "[[B]]"]` を 1 スカラーとして誤判定しない。"""
        self.write("wiki/entities/書名.md", "本文\n")
        self.write("wiki/entities/著者.md", "本文\n")
        self.write("wiki/sources/a.md",
                   page(related='["[[著者]]", "[[書名]]"]', body="本文。\n" * 40))
        ok = self.run_verify("--pages", "wiki/sources/a.md",
                             "--require-related", "[[書名]]")
        self.assertEqual(ok.returncode, 0, ok.stdout)

        bad = self.run_verify("--pages", "wiki/sources/a.md",
                              "--require-related", "[[別のハブ]]")
        self.assertHas(bad, "ERROR", "FM-RELATED")

    def test_figure_ids_skip_references_section(self):
        """参考文献部の題名に混ざる `Figure N` を拾わない。"""
        raw = self.vault / "raw.txt"
        raw.write_text("\n".join([
            "本文は Figure 1 を参照する。",
            "References",
            "[1] Someone. Figure 9 as a metaphor. 2020.",
        ]) + "\n", encoding="utf-8")
        self.write("wiki/sources/a.md", page(body="Figure 1 の説明。\n" * 40))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--figure-ids-from", str(raw))
        self.assertLacks(proc, "FIG-MISSING")
        self.assertEqual(self.summary(proc)["figure_ids"], "1")

    def test_figure_ids_expand_enumerations_and_ranges(self):
        raw = self.vault / "raw.txt"
        raw.write_text("Figures 2 and 3。Figs. 5, 6 and 8。Tables 1 and 2。"
                       "Figures 10-12。Figs. 20–21。図 4-1。\n",
                       encoding="utf-8")
        self.write("wiki/sources/a.md", page(body="Figure 2 だけ載せた。\n" * 40))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--figure-ids-from", str(raw))
        missing = [line for line in proc.stdout.splitlines()
                   if "FIG-MISSING" in line]
        got = {line.rsplit(": ", 1)[1] for line in missing}
        self.assertEqual(got, {
            "Figure 3", "Figure 5", "Figure 6", "Figure 8",
            "Figure 10", "Figure 11", "Figure 12", "Figure 20", "Figure 21",
            "Table 1", "Table 2", "図 4-1",
        })
        self.assertEqual(self.summary(proc)["figure_ids"], "13")

    def test_figure_ids_complete(self):
        raw = self.vault / "raw.txt"
        raw.write_text("Figure 1 と Table 2。\n", encoding="utf-8")
        self.write("wiki/sources/a.md",
                   page(body="Figure 1 と Table 2 を載せた。\n" * 40))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--figure-ids-from", str(raw))
        self.assertLacks(proc, "FIG-MISSING")
        self.assertEqual(self.summary(proc)["figure_ids_missing"], "0")

    def test_figure_ids_unreadable_raw_is_error(self):
        self.write("wiki/sources/a.md", page(body="本文。\n" * 40))
        proc = self.run_verify("--pages", "wiki/sources/a.md",
                               "--figure-ids-from", str(self.vault / "居ない.txt"))
        self.assertHas(proc, "ERROR", "FIG-RAW")
        self.assertEqual(proc.returncode, 1)

    def test_list_figure_ids_counts_references_and_expansion(self):
        """References 切り・並列展開・回数。対象ページは不要で終了 0。"""
        raw = self.vault / "raw.txt"
        raw.write_text("\n".join([
            "Figure 1 を参照する。Figure 1 をもう一度。",
            "Figures 2 and 3。",
            "References",
            "[1] Someone. Figure 9 as a metaphor. 2020.",
        ]) + "\n", encoding="utf-8")
        proc = self.run_verify("--list-figure-ids", str(raw))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Figure 1\t2", proc.stdout)
        self.assertIn("Figure 2\t1", proc.stdout)
        self.assertIn("Figure 3\t1", proc.stdout)
        self.assertNotIn("Figure 9", proc.stdout)
        self.assertEqual(self.summary(proc)["figure_ids"], "3")
        self.assertEqual(self.summary(proc)["captions_like"], "2")

    def test_list_figure_ids_json(self):
        raw = self.vault / "raw.txt"
        raw.write_text("Figure 1 と Figures 2 and 3。\n", encoding="utf-8")
        proc = self.run_verify("--list-figure-ids", str(raw), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["summary"]["figure_ids"], 3)
        self.assertEqual(payload["counts"]["Figure 1"], 1)
        self.assertEqual(payload["counts"]["Figure 2"], 1)
        self.assertEqual(payload["counts"]["Figure 3"], 1)

    def test_list_figure_ids_missing_raw_is_usage_error(self):
        proc = self.run_verify("--list-figure-ids", str(self.vault / "居ない.txt"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("読めない", proc.stderr)

    # ------------------------------------------------------- 対象集合の解決

    def test_match_classifies_by_body(self):
        self.require_git()
        self.write("wiki/sources/mine.md", page(body="本書『対象の書名』の章。\n" * 40))
        self.write("wiki/sources/other.md", page(body="別セッションの論文。\n" * 40))
        proc = self.run_verify("--match", "対象の書名", "--classify")
        self.assertEqual(self.summary(proc)["pages"], "1")
        self.assertIn("wiki/sources/other.md", proc.stdout)
        self.assertHas(proc, "INFO", "CLASSIFY-OTHER")

    def test_renamed_page_is_still_classified(self):
        """`git mv` した章ページを分類から取りこぼさない。"""
        self.require_git()
        self.write("wiki/sources/古い章.md", page(body="対象の書名。\n" * 40))
        self.write("wiki/sources/他人.md",
                   page(address="c-000002", title='"他人"', h1="他人",
                        body="別セッション。\n" * 40))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        self.git("mv", "wiki/sources/古い章.md", "wiki/sources/新しい章.md")
        self.git("mv", "wiki/sources/他人.md", "wiki/sources/他人 改.md")

        proc = self.run_verify("--match", "対象の書名", "--classify")
        self.assertEqual(self.summary(proc)["pages"], "1")
        self.assertIn("wiki/sources/他人 改.md", proc.stdout)
        self.assertEqual(self.summary(proc)["other_changed"], "1")

        payload = json.loads(self.run_verify(
            "--match", "対象の書名", "--classify", "--json").stdout)
        self.assertEqual(payload["targets"], ["wiki/sources/新しい章.md"])

    def test_classify_quiet_keeps_other_count(self):
        """`--quiet` でも他セッション分の件数は summary に残す。"""
        self.require_git()
        self.write("wiki/sources/mine.md", page(body="対象の書名。\n" * 40))
        self.write("wiki/sources/other.md",
                   page(address="c-000002", title='"other"', h1="other",
                        body="別セッション。\n" * 40))
        proc = self.run_verify("--match", "対象の書名", "--classify", "--quiet")
        self.assertNotIn("CLASSIFY-OTHER", proc.stdout)
        self.assertEqual(self.summary(proc)["other_changed"], "1")

    def test_from_git_status_takes_all_wiki_pages(self):
        self.require_git()
        self.write("wiki/sources/mine.md", page(body="A\n" * 120))
        self.write("wiki/sources/other.md", page(body="B\n" * 120))
        proc = self.run_verify("--from-git-status")
        self.assertEqual(self.summary(proc)["pages"], "2")

    def test_catalog_files_are_not_verified_as_pages(self):
        self.require_git()
        self.write("wiki/index.md", "# index\n\n対象の書名\n")
        self.write("wiki/sources/mine.md", page(body="対象の書名。\n" * 120))
        proc = self.run_verify("--match", "対象の書名", "--classify")
        self.assertEqual(self.summary(proc)["pages"], "1")

    def test_glob_and_japanese_at_filenames(self):
        name = "@2013__KindaiKagaku__書名 第3版 - Chapter 1 序 章.md"
        self.write("wiki/sources/" + name, page(body="本文。\n" * 120))
        proc = self.run_verify("--glob", "wiki/sources/@2013__KindaiKagaku__書名*")
        self.assertEqual(self.summary(proc)["pages"], "1")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_expect_count(self):
        self.write("wiki/sources/a.md", page(body="本文。\n" * 120))
        bad = self.run_verify("--pages", "wiki/sources/a.md", "--expect-count", "2")
        self.assertHas(bad, "ERROR", "COUNT")
        ok = self.run_verify("--pages", "wiki/sources/a.md", "--expect-count", "1")
        self.assertEqual(ok.returncode, 0, ok.stdout)

    # --------------------------------------------------------- staged 照合

    def test_staged_check(self):
        self.require_git()
        self.write("wiki/sources/a.md", page(body="本文。\n" * 120))
        self.write("wiki/index.md", "# index\n")
        self.write("wiki/sources/_attachments/bookslug/x.png", "x")
        self.git("add", "wiki/sources/a.md", "wiki/index.md",
                 "wiki/sources/_attachments/bookslug/x.png")

        ok = self.run_verify("--pages", "wiki/sources/a.md", "--staged-check",
                             "--extra-files", "wiki/index.md",
                             "wiki/sources/_attachments/bookslug/")
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertEqual(self.summary(ok)["staged_extra"], "0")
        self.assertEqual(self.summary(ok)["staged_missing"], "0")

        extra = self.run_verify("--pages", "wiki/sources/a.md", "--staged-check")
        self.assertHas(extra, "ERROR", "STAGED-EXTRA")

    def test_staged_check_reports_missing(self):
        self.require_git()
        self.write("wiki/sources/a.md", page(body="本文。\n" * 120))
        self.write("wiki/sources/b.md", page(title='"b"', h1="b", body="本文。\n" * 120))
        self.git("add", "wiki/sources/a.md")
        proc = self.run_verify("--pages", "wiki/sources/a.md", "wiki/sources/b.md",
                               "--staged-check")
        self.assertHas(proc, "ERROR", "STAGED-MISS")
        self.assertEqual(self.summary(proc)["staged_missing"], "1")

    def test_staged_check_with_expect_files(self):
        self.require_git()
        self.write("wiki/sources/a.md", page(body="本文。\n" * 120))
        self.git("add", "wiki/sources/a.md")
        listing = self.vault / "expected.txt"
        listing.write_text("wiki/sources/a.md\n", encoding="utf-8")
        proc = self.run_verify("--pages", "wiki/sources/a.md", "--staged-check",
                               "--expect-files", str(listing))
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_staged_check_runs_without_target_selector(self):
        """コミット直前の照合だけを単独で回せる。"""
        self.require_git()
        self.write("wiki/sources/a.md", page(body="本文。\n" * 120))
        self.git("add", "wiki/sources/a.md")
        listing = self.vault / "expected.txt"
        listing.write_text("wiki/sources/a.md\n", encoding="utf-8")
        ok = self.run_verify("--staged-check", "--expect-files", str(listing))
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertEqual(self.summary(ok)["pages"], "0")

        listing.write_text("wiki/sources/b.md\n", encoding="utf-8")
        bad = self.run_verify("--staged-check", "--expect-files", str(listing))
        self.assertHas(bad, "ERROR", "STAGED-EXTRA")
        self.assertHas(bad, "ERROR", "STAGED-MISS")

    # ------------------------------------------------------------ カタログ

    def test_classify_reports_catalog_numstat(self):
        self.require_git()
        self.write("wiki/index.md", "# index\n")
        self.write("wiki/concepts/_index.md", "# concepts\n")
        self.write("wiki/sources/a.md", page(body="書名。\n" * 120))
        self.git("add", "wiki/index.md", "wiki/concepts/_index.md")
        self.git("commit", "-q", "-m", "seed")
        with open(self.vault / "wiki/index.md", "a", encoding="utf-8") as fh:
            fh.write("- 追記 1\n- 追記 2\n- 追記 3\n")

        proc = self.run_verify("--match", "書名", "--classify")
        self.assertEqual(self.summary(proc)["catalog_lines"], "3")
        self.assertHas(proc, "INFO", "CATALOG")

        warned = self.run_verify("--match", "書名", "--classify",
                                 "--expect-catalog-lines", "2")
        self.assertHas(warned, "WARN", "CATALOG")

    # ---------------------------------------------------- 出力の形と打ち切り

    def test_json_keys(self):
        self.write("wiki/sources/a.md", page(body="- [[居ない]]"))
        proc = self.run_verify("--pages", "wiki/sources/a.md", "--json")
        payload = json.loads(proc.stdout)
        self.assertEqual(set(payload), {"targets", "findings", "summary", "truncated"})
        self.assertEqual(payload["targets"], ["wiki/sources/a.md"])
        self.assertLessEqual({"severity", "id", "path", "line", "message"},
                             set(payload["findings"][0]))
        self.assertLessEqual({"pages", "errors", "warnings", "embeds",
                              "unresolved_links", "unused_attachments"},
                             set(payload["summary"]))

    def test_quiet_hides_warnings(self):
        self.write("wiki/sources/a.md",
                   page(h1="ちがう見出し", body="- [[居ない]]\n" + "行。\n" * 120))
        proc = self.run_verify("--pages", "wiki/sources/a.md", "--quiet")
        self.assertLacks(proc, "H1-TITLE")
        self.assertHas(proc, "ERROR", "LINK")
        # 隠しても集計には残る。
        self.assertEqual(self.summary(proc)["warnings"], "1")

    def test_max_findings_truncates(self):
        body = "\n".join("- [[居ない%d]]" % i for i in range(10))
        self.write("wiki/sources/a.md", page(body=body))
        proc = self.run_verify("--pages", "wiki/sources/a.md", "--max-findings", "3")
        self.assertEqual(len(self.ids(proc)), 3)
        self.assertEqual(self.summary(proc)["truncated"], "8")

    def test_errors_survive_truncation(self):
        body = "- [[居ない]]\n" + "\n".join("- [[自分]]" for _ in range(20))
        self.write("wiki/sources/自分.md",
                   page(title='"自分"', h1="自分", body=body))
        proc = self.run_verify("--pages", "wiki/sources/自分.md", "--max-findings", "2")
        self.assertHas(proc, "ERROR", "LINK")


if __name__ == "__main__":
    unittest.main(verbosity=1)
