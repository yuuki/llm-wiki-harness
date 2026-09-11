#!/usr/bin/env python3
"""claim-audit.py — 命題の再検証(adversary)のためのパケット生成と結果の台帳。

concept ページの主題節にある太字命題は、再編纂で観察を畳んだ結果であり、畳む過程で
出典に無い一般化や数値のずれが入り得る。本スクリプトは命題を標本抽出し、命題が引く
source ページ(と、あれば `.raw/` の抽出テキスト)から語彙重なりの高い段落を切り出して
「命題 + 根拠候補」のパケットにする。判定(支持 / 不支持 / 出典に無い)は LLM か人間が行い、
`record` で台帳と報告書に残す。本スクリプトは wiki の本文ページを書かない。

使い方:
  python3 scripts/claim-audit.py sample --pages wiki/concepts/X.md [...] [--per-page 5]
  python3 scripts/claim-audit.py sample --recompiled [--limit 5] [--per-page 3]
  python3 scripts/claim-audit.py sample --random 10 [--seed 7] [--per-page 2]
      命題を選び、根拠候補の抜粋を添えた JSON を stdout に出す。台帳に既に判定のある命題は
      --reaudit を付けない限り除く。--include-inbox で受信箱の観察も対象にする。
  python3 scripts/claim-audit.py record --verdicts /tmp/verdicts.json [--by human|agent]
      判定 JSON([{"id","verdict","note"}])を台帳 `.vault-meta/claim-audit.json` に追記し、
      `wiki/meta/claim-audit-YYYY-MM-DD.md` を当日分の台帳から再描画する。
  python3 scripts/claim-audit.py resolve <id> [--note TEXT]
      不支持・出典に無いと判定した命題に対処した(留保を付けた / 出典を直した)ことを台帳に記す。
  python3 scripts/claim-audit.py report
      台帳の集計を markdown で stdout に出す(lint 用)。

判定語:
  supported       出典の記述が命題を支持する
  unsupported     出典の記述が命題と食い違う(数値・条件・向き)
  not_in_source   出典にその内容が無い(命題が正しくても引用が誤り。wiki 側の解釈・対応づけも含む)
  source_missing  引かれた source ページが存在しない
  unclear         抜粋では判定できない(source ページを excerpt して再判定する)

evidence の status:
  ok / stub(lint が作った source stub か本文 120 字未満。wiki からは検証できないので raw か原典を見る)
  / source_missing / unreadable

環境変数 WIKI_VAULT_ROOT で vault ルートを差し替えられる(試験用)。

終了コード:
  0 — 成功
  2 — 使い方の誤り
  3 — 入力ファイルが無い
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_lock import page_lock

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

LEDGER_REL = ".vault-meta/claim-audit.json"
REPORT_DIR_REL = "wiki/meta"
VERDICTS = ("supported", "unsupported", "not_in_source", "source_missing", "unclear")

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
PROPOSITION_RE = re.compile(r"^- \*\*(.+?)\*\*\s*(.*)$")
EVIDENCE_RE = re.compile(r"^\s{2,}- (根拠|反証|留保|関連):\s*(.*)$")
TOP_BULLET_RE = re.compile(r"^- (.*)$")
RAW_TEXT_SUFFIXES = (".txt", ".md")
EXCERPT_CHARS = 420
TOP_PARAGRAPHS = 3


def log(msg):
    print(msg, file=sys.stderr)


def load_stats_module():
    spec = importlib.util.spec_from_file_location("wiki_concept_stats", _SCRIPTS / "wiki-concept-stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STATS = load_stats_module()


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


def rel_posix(path):
    return Path(path).resolve().relative_to(VAULT_ROOT.resolve()).as_posix()


# --------------------------------------------------------------------------- 命題の抽出

def claim_id(page_rel, text):
    norm = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(f"{page_rel}\n{norm}".encode("utf-8")).hexdigest()[:10]


def source_names(text):
    return [t.strip() for t in WIKILINK_RE.findall(text) if t.strip().startswith("@")]


def extract_claims(page_rel, include_inbox=False):
    path = VAULT_ROOT / page_rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    fm, body = STATS.split_frontmatter(text)
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
                })
            i = j
    for c in claims:
        c["id"] = claim_id(page_rel, c["claim"])
    return claims


# --------------------------------------------------------------------------- 根拠の抜粋

def tokens(text):
    """和文は文字 2-gram、英数字は小文字の語。記号・空白は捨てる。"""
    out = set()
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-+%]*", text):
        out.add(word.lower())
    cjk = re.sub(r"[^\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]", " ", text)
    for run in cjk.split():
        for k in range(len(run) - 1):
            out.add(run[k:k + 2])
    return out


def looks_like_table_dump(s):
    """PDF 抽出テキストの表・図キャプション断片(数字と短い断片ばかり)を捨てる。"""
    toks = s.split()
    if len(toks) < 6:
        return True
    numeric = sum(1 for t in toks if re.fullmatch(r"[\d.,%±()\-–]+", t))
    return numeric / len(toks) > 0.35


def paragraphs(text):
    paras = []
    for chunk in re.split(r"\n\s*\n", text):
        s = re.sub(r"\s+", " ", " ".join(line.strip() for line in chunk.splitlines() if line.strip())).strip()
        if len(s) >= 20 and not looks_like_table_dump(s):
            paras.append(s)
    return paras


def bullet_aware_paragraphs(body):
    """wiki ページは箇条書き 1 行が 1 主張なので、箇条書きは行単位、散文は段落単位。"""
    out = []
    for chunk in re.split(r"\n\s*\n", body):
        lines = [l for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue
        if all(re.match(r"^\s*[-*] ", l) or re.match(r"^\s*>", l) for l in lines):
            for l in lines:
                s = re.sub(r"^\s*[-*>]\s*", "", l).strip()
                if len(s) >= 20:
                    out.append(s)
        else:
            s = " ".join(l.strip() for l in lines)
            if len(s) >= 20 and not s.startswith("#"):
                out.append(s)
    return out


def rank_paragraphs(claim, paras, top=TOP_PARAGRAPHS):
    ct = tokens(claim)
    if not ct:
        return []
    scored = []
    for p in paras:
        pt = tokens(p)
        if not pt:
            continue
        overlap = len(ct & pt)
        if overlap == 0:
            continue
        score = overlap / math.sqrt(len(pt))
        scored.append((score, p))
    scored.sort(key=lambda sp: -sp[0])
    out = []
    for score, p in scored[:top]:
        out.append({"score": round(score, 3), "text": p if len(p) <= EXCERPT_CHARS else p[: EXCERPT_CHARS - 1] + "…"})
    return out


def resolve_source_page(name):
    p = VAULT_ROOT / "wiki" / "sources" / f"{name}.md"
    return p if p.is_file() else None


def raw_text_paths(fm):
    """source ページの `sources:` にある `.raw/...` 参照から、読める抽出テキストを探す。"""
    out = []
    for item in STATS.as_list(fm.get("sources")):
        target = STATS.wikilink_target(item) or str(item)
        target = target.strip().strip('"')
        if not target.startswith(".raw/"):
            continue
        cand = VAULT_ROOT / target
        candidates = []
        if cand.suffix.lower() in RAW_TEXT_SUFFIXES:
            candidates.append(cand)
        else:
            for suf in RAW_TEXT_SUFFIXES:
                candidates.append(cand.with_suffix(suf))
        for c in candidates:
            if c.is_file() and c not in out:
                out.append(c)
    return out


def evidence_packet(claim):
    packet = []
    for name in claim["sources"]:
        page = resolve_source_page(name)
        if page is None:
            packet.append({"source": name, "page": None, "status": "source_missing", "page_excerpts": [],
                           "raw_text": None, "raw_excerpts": []})
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            packet.append({"source": name, "page": rel_posix(page), "status": "unreadable", "page_excerpts": [],
                           "raw_text": None, "raw_excerpts": []})
            continue
        fm, body = STATS.split_frontmatter(text)
        paras = bullet_aware_paragraphs(body)
        for kc in STATS.as_list(fm.get("key_claims")):
            if len(str(kc)) >= 20:
                paras.append(str(kc))
        is_stub = "source stub" in body or len(body.strip()) < 120
        entry = {
            "source": name,
            "page": rel_posix(page),
            "status": "stub" if is_stub else "ok",
            "page_excerpts": rank_paragraphs(claim["claim"], paras),
            "raw_text": None,
            "raw_excerpts": [],
        }
        raws = raw_text_paths(fm)
        if raws:
            raw = raws[0]
            try:
                raw_text = raw.read_text(encoding="utf-8", errors="replace")
                entry["raw_text"] = rel_posix(raw)
                entry["raw_excerpts"] = rank_paragraphs(claim["claim"], paragraphs(raw_text))
            except OSError:
                pass
        packet.append(entry)
    return packet


# --------------------------------------------------------------------------- 台帳

def load_ledger():
    path = VAULT_ROOT / LEDGER_REL
    if not path.is_file():
        return {"version": 1, "updated_at": None, "verdicts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"ERR: ledger unreadable: {exc}")
        raise SystemExit(EXIT_USAGE)
    data.setdefault("verdicts", {})
    return data


def save_ledger(data):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    atomic_write(VAULT_ROOT / LEDGER_REL, json.dumps(data, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- sample

def concept_pages():
    folder = VAULT_ROOT / "wiki" / "concepts"
    if not folder.is_dir():
        return []
    return sorted(rel_posix(p) for p in folder.glob("*.md") if not p.name.startswith("_"))


def recompiled_pages(limit):
    rows = []
    for rel in concept_pages():
        try:
            text = (VAULT_ROOT / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = STATS.split_frontmatter(text)
        rec = fm.get("recompiled")
        if rec and not isinstance(rec, list):
            rows.append((str(rec), rel))
    rows.sort(reverse=True)
    return [rel for _, rel in rows[:limit]]


def sample(pages, per_page, include_inbox, reaudit, rng):
    ledger = load_ledger()
    audited = set(ledger["verdicts"])
    out = []
    skipped = 0
    for rel in pages:
        claims = extract_claims(rel, include_inbox=include_inbox)
        if not reaudit:
            fresh = [c for c in claims if c["id"] not in audited]
            skipped += len(claims) - len(fresh)
            claims = fresh
        if rng is not None:
            rng.shuffle(claims)
        for c in claims[:per_page]:
            c["evidence"] = evidence_packet(c)
            out.append(c)
    return out, skipped


# --------------------------------------------------------------------------- record / report

def render_report(day, entries):
    counts = {v: 0 for v in VERDICTS}
    for e in entries:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    lines = [
        "---",
        "type: meta",
        f'title: "Claim Audit {day}"',
        f"date: {day} 00:00",
        f"created: {day}",
        f"updated: {day}",
        "tags:",
        f"  - {day.replace('-', '/')}",
        "  - meta",
        "  - claim-audit",
        "status: developing",
        "related:",
        '  - "[[index]]"',
        '  - "[[conventions]]"',
        "---",
        "",
        f"# Claim Audit: {day}",
        "",
        "`python3 scripts/claim-audit.py record` が台帳(`.vault-meta/claim-audit.json`)の当日分から再描画する。手で編集しない。"
        "判定は命題が引く source ページ(と `.raw/` の抽出テキスト)との照合であり、命題そのものの真偽ではない。",
        "",
        "## Summary",
        "",
        f"- Claims audited: {len(entries)}("
        + " / ".join(f"{v} {counts[v]}" for v in VERDICTS) + ")",
        f"- Pages: {len({e['page'] for e in entries})}",
        "",
        "## Findings",
        "",
    ]
    problems = [e for e in entries if e["verdict"] != "supported"]
    if not problems:
        lines.append("- 不支持・出典に無い・欠損の判定なし。")
    for e in sorted(problems, key=lambda e: (e["page"], e["line"])):
        name = Path(e["page"]).stem
        srcs = "、".join(f"[[{s}]]" for s in e.get("sources", [])[:3]) or "—"
        note = f" — {e['note']}" if e.get("note") else ""
        lines.append(f"- [[{name}]] L{e['line']} — **{e['claim'][:120]}** — `{e['verdict']}`{note}。出典: {srcs}")
    lines += ["", "## Supported", ""]
    ok = [e for e in entries if e["verdict"] == "supported"]
    if not ok:
        lines.append("- なし")
    for e in sorted(ok, key=lambda e: (e["page"], e["line"])):
        lines.append(f"- [[{Path(e['page']).stem}]] L{e['line']} — {e['claim'][:100]}")
    lines.append("")
    return "\n".join(lines)


def record(verdicts_path, by):
    path = Path(verdicts_path)
    if not path.is_file():
        log(f"ERR: verdicts file not found: {verdicts_path}")
        return None, EXIT_MISSING
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"ERR: verdicts JSON invalid: {exc}")
        return None, EXIT_USAGE
    if isinstance(items, dict):
        items = items.get("verdicts") or items.get("claims") or []
    ledger = load_ledger()
    day = date.today().isoformat()
    accepted, rejected = 0, []
    for item in items:
        cid = str(item.get("id", "")).strip()
        verdict = str(item.get("verdict", "")).strip()
        if not cid or verdict not in VERDICTS:
            rejected.append({"id": cid, "verdict": verdict})
            continue
        entry = {
            "id": cid,
            "page": item.get("page", ""),
            "line": int(item.get("line", 0) or 0),
            "claim": item.get("claim", ""),
            "sources": item.get("sources", []),
            "verdict": verdict,
            "note": str(item.get("note", "")).strip(),
            "date": day,
            "by": by,
        }
        prev = ledger["verdicts"].get(cid)
        if prev:
            entry["history"] = prev.get("history", []) + [{k: prev[k] for k in ("verdict", "note", "date", "by") if k in prev}]
        ledger["verdicts"][cid] = entry
        accepted += 1
    save_ledger(ledger)
    todays = [e for e in ledger["verdicts"].values() if e.get("date") == day]
    report_rel = f"{REPORT_DIR_REL}/claim-audit-{day}.md"
    with page_lock(report_rel):
        atomic_write(VAULT_ROOT / report_rel, render_report(day, todays))
    return {"ok": True, "accepted": accepted, "rejected": rejected, "report": report_rel,
            "ledger": LEDGER_REL, "today_total": len(todays)}, EXIT_OK


def resolve(cid, note):
    ledger = load_ledger()
    entry = ledger["verdicts"].get(cid)
    if entry is None:
        log(f"ERR: verdict not found: {cid}")
        return None, EXIT_MISSING
    entry["resolved"] = date.today().isoformat()
    entry["resolved_note"] = note
    save_ledger(ledger)
    return {"ok": True, "id": cid, "resolved": entry["resolved"], "note": note}, EXIT_OK


def render_summary(ledger):
    entries = list(ledger["verdicts"].values())
    counts = {v: 0 for v in VERDICTS}
    for e in entries:
        counts[e.get("verdict", "unclear")] = counts.get(e.get("verdict", "unclear"), 0) + 1
    pages = {}
    for e in entries:
        if e.get("verdict") in ("unsupported", "not_in_source", "source_missing"):
            pages[e.get("page", "")] = pages.get(e.get("page", ""), 0) + 1
    lines = [
        "## Claim Audit",
        f"- Claims audited (cumulative): {len(entries)}(" + " / ".join(f"{v} {counts[v]}" for v in VERDICTS) + ")",
    ]
    if entries:
        bad = counts["unsupported"] + counts["not_in_source"] + counts["source_missing"]
        lines.append(f"- Problem rate: {bad}/{len(entries)} = {bad / len(entries):.1%}")
    if pages:
        top = sorted(pages.items(), key=lambda kv: -kv[1])[:5]
        lines.append("- Pages with most problems: " + ", ".join(f"[[{Path(p).stem}]] ({n})" for p, n in top if p))
    open_items = [e for e in entries if e.get("verdict") in ("unsupported", "not_in_source") and not e.get("resolved")]
    if open_items:
        lines.append(f"- Unresolved findings: {len(open_items)}(命題側に `- 留保: 再検証` を付けるか、出典を直す)")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="命題の再検証パケットと台帳。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sample")
    p.add_argument("--pages", nargs="*", default=[])
    p.add_argument("--recompiled", action="store_true", help="recompiled: を持つ concept を新しい順に")
    p.add_argument("--random", type=int, default=0, metavar="N", help="concept をランダムに N 頁")
    p.add_argument("--limit", type=int, default=5, help="--recompiled の頁数(既定 5)")
    p.add_argument("--per-page", type=int, default=3)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--include-inbox", action="store_true")
    p.add_argument("--reaudit", action="store_true", help="台帳に判定のある命題も含める")

    p = sub.add_parser("record")
    p.add_argument("--verdicts", required=True)
    p.add_argument("--by", default="agent", choices=("human", "agent"))

    p = sub.add_parser("resolve")
    p.add_argument("id")
    p.add_argument("--note", default="", help="何をしたか(留保を付けた / 出典を直した / 命題を弱めた)")

    sub.add_parser("report")

    args = parser.parse_args(argv)

    if args.cmd == "sample":
        if args.per_page < 1 or args.limit < 1 or args.random < 0:
            log("ERR: --per-page / --limit は 1 以上、--random は 0 以上")
            return EXIT_USAGE
        modes = sum(bool(x) for x in (args.pages, args.recompiled, args.random))
        if modes != 1:
            log("ERR: --pages / --recompiled / --random のいずれか 1 つを指定する")
            return EXIT_USAGE
        rng = random.Random(args.seed) if (args.seed is not None or args.random) else None
        if args.pages:
            pages = []
            for raw in args.pages:
                rel = raw if raw.startswith("wiki/") else f"wiki/concepts/{raw.removesuffix('.md')}.md"
                if not (VAULT_ROOT / rel).is_file():
                    log(f"ERR: page not found: {rel}")
                    return EXIT_MISSING
                pages.append(rel)
        elif args.recompiled:
            pages = recompiled_pages(args.limit)
        else:
            pool = concept_pages()
            rng.shuffle(pool)
            pages = pool[: args.random]
        claims, skipped = sample(pages, args.per_page, args.include_inbox, args.reaudit, rng)
        log(f"claim-audit: {len(claims)} claim(s) from {len(pages)} page(s); {skipped} already audited")
        print(json.dumps({"generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                          "pages": pages, "claims": claims}, ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "record":
        out, code = record(args.verdicts, args.by)
        if out is not None:
            log(f"claim-audit: recorded {out['accepted']} verdict(s); report {out['report']}")
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return code

    if args.cmd == "resolve":
        out, code = resolve(args.id.strip(), args.note.strip())
        if out is not None:
            print(json.dumps(out, ensure_ascii=False))
        return code

    if args.cmd == "report":
        sys.stdout.write(render_summary(load_ledger()))
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
