"""wiki_claims.py — concept / question / survey ページから命題を抽出する。

claim-audit と wiki-graph が共有する。wiki 本文は書かない。
`text` を渡した呼び出しはディスクを読まない。
"""

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
PROPOSITION_RE = re.compile(r"^- \*\*(.+?)\*\*\s*(.*)$")
EVIDENCE_RE = re.compile(r"^\s{2,}- (根拠|反証|留保|関連):\s*(.*)$")
TOP_BULLET_RE = re.compile(r"^- (.*)$")


def _load_stats():
    spec = importlib.util.spec_from_file_location("wiki_concept_stats", _SCRIPTS / "wiki-concept-stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STATS = _load_stats()


def link_basename(target):
    """wiki-graph.link_basename と同じ規則。こちらから wiki-graph は import しない。"""
    t = target.strip()
    if not t or t.startswith((".raw/", "_attachments/", "papers/", "research/", "structures/", "notes/", "books/")):
        return None
    if t.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf", ".gif", ".webp", ".json")):
        return None
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if t.endswith(".md"):
        t = t[:-3]
    return t or None


def claim_id(page_rel, text):
    norm = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(f"{page_rel}\n{norm}".encode("utf-8")).hexdigest()[:10]


def source_names(text):
    return [t.strip() for t in WIKILINK_RE.findall(text) if t.strip().startswith("@")]


def claim_targets(claim_text):
    """太字命題上の非 @ wikilink stem。根拠行は見ない。"""
    out = []
    for t in WIKILINK_RE.findall(claim_text):
        base = link_basename(t)
        if not base or base.startswith("@"):
            continue
        if base not in out:
            out.append(base)
    return out


def extract_claims(page_rel, include_inbox=False, text=None):
    if text is None:
        path = VAULT_ROOT / page_rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
    fm, body = STATS.split_frontmatter(text)
    del fm
    fm_lines = len(text.splitlines()) - len(body.splitlines())
    claims = []
    for heading, block in STATS.h2_sections(body):
        is_inbox = heading in (STATS.INBOX_HEADING, STATS.LEGACY_INBOX_HEADING)
        if heading in STATS.FIXED_HEADINGS and not (is_inbox and include_inbox):
            continue
        block_start = body.find(block)
        line_base = body[:block_start].count("\n") + fm_lines + 1
        lines = block.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if is_inbox:
                m = TOP_BULLET_RE.match(line)
                if m and source_names(line):
                    claims.append({
                        "kind": "observation", "page": page_rel, "section": heading,
                        "line": line_base + i, "claim": m.group(1).strip(),
                        "sources": source_names(line), "evidence_lines": [],
                        "targets": [],
                    })
                i += 1
                continue
            m = PROPOSITION_RE.match(line)
            if not m:
                i += 1
                continue
            claim_text = m.group(1).strip()
            sources = source_names(line)
            evidence_lines = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip() == ""):
                ev = EVIDENCE_RE.match(lines[j])
                if ev:
                    evidence_lines.append(lines[j].strip())
                    if ev.group(1) in ("根拠", "反証"):
                        sources.extend(source_names(lines[j]))
                j += 1
            if sources:
                dedup = []
                for s in sources:
                    if s not in dedup:
                        dedup.append(s)
                claims.append({
                    "kind": "proposition", "page": page_rel, "section": heading,
                    "line": line_base + i, "claim": claim_text,
                    "sources": dedup, "evidence_lines": evidence_lines,
                    "targets": claim_targets(claim_text),
                })
            i = j
    for c in claims:
        c["id"] = claim_id(page_rel, c["claim"])
    return claims
