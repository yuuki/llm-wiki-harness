#!/usr/bin/env python3
"""recompile-queue.py — 再編纂キュー(compile 負債の永続化と却下フィードバック)。

`wiki-concept-stats.py --compile-debt` は毎回の走査結果を出すだけで、どのページに
着手したか、人間が却下した理由、何回却下されたかを覚えない。本スクリプトは負債を
`.vault-meta/recompile-queue.json`(git 追跡)に同期し、ページごとの状態と試行履歴を
持つ。wiki-refactor は着手前に `next` / `show` で却下理由を読み、同じ失敗を繰り返さない。

使い方:
  python3 scripts/recompile-queue.py refresh [--min-inbox 5] [--min-orphan-inbox 15]
      負債を走査してキューに同期する。新規は pending、負債が消えた pending / in_progress は done。
  python3 scripts/recompile-queue.py next [--limit 5] [--include-blocked]
      次に着手する候補を優先順に出す(却下理由を添える)。
  python3 scripts/recompile-queue.py show <page>
  python3 scripts/recompile-queue.py mark <page> --state in_progress|done|rejected|skipped
                                     [--reason TEXT] [--by human|agent] [--block-after 3]
      試行を記録する。rejected が --block-after 回に達したら blocked(人間が reopen するまで next に出ない)。
  python3 scripts/recompile-queue.py reopen <page>
  python3 scripts/recompile-queue.py report
      lint 用の markdown 断片を stdout に出す(ファイルは書かない)。

<page> は `wiki/concepts/X.md` か `X`(ページ名)のどちらでもよい。

状態:
  pending      負債あり、未着手
  in_progress  着手中(mark で宣言)
  done         負債が閾値を下回った(refresh が自動判定)か、mark で完了宣言
  rejected     再編纂結果を人間が却下した。reason を必ず残す。次の候補として出続ける
  blocked      却下が --block-after 回に達した。reopen まで next に出ない
  skipped      当面やらないと決めた。refresh は状態を変えない

キューは `.vault-meta/` 配下だが `.gitignore` の例外として追跡する(mode.json と同じ扱い)。
環境変数 WIKI_VAULT_ROOT で vault ルートを差し替えられる(試験用)。

終了コード:
  0 — 成功
  2 — 使い方の誤り
  3 — 対象ページがキューに無い / 見つからない
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

QUEUE_REL = ".vault-meta/recompile-queue.json"
STATES = ("pending", "in_progress", "done", "rejected", "blocked", "skipped")
MARKABLE = ("in_progress", "done", "rejected", "skipped")
DEFAULT_BLOCK_AFTER = 3


def log(msg):
    print(msg, file=sys.stderr)


def load_stats_module():
    """wiki-concept-stats.py(ハイフン名)を読み込み、負債走査と指標計算を再利用する。"""
    spec = importlib.util.spec_from_file_location("wiki_concept_stats", _SCRIPTS / "wiki-concept-stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def today():
    return date.today().isoformat()


# --------------------------------------------------------------------------- キュー入出力

def queue_path():
    return VAULT_ROOT / QUEUE_REL


def load_queue():
    path = queue_path()
    if not path.is_file():
        return {"version": 1, "updated_at": None, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"ERR: queue unreadable: {exc}")
        raise SystemExit(EXIT_USAGE)
    data.setdefault("version", 1)
    data.setdefault("entries", {})
    return data


def save_queue(data):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ordered = dict(sorted(data["entries"].items(), key=lambda kv: (-kv[1].get("inbox_bullets", 0), kv[0])))
    data["entries"] = ordered
    atomic_write(queue_path(), json.dumps(data, ensure_ascii=False, indent=1))


def normalize_page(raw):
    """`wiki/concepts/X.md` / `X.md` / `X` → `wiki/concepts/X.md`。"""
    s = raw.strip()
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2].split("|")[0]
    if s.startswith("wiki/concepts/"):
        return s if s.endswith(".md") else s + ".md"
    if s.endswith(".md"):
        s = s[:-3]
    return f"wiki/concepts/{s}.md"


def rejections(entry):
    return [a for a in entry.get("attempts", []) if a.get("state") == "rejected"]


def rejection_reasons(entry):
    return [a.get("reason", "") for a in rejections(entry) if a.get("reason")]


def public_entry(entry, path):
    out = {
        "path": path,
        "title": entry.get("title", Path(path).stem),
        "state": entry.get("state"),
        "inbox_bullets": entry.get("inbox_bullets", 0),
        "topic_sections": entry.get("topic_sections", 0),
        "legacy_heading": entry.get("legacy_heading", False),
        "lines": entry.get("lines", 0),
        "first_seen": entry.get("first_seen"),
        "resolved_at": entry.get("resolved_at"),
        "recompiled": entry.get("recompiled"),
        "rejections": len(rejections(entry)),
        "rejection_reasons": rejection_reasons(entry),
        "attempts": entry.get("attempts", []),
    }
    return out


# --------------------------------------------------------------------------- refresh

def page_recompiled_date(stats, path):
    try:
        text = (VAULT_ROOT / path).read_text(encoding="utf-8")
    except OSError:
        return None, None
    fm, body = stats.split_frontmatter(text)
    rec = fm.get("recompiled")
    metrics = stats.compile_metrics(body)
    metrics["lines"] = len(text.splitlines())
    return (str(rec) if rec and not isinstance(rec, list) else None), metrics


def refresh(min_inbox, min_orphan_inbox):
    stats = load_stats_module()
    debt = {row["path"]: row for row in stats.scan_compile_debt(min_inbox, min_orphan_inbox)}
    data = load_queue()
    entries = data["entries"]
    day = today()
    added, resolved, reentered = [], [], []

    for path, row in debt.items():
        rec, _ = page_recompiled_date(stats, path)
        entry = entries.get(path)
        metrics = {
            "title": row["title"],
            "inbox_bullets": row["inbox_bullets"],
            "topic_sections": row["topic_sections"],
            "legacy_heading": row["legacy_heading"],
            "lines": row["lines"],
            "recompiled": rec,
        }
        if entry is None:
            entries[path] = {"state": "pending", "first_seen": day, "resolved_at": None, "attempts": [], **metrics}
            added.append(path)
            continue
        entry.update(metrics)
        if entry.get("state") == "done":
            entry["state"] = "pending"
            entry["resolved_at"] = None
            entry.setdefault("attempts", []).append({"date": day, "state": "reentered", "by": "refresh",
                                                     "reason": f"inbox {row['inbox_bullets']} 件で負債に再入"})
            reentered.append(path)

    for path, entry in entries.items():
        if path in debt:
            continue
        rec, metrics = page_recompiled_date(stats, path)
        if metrics is None:
            entry["missing"] = True
            continue
        entry.pop("missing", None)
        entry.update({"inbox_bullets": metrics["inbox_bullets"], "topic_sections": metrics["topic_sections"],
                      "legacy_heading": metrics["legacy_heading"], "lines": metrics["lines"], "recompiled": rec})
        if entry.get("state") in ("pending", "in_progress"):
            entry["state"] = "done"
            entry["resolved_at"] = day
            entry.setdefault("attempts", []).append({"date": day, "state": "done", "by": "refresh",
                                                     "reason": "負債が閾値を下回った"})
            resolved.append(path)

    save_queue(data)
    return {
        "ok": True,
        "queue": QUEUE_REL,
        "thresholds": {"min_inbox": min_inbox, "min_orphan_inbox": min_orphan_inbox},
        "debt_pages": len(debt),
        "counts": state_counts(entries),
        "waiting_bullets": sum(e.get("inbox_bullets", 0) for e in entries.values()
                               if e.get("state") in ("pending", "in_progress", "rejected")),
        "added": added,
        "resolved": resolved,
        "reentered": reentered,
    }


def state_counts(entries):
    counts = {s: 0 for s in STATES}
    for e in entries.values():
        counts[e.get("state", "pending")] = counts.get(e.get("state", "pending"), 0) + 1
    return counts


# --------------------------------------------------------------------------- next / show / mark

def next_candidates(entries, limit, include_blocked):
    def order(item):
        path, e = item
        state_rank = {"in_progress": 0, "pending": 1, "rejected": 2, "blocked": 3}.get(e.get("state"), 9)
        return (state_rank, -e.get("inbox_bullets", 0), path)

    allowed = {"pending", "in_progress", "rejected"} | ({"blocked"} if include_blocked else set())
    rows = [(p, e) for p, e in entries.items() if e.get("state") in allowed and not e.get("missing")]
    rows.sort(key=order)
    return [public_entry(e, p) for p, e in rows[:limit]]


def mark(page, state, reason, by, block_after):
    data = load_queue()
    path = normalize_page(page)
    entry = data["entries"].get(path)
    if entry is None:
        if not (VAULT_ROOT / path).is_file():
            log(f"ERR: page not found: {path}")
            return None, EXIT_MISSING
        entry = {"state": "pending", "first_seen": today(), "resolved_at": None, "attempts": [],
                 "title": Path(path).stem, "inbox_bullets": 0, "topic_sections": 0, "legacy_heading": False,
                 "lines": 0, "recompiled": None}
        data["entries"][path] = entry
    if state == "rejected" and not reason:
        log("ERR: --state rejected には --reason が必要(次の試行が読む)")
        return None, EXIT_USAGE
    entry.setdefault("attempts", []).append({"date": today(), "state": state, "by": by, "reason": reason or ""})
    entry["state"] = state
    if state == "done":
        entry["resolved_at"] = today()
    elif state == "rejected" and len(rejections(entry)) >= block_after:
        entry["state"] = "blocked"
    save_queue(data)
    return public_entry(entry, path), EXIT_OK


def reopen(page):
    data = load_queue()
    path = normalize_page(page)
    entry = data["entries"].get(path)
    if entry is None:
        log(f"ERR: not in queue: {path}")
        return None, EXIT_MISSING
    entry.setdefault("attempts", []).append({"date": today(), "state": "reopened", "by": "human", "reason": ""})
    entry["state"] = "pending"
    entry["resolved_at"] = None
    save_queue(data)
    return public_entry(entry, path), EXIT_OK


# --------------------------------------------------------------------------- report

def render_report(entries, top=10):
    counts = state_counts(entries)
    waiting = [(p, e) for p, e in entries.items() if e.get("state") in ("pending", "in_progress", "rejected")]
    waiting.sort(key=lambda pe: -pe[1].get("inbox_bullets", 0))
    bullets = sum(e.get("inbox_bullets", 0) for _, e in waiting)
    lines = [
        "## Recompile Queue",
        f"- Queue: pending {counts['pending']} / in_progress {counts['in_progress']} / rejected {counts['rejected']}"
        f" / blocked {counts['blocked']} / skipped {counts['skipped']} / done {counts['done']}(累計)",
        f"- Waiting inbox bullets: {bullets}(pending + in_progress + rejected の合計)",
    ]
    if waiting:
        lines.append(f"- Top {min(top, len(waiting))}:")
        for path, e in waiting[:top]:
            name = Path(path).stem
            tag = f"{e.get('inbox_bullets', 0)} 観察 / {e.get('topic_sections', 0)} 主題節"
            if e.get("legacy_heading"):
                tag += " / 旧見出し"
            rej = rejection_reasons(e)
            extra = f" — 却下 {len(rej)} 回: {rej[-1]}" if rej else ""
            lines.append(f"  - [[{name}]]({tag}, {e.get('state')}){extra}")
    blocked = [(p, e) for p, e in entries.items() if e.get("state") == "blocked"]
    if blocked:
        lines.append("- Blocked(人間の reopen 待ち):")
        for path, e in blocked:
            reasons = rejection_reasons(e)
            lines.append(f"  - [[{Path(path).stem}]] — 却下 {len(reasons)} 回: {' / '.join(reasons[-3:])}")
    missing = [p for p, e in entries.items() if e.get("missing")]
    if missing:
        lines.append(f"- Missing pages still in queue: {len(missing)}(削除・改名されたページ。refresh は状態を変えない)")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="再編纂キュー(compile 負債の永続化と却下フィードバック)。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("refresh")
    p.add_argument("--min-inbox", type=int, default=5)
    p.add_argument("--min-orphan-inbox", type=int, default=15)

    p = sub.add_parser("next")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--include-blocked", action="store_true")

    p = sub.add_parser("show")
    p.add_argument("page")

    p = sub.add_parser("mark")
    p.add_argument("page")
    p.add_argument("--state", required=True, choices=MARKABLE)
    p.add_argument("--reason", default="")
    p.add_argument("--by", default="agent", choices=("human", "agent"))
    p.add_argument("--block-after", type=int, default=DEFAULT_BLOCK_AFTER)

    p = sub.add_parser("reopen")
    p.add_argument("page")

    sub.add_parser("report")

    args = parser.parse_args(argv)

    if args.cmd == "refresh":
        if args.min_inbox < 0 or args.min_orphan_inbox < 0:
            log("ERR: 閾値は 0 以上")
            return EXIT_USAGE
        out = refresh(args.min_inbox, args.min_orphan_inbox)
        log(f"recompile-queue: {out['debt_pages']} debt page(s); +{len(out['added'])} added,"
            f" {len(out['resolved'])} resolved, {len(out['reentered'])} reentered")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "next":
        if args.limit < 1:
            log("ERR: --limit は 1 以上")
            return EXIT_USAGE
        data = load_queue()
        rows = next_candidates(data["entries"], args.limit, args.include_blocked)
        log(f"recompile-queue: {len(rows)} candidate(s)")
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "show":
        data = load_queue()
        path = normalize_page(args.page)
        entry = data["entries"].get(path)
        if entry is None:
            log(f"ERR: not in queue: {path}")
            return EXIT_MISSING
        print(json.dumps(public_entry(entry, path), ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "mark":
        if args.block_after < 1:
            log("ERR: --block-after は 1 以上")
            return EXIT_USAGE
        out, code = mark(args.page, args.state, args.reason.strip(), args.by, args.block_after)
        if out is not None:
            log(f"recompile-queue: {out['path']} → {out['state']}")
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return code

    if args.cmd == "reopen":
        out, code = reopen(args.page)
        if out is not None:
            log(f"recompile-queue: {out['path']} → pending")
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return code

    if args.cmd == "report":
        data = load_queue()
        sys.stdout.write(render_report(data["entries"]))
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
