#!/usr/bin/env python3
"""wiki-survey-check.py — wiki/surveys/ の長編ページに対する機械検査と実測。

`wiki-survey` skill のフェーズ 6(検証)で手で打っていた検査(wikilink の実在、画像パスの実在、
ダッシュと中黒、LLM 口調、同一出典の連続一文一段落、図番号の連番、frontmatter と本文の
sources の同期)を 1 コマンドに束ねる。何も書き換えない。判定を要する項目は WARN で場所を
列挙し、判断は人が行う。

`stats` は `wiki/surveys/` 全ページの行数・sources 本数・図表点数・H2 本数を出す。
skill 本文に実測の数字を書き写さず、必要なときにこれを叩く。

使い方:
  python3 scripts/wiki-survey-check.py check wiki/surveys/<タイトル>.md
  python3 scripts/wiki-survey-check.py check wiki/surveys/<タイトル>.md --json
  python3 scripts/wiki-survey-check.py check wiki/surveys/<タイトル>.md --only wikilinks,images
  python3 scripts/wiki-survey-check.py stats
  python3 scripts/wiki-survey-check.py stats --json

検査(名前):
  frontmatter          type: survey、question、address、sources、title のダッシュ、日付タグ
  wikilinks            `[[...]]` の実在(`![[` は除く。wiki/ と structures/ とパス指定だけを探す)
  images               `![[...]]` の実ファイル存在(`_attachments/` は wiki/sources/ 配下)
  dashes               本文の em ダッシュと 2 倍ダッシュ(wikilink と code fence の内側は除く)
  nakaguro             中黒。カタカナに挟まれたもの(人名の可能性)は WARN、それ以外は FAIL
  llm_phrases          japanese-tech-writing が禁じる空句。既定 4 語は FAIL、拡張語は WARN
  single_source_runs   同一の単一出典だけを引く一文段落が 3 つ以上連続(WARN。判断は人が行う)
  figures              図N-M / 表N-M の章内連番、重複、出典行の原本図表番号、同一画像の二重貼付
  sources_sync         frontmatter の sources と本文の `[[@...]]` の差分
  sections             空の節(見出し直後に同格以上の見出し)と、セリフ型の可能性がある見出し

終了コード: 0 FAIL なし / 1 FAIL あり / 2 使い方の誤り
環境変数 `WIKI_VAULT_ROOT` で vault ルートを差し替えられる(試験用)。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

VAULT_ROOT = Path(
    os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent
).resolve()

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# wikilink の解決先。papers/ は含めない(`@` を落とした source リンクは papers/ 側へ誤解決する。conventions §4)。
LINK_SEARCH_DIRS = (
    "wiki/sources",
    "wiki/concepts",
    "wiki/entities",
    "wiki/surveys",
    "wiki/questions",
    "wiki/asks",
    "wiki/briefs",
    "wiki/meta",
    "wiki",
    "structures",
)

# japanese-tech-writing「LLM っぽい表現の禁止」。skill が従来 FAIL にしてきた 4 語と、拡張語(WARN)。
LLM_PHRASES_FAIL = ("重要なのは", "正面から", "多角的", "掘り下げ")
LLM_PHRASES_WARN = ("深掘り", "に他ならない", "包括的", "総合的", "不可欠", "核心的", "鍵となる", "極めて", "非常に")

DASH_RE = re.compile(r"[—―]")
EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
# `![[` を除いた wikilink。直前が `!` でないことを lookbehind で保証する。
LINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
SOURCE_LINE_RE = re.compile(r"\(Source: (\[\[.*?\]\](?:, \[\[.*?\]\])*)\)\s*。?\s*$")
CAPTION_RE = re.compile(r"^\*\*(図|表)(\d+)-(\d+)\*\*")
SELF_CAPTION_RE = re.compile(r"^(図|表)[\s\u3000]*\d")  # 自作図「図 N 」(figures.md §4)
FIGURE_SOURCE_RE = re.compile(r"^\(Source: \[\[[^\]]+\]\](?:, ([^)]*))?\)")
FIGURE_LOCATOR_RE = re.compile(r"(図|表|Figure|Fig\.|Table|p\.|frame-)\s*\d*")
KATAKANA_RE = re.compile(r"[\u30A0-\u30FF]")
DATE_TAG_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
ADDRESS_RE = re.compile(r"^c-\d{6}$")
# 見出し末尾が活用語尾なら「セリフ型」の可能性。名詞止めと疑問(か)は対象外。
SPEECH_HEADING_RE = re.compile(r"(る|う|く|す|つ|ぬ|ぶ|む|ぐ|ず|い|た|だ|ない)\s*$")
# 活用語尾と同じ字で終わる名詞(問い、違い、流れ、狙い、限り、動き、切り替え 等)。
NOUN_ENDING_RE = re.compile(
    r"(問い|違い|狙い|誤り|流れ|備え|遅れ|扱い|疑い|戦い|振る舞い|振れ|揺れ|ずれ|限り|終わり|始まり|"
    r"向き|動き|働き|残り|切り替え|置き換え|取り扱い|使い|支払い|扉|構え|控え|訴え|教え|答え|考え|"
    r"見え|冴え|頼り|誤り|語り|集まり|繋がり|つながり|手がかり|足がかり|手触り|裏返し|見直し|洗い出し|"
    r"割り当て|組み立て|手立て|見立て|申し立て|建て|立て)\s*$"
)

ALL_CHECKS = (
    "frontmatter",
    "wikilinks",
    "images",
    "dashes",
    "nakaguro",
    "llm_phrases",
    "single_source_runs",
    "figures",
    "sources_sync",
    "sections",
)


# ---------------------------------------------------------------------------
# 入出力
# ---------------------------------------------------------------------------


class Finding:
    def __init__(self, check: str, level: str, message: str, locations=None):
        self.check = check
        self.level = level  # OK | INFO | WARN | FAIL
        self.message = message
        self.locations = list(locations or [])

    def to_dict(self):
        return {
            "check": self.check,
            "level": self.level,
            "message": self.message,
            "locations": self.locations,
        }


def split_frontmatter(text: str):
    """(frontmatter 行, 本文行, 本文の開始行番号(1 始まり)) を返す。frontmatter が無ければ None。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], lines[i + 1 :], i + 2
    return None


