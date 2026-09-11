#!/usr/bin/env python3
"""wiki-verify-ingest.py — ingest 対象集合の機械的検証を 1 呼び出しに束ねる。

fan-out 後の検証(図の埋め込みと attachment の両方向照合、Navigation 行の
リンク解決、frontmatter の必須項目、枚数)と、索引更新前の「本書分ファイルの
分類」、コミット前の「staged 集合の厳密照合」は、これまで数回〜十数回の Bash
で回していた。入力トークンの大半はキャッシュ読み戻し(呼び出し時点の常駐文脈長
の総和)なので、ツール呼び出しを 1 回減らすと常駐文脈 1 回分がまるごと浮く。
本スクリプトは検査項目をまとめて 1 プロセスで走らせ、1 所見 1 行で報告する。

本スクリプトは原則**読み取り専用**である。ページを書かない・直さない。例外は
`--cleanup-images`(fetch が今作った images ディレクトリから page-render を除く)
だけである。作成時の frontmatter 検証は `wiki-page-write.py`、既存ページへの
追記は `wiki-append.py` の領分で、本スクリプトはそれらが書いた結果と、
subagent が Write で書いた結果をまとめて突き合わせる。全 wiki の健全性検査は
`wiki-lint` skill の領分であり、本スクリプトは「今回の ingest 対象集合」に
限定した高速検査である。

使い方:
  python3 scripts/wiki-verify-ingest.py --glob 'wiki/sources/@2013__X__書名*' \\
      --attachments-slug <slug> --expect-count 39 --date 2026-09-04
  python3 scripts/wiki-verify-ingest.py --match "<書名>" --classify
  python3 scripts/wiki-verify-ingest.py --match "<書名>" --staged-check \\
      --extra-files .raw/books/<slug>/ wiki/index.md wiki/log.md
  python3 scripts/wiki-verify-ingest.py --list-figure-ids .raw/papers/<slug>.txt
  python3 scripts/wiki-verify-ingest.py --cleanup-images .raw/papers/<slug>/images

環境変数 `WIKI_VAULT_ROOT` で vault ルートを差し替えられる(試験用)。

終了コード:
  0 — error なし
  1 — error あり
  2 — 使い方の誤り
"""

import argparse
import glob as globlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote as url_unquote

VAULT_ROOT = Path(
    os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent
).resolve()

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_USAGE = 2


def _load_writer():
    """`wiki-page-write.py` の flat YAML 解析を再利用する。

    ファイル名にハイフンがあり `import` できないので importlib で読み込む。
    検証の判定を書き手と同じ解析器の上に載せたいので、複製ではなく再利用する
    (書き手が通した frontmatter を検証側が別解釈で落とす事故を避ける)。
    """
    path = Path(__file__).resolve().parent / "wiki-page-write.py"
    spec = importlib.util.spec_from_file_location("wiki_page_write", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_APPENDER = None


def _load_appender():
    """`wiki-append.py` を遅延読み込みする(インライン配列の解析にだけ要る)。

    `--staged-check` 単独のような軽い呼び出しに読み込み時間を足さないよう、
    必要になった時点で 1 度だけ読む。
    """
    global _APPENDER
    if _APPENDER is None:
        path = Path(__file__).resolve().parent / "wiki-append.py"
        spec = importlib.util.spec_from_file_location("wiki_append", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _APPENDER = module
    return _APPENDER


_WRITER = _load_writer()
split_frontmatter = _WRITER.split_frontmatter
frontmatter_keys = _WRITER.frontmatter_keys
unquote = _WRITER.unquote

# 走査から外すディレクトリ。Obsidian 自身も先頭 `.` のディレクトリを vault の
# ノートとして扱わないので、`.git` `.obsidian` `.raw` `.trash` などはまとめて
# 落とす。`.raw/` 配下への wikilink(source の `sources:` が指す原本)は索引では
# なく実パス解決で拾うので、除外しても未解決にはならない。
SKIP_DIR_NAMES = frozenset({"node_modules"})

KNOWN_TYPES = frozenset({
    "source", "entity", "concept", "question", "comparison",
    "meta", "survey", "fold", "overview", "thesis",
    "ask", "brief",
})
CATALOG_RELS = ("wiki/index.md", "wiki/hot.md", "wiki/log.md")

ADDRESS_RE = re.compile(r"^c-\d{6}$")
DATE_TAG_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")
ADDRESS_LINE_RE = re.compile(r"^address:\s*(\S+)\s*$", re.M)

# frontmatter のうちリンクを検査するキー(L4: `title` などは対象外)。
LINKED_FM_KEYS = frozenset({"related", "sources", "asks", "brief"})
FM_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
FM_ITEM_RE = re.compile(r"^\s*-\s+")

# 図表 ID。`Figure 3` `Fig. 3a` `Table 1` `図 9-1` `表2.3` を拾う。置き換え元の
# grep に合わせて大文字小文字は区別する(散文の "table 1" を拾わない)。
FIGURE_ID_RE = re.compile(
    r"(?:\b(Figures?|Figs?|Tables?|Tabs?)\.?[ \t]*|([図表])[ \t]*)"
    r"(\d+(?:[.\-–—]\d+)?[a-z]?)")
# `Figures 2 and 3` `Figs. 2, 3 and 5` のような並列言及の続き。
FIGURE_MORE_RE = re.compile(
    r"(?:[ \t]*(?:[,、&]|\band\b|および))+[ \t]*(\d+(?:[.\-–—]\d+)?[a-z]?)")
FIGURE_RANGE_RE = re.compile(r"^(\d+)[-–—](\d+)$")
FIGURE_LABELS = {"figure": "Figure", "figures": "Figure",
                 "fig": "Figure", "figs": "Figure",
                 "table": "Table", "tables": "Table",
                 "tab": "Table", "tabs": "Table"}
# 複数形のときだけ `2-4` を範囲として開く。単数形の `Figure 4-1` と `図 4-1` は
# 書籍の「章-連番」なので 1 つの ID として扱う。
FIGURE_PLURALS = frozenset({"figures", "figs", "tables", "tabs"})
FIGURE_RANGE_MAX = 20
# raw テキストのうち図表 ID を拾わない範囲の始まり(参考文献部)。
REFERENCES_HEAD_RE = re.compile(
    r"^[#>\s]*(?:\d+[.)]?[ \t]*)?(?:references|bibliography|参考文献)\b",
    re.M | re.I)

WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+)\]\]")
MD_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# source ページに置いてはならない検証結果節(wiki-ingest-paper「出典検査」の
# 規律: 検査結果はチャットへ報告し、ノート本文に書かない)。
VERIFY_SECTION_WORDS = ("検証パス", "検証結果", "出典検査", "出典検証",
                        "ファクトチェック", "検証済み")
