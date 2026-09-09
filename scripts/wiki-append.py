#!/usr/bin/env python3
"""wiki-append.py — 既存 wiki ページへの ingest 由来の追記を 1 呼び出しに束ねる。

concept ページ 1 枚を更新するのに、従来は「未編纂の観察に追記・未解決の問いに
追記・関連行にリンク追加・出典に追記・frontmatter の updated と日付タグ更新」で
4〜6 回の Edit を費やしていた。入力トークンの大半はキャッシュ読み戻し
(呼び出し時点の常駐文脈長の総和)なので、ターン数の削減がそのまま
トークン削減になる。本スクリプトは 1 ページ分の追記をまとめて適用し、
`--batch` で複数ページを 1 ターンにまとめる。

**主題節(自由見出し)は絶対に書き換えない**(conventions §8 更新ルール 2:
ingest は主題節を直接書き換えない)。書き込み先は固定名の節だけである。

ロックは `wiki_lock.acquire()` 経由で `scripts/wiki-lock.sh` と同じ
ロックファイルを使うので、`wiki-lock.sh` を直接使う他のエージェントとも
排他が成立する。

使い方(単一ページ):
  python3 scripts/wiki-append.py --page wiki/concepts/Foo.md \\
      --observation "[節名] 観察文(Source: [[@X]])" \\
      --question "残った問い" \\
      --related-source "[[@X]]" --related-concept "[[Bar]]" \\
      --source-entry "[[@X]](何を根拠にしたか)" \\
      --frontmatter-source "[[@X]]"

使い方(複数ページ): `--batch SPEC.json`。SPEC は操作のリストで、
1 操作のスキーマは全キー任意(`page` のみ必須)。

  [{"page": "wiki/concepts/テンソル並列.md",
    "date": "2026-09-04",
    "observations": ["[節名] 観察文(Source: [[@X]])"],
    "questions_add": ["問い"],
    "questions_remove_containing": ["解決済みの問いの一部文字列"],
    "related": {"ソース": ["[[@X]]"], "概念": ["[[Y]]"], "エンティティ": ["[[Z]]"]},
    "sources_section": [{"link": "[[@X]]", "note": "(何を根拠にしたか)"}],
    "frontmatter_sources": ["[[@X]]"],
    "section_append": [{"section": "関連ソース", "lines": ["- [[@X]] — 要旨"]}]}]

冪等性: 同一文字列の箇条書きが節内に既にあれば追加せず `skipped_duplicate` に
数える。同じ操作を 2 回実行しても 2 回目は無変更で終わる(`updated` も触らない)。

環境変数 `WIKI_VAULT_ROOT` で vault ルートを差し替えられる(試験用)。

終了コード:
  0  — 全ページ成功
  2  — 使い方の誤り
  65 — ページ不在・frontmatter 不在・節不在などのデータエラー
  75 — ロック取得失敗を含む(EX_TEMPFAIL 相当。他ページの処理は続行する)
"""

import argparse
import datetime
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import wiki_lock

VAULT_ROOT = Path(
    os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent
).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DATA = 65
EXIT_LOCKED = 75

INBOX = "未編纂の観察"
LEGACY_INBOX = "横断的知見"
QUESTIONS = "未解決の問い"
RELATED = "関連"
SOURCES = "出典"
RELATED_LABELS = ("ソース", "概念", "エンティティ")
LINE_SOFT_LIMIT = 300

