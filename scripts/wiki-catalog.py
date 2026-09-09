#!/usr/bin/env python3
"""wiki-catalog.py — append/trim catalog files without full-document edits.

Subcommands write UTF-8 via tmp+replace and print JSON
{"ok":true,"path":"...","action":"..."} on success.

Exit codes:
  0 — success
  2 — usage error
  3 — target file missing (trim-hot / splice commands)
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_lock import page_lock

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")


def log(msg):
    print(msg, file=sys.stderr)


def estimate_tokens(text):
    if not text:
        return 0
    return max(1, len(text) // 3)


def ensure_nl(text):
    if text.endswith("\n"):
        return text
    return text + "\n"


def resolve_vault_path(raw):
    path = Path(raw)
    if not path.is_absolute():
        path = VAULT_ROOT / path
    try:
        resolved = path.resolve()
        resolved.relative_to(VAULT_ROOT.resolve())
    except ValueError:
        log(f"ERR: path escapes vault: {raw}")
        return None
    return resolved


def rel_posix(path):
    return path.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()


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


def split_hash_entries(text):
    matches = list(re.finditer(r"^## ", text, flags=re.M))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    entries = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries.append(text[match.start():end])
    return preamble, entries


def compress_hot_entry(entry):
    lines = entry.splitlines(keepends=True)
    if not lines:
        return entry
    kept = [lines[0]]
    for line in lines[1:]:
        if "Focus:" in line or "Key insight:" in line:
            kept.append(line)
    return "".join(kept)


def trim_hot_text(text, budget_tokens, max_entries):
    preamble, entries = split_hash_entries(text)
    if not entries:
        return text
    kept = []
    for i, entry in enumerate(entries):
        if i >= max_entries:
            break
        trial = preamble + "".join(kept) + entry
        if estimate_tokens(trial) > budget_tokens:
            if not kept:
                kept.append(compress_hot_entry(entry))
            break
        kept.append(entry)
    out = preamble + "".join(kept)
    return ensure_nl(out) if out else out


def split_frontmatter_raw(text):
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[: i + 1]), "".join(lines[i + 1:])
    return "", text


def is_h1(line):
    stripped = line.rstrip("\n")
    return stripped.startswith("# ") and not stripped.startswith("## ")


def insert_after_fm_and_title(text, insertion):
    insertion = ensure_nl(insertion)
    fm_raw, rest = split_frontmatter_raw(text)
    lines = rest.splitlines(keepends=True)
    idx = 0
    prefix = [fm_raw] if fm_raw else []
    while idx < len(lines) and lines[idx].strip() == "":
        prefix.append(lines[idx])
        idx += 1
    if idx < len(lines) and is_h1(lines[idx]):
        prefix.append(lines[idx])
        idx += 1
        if idx < len(lines) and lines[idx].strip() == "":
            prefix.append(lines[idx])
            idx += 1
        else:
            prefix.append("\n")
        return "".join(prefix) + insertion + "".join(lines[idx:])
    return "".join(prefix) + insertion + "".join(lines[idx:])


def insert_after_master_title(text, insertion):
    insertion = ensure_nl(insertion)
    lines = text.splitlines(keepends=True)
    target = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "# Wiki Index":
            target = i
            break
    if target is None:
        for i, line in enumerate(lines):
            if is_h1(line):
                target = i
                break
    if target is None:
        return insertion + text
    prefix = "".join(lines[: target + 1])
    rest = lines[target + 1:]
    if rest and rest[0].strip() == "":
        prefix += rest[0]
        rest = rest[1:]
    else:
        prefix += "\n"
    return prefix + insertion + "".join(rest)


def wikilink_target(line):
    match = WIKILINK_RE.search(line)
    if not match:
        return None
    return match.group(1).strip()


def wikilink_sort_key(line):
    target = wikilink_target(line)
    return target if target is not None else line.strip()


def add_catalog_line(text, heading, line):
    line = ensure_nl(line)
    heading_re = re.compile(r"^## " + re.escape(heading) + r"\s*$", flags=re.M)
    match = heading_re.search(text)
    if not match:
        suffix = "" if text.endswith("\n") or text == "" else "\n"
        return text + suffix + f"\n## {heading}\n\n{line}"

    after = match.end()
    next_h = re.search(r"^## ", text[after:], flags=re.M)
    sec_end = after + next_h.start() if next_h else len(text)
    section = text[match.start():sec_end]
    new_target = wikilink_target(line)
    for existing in section.splitlines():
        if new_target and wikilink_target(existing) == new_target:
            return text
        if existing.strip() == line.strip():
            return text

    sec_lines = section.splitlines(keepends=True)
    list_idxs = [i for i, ln in enumerate(sec_lines) if re.match(r"^[-*] ", ln)]
    insert_at = None
    new_key = wikilink_sort_key(line)
    for i in list_idxs:
        if new_key < wikilink_sort_key(sec_lines[i]):
            insert_at = i
            break
    if insert_at is None:
        if list_idxs:
            insert_at = list_idxs[-1] + 1
        else:
            insert_at = 1
            if insert_at < len(sec_lines) and sec_lines[insert_at].strip() == "":
                insert_at += 1
            insert_at = min(insert_at, len(sec_lines))
    sec_lines.insert(insert_at, line)
    return text[: match.start()] + "".join(sec_lines) + text[sec_end:]


def ok(path, action):
    print(json.dumps({"ok": True, "path": rel_posix(path), "action": action}, ensure_ascii=False))
    return EXIT_OK


def require_file(path, create_empty=False):
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if create_empty:
        return ""
    try:
        shown = rel_posix(path)
    except Exception:
        shown = str(path)
    log(f"ERR: missing file: {shown}")
    return None


def with_lock(path, fn):
    try:
        rel = rel_posix(path)
    except Exception:
        rel = path.name
    with page_lock(rel):
        return fn()


def cmd_prepend_log(path, text):
    def _write():
        existing = require_file(path, create_empty=True)
        if existing is None:
            return EXIT_MISSING
        atomic_write(path, ensure_nl(text) + existing)
        log(f"catalog prepend-log: {rel_posix(path)}")
        return ok(path, "prepend-log")

    return with_lock(path, _write)


def cmd_prepend_hot(path, text, budget, max_entries):
    def _write():
        existing = require_file(path, create_empty=True)
        if existing is None:
            return EXIT_MISSING
        merged = trim_hot_text(ensure_nl(text) + existing, budget, max_entries)
        atomic_write(path, merged)
        log(f"catalog prepend-hot: {rel_posix(path)} ~{estimate_tokens(merged)} tokens")
        return ok(path, "prepend-hot")

    return with_lock(path, _write)


def cmd_trim_hot(path, budget, max_entries):
    def _write():
        if not path.is_file():
            log(f"ERR: missing file: {path}")
            return EXIT_MISSING
        existing = path.read_text(encoding="utf-8")
        atomic_write(path, trim_hot_text(existing, budget, max_entries))
        log(f"catalog trim-hot: {rel_posix(path)}")
        return ok(path, "trim-hot")

    return with_lock(path, _write)


def cmd_prepend_changelog(path, text):
    def _write():
        if not path.is_file():
            log(f"ERR: missing file: {path}")
            return EXIT_MISSING
        existing = path.read_text(encoding="utf-8")
        atomic_write(path, insert_after_fm_and_title(existing, text))
        log(f"catalog prepend-changelog: {rel_posix(path)}")
        return ok(path, "prepend-changelog")

    return with_lock(path, _write)


def cmd_add_catalog_line(path, heading, line):
    def _write():
        if not path.is_file():
            log(f"ERR: missing file: {path}")
            return EXIT_MISSING
        existing = path.read_text(encoding="utf-8")
        atomic_write(path, add_catalog_line(existing, heading, line))
        log(f"catalog add-catalog-line: {rel_posix(path)} ## {heading}")
        return ok(path, "add-catalog-line")

    return with_lock(path, _write)


def cmd_prepend_master(path, text):
    def _write():
        if not path.is_file():
            log(f"ERR: missing file: {path}")
            return EXIT_MISSING
        existing = path.read_text(encoding="utf-8")
        atomic_write(path, insert_after_master_title(existing, text))
        log(f"catalog prepend-master: {rel_posix(path)}")
        return ok(path, "prepend-master")

    return with_lock(path, _write)


def add_file_arg(parser, default=None, required=False):
    parser.add_argument("--file", default=default, required=required,
                        help="Vault-relative or absolute path")


def add_text_arg(parser):
    parser.add_argument("--text", required=True, help="Markdown to insert")


def add_hot_budget_args(parser):
    parser.add_argument("--budget-tokens", type=int, default=2000)
    parser.add_argument("--max-entries", type=int, default=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Catalog prepend/trim helpers.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepend-log")
    add_file_arg(p, default="wiki/log.md")
    add_text_arg(p)

    p = sub.add_parser("prepend-hot")
    add_file_arg(p, default="wiki/hot.md")
    add_text_arg(p)
    add_hot_budget_args(p)

    p = sub.add_parser("trim-hot")
    add_file_arg(p, default="wiki/hot.md")
    add_hot_budget_args(p)

    p = sub.add_parser("prepend-changelog")
    add_file_arg(p, required=True)
    add_text_arg(p)

    p = sub.add_parser("add-catalog-line")
    add_file_arg(p, required=True)
    p.add_argument("--section", required=True, help="Exact ## heading text")
    p.add_argument("--line", required=True, help="Markdown list line to insert")

    p = sub.add_parser("prepend-master")
    add_file_arg(p, default="wiki/index.md")
    add_text_arg(p)

    args = parser.parse_args(argv)
    path = resolve_vault_path(args.file)
    if path is None:
        return EXIT_USAGE

    if args.cmd in ("prepend-hot", "trim-hot"):
        if args.budget_tokens < 1 or args.max_entries < 1:
            log("ERR: --budget-tokens and --max-entries must be >= 1")
            return EXIT_USAGE

    if args.cmd == "prepend-log":
        return cmd_prepend_log(path, args.text)
    if args.cmd == "prepend-hot":
        return cmd_prepend_hot(path, args.text, args.budget_tokens, args.max_entries)
    if args.cmd == "trim-hot":
        return cmd_trim_hot(path, args.budget_tokens, args.max_entries)
    if args.cmd == "prepend-changelog":
        return cmd_prepend_changelog(path, args.text)
    if args.cmd == "add-catalog-line":
        return cmd_add_catalog_line(path, args.section, args.line)
    if args.cmd == "prepend-master":
        return cmd_prepend_master(path, args.text)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