# concept の節名に使ってはならない語(conventions §8 主題節の規則 1 が列挙する
# 「ページの生成過程を指す語」)。
GENERIC_SECTION_WORDS = ("知見", "観察", "考察", "示唆", "まとめ",
                         "その他", "補足", "横断的")
# 生成過程の語を繋いだだけの見出しを 1 語扱いにするための接続語と約物。
SECTION_JOINERS = "とやおよび並びに・／/、,＆&|｜()（）[]「」:：-—〜~ \t　"
# 固定見出しは機能名なので上の語だけで構成されていても許す。`横断的知見` は
# `未編纂の観察` の旧名(conventions §8 規則 8)。
FIXED_SECTIONS = frozenset({"未編纂の観察", "未解決の問い", "横断的知見"})

MAX_LINES = 300
MIN_SOURCE_LINES = 100
# 書籍の章 source は「短い章は 30〜50 行」を許す(wiki-ingest-book Step 3)。
MIN_BOOK_SOURCE_LINES = 30


def log(msg):
    print(msg, file=sys.stderr)


def nfc(text):
    """macOS の APFS は正規化を保存するので、索引と照合の両側を NFC に寄せる。"""
    return unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------- git 連携

def git_out(args):
    """git の標準出力を返す。git が使えない・失敗したら None。"""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(VAULT_ROOT), capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_lines(args):
    """NUL 区切りの git 出力をリストで返す。git が使えなければ None。"""
    out = git_out(args)
    return None if out is None else [x for x in out.split("\0") if x]


def git_status_entries():
    """`git status --porcelain=v1 -z` を [(status, path)] にほどく。"""
    items = git_lines(["status", "--porcelain=v1", "-z",
                       "--untracked-files=all", "--", "wiki/"])
    if items is None:
        return None
    entries = []
    i = 0
    while i < len(items):
        raw = items[i]
        i += 1
        if len(raw) < 4:
            continue
        # `-z` の改名・複製は「新パス NUL 旧パス NUL」の順なので、
        # raw[3:] が新パスであり、次の要素(旧パス)は読み飛ばす。
        status, path = raw[:2], raw[3:]
        if status[0] in ("R", "C"):
            i += 1
        entries.append((status, nfc(path)))
    return entries


def classify_status(status):
    """git status の 2 文字を changed / gone / None に畳む。

    skill の分類 bash は未 stage の `??` と ` M` だけを見るが、stage 済みの
    同じ変更(`A ` `M ` `MM`)も、`git mv` による改名(`R ` `RM`)や複製(`C `)も
    同じ対象である。改名を落とすと、章名を直したページが分類からも
    `CLASSIFY-OTHER` からも消えて索引・log・commit から静かに抜ける。
    """
    if status == "??":
        return "changed"
    if status in ("!!", "  "):
        return None
    if status[1] == "D" or ("D" in status and not any(c in "MARC" for c in status)):
        return "gone"
    if any(c in "MARC" for c in status):
        return "changed"
    return None


def tracked_files():
    """git 管理下のパス集合。既存ページと新規ページの区別に使う。"""
    items = git_lines(["ls-files", "-z", "--", "wiki/"])
    if items is None:
        return None
    return {nfc(x) for x in items}


# ------------------------------------------------------------ vault の索引

def build_index():
    """(md_by_stem, asset_by_name) を 1 回の走査で作る。ファイルは開かない。

    md_by_stem  — 拡張子を落とした basename(casefold/NFC)→ 相対パス列
    asset_by_name — `.md` 以外の basename(casefold/NFC)→ 相対パス列
    """
    md_by_stem = {}
    asset_by_name = {}
    root_str = str(VAULT_ROOT)
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in SKIP_DIR_NAMES]
        rel_dir = os.path.relpath(dirpath, root_str)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for name in filenames:
            rel = nfc(f"{rel_dir}/{name}" if rel_dir else name)
            base = nfc(name)
            if name.endswith(".md"):
                md_by_stem.setdefault(base[:-3].casefold(), []).append(rel)
            else:
                asset_by_name.setdefault(base.casefold(), []).append(rel)
    return md_by_stem, asset_by_name


def build_address_map():
    """`wiki/` 配下の `.md` だけを走査して address → 相対パス列を作る。

    全ページを 1 件ずつ開くので、この索引は「対象ページが address を 1 つでも
    持つとき」にだけ作る(呼び出し側で判定する)。走査は `os.scandir` で
    ディレクトリ実体を 1 度だけ読み、各ファイルは冒頭 1KB しか読まない。
    """
    addr_map = {}
    root = VAULT_ROOT / "wiki"
    if not root.is_dir():
        return addr_map
    stack = [str(root)]
    root_str = str(VAULT_ROOT)
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in SKIP_DIR_NAMES:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                if not name.endswith(".md") or not entry.is_file():
                    continue
            except OSError:
                continue
            addr = read_address(entry.path)
            if addr:
                rel = nfc(os.path.relpath(entry.path, root_str).replace(os.sep, "/"))
                addr_map.setdefault(addr, []).append(rel)
    return addr_map


def is_generic_section(heading):
    """concept の節名が「生成過程の語だけ」で出来ているかを判定する。

    conventions §8 主題節の規則 1 は「知見・観察・考察・示唆・まとめ・その他・
    補足・横断的」を節名に使うなと定めるが、部分一致で弾くと `## 観察可能性` や
    `## まとめ買い` のような正当な主題名まで落ちる。そこで**禁止語と接続語を
    取り除いて何も残らない見出しだけ**を違反とする。

    - 違反: `## 知見` `## 考察` `## まとめ` `## 知見と考察` `## 横断的観察`
            `## 知見(まとめ)` `## 知見: まとめ`(括弧・コロンも接続語に数える)
    - 許容: `## 観察可能性` `## まとめ買い` `## 観察可能性の設計` `## 補足事項`
    - 別名: `## 横断的知見` は残余が空でも FIXED_SECTIONS 側で許す
    """
    rest = heading
    for word in GENERIC_SECTION_WORDS:
        rest = rest.replace(word, "")
    return rest.strip(SECTION_JOINERS) == "" and heading.strip() != ""