# `--lock-wait-sec 0` を素のまま wiki_lock に渡すと 1 回も試行せず必ず失敗する。
# 0 は「1 回だけ試す」の意味に読み替え、この下限に丸める。
MIN_LOCK_WAIT_SEC = 0.1

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
BULLET_RE = re.compile(r"^[-*]\s")
EMPTY_BULLET_RE = re.compile(r"^[-*]\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_TAG_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

CATALOG_FILES = ("wiki/index.md", "wiki/hot.md", "wiki/log.md")


def log(msg):
    print(msg, file=sys.stderr)


def normalize_rel(raw):
    if raw is None or not str(raw).strip():
        return None, "ページのパスが空である"
    text = str(raw).strip()
    if text.startswith("/"):
        try:
            rel = Path(text).resolve().relative_to(VAULT_ROOT).as_posix()
        except ValueError:
            return None, "パスが vault の外を指している: %s" % text
    else:
        rel = text
    if ".." in Path(rel).parts:
        return None, "パスに '..' を含められない: %s" % text
    rel = Path(rel).as_posix()
    if not rel.endswith(".md"):
        return None, "wiki ページは .md でなければならない: %s" % text
    if Path(rel).parts[:1] != ("wiki",):
        return None, "wiki/ 配下のパスだけを受け付ける: %s" % text
    if rel in CATALOG_FILES or Path(rel).name == "_index.md":
        return None, "%s はカタログである。wiki-catalog.py で更新する" % rel
    # シンボリックリンクを辿った先が wiki/ の外なら、ロック層で 75 になる前に
    # パスエラーとして落とす(wiki-lock.sh の validate_path と同じ判定)。
    real = Path(os.path.realpath(str(VAULT_ROOT / rel)))
    wiki_real = Path(os.path.realpath(str(VAULT_ROOT / "wiki")))
    try:
        real.relative_to(wiki_real)
    except ValueError:
        return None, "シンボリックリンクを解決すると wiki/ の外を指す: %s" % text
    return rel, None


def link_targets(text):
    """文字列中の wikilink 対象名を正規化して並べる(別名・アンカーは落とす)。"""
    out = []
    for match in WIKILINK_RE.finditer(text or ""):
        target = match.group(1).split("|")[0].split("#")[0].strip()
        if target:
            out.append(target)
    return out


def bullet_text(line):
    """箇条書き行の内容部分。比較用に前後の空白を落とす。"""
    return BULLET_RE.sub("", line, count=1).strip()


def normalize_item(text):
    """追記候補の文字列を比較可能な形にする(先頭の `- ` は許容して落とす)。"""
    stripped = str(text).strip()
    if BULLET_RE.match(stripped):
        stripped = bullet_text(stripped)
    return re.sub(r"\s+", " ", stripped)


def read_verbatim(path):
    """改行を変換せずに読む(CRLF のページを LF に潰さないため)。"""
    with open(str(path), "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PageError(Exception):
    """そのページの適用を中止すべきデータエラー。"""


def split_lines_keep_eol(text):
    """テキストを (行の内容, その行の改行) の並びに分ける。

    `str.splitlines` は垂直タブや U+2028 なども行境界として扱うため使わない。
    ここで行境界と見るのは CRLF と LF だけで、他の文字は内容として残す。
    """
    out = []
    start = 0
    i = 0
    size = len(text)
    while i < size:
        if text[i] == "\n":
            out.append((text[start:i], "\n"))
            i += 1
            start = i
            continue
        if text[i] == "\r" and i + 1 < size and text[i + 1] == "\n":
            out.append((text[start:i], "\r\n"))
            i += 2
            start = i
            continue
        i += 1
    if start < size:
        out.append((text[start:], ""))
    return out


class Page(object):
    """行のリストとして wiki ページを保持し、固定名の節にだけ追記する。

    改行は行ごとに覚える。1 箇所でも CRLF があるからと全行を CRLF 化すると、
    触っていない行まで差分に出て、追記の影響範囲が読めなくなる。
    """

    def __init__(self, text):
        pairs = split_lines_keep_eol(text)
        self.lines = [pair[0] for pair in pairs]
        self.endings = [pair[1] for pair in pairs]
        counts = {}
        for eol in self.endings:
            if eol:
                counts[eol] = counts.get(eol, 0) + 1
        # 新しく挿入する行の改行は多数派に倣う(改行が無ければ LF)
        self.default_eol = "\n"
        if counts:
            self.default_eol = max(sorted(counts), key=lambda k: counts[k])
        self.fm_start = None
        self.fm_end = None
        if self.lines and self.lines[0].strip() == "---":
            for i in range(1, len(self.lines)):
                if self.lines[i].strip() == "---":
                    self.fm_start = 1
                    self.fm_end = i
                    break

    # ── 描画 ────────────────────────────────────────────────────────────
    def render(self, force_trailing_nl=False):
        out = []
        for i, line in enumerate(self.lines):
            out.append(line)
            out.append(self.endings[i])
        text = "".join(out)
        if force_trailing_nl and self.endings and self.endings[-1] == "":
            text += self.default_eol
        return text

    # ── 行の挿入と削除 ─────────────────────────────────────────────────
    def insert(self, at, new_lines):
        """`at` の位置へ行を挿入する。挿入行の改行は default_eol を使う。"""
        block = list(new_lines)
        if not block:
            return
        if at > 0 and self.endings[at - 1] == "":
            # 末尾改行が無いページの末尾に足す場合、まず改行を補う
            self.endings[at - 1] = self.default_eol
        self.lines[at:at] = block
        self.endings[at:at] = [self.default_eol] * len(block)

    def delete(self, start, end):
        del self.lines[start:end]
        del self.endings[start:end]

    @property
    def has_frontmatter(self):
        return self.fm_end is not None

    @property
    def body_start(self):
        return self.fm_end + 1 if self.has_frontmatter else 0

    # ── frontmatter ────────────────────────────────────────────────────
    def fm_range(self):
        return range(self.fm_start, self.fm_end)

    def fm_key_index(self, key):
        pattern = re.compile(r"^%s:\s*(.*)$" % re.escape(key))
        for i in self.fm_range():
            if pattern.match(self.lines[i]):
                return i
        return None

    def fm_item_indices(self, key_idx):
        """key 行の直後に続く `- item` 行の index を並べる。"""
        out = []
        for i in range(key_idx + 1, self.fm_end):
            if re.match(r"^\s+-\s+", self.lines[i]):
                out.append(i)
                continue
            if self.lines[i].strip() == "":
                continue
            break
        return out

    def fm_insert(self, at, new_lines):
        block = list(new_lines)
        if not block:
            return
        self.insert(at, block)
        self.fm_end += len(block)

    # ── 節 ──────────────────────────────────────────────────────────────
    def sections(self):
        """本文の `## ` 節を (見出し, 開始 index, 終了 index) で並べる。

        コードブロック(``` / ~~~)の内側の `## ` は見出しとして扱わない。
        `###` 以下は節の内側である。
        """
        heads = []
        fence = None
        for i in range(self.body_start, len(self.lines)):
            stripped = self.lines[i].strip()
            if fence is not None:
                if stripped.startswith(fence):
                    fence = None
                continue
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                continue
            if self.lines[i].startswith("## "):
                heads.append((self.lines[i][3:].strip(), i))
        out = []
        for n, (name, start) in enumerate(heads):
            end = heads[n + 1][1] if n + 1 < len(heads) else len(self.lines)
            out.append((name, start, end))
        return out

    def find_section(self, name, aliases=()):
        table = {n: (n, s, e) for n, s, e in self.sections()}
        for candidate in (name,) + tuple(aliases):
            if candidate in table:
                return table[candidate]
        return None

    def section_bullets(self, start, end):
        """節内の最上位箇条書きを (先頭 index, 終了 index) で並べる。"""
        items = []
        i = start + 1
        while i < end:
            if BULLET_RE.match(self.lines[i]):
                j = i + 1
                while j < end and self.lines[j].strip() != "" \
                        and self.lines[j][:1] in (" ", "\t"):
                    j += 1
                items.append((i, j))
                i = j
                continue
            i += 1
        return items

    def create_section(self, name, before):
        """`before` の直前(len(lines) なら末尾)に空の節を作り開始 index を返す。"""
        if before >= len(self.lines):
            block = ["## " + name, ""]
            if self.lines and self.lines[-1].strip() != "":
                block = [""] + block
            at = len(self.lines)
            self.insert(at, block)
            return at + (1 if block[0] == "" else 0)
        block = ["## " + name, "", ""]
        self.insert(before, block)
        return before

    def append_to_section(self, start, end, new_lines):
        """節の末尾(次の `## ` の直前。末尾の空行は保つ)に行を足す。"""
        content = [i for i in range(start + 1, end) if self.lines[i].strip() != ""]
        # 情報を持たない placeholder の `- ` だけの節は、その行を捨てて置き換える
        if len(content) == 1 and EMPTY_BULLET_RE.match(self.lines[content[0]]):
            self.delete(content[0], content[0] + 1)
            return self.append_to_section(start, end - 1, new_lines)

        block = list(new_lines)
        if not content:
            # 空の節。見出しの直後に空行を 1 行だけ置いてから内容を入れる
            insert_at = start + 1
            if insert_at < end and self.lines[insert_at].strip() == "":
                insert_at += 1
            else:
                block = [""] + block
        else:
            insert_at = content[-1] + 1
        # 次の `## ` 見出しとの間には空行を残す(最終節なら不要)
        if end < len(self.lines) \
                and not any(self.lines[i].strip() == "" for i in range(insert_at, end)):
            block = block + [""]
        self.insert(insert_at, block)
        return len(block)


class Result(object):
    def __init__(self, page):
        self.page = page
        self.added = {"observations": 0, "questions": 0, "related": 0,
                      "sources_section": 0, "frontmatter_sources": 0,
                      "section_append": 0}
        self.removed_questions = 0
        self.skipped_duplicate = 0
        self.warnings = []

    def payload(self, changed, lines):
        return {"page": self.page, "changed": changed, "added": self.added,
                "removed_questions": self.removed_questions,
                "skipped_duplicate": self.skipped_duplicate,
                "lines": lines, "warnings": self.warnings}


def inbox_section(page):
    """受信箱の節。旧名 `## 横断的知見` があればそちらを返す(conventions §8-8)。"""
    return page.find_section(INBOX, (LEGACY_INBOX,))


def created_section(page, name, result, aliases=()):
    """新設した節を読み直す。節として認識できなければ PageError。

    閉じていないコードフェンス(``` / ~~~)が本文にあると、それ以降の
    `## ` は節として読めない。ここで None を返すと呼び出し側が
    タプル展開で TypeError になり、バッチ全体が JSON を出さずに落ちる。
    """
    result.warnings.append("`## %s` を新設した" % name)
    found = page.find_section(name, aliases)
    if found is None:
        raise PageError(
            "`## %s` を新設したが節として認識できない。閉じていないコード"
            "フェンス(``` / ~~~)が本文にあると、それ以降の見出しが節として"
            "読めない(conventions §3)" % name)
    return found


def ensure_inbox(page, result):
    found = inbox_section(page)
    if found is not None:
        return found
    related = page.find_section(RELATED)
    before = related[1] if related is not None else len(page.lines)
    page.create_section(INBOX, before)
    return created_section(page, INBOX, result, (LEGACY_INBOX,))


def ensure_questions(page, result):
    found = page.find_section(QUESTIONS)
    if found is not None:
        return found
    anchor = inbox_section(page)
    if anchor is None:
        anchor = page.find_section(RELATED)
    before = anchor[1] if anchor is not None else len(page.lines)
    page.create_section(QUESTIONS, before)
    return created_section(page, QUESTIONS, result)


def ensure_related(page, result):
    found = page.find_section(RELATED)
    if found is not None:
        return found
    sources = page.find_section(SOURCES)
    before = sources[1] if sources is not None else len(page.lines)
    page.create_section(RELATED, before)
    return created_section(page, RELATED, result)


def ensure_sources(page, result):
    found = page.find_section(SOURCES)
    if found is not None:
        return found
    page.create_section(SOURCES, len(page.lines))
    return created_section(page, SOURCES, result)


def append_bullets(page, section, items, result, counter):
    """節の末尾に `- item` を追記する。既存と同文なら数えるだけで足さない。"""
    name, start, end = section
    existing = set()
    for i, j in page.section_bullets(start, end):
        existing.add(normalize_item(page.lines[i]))
    fresh = []
    for item in items:
        norm = normalize_item(item)
        if not norm:
            continue
        if norm in existing:
            result.skipped_duplicate += 1
            continue
        existing.add(norm)
        fresh.append("- " + norm)
    if not fresh:
        return False
    page.append_to_section(start, end, fresh)
    result.added[counter] += len(fresh)
    return True


def remove_questions(page, needles, result):
    section = page.find_section(QUESTIONS)
    if section is None:
        return False
    changed = False
    for needle in needles:
        text = str(needle).strip()
        if not text:
            continue
        while True:
            section = page.find_section(QUESTIONS)
            if section is None:
                break
            _, start, end = section
            hit = None
            for i, j in page.section_bullets(start, end):
                if text in "\n".join(page.lines[i:j]):
                    hit = (i, j)
                    break
            if hit is None:
                break
            page.delete(hit[0], hit[1])
            result.removed_questions += 1
            changed = True
    return changed


def apply_related(page, mapping, result):
    changed = False
    section = None
    for label in list(RELATED_LABELS) + [k for k in mapping if k not in RELATED_LABELS]:
        links = mapping.get(label) or []
        if isinstance(links, str):
            links = [links]
        links = [str(l).strip() for l in links if str(l).strip()]
        if not links:
            continue
        section = ensure_related(page, result)
        name, start, end = section
        pattern = re.compile(r"^[-*]\s+%s\s*:" % re.escape(label))
        target = None
        for i in range(start + 1, end):
            if pattern.match(page.lines[i]):
                target = i
                break
        if target is None:
            fresh = []
            for link in links:
                if any(t in link_targets(" ".join(fresh)) for t in link_targets(link)):
                    result.skipped_duplicate += 1
                    continue
                fresh.append(link)
            if not fresh:
                continue
            page.append_to_section(start, end, ["- %s: %s" % (label, " / ".join(fresh))])
            result.added["related"] += len(fresh)
            changed = True
            continue
        line = page.lines[target]
        present = set(link_targets(line))
        additions = []
        for link in links:
            targets = link_targets(link)
            if targets and all(t in present for t in targets):
                result.skipped_duplicate += 1
                continue
            if not targets and link in line:
                result.skipped_duplicate += 1
                continue
            present.update(targets)
            additions.append(link)
        if not additions:
            continue
        suffix = " / ".join(additions)
        if line.rstrip().endswith(":"):
            page.lines[target] = line.rstrip() + " " + suffix
        else:
            page.lines[target] = line.rstrip() + " / " + suffix
        result.added["related"] += len(additions)
        changed = True
    return changed


def apply_sources_section(page, entries, result):
    section = ensure_sources(page, result)
    name, start, end = section
    present = set()
    for i in range(start + 1, end):
        present.update(link_targets(page.lines[i]))
    fresh = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"link": entry}
        link = str(entry.get("link") or "").strip()
        if not link:
            continue
        targets = link_targets(link)
        if targets and all(t in present for t in targets):
            result.skipped_duplicate += 1
            continue
        present.update(targets)
        note = str(entry.get("note") or "").strip()
        if note and not note.startswith("("):
            note = "(%s)" % note
        fresh.append("- %s%s" % (link, note))
    if not fresh:
        return False
    page.append_to_section(start, end, fresh)
    result.added["sources_section"] += len(fresh)
    return True


def yaml_quote(value, quoted):
    if not quoted:
        return value
    return '"%s"' % value.replace('"', '\\"')


def parse_inline_list(raw):
    """`[a, "b"]` を要素の文字列リストに分ける。解析できなければ None。

    要素には `[[@X, Y]]` のような wikilink が入るので、角括弧の深さと
    引用符の内側を数えながら区切る。単純な `split(",")` では題名に
    含まれる読点や桁区切りのカンマで壊れる。
    """
    text = raw.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return []
    items = []
    buf = []
    quote = None
    depth = 0
    i = 0
    while i < len(inner):
        char = inner[i]
        if quote is not None:
            if char == "\\" and i + 1 < len(inner):
                buf.append(inner[i:i + 2])
                i += 2
                continue
            buf.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(char)
        elif char == "[":
            depth += 1
            buf.append(char)
        elif char == "]":
            depth -= 1
            if depth < 0:
                return None
            buf.append(char)
        elif char == "," and depth == 0:
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
        i += 1
    if quote is not None or depth != 0:
        return None
    items.append("".join(buf).strip())
    return [item for item in items if item]


def ensure_block_list(page, key):
    """frontmatter の `key:` をブロックリスト形に揃え、key 行の index を返す。

    `tags: [2026/08/25, concept]` のようなインライン配列へ `- item` を
    足すと flow と block が混ざって frontmatter が壊れるため、先に要素を
    解析してブロックリストへ展開する。引用符の有無は元の要素の書き方を
    そのまま引き継ぐ。解析できない形なら PageError でページを中止する。
    """
    key_idx = page.fm_key_index(key)
    if key_idx is None:
        return None
    match = re.match(r"^%s:\s*(.*)$" % re.escape(key), page.lines[key_idx])
    raw = (match.group(1) or "").strip()
    if not raw or raw.startswith("#"):
        return key_idx
    if raw.startswith("["):
        values = parse_inline_list(raw)
        if values is None:
            raise PageError(
                "frontmatter の `%s:` をインライン配列として解析できない: %s"
                % (key, raw))
    else:
        values = [raw]
    if page.fm_item_indices(key_idx):
        raise PageError(
            "frontmatter の `%s:` が値とブロック項目を同時に持っている" % key)
    page.lines[key_idx] = "%s:" % key
    page.fm_insert(key_idx + 1, ["  - %s" % value for value in values])
    return key_idx


def apply_frontmatter_sources(page, links, result):
    key_idx = page.fm_key_index("sources")
    if key_idx is None:
        anchor = page.fm_key_index("related")
        if anchor is None:
            anchor = page.fm_end - 1
        else:
            items = page.fm_item_indices(anchor)
            anchor = items[-1] if items else anchor
        page.fm_insert(anchor + 1, ["sources:"])
        result.warnings.append("frontmatter に `sources:` を新設した")

    key_idx = ensure_block_list(page, "sources")
    items = page.fm_item_indices(key_idx)
    present = set()
    for i in items:
        present.update(link_targets(page.lines[i]))
    indent = "  "
    quoted = True
    if items:
        match = re.match(r"^(\s+)-\s+(.*)$", page.lines[items[0]])
        if match:
            indent = match.group(1)
            quoted = match.group(2).strip()[:1] in ('"', "'")

    fresh = []
    for link in links:
        text = str(link).strip()
        if not text:
            continue
        targets = link_targets(text)
        if targets and all(t in present for t in targets):
            result.skipped_duplicate += 1
            continue
        present.update(targets)
        fresh.append("%s- %s" % (indent, yaml_quote(text, quoted)))
    if not fresh:
        return False

    at = (items[-1] if items else key_idx) + 1
    page.fm_insert(at, fresh)
    result.added["frontmatter_sources"] += len(fresh)
    return True


def apply_section_append(page, specs, result):
    changed = False
    for spec in specs:
        name = str(spec.get("section") or "").strip()
        lines = spec.get("lines") or []
        if isinstance(lines, str):
            lines = [lines]
        lines = [str(l) for l in lines if str(l).strip()]
        if not name:
            raise PageError("section_append に `section` が無い")
        if not lines:
            continue
        aliases = (LEGACY_INBOX,) if name == INBOX else \
            ((INBOX,) if name == LEGACY_INBOX else ())
        section = page.find_section(name, aliases)
        if section is None:
            raise PageError("`## %s` が無い(section_append は節を新設しない)" % name)
        _, start, end = section
        existing = set()
        for i, j in page.section_bullets(start, end):
            existing.add(normalize_item(page.lines[i]))
        for i in range(start + 1, end):
            existing.add(normalize_item(page.lines[i]))
        fresh = []
        for line in lines:
            norm = normalize_item(line)
            if norm in existing:
                result.skipped_duplicate += 1
                continue
            existing.add(norm)
            fresh.append(line.rstrip())
        if not fresh:
            continue
        page.append_to_section(start, end, fresh)
        result.added["section_append"] += len(fresh)
        changed = True
    return changed


def touch_frontmatter(page, date, result):
    """`updated:` を date に、`tags:` に日付タグを入れる。`date:` は触らない。"""
    idx = page.fm_key_index("updated")
    if idx is None:
        anchor = page.fm_key_index("created")
        at = anchor + 1 if anchor is not None else page.fm_end
        page.fm_insert(at, ["updated: %s" % date])
        result.warnings.append("frontmatter に `updated:` を新設した")
    else:
        match = re.match(r"^updated:\s*(.*)$", page.lines[idx])
        if (match.group(1) or "").strip() != date:
            page.lines[idx] = "updated: %s" % date

    tag = date.replace("-", "/")
    key_idx = page.fm_key_index("tags")
    if key_idx is None:
        anchor = page.fm_key_index("updated")
        at = anchor + 1 if anchor is not None else page.fm_end
        page.fm_insert(at, ["tags:", "  - %s" % tag])
        result.warnings.append("frontmatter に `tags:` を新設した")
        return
    key_idx = ensure_block_list(page, "tags")
    items = page.fm_item_indices(key_idx)
    values = []
    for i in items:
        match = re.match(r"^(\s+)-\s+(.*)$", page.lines[i])
        values.append((i, match.group(1), match.group(2).strip().strip('"\'')))
    if any(v == tag for _, _, v in values):
        return
    indent = values[0][1] if values else "  "
    at = key_idx + 1
    for i, _, value in values:
        if DATE_TAG_RE.match(value):
            at = i + 1
        else:
            break
    page.fm_insert(at, ["%s- %s" % (indent, tag)])


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def apply_operation(page, op, result):
    changed = False
    observations = as_list(op.get("observations"))
    if observations:
        section = ensure_inbox(page, result)
        changed = append_bullets(page, section, observations, result, "observations") or changed

    removals = as_list(op.get("questions_remove_containing"))
    if removals:
        changed = remove_questions(page, removals, result) or changed

    questions = as_list(op.get("questions_add"))
    if questions:
        section = ensure_questions(page, result)
        changed = append_bullets(page, section, questions, result, "questions") or changed

    related = op.get("related") or {}
    if related:
        if not isinstance(related, dict):
            raise PageError("`related` はラベルからリンク配列への辞書である")
        changed = apply_related(page, related, result) or changed

    sources = as_list(op.get("sources_section"))
    if sources:
        changed = apply_sources_section(page, sources, result) or changed

    fm_sources = as_list(op.get("frontmatter_sources"))
    if fm_sources:
        changed = apply_frontmatter_sources(page, fm_sources, result) or changed

    section_specs = as_list(op.get("section_append"))
    if section_specs:
        changed = apply_section_append(page, section_specs, result) or changed

    return changed


def process(op, opts):
    """1 ページを処理して (結果 dict, 終了コード寄与, diff テキスト) を返す。"""
    rel, err = normalize_rel(op.get("page"))
    if rel is None:
        return {"page": op.get("page"), "changed": False, "error": err}, EXIT_DATA, None

    date = str(op.get("date") or opts.date or datetime.date.today().isoformat()).strip()
    if not DATE_RE.match(date):
        return ({"page": rel, "changed": False,
                 "error": "date は YYYY-MM-DD 形式である: %r" % date}, EXIT_DATA, None)

    path = VAULT_ROOT / rel
    if not path.is_file():
        return ({"page": rel, "changed": False, "error": "ページが無い"},
                EXIT_DATA, None)

    result = Result(rel)
    try:
        token = wiki_lock.acquire(rel)
    except RuntimeError as exc:
        return ({"page": rel, "changed": False,
                 "error": "ロック取得に失敗した: %s" % exc}, EXIT_LOCKED, None)
    try:
        try:
            original = read_verbatim(path)
        except OSError as exc:
            return ({"page": rel, "changed": False,
                     "error": "ページを読めない: %s" % exc}, EXIT_DATA, None)

        page = Page(original)
        if not page.has_frontmatter:
            return ({"page": rel, "changed": False,
                     "error": "frontmatter が無い(conventions §2)"}, EXIT_DATA, None)

        try:
            changed = apply_operation(page, op, result)
            if changed:
                touch_frontmatter(page, date, result)
        except PageError as exc:
            return ({"page": rel, "changed": False, "error": str(exc),
                     "warnings": result.warnings}, EXIT_DATA, None)
        except Exception as exc:
            # 想定外の例外でバッチ全体を落とさない。このページだけ失敗として
            # 報告し、ロックは finally で解放して次のページへ進ませる。
            return ({"page": rel, "changed": False,
                     "error": "適用中に想定外の例外: %s: %s"
                              % (type(exc).__name__, exc),
                     "warnings": result.warnings}, EXIT_DATA, None)

        updated = page.render(force_trailing_nl=changed)
        changed = updated != original
        lines = len(updated.replace("\r\n", "\n").rstrip("\n").split("\n"))
        if lines > LINE_SOFT_LIMIT:
            result.warnings.append(
                "%d 行(目安 %d 行超)。分割か再編纂を検討する。追記自体は行った"
                "(conventions §7)" % (lines, LINE_SOFT_LIMIT))

        diff = None
        if opts.dry_run:
            if changed:
                diff = "".join(difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile="a/" + rel, tofile="b/" + rel))
        elif changed:
            try:
                atomic_write(path, updated)
            except OSError as exc:
                return ({"page": rel, "changed": False,
                         "error": "書き込みに失敗した: %s" % exc}, EXIT_DATA, None)
    finally:
        wiki_lock.release(rel, token)

    return result.payload(changed, lines), EXIT_OK, diff


