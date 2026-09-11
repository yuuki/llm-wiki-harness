#!/usr/bin/env python3
"""paper-ids.py — 書誌 ID(arXiv ID / DOI / 題名キー)の索引と重複照合、frontmatter への埋め戻し。

source ページは `url:` に arXiv や doi.org を持つが、`arxiv_id:` / `doi:` を frontmatter に
揃えているのは数ページしか無い。`.raw/papers/` の slug も `arxiv-2408.08147` と題名形式が
混在するため、同じ論文を 2 回取り込んでも気づけない。本スクリプトは source ページから
ID を機械的に導出して索引 `.vault-meta/paper-ids.json`(派生キャッシュ、git 追跡しない)を
作り、取り込み前の照合(`check`)、重複の列挙(`dupes`)、frontmatter への埋め戻し
(`backfill`)を行う。

使い方:
  python3 scripts/paper-ids.py scan [--write-index]
      全 source ページを走査して ID 索引を作る。--write-index で .vault-meta/paper-ids.json に保存。
  python3 scripts/paper-ids.py check <ref> [<ref> ...] [--compact] [--fail-on-match]
      <ref> は arXiv URL / arXiv ID / DOI / doi.org URL / 題名。一致する既存 source ページを返す。
      --fail-on-match は一致があれば終了コード 4(fetch-paper-pdf.sh の取り込み前ゲート用)。
  python3 scripts/paper-ids.py dupes
      同じ arXiv ID / DOI / 題名キーを共有するページ群を列挙する。
  python3 scripts/paper-ids.py backfill [--pages P ...] [--write] [--bump-updated] [--include-dirty]
      `arxiv_id:` / `doi:` が無く、url / sources / 本文から導出できる source ページに frontmatter
      行を足す。既定は dry-run(差分件数と対象を出すだけ)。--write で書く(ページごとに lock)。
      --bump-updated を付けない限り `updated:` は触らない(ID の補完は本文の編集ではない)。
      git 作業木で変更中のページは既定で飛ばす(並行する取り込みの途中経過を巻き込まない)。
  python3 scripts/paper-ids.py report
      lint 用の markdown 断片(`## Paper IDs`)。

ID の導出元(優先順):
  arXiv  frontmatter `arxiv_id:` > `url:` の arxiv.org/abs|pdf/<id> > `sources:` の `.raw/papers/arxiv-<id>`
         > 本文の arxiv.org/abs|pdf/<id>(本文は「その論文自身の」リンクとは限らないので、frontmatter に無く
         かつ本文に 1 種類しか無いときだけ採る)
  DOI    frontmatter `doi:` > `url:` の doi.org/<doi> > 本文の doi.org/<doi>(1 種類だけのとき)
  題名   frontmatter `title:` を NFKC + casefold + 英数字以外除去した文字列(重複検出の弱いキー)

版番号(v2 など)は arXiv ID から落として比べる。本文からの導出は `source_type: paper` のページだけ
(書籍の章が引く論文のリンクを章自身の ID にしない)。重複は章分割 source を文書に畳んで数える。

終了コード:
  0 — 成功 / 2 — 使い方の誤り / 3 — 対象ページが無い / 4 — check --fail-on-match で一致あり
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wiki_lock import page_lock  # noqa: E402

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
SOURCES_DIR = "wiki/sources"
INDEX_REL = ".vault-meta/paper-ids.json"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3
EXIT_MATCH = 4

ARXIV_ID_RE = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(v\d+)?(?![\d])")
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_SLUG_RE = re.compile(r"arxiv-(\d{4}\.\d{4,5})(v\d+)?", re.I)
# DOI は `(` `)` を含み得る(10.1016/0167-9236(94)90040-4)。閉じ括弧は後で対応の取れない分だけ落とす
_DOI_TAIL = r"(?:(?![\"'<>\]`])[\x21-\x7e])+"  # ASCII 可視文字のみ(全角括弧やバッククォートで止まる)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/" + _DOI_TAIL + ")")
DOI_URL_RE = re.compile(r"doi\.org/(10\.\d{4,9}/" + _DOI_TAIL + ")", re.I)
# 本文から拾わない DOI: zenodo(データセット・成果物)、10.48550(arXiv の DataCite DOI。arxiv_id と重複する)
ARTIFACT_DOI_RE = re.compile(r"^(10\.5281/zenodo\.|10\.48550/arxiv\.)", re.I)
FM_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def log(msg):
    print(msg, file=sys.stderr)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).replace(path)
    except Exception:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- 正規化

def norm_arxiv(raw):
    m = ARXIV_ID_RE.search(raw or "")
    return m.group(1) if m else None


def norm_doi(raw):
    s = (raw or "").strip().strip("\"'")
    m = DOI_URL_RE.search(s) or DOI_RE.search(s)
    if not m:
        return None
    doi = m.group(1).split("?", 1)[0].split("#", 1)[0].rstrip(".,;:")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".,;:")
    while doi.endswith("(") and doi.count("(") > doi.count(")"):
        doi = doi[:-1].rstrip(".,;:")
    return doi.lower()


def title_key(title):
    s = unicodedata.normalize("NFKC", title or "").casefold()
    s = re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+", "", s)
    return s or None


def unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


# --------------------------------------------------------------------------- ページ走査

def split_frontmatter(text):
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def parse_fm(fm_lines):
    """flat YAML(スカラーと `- item` リスト)だけを読む。"""
    fm = {}
    key = None
    for line in fm_lines:
        if line.startswith("  - ") and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(unquote(line[4:]))
            continue
        m = FM_LINE_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            fm[key] = unquote(val) if val else []
    return fm


def derive_ids(rel, text):
    """1 ページの導出結果。{arxiv, arxiv_from, doi, doi_from, title, title_key, source_type}"""
    fm_lines, body = split_frontmatter(text)
    if fm_lines is None:
        return None
    fm = parse_fm(fm_lines)
    out = {"path": rel, "title": fm.get("title") if isinstance(fm.get("title"), str) else None,
           "source_type": fm.get("source_type") if isinstance(fm.get("source_type"), str) else None,
           "arxiv": None, "arxiv_from": None, "doi": None, "doi_from": None,
           "has_arxiv_field": isinstance(fm.get("arxiv_id"), str) and bool(fm.get("arxiv_id")),
           "has_doi_field": isinstance(fm.get("doi"), str) and bool(fm.get("doi"))}
    out["title_key"] = title_key(out["title"])

    url = fm.get("url") if isinstance(fm.get("url"), str) else ""
    srcs = fm.get("sources") if isinstance(fm.get("sources"), list) else []

    if out["has_arxiv_field"]:
        out["arxiv"], out["arxiv_from"] = norm_arxiv(fm["arxiv_id"]), "frontmatter"
    elif ARXIV_URL_RE.search(url):
        out["arxiv"], out["arxiv_from"] = ARXIV_URL_RE.search(url).group(1), "url"
    else:
        slug_hits = {m.group(1) for s in srcs for m in [ARXIV_SLUG_RE.search(s)] if m}
        if len(slug_hits) == 1:
            out["arxiv"], out["arxiv_from"] = slug_hits.pop(), "sources"
        elif out["source_type"] == "paper":
            # 本文のリンクは引用先かもしれない。paper 以外(書籍の章など)では採らない
            body_hits = {m.group(1) for m in ARXIV_URL_RE.finditer(body)}
            if len(body_hits) == 1:
                out["arxiv"], out["arxiv_from"] = body_hits.pop(), "body"

    if out["has_doi_field"]:
        out["doi"], out["doi_from"] = norm_doi(fm["doi"]), "frontmatter"
    elif DOI_URL_RE.search(url):
        out["doi"], out["doi_from"] = norm_doi(url), "url"
    elif out["source_type"] == "paper":
        body_hits = {norm_doi(m.group(0)) for m in DOI_URL_RE.finditer(body)}
        body_hits = {d for d in body_hits if d and not ARTIFACT_DOI_RE.match(d)}
        if len(body_hits) == 1:
            out["doi"], out["doi_from"] = body_hits.pop(), "body"
    return out


CHAPTER_SUFFIX_RE = re.compile(r"\s+-\s+(Chapter|Ch\.|Part|Section|Appendix|第|付録)\s*.*$", re.I)


def doc_key(rel):
    """章分割 source を文書に畳む(`@X - Chapter 3 ...` → `@X`)。重複は文書単位で数える。"""
    return CHAPTER_SUFFIX_RE.sub("", Path(rel).stem).strip()


def dupe_groups(mapping):
    """{key: [pages]} のうち、2 文書以上にまたがる key だけを {key: [pages]} で返す。"""
    out = {}
    for k, pages in mapping.items():
        if len({doc_key(p) for p in pages}) > 1:
            out[k] = pages
    return out


def iter_sources(pages=None):
    root = VAULT_ROOT / SOURCES_DIR
    if pages:
        for p in pages:
            rel = p if p.startswith(SOURCES_DIR) else f"{SOURCES_DIR}/{p}"
            rel = rel if rel.endswith(".md") else rel + ".md"
            path = VAULT_ROOT / rel
            if path.is_file():
                yield rel, path
            else:
                log(f"WARN: not found: {rel}")
        return
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("_"):
            continue
        yield f"{SOURCES_DIR}/{path.name}", path


def scan(pages=None):
    records = []
    for rel, path in iter_sources(pages):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rec = derive_ids(rel, text)
        if rec:
            records.append(rec)
    return records


def build_index(records):
    idx = {"version": 1, "built_at": date.today().isoformat(), "pages": len(records),
           "arxiv": defaultdict(list), "doi": defaultdict(list), "title": defaultdict(list)}
    for r in records:
        if r["arxiv"]:
            idx["arxiv"][r["arxiv"]].append(r["path"])
        if r["doi"]:
            idx["doi"][r["doi"]].append(r["path"])
        if r["title_key"] and len(r["title_key"]) >= 12:
            idx["title"][r["title_key"]].append(r["path"])
    for k in ("arxiv", "doi", "title"):
        idx[k] = dict(idx[k])
    return idx


def index_path():
    return VAULT_ROOT / INDEX_REL


def load_or_build_index():
    """索引があり、wiki/sources/ のディレクトリ mtime(追加・削除で更新される)より新しければ使う。
    古ければ走査して書き直す。既存ページ内の url 変更までは追わない(scan --write-index で更新)。"""
    path = index_path()
    src_dir = VAULT_ROOT / SOURCES_DIR
    if path.is_file():
        try:
            fresh = not src_dir.is_dir() or path.stat().st_mtime >= src_dir.stat().st_mtime
            if fresh:
                return json.loads(path.read_text(encoding="utf-8")), True
        except (OSError, json.JSONDecodeError):
            pass
    idx = build_index(scan())
    try:
        atomic_write(path, json.dumps(idx, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return idx, False


# --------------------------------------------------------------------------- サブコマンド

def cmd_scan(args):
    records = scan()
    idx = build_index(records)
    summary = {
        "pages": len(records),
        "papers": sum(1 for r in records if r["source_type"] == "paper"),
        "with_arxiv": sum(1 for r in records if r["arxiv"]),
        "arxiv_in_frontmatter": sum(1 for r in records if r["has_arxiv_field"]),
        "with_doi": sum(1 for r in records if r["doi"]),
        "doi_in_frontmatter": sum(1 for r in records if r["has_doi_field"]),
        "dupe_arxiv": len(dupe_groups(idx["arxiv"])),
        "dupe_doi": len(dupe_groups(idx["doi"])),
        "dupe_title": len(dupe_groups(idx["title"])),
    }
    if args.write_index:
        atomic_write(index_path(), json.dumps(idx, ensure_ascii=False, indent=1))
        summary["index"] = INDEX_REL
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return EXIT_OK


def classify_ref(ref):
    """<ref> を (kind, key) にする。arxiv / doi / title。"""
    s = ref.strip()
    if ARXIV_URL_RE.search(s) or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", s) or s.lower().startswith("arxiv:"):
        return "arxiv", norm_arxiv(s)
    doi = norm_doi(s)
    if doi and (DOI_URL_RE.search(s) or s.lower().startswith("doi:") or DOI_RE.match(s)):
        return "doi", doi
    return "title", title_key(s)


def cmd_check(args):
    idx, cached = load_or_build_index()
    results = []
    any_match = False
    for ref in args.refs:
        kind, key = classify_ref(ref)
        matches = []
        if key:
            if kind == "arxiv":
                matches = idx["arxiv"].get(key, [])
            elif kind == "doi":
                matches = idx["doi"].get(key, [])
            else:
                matches = idx["title"].get(key, []) if len(key) >= 12 else []
        any_match = any_match or bool(matches)
        results.append({"ref": ref, "kind": kind, "key": key, "matches": matches})
    if args.compact:
        for r in results:
            print(f"{r['ref']}\t{r['kind']}\t{r['key'] or ''}\t{'HIT' if r['matches'] else 'NONE'}\t" + ";".join(r["matches"]))
    else:
        payload = results[0] if len(results) == 1 else {"results": results}
        if isinstance(payload, dict):
            payload["index_cached"] = cached
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.fail_on_match and any_match:
        return EXIT_MATCH
    return EXIT_OK


def cmd_dupes(args):
    idx, _ = load_or_build_index()
    out = {"arxiv": dupe_groups(idx["arxiv"]), "doi": dupe_groups(idx["doi"]), "title": dupe_groups(idx["title"])}
    out["count"] = sum(len(v) for v in out.values() if isinstance(v, dict))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return EXIT_OK


def insert_fm_lines(text, additions, bump_updated):
    """frontmatter の末尾(閉じ `---` の直前)に行を足す。updated は任意で今日に。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    fm = lines[1:end]
    if bump_updated:
        today = date.today().isoformat()
        for j, line in enumerate(fm):
            if line.startswith("updated:"):
                fm[j] = f"updated: {today}"
    # url: の直後に置けるならそこへ(人が読むときに並ぶ)。無ければ末尾
    pos = len(fm)
    for j, line in enumerate(fm):
        if line.startswith("url:"):
            pos = j + 1
            break
    fm = fm[:pos] + additions + fm[pos:]
    return "\n".join(lines[:1] + fm + lines[end:])