def read_address(path):
    """frontmatter 冒頭だけを読んで `address` の値を返す。無ければ None。

    `address` は wiki-page-write が frontmatter の先頭付近へ置く規約なので、
    全ページを開く走査では冒頭 1KB だけを読む(重複検出は最善努力であり、
    読み落としは偽陰性にしかならない)。値は引用符の有無で表記が揺れるため、
    照合側と同じ `unquote` で正規化してから返す。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(1024)
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    end = head.find("\n---", 3)
    match = ADDRESS_LINE_RE.search(head if end < 0 else head[:end])
    return unquote(match.group(1)) if match else None


# ------------------------------------------------------------ ページの解析

def read_page(rel):
    try:
        return (VAULT_ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def strip_code(line):
    return INLINE_CODE_RE.sub("", line)


def scan_body(body_lines, first_lineno):
    """本文をコードフェンスを避けて走査し、行番号つきの行列を返す。"""
    out = []
    fence = None
    for offset, line in enumerate(body_lines):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        out.append((first_lineno + offset, line))
    return out


def link_target(raw):
    """`|alias`・`#見出し`・`^block` を落とした素のリンク先を返す。

    表の中の wikilink は GFM のセル区切りと衝突するため、alias 区切りが
    `[[Target\\|alias]]`(埋め込みなら `![[f.png\\|300]]`)と書かれる。区切りと
    しては通常の `|` と同じものなので、分割の前にエスケープを外す。
    """
    text = raw.replace("\\|", "|").split("|", 1)[0]
    text = text.split("#", 1)[0]
    text = text.split("^", 1)[0]
    return nfc(text.strip())


def link_stem(target):
    """リンク先の basename から `.md` だけを落とす。`4.2` のような点は残す。"""
    name = PurePosixPath(target).name
    return name[:-3] if name.lower().endswith(".md") else name


def path_exists(rel_candidates):
    for cand in rel_candidates:
        if not cand:
            continue
        if (VAULT_ROOT / cand).exists():
            return True
    return False


def path_candidates(target, page_rel):
    """パス修飾されたリンク先の候補を、vault ルートとページ位置から並べる。

    `[[.raw/books/<slug>/chapters/ch-01]]` のように拡張子を省いた原本リンクが
    あるので、`.md` 以外に `.txt` `.pdf` も補う(索引ではなく実パス解決側だけ)。
    """
    text = target.lstrip("/")
    bases = ["", str(PurePosixPath(page_rel).parent), "wiki/sources"]
    out = []
    for base in bases:
        prefix = f"{base}/" if base and base != "." else ""
        out.append(prefix + text)
        for suffix in (".md", ".txt", ".pdf"):
            if not text.lower().endswith(suffix):
                out.append(prefix + text + suffix)
    return out


def is_self_ref(target, page_rel, self_names):
    """パス修飾リンクは実ファイルが当ページのときだけ自己参照。

    `brief:` / `asks:` は source と同じ basename を持つが別ファイルなので、
    stem 一致だけでは自己参照にしない。
    """
    if "/" in target:
        page_path = (VAULT_ROOT / page_rel).resolve()
        for cand in path_candidates(target, page_rel):
            p = VAULT_ROOT / cand
            if p.exists() and p.resolve() == page_path:
                return True
        return False
    return nfc(link_stem(target)).casefold() in self_names


def resolve_link(target, page_rel, index):
    """通常の wikilink が vault 内のどれかに解決するか。

    パス修飾(`wiki/briefs/@...` など `/` を含む)は実パスだけを見る。
    stem へ落とすと、source と同じ basename の派生ノート死リンクが
    source 自身に誤解決する(conventions §14・§15)。
    """
    if not target or URL_RE.match(target):
        return True
    if "/" in target:
        return path_exists(path_candidates(target, page_rel))
    md_by_stem, asset_by_name = index
    return (link_stem(target).casefold() in md_by_stem
            or PurePosixPath(target).name.casefold() in asset_by_name)


def resolve_embed(target, page_rel, index):
    """埋め込み(画像・ノート)が実ファイルに解決するか。"""
    if not target or URL_RE.match(target):
        return True
    if path_exists(path_candidates(target, page_rel)):
        return True
    md_by_stem, asset_by_name = index
    if PurePosixPath(target).name.casefold() in asset_by_name:
        return True
    return link_stem(target).casefold() in md_by_stem


# -------------------------------------------------------------- 所見の器

class Report:
    def __init__(self):
        self.findings = []
        self.counters = {
            "pages": 0, "embeds": 0, "unresolved_links": 0,
            "unresolved_embeds": 0, "unused_attachments": 0,
        }

    def add(self, severity, check, message, path=None, line=None):
        self.findings.append({
            "severity": severity, "id": check,
            "path": path, "line": line, "message": message,
        })

    def error(self, check, message, path=None, line=None):
        self.add("ERROR", check, message, path, line)

    def warn(self, check, message, path=None, line=None):
        self.add("WARN", check, message, path, line)

    def info(self, check, message, path=None, line=None):
        self.add("INFO", check, message, path, line)


SEV_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}


# ------------------------------------------------------------ ページの検査

def check_frontmatter(rel, fm, report, opts, is_tracked):
    """conventions §2 / §3 の必須項目を見る。"""
    address = fm.get("address")
    if address is None:
        if is_tracked:
            report.warn("FM-ADDR", "既存ページに address が無い"
                        "(更新時は再採番しない。conventions 鉄則 7)", rel)
        else:
            report.error("FM-ADDR", "address が無い(conventions §2)", rel)
    elif isinstance(address, list) or not ADDRESS_RE.match(unquote(address)):
        report.error("FM-ADDR", "address が c-NNNNNN 形式でない: %r" % address, rel)

    page_type = scalar(fm, "type")
    if page_type is None:
        report.error("FM-TYPE", "type が無い(conventions §2)", rel)
    elif page_type not in KNOWN_TYPES:
        report.error("FM-TYPE", "type が未知の値である: %r" % page_type, rel)

    if scalar(fm, "title") is None:
        report.error("FM-TITLE", "title が無い(conventions §2)", rel)

    date = scalar(fm, "date")
    if date is None:
        report.error("FM-DATE", "date が無い(conventions §2 は時刻付きを要求する)", rel)
    elif not DATETIME_RE.match(date):
        report.error("FM-DATE", "date が `YYYY-MM-DD HH:mm` でない: %r" % date, rel)

    for key, check in (("created", "FM-CREATED"), ("updated", "FM-UPDATED")):
        value = scalar(fm, key)
        if value is None:
            report.error(check, "%s が無い(conventions §2)" % key, rel)
        elif not DAY_RE.match(value):
            report.error(check, "%s が `YYYY-MM-DD` でない: %r" % (key, value), rel)

    tags = fm.get("tags")
    if tags is None:
        report.error("FM-TAG", "tags が無い(conventions §2)", rel)
    elif not isinstance(tags, list):
        report.error("FM-TAG", "tags が `- item` 形式のリストでない(conventions §2 ルール 4)", rel)
    elif not tags:
        report.error("FM-TAG", "tags が空である。先頭に日付タグ YYYY/MM/DD が要る", rel)
    else:
        head = unquote(tags[0])
        if not DATE_TAG_RE.match(head):
            report.error("FM-TAG", "tags の先頭が日付タグ YYYY/MM/DD でない: %r" % head, rel)
        if opts.date:
            wanted = opts.date.replace("-", "/")
            if wanted not in [unquote(t) for t in tags]:
                report.error("FM-TAGDATE", "当日の日付タグ %s が tags に無い" % wanted, rel)

    if scalar(fm, "status") is None:
        report.error("FM-STATUS", "status が無い(conventions §2)", rel)

    if page_type == "source":
        source_type = scalar(fm, "source_type")
        if source_type is None:
            report.error("FM-STYPE", "source に source_type が無い(conventions §3)", rel)
        publish = (scalar(fm, "publish") or "").lower()
        if source_type == "book" and publish != "false":
            report.error("FM-PUBLISH",
                         "書籍の章 source に `publish: false` が無い(conventions §9)", rel)
        if source_type == "thesis" and publish == "false":
            report.warn("FM-PUBLISH",
                        "thesis に `publish: false` は不要(conventions §10-3)", rel)
        check_paper_ids(rel, fm, report, source_type)
    elif page_type == "entity":
        if scalar(fm, "entity_type") is None:
            report.error("FM-ETYPE", "entity に entity_type が無い(conventions §3)", rel)
    elif page_type == "concept":
        for key in ("complexity", "domain"):
            if scalar(fm, key) is None:
                report.error("FM-CFIELD", "concept に %s が無い(conventions §3)" % key, rel)


ARXIV_ID_VALUE_RE = re.compile(r"^\d{4}\.\d{4,5}$")
ARXIV_IN_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
DOI_VALUE_RE = re.compile(r"^10\.\d{4,9}/\S+$")
DOI_IN_URL_RE = re.compile(r"doi\.org/(10\.\d{4,9}/\S+)", re.I)


def check_paper_ids(rel, fm, report, source_type):
    """conventions §3 の書誌 ID。paper で url から導出できるのに frontmatter に無いものは WARN
    (scripts/paper-ids.py backfill で埋まる)。形式の誤りは ERROR。"""
    arxiv_id = scalar(fm, "arxiv_id")
    doi = scalar(fm, "doi")
    if arxiv_id is not None and not ARXIV_ID_VALUE_RE.match(arxiv_id):
        report.error("FM-ARXIV", "arxiv_id は版番号なしの `YYMM.NNNNN` にする: %r" % arxiv_id, rel)
    if doi is not None:
        if doi.lower().startswith(("http", "doi:")):
            report.error("FM-DOI", "doi は `10.xxxx/...` だけを書く(doi.org/ や doi: を付けない): %r" % doi, rel)
        elif not DOI_VALUE_RE.match(doi):
            report.error("FM-DOI", "doi が `10.xxxx/...` 形式でない: %r" % doi, rel)
        elif doi != doi.lower():
            report.warn("FM-DOI", "doi は小文字に揃える(paper-ids.py の照合キー): %r" % doi, rel)
    if source_type != "paper":
        return
    url = scalar(fm, "url") or ""
    if arxiv_id is None and ARXIV_IN_URL_RE.search(url):
        report.warn("FM-ARXIV", "url に arXiv ID があるのに arxiv_id が無い(conventions §3。"
                    "`paper-ids.py backfill --write --pages` で埋まる)", rel)
    if doi is None and DOI_IN_URL_RE.search(url):
        report.warn("FM-DOI", "url が doi.org なのに doi が無い(conventions §3)", rel)


def scalar(fm, key):
    """スカラー値を素の文字列で返す。未設定・リストは None。"""
    value = fm.get(key)
    if value is None or isinstance(value, list):
        return None
    text = unquote(value)
    return text if text != "" else None


def check_page(rel, report, opts, index, tracked, embed_names):
    """1 ページを検査し、重複判定に使う address を返す(無ければ None)。"""
    text = read_page(rel)
    if text is None:
        report.error("TARGET", "ページを読めない", rel)
        return None
    report.counters["pages"] += 1
    is_tracked = tracked is not None and rel in tracked

    parts = split_frontmatter(text)
    if parts is None:
        head = text.split("\n", 1)[0].strip()
        report.error("FM-PARSE",
                     "frontmatter が `---` で始まっていない" if head != "---"
                     else "frontmatter の閉じ `---` が無い", rel)
        fm_lines, body_lines, close_at = [], text.split("\n"), -1
        fm = {}
    else:
        fm_lines, body_lines, close_at = parts
        fm = frontmatter_keys(fm_lines)
        check_frontmatter(rel, fm, report, opts, is_tracked)

    title = scalar(fm, "title") or ""
    page_type = scalar(fm, "type") or ""
    source_type = scalar(fm, "source_type") or ""
    body_first_lineno = close_at + 2

    check_headings(rel, title, page_type, body_lines, body_first_lineno, report)
    check_links(rel, fm_lines, body_lines, body_first_lineno,
                title, report, index, embed_names)
    check_length(rel, page_type, source_type, text, report, opts)
    if opts.require_related and page_type == "source":
        check_required_related(rel, fm, opts.require_related, report)
    return scalar(fm, "address")


def check_required_related(rel, fm, required, report):
    """章 source の `related` がハブ(book entity / thesis hub)を指すか見る。"""
    want = nfc(link_stem(link_target(strip_wikilink(required)))).casefold()
    for value in related_values(fm):
        target = link_target(strip_wikilink(unquote(value)))
        if nfc(link_stem(target)).casefold() == want:
            return
    report.error("FM-RELATED", "related が %s を指していない" % required, rel)


def check_duplicate_addresses(addresses, report):
    """対象ページの address が wiki 内で重複していないか見る。

    `wiki/` 全ページを開く走査なので、対象が address を 1 つも持たないときは
    索引そのものを作らない(M5: 走査コストを検査の必要性に紐づける)。
    """
    if not any(addresses.values()):
        return
    addr_map = build_address_map()
    for rel, address in addresses.items():
        if not address:
            continue
        others = [p for p in addr_map.get(address, []) if p != rel]
        if others:
            report.error("ADDR-DUP", "address %s が %s と重複している"
                         % (address, ", ".join(sorted(others)[:3])), rel)


def related_values(fm):
    """`related` を要素列にほどく。ブロック形式もインライン配列も受ける。

    `frontmatter_keys` は `related: ["[[A]]", "[[B]]"]` を 1 つの文字列で返すので、
    そのまま突き合わせると `[[A]]` を指していても不一致になる。要素分割は
    `wiki-append.py` の `parse_inline_list` を再利用する(角括弧の深さと引用符を
    数えるので、題名に読点やカンマを含む wikilink でも壊れない)。
    """
    related = fm.get("related")
    if related is None:
        return []
    if isinstance(related, list):
        return related
    items = _load_appender().parse_inline_list(related)
    return [related] if items is None else items


def strip_wikilink(text):
    text = text.strip()
    if text.startswith("[[") and text.endswith("]]"):
        return text[2:-2]
    return text


def check_headings(rel, title, page_type, body_lines, first_lineno, report):
    h1 = []
    for lineno, line in scan_body(body_lines, first_lineno):
        if line.startswith("# "):
            h1.append((lineno, line[2:].strip()))
            continue
        if not line.startswith("## "):
            continue
        heading = line[3:].strip()
        if page_type == "source" and any(w in heading for w in VERIFY_SECTION_WORDS):
            report.error("SEC-VERIFY",
                         "source 本文に検証結果の節がある: `## %s`"
                         "(検査結果はチャットへ報告する)" % heading, rel, lineno)
        if page_type == "concept" and heading not in FIXED_SECTIONS \
                and is_generic_section(heading):
            report.error("SEC-NAME",
                         "concept の節名に生成過程の語がある: `## %s`"
                         "(conventions §8 主題節の規則 1)" % heading, rel, lineno)
    if not h1:
        report.error("H1-COUNT", "H1 見出しが無い", rel)
    elif len(h1) > 1:
        report.error("H1-COUNT", "H1 見出しが %d 個ある(1 つにする)" % len(h1),
                     rel, h1[1][0])
    elif title and nfc(h1[0][1]) != nfc(title):
        report.warn("H1-TITLE", "H1 が frontmatter の title と一致しない: %r ≠ %r"
                    % (h1[0][1], title), rel, h1[0][0])


def check_links(rel, fm_lines, body_lines, first_lineno,
                title, report, index, embed_names):
    self_names = {nfc(link_stem(rel)).casefold()}
    if title:
        self_names.add(nfc(title).casefold())

    numbered = frontmatter_link_lines(fm_lines)
    numbered += scan_body(body_lines, first_lineno)

    for lineno, raw in numbered:
        line = strip_code(raw)
        is_nav = line.lstrip().startswith("> ")
        for bang, inner in WIKILINK_RE.findall(line):
            target = link_target(inner)
            if bang == "!":
                report.counters["embeds"] += 1
                embed_names.add(PurePosixPath(target).name.casefold())
                if not resolve_embed(target, rel, index):
                    report.counters["unresolved_embeds"] += 1
                    report.error("EMBED", "埋め込みが解決しない: [[%s]]" % inner,
                                 rel, lineno)
                continue
            if not resolve_link(target, rel, index):
                report.counters["unresolved_links"] += 1
                check = "NAV-LINK" if is_nav else "LINK"
                report.error(check, "リンクが解決しない: [[%s]]" % inner, rel, lineno)
            elif is_self_ref(target, rel, self_names):
                report.warn("SELF-REF", "自分自身を [[%s]] で参照している" % inner,
                            rel, lineno)
        for src in MD_IMAGE_RE.findall(line):
            target = nfc(url_unquote(src.split(" ", 1)[0].strip()))
            if URL_RE.match(target):
                continue
            report.counters["embeds"] += 1
            embed_names.add(PurePosixPath(target).name.casefold())
            if not resolve_embed(target, rel, index):
                report.counters["unresolved_embeds"] += 1
                report.error("EMBED", "埋め込みが解決しない: ![](%s)" % src, rel, lineno)


def frontmatter_link_lines(fm_lines):
    """frontmatter のうちリンクを検査する行だけを (行番号, 行) で返す。

    対象は `LINKED_FM_KEYS`(`related` / `sources` / `asks` / `brief`)。
    `title` などの本文的な値に `[[...]]` を含む書き方があり、それを
    未解決 error にすると偽陽性になる。
    """
    out = []
    key = None
    for offset, line in enumerate(fm_lines):
        match = FM_KEY_RE.match(line)
        if match:
            key, rest = match.group(1), match.group(2)
            if key in LINKED_FM_KEYS and rest.strip():
                out.append((offset + 2, line))
        elif key in LINKED_FM_KEYS and FM_ITEM_RE.match(line):
            out.append((offset + 2, line))
    return out


def check_length(rel, page_type, source_type, text, report, opts):
    lines = len(text.rstrip("\n").split("\n"))
    if lines > MAX_LINES:
        if page_type == "concept":
            report.warn("LEN-MAX", "%d 行(300 行は分割か再編纂を検討する目安。"
                        "追記は止めない。conventions §7)" % lines, rel)
        else:
            report.warn("LEN-MAX", "%d 行(source / entity は 300 行を上限として"
                        "分割を検討する。conventions §7)" % lines, rel)
    if page_type != "source":
        return
    if opts.min_lines is not None:
        floor = opts.min_lines
    elif source_type == "book":
        floor = MIN_BOOK_SOURCE_LINES
    else:
        floor = MIN_SOURCE_LINES
    if lines < floor:
        report.warn("LEN-MIN", "%d 行(source は %d 行を下限の目安とする。"
                    "conventions §7)" % (lines, floor), rel)


# ------------------------------------------------- attachment の逆方向照合

def check_attachments(slug, targets, report, embed_names):
    base = VAULT_ROOT / "wiki/sources/_attachments" / slug
    if not base.is_dir():
        report.error("ATTACH-DIR", "attachment ディレクトリが無い",
                     f"wiki/sources/_attachments/{slug}")
        return
    bodies = []
    for rel in targets:
        text = read_page(rel)
        if text is not None:
            bodies.append(nfc(text))
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        name = nfc(path.name)
        rel = path.relative_to(VAULT_ROOT).as_posix()
        if name.casefold() in embed_names:
            continue
        if any(name in body for body in bodies):
            continue
        report.counters["unused_attachments"] += 1
        report.warn("ATTACH-UNUSED", "対象集合のどこからも参照されていない", rel)


# ------------------------------------------------------- 図表 ID の網羅照合

def iter_figure_ids(text):
    """本文中の図表 ID を出現順に yield する(展開後)。

    `Figures 2 and 3` `Figs. 2, 3 and 5` のような並列言及は各 ID へ展開し、
    複数形のラベルに限って `Figures 2-4` を範囲として開く(単数形の
    `Figure 4-1` と `図 4-1` は書籍の「章-連番」なので開かない)。
    """
    for match in FIGURE_ID_RE.finditer(text):
        label = match.group(1) or match.group(2)
        key = FIGURE_LABELS.get(label.lower(), label)
        plural = label.lower() in FIGURE_PLURALS
        numbers = list(expand_figure_number(match.group(3), plural))
        end = match.end()
        while True:
            more = FIGURE_MORE_RE.match(text, end)
            if more is None:
                break
            end = more.end()
            # `Fig. 1, 2020` のような並置の年号を図番号にしない。
            if int(re.match(r"\d+", more.group(1)).group(0)) >= 1000:
                break
            numbers += expand_figure_number(more.group(1), plural)
        for num in numbers:
            yield "%s %s" % (key, num)


def figure_ids(text):
    """本文中の `Figure N` / `Fig. N` / `Table N` / `図N-M` を正規化して集める。"""
    return set(iter_figure_ids(text))


def figure_id_counts(text):
    """図表 ID の出現回数。展開規則は `figure_ids()` と同じ。"""
    counts = {}
    for fid in iter_figure_ids(text):
        counts[fid] = counts.get(fid, 0) + 1
    return counts


def expand_figure_number(number, plural):
    """`2-4` を 2・3・4 に開く。開く条件を満たさなければそのまま返す。"""
    match = FIGURE_RANGE_RE.match(number) if plural else None
    if match is None:
        return [number]
    start, stop = int(match.group(1)), int(match.group(2))
    if not 0 < stop - start <= FIGURE_RANGE_MAX:
        return [number]
    return [str(n) for n in range(start, stop + 1)]


def strip_references(text):
    """参考文献部以降を落とす。見つからなければ全文を返す。

    参考文献の一覧には引用先論文の題名として `Figure` を含む行が混ざり、
    そのまま拾うと本文が参照していない ID を `FIG-MISSING` に並べてしまう。
    冒頭の目次にも `References` が現れうるので、**最後に現れた見出し行**を
    切れ目とする。
    """
    last = None
    for match in REFERENCES_HEAD_RE.finditer(text):
        last = match
    return text if last is None else text[:last.start()]


def list_figure_ids_from(raw_path):
    """raw から図表 ID と出現回数を集める。参考文献部は落とす。

    戻り値は (id → 回数 の dict, 読み取りエラー文字列 or None)。
    """
    try:
        raw_text = Path(raw_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, "raw テキストを読めない: %s" % exc
    return figure_id_counts(strip_references(nfc(raw_text))), None


def emit_figure_id_list(counts, as_json):
    """`--list-figure-ids` の人間向け / JSON 出力。終了コードは常に 0。"""
    items = sorted(counts.items())
    captions_like = sum(1 for _, n in items if n == 1)
    summary = {"figure_ids": len(items), "captions_like": captions_like}
    if as_json:
        print(json.dumps({
            "ids": [{"id": fid, "count": n} for fid, n in items],
            "counts": {fid: n for fid, n in items},
            "summary": summary,
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    for fid, n in items:
        print("%s\t%s" % (fid, n))
    print("figure_ids=%s captions_like=%s"
          % (summary["figure_ids"], summary["captions_like"]))
    return EXIT_OK


class CleanupRefused(Exception):
    """images ディレクトリがシンボリックリンクなど、掃除してはいけない対象。"""


def _is_link(path):
    """シンボリックリンクなら True。`is_file()` より先に見る(辿らない)。"""
    path = Path(path)
    return path.is_symlink() or os.path.islink(str(path))


def _is_regular_file(path):
    """通常ファイルだけ。リンクは辿らず False。"""
    path = Path(path)
    if _is_link(path):
        return False
    return path.is_file()


def _count_regular_images(root):
    return sum(1 for path in Path(root).glob("image-*.png")
               if _is_regular_file(path))


def cleanup_extracted_images(images_dir):
    """fetch が今作った images ディレクトリから page-render を除く。

    ディレクトリ自体がシンボリックリンクなら拒否する(消さない・JSON も
    触らない)。通常ファイルの `page-*.png` だけを消す。リンクは辿らない。
    JSON は消す前に読む。根が dict でない・壊れているときは書き戻さない。
    `images` が list でなければ空 list として扱い、根が dict なら他キーを
    保って書き戻す。戻り値は (残存数, 警告 or None)。
    """
    root = Path(images_dir)
    if _is_link(root):
        raise CleanupRefused("images ディレクトリがシンボリックリンクである: %s" % root)
    if not root.is_dir():
        raise FileNotFoundError("images ディレクトリが無い: %s" % root)

    manifest = root / "images.json"
    data = None
    warning = None
    if _is_regular_file(manifest):
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warning = "images.json が壊れている: %s" % exc
            parsed = None
        if parsed is not None and not isinstance(parsed, dict):
            warning = "images.json の根が dict でない"
            parsed = None
        if isinstance(parsed, dict):
            images = parsed.get("images")
            if not isinstance(images, list):
                images = []
            kept = []
            for item in images:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                name = str(item.get("file", ""))
                if kind == "page-render" or name.startswith("page-"):
                    continue
                kept.append(item)
            parsed["images"] = kept
            parsed["images_count"] = len(kept)
            data = parsed
    elif _is_link(manifest):
        warning = "images.json がシンボリックリンクである"

    for path in root.glob("page-*.png"):
        if not _is_regular_file(path):
            continue
        try:
            path.unlink()
        except OSError as exc:
            log("warn: page を消せない: %s" % exc)

    if data is not None and not _is_link(manifest):
        try:
            manifest.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError as exc:
            warning = "images.json を書けない: %s" % exc
            data = None

    if data is not None:
        remaining = data["images_count"]
    else:
        remaining = _count_regular_images(root)
    return remaining, warning


def emit_cleanup_images(images_dir, as_json):
    try:
        remaining, warning = cleanup_extracted_images(images_dir)
    except (CleanupRefused, FileNotFoundError) as exc:
        log("ERR: 画像掃除を拒否した: %s" % exc)
        return EXIT_USAGE
    if warning:
        log("warn: %s" % warning)
    if as_json:
        print(json.dumps({"images_count": remaining}, ensure_ascii=False))
    else:
        print("images_count=%d" % remaining)
    return EXIT_OK


def check_figure_ids(raw_path, targets, report):
    """raw 原本にある図表 ID が対象ページに全て現れるかを照合する。

    wiki-ingest-paper §1.5 の「本文が参照する図表は全件載せる」規律の機械化で、
    raw にあって source に無い ID を `FIG-MISSING` として列挙する。逆向き
    (source にしか無い ID)は本文の言い換えで普通に起きるので見ない。
    """
    try:
        raw_text = Path(raw_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.error("FIG-RAW", "raw テキストを読めない: %s" % exc, str(raw_path))
        return
    wanted = figure_ids(strip_references(nfc(raw_text)))
    found = set()
    for rel in targets:
        text = read_page(rel)
        if text is not None:
            found |= figure_ids(nfc(text))
    missing = sorted(wanted - found)
    report.counters["figure_ids"] = len(wanted)
    report.counters["figure_ids_missing"] = len(missing)
    for fid in missing:
        report.warn("FIG-MISSING", "raw にあるが対象ページに現れない: %s" % fid)


# --------------------------------------------------------- 対象集合の解決

def is_catalog(rel):
    return rel in CATALOG_RELS or PurePosixPath(rel).name == "_index.md"


def collect_targets(opts, report):
    """(対象ページ列, 対象外だった差分ファイル列) を返す。"""
    targets = []
    seen = set()
    others = []

    def add(rel):
        rel = nfc(rel)
        if rel in seen:
            return
        seen.add(rel)
        targets.append(rel)

    for raw in opts.pages or []:
        rel = to_relative(raw)
        if rel is None:
            report.error("TARGET", "vault の外を指している: %s" % raw)
            continue
        if not (VAULT_ROOT / rel).is_file():
            report.error("TARGET", "ページが存在しない", rel)
            continue
        add(rel)

    for pattern in opts.glob or []:
        hits = sorted(globlib.glob(str(VAULT_ROOT / pattern), recursive=True))
        matched = [h for h in hits if h.endswith(".md") and Path(h).is_file()]
        if not matched:
            report.error("TARGET", "glob が 1 件も一致しない: %s" % pattern)
        for hit in matched:
            add(Path(hit).relative_to(VAULT_ROOT).as_posix())

    if opts.match or opts.from_git_status:
        entries = git_status_entries()
        if entries is None:
            report.error("TARGET", "git status を取得できない"
                                   "(--match / --from-git-status には git が要る)")
            entries = []
        for status, rel in entries:
            kind = classify_status(status)
            if kind == "gone":
                others.append((status, rel))
                continue
            if kind != "changed" or not rel.endswith(".md"):
                continue
            if is_catalog(rel):
                others.append((status, rel))
                continue
            text = read_page(rel)
            if text is None:
                others.append((status, rel))
                continue
            if opts.match and nfc(opts.match) not in nfc(text):
                others.append((status, rel))
                continue
            add(rel)

    return targets, others


def to_relative(raw):
    text = str(raw).strip()
    if not text:
        return None
    if text.startswith("/"):
        try:
            return Path(text).resolve().relative_to(VAULT_ROOT).as_posix()
        except ValueError:
            return None
    if ".." in Path(text).parts:
        return None
    return Path(text).as_posix()


# ---------------------------------------------------- 共有カタログと staged

def catalog_paths():
    rels = [r for r in CATALOG_RELS if (VAULT_ROOT / r).is_file()]
    for path in sorted((VAULT_ROOT / "wiki").glob("*/_index.md")):
        rels.append(path.relative_to(VAULT_ROOT).as_posix())
    return rels


def check_catalog(opts, report):
    """カタログの追加行数を出す。他セッションの追記が乗っていないかの目安。"""
    rels = catalog_paths()
    if not rels:
        return
    # HEAD を基準にすると stage 済みの追記も数えられる。カタログのパスは
    # ASCII なので `-z` を使わず行で読む。
    args = ["diff", "--numstat"]
    if git_out(["rev-parse", "--verify", "HEAD"]) is not None:
        args.append("HEAD")
    out = git_out(args + ["--"] + rels)
    if out is None:
        report.warn("CATALOG", "git diff --numstat を取得できない")
        return
    total = 0
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 3 or not cols[0].isdigit():
            continue
        total += int(cols[0])
        report.info("CATALOG", "+%s 行" % cols[0], nfc(cols[2]))
    report.counters["catalog_lines"] = total
    if opts.expect_catalog_lines is not None and total != opts.expect_catalog_lines:
        report.warn("CATALOG", "カタログの追加行数が %d 行、期待は %d 行"
                    "(他セッションの追記が乗っている可能性がある)"
                    % (total, opts.expect_catalog_lines))


def expected_staged(opts, targets, report):
    entries = []
    if opts.expect_files:
        try:
            raw = Path(opts.expect_files).read_text(encoding="utf-8")
        except OSError as exc:
            report.error("STAGED-MISS", "--expect-files を読めない: %s" % exc)
            return None
        entries = [x.strip() for x in raw.split("\n") if x.strip()]
    else:
        entries = list(targets)
    entries += list(opts.extra_files or [])
    out = []
    for raw in entries:
        rel = to_relative(raw)
        if rel is None:
            report.error("STAGED-MISS", "vault の外を指している: %s" % raw)
            continue
        if str(raw).rstrip().endswith("/") or (VAULT_ROOT / rel).is_dir():
            out.append(rel.rstrip("/") + "/")
        else:
            out.append(rel)
    return out


def check_staged(opts, targets, report):
    expected = expected_staged(opts, targets, report)
    if expected is None:
        return
    staged = git_lines(["diff", "--cached", "--name-only", "-z"])
    if staged is None:
        report.error("STAGED-EXTRA", "git diff --cached を取得できない")
        return
    staged = [nfc(x) for x in staged]
    files = {e for e in expected if not e.endswith("/")}
    prefixes = sorted(e for e in expected if e.endswith("/"))

    covered_prefix = set()
    extra = []
    for rel in staged:
        if rel in files:
            continue
        hit = next((p for p in prefixes if rel.startswith(p)), None)
        if hit is not None:
            covered_prefix.add(hit)
            continue
        extra.append(rel)
    missing = [rel for rel in sorted(files) if rel not in set(staged)]
    missing += [p for p in prefixes if p not in covered_prefix]

    for rel in extra:
        report.error("STAGED-EXTRA", "期待集合に無い staged ファイル", rel)
    for rel in sorted(missing):
        report.error("STAGED-MISS", "期待集合のうち staged されていない", rel)
    report.counters["staged_extra"] = len(extra)
    report.counters["staged_missing"] = len(missing)


# -------------------------------------------------------------------- 出力

def format_finding(item):
    where = " %s" % item["path"] if item["path"] else ""
    if item["path"] and item.get("line"):
        where += ":%d" % item["line"]
    return "%s %s%s: %s" % (item["severity"], item["id"], where, item["message"])


def summary_line(counters, order):
    return " ".join("%s=%s" % (k, counters[k]) for k in order if k in counters)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ingest 対象集合を機械的に検証する(読み取り専用)。")
    parser.add_argument("--pages", nargs="+", metavar="P",
                        help="vault 相対パス(複数可)")
    parser.add_argument("--glob", nargs="+", metavar="G",
                        help="vault ルート起点の glob(複数可)")
    parser.add_argument("--match", metavar="STR",
                        help="git status の wiki 差分のうち本文に STR を含むページ")
    parser.add_argument("--from-git-status", action="store_true",
                        help="git status に出る wiki 配下の新規・更新ページ全部")
    parser.add_argument("--expect-count", type=int, metavar="N",
                        help="対象 source ページ数の期待値(章数)")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="この日付タグが tags にあることを確認する")
    parser.add_argument("--attachments-slug", metavar="SLUG",
                        help="wiki/sources/_attachments/SLUG の未使用ファイルを見る")
    parser.add_argument("--figure-ids-from", metavar="RAW",
                        help="raw テキストの図表 ID と対象ページの図表 ID を照合する")
    parser.add_argument("--list-figure-ids", metavar="RAW",
                        help="raw の図表 ID と出現回数を出す(対象ページ不要・終了 0)")
    parser.add_argument("--cleanup-images", metavar="DIR",
                        help="DIR の page-*.png と page-render 項目を除く")
    parser.add_argument("--require-related", metavar="LINK",
                        help="対象 source 全ページの related がこのリンクを含むこと")
    parser.add_argument("--min-lines", type=int, metavar="N",
                        help="source の行数下限(既定は book 30 行・他 100 行)")
    parser.add_argument("--classify", action="store_true",
                        help="対象外だった差分ファイルと共有カタログの追加行数を出す")
    parser.add_argument("--expect-catalog-lines", type=int, metavar="N",
                        help="共有カタログの追加行数の期待値")
    parser.add_argument("--staged-check", action="store_true",
                        help="git diff --cached の集合と期待集合を突き合わせる")
    parser.add_argument("--expect-files", metavar="FILE",
                        help="期待する staged 集合(1 行 1 パス)。対象集合の代わりに使う")
    parser.add_argument("--extra-files", nargs="+", metavar="F",
                        help="期待集合に足すパス。末尾 `/` はディレクトリ前方一致")
    parser.add_argument("--json", action="store_true", help="機械可読な JSON で出す")
    parser.add_argument("--quiet", action="store_true", help="error と summary だけ出す")
    parser.add_argument("--max-findings", type=int, default=50, metavar="N",
                        help="所見の打ち切り件数(既定 50)")
    opts = parser.parse_args(argv)

    has_selector = bool(opts.pages or opts.glob or opts.match or opts.from_git_status)
    # `--staged-check --expect-files` は期待集合を外から与えるので、
    # コミット直前の照合だけを単独で回せる(ページ検査は Step 3.5 で済んでいる)。
    staged_only = bool(opts.staged_check and opts.expect_files)
    list_only = bool(opts.list_figure_ids)
    cleanup_only = bool(opts.cleanup_images)
    if list_only and cleanup_only:
        log("ERR: --list-figure-ids と --cleanup-images は同時に使わない")
        return EXIT_USAGE
    if list_only:
        counts, err = list_figure_ids_from(opts.list_figure_ids)
        if err is not None:
            log("ERR: %s" % err)
            return EXIT_USAGE
        return emit_figure_id_list(counts, opts.json)
    if cleanup_only:
        return emit_cleanup_images(opts.cleanup_images, opts.json)
    if not has_selector and not staged_only:
        log("ERR: --pages / --glob / --match / --from-git-status のいずれかが要る"
            "(--list-figure-ids / --cleanup-images / "
            "--staged-check --expect-files のときは省略できる)")
        return EXIT_USAGE
    if opts.date and not DAY_RE.match(opts.date):
        log("ERR: --date は YYYY-MM-DD 形式である")
        return EXIT_USAGE
    if opts.max_findings < 1:
        log("ERR: --max-findings は 1 以上である")
        return EXIT_USAGE
    if opts.expect_catalog_lines is not None and not opts.classify:
        log("ERR: --expect-catalog-lines は --classify と併せて使う")
        return EXIT_USAGE
    if (opts.expect_files or opts.extra_files) and not opts.staged_check:
        log("ERR: --expect-files / --extra-files は --staged-check と併せて使う")
        return EXIT_USAGE
    if opts.min_lines is not None and opts.min_lines < 0:
        log("ERR: --min-lines は 0 以上である")
        return EXIT_USAGE
    if opts.figure_ids_from and not has_selector:
        log("ERR: --figure-ids-from は対象集合の指定と併せて使う")
        return EXIT_USAGE

    report = Report()
    targets, others = collect_targets(opts, report)
    if has_selector and not targets:
        report.error("TARGET", "対象ページが 1 件も無い(指定を見直す)")

    embed_names = set()
    if targets:
        # vault 全体の索引は 1 回だけ作る(対象が空なら作らない)。
        index = build_index()
        tracked = tracked_files()
        addresses = {}
        for rel in targets:
            addresses[rel] = check_page(rel, report, opts, index,
                                        tracked, embed_names)
        check_duplicate_addresses(addresses, report)

    if opts.attachments_slug:
        check_attachments(opts.attachments_slug, targets, report, embed_names)

    if opts.figure_ids_from:
        check_figure_ids(opts.figure_ids_from, targets, report)

    if opts.expect_count is not None:
        found = sum(1 for rel in targets if rel.startswith("wiki/sources/"))
        if found != opts.expect_count:
            report.error("COUNT", "対象 source ページが %d 件、期待は %d 件"
                         % (found, opts.expect_count))

    if opts.classify:
        for status, rel in others:
            report.info("CLASSIFY-OTHER", "対象外(%s)" % status.strip(), rel)
        report.counters["other_changed"] = len(others)
        check_catalog(opts, report)

    if opts.staged_check:
        check_staged(opts, targets, report)

    counters = report.counters
    counters["errors"] = sum(1 for f in report.findings if f["severity"] == "ERROR")
    counters["warnings"] = sum(1 for f in report.findings if f["severity"] == "WARN")

    shown = sorted(report.findings,
                   key=lambda f: (SEV_ORDER[f["severity"]], f["id"],
                                  f["path"] or "", f["line"] or 0))
    if opts.quiet:
        shown = [f for f in shown if f["severity"] == "ERROR"]
    truncated = max(0, len(shown) - opts.max_findings)
    shown = shown[:opts.max_findings]
    if truncated:
        counters["truncated"] = truncated

    # 常に出す基本キーと、その検査を実行したときだけ出すキー。
    # `unresolved_embeds` は図表網羅の確認キーなので 0 でも必ず出す。
    order = ["pages", "errors", "warnings", "embeds", "unresolved_links",
             "unresolved_embeds", "unused_attachments"]
    for key in ("figure_ids", "figure_ids_missing", "other_changed",
                "catalog_lines", "staged_extra", "staged_missing", "truncated"):
        if key in counters:
            order.append(key)

    if opts.json:
        print(json.dumps({
            "targets": targets,
            "findings": shown,
            "summary": {k: counters[k] for k in order},
            "truncated": truncated,
        }, ensure_ascii=False, indent=2))
    else:
        for item in shown:
            print(format_finding(item))
        print(summary_line(counters, order))

    return EXIT_FOUND if counters["errors"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
