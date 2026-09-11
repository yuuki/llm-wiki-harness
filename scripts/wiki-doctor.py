#!/usr/bin/env python3
"""wiki-doctor.py — wiki レイヤーの機械状態を一括で見る読み取り専用の健全性検査。

`wiki-verify-ingest.py` は取り込み 1 回分のページを見、`wiki-lint` はページの内容を定期に見る。
どちらも見ないのが「機械状態」である: 検索索引の鮮度、消えたページを指す chunk、残留ロック、
address 計数器と実ページの整合、`.raw/.manifest.json` と原本の整合、台帳 JSON の破損、
hot 窓の超過、一時ファイルの残骸。本スクリプトはそれらを 1 コマンドで OK / WARN / FAIL に分け、
直すためのコマンドを添える。**何も書き換えない**(--fix は無い。直すコマンドは人が打つ)。

使い方:
  python3 scripts/wiki-doctor.py            # 表形式
  python3 scripts/wiki-doctor.py --json     # 機械可読
  python3 scripts/wiki-doctor.py --only locks,stale_chunks

検査(名前):
  retrieve_provisioned  BM25 索引と chunks がある
  bm25_freshness        索引の updated_at より新しい wiki ページの数
  stale_chunks          page_path が存在しない chunk(ページの移動・削除の残骸)
  unchunked_pages       chunk が無い wiki ページ(新規・未 refresh)
  derived_caches        graph.json / paper-ids.json / contradictions.json の鮮度
  locks                 .vault-meta/locks の残留(1 時間超は放置ロック)
  address_counter       address-counter.txt と最大 address、重複 address
  manifest              .raw/.manifest.json の原本欠落と未登録原本
  ledgers               台帳 JSON の構文と追跡状態
  hot_window            hot.md が 5 エントリ / 2,000 トークン以内
  log_head              log.md の先頭がエントリ見出し
  tmp_residue           wiki/ 配下の *.tmp
  auto_commit_disabled  plugin の自動コミットが無効化されている
  ollama                rerank 用 ollama(127.0.0.1:11434)への到達(INFO)
  git_dirty_wiki        wiki/ 配下の未コミット変更数(INFO)

終了コード: 0 FAIL なし / 1 FAIL あり / 2 使い方の誤り
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
META = VAULT_ROOT / ".vault-meta"
WIKI_DIRS = ("sources", "entities", "concepts", "questions", "surveys")
ADDRESS_RE = re.compile(r"^address:\s*\"?(c-\d{6})\"?\s*$", re.M)
HOT_MAX_ENTRIES, HOT_MAX_TOKENS = 5, 2000
LOCK_STALE_SEC = 3600
TRACKED_LEDGERS = ("recompile-queue.json", "claim-audit.json", "concept-candidates.json")
OPTIONAL_LEDGERS = ("entity-merges.json", "mode.json", "transport.json")


def wiki_pages():
    for sub in WIKI_DIRS:
        d = VAULT_ROOT / "wiki" / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            if not p.name.startswith("_"):
                yield p


def rel(p):
    try:
        return str(Path(p).resolve().relative_to(VAULT_ROOT))
    except ValueError:
        return str(p)


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, status, detail, fix=None):
        self.rows.append({"check": name, "status": status, "detail": detail, "fix": fix})

    def ok(self, n, d):
        self.add(n, "OK", d)

    def warn(self, n, d, fix=None):
        self.add(n, "WARN", d, fix)

    def fail(self, n, d, fix=None):
        self.add(n, "FAIL", d, fix)

    def info(self, n, d):
        self.add(n, "INFO", d)


# --------------------------------------------------------------------------- 検査

def check_retrieve_provisioned(r, ctx):
    idx = META / "bm25" / "index.json"
    chunks = META / "chunks"
    if idx.is_file() and chunks.is_dir() and any(chunks.iterdir()):
        r.ok("retrieve_provisioned", f"bm25 index + chunks({sum(1 for _ in chunks.iterdir())} 頁分)")
        ctx["provisioned"] = True
    else:
        r.warn("retrieve_provisioned", "BM25 索引か chunks が無い(retrieve.py は終了 10 を返す)",
               "bash bin/setup-retrieve.sh && python3 scripts/wiki-retrieve-refresh.py")
        ctx["provisioned"] = False


def check_bm25_freshness(r, ctx):
    idx = META / "bm25" / "index.json"
    if not idx.is_file():
        return
    try:
        head = idx.read_text(encoding="utf-8")[:400]
        m = re.search(r'"updated_at":\s*"([^"]+)"', head)
        updated = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp() if m else idx.stat().st_mtime
    except (OSError, ValueError):
        updated = idx.stat().st_mtime
    newer = [p for p in ctx["pages"] if p.stat().st_mtime > updated]
    age_h = (time.time() - updated) / 3600
    if newer:
        r.warn("bm25_freshness", f"索引より新しいページ {len(newer)} 頁(索引は {age_h:.1f} 時間前。mtime 基準なので frontmatter だけの変更も数える)",
               "python3 scripts/wiki-retrieve-refresh.py")
    else:
        r.ok("bm25_freshness", f"索引は最新({age_h:.1f} 時間前)")


def check_stale_chunks(r, ctx):
    chunks = META / "chunks"
    if not chunks.is_dir():
        return
    stale, paths = [], set()
    for d in chunks.iterdir():
        c0 = d / "chunk-000.json"
        if not c0.is_file():
            continue
        try:
            head = c0.read_text(encoding="utf-8")[:600]
            m = re.search(r'"page_path":\s*"([^"]+)"', head)
        except OSError:
            continue
        if not m:
            continue
        pp = m.group(1)
        paths.add(pp)
        if not (VAULT_ROOT / pp).is_file():
            stale.append(pp)
    ctx["chunk_paths"] = paths
    if stale:
        sample = "、".join(Path(s).name for s in stale[:3])
        r.warn("stale_chunks", f"存在しないページを指す chunk {len(stale)} 件(例: {sample})。retrieve が古いパスを返す",
               "python3 scripts/wiki-retrieve-refresh.py(全再構築。約 3 分)")
    else:
        r.ok("stale_chunks", f"chunk {len(paths)} 頁分すべて実在ページを指す")


def check_unchunked_pages(r, ctx):
    paths = ctx.get("chunk_paths")
    if paths is None:
        return
    missing = [p for p in ctx["pages"] if rel(p) not in paths]
    if missing:
        r.warn("unchunked_pages", f"chunk が無いページ {len(missing)} 頁(例: {Path(missing[0]).name})",
               "python3 scripts/wiki-retrieve-refresh.py")
    else:
        r.ok("unchunked_pages", "全ページに chunk がある")


def check_derived_caches(r, ctx):
    newest = max((p.stat().st_mtime for p in ctx["pages"]), default=0)
    notes, stale = [], []
    for name, fix in (("graph.json", "python3 scripts/wiki-graph.py build"),
                      ("paper-ids.json", "python3 scripts/paper-ids.py scan --write-index"),
                      ("contradictions.json", "python3 scripts/contradiction-index.py build")):
        f = META / name
        if not f.is_file():
            notes.append(f"{name} 無し")
            continue
        behind = sum(1 for p in ctx["pages"] if p.stat().st_mtime > f.stat().st_mtime)
        if behind:
            stale.append(f"{name} より新しいページ {behind} 頁({fix})")
        else:
            notes.append(f"{name} 最新")
    if stale:
        r.warn("derived_caches", "、".join(stale + notes))
    else:
        r.ok("derived_caches", "、".join(notes))


def check_locks(r, ctx):
    d = META / "locks"
    if not d.is_dir():
        r.ok("locks", "locks ディレクトリ無し")
        return
    held, stale = [], []
    now = time.time()
    for f in d.glob("*.lock"):
        age = now - f.stat().st_mtime
        try:
            body = f.read_text(encoding="utf-8").strip()
        except OSError:
            body = ""
        pid = None
        m = re.search(r"\b(\d{2,7})\b", body)
        if m:
            pid = int(m.group(1))
        alive = pid_alive(pid) if pid else None
        rec = f"{f.name[:8]} age {int(age)}s pid {pid} {'alive' if alive else 'dead' if alive is False else '?'}"
        (stale if age > LOCK_STALE_SEC or alive is False else held).append(rec)
    if stale:
        r.warn("locks", f"放置ロック {len(stale)} 件(持ち主が死んでいるか 1 時間超): " + "; ".join(stale[:3]),
               f"bash scripts/wiki-lock.sh reap {LOCK_STALE_SEC}")
    elif held:
        r.info("locks", f"保持中のロック {len(held)} 件(並行する書き込み中): " + "; ".join(held[:3]))
    else:
        r.ok("locks", "残留ロック無し")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def check_address_counter(r, ctx):
    counter_file = META / "address-counter.txt"
    addrs = Counter()
    for p in ctx["pages"]:
        try:
            head = p.read_text(encoding="utf-8")[:1500]
        except (OSError, UnicodeDecodeError):
            continue
        m = ADDRESS_RE.search(head)
        if m:
            addrs[m.group(1)] += 1
    dupes = [a for a, n in addrs.items() if n > 1]
    max_used = max((int(a[2:]) for a in addrs), default=0)
    try:
        counter = int(counter_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        counter = None
    if dupes:
        r.fail("address_counter", f"重複 address {len(dupes)} 件(例: {dupes[0]})。conventions 鉄則 7 に反する",
               "重複ページの片方を wiki-refactor で統合するか address を再採番する(手作業)")
    elif counter is None:
        r.warn("address_counter", f"address-counter.txt が無いか非数値(最大使用 c-{max_used:06d})",
               "bash scripts/allocate-address.sh --rebuild")
    elif counter < max_used:
        r.fail("address_counter", f"計数器 {counter} が最大使用 {max_used} より小さい(次の採番が衝突する)",
               "bash scripts/allocate-address.sh --rebuild")
    else:
        r.ok("address_counter", f"計数器 {counter}、最大使用 c-{max_used:06d}、address 付きページ {sum(addrs.values())} 頁、重複無し")


def check_manifest(r, ctx):
    mf = VAULT_ROOT / ".raw" / ".manifest.json"
    if not mf.is_file():
        r.warn("manifest", ".raw/.manifest.json が無い")
        return
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        r.fail("manifest", f".manifest.json を読めない: {exc}")
        return
    entries = [k for k in m if k.startswith(".raw/")]
    missing = [k for k in entries if not (VAULT_ROOT / k).exists()]
    registered = set(entries)
    unregistered = 0
    for sub in ("papers", "articles", "slides", "videos", "books", "theses"):
        d = VAULT_ROOT / ".raw" / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            # 原本はファイル(pdf / md)。<slug>/images などの派生ディレクトリは数えない
            if f.name.startswith(".") or not f.is_file() or f.suffix not in (".pdf", ".md"):
                continue
            if f".raw/{sub}/{f.name}" not in registered:
                unregistered += 1
    detail = (f"登録 {len(entries)} 件、原本欠落 {len(missing)} 件、未登録原本 {unregistered} 件"
              "(manifest 導入前の取り込み。wiki-ingest-* が次に触れたとき登録される)")
    if missing:
        r.warn("manifest", detail + f"(欠落例: {missing[0]})",
               ".raw/ の原本は不変。欠落は git log -- <path> で経緯を確かめ、復元するか manifest の項目を人が消す")
    else:
        r.ok("manifest", detail)


def check_ledgers(r, ctx):
    bad, notes = [], []
    tracked = git_tracked_meta()
    for name in TRACKED_LEDGERS + OPTIONAL_LEDGERS:
        f = META / name
        if not f.is_file():
            if name in TRACKED_LEDGERS:
                notes.append(f"{name} 無し")
            continue
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bad.append(name)
            continue
        if name in TRACKED_LEDGERS and tracked is not None and f".vault-meta/{name}" not in tracked:
            notes.append(f"{name} が git 未追跡(git add -f .vault-meta/{name})")
    if bad:
        r.fail("ledgers", "JSON が壊れている: " + "、".join(bad), "git checkout -- .vault-meta/<name> で戻す")
    elif notes:
        r.warn("ledgers", "、".join(notes))
    else:
        r.ok("ledgers", "台帳 JSON はすべて読める")


def git_tracked_meta():
    try:
        out = subprocess.run(["git", "ls-files", ".vault-meta"], cwd=str(VAULT_ROOT),
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return set(out.split())


def check_hot_window(r, ctx):
    hot = VAULT_ROOT / "wiki" / "hot.md"
    if not hot.is_file():
        r.warn("hot_window", "wiki/hot.md が無い")
        return
    text = hot.read_text(encoding="utf-8")
    entries = len(re.findall(r"^## ", text, re.M))
    tokens = max(1, len(text) // 3)
    if entries > HOT_MAX_ENTRIES or tokens > HOT_MAX_TOKENS:
        r.warn("hot_window", f"hot.md が {entries} エントリ / 約 {tokens} トークン(上限 {HOT_MAX_ENTRIES} / {HOT_MAX_TOKENS})",
               "python3 scripts/wiki-catalog.py trim-hot")
    else:
        r.ok("hot_window", f"{entries} エントリ / 約 {tokens} トークン")


def check_log_head(r, ctx):
    log = VAULT_ROOT / "wiki" / "log.md"
    if not log.is_file():
        r.warn("log_head", "wiki/log.md が無い")
        return
    with log.open(encoding="utf-8") as fh:
        first = fh.readline().rstrip("\n")
    if re.match(r"^## \[\d{4}-\d{2}-\d{2}\]", first):
        r.ok("log_head", f"先頭エントリ: {first[:60]}")
    else:
        r.warn("log_head", f"先頭がエントリ見出しでない: {first[:60]!r}(prepend-log 以外で編集された可能性)")


def check_tmp_residue(r, ctx):
    tmps = list((VAULT_ROOT / "wiki").rglob("*.tmp"))
    if tmps:
        r.warn("tmp_residue", f"wiki/ 配下の一時ファイル {len(tmps)} 件(例: {rel(tmps[0])})。中断した書き込みの残骸か並行作業中",
               "並行作業が無いことを確かめてから rm")
    else:
        r.ok("tmp_residue", "一時ファイル無し")


def check_auto_commit_disabled(r, ctx):
    if (META / "auto-commit.disabled").is_file():
        r.ok("auto_commit_disabled", "plugin の自動コミットは無効")
    else:
        r.warn("auto_commit_disabled", ".vault-meta/auto-commit.disabled が無い(ツール呼び出しごとの機械的コミットが走る)",
               "touch .vault-meta/auto-commit.disabled")


def check_ollama(r, ctx):
    host, port = "127.0.0.1", 11434
    try:
        with socket.create_connection((host, port), timeout=0.5):
            r.info("ollama", f"{host}:{port} に到達(rerank 有効)")
    except OSError:
        r.info("ollama", f"{host}:{port} に到達できない(retrieve は BM25 のみ、tiling-check は終了 10)")


def check_git_dirty_wiki(r, ctx):
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "wiki"], cwd=str(VAULT_ROOT),
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return
    n = sum(1 for l in out.splitlines() if l.strip())
    r.info("git_dirty_wiki", f"wiki/ 配下の未コミット変更 {n} 件")


CHECKS = [
    ("retrieve_provisioned", check_retrieve_provisioned),
    ("bm25_freshness", check_bm25_freshness),
    ("stale_chunks", check_stale_chunks),
    ("unchunked_pages", check_unchunked_pages),
    ("derived_caches", check_derived_caches),
    ("locks", check_locks),
    ("address_counter", check_address_counter),
    ("manifest", check_manifest),
    ("ledgers", check_ledgers),
    ("hot_window", check_hot_window),
    ("log_head", check_log_head),
    ("tmp_residue", check_tmp_residue),
    ("auto_commit_disabled", check_auto_commit_disabled),
    ("ollama", check_ollama),
    ("git_dirty_wiki", check_git_dirty_wiki),
]


def render(rows):
    width = max(len(r["check"]) for r in rows)
    out = []
    for r in rows:
        out.append(f"{r['status']:<5} {r['check']:<{width}}  {r['detail']}")
        if r.get("fix"):
            out.append(f"{'':<5} {'':<{width}}  fix: {r['fix']}")
    counts = Counter(r["status"] for r in rows)
    out.append("")
    out.append("summary: " + "、".join(f"{k} {counts[k]}" for k in ("OK", "WARN", "FAIL", "INFO") if counts[k]))
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="wiki レイヤーの機械状態の健全性検査(読み取り専用)。")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only", default=None, help="カンマ区切りの検査名")
    args = parser.parse_args(argv)
    wanted = set(args.only.split(",")) if args.only else None
    unknown = (wanted or set()) - {n for n, _ in CHECKS}
    if unknown:
        print(f"ERR: unknown check: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    ctx = {"pages": list(wiki_pages())}
    report = Report()
    for name, fn in CHECKS:
        if wanted and name not in wanted:
            continue
        try:
            fn(report, ctx)
        except Exception as exc:  # 検査自体の失敗は FAIL として残す(他の検査は続ける)
            report.fail(name, f"検査が例外で止まった: {exc!r}")
    payload = {"vault": str(VAULT_ROOT), "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "pages": len(ctx["pages"]), "rows": report.rows}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(f"wiki-doctor  {payload['vault']}  pages {payload['pages']}\n\n")
        sys.stdout.write(render(report.rows))
    return 1 if any(r["status"] == "FAIL" for r in report.rows) else 0


if __name__ == "__main__":
    sys.exit(main())