def git_dirty_sources():
    """作業木で変更・追加・削除中の source ページ(相対パス)。並行する取り込みの途中経過に触らないため。"""
    import subprocess
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", SOURCES_DIR], cwd=str(VAULT_ROOT),
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    dirty = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = bytes(path[1:-1], "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
        dirty.add(path)
    return dirty


def cmd_backfill(args):
    records = scan(args.pages)
    if args.pages and not records:
        return EXIT_MISSING
    dirty = set() if args.include_dirty else git_dirty_sources()
    skipped_dirty = 0
    planned = []
    for r in records:
        if r["path"] in dirty:
            skipped_dirty += 1
            continue
        adds = []
        if r["arxiv"] and not r["has_arxiv_field"]:
            adds.append(f'arxiv_id: "{r["arxiv"]}"')
        if r["doi"] and not r["has_doi_field"]:
            adds.append(f'doi: "{r["doi"]}"')
        if adds:
            planned.append({"path": r["path"], "add": adds,
                            "from": {"arxiv": r["arxiv_from"], "doi": r["doi_from"]}})
    if not args.write:
        print(json.dumps({"dry_run": True, "pages": len(planned), "skipped_dirty": skipped_dirty,
                          "arxiv_added": sum(1 for p in planned if any(a.startswith("arxiv_id") for a in p["add"])),
                          "doi_added": sum(1 for p in planned if any(a.startswith("doi") for a in p["add"])),
                          "items": planned[: args.show]}, ensure_ascii=False, indent=2))
        return EXIT_OK
    written = 0
    for p in planned:
        rel = p["path"]
        path = VAULT_ROOT / rel
        with page_lock(rel):
            text = path.read_text(encoding="utf-8")
            new = insert_fm_lines(text, p["add"], args.bump_updated)
            if new is None or new == text:
                continue
            atomic_write(path, new)
            written += 1
    if written and not args.no_index:
        atomic_write(index_path(), json.dumps(build_index(scan()), ensure_ascii=False, indent=1))
    print(json.dumps({"written": written, "pages": len(planned), "skipped_dirty": skipped_dirty}, ensure_ascii=False))
    return EXIT_OK


def whole_plus_chapters(records):
    """1 枚ものの source と、同じ文書の章分割 source が併存している文書(再取り込みの残骸)。"""
    stems = {Path(r["path"]).stem for r in records}
    chaptered = defaultdict(int)
    for r in records:
        stem = Path(r["path"]).stem
        dk = doc_key(r["path"])
        if dk != stem:
            chaptered[dk] += 1
    return sorted((dk, n) for dk, n in chaptered.items() if dk in stems)


def render_report(records, idx):
    papers = [r for r in records if r["source_type"] == "paper"]
    coexist = whole_plus_chapters(records)
    missing_arxiv = [r for r in papers if r["arxiv"] and not r["has_arxiv_field"]]
    missing_doi = [r for r in papers if r["doi"] and not r["has_doi_field"]]
    no_id = [r for r in papers if not r["arxiv"] and not r["doi"]]
    dup_ax, dup_doi, dup_title = dupe_groups(idx["arxiv"]), dupe_groups(idx["doi"]), dupe_groups(idx["title"])
    out = ["## Paper IDs", ""]
    out.append(f"- source {len(records)} 頁(paper {len(papers)})。arXiv ID を導出できた {sum(1 for r in records if r['arxiv'])} 頁"
               f"(frontmatter に持つ {sum(1 for r in records if r['has_arxiv_field'])})、DOI {sum(1 for r in records if r['doi'])} 頁"
               f"(frontmatter {sum(1 for r in records if r['has_doi_field'])})")
    out.append(f"- 埋め戻し可能: arxiv_id {len(missing_arxiv)} 頁、doi {len(missing_doi)} 頁(`paper-ids.py backfill --write`)")
    out.append(f"- paper なのに arXiv も DOI も導出できない: {len(no_id)} 頁(url が会議サイト直リンクなど。手で `doi:` を足すか、そのままにする)")
    out.append(f"- 重複候補: arXiv {len(dup_ax)} 組、DOI {len(dup_doi)} 組、題名キー {len(dup_title)} 組")
    out.append(f"- 1 枚ものと章分割の併存: {len(coexist)} 文書(章単位で再取り込みした後に旧ページが残っている)")
    out.append("")
    if coexist:
        out.append("### 1 枚ものと章分割の併存")
        out.append("")
        for dk, n in coexist:
            out.append(f"- [[{dk}]] と章 source {n} 頁。旧ページを残す理由が無ければ、リンクを章へ張り替えて削除する(人間判断)")
        out.append("")
    if dup_ax or dup_doi or dup_title:
        out.append("### 重複候補(同じ論文が 2 つ以上の文書として取り込まれている。章分割は 1 文書に畳んで数えた)")
        out.append("")
        for label, groups in (("arXiv", dup_ax), ("DOI", dup_doi), ("題名", dup_title)):
            for k, pages in sorted(groups.items()):
                docs = sorted({doc_key(p) for p in pages})
                names = "、".join(f"[[{d}]]" for d in docs)
                out.append(f"- {label} `{k}`: {names}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cmd_report(args):
    records = scan()
    idx = build_index(records)
    sys.stdout.write(render_report(records, idx))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="書誌 ID の索引・照合・埋め戻し。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--write-index", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("check")
    p.add_argument("refs", nargs="+")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--fail-on-match", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("dupes")
    p.set_defaults(func=cmd_dupes)

    p = sub.add_parser("backfill")
    p.add_argument("--pages", nargs="*", default=None)
    p.add_argument("--write", action="store_true")
    p.add_argument("--bump-updated", action="store_true")
    p.add_argument("--include-dirty", action="store_true", help="git 作業木で変更中のページも対象にする(既定は飛ばす)")
    p.add_argument("--no-index", action="store_true", help="書き込み後の索引再構築を省く")
    p.add_argument("--show", type=int, default=20, help="dry-run で列挙する件数")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
