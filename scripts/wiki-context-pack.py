#!/usr/bin/env python3
"""wiki-context-pack.py — 目標(goal)に対する引用付き・予算内・省略明示のコンテキスト束を 1 つ作る。

subagent への引き継ぎや長い作業の再開では、「何を読ませるか」を毎回 retrieve と excerpt を手で
組み合わせて決めている。本スクリプトはその手順を 1 コマンドに畳む:

  1. retrieve.py で候補ページを取る(BM25 + rerank + graph 路。ページ単位に畳む)
  2. 上位から順に wiki-excerpt.py の抜粋を予算(--budget-tokens)に詰める
  3. 予算に入らないページは outline(見出しと箇条書き数だけ、~150 トークン)に落とし、
     それも入らなければ「省略」として題名と address だけを列挙する
  4. 先頭に目標・生成日・予算の消費・読み方(出典の書き方、省略ページの追加取得法)を書く

出力は markdown 1 本(stdout か --out)。読み取り専用で wiki を書き換えない。retrieve.py が
未整備(終了 10)なら --pages で明示したページだけで束を作る。

使い方:
  python3 scripts/wiki-context-pack.py "<goal>" [--budget-tokens 6000] [--top 10]
        [--per-page 1200] [--pages P ...] [--exclude P ...] [--no-retrieve] [--out /tmp/pack.md] [--json]

  --pages     retrieve の結果に加えて必ず入れるページ(先頭に置く)
  --exclude   入れないページ(既に読んだもの、出自ページなど)
  --per-page  1 ページの抜粋上限(既定 1200 トークン)。wiki-excerpt.py の --budget-tokens
  --json      機械可読(含めたページ、outline に落としたページ、省略したページ、消費トークン)

効果の測定は scripts/usage-report.py(束を渡した session と渡さない session の常駐文脈長を比べる)。

終了コード: 0 成功 / 2 使い方の誤り / 3 候補ページが 1 つも無い
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or _SCRIPTS.parent).resolve()

EXIT_OK, EXIT_USAGE, EXIT_EMPTY = 0, 2, 3
OUTLINE_BUDGET = 220  # outline 1 頁の目安(wiki-excerpt --outline はこの程度に収まる)


def log(msg):
    print(msg, file=sys.stderr)


def estimate_tokens(text):
    return max(1, len(text) // 3) if text else 0


def run(cmd, timeout=180):
    try:
        return subprocess.run(cmd, cwd=str(VAULT_ROOT), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"WARN: {cmd[0]} failed: {exc}")
        return None


def retrieve_pages(goal, top):
    """retrieve.py をページ単位に畳んだ [(page_path, score_info)] を順序つきで返す。"""
    proc = run([sys.executable, str(_SCRIPTS / "retrieve.py"), goal, "--top", str(top)])
    if proc is None or proc.returncode != 0:
        if proc is not None:
            log(f"WARN: retrieve.py exit {proc.returncode}; --pages だけで束を作る")
        return [], None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], None
    seen, out = set(), []
    for c in data.get("candidates", []):
        p = c.get("page_path")
        if not p or p in seen:
            continue
        seen.add(p)
        out.append((p, {"channels": c.get("channels") or ["bm25"], "address": c.get("page_address")}))
    return out, data.get("strategy")


def excerpt(page, budget, outline=False):
    cmd = [sys.executable, str(_SCRIPTS / "wiki-excerpt.py"), page, "--quiet",
           "--fm-keys", "title,type,address,entity_tier,domain"]
    cmd += ["--outline"] if outline else ["--budget-tokens", str(budget)]
    proc = run(cmd)
    if proc is None or proc.returncode not in (0,):
        return None
    return proc.stdout


def title_of(page):
    return Path(page).stem


def demote(text):
    """抜粋の見出しを 1 段下げ、ページ自身の h1(束の ## と重複)は落とす。"""
    out = []
    for line in text.split("\n"):
        if line.startswith("# ") and not line.startswith("## "):
            continue
        if line.startswith("##"):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def resolve_moved(page):
    """索引が古く page_path が無いとき、同名ページを wiki/ 配下の別ディレクトリから探す。"""
    stem = Path(page).name
    for sub in ("surveys", "questions", "concepts", "entities", "sources", "meta"):
        cand = VAULT_ROOT / "wiki" / sub / stem
        if cand.is_file():
            return f"wiki/{sub}/{stem}"
    return None


def build(goal, pages, budget, per_page, strategy):
    header_lines = [
        f"# コンテキスト束: {goal}",
        "",
        f"- 生成 {date.today().isoformat()}。検索路 {strategy or 'なし(--pages のみ)'}。予算 {budget} トークン、1 頁上限 {per_page}",
        "- 読み方: 各ページは wiki-excerpt の抜粋(受信箱は末尾側、主題節は命題の太字行)。主張を使うときは "
        "`(Source: [[<ページ名>]])` で引く。抜粋に無いことは推測せず「記載なし」とする",
        "- 省略ページは `python3 scripts/wiki-excerpt.py \"<path>\" --budget-tokens 1800` で追加取得する。"
        "`wiki/index.md` や `_index.md` は読まない",
        "",
    ]
    header = "\n".join(header_lines)
    used = estimate_tokens(header)
    included, outlined, omitted = [], [], []
    body_parts = []
    for page, info in pages:
        full = excerpt(page, per_page)
        if full is None:
            omitted.append((page, info, "読めない"))
            continue
        cost = estimate_tokens(full)
        if used + cost <= budget:
            body_parts.append(f"## {title_of(page)}\n\n{demote(full).strip()}\n")
            used += cost
            included.append((page, info, cost))
            continue
        ol = excerpt(page, per_page, outline=True)
        ol_cost = estimate_tokens(ol) if ol else OUTLINE_BUDGET
        if ol and used + ol_cost <= budget:
            body_parts.append(f"## {title_of(page)}(outline のみ。本文は予算外)\n\n{demote(ol).strip()}\n")
            used += ol_cost
            outlined.append((page, info, ol_cost))
        else:
            omitted.append((page, info, "予算超過"))
    tail = []
    if outlined or omitted:
        tail.append("## 省略(予算に入らなかったページ)")
        tail.append("")
        for page, info, _ in outlined:
            tail.append(f"- outline のみ: [[{title_of(page)}]](`{page}`)")
        for page, info, why in omitted:
            addr = f"、address {info.get('address')}" if info.get("address") else ""
            tail.append(f"- 省略({why}): [[{title_of(page)}]](`{page}`{addr})")
        tail.append("")
    footer = f"- 消費 {used} / {budget} トークン。全文 {len(included)} 頁、outline {len(outlined)} 頁、省略 {len(omitted)} 頁\n"
    text = header + "\n".join(body_parts) + ("\n" + "\n".join(tail) if tail else "") + "\n" + footer
    summary = {
        "goal": goal, "strategy": strategy, "budget": budget, "used": used,
        "included": [{"page": p, "tokens": c, "channels": i.get("channels")} for p, i, c in included],
        "outlined": [{"page": p, "tokens": c} for p, i, c in outlined],
        "omitted": [{"page": p, "reason": w} for p, i, w in omitted],
    }
    return text, summary


def norm_page(p):
    p = p.strip()
    if p.startswith(str(VAULT_ROOT)):
        p = str(Path(p).relative_to(VAULT_ROOT))
    return p if p.endswith(".md") else p + ".md"


def main(argv=None):
    parser = argparse.ArgumentParser(description="引用付き・予算内・省略明示のコンテキスト束。")
    parser.add_argument("goal")
    parser.add_argument("--budget-tokens", type=int, default=6000)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--per-page", type=int, default=1200)
    parser.add_argument("--pages", nargs="*", default=[])
    parser.add_argument("--exclude", nargs="*", default=[])
    parser.add_argument("--no-retrieve", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.budget_tokens < 500 or args.per_page < 100:
        log("ERR: --budget-tokens は 500 以上、--per-page は 100 以上")
        return EXIT_USAGE

    pinned = [(norm_page(p), {"channels": ["pinned"], "address": None}) for p in args.pages]
    exclude = {norm_page(p) for p in args.exclude}
    retrieved, strategy = ([], None) if args.no_retrieve else retrieve_pages(args.goal, args.top)
    seen = set()
    pages = []
    for p, info in pinned + retrieved:
        if p in seen or p in exclude:
            continue
        if not (VAULT_ROOT / p).is_file():
            moved = resolve_moved(p)
            if moved is None:
                log(f"WARN: not found: {p}")
                continue
            log(f"note: {p} は無い。{moved} を使う(索引が古い。wiki-retrieve-refresh.py で更新)")
            p = moved
        seen.add(p)
        pages.append((p, info))
    if not pages:
        log("ERR: 候補ページが無い(retrieve 未整備なら --pages で指定する)")
        return EXIT_EMPTY

    text, summary = build(args.goal, pages, args.budget_tokens, args.per_page, strategy)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        summary["out"] = args.out
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif not args.out:
        sys.stdout.write(text)
    else:
        log(f"wrote {args.out} ({summary['used']} tokens, {len(summary['included'])} full / "
            f"{len(summary['outlined'])} outline / {len(summary['omitted'])} omitted)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