def frontmatter_keys(fm_lines):
    """flat YAML を dict に。リストは list、スカラーは str。"""
    keys = {}
    key = None
    for line in fm_lines:
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item is not None and key is not None:
            keys.setdefault(key, []).append(item.group(1).strip())
            continue
        kv = re.match(r"^([A-Za-z0-9_/-]+):\s*(.*)$", line)
        if kv is None:
            continue
        key = kv.group(1)
        raw = kv.group(2).strip()
        if raw in ("", "[]"):
            keys.setdefault(key, [])
        else:
            keys[key] = raw
    return keys


def unquote(value) -> str:
    s = str(value).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def link_target(raw: str) -> str:
    """`[[target|alias#anchor]]` の中身から target だけを取り出す。

    表のセル内では `\\|` でパイプを逃がす(`[[@...\\|略称]]`)ので、逃がしを外してから切る。
    """
    t = raw.replace("\\|", "|").split("|", 1)[0].split("#", 1)[0].strip()
    return t


def body_without_fences(body_lines):
    """code fence の内側を空行に置き換えた本文(行番号を保つ)。"""
    out = []
    fence = None
    for line in body_lines:
        stripped = line.strip()
        if fence is not None:
            out.append("")
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            out.append("")
            continue
        out.append(line)
    return out


def strip_links(line: str) -> str:
    """wikilink(埋め込み含む)を取り除いた行。ファイル名内の中黒やダッシュを数えないため。"""
    return re.sub(r"!?\[\[[^\]]*\]\]", "", line)


# ---------------------------------------------------------------------------
# 各検査
# ---------------------------------------------------------------------------


