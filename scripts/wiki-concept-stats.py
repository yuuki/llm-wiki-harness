#!/usr/bin/env python3
"""wiki-concept-stats.py — find oversized concept hubs and optional 子概念 stubs.

Usage:
  wiki-concept-stats.py [--json] [--min-bytes 50000] [--min-lines 250]
                        [--promote-children] [--limit 20]
  wiki-concept-stats.py --compile-debt [--min-inbox 5] [--min-orphan-inbox 15] [--limit 20]
  wiki-concept-stats.py --compile-debt --page wiki/concepts/X.md
  wiki-concept-stats.py --structure [--page X] [--limit 20]

Stdout: JSON list of hub rows. --promote-children / --repair-children rewrite ## 子概念 from related
pages that are not themselves hub-sized. Does not move body insights.

--compile-debt lists concept pages whose inbox (`## 未編纂の観察`, legacy
`## 横断的知見`) holds >= --min-inbox top-level bullets, or that have no
topic section at all and >= --min-orphan-inbox bullets. Cited assertions that
ingest left in `## 未解決の問い` (question_assertions) count toward the inbox.
Bullets that a recompile deliberately left behind with a trailing
`— 保留理由: …` are not counted toward the threshold (held_bullets reports them
separately), so held observations alone never put a page back into debt.
recompile-queue.py refresh reuses compile_metrics, so both count the same way.
Sorted by inbox size.
With --page, judges exactly one page (exact path or title, no substring match)
and prints one row with `in_debt`; the exit code is 1 when it is in debt.
Every row (hub or debt) carries inbox_bullets / topic_sections / legacy_heading.
Section kinds come from wiki_sections: reference-list headings (`## 参照`,
`## 関連ページ`, …) are not topic sections, bracketed variants of the inbox
(`## 横断的知見(続き)`) count as inbox, and the empty-inbox placeholder line
(「現時点では単一ソースのため、今後追記する」, or a bare status such as 「未実施」
「保留した観察: 0 件」) is not an observation.

--structure reports concept pages whose skeleton is broken (wiki-lint の構造検査。
読み取り専用): `missing_h1_title` (no `# タイトル` line), `missing_definition`
(no `## 定義`), `missing_sources_section` (no `## 出典`), and
`empty_frontmatter_sources` (frontmatter `sources:` is missing or empty while
the body cites `[[@…]]`). Stdout is a JSON list of {path, title, problems}
for the pages with at least one problem; stderr gives the count per problem.
With --page it prints one row (problems may be empty).

Exit codes:
  0 — success (with --page: the page has no compile debt / no structure problem)
  1 — with --page: the page has compile debt / a structure problem
  2 — usage error
  3 — with --page: page not found
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_lock import page_lock
from wiki_questions import is_stray_assertion
# FIXED_HEADINGS / INBOX_HEADING / LEGACY_INBOX_HEADING / QUESTIONS_HEADING は他スクリプトが
# このモジュール経由で参照するので残す(wiki_claims ほか)。
from wiki_sections import (FIXED_HEADINGS, INBOX_HEADING, KIND_DEFINITION, KIND_INBOX,  # noqa: F401
                           KIND_QUESTIONS, KIND_TOPIC, LEGACY_INBOX_HEADING, QUESTIONS_HEADING, base_heading,
                           is_legacy_inbox_heading,
                           is_held_bullet, is_placeholder_bullet, section_kind)

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_IN_DEBT = 1
EXIT_USAGE = 2
EXIT_MISSING = 3

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
CHILD_HEADING_RE = re.compile(r"^## 子概念\s*$", flags=re.M)
DEF_HEADING_RE = re.compile(r"^## 定義\s*$", flags=re.M)
H2_RE = re.compile(r"^## ", flags=re.M)
TOP_BULLET_RE = re.compile(r"^[-*] ", flags=re.M)

DEFAULT_MIN_INBOX = 5
DEFAULT_MIN_ORPHAN_INBOX = 15


def log(msg):
    print(msg, file=sys.stderr)


def unquote(value):
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].replace("\\'", "'").replace('\\"', '"')
    return s


def parse_simple_yaml(text):
    data = {}
    key = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^(\s+)-\s+(.*)$", line)
        if item and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(unquote(item.group(2)))
            continue
        kv = re.match(r"^([A-Za-z0-9_/-]+):\s*(.*)$", line)
        if not kv:
            continue
        key = kv.group(1)
        raw = kv.group(2).strip()
        if raw in ("", "|", ">"):
            data[key] = []
        elif raw == "[]":
            data[key] = []
        else:
            data[key] = unquote(raw)
    return data


def split_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            fm = parse_simple_yaml("".join(lines[1:i]))
            body = "".join(lines[i + 1:])
            return fm, body
    return {}, text


def as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None and str(v) != ""]
    return [value]


def wikilink_target(item):
    match = WIKILINK_RE.search(str(item))
    if not match:
        return None
    return match.group(1).strip()


def is_hub_sized(path, min_bytes, min_lines):
    try:
        st = path.stat()
    except OSError:
        return False
    try:
        with path.open(encoding="utf-8") as fh:
            lines = sum(1 for _ in fh)
    except OSError:
        return False
    return st.st_size >= min_bytes or lines >= min_lines


def is_peer_hub(child_path, min_bytes, min_lines):
    """Peer = the related page is itself hub-sized.

    Reciprocal links are common for true children (手法 → 親). Size is the
    signal: AIOps / ログ解析 class pages are peers; thin technique pages
    stay children even when they back-link the hub.
    """
    return is_hub_sized(child_path, min_bytes, min_lines)


def child_candidates_from_related(related, hub_names=None, min_bytes=0, min_lines=0):
    found = []
    seen = set()
    concepts = VAULT_ROOT / "wiki" / "concepts"
    hub_names = set(hub_names or ())
    for item in as_list(related):
        target = wikilink_target(item)
        if not target or target.startswith("@"):
            continue
        if hub_names and target in hub_names:
            continue
        child_path = concepts / f"{target}.md"
        if not child_path.is_file():
            continue
        if target in seen:
            continue
        if is_peer_hub(child_path, min_bytes, min_lines):
            continue
        seen.add(target)
        found.append(target)
    found.sort()
    return found


def bump_updated(text, day=None):
    day = day or date.today().isoformat()
    if re.search(r"^updated:\s*\S+", text, flags=re.M):
        return re.sub(r"^updated:\s*\S+", f"updated: {day}", text, count=1, flags=re.M)
    fm_end = text.find("\n---", 3)
    if fm_end == -1:
        return text
    insert_at = fm_end + 1
    return text[:insert_at] + f"updated: {day}\n" + text[insert_at:]


def replace_child_section(text, children):
    if CHILD_HEADING_RE.search(text):
        start = CHILD_HEADING_RE.search(text).start()
        rest = text[start + 2:]
        nxt = H2_RE.search(text, start + 3)
        end = nxt.start() if nxt else len(text)
        if children:
            block = "## 子概念\n\n" + "\n".join(f"- [[{name}]]" for name in children) + "\n\n"
            return text[:start] + block + text[end:].lstrip("\n")
        before = text[:start].rstrip() + "\n\n"
        return before + text[end:].lstrip("\n")
    if not children:
        return text
    return insert_child_section(text, children)


def atomic_write(path, text):
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


def insert_child_section(text, children):
    block = "## 子概念\n\n" + "\n".join(f"- [[{name}]]" for name in children) + "\n"
    anchor = DEF_HEADING_RE.search(text) or H2_RE.search(text)
    if not anchor:
        out = text if text.endswith("\n") else text + "\n"
        return out + "\n" + block
    next_h = H2_RE.search(text, anchor.end())
    if next_h:
        insert_at = next_h.start()
        before = text[:insert_at]
        if not before.endswith("\n"):
            before += "\n"
        if not before.endswith("\n\n"):
            before += "\n"
        return before + block + "\n" + text[insert_at:]
    out = text if text.endswith("\n") else text + "\n"
    return out + "\n" + block


def h2_sections(body):
    matches = list(H2_RE.finditer(body))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[match.start():end]
        heading = block.split("\n", 1)[0][3:].strip()
        out.append((heading, block))
    return out


def inbox_bullet_count(block):
    """受信箱の観察数。空欄の案内文(wiki_sections.is_placeholder_bullet)は観察ではないので数えない。
    保留理由つきの観察(is_held_bullet)も負債の閾値には数えない(held_bullet_count が別に数える)。"""
    return sum(1 for line in block.splitlines()
               if TOP_BULLET_RE.match(line) and not is_placeholder_bullet(line) and not is_held_bullet(line))


def held_bullet_count(block):
    """再編纂が `— 保留理由:` を付けて受信箱に残した観察の数。"""
    return sum(1 for line in block.splitlines()
               if TOP_BULLET_RE.match(line) and not is_placeholder_bullet(line) and is_held_bullet(line))


def compile_metrics(body):
    """inbox_bullets / held_bullets / topic_sections / legacy_heading / question_assertions for one concept body.

    節の分類は wiki_sections.section_kind に従う。`横断的知見(続き)` のような受信箱の変種は受信箱に、
    `## 参照` `## 関連ページ` のような参照一覧の節はどこにも数えない(主題節ではない)。
    question_assertions は `## 未解決の問い` に紛れた出典付きの断定(wiki_questions の判定)。
    ingest が問い節を第二の受信箱にした分で、再編纂が主題節へ畳む対象なので負債に数える。
    held_bullets は受信箱の保留理由つきの観察で、inbox_bullets には含めない(負債に数えない)。
    """
    inbox = 0
    held = 0
    topics = 0
    legacy = False
    question_assertions = 0
    for heading, block in h2_sections(body):
        kind = section_kind(heading)
        if kind == KIND_INBOX:
            inbox += inbox_bullet_count(block)
            held += held_bullet_count(block)
            if is_legacy_inbox_heading(heading):
                legacy = True
        elif kind == KIND_QUESTIONS:
            question_assertions += sum(1 for line in block.splitlines()
                                       if line.startswith(("- ", "* ")) and is_stray_assertion(line))
        elif kind == KIND_TOPIC:
            topics += 1
    return {"inbox_bullets": inbox, "held_bullets": held, "topic_sections": topics, "legacy_heading": legacy,
            "question_assertions": question_assertions}


def in_compile_debt(metrics, min_inbox, min_orphan_inbox):
    inbox = metrics["inbox_bullets"] + metrics.get("question_assertions", 0)
    return inbox >= min_inbox or (metrics["topic_sections"] == 0 and inbox >= min_orphan_inbox)


def compile_debt_row(path, text, min_inbox, min_orphan_inbox):
    fm, body = split_frontmatter(text)
    metrics = compile_metrics(body)
    title = fm.get("title")
    if not title or isinstance(title, list):
        title = path.stem
    row = {
        "path": path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix(),
        "title": str(title).strip() or path.stem,
        "lines": len(text.splitlines()),
        "has_child_section": bool(CHILD_HEADING_RE.search(text)),
    }
    row.update(metrics)
    row["in_debt"] = in_compile_debt(metrics, min_inbox, min_orphan_inbox)
    return row


H1_RE = re.compile(r"^# \S", flags=re.M)
BODY_CITATION_RE = re.compile(r"\[\[(?:[^\]|#]*/)?@")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", flags=re.M | re.S)
STRUCTURE_PROBLEMS = ("missing_h1_title", "missing_definition", "missing_sources_section",
                      "empty_frontmatter_sources")


def structure_problems(text):
    """concept ページの骨格の欠落。STRUCTURE_PROBLEMS のうち当たるものを順に返す。"""
    fm, body = split_frontmatter(text)
    plain = FENCE_RE.sub("", body)
    problems = []
    if not H1_RE.search(plain):
        problems.append("missing_h1_title")
    headings = [h for h, _ in h2_sections(plain)]
    if not any(section_kind(h) == KIND_DEFINITION for h in headings):
        problems.append("missing_definition")
    if not any(base_heading(h) == "出典" for h in headings):
        problems.append("missing_sources_section")
    sources = fm.get("sources")
    empty = sources is None or (isinstance(sources, list) and not [x for x in sources if str(x).strip()]) \
        or (isinstance(sources, str) and not sources.strip())
    if empty and BODY_CITATION_RE.search(plain):
        problems.append("empty_frontmatter_sources")
    return problems


def structure_row(path, text):
    fm, _ = split_frontmatter(text)
    title = fm.get("title")
    if not title or isinstance(title, list):
        title = path.stem
    return {"path": path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix(),
            "title": str(title).strip() or path.stem, "problems": structure_problems(text)}


def scan_structure():
    rows = []
    for path in list_concept_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        row = structure_row(path, text)
        if row["problems"]:
            rows.append(row)
    rows.sort(key=lambda r: r["path"])
    return rows


def resolve_concept_page(page):
    """Exact path or exact title/stem. Never a substring match."""
    raw = page.strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2].split("|", 1)[0]
    candidate = Path(raw)
    for p in (candidate, VAULT_ROOT / candidate):
        if p.suffix == ".md" and p.is_file():
            return p
    name = raw[:-3] if raw.endswith(".md") else raw
    path = VAULT_ROOT / "wiki" / "concepts" / f"{Path(name).name}.md"
    return path if path.is_file() else None


def scan_compile_debt(min_inbox, min_orphan_inbox):
    rows = []
    for path in list_concept_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = split_frontmatter(text)
        metrics = compile_metrics(body)
        inbox = metrics["inbox_bullets"]
        if not in_compile_debt(metrics, min_inbox, min_orphan_inbox):
            continue
        title = fm.get("title")
        if not title or isinstance(title, list):
            title = path.stem
        rel = path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
        row = {
            "path": rel,
            "title": str(title).strip() or path.stem,
            "lines": len(text.splitlines()),
            "has_child_section": bool(CHILD_HEADING_RE.search(text)),
        }
        row.update(metrics)
        rows.append(row)
    rows.sort(key=lambda r: (-r["inbox_bullets"], r["path"]))
    return rows


def list_concept_files():
    concepts = VAULT_ROOT / "wiki" / "concepts"
    if not concepts.is_dir():
        return []
    out = []
    for name in os.listdir(concepts):
        if not name.endswith(".md") or name == "_index.md":
            continue
        path = concepts / name
        if path.is_file():
            out.append(path)
    return out


def scan_hubs(min_bytes, min_lines):
    rows = []
    for path in list_concept_files():
        try:
            st = path.stat()
        except OSError:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = len(text.splitlines())
        if st.st_size < min_bytes and lines < min_lines:
            continue
        fm, body = split_frontmatter(text)
        title = fm.get("title")
        if not title or isinstance(title, list):
            title = path.stem
        else:
            title = str(title).strip() or path.stem
        hub_names = {path.stem, title}
        rel = path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
        row = {
            "path": rel,
            "title": title,
            "bytes": int(st.st_size),
            "lines": lines,
            "has_child_section": bool(CHILD_HEADING_RE.search(text)),
            "child_candidates": child_candidates_from_related(
                fm.get("related"), hub_names, min_bytes, min_lines
            ),
            "_text": text,
            "_abs": path,
            "_hub_names": hub_names,
        }
        row.update(compile_metrics(body))
        rows.append(row)
    rows.sort(key=lambda r: (-r["bytes"], r["path"]))
    return rows


def existing_child_names(text):
    match = CHILD_HEADING_RE.search(text)
    if not match:
        return []
    nxt = H2_RE.search(text, match.end())
    block = text[match.end(): nxt.start() if nxt else len(text)]
    names = []
    for item in WIKILINK_RE.findall(block):
        name = item.strip()
        if name and name not in names:
            names.append(name)
    return names


def promote_hubs(hubs, repair=False):
    updated = 0
    for row in hubs:
        children = row["child_candidates"]
        has = row["has_child_section"]
        current = existing_child_names(row["_text"]) if has else []
        if has and current == children:
            continue
        if not has and not children:
            continue
        if has and not repair and current:
            continue
        new_text = replace_child_section(row["_text"], children)
        new_text = bump_updated(new_text)
        if new_text == row["_text"]:
            continue
        with page_lock(row["path"]):
            atomic_write(row["_abs"], new_text)
        row["has_child_section"] = bool(children)
        row["_text"] = new_text
        updated += 1
    return updated


def main(argv=None):
    parser = argparse.ArgumentParser(description="Concept hub stats and optional 子概念 stubs.")
    parser.add_argument("--json", action="store_true", help="Print JSON (always on; accepted for CLI parity)")
    parser.add_argument("--min-bytes", type=int, default=50000)
    parser.add_argument("--min-lines", type=int, default=250)
    parser.add_argument("--promote-children", action="store_true")
    parser.add_argument("--repair-children", action="store_true",
                        help="Rewrite existing ## 子概念; drop hub-sized related pages.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--compile-debt", action="store_true",
                        help="List pages whose 未編纂の観察 (legacy 横断的知見) inbox needs recompiling.")
    parser.add_argument("--min-inbox", type=int, default=DEFAULT_MIN_INBOX,
                        help="Inbox bullets that flag compile debt (default 5).")
    parser.add_argument("--min-orphan-inbox", type=int, default=DEFAULT_MIN_ORPHAN_INBOX,
                        help="Inbox bullets that flag debt when the page has no topic section (default 15).")
    parser.add_argument("--structure", action="store_true",
                        help="List concept pages missing # title / ## 定義 / ## 出典, or with empty frontmatter "
                             "sources: while the body cites [[@...]].")
    parser.add_argument("--page", help="With --compile-debt / --structure: judge one page (exact path or title). "
                                       "Exit 1 if in debt / broken.")
    args = parser.parse_args(argv)

    if args.min_bytes < 0 or args.min_lines < 0:
        log("ERR: --min-bytes and --min-lines must be >= 0")
        return EXIT_USAGE
    if args.limit < 1:
        log("ERR: --limit must be >= 1")
        return EXIT_USAGE
    if args.min_inbox < 0 or args.min_orphan_inbox < 0:
        log("ERR: --min-inbox and --min-orphan-inbox must be >= 0")
        return EXIT_USAGE

    if args.page and not (args.compile_debt or args.structure):
        log("ERR: --page requires --compile-debt or --structure")
        return EXIT_USAGE

    if args.structure:
        if args.compile_debt or args.promote_children or args.repair_children:
            log("ERR: --structure cannot be combined with --compile-debt / --promote-children / --repair-children")
            return EXIT_USAGE
        if args.page:
            path = resolve_concept_page(args.page)
            if path is None:
                log(f"ERR: page not found: {args.page}")
                return EXIT_MISSING
            row = structure_row(path, path.read_text(encoding="utf-8"))
            print(json.dumps(row, ensure_ascii=False, indent=2))
            return EXIT_IN_DEBT if row["problems"] else EXIT_OK
        rows = scan_structure()
        counts = {p: sum(1 for r in rows if p in r["problems"]) for p in STRUCTURE_PROBLEMS}
        log(f"wiki-concept-stats: {len(rows)} of {len(list_concept_files())} concept page(s) with structure "
            "problems (" + ", ".join(f"{k} {v}" for k, v in counts.items()) + f"); showing {min(len(rows), args.limit)}")
        print(json.dumps(rows[: args.limit], ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.compile_debt:
        if args.promote_children or args.repair_children:
            log("ERR: --compile-debt cannot be combined with --promote-children / --repair-children")
            return EXIT_USAGE
        if args.page:
            path = resolve_concept_page(args.page)
            if path is None:
                log(f"ERR: page not found: {args.page}")
                return EXIT_MISSING
            row = compile_debt_row(path, path.read_text(encoding="utf-8"), args.min_inbox, args.min_orphan_inbox)
            print(json.dumps(row, ensure_ascii=False, indent=2))
            return EXIT_IN_DEBT if row["in_debt"] else EXIT_OK
        debt = scan_compile_debt(args.min_inbox, args.min_orphan_inbox)
        total = len(list_concept_files())
        log(f"wiki-concept-stats: {len(debt)} of {total} concept page(s) in compile debt; "
            f"showing {min(len(debt), args.limit)} (--limit {args.limit})")
        print(json.dumps(debt[: args.limit], ensure_ascii=False, indent=2))
        return EXIT_OK

    hubs = scan_hubs(args.min_bytes, args.min_lines)
    if args.promote_children or args.repair_children:
        n = promote_hubs(hubs, repair=bool(args.repair_children or args.promote_children))
        log(f"wiki-concept-stats: updated {n} file(s)")

    public = []
    for row in hubs[: args.limit]:
        public.append({
            "path": row["path"],
            "title": row["title"],
            "bytes": row["bytes"],
            "lines": row["lines"],
            "has_child_section": row["has_child_section"],
            "child_candidates": row["child_candidates"],
            "inbox_bullets": row["inbox_bullets"],
            "held_bullets": row["held_bullets"],
            "topic_sections": row["topic_sections"],
            "legacy_heading": row["legacy_heading"],
        })
    log(f"wiki-concept-stats: {len(public)} hub(s) (of {len(hubs)})")
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
