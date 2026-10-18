#!/usr/bin/env bash
# recompile-finalize.sh — 再編纂 1 ページ分の確定(大域ロックの中で台帳の更新 → コミット、ロックの外で索引の更新)。
#
# 使い方:
#   bash scripts/recompile-finalize.sh <page> --log FILE [--verdicts FILE [--resolve ID NOTE]...] [--no-commit] [--no-index]
#
#   <page>        `wiki/concepts/X.md` か `X`(ページ名)
#   --log FILE    wiki/log.md の先頭に入れるエントリ(`## [YYYY-MM-DD] recompile | X` から始まる)
#   --verdicts F  claim-audit の判定 JSON(あれば `claim-audit.py record` に渡す)
#   --resolve ID NOTE  record のあと `claim-audit.py resolve ID --note NOTE` を打つ(手当てした命題ごとに繰り返す)
#   --no-commit   台帳と索引だけ更新し、コミットしない(呼び出し側でまとめてコミットする場合)
#   --no-index    索引の更新(wiki-retrieve-refresh)を飛ばす。後で打つコマンドを INFO 行で出す
#
# 大域ロック:
#   台帳(wiki/log.md・.vault-meta/claim-audit.json・.vault-meta/recompile-queue.json・当日の
#   wiki/meta/claim-audit-YYYY-MM-DD.md)は全ページ共有で、ファイル単位で stage される。並行する
#   finalize が台帳の更新とコミットを交互に行うと、他ページの台帳行が自分のコミットに混ざり
#   「1 ページ = 1 コミット」が崩れる。そこで台帳の更新から git commit までを、
#   `.vault-meta/locks/recompile-finalize.flock` への flock(1) で直列化する。
#   wiki-lock.sh の lockfile は 60 秒で失効し、commit の再試行(既定 10 回 × 3 秒)を含む区間を
#   守り切れないので使わない。flock はプロセスの終了で解放されるので失効時刻を持たない。
#   待ち時間の上限は RECOMPILE_FINALIZE_LOCK_WAIT_SEC(既定 600 秒)。flock が無ければ何もせず終了 1。
#   このロックが直列化するのは finalize 同士だけである。ingest の prepend-log のように finalize の外で
#   wiki/log.md を書く処理の変更は、従来どおりコミットに混ざり得る。
#
# 順序の理由:
#   台帳(log・claim-audit・キュー)を先に確定してコミットし、索引(wiki-retrieve-refresh)は
#   ロックを放した後に回す。索引の更新は bm25 のロックを最大 180 秒待つので、ロックの中に置くと
#   他ページの確定を待たせる。索引は .vault-meta/ 配下の派生キャッシュでコミット対象ではなく、
#   取りこぼしは wiki-doctor の bm25_freshness が拾う。索引を台帳より前に置いて `set -e` にすると、
#   並行セッションの `--all` 再構築とのロック競合で refresh が落ちたとき、キューの
#   `mark --state done` だけが飛ぶ(2026-09-25 に 3 ページで起きた)。
#
# コミットは対象パスを列挙して限定し(`git add -A` は使わない)、index.lock の競合は再試行する。
# 成功時は自分のコミットのハッシュ(git commit の出力から取る。HEAD は読まない)を表示する。
# --resolve は ID ごとに `resolve: <ID> ok` か `WARN: resolve <ID> failed` を出し、失敗しても確定は続ける
# (台帳の先頭追記を済ませた後で止めると、再実行が log を二重に書くため)。失敗した ID は表示される
# コマンドで打ち直す。
# 出力は絞らずに返す。呼び出し側で grep するなら `WARN|FAILED|ERR` を必ず残すこと。
#
# 終了コード:
#   0 — 確定とコミットに成功(refresh と resolve の失敗は WARN を出して 0)
#   1 — 台帳の更新・ロックの取得・コミットのいずれかに失敗
#   2 — 使い方の誤り
set -euo pipefail

VAULT_ROOT="${WIKI_VAULT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
COMMIT_RETRIES="${RECOMPILE_COMMIT_RETRIES:-10}"
LOCK_WAIT_SEC="${RECOMPILE_FINALIZE_LOCK_WAIT_SEC:-600}"

usage() { sed -n '4,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

page="" log_file="" verdicts="" commit=1 index=1
resolve_ids=() resolve_notes=()
while [ $# -gt 0 ]; do
  case "$1" in
    --log) log_file="${2:-}"; shift 2 ;;
    --verdicts) verdicts="${2:-}"; shift 2 ;;
    --resolve) [ $# -ge 3 ] || usage; resolve_ids+=("$2"); resolve_notes+=("$3"); shift 3 ;;
    --no-commit) commit=0; shift ;;
    --no-index) index=0; shift ;;
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