def check_frontmatter(fm, page_rel):
    findings = []
    errors = []
    warns = []
    if unquote(fm.get("type") or "") != "survey":
        errors.append("`type: survey` でない: %r" % fm.get("type"))
    q = unquote(fm.get("question") or "")
    if not q:
        errors.append("`question:` が空。設問の逐語を入れる")
    addr = unquote(fm.get("address") or "")
    if not ADDRESS_RE.match(addr):
        errors.append("`address:` が c-NNNNNN 形式でない: %r" % addr)
    sources = fm.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("`sources:` が空。母集団の全件を並べる")
    else:
        bad = [s for s in sources if not unquote(s).startswith("[[")]
        if bad:
            errors.append("`sources:` に wikilink でない項目: %s" % ", ".join(bad[:3]))
        unquoted = [s for s in sources if not (s.startswith('"') or s.startswith("'"))]
        if unquoted:
            errors.append("`sources:` の wikilink がクォートされていない(conventions §2): %d 件" % len(unquoted))
    title = unquote(fm.get("title") or "")
    if not title:
        errors.append("`title:` が空")
    elif re.search(r"[—―–]| - ", title):
        errors.append("`title:` に副題をつなぐダッシュがある。単一句にする: %r" % title)
    tags = fm.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append("`tags:` が空。先頭に日付タグ YYYY/MM/DD")
    elif not DATE_TAG_RE.match(unquote(tags[0])):
        errors.append("`tags:` の先頭が日付タグでない: %r" % tags[0])
    elif "教科書" in title and "survey" in [unquote(t) for t in tags]:
        warns.append("教科書型に `survey` タグが付いている(skeletons §2: サーベイ型だけに付ける)")
    for key in ("created", "updated", "date"):
        if key not in fm:
            errors.append("`%s:` が無い" % key)
    if errors:
        findings.append(Finding("frontmatter", "FAIL", "; ".join(errors)))
    if warns:
        findings.append(Finding("frontmatter", "WARN", "; ".join(warns)))
    if not errors and not warns:
        findings.append(Finding("frontmatter", "OK", "type / question / address / sources / title / tags"))
    return findings


def _basename_index(root: Path):
    """LINK_SEARCH_DIRS 配下の .md を basename(拡張子なし)で索引する。"""
    index = {}
    for rel in LINK_SEARCH_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        # wiki/ 直下だけは再帰しない(サブディレクトリは個別に列挙済み)。
        it = d.glob("*.md") if rel == "wiki" else d.rglob("*.md")
        for p in it:
            if "_attachments" in p.parts:
                continue
            index.setdefault(p.stem, []).append(p)
    return index


def resolve_link(target: str, root: Path, index) -> bool:
    if not target or target.startswith("#"):
        return True
    t = target.strip()
    if (root / t).is_file():
        return True
    if (root / (t + ".md")).is_file():
        return True
    base = Path(t).name
    if base.endswith(".md"):
        base = base[:-3]
    return base in index


def check_wikilinks(body, root: Path):
    index = _basename_index(root)
    missing = {}
    for i, line in enumerate(body, 1):
        for m in LINK_RE.finditer(line):
            t = link_target(m.group(1))
            if not resolve_link(t, root, index):
                missing.setdefault(t, []).append(i)
    if missing:
        locs = ["L%s [[%s]]" % (",".join(str(x) for x in ls[:3]), t) for t, ls in sorted(missing.items())]
        return [Finding("wikilinks", "FAIL", "実在しない wikilink %d 件" % len(missing), locs)]
    total = sum(len(LINK_RE.findall(l)) for l in body)
    return [Finding("wikilinks", "OK", "wikilink %d 件すべて実在" % total)]


def embed_to_path(embed: str, root: Path) -> Path:
    e = embed.strip()
    if e.startswith("_attachments/"):
        return root / "wiki" / "sources" / e
    return root / e


def check_images(body, root: Path):
    missing = []
    total = 0
    for i, line in enumerate(body, 1):
        for m in EMBED_RE.finditer(line):
            total += 1
            p = embed_to_path(m.group(1), root)
            if not p.is_file():
                missing.append("L%d %s" % (i, m.group(1)))
    if missing:
        return [Finding("images", "FAIL", "実ファイルが無い埋め込み %d 件" % len(missing), missing)]
    return [Finding("images", "OK", "埋め込み %d 点すべて実在" % total)]


def check_dashes(body):
    hits = []
    for i, line in enumerate(body, 1):
        s = strip_links(line)
        if DASH_RE.search(s):
            hits.append("L%d %s" % (i, s.strip()[:60]))
    if hits:
        return [Finding("dashes", "FAIL", "ダッシュ %d 行" % len(hits), hits)]
    return [Finding("dashes", "OK", "ダッシュ 0 件")]


def _nakaguro_between_katakana(s: str, pos: int) -> bool:
    left = s[pos - 1] if pos > 0 else ""
    right = s[pos + 1] if pos + 1 < len(s) else ""
    return bool(KATAKANA_RE.match(left) and KATAKANA_RE.match(right))


