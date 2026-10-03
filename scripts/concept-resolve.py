#!/usr/bin/env python3
"""concept-resolve.py — 概念の方言帰着(対の提案・lookup・新設ゲート)。

wiki ページは書き換えない。wiki-resolve / concept-candidates / wiki-page-write は
import しない。候補台帳は JSON を直接読む。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_lock import page_lock  # noqa: E402

LEDGER_REL = ".vault-meta/concept-merges.json"
CANDIDATES_REL = ".vault-meta/concept-candidates.json"
CONCEPTS_DIR = "wiki/concepts"
GRAPH_REL = ".vault-meta/graph.json"
INDEX_NAMES = frozenset({"index.md", "_index.md"})
FIXED_SECTIONS = frozenset(
    {"定義", "子概念", "未解決の問い", "未編纂の観察", "横断的知見", "関連", "出典"}
)
SHORT_ALIAS_EXACT = frozenset({
    "dp", "cv", "cam", "tas", "rss", "rfs", "rps", "cd", "mle", "tsr", "dsa", "zero",
})
TIER_RANK = {"weak": 0, "medium": 1, "strong": 2}
LINK_SCAN_DIRS = (
    "wiki/sources", "wiki/entities", "wiki/concepts",
    "wiki/questions", "wiki/surveys", "wiki/asks", "wiki/briefs",
)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
FM_LINE_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
FOLD_STRIP_RE = re.compile(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+")
TRAIL_PUNCT_RE = re.compile(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+$")
TRAIL_LATIN_RE = re.compile(r"[a-z]+$")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

DEFAULT_TABLE = {
    "dialects": {
        "alert": ["alert", "alerts", "アラート"],
        "alarm": ["alarm", "alarms", "アラーム"],
    },
    "dialect_groups": [
        ["alert", "alarm"],
    ],
    "suffixes": {
        "correlation": ["correlation", "correlations", "相関"],
        "management": ["management", "管理"],
    },
}

SEED_DECISIONS = {
    "オブザーバビリティ || 可観測性": {
        "state": "rejected",
        "keep": "オブザーバビリティ",
        "drop": "可観測性",
        "surfaces_keep": ["オブザーバビリティ", "Observability"],
        "surfaces_drop": ["可観測性"],
        "reason": "同名異物。Kalman の出力からの状態再構成と運用テレメトリは別",
        "day": "2026-09-13",
    },
}


def log(msg):
    print(msg, file=sys.stderr)


def vault_root():
    return Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()


def unquote(value):
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def pair_key(a, b):
    return " || ".join(sorted([a, b]))


def fold(s):
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return FOLD_STRIP_RE.sub("", s)


def names_match(a, b):
    return fold(a) == fold(b) and bool(fold(a))


def dialect_key(name, table):
    if table is None:
        raise TypeError("dialect_key requires table")
    s = unicodedata.normalize("NFKC", name or "").casefold()
    surfaces = []
    groups = table.get("dialect_groups") or []
    dialects = table.get("dialects") or {}
    group_rep = {}
    for row in groups:
        if not row:
            continue
        rep = row[0]
        for did in row:
            group_rep[did] = rep
    for did, names in dialects.items():
        rep = group_rep.get(did, did)
        for surf in names or []:
            surfaces.append((unicodedata.normalize("NFKC", surf).casefold(), rep))
    surfaces.sort(key=lambda x: (-len(x[0]), x[0]))
    for surf, rep in surfaces:
        if surf:
            s = s.replace(surf, rep)
    s = TRAIL_PUNCT_RE.sub("", s)
    suffix_pairs = []
    for rep, names in (table.get("suffixes") or {}).items():
        for surf in names or []:
            suffix_pairs.append((unicodedata.normalize("NFKC", surf).casefold(), rep))
    suffix_pairs.sort(key=lambda x: (-len(x[0]), x[0]))
    replaced = False
    for surf, rep in suffix_pairs:
        if surf and s.endswith(surf):
            s = s[: -len(surf)] + rep
            replaced = True
            break
    if not replaced:
        m = TRAIL_LATIN_RE.search(s)
        if m and m.group(0).endswith("s") and len(m.group(0)) > 1:
            word = m.group(0)[:-1]
            for surf, rep in suffix_pairs:
                if word == surf:
                    s = s[: m.start()] + rep
                    break
    return fold(s)


def in_surfaces(x, names):
    return any(names_match(x, n) for n in (names or []) if n)


def _surfaces_or_default(names, fallback):
    vals = [n for n in (names or []) if n]
    return vals if vals else [fallback]


def rejected_covers(name, page_stem):
    for dec in (load_ledger().get("decisions") or {}).values():
        if dec.get("state") != "rejected":
            continue
        keep_s = _surfaces_or_default(dec.get("surfaces_keep"), dec.get("keep"))
        drop_s = _surfaces_or_default(dec.get("surfaces_drop"), dec.get("drop"))
        if in_surfaces(name, drop_s) and in_surfaces(page_stem, keep_s):
            return True
        if in_surfaces(name, keep_s) and in_surfaces(page_stem, drop_s):
            return True
    return False


def is_merged_drop(name):
    table = ledger_table()
    for dec in (load_ledger().get("decisions") or {}).values():
        if dec.get("state") != "merged":
            continue
        drop = dec.get("drop") or ""
        if names_match(name, drop) or dialect_key(name, table) == dialect_key(drop, table):
            return True
    return False


def empty_ledger():
    return {
        "version": 1,
        "updated_at": None,
        "dialects": json.loads(json.dumps(DEFAULT_TABLE["dialects"])),
        "dialect_groups": json.loads(json.dumps(DEFAULT_TABLE["dialect_groups"])),
        "suffixes": json.loads(json.dumps(DEFAULT_TABLE["suffixes"])),
        "decisions": {},
    }


def ledger_path():
    return vault_root() / LEDGER_REL


def load_ledger():
    path = ledger_path()
    if not path.is_file():
        data = empty_ledger()
        data["decisions"] = json.loads(json.dumps(SEED_DECISIONS))
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = empty_ledger()
    data.setdefault("version", 1)
    data.setdefault("dialects", json.loads(json.dumps(DEFAULT_TABLE["dialects"])))
    data.setdefault("dialect_groups", json.loads(json.dumps(DEFAULT_TABLE["dialect_groups"])))
    data.setdefault("suffixes", json.loads(json.dumps(DEFAULT_TABLE["suffixes"])))
    data.setdefault("decisions", {})
    return data


def ledger_table(ledger=None):
    data = ledger if ledger is not None else load_ledger()
    return {
        "dialects": data.get("dialects") or DEFAULT_TABLE["dialects"],
        "dialect_groups": data.get("dialect_groups") or DEFAULT_TABLE["dialect_groups"],
        "suffixes": data.get("suffixes") or DEFAULT_TABLE["suffixes"],
    }


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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


def save_ledger(data):
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with page_lock(LEDGER_REL):
        atomic_write(ledger_path(), json.dumps(data, ensure_ascii=False, indent=1))


def split_frontmatter(text):
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def parse_fm(fm_lines):
    fm, key = {}, None
    for line in fm_lines:
        if line.startswith("  - ") and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(unquote(line[4:]))
            continue
        m = FM_LINE_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                fm[key] = [unquote(x) for x in val[1:-1].split(",") if x.strip()]
            else:
                fm[key] = unquote(val) if val else []
    return fm


def as_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x]
    if isinstance(val, str) and val:
        return [val]
    return []


def load_degrees():
    path = vault_root() / GRAPH_REL
    if not path.is_file():
        return {}
    try:
        g = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {page: len(nbrs) for page, nbrs in (g.get("adj") or {}).items()}


def load_pages():
    root = vault_root() / CONCEPTS_DIR
    degrees = load_degrees()
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.md")):
        if path.name in INDEX_NAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_lines, body = split_frontmatter(text)
        fm = parse_fm(fm_lines) if fm_lines else {}
        title = fm.get("title") or path.stem
        if isinstance(title, list):
            title = title[0] if title else path.stem
        rel = f"{CONCEPTS_DIR}/{path.name}"
        headings = [HEADING_RE.match(ln).group(1).strip()
                    for ln in body.splitlines() if HEADING_RE.match(ln)]
        topics = [h for h in headings if h not in FIXED_SECTIONS]
        out.append({
            "stem": path.stem,
            "title": str(title),
            "path": rel,
            "aliases": as_list(fm.get("aliases")),
            "degree": degrees.get(rel) or 0,
            "headings": headings,
            "topics": topics,
            "has_topics": bool(topics),
        })
    return out


def concept_file_exists(stem_or_name):
    p = vault_root() / CONCEPTS_DIR / f"{stem_or_name}.md"
    return p.is_file()


def find_page(stem_or_name, pages=None):
    pages = pages if pages is not None else load_pages()
    for p in pages:
        if names_match(p["stem"], stem_or_name) or names_match(p["title"], stem_or_name):
            return p
    return None


def load_candidates_json():
    path = vault_root() / CANDIDATES_REL
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw.get("candidates") or {}


def candidate_aliases(name):
    wanted = fold(name)
    out = []
    for entry in load_candidates_json().values():
        if fold(entry.get("name") or "") == wanted:
            out.extend(as_list(entry.get("aliases")))
    return out


def is_short_shared_alias(alias, titles_match):
    fa = fold(alias)
    if not fa:
        return True
    if len(fa) <= 3:
        return True
    if fa in SHORT_ALIAS_EXACT:
        return True
    if len(fa) == 4 and not titles_match:
        return True
    return False


def _alias_hits(name, aliases):
    return any(names_match(name, a) for a in aliases)


def lookup_one(name):
    pages = load_pages()
    table = ledger_table()
    ledger = load_ledger()
    empty = {"status": "NONE", "via": None, "path": None, "title": None,
             "aliases": [], "dialect_gate": None}

    for p in pages:
        if names_match(name, p["title"]) or names_match(name, p["stem"]):
            return {"status": "HIT", "via": "page", "path": p["path"],
                    "title": p["title"], "aliases": p["aliases"], "dialect_gate": None}

    for dec in (ledger.get("decisions") or {}).values():
        if dec.get("state") != "merged":
            continue
        drop = dec.get("drop") or ""
        if concept_file_exists(drop):
            continue
        if names_match(name, drop) or dialect_key(name, table) == dialect_key(drop, table):
            keep = find_page(dec.get("keep") or "", pages)
            if keep:
                return {"status": "HIT", "via": "merged", "path": keep["path"],
                        "title": keep["title"], "aliases": keep["aliases"],
                        "dialect_gate": None}

    alias_hits = [p for p in pages if _alias_hits(name, p["aliases"])]
    if len(alias_hits) == 1:
        p = alias_hits[0]
        return {"status": "HIT", "via": "alias", "path": p["path"],
                "title": p["title"], "aliases": p["aliases"], "dialect_gate": None}
    if len(alias_hits) >= 2:
        return dict(empty)

    qk = dialect_key(name, table)
    gate = []
    if qk and len(qk) >= 4:
        seen = set()
        for p in pages:
            keys = [dialect_key(p["title"], table)] + [dialect_key(a, table) for a in p["aliases"]]
            if qk in keys and p["path"] not in seen:
                seen.add(p["path"])
                gate.append({"path": p["path"], "stem": p["stem"], "title": p["title"]})
    if gate:
        out = dict(empty)
        out["dialect_gate"] = {"pages": gate}
        return out
    return empty


def is_create_blocked(name, ignore_stems=None):
    ignore = {fold(s) for s in (ignore_stems or []) if s}
    if is_merged_drop(name):
        return True
    hit = lookup_one(name)
    pages = (hit.get("dialect_gate") or {}).get("pages") or []
    if pages and any(not rejected_covers(name, p["stem"]) for p in pages):
        return True
    for p in load_pages():
        if fold(p["stem"]) in ignore:
            continue
        if names_match(name, p["title"]) or names_match(name, p["stem"]):
            return True
        if any(names_match(name, a) for a in p["aliases"]):
            return True
    return False


def create_block_reason(name):
    if is_merged_drop(name):
        return "merged-drop: 統合済み drop 表層は新設できない"
    for p in load_pages():
        if names_match(name, p["title"]) or names_match(name, p["stem"]) \
                or any(names_match(name, a) for a in p["aliases"]):
            return "alias-collision: 既存 concept の題名・stem・alias と一致する"
    hit = lookup_one(name)
    if (hit.get("dialect_gate") or {}).get("pages"):
        n = len(hit["dialect_gate"]["pages"])
        return f"dialect-gate: {n} pages"
    return "dialect-gate: 新設できない"


def _pair_rejected(a, b):
    return (
        rejected_covers(a["title"], b["stem"])
        or rejected_covers(b["title"], a["stem"])
        or rejected_covers(a["stem"], b["stem"])
        or rejected_covers(b["stem"], a["stem"])
    )


def scan_pairs(min_tier="weak", limit=0, include_all=False):
    pages = load_pages()
    table = ledger_table()
    found = {}

    def add(a, b, tier, reason, extra=None):
        if a["path"] == b["path"]:
            return
        if _pair_rejected(a, b):
            return
        k = pair_key(a["stem"], b["stem"])
        cur = found.get(k)
        rec = {"a": a, "b": b, "tier": tier, "reasons": [reason],
               "path_drop": extra.get("path_drop") if extra else None,
               "page_less": bool(extra and extra.get("page_less"))}
        if extra:
            rec.update(extra)
        if cur is None or TIER_RANK[tier] > TIER_RANK[cur["tier"]]:
            found[k] = rec
        elif TIER_RANK[tier] == TIER_RANK[cur["tier"]] and reason not in cur["reasons"]:
            cur["reasons"].append(reason)

    for i, a in enumerate(pages):
        for b in pages[i + 1:]:
            titles_match = names_match(a["title"], b["title"])
            if titles_match:
                add(a, b, "strong", "題名の fold が一致する")
            alias_strong = False
            for src, dst in ((a, b), (b, a)):
                for al in src["aliases"]:
                    hit_title = names_match(al, dst["title"])
                    hit_alias = any(names_match(al, x) for x in dst["aliases"])
                    if not (hit_title or hit_alias):
                        continue
                    if is_short_shared_alias(al, titles_match):
                        continue
                    alias_strong = True
            if alias_strong:
                add(a, b, "strong", "alias が他方の題名または alias に一致する")
            ka, kb = dialect_key(a["title"], table), dialect_key(b["title"], table)
            if ka and ka == kb and len(ka) >= 4:
                add(a, b, "medium", "方言キーが一致する")
            fa, fb = fold(a["title"]), fold(b["title"])
            if fa and fb and fa != fb and min(len(fa), len(fb)) >= 8:
                ratio = difflib.SequenceMatcher(None, fa, fb).ratio()
                if ratio >= 0.92:
                    add(a, b, "weak", f"正規化キーの類似度 {ratio:.2f}")

    for entry in load_candidates_json().values():
        if entry.get("state") != "pending":
            continue
        cname = entry.get("name") or ""
        if not cname or not is_create_blocked(cname):
            continue
        hit = lookup_one(cname)
        for gp in (hit.get("dialect_gate") or {}).get("pages") or []:
            if rejected_covers(cname, gp["stem"]):
                continue
            page = find_page(gp["stem"], pages)
            if not page:
                continue
            fake = {"stem": cname, "title": cname, "path": None, "aliases": as_list(entry.get("aliases")),
                    "degree": 0, "has_topics": False}
            add(page, fake, "medium", "候補台帳の dialect-gate",
                {"page_less": True, "path_drop": None, "suggest_keep": page["title"],
                 "suggest_drop": cname})

    rows = []
    for k, c in found.items():
        if TIER_RANK[c["tier"]] < TIER_RANK[min_tier]:
            continue
        a, b = c["a"], c["b"]
        if c.get("page_less"):
            keep_t, drop_t = c.get("suggest_keep") or a["title"], c.get("suggest_drop") or b["title"]
            keep_p = a["path"] if a["path"] else b["path"]
            drop_p = None
        else:
            def rank(e):
                return (e.get("degree") or 0, len(e.get("aliases") or []), -len(e.get("title") or ""))
            if a["path"] and not b["path"]:
                keep, drop = a, b
            elif b["path"] and not a["path"]:
                keep, drop = b, a
            else:
                keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
            keep_t, drop_t = keep["title"], drop["title"]
            keep_p, drop_p = keep["path"], drop.get("path")
        rows.append({
            "key": k,
            "tier": c["tier"],
            "reasons": c["reasons"],
            "suggest_keep": keep_t,
            "suggest_drop": drop_t,
            "keep_path": keep_p,
            "drop_path": drop_p,
            "page_less": bool(c.get("page_less")),
        })
    rows.sort(key=lambda r: (-TIER_RANK[r["tier"]], r["key"]))
    if limit:
        rows = rows[:limit]
    return rows


def link_re(stem):
    return re.compile(r"\[\[(?:[^\]|#]*/)?%s(?:[|#^]|\])" % re.escape(stem))


def find_linkers(stem):
    rx = link_re(stem)
    hits = []
    root = vault_root()
    for rel_dir in LINK_SCAN_DIRS:
        d = root / rel_dir
        if not d.is_dir():
            continue
        for path in d.rglob("*.md"):
            if path.name in INDEX_NAMES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if rx.search(text):
                hits.append(str(path.relative_to(root)))
    return hits


def excerpt_definition(rel):
    if not rel:
        return ""
    excerpt = _SCRIPTS / "wiki-excerpt.py"
    if not excerpt.is_file():
        return ""
    env = dict(os.environ)
    env["WIKI_VAULT_ROOT"] = str(vault_root())
    try:
        proc = subprocess.run(
            [sys.executable, str(excerpt), rel, "--sections", "定義", "--budget-tokens", "400"],
            capture_output=True, text=True, env=env, check=False,
        )
    except OSError:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def count_topics(page):
    return len(page.get("topics") or []) if page else 0


def default_surfaces(keep_name, drop_name, keep_page, drop_page):
    keep = [keep_name]
    if keep_page:
        keep.append(keep_page["stem"])
        keep.append(keep_page["title"])
        keep.extend(keep_page["aliases"])
    drop = [drop_name]
    if drop_page:
        drop.extend(drop_page["aliases"])
    drop.extend(candidate_aliases(drop_name))
    return _dedupe_surfaces(keep), _dedupe_surfaces(drop)


def _dedupe_surfaces(names):
    out, seen = [], set()
    for n in names:
        if not n:
            continue
        k = fold(n)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def subtract_keep_from_drop(keep, drop):
    return [d for d in drop if not in_surfaces(d, keep)]


def merge_kind(drop_name, drop_page):
    if drop_page is None and not concept_file_exists(drop_name):
        return "alias-only"
    page = drop_page or find_page(drop_name)
    if page is None:
        return "alias-only"
    if page.get("has_topics"):
        return "recompile-keep"
    return "inbox-append"


def build_plan(keep_name, drop_name):
    pages = load_pages()
    keep_page = find_page(keep_name, pages)
    drop_page = find_page(drop_name, pages)
    kind = merge_kind(drop_name, drop_page)
    keep_s, drop_s = default_surfaces(keep_name, drop_name, keep_page, drop_page)
    drop_s = subtract_keep_from_drop(keep_s, drop_s)
    steps = []
    if kind == "alias-only":
        steps.append("残す側の aliases に drop 表層と候補台帳の aliases を足す")
        steps.append("git rm しない。再編纂しない。decide --merged")
    elif kind == "inbox-append":
        steps.append("drop の受信箱と問いを keep へ移す")
        steps.append("リンクを張り替え、drop を git rm する")
    else:
        steps.append("keep の再編纂と drop の git rm とリンク張り替えは同一コミット")
        if keep_page and count_topics(keep_page) >= 7:
            steps.append("keep の主題節は既に 7。新しい ## を足さず、入らない命題は受信箱へ。親子化を検討")
    return {
        "keep": keep_name,
        "drop": drop_name,
        "keep_path": keep_page["path"] if keep_page else None,
        "drop_path": drop_page["path"] if drop_page else None,
        "merge_kind": kind,
        "drop_has_topics": bool(drop_page and drop_page.get("has_topics")),
        "keep_definition": excerpt_definition(keep_page["path"]) if keep_page else "",
        "drop_definition": excerpt_definition(drop_page["path"]) if drop_page else "",
        "surfaces_keep": keep_s,
        "surfaces_drop": drop_s,
        "linkers": find_linkers(drop_page["stem"] if drop_page else drop_name),
        "steps": steps,
    }


def cmd_scan(args):
    rows = scan_pairs(min_tier=args.min_tier, limit=args.limit or 0)
    print(json.dumps({"count": len(rows), "pairs": rows}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_report(args):
    rows = scan_pairs(min_tier="weak", limit=args.limit or 30)
    ledger = load_ledger()
    unfinished = []
    for k, dec in (ledger.get("decisions") or {}).items():
        if dec.get("state") != "merged":
            continue
        drop = dec.get("drop") or ""
        if concept_file_exists(drop):
            unfinished.append((k, dec))
    out = ["## Concept Aliases", ""]
    out.append(f"- 未完了マージ(drop ファイル残): {len(unfinished)} 件")
    page_less = [r for r in rows if r.get("page_less")]
    out.append(f"- alias-only 候補(頁無し対): {len(page_less)} 件")
    out.append("")
    if unfinished:
        out.append("### 未完了マージ")
        out.append("")
        for k, dec in unfinished[:30]:
            out.append(f"- **merged** [[{dec.get('keep')}]] ← [[{dec.get('drop')}]] (`{k}`)。drop ファイルが残っている")
        out.append("")
    if page_less:
        out.append("### alias-only 候補")
        out.append("")
        for r in page_less[:30]:
            out.append(f"- **{r['tier']}** 頁 [[{r['suggest_keep']}]] と頁無し `{r['suggest_drop']}`。{ '。'.join(r['reasons']) }")
        out.append("")
    existing = [r for r in rows if not r.get("page_less")]
    if existing:
        out.append("### 既存頁の対")
        out.append("")
        for r in existing[:30]:
            out.append(f"- **{r['tier']}** [[{r['suggest_keep']}]] と [[{r['suggest_drop']}]]。{'。'.join(r['reasons'])}")
        out.append("")
    sys.stdout.write("\n".join(out).rstrip() + "\n")
    return EXIT_OK


def cmd_plan(args):
    print(json.dumps(build_plan(args.keep, args.drop), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_decide(args):
    if bool(args.merged) == bool(args.rejected):
        log("ERR: --merged か --rejected のどちらか一方")
        return EXIT_USAGE
    state = "merged" if args.merged else "rejected"
    keep_name, drop_name = args.keep, args.drop
    pages = load_pages()
    keep_page = find_page(keep_name, pages)
    drop_page = find_page(drop_name, pages)
    keep_s, drop_s = default_surfaces(keep_name, drop_name, keep_page, drop_page)
    extra_keep = args.surface_keep or []
    extra_drop = args.surface_drop or []
    for s in extra_keep:
        if in_surfaces(s, extra_drop):
            log("ERR: --surface-keep と --surface-drop が交差する")
            return EXIT_USAGE
    # 明示した drop 表層は、残す側ページの既定 aliases より優先する。
    # 既定同士の交差は従来どおり keep だけに残す。
    keep_s = [k for k in keep_s if not in_surfaces(k, extra_drop)]
    keep_s = _dedupe_surfaces(keep_s + extra_keep)
    drop_s = _dedupe_surfaces(drop_s + extra_drop)
    drop_s = subtract_keep_from_drop(keep_s, drop_s)
    key = pair_key(keep_page["stem"] if keep_page else keep_name,
                   drop_page["stem"] if drop_page else drop_name)
    rec = {
        "state": state,
        "keep": keep_page["stem"] if keep_page else keep_name,
        "drop": drop_page["stem"] if drop_page else drop_name,
        "surfaces_keep": keep_s,
        "surfaces_drop": drop_s,
        "reason": (args.reason or "").strip(),
        "day": date.today().isoformat(),
        "merge_kind": merge_kind(drop_name, drop_page),
    }
    data = load_ledger()
    data.setdefault("decisions", {})[key] = rec
    save_ledger(data)
    print(json.dumps({"key": key, **rec}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_lookup(args):
    out = []
    for name in args.names:
        hit = lookup_one(name)
        out.append({"query": name, **hit})
    print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_ledger(_args):
    print(json.dumps(load_ledger(), ensure_ascii=False, indent=2))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="概念の方言帰着。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--min-tier", choices=("weak", "medium", "strong"), default="weak")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("plan")
    p.add_argument("--keep", required=True)
    p.add_argument("--drop", required=True)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("decide")
    p.add_argument("--keep", required=True)
    p.add_argument("--drop", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--merged", action="store_true")
    g.add_argument("--rejected", action="store_true")
    p.add_argument("--reason", default="")
    p.add_argument("--surface-keep", action="append", default=[])
    p.add_argument("--surface-drop", action="append", default=[])
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("lookup")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("ledger")
    p.set_defaults(func=cmd_ledger)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