# macOS の util-linux は keg-only で flock が PATH 外のことが多い(wiki-lock.sh と同じ解決)
if ! command -v flock >/dev/null 2>&1; then
  for _d in /opt/homebrew/opt/util-linux/bin /usr/local/opt/util-linux/bin /opt/homebrew/bin; do
    if [ -x "$_d/flock" ]; then PATH="$_d:$PATH"; break; fi
  done
fi
command -v flock >/dev/null 2>&1 || fail "flock not found (brew install util-linux); ledgers untouched"
mkdir -p .vault-meta/locks
LOCK_FILE=".vault-meta/locks/recompile-finalize.flock"

# 大域ロックの中身: 台帳の更新 → (あれば)パス限定コミット。サブシェルで走るので exit はこの区間だけを抜ける。
locked_section() {
  # 1. 台帳(ここで落ちたら止める)
  python3 "$SCRIPTS/wiki-catalog.py" prepend-log --text "$(cat "$log_file")" >/dev/null \
    || fail "wiki-catalog.py prepend-log"
  local report="" out
  if [ -n "$verdicts" ]; then
    [ -s "$verdicts" ] || fail "verdicts file missing or empty: $verdicts"
    out="$(python3 "$SCRIPTS/claim-audit.py" record --verdicts "$verdicts" --by agent)" \
      || fail "claim-audit.py record"
    report="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("report") or "")')"
    local i ok=0 ng=0 err n=${#resolve_ids[@]}
    for ((i = 0; i < n; i++)); do
      if err="$(python3 "$SCRIPTS/claim-audit.py" resolve "${resolve_ids[$i]}" --note "${resolve_notes[$i]}" 2>&1 >/dev/null)"; then
        echo "recompile-finalize: resolve: ${resolve_ids[$i]} ok"
        ok=$((ok + 1))
      else
        echo "WARN: resolve ${resolve_ids[$i]} failed: ${err:-unknown error}." \
             "Re-run: python3 scripts/claim-audit.py resolve ${resolve_ids[$i]} --note \"…\"" >&2
        ng=$((ng + 1))
      fi
    done
    [ "$n" -eq 0 ] || echo "recompile-finalize: resolve: $ok ok, $ng failed"
  fi
  python3 "$SCRIPTS/recompile-queue.py" mark "$rel" --state done >/dev/null \
    || fail "recompile-queue.py mark --state done"
  echo "recompile-finalize: $title ledgers updated"

  [ "$commit" -eq 1 ] || return 0

  # 2. コミット(パス限定。index.lock の競合は再試行)
  local files=("$rel" wiki/log.md .vault-meta/recompile-queue.json) msg="wiki: recompile $title" res hash
  [ -z "$verdicts" ] || files+=(.vault-meta/claim-audit.json)
  [ -z "$report" ] || files+=("$report")
  for _ in $(seq 1 "$COMMIT_RETRIES"); do
    if git add -f -- "${files[@]}" && res="$(git commit -m "$msg" -- "${files[@]}")"; then
      # 1 行目は `[branch abc1234] msg` か `[branch (root-commit) abc1234] msg`。HEAD は並行コミットで動き得るので読まない
      hash="$(printf '%s\n' "$res" | head -1 | sed -n 's/^\[[^]]* \([0-9a-f]\{7,\}\)\].*$/\1/p')"
      [ -n "$hash" ] || fail "committed, but could not read the commit hash from: $(printf '%s' "$res" | head -1)"
      echo "recompile-finalize: committed $hash $msg"
      return 0
    fi
    sleep 3
  done
  fail "git commit (retried $COMMIT_RETRIES times)"
}

(
  flock -x -w "$LOCK_WAIT_SEC" 9 || fail "global finalize lock not acquired within ${LOCK_WAIT_SEC}s; ledgers untouched"
  locked_section
) 9>"$LOCK_FILE" || exit 1

# 3. 索引(派生キャッシュ。ロックの外。落ちても台帳とコミットは確定済み)
refresh_cmd="python3 scripts/wiki-retrieve-refresh.py --pages \"$rel\" --no-llm"
if [ "$index" -eq 0 ]; then
  echo "recompile-finalize: INFO index refresh skipped (--no-index). Run later: $refresh_cmd"
elif ! python3 "$SCRIPTS/wiki-retrieve-refresh.py" --pages "$rel" --no-llm >/dev/null; then
  echo "WARN: wiki-retrieve-refresh failed for $rel; ledgers are already recorded." \
       "Re-run: $refresh_cmd" >&2
fi
exit 0