def check_nakaguro(body):
    fail_hits = []
    warn_hits = []
    for i, line in enumerate(body, 1):
        s = strip_links(line)
        for pos, ch in enumerate(s):
            if ch != "・":
                continue
            ctx = s[max(0, pos - 12) : pos + 13].strip()
            if _nakaguro_between_katakana(s, pos):
                warn_hits.append("L%d …%s…" % (i, ctx))
            else:
                fail_hits.append("L%d …%s…" % (i, ctx))
    out = []
    if fail_hits:
        out.append(Finding("nakaguro", "FAIL", "並列の中黒 %d 件(読点に替える)" % len(fail_hits), fail_hits))
    if warn_hits:
        out.append(
            Finding(
                "nakaguro",
                "WARN",
                "カタカナに挟まれた中黒 %d 件。人名・固有名詞の内部なら許容し、並列なら読点に替える。判断を log に残す"
                % len(warn_hits),
                warn_hits,
            )
        )
    if not out:
        out.append(Finding("nakaguro", "OK", "中黒 0 件(wikilink 内を除く)"))
    return out


def check_llm_phrases(body):
    fail_hits = []
    warn_hits = []
    for i, line in enumerate(body, 1):
        s = strip_links(line)
        for ph in LLM_PHRASES_FAIL:
            if ph in s:
                fail_hits.append("L%d 「%s」 %s" % (i, ph, s.strip()[:50]))
        for ph in LLM_PHRASES_WARN:
            if ph in s:
                warn_hits.append("L%d 「%s」 %s" % (i, ph, s.strip()[:50]))
    out = []
    if fail_hits:
        out.append(Finding("llm_phrases", "FAIL", "禁止語 %d 件" % len(fail_hits), fail_hits))
    if warn_hits:
        out.append(
            Finding(
                "llm_phrases",
                "WARN",
                "空句の可能性 %d 件(引用や固有の用法なら残す。強調だけなら削る)" % len(warn_hits),
                warn_hits,
            )
        )
    if not out:
        out.append(Finding("llm_phrases", "OK", "禁止語 0 件"))
    return out


def single_source_runs(body, min_run: int = 3):
    """同一の単一出典だけを引く一文段落が min_run 以上連続する区間を返す。"""
    runs = []
    cur = []
    cur_src = None

    def flush():
        nonlocal cur, cur_src
        if len(cur) >= min_run:
            runs.append((cur[0], cur[-1], cur_src, len(cur)))
        cur, cur_src = [], None

    for i, line in enumerate(body, 1):
        s = line.strip()
        if not s:
            continue
        m = SOURCE_LINE_RE.search(s)
        if m and m.group(1) == cur_src:
            cur.append(i)
        elif m:
            flush()
            cur, cur_src = [i], m.group(1)
        else:
            flush()
    flush()
    return runs


def check_single_source_runs(body):
    runs = single_source_runs(body)
    if runs:
        locs = ["L%d-L%d (%d 文) %s" % (a, b, n, src[:60]) for a, b, src, n in runs]
        return [
            Finding(
                "single_source_runs",
                "WARN",
                "同一出典の連続一文一段落 %d 箇所。論点が 1 つなら段落を統合し出典を段落末へ集約する。論点が別なら残し、判断を log に残す"
                % len(runs),
                locs,
            )
        ]
    return [Finding("single_source_runs", "OK", "3 連続以上の区間なし")]


