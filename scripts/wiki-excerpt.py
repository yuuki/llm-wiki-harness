#!/usr/bin/env python3
"""wiki-excerpt.py — compact frontmatter + selected ## sections.

Usage:
  wiki-excerpt.py PATH [--sections A,B,C] [--tail 15] [--budget-tokens 1800]
  wiki-excerpt.py PATH --outline [--budget-tokens 1800]
  wiki-excerpt.py PATH1 PATH2 PATH3 --outline
  wiki-excerpt.py PATH1 PATH2 --fm-keys title,aliases   (--fm-keys none omits it)

Read every page of one step in ONE call: one call costs one resident-context
read, so N single-page calls cost N of them.

PATH is vault-relative or absolute. Markdown is printed to stdout.
Missing named sections are skipped. Missing file → exit 3.

With several PATHs each page block is introduced by a `=== <vault-relative
path> ===` line; a missing page becomes `=== path === (missing)` (still exit 3,
the other pages are printed), and once --total-budget is used up the remaining
*existing* pages become `=== path === (skipped: total budget)`. A missing page
after the cap is still `(missing)` and still exit 3. Arguments that start with
`#` are query-separator lines from wiki-resolve --paths-only and are ignored.
A single PATH prints exactly what it always did — no separator line.
Use --total-budget only when it is at least N * --budget-tokens.

Section aliases: the concept inbox is `未編纂の観察`; its legacy name
`横断的知見` resolves to the same block (whichever heading exists).

--outline prints the page shape only: every ## heading, ### headings and
the bold claim line of each top-level bullet inside topic sections
(all ## that are not one of the fixed concept sections), plus bullet
counts for the fixed sections. No body text.

Exit codes:
  0 — success
  2 — usage error
  3 — file missing
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

COMPACT_SCALARS = (
    "title", "type", "aliases", "address", "entity_tier",
    "complexity", "domain", "role",
)
LIST_CAPS = (("related", 8), ("sources", 8))
INBOX_SECTION = "未編纂の観察"
LEGACY_INBOX_SECTION = "横断的知見"
SECTION_ALIASES = {
    INBOX_SECTION: (LEGACY_INBOX_SECTION,),
    LEGACY_INBOX_SECTION: (INBOX_SECTION,),
}
CONCEPT_SECTIONS = ("定義", "子概念", "未解決の問い", INBOX_SECTION)
TAIL_MARKERS = (INBOX_SECTION, LEGACY_INBOX_SECTION, "未解決の問い")
# Fixed (functional) concept sections; every other ## is a topic section.
FIXED_SECTIONS = ("定義", "子概念", "未解決の問い", INBOX_SECTION, LEGACY_INBOX_SECTION, "関連", "出典")
DEFAULT_TAIL = 15
DEFAULT_BUDGET = 1800
BODY_LINE_CAP = 80
TRUNCATION_NOTE = "<!-- excerpt-truncated: over --budget-tokens; dropped older bullets / extra 定義 -->\n"
FM_NONE = "\0fm-none"  # sentinel for --fm-keys none
FM_KEYS_AVAILABLE = COMPACT_SCALARS + tuple(key for key, _ in LIST_CAPS)

QUIET = False


def log(msg):
    if QUIET:
        return
    print(msg, file=sys.stderr)


def err(msg):
    """Usage/failure line; printed even under --quiet."""
    print(msg, file=sys.stderr)


def estimate_tokens(text):
    if not text:
        return 0
    return max(1, len(text) // 3)


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


def yaml_scalar(value):
    s = str(value)
    if (
        s == ""
        or s != s.strip()
        or s.startswith("[[")
        or any(ch in s for ch in ":#{}[]&*!|>'\"%@")
        or s in ("true", "false", "null", "True", "False", "None")
    ):
        return json.dumps(s, ensure_ascii=False)
    return s


def emit_compact_frontmatter(fm, keys=None):
    """keys=None keeps every compact key; a key list narrows it (empty → "")."""
    wanted = None if keys is None else set(keys)
    emitted = 0
    lines = ["---"]
    for key in COMPACT_SCALARS:
        if key not in fm:
            continue
        if wanted is not None and key not in wanted:
            continue
        val = fm[key]
        if val is None or val == "":
            continue
        if key == "aliases":
            items = as_list(val)
            if not items:
                continue
            lines.append("aliases:")
            for item in items:
                lines.append(f"  - {yaml_scalar(item)}")
            emitted += 1
            continue
        if isinstance(val, list):
            if not val:
                continue
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {yaml_scalar(item)}")
            emitted += 1
            continue
        lines.append(f"{key}: {yaml_scalar(val)}")
        emitted += 1
    for key, cap in LIST_CAPS:
        if wanted is not None and key not in wanted:
            continue
        items = as_list(fm.get(key))
        if not items:
            continue
        lines.append(f"{key}:")
        for item in items[:cap]:
            lines.append(f"  - {yaml_scalar(item)}")
        emitted += 1
    lines.append("---")
    if wanted is not None and emitted == 0:
        return ""
    return "\n".join(lines) + "\n"


def resolve_path(raw):
    path = Path(raw)
    if not path.is_absolute():
        path = VAULT_ROOT / path
    try:
        resolved = path.resolve()
        resolved.relative_to(VAULT_ROOT.resolve())
    except ValueError:
        err(f"ERR: path escapes vault: {raw}")
        return None
    return resolved


def split_h2_sections(body):
    matches = list(re.finditer(r"^## ", body, flags=re.M))
    if not matches:
        return body, []
    preamble = body[: matches[0].start()]
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[match.start():end]
        heading_line = block.split("\n", 1)[0]
        heading = heading_line[3:].strip()
        sections.append((heading, block))
    return preamble, sections


def bullet_items(lines):
    """Group a section's lines into top-level bullet items.

    An item is a `- ` / `* ` line plus the indented lines that follow it
    (nested evidence bullets, continuation lines). Returns a list of
    (start_idx, end_idx_exclusive) tuples.
    """
    items = []
    i = 0
    while i < len(lines):
        if re.match(r"^[-*] ", lines[i]):
            j = i + 1
            while j < len(lines) and lines[j].strip() != "" and lines[j][:1] in (" ", "\t"):
                j += 1
            items.append((i, j))
            i = j
            continue
        i += 1
    return items


def apply_tail(block, n):
    if n is None:
        return block
    lines = block.splitlines(keepends=True)
    items = bullet_items(lines)
    if n <= 0:
        drop_items = items
    elif len(items) <= n:
        return block
    else:
        drop_items = items[:-n]
    drop = set()
    for start, end in drop_items:
        drop.update(range(start, end))
    kept = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        if line.strip() == "" and kept and kept[-1].strip() == "":
            continue
        kept.append(line)
    return "".join(kept)


def resolve_section(name, by_heading):
    """Return (actual_heading, block) honouring SECTION_ALIASES, or (None, None)."""
    if name in by_heading:
        return name, by_heading[name]
    for alias in SECTION_ALIASES.get(name, ()):
        if alias in by_heading:
            return alias, by_heading[alias]
    return None, None


def claim_line(item_text, cap=90):
    """Bold lead of a top-level bullet, else a truncated first line."""
    first = item_text.splitlines()[0] if item_text else ""
    body = re.sub(r"^[-*] ", "", first).strip()
    m = re.match(r"\*\*(.+?)\*\*", body)
    if m:
        text = m.group(1).strip()
    else:
        text = body
    text = re.sub(r"\s+", " ", text)
    if len(text) > cap:
        text = text[:cap].rstrip() + "…"
    return text


def render_outline(rel, sections):
    out = [f"# outline: {rel}\n"]
    for heading, block in sections:
        lines = block.splitlines(keepends=True)
        items = bullet_items(lines)
        if heading in FIXED_SECTIONS:
            label = heading
            if heading == LEGACY_INBOX_SECTION:
                label = f"{heading} (旧名; = {INBOX_SECTION})"
            if heading == "定義":
                out.append(f"## {label}\n")
            else:
                out.append(f"## {label}  ({len(items)} 件)\n")
            continue
        if items:
            out.append(f"## {heading}  ({len(items)} 命題)\n")
        else:
            prose = sum(1 for ln in lines[1:] if ln.strip() and not ln.startswith("#"))
            out.append(f"## {heading}  (散文 {prose} 行)\n")
        # Walk in order so ### sub-headings interleave with their bullets.
        item_starts = {start: end for start, end in items}
        i = 1  # skip the heading line itself
        while i < len(lines):
            line = lines[i]
            if line.startswith("### "):
                out.append(line if line.endswith("\n") else line + "\n")
                i += 1
                continue
            if i in item_starts:
                end = item_starts[i]
                out.append(f"- {claim_line(''.join(lines[i:end]))}\n")
                i = end
                continue
            m = re.match(r"^> \[!(\w+)\]", line)
            if m:
                out.append(f"> [!{m.group(1)}]\n")
            i += 1
    return "".join(out)


def first_paragraph_of_section(block):
    lines = block.splitlines(keepends=True)
    if not lines:
        return block
    out = [lines[0]]
    started = False
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if not started:
            if line.strip() == "":
                out.append(line)
                continue
            started = True
            out.append(line)
            continue
        if line.strip() == "":
            break
        out.append(line)
    return "".join(out)


def shrink_for_budget(named_blocks, budget):
    """named_blocks: list of (name, text). Mutates copies until under budget."""
    blocks = list(named_blocks)
    truncated = False

    def render():
        return "\n".join(text.rstrip("\n") for _, text in blocks if text).rstrip() + "\n"

    out = render()
    if estimate_tokens(out) <= budget:
        return blocks, False

    # Over budget: drop extra 定義 prose first (low retrieval density),
    # then older tail bullets, then a hard character cap.
    next_blocks = []
    for name, text in blocks:
        if name == "定義" or name.endswith("定義"):
            collapsed = first_paragraph_of_section(text)
            if collapsed != text:
                truncated = True
            next_blocks.append((name, collapsed))
        else:
            next_blocks.append((name, text))
    blocks = next_blocks

    tail_n = None
    for name, text in blocks:
        if any(marker in name for marker in TAIL_MARKERS):
            n = len(re.findall(r"^[-*] ", text, flags=re.M))
            tail_n = n if tail_n is None else max(tail_n, n)

    if tail_n is None:
        tail_n = 0
    while estimate_tokens(render()) > budget and tail_n > 0:
        tail_n -= 1
        truncated = True
        next_blocks = []
        for name, text in blocks:
            if any(marker in name for marker in TAIL_MARKERS):
                next_blocks.append((name, apply_tail(text, tail_n)))
            else:
                next_blocks.append((name, text))
        blocks = next_blocks

    out = render()
    if estimate_tokens(out) > budget:
        cap = max(200, budget * 3)
        cut = out[:cap].rstrip() + "\n"
        blocks = [("_truncated", cut)]
        truncated = True
    return blocks, truncated


def title_line(preamble):
    for line in preamble.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line if line.endswith("\n") else line + "\n"
    return ""


def default_section_names(fm, rel, headings):
    typ = str(fm.get("type") or "")
    if typ == "concept" or "/concepts/" in rel:
        return list(CONCEPT_SECTIONS)
    if typ == "entity" or "/entities/" in rel:
        present = [name for name in CONCEPT_SECTIONS + (LEGACY_INBOX_SECTION,) if name in headings]
        if present:
            return present
        return None
    return None


def first_n_body_lines(body, n):
    lines = body.splitlines(keepends=True)
    return "".join(lines[:n])


def render_page(rel, text, args, fm_keys):
    """One page's excerpt. Returns (out_text, truncated)."""
    fm, body = split_frontmatter(text)
    preamble, sections = split_h2_sections(body)
    headings = [h for h, _ in sections]
    by_heading = {h: block for h, block in sections}

    if args.outline:
        out = render_outline(rel, sections)
        if estimate_tokens(out) > args.budget_tokens:
            return out[: max(200, args.budget_tokens * 3)].rstrip() + "\n" + TRUNCATION_NOTE, True
        return out, False

    if args.sections is not None:
        named = [part.strip() for part in args.sections.split(",") if part.strip()]
    else:
        named = default_section_names(fm, rel, headings)

    named_blocks = []
    if fm_keys != FM_NONE:
        block = emit_compact_frontmatter(fm, fm_keys)
        if block:
            named_blocks.append(("frontmatter", block))
    if named is None:
        if "/entities/" in rel or str(fm.get("type") or "") == "entity":
            title = title_line(preamble)
            if title:
                named_blocks.append(("title", title if title.endswith("\n") else title + "\n"))
            if sections:
                named_blocks.append((sections[0][0], sections[0][1].rstrip() + "\n"))
            elif not title:
                named_blocks.append(("body", first_n_body_lines(body, BODY_LINE_CAP)))
        else:
            named_blocks.append(("body", first_n_body_lines(body, BODY_LINE_CAP)))
    else:
        seen = set()
        for name in named:
            actual, block = resolve_section(name, by_heading)
            if block is None or actual in seen:
                continue
            seen.add(actual)
            if any(marker in actual for marker in TAIL_MARKERS):
                block = apply_tail(block, args.tail)
            named_blocks.append((actual, block.rstrip() + "\n"))

    named_blocks, truncated = shrink_for_budget(named_blocks, args.budget_tokens)
    out = "\n".join(block.rstrip("\n") for _, block in named_blocks if block).rstrip() + "\n"
    if truncated:
        out = out.rstrip() + "\n" + TRUNCATION_NOTE
    return out, truncated


