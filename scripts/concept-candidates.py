#!/usr/bin/env python3
"""concept-candidates.py — concept 候補の台帳(新設閾値に届かない候補の永続化)。

conventions §12 は「concept の新設は 2 ソース以上にまたがるときだけ」「1 取り込みで
新規最大 3、溢れは log の `Deferred:` に書く」と定めるが、Deferred に書かれた候補が
その後どの source で再び現れたかを数える仕組みが無い。候補は log の中で散り、閾値に
達しても誰も気づかない。本スクリプトは候補を `.vault-meta/concept-candidates.json`
(git 追跡)に持ち、ingest が言及を追記し、lint が閾値到達を報告し、resolve が
「既存 concept は無いが台帳に候補がある」と返せるようにする。

使い方:
  python3 scripts/concept-candidates.py add --name NAME [--alias A ...] --source "@..." [--source ...]
                                            [--reason TEXT] [--by ingest|human|seed-log] [--date YYYY-MM-DD]
      候補に言及を 1 件足す。無ければ作る。source は `[[@...]]` でも `@...` でもよい。
  python3 scripts/concept-candidates.py list [--ready] [--state pending|promoted|rejected] [--min-sources 2] [--limit N]
      候補を source 数の多い順に出す。--ready は pending かつ独立 source 数 >= --min-sources。
  python3 scripts/concept-candidates.py show NAME
  python3 scripts/concept-candidates.py promote NAME --page wiki/concepts/X.md
      concept を新設したら台帳を promoted にする(ページの実在を確認する)。
  python3 scripts/concept-candidates.py reject NAME --reason TEXT
      「これは concept にしない」と決めた候補。以後の add は言及だけ積み、state は変えない。
  python3 scripts/concept-candidates.py reopen NAME
  python3 scripts/concept-candidates.py seed-from-log [--dry-run]
      wiki/log.md の `- Deferred:` 行から候補を機械的に拾って台帳に入れる(冪等)。
      これはスクリプトによる走査であり、エージェントが log を読む行為ではない。
  python3 scripts/concept-candidates.py lookup NAME [NAME ...]
      resolve 用。名前または別名が一致する候補を返す(無ければ空)。
  python3 scripts/concept-candidates.py report [--min-sources 2]
      lint 用の markdown 断片(`## Concept Candidates`)を stdout に出す。ファイルは書かない。

台帳の形:
  {"version": 1, "updated_at": ..., "candidates": {
     "<key>": {"name", "aliases": [], "sources": ["@..."], "state", "first_seen", "last_seen",
               "mentions": [{"date", "sources": [], "reason", "by"}], "promoted_to", "resolved_at", "reason"}}}
  key は NFKC 正規化 + casefold + 空白圧縮した名前。独立 source 数は章分割 source を文書に
  畳んで数える(`@X - Chapter 3 ...` と `@X - Chapter 7 ...` は 1 本)。既存 concept ページと同名の候補は
  list / report で `exists_as` を添えて出す(promote し忘れの検出)。

終了コード:
  0 — 成功 / 2 — 使い方の誤り / 3 — 候補が台帳に無い、またはページが見つからない
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wiki_lock import page_lock  # noqa: E402

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

LEDGER_REL = ".vault-meta/concept-candidates.json"
LOG_REL = "wiki/log.md"
CONCEPTS_DIR = "wiki/concepts"
STATES = ("pending", "promoted", "rejected")
DEFAULT_MIN_SOURCES = 2

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
LOG_HEAD_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]\s*(.*)$")
DEFERRED_RE = re.compile(r"^-\s*Deferred:\s*(.*)$")
NONE_WORDS = ("なし", "無し", "none", "n/a", "特になし")


def log(msg):
    print(msg, file=sys.stderr)


def today():
    return date.today().isoformat()


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

def norm_key(name):
    s = unicodedata.normalize("NFKC", name or "").casefold()
    return re.sub(r"\s+", " ", s).strip()


def strip_link(raw):
    s = (raw or "").strip()
    m = WIKILINK_RE.fullmatch(s)
    if m:
        s = m.group(1).strip()
    if s.startswith("wiki/"):
        s = s.split("/")[-1]
    if s.endswith(".md"):
        s = s[:-3]
    return s.strip()


def norm_source(raw):
    """`[[@X]]` / `@X` / `wiki/sources/@X.md` → `@X`。`@` の無いものは通さない。"""
    s = strip_link(raw)
    return s if s.startswith("@") else None


CHAPTER_SUFFIX_RE = re.compile(r"\s+-\s+(Chapter|Ch\.|Part|Section|Appendix|第|付録)\s*.*$", re.I)


def doc_key(source):
    """章分割 source(`@X - Chapter N ...`)を文書単位に畳む。独立 source 数は文書で数える(conventions §12)。"""
    return CHAPTER_SUFFIX_RE.sub("", source).strip()


def independent_sources(entry):
    return sorted({doc_key(s) for s in entry.get("sources", [])})


def concept_page_for(name, aliases=()):
    """同名(または別名)の concept ページがあればその vault 相対パス。"""
    root = VAULT_ROOT / CONCEPTS_DIR
    if not root.is_dir():
        return None
    wanted = {norm_key(name)} | {norm_key(a) for a in aliases if a}
    for p in root.glob("*.md"):
        if norm_key(p.stem) in wanted:
            return f"{CONCEPTS_DIR}/{p.name}"
    return None


# --------------------------------------------------------------------------- 台帳入出力

def ledger_path():
    return VAULT_ROOT / LEDGER_REL


def load_ledger():
    path = ledger_path()
    if not path.is_file():
        return {"version": 1, "updated_at": None, "candidates": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"ERR: ledger unreadable: {exc}")
        raise SystemExit(EXIT_USAGE)
    data.setdefault("version", 1)
    data.setdefault("candidates", {})
    return data


def save_ledger(data):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ordered = dict(sorted(data["candidates"].items(),
                          key=lambda kv: (-len(independent_sources(kv[1])), kv[1].get("first_seen") or "", kv[0])))
    data["candidates"] = ordered
    atomic_write(ledger_path(), json.dumps(data, ensure_ascii=False, indent=1))


def find_entry(data, name):
    key = norm_key(strip_link(name))
    if key in data["candidates"]:
        return key, data["candidates"][key]
    for k, e in data["candidates"].items():
        if key in {norm_key(a) for a in e.get("aliases", [])}:
            return k, e
    return None, None


def new_entry(name, day):
    return {
        "name": name,
        "aliases": [],
        "sources": [],
        "state": "pending",
        "first_seen": day,
        "last_seen": day,
        "mentions": [],
        "promoted_to": None,
        "resolved_at": None,
        "reason": None,
    }


def record_mention(data, name, aliases, sources, reason, by, day):
    """言及を 1 件足す。(day, sources, reason) が同じ言及は二重に積まない。戻り値は (key, entry, added)。"""
    key, entry = find_entry(data, name)
    clean_name = strip_link(name)
    if entry is None:
        # 別名の側で既存候補に着くなら、そこへ寄せて今回の名前を別名にする
        for a in aliases or ():
            key, entry = find_entry(data, a)
            if entry is not None:
                aliases = list(aliases) + [clean_name]
                break
    if entry is None:
        key = norm_key(clean_name)
        entry = new_entry(clean_name, day)
        data["candidates"][key] = entry
    for a in aliases or ():
        a = strip_link(a)
        if a and norm_key(a) != norm_key(entry["name"]) and a not in entry["aliases"]:
            entry["aliases"].append(a)
    srcs = sorted({s for s in (norm_source(x) for x in sources or ()) if s})
    mention = {"date": day, "sources": srcs, "reason": (reason or "").strip(), "by": by}
    dup = any(m.get("date") == day and m.get("sources") == srcs and m.get("reason") == mention["reason"]
              for m in entry["mentions"])
    if dup:
        return key, entry, False
    entry["mentions"].append(mention)
    for s in srcs:
        if s not in entry["sources"]:
            entry["sources"].append(s)
    if day > (entry.get("last_seen") or ""):
        entry["last_seen"] = day
    if day < (entry.get("first_seen") or "9999"):
        entry["first_seen"] = day
    return key, entry, True


def public_entry(key, entry, min_sources):
    exists = concept_page_for(entry["name"], entry.get("aliases", ()))
    return {
        "key": key,
        "name": entry["name"],
        "aliases": entry.get("aliases", []),
        "state": entry.get("state", "pending"),
        "sources": entry.get("sources", []),
        "documents": independent_sources(entry),
        "source_count": len(independent_sources(entry)),
        "ready": entry.get("state") == "pending" and len(independent_sources(entry)) >= min_sources,
        "exists_as": exists,
        "first_seen": entry.get("first_seen"),
        "last_seen": entry.get("last_seen"),
        "mentions": len(entry.get("mentions", [])),
        "last_reason": (entry["mentions"][-1].get("reason") if entry.get("mentions") else None),
        "promoted_to": entry.get("promoted_to"),
        "reason": entry.get("reason"),
    }


# --------------------------------------------------------------------------- サブコマンド

def cmd_add(args):
    if not args.source:
        log("ERR: --source を 1 つ以上")
        return EXIT_USAGE
    day = args.date or today()
    with page_lock(LEDGER_REL):
        data = load_ledger()
        key, entry, added = record_mention(data, args.name, args.alias, args.source, args.reason, args.by, day)
        if added:
            save_ledger(data)
    out = public_entry(key, entry, args.min_sources)
    out["added"] = added
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if out["ready"]:
        log(f"concept-candidates: {entry['name']!r} は独立 source {out['source_count']} 本。新設を検討する(conventions §12)")
    if out["exists_as"]:
        log(f"concept-candidates: 同名の concept が既にある: {out['exists_as']}(promote し忘れか)")
    return EXIT_OK


def cmd_list(args):
    data = load_ledger()
    rows = [public_entry(k, e, args.min_sources) for k, e in data["candidates"].items()]
    if args.state:
        rows = [r for r in rows if r["state"] == args.state]
    if args.ready:
        rows = [r for r in rows if r["ready"]]
    rows.sort(key=lambda r: (-r["source_count"], r["first_seen"] or "", r["name"]))
    if args.limit:
        rows = rows[: args.limit]
    print(json.dumps({"count": len(rows), "candidates": rows}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_show(args):
    data = load_ledger()
    key, entry = find_entry(data, args.name)
    if entry is None:
        log(f"ERR: not in ledger: {args.name}")
        return EXIT_MISSING
    out = public_entry(key, entry, args.min_sources)
    out["mention_log"] = entry.get("mentions", [])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_promote(args):
    page = args.page.strip()
    if not page.startswith(CONCEPTS_DIR + "/"):
        page = f"{CONCEPTS_DIR}/{strip_link(page)}.md"
    if not (VAULT_ROOT / page).is_file():
        log(f"ERR: page not found: {page}")
        return EXIT_MISSING
    with page_lock(LEDGER_REL):
        data = load_ledger()
        key, entry = find_entry(data, args.name)
        if entry is None:
            log(f"ERR: not in ledger: {args.name}")
            return EXIT_MISSING
        entry["state"] = "promoted"
        entry["promoted_to"] = page
        entry["resolved_at"] = today()
        save_ledger(data)
    print(json.dumps(public_entry(key, entry, args.min_sources), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_reject(args):
    if not (args.reason or "").strip():
        log("ERR: reject には --reason が要る")
        return EXIT_USAGE
    with page_lock(LEDGER_REL):
        data = load_ledger()
        key, entry = find_entry(data, args.name)
        if entry is None:
            log(f"ERR: not in ledger: {args.name}")
            return EXIT_MISSING
        entry["state"] = "rejected"
        entry["reason"] = args.reason.strip()
        entry["resolved_at"] = today()
        save_ledger(data)
    print(json.dumps(public_entry(key, entry, args.min_sources), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_reopen(args):
    with page_lock(LEDGER_REL):
        data = load_ledger()
        key, entry = find_entry(data, args.name)
        if entry is None:
            log(f"ERR: not in ledger: {args.name}")
            return EXIT_MISSING
        entry["state"] = "pending"
        entry["promoted_to"] = None
        entry["resolved_at"] = None
        entry["reason"] = None
        save_ledger(data)
    print(json.dumps(public_entry(key, entry, args.min_sources), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_lookup(args):
    data = load_ledger()
    out = []
    for raw in args.names:
        key, entry = find_entry(data, raw)
        out.append({"query": raw, "candidate": public_entry(key, entry, args.min_sources) if entry else None})
    print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=2))
    return EXIT_OK


# --------------------------------------------------------------------------- log からの種入れ

def split_log_entries(text):
    """`## [YYYY-MM-DD] ...` 見出しごとに (date, header, lines) を返す。"""
    entries = []
    cur = None
    for line in text.splitlines():
        m = LOG_HEAD_RE.match(line)
        if m:
            cur = {"date": m.group(1), "header": m.group(2).strip(), "lines": []}
            entries.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    return entries


def is_none_text(text):
    s = re.sub(r"[()（）。\s]", "", text or "").casefold()
    if not s:
        return True
    return any(s.startswith(w.casefold()) for w in NONE_WORDS)


def candidate_names_from_deferred(text):
    """Deferred 行から候補名を拾う。wikilink があればそれだけ、無ければ区切り文字で割る。"""
    links = [strip_link(m.group(0)) for m in WIKILINK_RE.finditer(text)]
    links = [n for n in links if n and not n.startswith("@")]
    if links:
        return links
    head = re.split(r"\s+[—–-]{1,2}\s+|:\s|：", text, maxsplit=1)[0]
    names = [n.strip(" 。.、,") for n in re.split(r"[,、，/／]|\s+と\s+|\s+および\s+", head)]
    out = []
    for n in names:
        n = re.sub(r"^\d+[.)]\s*", "", n).strip()
        if not n or is_none_text(n) or len(n) > 40:
            continue
        # 文になっているものは候補名ではない(「〜は」「〜した」「〜に絞った」など)
        if SENTENCE_RE.search(n) or n.endswith(tuple("はをにがのでも")):
            continue
        out.append(n)
    return out


SENTENCE_RE = re.compile(r"。|、|した|しない|ない|する|ある|参照|スコープ|運用|作業|上限|見送|保留|閾値|判断|理由|名の|ページ|作らず|絞った")
SOURCE_LINE_RE = re.compile(r"^-\s*(Source|Sources|Chapters|Added|Created|Pages created|New source)\s*:", re.I)
INGEST_HEAD_RE = re.compile(r"^(wiki-)?(ingest|autoresearch|batch-ingest|enrich-source)", re.I)


def cmd_seed_from_log(args):
    path = VAULT_ROOT / LOG_REL
    if not path.is_file():
        log(f"ERR: {LOG_REL} not found")
        return EXIT_MISSING
    text = path.read_text(encoding="utf-8")
    planned = []
    for ent in split_log_entries(text):
        # concept 候補を生むのは ingest 系エントリだけ。tooling / query / recompile の Deferred は作業の保留
        if not INGEST_HEAD_RE.match(ent["header"]) and not args.all_kinds:
            continue
        # 出典は「取り込んだもの」を書く行から取る(Pages updated: に並ぶ既存 source を混ぜない)
        own = [l for l in ent["lines"] if SOURCE_LINE_RE.match(l.strip())]
        body = "\n".join(own or ent["lines"])
        sources = sorted({norm_source(m.group(0)) for m in WIKILINK_RE.finditer(body) if norm_source(m.group(0))})
        for line in ent["lines"]:
            m = DEFERRED_RE.match(line.strip())
            if not m:
                continue
            deferred = m.group(1).strip()
            if is_none_text(deferred):
                continue
            for name in candidate_names_from_deferred(deferred):
                if concept_page_for(name) and not args.include_existing:
                    continue
                planned.append({"name": name, "sources": sources, "reason": deferred[:200],
                                "date": ent["date"], "header": ent["header"]})
    if args.dry_run:
        print(json.dumps({"planned": len(planned), "items": planned}, ensure_ascii=False, indent=2))
        return EXIT_OK
    added = 0
    with page_lock(LEDGER_REL):
        data = load_ledger()
        for it in planned:
            _, _, ok = record_mention(data, it["name"], (), it["sources"], it["reason"], "seed-log", it["date"])
            added += int(ok)
        if added:
            save_ledger(data)
    print(json.dumps({"planned": len(planned), "added": added,
                      "candidates": len(load_ledger()["candidates"])}, ensure_ascii=False))
    return EXIT_OK


# --------------------------------------------------------------------------- report

def render_report(data, min_sources):
    rows = [public_entry(k, e, min_sources) for k, e in data["candidates"].items()]
    pending = [r for r in rows if r["state"] == "pending"]
    ready = [r for r in pending if r["ready"]]
    stale_promoted = [r for r in pending if r["exists_as"]]
    rejected = [r for r in rows if r["state"] == "rejected"]
    promoted = [r for r in rows if r["state"] == "promoted"]
    out = ["## Concept Candidates", ""]
    out.append(f"- 台帳: `{LEDGER_REL}`。候補 {len(rows)} 件(pending {len(pending)}、promoted {len(promoted)}、rejected {len(rejected)})")
    out.append(f"- 新設閾値(独立 source {min_sources} 本以上)に達した pending: {len(ready)} 件")
    out.append(f"- 同名の concept ページが既にあるのに pending のまま: {len(stale_promoted)} 件(`promote` し忘れ)")
    out.append("")
    if ready:
        out.append(f"### 新設を検討する候補({len(ready)} 件)")
        out.append("")
        out.append("| 候補 | 独立 source | 初出 | 最終 | 直近の保留理由 |")
        out.append("|---|---|---|---|---|")
        for r in sorted(ready, key=lambda r: (-r["source_count"], r["first_seen"] or "")):
            srcs = "、".join(f"[[{s}]]" for s in r["sources"][:3])
            more = f" ほか {len(r['sources']) - 3} 頁" if len(r["sources"]) > 3 else ""
            reason = (r["last_reason"] or "").replace("|", "／")[:80]
            out.append(f"| {r['name']} | {r['source_count']}: {srcs}{more} | {r['first_seen']} | {r['last_seen']} | {reason} |")
        out.append("")
    if stale_promoted:
        out.append("### promote し忘れ")
        out.append("")
        for r in stale_promoted:
            out.append(f"- {r['name']} → `{r['exists_as']}`(`concept-candidates.py promote \"{r['name']}\" --page \"{r['exists_as']}\"`)")
        out.append("")
    waiting = [r for r in pending if not r["ready"] and not r["exists_as"]]
    if waiting:
        out.append(f"### 閾値未達の pending({len(waiting)} 件、source 数の多い順に 15 件まで)")
        out.append("")
        for r in sorted(waiting, key=lambda r: (-r["source_count"], r["first_seen"] or ""))[:15]:
            out.append(f"- {r['name']}(source {r['source_count']}、初出 {r['first_seen']})")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cmd_report(args):
    data = load_ledger()
    sys.stdout.write(render_report(data, args.min_sources))
    return EXIT_OK


# --------------------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description="concept 候補の台帳。")
    parser.add_argument("--min-sources", type=int, default=DEFAULT_MIN_SOURCES,
                        help=f"新設閾値とする独立 source 数(既定 {DEFAULT_MIN_SOURCES})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add")
    p.add_argument("--name", required=True)
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--source", action="append", default=[], help="`[[@...]]` または `@...`。複数可")
    p.add_argument("--reason", default="")
    p.add_argument("--by", default="ingest", choices=("ingest", "human", "seed-log", "agent"))
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list")
    p.add_argument("--ready", action="store_true")
    p.add_argument("--state", choices=STATES, default=None)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("promote")
    p.add_argument("name")
    p.add_argument("--page", required=True)
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("reject")
    p.add_argument("name")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("reopen")
    p.add_argument("name")
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("lookup")
    p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("seed-from-log")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-existing", action="store_true",
                   help="同名の concept ページが既にある候補も入れる(既定は飛ばす)")
    p.add_argument("--all-kinds", action="store_true",
                   help="ingest 系以外のエントリ(tooling / query など)の Deferred も見る(既定は ingest 系だけ)")
    p.set_defaults(func=cmd_seed_from_log)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    if args.min_sources < 1:
        log("ERR: --min-sources は 1 以上")
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