def build_op_from_flags(args):
    op = {"page": args.page, "date": args.date}
    if args.observation:
        op["observations"] = args.observation
    if args.question:
        op["questions_add"] = args.question
    if args.remove_question_containing:
        op["questions_remove_containing"] = args.remove_question_containing
    related = {}
    if args.related_source:
        related["ソース"] = args.related_source
    if args.related_concept:
        related["概念"] = args.related_concept
    if args.related_entity:
        related["エンティティ"] = args.related_entity
    if related:
        op["related"] = related
    if args.source_entry:
        op["sources_section"] = [{"link": e} for e in args.source_entry]
    if args.frontmatter_source:
        op["frontmatter_sources"] = args.frontmatter_source
    if args.section_line:
        op["section_append"] = [{"section": args.section, "lines": args.section_line}]
    return op


def load_batch(spec_path):
    try:
        data = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except OSError as exc:
        log("ERR: --batch を読めない: %s" % exc)
        return None
    except ValueError as exc:
        log("ERR: --batch が JSON として不正: %s" % exc)
        return None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        log("ERR: --batch は操作の非空リストでなければならない")
        return None
    for item in data:
        if not isinstance(item, dict):
            log("ERR: --batch の要素はオブジェクトでなければならない")
            return None
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="既存 wiki ページへの ingest 由来の追記を 1 呼び出しで行う。")
    parser.add_argument("--page", help="vault 相対パス(wiki/ 配下の .md)")
    parser.add_argument("--batch", help="複数ページの SPEC.json")
    parser.add_argument("--date", help="updated と日付タグに使う YYYY-MM-DD(既定は今日)")
    parser.add_argument("--observation", action="append",
                        help="`## 未編纂の観察` への追記(旧名 `## 横断的知見` を優先)")
    parser.add_argument("--question", action="append", help="`## 未解決の問い` への追記")
    parser.add_argument("--remove-question-containing", action="append",
                        help="この文字列を含む未解決の問いを落とす")
    parser.add_argument("--related-source", action="append", help="`## 関連` の `- ソース:` 行へ")
    parser.add_argument("--related-concept", action="append", help="`## 関連` の `- 概念:` 行へ")
    parser.add_argument("--related-entity", action="append",
                        help="`## 関連` の `- エンティティ:` 行へ")
    parser.add_argument("--source-entry", action="append",
                        help="`## 出典` への追記。例 '[[@X]](何を根拠にしたか)'")
    parser.add_argument("--frontmatter-source", action="append",
                        help="frontmatter の `sources:` へ追加")
    parser.add_argument("--section", help="--section-line の宛先となる `## ` 見出し名")
    parser.add_argument("--section-line", action="append",
                        help="--section で指定した既存節への行追記(節が無ければ error)")
    parser.add_argument("--dry-run", action="store_true",
                        help="書かずに unified diff を表示する")
    parser.add_argument("--lock-wait-sec", type=float, default=None,
                        help="ロック待ちの上限秒(既定は wiki_lock の %d 秒)。"
                             "0 は「1 回だけ試す」の意味で %s 秒に丸める"
                             % (wiki_lock.LOCK_WAIT_SEC, MIN_LOCK_WAIT_SEC))
    args = parser.parse_args(argv)

    if args.lock_wait_sec is not None:
        if args.lock_wait_sec < 0:
            log("ERR: --lock-wait-sec は 0 以上である")
            return EXIT_USAGE
        wiki_lock.LOCK_WAIT_SEC = max(MIN_LOCK_WAIT_SEC, args.lock_wait_sec)

    if args.batch and args.page:
        log("ERR: --batch と --page は併用できない")
        return EXIT_USAGE
    if args.section_line and not args.section:
        log("ERR: --section-line には --section が必要である")
        return EXIT_USAGE
    if args.date and not DATE_RE.match(args.date):
        log("ERR: --date は YYYY-MM-DD 形式である")
        return EXIT_USAGE

    if args.batch:
        ops = load_batch(args.batch)
        if ops is None:
            return EXIT_USAGE
    else:
        if not args.page:
            log("ERR: --page か --batch のどちらかが必要である")
            return EXIT_USAGE
        ops = [build_op_from_flags(args)]

    status = EXIT_OK
    for op in ops:
        try:
            result, code, diff = process(op, args)
        except Exception as exc:
            # 1 ページの想定外の失敗で後続ページを取りこぼさない
            result = {"page": op.get("page"), "changed": False,
                      "error": "処理中に想定外の例外: %s: %s"
                               % (type(exc).__name__, exc)}
            code, diff = EXIT_DATA, None
        if code == EXIT_LOCKED or (code == EXIT_DATA and status != EXIT_LOCKED):
            status = code
        if diff:
            sys.stdout.write(diff)
        print(json.dumps(result, ensure_ascii=False))
    return status


if __name__ == "__main__":
    sys.exit(main())
