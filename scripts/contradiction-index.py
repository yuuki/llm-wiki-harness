#!/usr/bin/env python3
"""contradiction-index.py — 矛盾 callout の索引を作る。

wiki 配下の `> [!contradiction]` callout を全て集め、保持頁・節・主題・関係する頁・
出典・状態(推定)を 1 件 1 行で持つ索引にする。conventions §5 は矛盾を両頁に
残すことを求めるが、これまで一覧も検索路も無かった。本スクリプトはその索引を
機械的に生成し、lint(片側だけに callout がある頁対)と query(「X と Y は矛盾
するか」)に検索路を与える。

使い方:
  python3 scripts/contradiction-index.py                    # 全 callout を JSON で stdout
  python3 scripts/contradiction-index.py --query KV 転送     # 語で絞る(AND、wikilink 名にも一致)
  python3 scripts/contradiction-index.py --one-sided        # 片側のみの候補(lint 用)
  python3 scripts/contradiction-index.py --write            # wiki/meta/contradictions.md と
                                                            # .vault-meta/contradictions.json を生成
  python3 scripts/contradiction-index.py --summary          # 件数だけ

状態(推定)の規則:
  - 本文に 要解決 / 要追加検証 / 未決着 / 決着はついていない / 判別できない などが
    あれば open。
  - 上記が無く、矛盾ではなく / 矛盾しない / 両立 / 対立ではなく などがあれば explained。
  - どちらも無ければ open(説明されるまで矛盾は開いたまま)。
  推定は見出し語の一致だけで行い、本文を解釈しない。判定を上書きしたいときは
  callout の本文に `(status: explained)` または `(status: open)` を書く。

片側のみの候補:
  callout が wikilink する相手頁(concept / entity / source)に、保持頁へ戻る
  wikilink か同じ出典を持つ contradiction callout が無い対を列挙する。survey と
  question は編纂物であり、相手頁としては数えない。候補であって違反の断定ではない。

本スクリプトは wiki の本文ページを書かない。書くのは `--write` の 2 ファイルだけである。
環境変数 WIKI_VAULT_ROOT で vault ルートを差し替えられる(試験用)。

終了コード:
  0 — 成功
  2 — 使い方の誤り
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_lock import page_lock

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2

SCAN_DIRS = ("sources", "entities", "concepts", "questions", "surveys")
PARTY_DIRS = ("concepts", "entities", "sources")
TYPE_BY_DIR = {
    "sources": "source",
    "entities": "entity",
    "concepts": "concept",
    "questions": "question",
    "surveys": "survey",
}

CALLOUT_RE = re.compile(r"^\s*((?:>\s*)+)\[!contradiction\]([-+]?)\s*(.*)$")
QUOTE_LINE_RE = re.compile(r"^\s*((?:>\s*)+)(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
STATUS_OVERRIDE_RE = re.compile(r"\(status:\s*(open|explained)\)")

OPEN_HINTS = (
    "要解決", "要追加検証", "未決着", "決着はついていない", "決着していない", "判別できない",
    "要確認", "未解決", "検証が必要", "確認が必要", "追加検証", "未検証",
)
EXPLAINED_HINTS = (
    "矛盾ではなく", "矛盾しない", "矛盾ではない", "矛盾はない", "両立する", "両立し",
    "対立ではなく", "食い違いではなく", "別人格", "別のシステム", "別の組織", "同名",
    "整合的であり", "問いの立て方が異なる", "配置の違い", "条件が異なる", "定義が異なる",
)

INDEX_REL = "wiki/meta/contradictions.md"
SIDECAR_REL = ".vault-meta/contradictions.json"


def log(msg):
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- 読み取り

def unquote(value):
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def frontmatter_title(text, fallback):
    if not text.startswith("---"):
        return fallback
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^title:\s*(.*)$", line)
        if m and m.group(1).strip():
            return unquote(m.group(1))
    return fallback


def frontmatter_created(text):
    if not text.startswith("---"):
        return None
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^created:\s*(\S+)", line)
        if m:
            return unquote(m.group(1))
    return None


def list_pages(types):
    out = []
    for sub in SCAN_DIRS:
        if sub not in types:
            continue
        folder = VAULT_ROOT / "wiki" / sub
        if not folder.is_dir():
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            path = folder / name
            if path.is_file():
                out.append((sub, path))
    return out


def link_basename(target):
    """`wiki/entities/X|X` や `papers/Y` のパス修飾を basename に落とす。primary 層は None。"""
    t = target.strip()
    if not t:
        return None
    if t.startswith(("papers/", "research/", "structures/", "notes/", "books/", "Clippings/")):
        return None
    if t.startswith("_attachments/") or t.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf", ".gif", ".webp")):
        return None
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if t.endswith(".md"):
        t = t[:-3]
    return t or None


def guess_status(text):
    override = STATUS_OVERRIDE_RE.search(text)
    if override:
        return override.group(1), f"(status: {override.group(1)})"
    for hint in OPEN_HINTS:
        if hint in text:
            return "open", hint
    for hint in EXPLAINED_HINTS:
        if hint in text:
            return "explained", hint
    return "open", ""


def extract_callouts(sub, path):
    rel = path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    host_title = frontmatter_title(text, path.stem)
    lines = text.splitlines()
    out = []
    section = ""
    i = 0
    in_fm = text.startswith("---")
    while i < len(lines):
        line = lines[i]
        if in_fm:
            if i > 0 and line.strip() == "---":
                in_fm = False
            i += 1
            continue
        h = HEADING_RE.match(line)
        if h and len(h.group(1)) >= 2:
            section = h.group(2).strip()
        m = CALLOUT_RE.match(line)
        if not m:
            i += 1
            continue
        depth = m.group(1).count(">")
        title = m.group(3).strip()
        body_lines = []
        j = i + 1
        while j < len(lines):
            q = QUOTE_LINE_RE.match(lines[j])
            if not q or q.group(1).count(">") < depth:
                break
            body_lines.append(q.group(2).rstrip())
            j += 1
        body = "\n".join(body_lines).strip()
        block = (title + "\n" + body).strip()
        targets = []
        for t in WIKILINK_RE.findall(block):
            base = link_basename(t)
            if base and base not in targets:
                targets.append(base)
        pages = [t for t in targets if not t.startswith("@")]
        sources = [t for t in targets if t.startswith("@")]
        status, hint = guess_status(block)
        out.append({
            "host": rel,
            "host_type": TYPE_BY_DIR.get(sub, sub),
            "host_title": host_title,
            "host_name": path.stem,
            "section": section,
            "line": i + 1,
            "title": title,
            "body": body,
            "pages": pages,
            "sources": sources,
            "status": status,
            "status_hint": hint,
            "nested": depth > 1,
        })
        i = j
    return out


def collect(types):
    records = []
    for sub, path in list_pages(types):
        records.extend(extract_callouts(sub, path))
    records.sort(key=lambda r: (r["host"], r["line"]))
    for n, r in enumerate(records, start=1):
        r["id"] = f"x-{n:04d}"
    return records


# --------------------------------------------------------------------------- 片側のみ

def resolve_party(name):
    """wikilink 名を wiki 内の頁パスに解決する。concept / entity / source だけを相手頁とする。"""
    for sub in PARTY_DIRS:
        p = VAULT_ROOT / "wiki" / sub / f"{name}.md"
        if p.is_file():
            return sub, p.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
    return None, None


def one_sided(records):
    by_host = {}
    for r in records:
        by_host.setdefault(r["host"], []).append(r)
    out = []
    seen = set()
    for r in records:
        if r["host_type"] in ("survey", "question"):
            continue
        for name in r["pages"] + r["sources"]:
            sub, party_rel = resolve_party(name)
            if party_rel is None or party_rel == r["host"]:
                continue
            key = (r["host"], party_rel)
            if key in seen:
                continue
            seen.add(key)
            back = False
            for other in by_host.get(party_rel, []):
                links_back = r["host_name"] in other["pages"] or r["host_name"] in other["sources"]
                shares_source = bool(set(r["sources"]) & set(other["sources"]))
                shares_page = bool(set(r["pages"]) & set(other["pages"]))
                if links_back or shares_source or shares_page:
                    back = True
                    break
            if back:
                continue
            out.append({
                "id": r["id"],
                "host": r["host"],
                "title": r["title"] or (r["body"][:60] + ("…" if len(r["body"]) > 60 else "")),
                "party": party_rel,
                "party_type": TYPE_BY_DIR.get(sub, sub),
            })
    out.sort(key=lambda o: (o["party_type"], o["host"], o["party"]))
    return out


# --------------------------------------------------------------------------- 検索

def matches(record, terms):
    hay = "\n".join([
        record["title"], record["body"], record["host_title"], record["section"],
        " ".join(record["pages"]), " ".join(record["sources"]),
    ]).casefold()
    return all(t.casefold() in hay for t in terms)


def excerpt(record, n=240):
    text = (record["title"] + " — " if record["title"] else "") + " ".join(record["body"].split())
    return text if len(text) <= n else text[: n - 1] + "…"


def public_record(r, with_body=True):
    out = {
        "id": r["id"],
        "host": r["host"],
        "host_type": r["host_type"],
        "host_title": r["host_title"],
        "section": r["section"],
        "line": r["line"],
        "title": r["title"],
        "status": r["status"],
        "status_hint": r["status_hint"],
        "pages": r["pages"],
        "sources": r["sources"],
        "nested": r["nested"],
    }
    if with_body:
        out["body"] = r["body"]
    else:
        out["excerpt"] = excerpt(r)
    return out


# --------------------------------------------------------------------------- 書き出し

def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") or text == "" else text + "\n"
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


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def page_link(rel):
    name = Path(rel).stem
    return f"[[{name}]]"


def links_cell(names, limit=4):
    shown = names[:limit]
    rest = len(names) - len(shown)
    parts = [f"[[{n}]]" for n in shown]
    if rest > 0:
        parts.append(f"ほか {rest}")
    return cell("、".join(parts)) if parts else "—"


def summary_counts(records, sided):
    by_type = {}
    for r in records:
        by_type[r["host_type"]] = by_type.get(r["host_type"], 0) + 1
    return {
        "callouts": len(records),
        "pages": len({r["host"] for r in records}),
        "open": sum(1 for r in records if r["status"] == "open"),
        "explained": sum(1 for r in records if r["status"] == "explained"),
        "one_sided": len(sided),
        "by_type": by_type,
    }


def render_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def render_markdown(records, sided, now, created):
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M")
    counts = summary_counts(records, sided)
    type_order = ("concept", "entity", "source", "survey", "question")
    type_line = " / ".join(
        f"{t} {counts['by_type'][t]}" for t in type_order if counts["by_type"].get(t)
    )

    def rows_for(status):
        rows = []
        for r in records:
            if r["status"] != status:
                continue
            subject = r["title"] or excerpt(r, 80)
            rows.append([
                cell(page_link(r["host"])),
                cell(r["section"] or "—"),
                cell(subject),
                links_cell(r["pages"]),
                links_cell(r["sources"], limit=3),
            ])
        return rows

    lines = [
        "---",
        "type: meta",
        'title: "矛盾索引"',
        f"date: {stamp}",
        f"created: {created}",
        f"updated: {day}",
        "tags:",
        f"  - {now.strftime('%Y/%m/%d')}",
        "  - meta",
        "  - contradictions",
        "status: evergreen",
        "related:",
        '  - "[[index]]"',
        '  - "[[conventions]]"',
        "---",
        "",
        "# 矛盾索引",
        "",
        "Navigation: [[index]] | [[conventions]]",
        "",
        "`python3 scripts/contradiction-index.py --write` が生成する。手で編集しない。callout を書いたら、または lint の前に再生成する。"
        "状態は本文の見出し語からの推定であり、callout 本文に `(status: explained)` / `(status: open)` を書けば上書きできる。"
        "検索は `python3 scripts/contradiction-index.py --query <語>...`。",
        "",
        "## 概要",
        "",
        f"- callout {counts['callouts']} 件 / 保持頁 {counts['pages']} 頁。未決着(推定){counts['open']}、説明済み(推定){counts['explained']}。",
        f"- 保持頁の種別: {type_line}" if type_line else "- 保持頁の種別: —",
        f"- 片側のみの候補: {counts['one_sided']} 対(§5 は矛盾を両頁に残す。候補であって違反の断定ではない)。",
        "",
        "## 未決着(推定)",
        "",
    ]
    open_rows = rows_for("open")
    if open_rows:
        lines.append(render_table(open_rows, ["保持頁", "節", "主題", "関係する頁", "出典"]))
    else:
        lines.append("- なし")
    lines += ["", "## 説明済み(推定)", ""]
    exp_rows = rows_for("explained")
    if exp_rows:
        lines.append(render_table(exp_rows, ["保持頁", "節", "主題", "関係する頁", "出典"]))
    else:
        lines.append("- なし")
    lines += ["", "## 片側のみの候補", ""]
    if sided:
        rows = [
            [cell(page_link(s["host"])), cell(s["title"]), cell(page_link(s["party"])), cell(s["party_type"])]
            for s in sided
        ]
        lines.append(render_table(rows, ["保持頁", "主題", "相手頁", "相手頁の種別"]))
    else:
        lines.append("- なし")
    lines.append("")
    return "\n".join(lines)


def write_outputs(records, sided, now):
    index_path = VAULT_ROOT / INDEX_REL
    created = None
    if index_path.is_file():
        try:
            created = frontmatter_created(index_path.read_text(encoding="utf-8"))
        except OSError:
            created = None
    created = created or now.strftime("%Y-%m-%d")
    md = render_markdown(records, sided, now, created)
    with page_lock(INDEX_REL):
        atomic_write(index_path, md)
    sidecar = VAULT_ROOT / SIDECAR_REL
    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": summary_counts(records, sided),
        "records": [public_record(r) for r in records],
        "one_sided": sided,
    }
    atomic_write(sidecar, json.dumps(payload, ensure_ascii=False, indent=1))
    return index_path, sidecar


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="矛盾 callout の索引を作る。")
    parser.add_argument("--types", default=",".join(SCAN_DIRS),
                        help="走査するフォルダ(カンマ区切り。既定: 全種別)")
    parser.add_argument("--query", nargs="+", metavar="TERM",
                        help="語で絞る(AND)。主題・本文・保持頁名・節・wikilink 名に一致")
    parser.add_argument("--one-sided", action="store_true", help="片側のみの候補を列挙する")
    parser.add_argument("--write", action="store_true",
                        help=f"{INDEX_REL} と {SIDECAR_REL} を生成する")
    parser.add_argument("--summary", action="store_true", help="件数だけを出す")
    parser.add_argument("--limit", type=int, default=50, help="--query の最大件数(既定 50)")
    args = parser.parse_args(argv)

    types = tuple(t.strip() for t in args.types.split(",") if t.strip())
    bad = [t for t in types if t not in SCAN_DIRS]
    if bad or not types:
        log(f"ERR: --types は {','.join(SCAN_DIRS)} から選ぶ: {bad}")
        return EXIT_USAGE
    if args.limit < 1:
        log("ERR: --limit は 1 以上")
        return EXIT_USAGE
    if sum(bool(x) for x in (args.query, args.one_sided, args.write, args.summary)) > 1:
        log("ERR: --query / --one-sided / --write / --summary は同時に指定しない")
        return EXIT_USAGE

    records = collect(types)

    if args.query:
        hits = [public_record(r, with_body=False) for r in records if matches(r, args.query)]
        log(f"contradiction-index: {min(len(hits), args.limit)} hit(s) (of {len(hits)}) for {args.query}")
        print(json.dumps(hits[: args.limit], ensure_ascii=False, indent=2))
        return EXIT_OK

    sided = one_sided(records)

    if args.one_sided:
        log(f"contradiction-index: {len(sided)} one-sided candidate(s)")
        print(json.dumps(sided, ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.summary:
        print(json.dumps(summary_counts(records, sided), ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.write:
        index_path, sidecar = write_outputs(records, sided, datetime.now())
        counts = summary_counts(records, sided)
        log(f"contradiction-index: wrote {INDEX_REL} and {SIDECAR_REL}")
        print(json.dumps({"ok": True, "index": INDEX_REL, "sidecar": SIDECAR_REL, "counts": counts},
                         ensure_ascii=False))
        return EXIT_OK

    log(f"contradiction-index: {len(records)} callout(s) on {len({r['host'] for r in records})} page(s)")
    print(json.dumps([public_record(r) for r in records], ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