def parse_fm_keys(raw):
    """None → every compact key (legacy); "none" → no frontmatter; else a key list."""
    if raw is None:
        return None
    if raw.strip().lower() == "none":
        return FM_NONE
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [key for key in keys if key not in FM_KEYS_AVAILABLE]
    if unknown:
        err("wiki-excerpt: unknown --fm-keys: " + ",".join(unknown))
    return keys


def rel_of(path, raw):
    try:
        return path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()
    except ValueError:
        return raw


def main(argv=None):
    global QUIET
    parser = argparse.ArgumentParser(description="Emit compact wiki page excerpts.")
    parser.add_argument("paths", nargs="+", help="Vault-relative or absolute page paths")
    parser.add_argument("--sections", default=None, help="Comma-separated ## heading names")
    parser.add_argument("--tail", type=int, default=DEFAULT_TAIL,
                        help="Keep last N bullets in 未編纂の観察(旧名 横断的知見) / 未解決の問い (default 15)")
    parser.add_argument("--budget-tokens", type=int, default=DEFAULT_BUDGET,
                        help="Per-page cap on emitted tokens (default 1800). Drops extra 定義 prose, then older bullets.")
    parser.add_argument("--total-budget", type=int, default=None,
                        help="Cap across all pages; once spent, remaining existing pages "
                             "are skipped. Set to at least N * --budget-tokens. "
                             "Missing pages are still reported as missing (exit 3)")
    parser.add_argument("--fm-keys", default=None,
                        help="Emit only these frontmatter keys ("
                             + ",".join(FM_KEYS_AVAILABLE) + "); `none` omits frontmatter")
    parser.add_argument("--outline", action="store_true",
                        help="Print page shape only: headings, bullet counts, bold claim lines of topic sections.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the stderr summary")
    args = parser.parse_args(argv)

    QUIET = args.quiet

    if args.tail is not None and args.tail < 0:
        err("ERR: --tail must be >= 0")
        return EXIT_USAGE
    if args.budget_tokens < 1:
        err("ERR: --budget-tokens must be >= 1")
        return EXIT_USAGE
    if args.total_budget is not None and args.total_budget < 1:
        err("ERR: --total-budget must be >= 1")
        return EXIT_USAGE
    fm_keys = parse_fm_keys(args.fm_keys)

    targets = []
    for raw in args.paths:
        raw = raw.strip()
        # wiki-resolve --paths-only emits `# <query>` separators; ignore them.
        if not raw or raw.startswith("#"):
            continue
        path = resolve_path(raw)
        if path is None:
            return EXIT_USAGE
        targets.append((raw, path))
    if not targets:
        err("ERR: no page path given")
        return EXIT_USAGE

    multi = len(targets) > 1
    chunks = []
    spent = 0
    missing = 0
    skipped = 0
    truncated_single = False
    single_rel = ""
    for raw, path in targets:
        rel = rel_of(path, raw)
        # spent is 0 on the first page, so a single page is never skipped.
        if args.total_budget is not None and spent >= args.total_budget:
            if not path.is_file():
                chunks.append(f"=== {rel} === (missing)\n")
                missing += 1
            else:
                chunks.append(f"=== {rel} === (skipped: total budget)\n")
                skipped += 1
            continue
        text = None
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                err(f"ERR: cannot read {raw}: {e}")
                if not multi:
                    return EXIT_MISSING
        elif not multi:
            err(f"ERR: missing file: {raw}")
            return EXIT_MISSING
        if text is None:
            chunks.append(f"=== {rel} === (missing)\n")
            missing += 1
            continue

        out, truncated = render_page(rel, text, args, fm_keys)
        spent += estimate_tokens(out)
        if multi:
            chunks.append(f"=== {rel} ===\n" + out)
        else:
            chunks.append(out)
            truncated_single = truncated
            single_rel = rel

    out_text = "\n".join(chunks)
    tokens = estimate_tokens(out_text)
    if multi:
        extra = []
        if missing:
            extra.append(f"{missing} missing")
        if skipped:
            extra.append(f"{skipped} skipped")
        log(f"wiki-excerpt: {len(targets)} pages ~{tokens} tokens"
            f"{' (' + ', '.join(extra) + ')' if extra else ''}")
    else:
        log(f"wiki-excerpt: {single_rel}{' outline' if args.outline else ''} ~{tokens} tokens"
            f"{' (truncated)' if truncated_single else ''}")
    sys.stdout.write(out_text)
    return EXIT_MISSING if missing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