def check_figures(body):
    fails = []
    warns = []
    seq = {}  # (kind, chapter) -> list of (m, line)
    embeds = {}
    for i, line in enumerate(body, 1):
        for m in EMBED_RE.finditer(line):
            embeds.setdefault(m.group(1).strip(), []).append(i)
        cm = CAPTION_RE.match(line.strip())
        if cm:
            kind, chap, num = cm.group(1), int(cm.group(2)), int(cm.group(3))
            seq.setdefault((kind, chap), []).append((num, i))
            # キャプションは一文一行で複数行になる。段落(空行・見出し・次の埋め込みまで)の中に
            # (Source: [[...]], 図 N) の行があり、原本の図表番号を持つことを求める。
            src_line = None
            for j in range(i, len(body)):
                s = body[j].strip()
                if not s or s.startswith("#") or EMBED_RE.match(s):
                    break
                if FIGURE_SOURCE_RE.match(s):
                    src_line = (j + 1, s)
                    break
            if src_line is None:
                warns.append("L%d %s%d-%d の段落に (Source: [[@...]], 図 N) の出典行が無い" % (i, kind, chap, num))
            else:
                loc = FIGURE_SOURCE_RE.match(src_line[1]).group(1) or ""
                if not FIGURE_LOCATOR_RE.search(loc):
                    warns.append(
                        "L%d %s%d-%d の出典行に原本の図表番号(図 N / p.N / frame-NNN)が無い" % (src_line[0], kind, chap, num)
                    )
    # 章ごとに、図と表を別系列で採番しても(図3-1, 図3-2, 表3-1)、出現順の 1 系列で採番しても
    # (表3-1, 図3-2)よい。どちらかで 1 から連番になっていれば通す。重複は常に FAIL。
    chapters = sorted({chap for _, chap in seq})
    for chap in chapters:
        per_kind_ok = True
        union = []
        for kind in ("図", "表"):
            items = seq.get((kind, chap), [])
            nums = sorted(n for n, _ in items)
            dup = sorted({n for n in nums if nums.count(n) > 1})
            if dup:
                fails.append("%s%d-* に重複番号: %s" % (kind, chap, ", ".join(str(d) for d in dup)))
            if nums and nums != list(range(1, len(nums) + 1)):
                per_kind_ok = False
            union.extend(nums)
        union_ok = sorted(union) == list(range(1, len(union) + 1))
        if not per_kind_ok and not union_ok:
            shown = ", ".join(
                "%s%d-%d" % (kind, chap, n) for kind in ("図", "表") for n, _ in sorted(seq.get((kind, chap), []))
            )
            fails.append("第 %d 章の図表番号が連番でない: %s" % (chap, shown))
    for path, ls in embeds.items():
        if len(ls) > 1:
            fails.append("同じ画像が %d 回貼られている: %s (L%s)" % (len(ls), path, ",".join(str(x) for x in ls)))
    # 埋め込みの直後にキャプションが無い。転載図(_attachments/)は 1 件ずつ、
    # 自作画像(wiki/surveys/_attachments/<ページ名>/ をフルパスで貼る)は章扉絵として置かれることがあるので件数だけ報告する。
    uncaptioned_own = []
    for i, line in enumerate(body, 1):
        em = EMBED_RE.match(line.strip())
        if not em:
            continue
        has_caption = False
        for j in range(i, min(i + 3, len(body))):
            s = body[j].strip()
            if not s:
                continue
            plain = s.lstrip("*_")  # 自作図のキャプションを斜体にしたページがある
            if CAPTION_RE.match(s) or SELF_CAPTION_RE.match(plain):
                has_caption = True
            break
        if has_caption:
            continue
        if em.group(1).strip().startswith("_attachments/"):
            warns.append("L%d 転載図の直後にキャプション(**図N-M**:)が無い" % i)
        else:
            uncaptioned_own.append(i)
    if uncaptioned_own:
        warns.append(
            "自作画像 %d 点にキャプション(図 N)が無い(章扉絵なら許容し、判断を log に残す): L%s"
            % (len(uncaptioned_own), ",".join(str(x) for x in uncaptioned_own[:8]))
        )
    out = []
    if fails:
        out.append(Finding("figures", "FAIL", "図番号または貼付の問題 %d 件" % len(fails), fails))
    if warns:
        out.append(Finding("figures", "WARN", "キャプション・出典行の問題 %d 件" % len(warns), warns))
    if not out:
        n_emb = sum(len(v) for v in embeds.values())
        out.append(Finding("figures", "OK", "埋め込み %d 点。章内連番・出典行・二重貼付なし" % n_emb))
    return out


def source_basename(target: str) -> str:
    """`wiki/sources/@X.md` も `@X` も `@X` に揃える。"""
    base = Path(target).name
    if base.endswith(".md"):
        base = base[:-3]
    return base


