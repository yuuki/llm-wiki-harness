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

from wiki_sections import KIND_INBOX, KIND_TOPIC, is_placeholder_bullet, section_kind  # noqa: E402


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
    """@ 付き source ページ名。パス付きの形(`[[wiki/sources/@X]]`)もファイル名だけの形と同じに扱う。"""
    out = []
    for t in WIKILINK_RE.findall(text):
        base = link_basename(t)
        if base and base.startswith("@"):
            out.append(base)
    return out


HUB_ENTITY_TYPES = ("thesis", "survey", "book")


def classify_evidence_link(target):
    """根拠行の非 @ リンクを分類する。

    source: `wiki/sources/<name>.md` が実在する(@ 無しの記事・ブログ等)
    hub: `wiki/entities/<name>.md` が博士論文・サーベイ・書籍のハブ entity
    None: それ以外(concept や人物など。出典として扱わない)
    """
    base = link_basename(target)
    if not base or base.startswith("@"):
        return None, base
    if (VAULT_ROOT / "wiki" / "sources" / f"{base}.md").is_file():
        return "source", base
    entity = VAULT_ROOT / "wiki" / "entities" / f"{base}.md"
    if entity.is_file():
        try:
            head = entity.read_text(encoding="utf-8")[:2000]
        except OSError:
            return None, base
        fm, _ = STATS.split_frontmatter(head if head.count("---") >= 2 else "")
        if str(fm.get("entity_type", "")).strip().strip('"') in HUB_ENTITY_TYPES:
            return "hub", base
    return None, base


def evidence_names(text):
    """根拠行から (sources, hubs, other_links) を返す。sources は @ ページと @ 無し source ページ。"""
    sources, hubs, others = [], [], []
    for t in WIKILINK_RE.findall(text):
        t = t.strip()
        base = link_basename(t)
        if base and base.startswith("@"):
            # `[[wiki/sources/@X]]` のようなパス付きの形も `[[@X]]` と同じ出典として数える
            sources.append(base)
            continue
        kind, base = classify_evidence_link(t)
        if kind == "source":
            sources.append(base)
        elif kind == "hub":
            hubs.append(base)
        elif base:
            others.append(base)
    return sources, hubs, others


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


def _dedup(items):
    out = []
    for item in items:
        if item not in out:
            out.append(item)
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
        # 節の分類は wiki-concept-stats と同じ(wiki_sections)。`## 参照` のような参照一覧の節は主題節ではない
        kind = section_kind(heading)
        is_inbox = kind == KIND_INBOX
        if kind != KIND_TOPIC and not (is_inbox and include_inbox):
            continue
        block_start = body.find(block)
        line_base = body[:block_start].count("\n") + fm_lines + 1
        lines = block.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if is_inbox:
                m = TOP_BULLET_RE.match(line)
                if m and is_placeholder_bullet(line):
                    m = None
                # 観察も根拠行と同じく、@ の付かない source ページとハブ entity を出典として拾う
                obs_sources, obs_hubs, _ = evidence_names(line) if m else ([], [], [])
                if m and (obs_sources or obs_hubs):
                    claims.append({
                        "kind": "observation", "page": page_rel, "section": heading,
                        "line": line_base + i, "claim": m.group(1).strip(),
                        "sources": _dedup(obs_sources), "hubs": _dedup(obs_hubs),
                        "evidence_lines": [], "targets": [],
                    })
                i += 1
                continue
            m = PROPOSITION_RE.match(line)
            if not m:
                i += 1
                continue
            claim_text = m.group(1).strip()
            sources = source_names(line)
            hubs, unresolved = [], []
            has_grounds = False
            evidence_lines = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip() == ""):
                ev = EVIDENCE_RE.match(lines[j])
                if ev:
                    evidence_lines.append(lines[j].strip())
                    if ev.group(1) in ("根拠", "反証"):
                        has_grounds = True
                        s_names, h_names, o_names = evidence_names(lines[j])
                        sources.extend(s_names)
                        hubs.extend(h_names)
                        unresolved.extend(o_names)
                j += 1
            if sources or hubs or has_grounds:
                # 根拠行があるのに検証可能な出典が 1 つも無い命題も落とさない(unverifiable として出す)
                claims.append({
                    "kind": "proposition", "page": page_rel, "section": heading,
                    "line": line_base + i, "claim": claim_text,
                    "sources": _dedup(sources), "hubs": _dedup(hubs),
                    "unresolved_links": _dedup(unresolved),
                    "unverifiable": not sources and not hubs,
                    "evidence_lines": evidence_lines,
                    "targets": claim_targets(claim_text),
                })
            i = j
    for c in claims:
        c["id"] = claim_id(page_rel, c["claim"])
    return claims
