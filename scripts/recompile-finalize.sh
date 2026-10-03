#!/usr/bin/env bash
# recompile-finalize.sh — 再編纂 1 ページ分の確定(台帳の更新 → 索引の更新 → コミット)。
#
# 使い方:
#   bash scripts/recompile-finalize.sh <page> --log FILE [--verdicts FILE [--resolve ID NOTE]...] [--no-commit]
#
#   <page>        `wiki/concepts/X.md` か `X`(ページ名)
#   --log FILE    wiki/log.md の先頭に入れるエントリ(`## [YYYY-MM-DD] recompile | X` から始まる)
#   --verdicts F  claim-audit の判定 JSON(あれば `claim-audit.py record` に渡す)
#   --resolve ID NOTE  record のあと `claim-audit.py resolve ID --note NOTE` を打つ(手当てした命題ごとに繰り返す)
#   --no-commit   台帳と索引だけ更新し、コミットしない(呼び出し側でまとめてコミットする場合)
#
# 順序の理由:
#   台帳(log・claim-audit・キュー)を先に確定し、索引(wiki-retrieve-refresh)は最後に回す。
#   索引は派生キャッシュで、取りこぼしは wiki-doctor の bm25_freshness が拾う。逆順で
#   `set -e` にすると、並行セッションの `--all` 再構築とのロック競合で refresh が落ちたとき、
#   キューの `mark --state done` だけが飛ぶ(2026-09-25 に 3 ページで起きた)。
#
# コミットは対象パスを列挙して限定し(`git add -A` は使わない)、index.lock の競合は再試行する。
# 出力は絞らずに返す。呼び出し側で grep するなら `WARN|FAILED|ERR` を必ず残すこと。
#
# 終了コード:
#   0 — 確定とコミットに成功(refresh の失敗は WARN を出して 0)
#   1 — 台帳の更新かコミットに失敗
#   2 — 使い方の誤り
set -euo pipefail

VAULT_ROOT="${WIKI_VAULT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
COMMIT_RETRIES="${RECOMPILE_COMMIT_RETRIES:-10}"

usage() { sed -n '4,11p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

page="" log_file="" verdicts="" commit=1
resolve_ids=() resolve_notes=()
while [ $# -gt 0 ]; do
  case "$1" in
    --log) log_file="${2:-}"; shift 2 ;;
    --verdicts) verdicts="${2:-}"; shift 2 ;;
    --resolve) [ $# -ge 3 ] || usage; resolve_ids+=("$2"); resolve_notes+=("$3"); shift 3 ;;
    --no-commit) commit=0; shift ;;
    -h|--help) usage ;;
    -*) echo "ERR: unknown option $1" >&2; usage ;;
    *) [ -z "$page" ] || usage; page="$1"; shift ;;
  esac
done
[ -n "$page" ] && [ -n "$log_file" ] || usage
[ ${#resolve_ids[@]} -eq 0 ] || [ -n "$verdicts" ] || { echo "ERR: --resolve needs --verdicts" >&2; exit 2; }
[ -s "$log_file" ] || { echo "ERR: log file missing or empty: $log_file" >&2; exit 2; }

case "$page" in
  wiki/*.md) rel="$page"; title="$(basename "$page" .md)" ;;
  *) title="$page"; rel="wiki/concepts/$page.md" ;;
esac
cd "$VAULT_ROOT"
[ -f "$rel" ] || { echo "ERR: page not found: $rel" >&2; exit 2; }

fail() { echo "FAILED: $title — $1" >&2; exit 1; }

# 1. 台帳(ここで落ちたら止める)
python3 "$SCRIPTS/wiki-catalog.py" prepend-log --text "$(cat "$log_file")" >/dev/null \
  || fail "wiki-catalog.py prepend-log"
report=""
if [ -n "$verdicts" ]; then
  [ -s "$verdicts" ] || fail "verdicts file missing or empty: $verdicts"
  out="$(python3 "$SCRIPTS/claim-audit.py" record --verdicts "$verdicts" --by agent)" \
    || fail "claim-audit.py record"
  report="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("report") or "")')"
  for i in "${!resolve_ids[@]}"; do
    python3 "$SCRIPTS/claim-audit.py" resolve "${resolve_ids[$i]}" --note "${resolve_notes[$i]}" >/dev/null \
      || fail "claim-audit.py resolve ${resolve_ids[$i]}"
  done
fi
python3 "$SCRIPTS/recompile-queue.py" mark "$rel" --state done >/dev/null \
  || fail "recompile-queue.py mark --state done"
echo "recompile-finalize: $title ledgers updated"

# 2. 索引(派生キャッシュ。落ちても台帳は確定済み)
if ! python3 "$SCRIPTS/wiki-retrieve-refresh.py" --pages "$rel" --no-llm >/dev/null; then
  echo "WARN: wiki-retrieve-refresh failed for $rel; ledgers are already recorded." \
       "Re-run: python3 scripts/wiki-retrieve-refresh.py --pages \"$rel\" --no-llm" >&2
fi

[ "$commit" -eq 1 ] || exit 0

# 3. コミット(パス限定。index.lock の競合は再試行)
files=("$rel" wiki/log.md .vault-meta/recompile-queue.json)
[ -z "$verdicts" ] || files+=(.vault-meta/claim-audit.json)
[ -z "$report" ] || files+=("$report")
for _ in $(seq 1 "$COMMIT_RETRIES"); do
  if git add -f -- "${files[@]}" && git commit -q -m "wiki: recompile $title" -- "${files[@]}"; then
    git log -1 --format="recompile-finalize: committed %h %s"
    exit 0
  fi
  sleep 3
done
fail "git commit (retried $COMMIT_RETRIES times)"