def check_sources_sync(fm, body):
    listed = set()
    non_source = []
    for s in fm.get("sources") or []:
        t = unquote(s)
        m = re.match(r"^\[\[([^\]]+)\]\]$", t)
        if not m:
            continue
        target = source_basename(link_target(m.group(1)))
        if target.startswith("@"):
            listed.add(target)
        else:
            non_source.append(target)
    cited = set()
    for line in body:
        for m in LINK_RE.finditer(line):
            t = source_basename(link_target(m.group(1)))
            if t.startswith("@"):
                cited.add(t)
    out = []
    missing_in_fm = sorted(cited - listed)
    if missing_in_fm:
        out.append(
            Finding(
                "sources_sync",
                "FAIL",
                "本文で引いているが frontmatter の sources に無い source %d 件" % len(missing_in_fm),
                ["[[%s]]" % t for t in missing_in_fm],
            )
        )
    unused = sorted(listed - cited)
    if unused:
        out.append(
            Finding(
                "sources_sync",
                "WARN",
                "frontmatter にあるが本文で引いていない source %d 件(母集団から外すか本文で引く)" % len(unused),
                ["[[%s]]" % t for t in unused],
            )
        )
    if non_source:
        out.append(
            Finding(
                "sources_sync",
                "WARN",
                "sources に `@` の無い項目 %d 件(concept / entity は related に置く)" % len(non_source),
                ["[[%s]]" % t for t in non_source],
            )
        )
    if not out:
        out.append(Finding("sources_sync", "OK", "sources %d 件が本文の引用と一致" % len(listed)))
    return out


