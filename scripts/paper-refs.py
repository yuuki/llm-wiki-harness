#!/usr/bin/env python3
"""paper-refs.py — 取り込み済み論文 raw の文献表から識別子候補を台帳に積む。

wiki-ingest-paper が source を書いたあと extract / mark-ingested を呼び、lint が
report を貼る。候補の自動 ingest はしない。書誌 API は使わない。arxiv と DOI は
結び付けない。wiki_lock の待ちロックは使わない。paper-ids 索引は読むだけで再生成しない。
本文の [N] と節見出しから役割件数を書く。対比は直前窓の定型句である。ready は役割で変えない。

使い方:
  python3 scripts/paper-refs.py extract --source "@YYYY__VENUE__Title"
  python3 scripts/paper-refs.py backfill [--limit N] [--dry-run]
  python3 scripts/paper-refs.py list [--ready] [--state pending|ingested|rejected] [--limit N]
  python3 scripts/paper-refs.py show KEY
  python3 scripts/paper-refs.py reject KEY --reason TEXT
  python3 scripts/paper-refs.py reopen KEY
  python3 scripts/paper-refs.py mark-ingested --page wiki/sources/@....md
  python3 scripts/paper-refs.py report [--limit 30] [--write]

終了コード:
  0 — 成功 / 1 — 壊れた台帳 JSON / 2 — 使い方 / 3 — ページ・キー・テキストが無い / 75 — lock
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load_hyphen_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


paper_ids = _load_hyphen_module("paper_ids", "paper-ids.py")

LEDGER_REL = ".vault-meta/paper-ref-candidates.json"
PAGE_REL = "wiki/meta/paper-ref-candidates.md"
SOURCES_DIR = "wiki/sources"
LOCK_SCRIPT = _SCRIPTS / "wiki-lock.sh"

EXIT_OK = 0
EXIT_CORRUPT = 1
EXIT_USAGE = 2
EXIT_MISSING = 3
EXIT_LOCK = 75

STATES = ("pending", "ingested", "rejected")
DECORATIONS = "♦●■※"
CJK_CHAR = r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]"

READY_MIN_DEMAND = 2
DEFAULT_REPORT_LIMIT = 30


def _spaced(word):
    return r"\s*".join(re.escape(ch) for ch in word)


_SECTION = r"(?:(?:\d+|[A-Z]|[IVXLCDM]+)[.)]\s+)?"
_HEADING_CORE = (
    rf"(?:{_spaced('REFERENCES')}|{_spaced('BIBLIOGRAPHY')}|"
    rf"{_spaced('LITERATURE')}\s+{_spaced('CITED')}|参考文献)"
)
HEADING_RE = re.compile(rf"^{_SECTION}{_HEADING_CORE}(?=\s|$|\[)", re.I)
BIB_ITEM_RE = re.compile(r"^\[\d+\]")
TOC_RE = re.compile(
    r"^(?:\.{2,}|…|[·⋯．\u2026\u00b7\u30fb]{2,})[.\s·…⋯．\u2026\u00b7\u30fb]*\d*\s*$"
)
END_RE = re.compile(
    r"^(?:Appendix|Appendices|Supplementary|Acknowledg(?:e)?ments?|謝辞)\b",
    re.I,
)
STAMP_RE = re.compile(
    r"^arXiv:(\d{4}\.\d{4,5})(v\d+)?(?:\s+\[\S+\])?(?:\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4})?\s*$",
    re.I,
)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
CJK_SPACE_RE = re.compile(rf"({CJK_CHAR})\s+(?={CJK_CHAR})")
PAPERS_TXT_RE = re.compile(r"(?:^|/)\.raw/papers/[^/]+\.txt$")
ITEM_SPLIT_RE = re.compile(r"(?=\[\d+\])")
BIB_MARKER_RE = re.compile(r"\[\d+\]")
HTTP_URL_RE = re.compile(r"https?://\S+")
ARXIV_PREFIX_RE = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(v\d+)?\b", re.I)
PREPRINT_RE = re.compile(r"\barXiv preprint\b", re.I)
ITEM_NUM_RE = re.compile(r"^\[\d+\]\s*")
TITLE_KEEP_RE = re.compile(r"[^0-9A-Za-z\u3040-\u9fff]")
EVAL_FORBIDDEN = ("読むべき", "重要", "推奨", "recommend", "should read")
ROLE_KEYS = (
    "related_work",
    "contrast",
    "baseline",
    "method",
    "experiment",
    "passing",
)
ROLE_LABELS = {
    "related_work": "関連研究",
    "contrast": "対比",
    "baseline": "ベースライン",
    "method": "手法",
    "experiment": "実験",
    "passing": "その他",
}
CONTRAST_WINDOW = 80
_CONTRAST_EN = (
    r"\b(?:"
    r"unlike|"
    r"in\s+contrast|"
    r"whereas|"
    r"as\s+opposed\s+to|"
    r"differ(?:s|ed|ing)?\s+from|"
    r"different\s+from"
    r")\b"
)
_CONTRAST_JA = (
    rf"(?:{_spaced('とは異なり')}|{_spaced('と異なり')}|"
    rf"{_spaced('対照的')}|{_spaced('に対して')})"
)
CONTRAST_RE = re.compile(rf"{_CONTRAST_EN}|{_CONTRAST_JA}", re.I)
RANGE_MAX_SPAN = 9
CITE_BRACKET_RE = re.compile(
    r"(?<!\[)\[(\d+(?:\s*[-–—]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–—]\s*\d+)?)*)\]"
)
BIB_NUM_RE = re.compile(r"^\[(\d+)\]")
ROLE_NUM_RE = (
    r"(?P<num>(?-i:(?:\d+(?:\.\d+)*(?:[.)])?|[IVXLCDM]+[.)]|[A-Z][.)])\s+))?"
)


def vault_root():
    return Path(os.environ.get("WIKI_VAULT_ROOT") or _SCRIPTS.parent).resolve()


def ledger_path():
    return vault_root() / LEDGER_REL


def today():
    return date.today().isoformat()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


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


def dump_ledger(data):
    data["updated_at"] = now_iso()
    atomic_write(ledger_path(), json.dumps(data, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- lock (wiki-lock.sh を 1 回。待ちロックは使わない)


def acquire_named(rel):
    """1 回だけ acquire。75 は待たずに返す。"""
    env = dict(os.environ)
    env["WIKI_VAULT_ROOT"] = str(vault_root())
    proc = subprocess.run(
        ["bash", str(LOCK_SCRIPT), "acquire", rel],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault_root()),
    )
    return proc.returncode


def release_named(rel):
    env = dict(os.environ)
    env["WIKI_VAULT_ROOT"] = str(vault_root())
    subprocess.run(
        ["bash", str(LOCK_SCRIPT), "release", rel],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(vault_root()),
    )


def acquire_ledger():
    return acquire_named(LEDGER_REL)


def release_ledger():
    release_named(LEDGER_REL)


def acquire_page():
    return acquire_named(PAGE_REL)


def release_page():
    release_named(PAGE_REL)


# --------------------------------------------------------------------------- 行頭正規化と見出し


def collapse_cjk_spaces(s):
    prev = None
    while prev != s:
        prev = s
        s = CJK_SPACE_RE.sub(r"\1", s)
    return s


def normalize_line(line):
    s = (line or "").lstrip()
    s = s.lstrip(DECORATIONS).rstrip(DECORATIONS)
    s = s.lstrip().rstrip()
    return collapse_cjk_spaces(s)


def _blank_or_deco(s):
    if not s:
        return True
    return all(ch in DECORATIONS or ch.isspace() for ch in s)


def heading_remainder(normed):
    m = HEADING_RE.match(normed)
    if not m:
        return None
    rest = normed[m.end():].lstrip()
    if _blank_or_deco(rest):
        return ""
    return rest


def classify_heading_line(line):
    """heading / toc / other / None(見出し語なし)。"""
    rest = heading_remainder(normalize_line(line))
    if rest is None:
        return None
    if rest == "":
        return "heading"
    if BIB_ITEM_RE.match(rest):
        return "heading"
    if TOC_RE.match(rest):
        return "toc"
    return "other"


def is_end_heading(line):
    return bool(END_RE.match(normalize_line(line)))


def find_ref_region(text):
    lines = (text or "").splitlines()
    last = None
    for i, line in enumerate(lines):
        if classify_heading_line(line) == "heading":
            last = i
    if last is None:
        return ""
    end = len(lines)
    for j in range(last + 1, len(lines)):
        if is_end_heading(lines[j]):
            end = j
            break
    return "\n".join(lines[last:end])


# --------------------------------------------------------------------------- 識別子採取


def stamp_excluded_arxiv(text):
    counts = Counter()
    for line in (text or "").splitlines():
        m = STAMP_RE.match(normalize_line(line))
        if m:
            counts[m.group(1)] += 1
    return {aid for aid, n in counts.items() if n >= 3}


def unwrap_source_item(raw):
    s = (raw or "").strip().strip("\"'")
    m = WIKILINK_RE.search(s)
    if m:
        s = m.group(1).strip()
    return s.replace("\\", "/")


def find_evidence_txt(sources):
    """sources: が指す .raw/papers/<slug>.txt。books / theses / articles は証拠にしない。"""
    pointed_missing = False
    for raw in sources or []:
        item = unwrap_source_item(raw)
        if item.startswith("./"):
            item = item[2:]
        idx = item.find(".raw/papers/")
        if idx < 0:
            continue
        rel = item[idx:]
        if not PAPERS_TXT_RE.search(rel):
            continue
        path = vault_root() / rel
        if path.is_file():
            return path
        pointed_missing = True
    if pointed_missing:
        return None
    return None


def self_idents(rel, page_text, sources):
    out = set()
    rec = paper_ids.derive_ids(rel, page_text)
    if rec:
        if rec.get("arxiv"):
            out.add(("arxiv", rec["arxiv"]))
        if rec.get("doi"):
            out.add(("doi", rec["doi"]))
    for raw in sources or []:
        m = paper_ids.ARXIV_SLUG_RE.search(unwrap_source_item(raw))
        if m:
            out.add(("arxiv", m.group(1)))
    return out


def harvest_idents(region):
    """領域内の (kind, id) を出現順・一意で返す。成果物 DOI と旧形式 arXiv は入らない。"""
    found = []
    seen = set()

    def add(kind, ident):
        if not ident:
            return
        key = (kind, ident)
        if key in seen:
            return
        seen.add(key)
        found.append(key)

    for m in paper_ids.ARXIV_URL_RE.finditer(region or ""):
        add("arxiv", paper_ids.norm_arxiv(m.group(1)))
    for m in paper_ids.ARXIV_ID_RE.finditer(region or ""):
        add("arxiv", paper_ids.norm_arxiv(m.group(1)))
    for m in paper_ids.DOI_URL_RE.finditer(region or ""):
        doi = paper_ids.norm_doi(m.group(0))
        if doi and not paper_ids.ARTIFACT_DOI_RE.match(doi):
            add("doi", doi)
    for m in paper_ids.DOI_RE.finditer(region or ""):
        doi = paper_ids.norm_doi(m.group(1))
        if doi and not paper_ids.ARTIFACT_DOI_RE.match(doi):
            add("doi", doi)
    return found


def iter_bib_items(region):
    text = region or ""
    if BIB_MARKER_RE.search(text):
        return [p for p in ITEM_SPLIT_RE.split(text) if p.strip()]
    return [ln for ln in text.splitlines() if ln.strip()]


def item_snippet(item, ident=None):
    """識別子と番号を除いた文献残片。8 文字未満は無し。題名を推定して削らない。"""
    s = item or ""
    s = ITEM_NUM_RE.sub("", s)
    s = paper_ids.ARXIV_URL_RE.sub(" ", s)
    s = paper_ids.DOI_URL_RE.sub(" ", s)
    s = HTTP_URL_RE.sub(" ", s)
    s = re.sub(r"https?://", " ", s)
    s = ARXIV_PREFIX_RE.sub(" ", s)
    s = paper_ids.DOI_RE.sub(" ", s)
    s = paper_ids.ARXIV_ID_RE.sub(" ", s)
    s = PREPRINT_RE.sub(" ", s)
    if ident:
        s = s.replace(ident, " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .,;:[]()。、")
    letters = TITLE_KEEP_RE.sub("", s)
    if len(letters) < 8:
        return None
    if len(s) > 200:
        s = s[:200].rstrip()
    return s or None


def harvest_with_titles(region):
    """(kind, id, title_or_None)。項目に識別子が 2 つ以上ならその項目では title を付けない。
    同一識別子のあとから単独項目の残片が来たら、空の title だけ埋める。"""
    titles = {}
    order = []
    for item in iter_bib_items(region):
        idents = harvest_idents(item)
        one = len(idents) == 1
        for kind, ident in idents:
            key = (kind, ident)
            snippet = item_snippet(item, ident) if one else None
            if key not in titles:
                titles[key] = snippet
                order.append(key)
            elif titles[key] is None and snippet:
                titles[key] = snippet
    return [(kind, ident, titles[(kind, ident)]) for kind, ident in order]


def collect_idents(evidence, rel, page_text, sources):
    region = find_ref_region(evidence)
    harvested = harvest_with_titles(region)
    banned = self_idents(rel, page_text, sources)
    stamps = stamp_excluded_arxiv(evidence)
    out = []
    for kind, ident, title in harvested:
        if (kind, ident) in banned:
            continue
        if kind == "arxiv" and ident in stamps:
            continue
        out.append((kind, ident, title))
    return out


# --------------------------------------------------------------------------- 本文役割


def _cjk_spaced(word):
    return r"\s*".join(re.escape(ch) for ch in word)


def heading_raw(line):
    """行頭正規化の段階 1–3。CJK 字間は潰さない(二段組余りの空白を残す)。"""
    s = (line or "").lstrip()
    s = s.lstrip(DECORATIONS).rstrip(DECORATIONS)
    return s.lstrip().rstrip().rstrip(".:;。")


def _role_res():
    vocabs = [
        (
            "related_work",
            r"(?:(?:Background|Motivation)\s+and\s+)?"
            + _spaced("RELATED")
            + r"\s+"
            + _spaced("WORK")
            + r"S?"
            + r"|"
            + _spaced("PRIOR")
            + r"\s+"
            + _spaced("WORK")
            + r"|"
            + _spaced("PREVIOUS")
            + r"\s+"
            + _spaced("WORK")
            + r"|"
            + _spaced("LITERATURE")
            + r"\s+"
            + _spaced("REVIEW")
            + r"|"
            + _cjk_spaced("関連研究"),
        ),
        (
            "baseline",
            _spaced("BASELINE") + r"S?|" + _spaced("COMPARISON") + r"S?|" + _cjk_spaced("比較"),
        ),
        (
            "method",
            r"(?:"
            + _spaced("PROPOSED")
            + r"\s+)?"
            + _spaced("METHODOLOGY")
            + r"|(?:"
            + _spaced("PROPOSED")
            + r"\s+)?"
            + _spaced("METHOD")
            + r"S?|"
            + _spaced("APPROACH")
            + r"|"
            + _cjk_spaced("手法"),
        ),
        (
            "experiment",
            _spaced("EXPERIMENT")
            + r"S?|"
            + _spaced("EVALUATION")
            + r"|"
            + _spaced("RESULTS")
            + r"|"
            + _cjk_spaced("実験"),
        ),
    ]
    compiled = []
    for role, vocab in vocabs:
        compiled.append(
            (role, re.compile(rf"^{ROLE_NUM_RE}(?P<vocab>{vocab})(?P<rest>.*)$", re.I))
        )
    return compiled


ROLE_RES = _role_res()


def last_ref_heading_index(text):
    last = None
    for i, line in enumerate((text or "").splitlines()):
        if classify_heading_line(line) == "heading":
            last = i
    return last


def first_end_heading_index(lines):
    for i, line in enumerate(lines):
        if is_end_heading(line):
            return i
    return None


def role_ref_region(text):
    """役割の番号対応。付録より前に文献表があればそちら。採取(最後の見出し)は変えない。"""
    lines = (text or "").splitlines()
    heads = [i for i, line in enumerate(lines) if classify_heading_line(line) == "heading"]
    if not heads:
        return ""
    end = first_end_heading_index(lines)
    if end is not None and heads[-1] > end:
        earlier = [i for i in heads if i < end]
        if earlier:
            return "\n".join(lines[earlier[-1]:end])
    return find_ref_region(text)


def skip_early_ref_block(lines, i):
    """参考文献見出しから終端(または列の末尾)の次へ。"""
    if classify_heading_line(lines[i]) != "heading":
        return i
    j = i + 1
    while j < len(lines) and not is_end_heading(lines[j]):
        j += 1
    if j < len(lines) and is_end_heading(lines[j]):
        j += 1
    return j


def bib_number_map(region):
    """項目先頭の [N] をその項目の識別子へ。同じ N は先勝ち。"""
    mapping = {}
    for item in iter_bib_items(region):
        m = BIB_NUM_RE.match((item or "").lstrip())
        if not m:
            continue
        n = int(m.group(1))
        if n < 1 or n in mapping:
            continue
        idents = harvest_idents(item)
        if idents:
            mapping[n] = idents
    return mapping


def parse_cite_nums(inner):
    nums = []
    for part in re.split(r"\s*,\s*", (inner or "").strip()):
        m = re.fullmatch(r"(\d+)(?:\s*[-–—]\s*(\d+))?", part)
        if not m:
            continue
        start = int(m.group(1))
        if m.group(2) is None:
            if start >= 1:
                nums.append(start)
            continue
        end = int(m.group(2))
        if start < end and (end - start) <= RANGE_MAX_SPAN:
            nums.extend(n for n in range(start, end + 1) if n >= 1)
    return [n for n in nums if n >= 1]


def role_num_sig(num):
    s = (num or "").strip()
    return s.rstrip(".)")


def match_role_heading(line):
    """(role, numbered, num) or None。余りは CJK 潰し前の生文字列。"""
    raw = heading_raw(line)
    if not raw:
        return None
    for role, rx in ROLE_RES:
        m = rx.match(raw)
        if not m:
            continue
        rest = m.group("rest") or ""
        if TOC_RE.match(rest.lstrip()):
            return None
        num = role_num_sig(m.group("num"))
        numbered = bool(num)
        if _blank_or_deco(rest) or (
            len(rest) >= 2 and rest[0].isspace() and rest[1].isspace()
        ):
            return role, numbered, num
        return None
    return None


def window_has_contrast(before):
    """定型句は切る前の列で照合する。開始が末尾 80 文字に入ったものだけ。"""
    text = before or ""
    if not text:
        return False
    floor = max(0, len(text) - CONTRAST_WINDOW)
    for m in CONTRAST_RE.finditer(text):
        if m.start() >= floor:
            return True
    return False


def harvest_roles(evidence):
    """(kind, ident) -> Counter(role -> n)。見出しが無ければ空。"""
    lines = (evidence or "").splitlines()
    last = last_ref_heading_index(evidence)
    if last is None:
        return {}
    region = role_ref_region(evidence)
    body = lines[:last]
    mapping = bib_number_map(region)
    if not mapping:
        return {}

    classified = []
    sig_counts = Counter()
    i = 0
    while i < len(body):
        nxt = skip_early_ref_block(body, i)
        if nxt != i:
            i = nxt
            continue
        hit = match_role_heading(body[i])
        if hit:
            role, numbered, num = hit
            sig = (role, numbered, num if numbered else "")
            classified.append((i, role, numbered, sig))
            sig_counts[sig] += 1
        i += 1
    heading_at = {i: (role, numbered, sig) for i, role, numbered, sig in classified}

    current = "passing"
    seen_numbered_run = set()
    out = {}
    prefix = ""
    i = 0
    while i < len(body):
        nxt = skip_early_ref_block(body, i)
        if nxt != i:
            prefix = ""
            i = nxt
            continue
        line = body[i]
        if i in heading_at:
            role, numbered, sig = heading_at[i]
            n = sig_counts[sig]
            if not numbered and n >= 3:
                pass
            elif numbered and n >= 3:
                if sig not in seen_numbered_run:
                    current = role
                    seen_numbered_run.add(sig)
            else:
                current = role
        for m in CITE_BRACKET_RE.finditer(line):
            hit_contrast = window_has_contrast(prefix + line[: m.start()])
            for n in parse_cite_nums(m.group(1)):
                for kind, ident in mapping.get(n, ()):
                    counts = out.setdefault((kind, ident), Counter())
                    counts[current] += 1
                    if hit_contrast:
                        counts["contrast"] += 1
        prefix = prefix + line + " "
        i += 1
    return out


def role_counts_for(role_map, kind, ident):
    raw = (role_map or {}).get((kind, ident)) or {}
    return {k: int(v) for k, v in raw.items() if k in ROLE_KEYS and int(v) > 0}


def apply_roles(entry, stem, counts):
    roles = entry.setdefault("roles", {})
    if not isinstance(roles, dict):
        entry["roles"] = {}
        roles = entry["roles"]
    cleaned = {k: int(v) for k, v in (counts or {}).items() if k in ROLE_KEYS and int(v) > 0}
    if cleaned:
        roles[stem] = cleaned
    else:
        roles.pop(stem, None)


def role_totals(entry):
    totals = Counter()
    raw = (entry or {}).get("roles")
    if not isinstance(raw, dict):
        return totals
    for rec in raw.values():
        if not isinstance(rec, dict):
            continue
        for key, n in rec.items():
            if key in ROLE_KEYS and isinstance(n, int) and n > 0:
                totals[key] += n
    return totals


def role_clause(entry):
    totals = role_totals(entry)
    parts = [f"{ROLE_LABELS[k]} {totals[k]}" for k in ROLE_KEYS if totals[k]]
    return "、".join(parts)


# --------------------------------------------------------------------------- 台帳 I/O


def empty_ledger():
    return {"version": 1, "updated_at": None, "candidates": {}}


def _parse_ledger_text(text):
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), dict):
        raise json.JSONDecodeError("ledger shape", text, 0)
    return data


def read_ledger_unlocked():
    """読取専用。欠落は空。JSONDecode は 1 回再読。それでも壊れなら (None, True)。"""
    path = ledger_path()
    if not path.is_file():
        return empty_ledger(), False
    last_exc = None
    for _ in range(2):
        try:
            return _parse_ledger_text(path.read_text(encoding="utf-8")), False
        except (OSError, json.JSONDecodeError) as exc:
            last_exc = exc
            continue
    log(f"corrupt ledger: {last_exc}")
    return None, True


def load_ledger_for_write():
    """lock 内。欠落は空(まだ書かない)。壊れは None。修復しない。"""
    path = ledger_path()
    if not path.is_file():
        return empty_ledger()
    try:
        return _parse_ledger_text(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"corrupt ledger: {exc}")
        return None


def read_paper_ids_index():
    """索引 JSON を読むだけ。無いか壊れていれば None。索引の再生成はしない。"""
    path = vault_root() / paper_ids.INDEX_REL
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def is_hit(index, kind, ident):
    if index is None:
        return False
    bucket = index.get("arxiv" if kind == "arxiv" else "doi")
    if not isinstance(bucket, dict):
        return False
    return ident in bucket


def hit_page(index, kind, ident):
    if not is_hit(index, kind, ident):
        return None
    bucket = index.get("arxiv" if kind == "arxiv" else "doi")
    pages = bucket.get(ident) or []
    return pages[0] if pages else None


def ledger_key(kind, ident):
    return f"{kind}:{ident}"


def parse_key(key):
    if not key or ":" not in key:
        return None, None
    kind, ident = key.split(":", 1)
    if kind == "arxiv":
        ident = paper_ids.norm_arxiv(ident)
        return ("arxiv", ident) if ident else (None, None)
    if kind == "doi":
        ident = paper_ids.norm_doi(ident)
        return ("doi", ident) if ident else (None, None)
    return None, None


def demand_of(entry):
    citing = entry.get("citing") or []
    return len({paper_ids.doc_key(s) for s in citing})


def ident_of(key, entry):
    kind, ident = parse_key(key)
    if kind:
        return kind, ident
    if entry.get("arxiv"):
        return "arxiv", entry["arxiv"]
    if entry.get("doi"):
        return "doi", entry["doi"]
    return None, None


def is_ready(entry, index, key=None):
    if index is None:
        return False
    if entry.get("state") != "pending":
        return False
    if demand_of(entry) < READY_MIN_DEMAND:
        return False
    kind, ident = ident_of(key, entry) if key else ident_of(None, entry)
    if not kind:
        return False
    if is_hit(index, kind, ident):
        return False
    return True


def ready_rows(data, index, limit=None):
    if index is None:
        return []
    rows = []
    for key, entry in (data.get("candidates") or {}).items():
        if is_ready(entry, index, key):
            rows.append((key, entry, demand_of(entry)))
    rows.sort(key=lambda r: (-r[2], r[0]))
    if limit is not None:
        return rows[:limit]
    return rows


def count_ready(data, index):
    return len(ready_rows(data, index))


def new_entry(kind, ident, source_stem, day):
    return {
        "arxiv": ident if kind == "arxiv" else None,
        "doi": ident if kind == "doi" else None,
        "title": None,
        "roles": {},
        "citing": [source_stem],
        "state": "pending",
        "first_seen": day,
        "last_seen": day,
        "mentions": [{"date": day, "source": source_stem, "by": "extract"}],
        "ingested_to": None,
        "resolved_at": None,
        "reason": None,
    }


def apply_mention(data, kind, ident, source_stem, day, index, title=None):
    """(source, key) で mentions 1 件。戻り値は mentions を増やしたか。"""
    key = ledger_key(kind, ident)
    cands = data.setdefault("candidates", {})
    added = False
    if key not in cands:
        cands[key] = new_entry(kind, ident, source_stem, day)
        added = True
    else:
        entry = cands[key]
        if any(m.get("source") == source_stem for m in entry.get("mentions") or []):
            entry["last_seen"] = day
        else:
            entry.setdefault("mentions", []).append(
                {"date": day, "source": source_stem, "by": "extract"}
            )
            if source_stem not in entry.get("citing", []):
                entry.setdefault("citing", []).append(source_stem)
            entry["last_seen"] = day
            added = True
    entry = cands[key]
    if title and not (entry.get("title") or "").strip():
        entry["title"] = title
    if entry.get("state") != "rejected" and index is not None and is_hit(index, kind, ident):
        entry["state"] = "ingested"
        page = hit_page(index, kind, ident)
        if page:
            entry["ingested_to"] = page
        entry["resolved_at"] = day
    return added


# --------------------------------------------------------------------------- ページ解決


def norm_page_arg(raw):
    s = (raw or "").strip()
    m = WIKILINK_RE.search(s)
    if m:
        s = m.group(1).strip()
    if s.endswith(".md"):
        s = s[:-3]
    if "/" in s:
        s = s.split("/")[-1]
    return s


def source_rel(stem):
    name = stem if stem.endswith(".md") else f"{stem}.md"
    return f"{SOURCES_DIR}/{name}"


def read_source_page(raw):
    stem = norm_page_arg(raw)
    if not stem:
        return None, None, None
    rel = source_rel(stem)
    path = vault_root() / rel
    if not path.is_file():
        return rel, None, None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return rel, path, None
    return rel, path, text


def page_sources(page_text):
    fm_lines, _ = paper_ids.split_frontmatter(page_text or "")
    if fm_lines is None:
        return []
    fm = paper_ids.parse_fm(fm_lines)
    srcs = fm.get("sources")
    return srcs if isinstance(srcs, list) else []


def classify_extract_input(raw):
    """ok | missing | not_paper | no_txt。ok なら (rel, page_text, txt_path, stem)。"""
    rel, path, text = read_source_page(raw)
    if path is None or text is None:
        return "missing", None
    rec = paper_ids.derive_ids(rel, text)
    if rec is None or rec.get("source_type") != "paper":
        return "not_paper", None
    srcs = page_sources(text)
    txt_path = find_evidence_txt(srcs)
    if txt_path is None:
        return "no_txt", None
    return "ok", {
        "rel": rel,
        "stem": Path(rel).stem,
        "page_text": text,
        "sources": srcs,
        "txt_path": txt_path,
        "rec": rec,
    }


def iter_source_pages():
    root = vault_root() / SOURCES_DIR
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("_"):
            continue
        yield f"{SOURCES_DIR}/{path.name}", path


# --------------------------------------------------------------------------- コマンド


def run_extract(source, dry_run=False, day=None):
    """採取して台帳に足す。(終了コード, payload or None)。stdout は書かない。"""
    kind, payload = classify_extract_input(source)
    if kind != "ok":
        return EXIT_MISSING, None
    try:
        evidence = payload["txt_path"].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return EXIT_MISSING, None
    idents = collect_idents(evidence, payload["rel"], payload["page_text"], payload["sources"])
    role_map = harvest_roles(evidence)
    out = {
        "source": payload["stem"],
        "harvested": [ledger_key(k, i) for k, i, _t in idents],
        "mentions_added": 0,
        "ready": 0,
    }
    if dry_run:
        out["dry_run"] = True
        return EXIT_OK, out

    rc = acquire_ledger()
    if rc == EXIT_LOCK:
        return EXIT_LOCK, None
    if rc != 0:
        log(f"lock acquire failed: {rc}")
        return rc, None
    try:
        data = load_ledger_for_write()
        if data is None:
            return EXIT_CORRUPT, None
        index = read_paper_ids_index()
        day = day or today()
        added = 0
        changed = False
        stem = payload["stem"]
        for k, ident, title in idents:
            if apply_mention(data, k, ident, stem, day, index, title=title):
                added += 1
                changed = True
            else:
                changed = True
            entry = data["candidates"][ledger_key(k, ident)]
            apply_roles(entry, stem, role_counts_for(role_map, k, ident))
        out["mentions_added"] = added
        out["ready"] = count_ready(data, index)
        if changed or (idents and data.get("candidates")):
            dump_ledger(data)
        return EXIT_OK, out
    finally:
        release_ledger()


def cmd_extract(args):
    rc, payload = run_extract(args.source, dry_run=args.dry_run, day=args.date)
    if rc == EXIT_OK and payload is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return rc


def cmd_backfill(args):
    stats = {
        "extracted": 0,
        "skipped_not_paper": 0,
        "skipped_no_txt": 0,
        "skipped_lock": 0,
        "exit3": 0,
        "mentions_added": 0,
    }
    dry_items = []
    tried = 0
    limit = args.limit
    for rel, path in iter_source_pages():
        if limit is not None and tried >= limit:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            stats["exit3"] += 1
            tried += 1
            continue
        rec = paper_ids.derive_ids(rel, text)
        if rec is None or rec.get("source_type") != "paper":
            stats["skipped_not_paper"] += 1
            continue
        srcs = page_sources(text)
        txt_path = find_evidence_txt(srcs)
        if txt_path is None:
            stats["skipped_no_txt"] += 1
            continue
        tried += 1
        rc, payload = run_extract(Path(rel).stem, dry_run=bool(args.dry_run))
        if rc == EXIT_LOCK:
            stats["skipped_lock"] += 1
            continue
        if rc == EXIT_CORRUPT:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return EXIT_CORRUPT
        if rc == EXIT_MISSING:
            stats["exit3"] += 1
            continue
        if rc != EXIT_OK:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return rc
        stats["extracted"] += 1
        if payload:
            stats["mentions_added"] += int(payload.get("mentions_added") or 0)
            if args.dry_run:
                dry_items.append({
                    "source": payload.get("source"),
                    "harvested": payload.get("harvested") or [],
                })
    if args.dry_run:
        stats["dry_run"] = True
        stats["sources"] = dry_items
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_list(args):
    data, corrupt = read_ledger_unlocked()
    if corrupt:
        return EXIT_CORRUPT
    index = read_paper_ids_index()
    rows = []
    for key, entry in (data.get("candidates") or {}).items():
        if args.state and entry.get("state") != args.state:
            continue
        ready = is_ready(entry, index, key)
        if args.ready and not ready:
            continue
        rows.append({
            "key": key,
            "demand": demand_of(entry),
            "state": entry.get("state"),
            "citing": list(entry.get("citing") or []),
            "ready": ready,
        })
    rows.sort(key=lambda r: (-r["demand"], r["key"]))
    if args.limit is not None:
        rows = rows[: args.limit]
    print(json.dumps({"count": len(rows), "candidates": rows}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_show(args):
    data, corrupt = read_ledger_unlocked()
    if corrupt:
        return EXIT_CORRUPT
    kind, ident = parse_key(args.key)
    key = ledger_key(kind, ident) if kind else args.key
    entry = (data.get("candidates") or {}).get(key)
    if entry is None:
        return EXIT_MISSING
    index = read_paper_ids_index()
    out = dict(entry)
    out["key"] = key
    out["demand"] = demand_of(entry)
    out["ready"] = is_ready(entry, index, key)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return EXIT_OK


def _with_write_lock(fn):
    rc = acquire_ledger()
    if rc == EXIT_LOCK:
        return EXIT_LOCK
    if rc != 0:
        log(f"lock acquire failed: {rc}")
        return rc
    try:
        data = load_ledger_for_write()
        if data is None:
            return EXIT_CORRUPT
        result = fn(data)
        if isinstance(result, tuple):
            code, payload = result
        else:
            code, payload = result, None
        if code == EXIT_OK and payload != "no-write":
            if data.get("candidates") or ledger_path().is_file():
                dump_ledger(data)
        return code
    finally:
        release_ledger()


def cmd_reject(args):
    if not (args.reason or "").strip():
        return EXIT_USAGE
    kind, ident = parse_key(args.key)
    if not kind:
        return EXIT_USAGE
    key = ledger_key(kind, ident)
    box = {}

    def mutate(data):
        entry = (data.get("candidates") or {}).get(key)
        if entry is None:
            return EXIT_MISSING, "no-write"
        entry["state"] = "rejected"
        entry["reason"] = args.reason.strip()
        entry["resolved_at"] = today()
        box["entry"] = entry
        box["key"] = key
        return EXIT_OK

    rc = _with_write_lock(mutate)
    if rc == EXIT_OK:
        print(json.dumps({"key": box["key"], **box["entry"]}, ensure_ascii=False, indent=2))
    return rc


def cmd_reopen(args):
    kind, ident = parse_key(args.key)
    if not kind:
        return EXIT_USAGE
    key = ledger_key(kind, ident)
    box = {}

    def mutate(data):
        entry = (data.get("candidates") or {}).get(key)
        if entry is None:
            return EXIT_MISSING, "no-write"
        if entry.get("state") != "rejected":
            return EXIT_USAGE, "no-write"
        entry["state"] = "pending"
        entry["reason"] = None
        entry["resolved_at"] = None
        box["entry"] = entry
        box["key"] = key
        return EXIT_OK

    rc = _with_write_lock(mutate)
    if rc == EXIT_OK:
        print(json.dumps({"key": box["key"], **box["entry"]}, ensure_ascii=False, indent=2))
    return rc


def cmd_mark_ingested(args):
    rel, path, text = read_source_page(args.page)
    if path is None or text is None:
        return EXIT_MISSING
    rec = paper_ids.derive_ids(rel, text)
    if rec is None:
        return EXIT_MISSING
    targets = []
    if rec.get("arxiv"):
        targets.append(("arxiv", rec["arxiv"]))
    if rec.get("doi"):
        targets.append(("doi", rec["doi"]))
    box = {"closed": []}

    def mutate(data):
        day = today()
        for kind, ident in targets:
            key = ledger_key(kind, ident)
            entry = (data.get("candidates") or {}).get(key)
            if entry is None:
                continue
            entry["state"] = "ingested"
            entry["ingested_to"] = rel
            entry["resolved_at"] = day
            box["closed"].append(key)
        return EXIT_OK

    rc = _with_write_lock(mutate)
    if rc == EXIT_OK:
        print(json.dumps({"page": rel, "closed": box["closed"]}, ensure_ascii=False, indent=2))
    return rc


def ingest_hint(key, entry):
    kind, ident = ident_of(key, entry)
    if kind == "arxiv" and ident:
        return f"/wiki-ingest-paper https://arxiv.org/abs/{ident}"
    if kind == "doi" and ident:
        return f"/wiki-ingest-paper https://doi.org/{ident}"
    return "/wiki-ingest-paper"


def entry_title(entry):
    title = entry.get("title") if entry else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def recommend_text(entry, demand):
    citing = list(entry.get("citing") or [])[:3]
    cites = "、".join(citing)
    body = f"需要 {demand}。"
    roles = role_clause(entry)
    if roles:
        body += f"{roles}。"
    body += f"{cites} が引いている。"
    title = entry_title(entry)
    if title:
        return f"{title} — {body}"
    return body


def render_report_markdown(data, index, limit):
    lines = ["## Paper Ref Candidates", ""]
    if index is None:
        lines.append("paper-ids-index: missing")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    all_ready = ready_rows(data, index)
    shown = all_ready[:limit]
    omitted = max(0, len(all_ready) - len(shown))
    for key, entry, demand in shown:
        lines.append(
            f"- `{key}` — {recommend_text(entry, demand)} — `{ingest_hint(key, entry)}`"
        )
    if omitted:
        lines.append("")
        lines.append(f"omitted: {omitted}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def existing_created(path):
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = re.search(r"^created:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return m.group(1) if m else None


def write_report_page(body):
    rc = acquire_page()
    if rc == EXIT_LOCK:
        return EXIT_LOCK
    if rc != 0:
        log(f"page lock acquire failed: {rc}")
        return rc
    try:
        path = vault_root() / PAGE_REL
        created = existing_created(path) or today()
        updated = today()
        page = (
            "---\n"
            "type: meta\n"
            'title: "Paper Ref Candidates"\n'
            f"created: {created}\n"
            f"updated: {updated}\n"
            "tags: [meta]\n"
            "status: developing\n"
            "---\n\n"
            "# Paper Ref Candidates\n\n"
            "`python3 scripts/paper-refs.py report --write` が生成する。手で本文を育てない。\n\n"
            f"{body.lstrip()}"
        )
        atomic_write(path, page)
        return EXIT_OK
    finally:
        release_page()


def cmd_report(args):
    data, corrupt = read_ledger_unlocked()
    if corrupt:
        return EXIT_CORRUPT
    index = read_paper_ids_index()
    limit = args.limit if args.limit is not None else DEFAULT_REPORT_LIMIT
    body = render_report_markdown(data, index, limit)
    sys.stdout.write(body)
    if not getattr(args, "write", False):
        return EXIT_OK
    return write_report_page(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="論文 raw の文献表から ingest 候補を台帳に積む。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract")
    p.add_argument("--source", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("backfill")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("list")
    p.add_argument("--ready", action="store_true")
    p.add_argument("--state", choices=STATES, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("key")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("reject")
    p.add_argument("key")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("reopen")
    p.add_argument("key")
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("mark-ingested")
    p.add_argument("--page", required=True)
    p.set_defaults(func=cmd_mark_ingested)

    p = sub.add_parser("report")
    p.add_argument("--limit", type=int, default=DEFAULT_REPORT_LIMIT)
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