def check_sections(body):
    heads = []  # (line_no, level, title)
    for i, line in enumerate(body, 1):
        m = re.match(r"^(#{2,4})\s+(.*)$", line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    empty = []
    for idx, (ln, lvl, title) in enumerate(heads):
        end = heads[idx + 1][0] - 1 if idx + 1 < len(heads) else len(body)
        content = [l for l in body[ln:end] if l.strip()]
        if not content:
            nxt_lvl = heads[idx + 1][1] if idx + 1 < len(heads) else None
            if nxt_lvl is None or nxt_lvl <= lvl:
                empty.append("L%d %s" % (ln, title[:40]))
    speech = []
    for ln, lvl, title in heads:
        t = re.sub(r"[。．.!?！？」』)]+$", "", title.strip())
        if t.endswith("か"):
            continue
        # サーベイ型の「発見 N　<単一文献では言えないこと>」は設計上の文形見出し(skeletons §3.2)。
        if re.match(r"^発見\s*\d+", t):
            continue
        if NOUN_ENDING_RE.search(t):
            continue
        if SPEECH_HEADING_RE.search(t):
            speech.append("L%d %s" % (ln, title[:50]))
    out = []
    if empty:
        out.append(Finding("sections", "FAIL", "本文の無い節 %d 件" % len(empty), empty))
    if speech:
        out.append(
            Finding(
                "sections",
                "WARN",
                "セリフ型の可能性がある見出し %d 件(活用語尾で終わる。名詞句へ改称するか、残す判断を log に残す)" % len(speech),
                speech,
            )
        )
    if not out:
        out.append(Finding("sections", "OK", "見出し %d 本。空の節なし" % len(heads)))
    return out


# ---------------------------------------------------------------------------
# check / stats
# ---------------------------------------------------------------------------


def page_summary(path: Path):
    text = path.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    n_lines = text.count("\n")  # wc -l と同じ数え方
    if parts is None:
        return {
            "page": str(path),
            "lines": n_lines,
            "sources": 0,
            "embeds": 0,
            "h2": 0,
            "question": False,
            "frontmatter": False,
        }
    fm_lines, body, _ = parts
    fm = frontmatter_keys(fm_lines)
    sources = fm.get("sources") or []
    return {
        "page": str(path),
        "lines": n_lines,
        "sources": len(sources) if isinstance(sources, list) else 0,
        "embeds": sum(len(EMBED_RE.findall(l)) for l in body),
        "h2": sum(1 for l in body if l.startswith("## ")),
        "question": bool(unquote(fm.get("question") or "")),
        "frontmatter": True,
    }


def run_check(page: Path, only=None):
    text = page.read_text(encoding="utf-8")
    parts = split_frontmatter(text)
    if parts is None:
        return [Finding("frontmatter", "FAIL", "frontmatter(--- ... ---)が無い")]
    fm_lines, body_raw, body_start = parts
    fm = frontmatter_keys(fm_lines)
    # 先頭に frontmatter 分の空行を詰め、報告する行番号をファイルの行番号に揃える。
    body = [""] * (body_start - 1) + body_without_fences(body_raw)
    selected = set(only) if only else set(ALL_CHECKS)
    findings = []
    if "frontmatter" in selected:
        findings += check_frontmatter(fm, str(page))
    if "wikilinks" in selected:
        findings += check_wikilinks(body, VAULT_ROOT)
    if "images" in selected:
        findings += check_images(body, VAULT_ROOT)
    if "dashes" in selected:
        findings += check_dashes(body)
    if "nakaguro" in selected:
        findings += check_nakaguro(body)
    if "llm_phrases" in selected:
        findings += check_llm_phrases(body)
    if "single_source_runs" in selected:
        findings += check_single_source_runs(body)
    if "figures" in selected:
        findings += check_figures(body)
    if "sources_sync" in selected:
        findings += check_sources_sync(fm, body)
    if "sections" in selected:
        findings += check_sections(body)
    return findings


def print_findings(page: Path, findings, max_locations: int):
    summary = page_summary(page)
    print(
        "%s  (%d 行 / sources %d / 埋め込み %d / H2 %d)"
        % (page, summary["lines"], summary["sources"], summary["embeds"], summary["h2"])
    )
    for f in findings:
        print("  [%-4s] %-19s %s" % (f.level, f.check, f.message))
        shown = f.locations[:max_locations] if max_locations >= 0 else f.locations
        for loc in shown:
            print("           - %s" % loc)
        if max_locations >= 0 and len(f.locations) > max_locations:
            print("           - … 他 %d 件(--max-locations -1 で全件)" % (len(f.locations) - max_locations))
    n_fail = sum(1 for f in findings if f.level == "FAIL")
    n_warn = sum(1 for f in findings if f.level == "WARN")
    print("  FAIL %d / WARN %d" % (n_fail, n_warn))


def cmd_check(args):
    pages = [Path(p) for p in args.pages]
    only = None
    if args.only:
        only = [c.strip() for c in args.only.split(",") if c.strip()]
        unknown = [c for c in only if c not in ALL_CHECKS]
        if unknown:
            print("未知の検査名: %s(候補: %s)" % (", ".join(unknown), ", ".join(ALL_CHECKS)), file=sys.stderr)
            return EXIT_USAGE
    any_fail = False
    results = []
    for page in pages:
        if not page.is_file():
            print("ファイルが無い: %s" % page, file=sys.stderr)
            return EXIT_USAGE
        findings = run_check(page, only)
        any_fail = any_fail or any(f.level == "FAIL" for f in findings)
        if args.json:
            results.append({"page": str(page), "summary": page_summary(page), "findings": [f.to_dict() for f in findings]})
        else:
            print_findings(page, findings, args.max_locations)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return EXIT_FAIL if any_fail else EXIT_OK


def cmd_stats(args):
    d = VAULT_ROOT / "wiki" / "surveys"
    pages = sorted(d.glob("*.md")) if d.is_dir() else []
    rows = [page_summary(p) for p in pages]
    rows.sort(key=lambda r: r["lines"], reverse=True)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        print("wiki/surveys/ にページが無い")
        return EXIT_OK
    print("%6s %8s %6s %4s %3s  %s" % ("行数", "sources", "図表", "H2", "Q", "ページ"))
    for r in rows:
        print(
            "%6d %8d %6d %4d %3s  %s"
            % (r["lines"], r["sources"], r["embeds"], r["h2"], "y" if r["question"] else "-", Path(r["page"]).name)
        )
    src = [r["sources"] for r in rows if r["sources"]]
    lines = [r["lines"] for r in rows]
    if src:
        print(
            "\n%d ページ。sources 最小 %d / 中央値 %d / 最大 %d。行数 最小 %d / 中央値 %d / 最大 %d。"
            % (
                len(rows),
                min(src),
                int(statistics.median(src)),
                max(src),
                min(lines),
                int(statistics.median(lines)),
                max(lines),
            )
        )
        print("フェーズ 1 のゲート下限は実測の最小 sources(%d 本)。" % min(src))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="wiki/surveys/ の長編ページの機械検査と実測")
    sub = parser.add_subparsers(dest="cmd")
    pc = sub.add_parser("check", help="1 ページ以上を検査する")
    pc.add_argument("pages", nargs="+")
    pc.add_argument("--json", action="store_true")
    pc.add_argument("--only", help="検査名をコンマ区切りで")
    pc.add_argument("--max-locations", type=int, default=12, help="各検査で表示する場所の上限(-1 で全件)")
    pc.set_defaults(func=cmd_check)
    ps = sub.add_parser("stats", help="wiki/surveys/ 全ページの実測")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_stats)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
