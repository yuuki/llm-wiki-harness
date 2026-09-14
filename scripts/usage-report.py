#!/usr/bin/env python3
"""usage-report.py — セッションの使用量レポート。

Claude Code では `~/.claude/projects/<slug>/*.jsonl` を読み、セッション単位で
API 呼び出し回数・常駐文脈長・キャッシュ収支・ツール呼び出し内訳・推定コストを
集計する。Cursor では `CURSOR_CONVERSATION_ID` と
`~/.cursor/projects/<slug>/agent-transcripts/<id>/<id>.jsonl` を読み、composer
スナップショット(`contextTokensUsed`)があるときはそれに合わせて常駐文脈を按分する。
Codex では `CODEX_THREAD_ID`(無ければ `CODEX_SESSION_ID`)と
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` を読み、`token_usage_record` の
実測トークンを積む。ingest 系スキルの改善効果を前後比較するための常設計測器であり、
標準ライブラリだけで動く。

集計の要点:

- 1 回の API 応答は thinking 行・text 行・tool_use 行に分割記録され、同じ
  `message.id` と同一の usage を持つ。そのため **`message.id` で重複排除**する
  (排除しないと呼び出し回数とトークンが 2 倍前後に膨らむ)。`message.id` を
  欠く行は `uuid` で代替する。
- 常駐文脈長は 1 呼び出しあたりの `input + cache_creation + cache_read`。入力
  トークンの大半はキャッシュ読み戻しなので、消費量はおおむね
  Σ(各呼び出し時点の常駐文脈長)になる。
- 「新規入力」(`new_in`)は `input + cache_creation`、つまりキャッシュから読み
  戻さずに今回課金対象として送った入力の合計。`input` 単体は「非キャッシュ入力」
  であり別物なので、詳細表示は両方を並べる。
- 推定コストは定価(公表単価)による目安であり、実際の請求額ではない。割引・
  バッチ・無償枠を反映しない。
- `message.id` も `uuid` も無い assistant 行は重複判定ができないため 1 行 = 1
  呼び出しとして数え、件数を `unkeyed_rows` に残す。

既知の制限:

- **サブエージェント(Task / Agent)の使用量は親セッションのログに含まれない**。
  本レポートの数値はオーケストレータ側だけの値であり、fan-out した ingest の
  総消費はこれより大きい。`subagents` 欄はサブエージェントの起動回数のみを示す。
- `isSidechain: true` の行と `model` が `<synthetic>` の行は集計から除く(前者は
  サブエージェント側の記録、後者は Claude Code のローカル通知で API 呼び出しでは
  ない)。件数は `sidechain_rows` と `synthetic_rows` に残す。
- `--since` はセッションの開始時刻ではなく**ログファイルの更新時刻**で絞る。
  再開したセッションは開始日が古いまま一覧に残る。
- 単価表はモデル名の部分一致で選ぶ。判定できないモデルは sonnet 単価で計算し、
  表示にモデル系統と `?` を付ける。
- Cursor の transcript には API の usage が無い。呼び出し回数とツール内訳は
  transcript から数え、トークンとコストは文字数概算と composer の最終文脈長から
  按分する。進行中セッションでは transcript に assistant 行がまだ無いことがある。
  そのときは composer の bubble から復元する。`--log-line` は `Cursor概算` と出し、
  Claude Code の実ログと混ぜて前後比較しない。
- Codex のトークンは rollout の `token_usage_record` の実測である。推定コストは
  公表単価の目安であり、Codex Plus の実請求ではない。`--log-line` は
  `Codex定価目安` と出し、Claude Code / Cursor の数値と混ぜて前後比較しない。

使い方:

  python3 scripts/usage-report.py [--self | --latest | --session ID_OR_PATH
                                  | --since YYYY-MM-DD | --all]
                                  [--top N] [--min-calls N] [--json] [--log-line]
                                  [--project-dir DIR] [--claude-home DIR]
                                  [--cursor-transcripts DIR] [--cursor-state-db FILE]
                                  [--codex-sessions DIR] [--codex-home DIR]
                                  [--recache-threshold N] [--pricing-json FILE]

`--top` を指定しなければ `--since` / `--all` は該当セッションを打ち切らずに全件
出す。指定して打ち切ったときは、表示分の合計と母集団全体の合計をフッターに併記
する(表示分だけの合計を全体と読み違えないため)。

`--self` は先に Claude Code の `CLAUDE_CODE_SESSION_ID`(無ければ
`CLAUDE_SESSION_ID`)、次に Codex の `CODEX_THREAD_ID`(無ければ
`CODEX_SESSION_ID`)、どれも空なら Cursor の `CURSOR_CONVERSATION_ID` を見る。
並行 ingest 中の `--latest` は更新時刻が新しい他人のセッションを拾うので、完了
報告には使わない。環境変数が空なら失敗する(`--latest` には落とさない)。
`--log-line` だけ(モード無し)も完了報告扱いとして `--self` と同じにする。人間が
最新を見るときだけ `--latest` を明示する。

`--session` の引数は、絶対パス・`/` を含む・`.jsonl` で終わるのいずれかならパスと
みなし、それ以外はセッション ID の前方一致として扱う(`72dbdbc8` のような ID が
cwd の同名ファイルに引っ張られないため)。パスを渡した場合はプロジェクトディレクトリ
の解決を経ないため、`--project-dir` の外にあるログや別プロジェクトのログも読める。

一覧モード(`--since` / `--all`)では 1 ファイルの解析が失敗しても stderr に警告を
1 行出して当該セッションだけを飛ばし、残りのセッションは出力する。単一セッション
モード(`--self` / `--latest` / `--session`)では例外を握り潰さず traceback を出す。

`--pricing-json` は 100 万トークンあたりの USD を持つ入れ子オブジェクトで既定値を
上書きする。キーはモデル名に部分一致させる文字列。

  {"sonnet": {"input": 3, "cache_write": 3.75, "cache_read": 0.3, "output": 15}}

終了コード:
  0 — 成功
  2 — 引数の誤り(`--since` の日付形式の誤り、`--self` で環境変数が空)
  3 — セッションログが見つからない
"""

import argparse
import datetime as _dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# resolve 前の生パスも保持する。Claude Code はユーザーが渡したパス(symlink 解決前)
# から slug を作るため、`/tmp/x` と `/private/tmp/x` のどちらも候補にする必要がある。
VAULT_ROOT_RAW = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent)
VAULT_ROOT = VAULT_ROOT_RAW.resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

# 100 万トークンあたりの USD(定価)。キーはモデル名への部分一致で使う。
DEFAULT_PRICING = {
    "opus": {"input": 15.0, "cache_write": 18.75, "cache_read": 1.5, "output": 75.0},
    "sonnet": {"input": 3.0, "cache_write": 3.75, "cache_read": 0.30, "output": 15.0},
    "haiku": {"input": 0.8, "cache_write": 1.0, "cache_read": 0.08, "output": 4.0},
}
FALLBACK_CLASS = "sonnet"
PRICE_FIELDS = ("input", "cache_write", "cache_read", "output")

RECACHE_THRESHOLD = 40000
GAP_SECONDS = 300.0

# `--self` が読む環境変数。先に Claude Code、次に Codex(TUI が Cursor の
# 会話 UUID を引き継いでも Codex 側を優先する)、最後に Cursor。
SELF_SESSION_ENVS = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CURSOR_CONVERSATION_ID",
)

CODEX_SESSION_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# 単価表に無いモデル名から表示用の系統だけを拾う。
MODEL_FAMILY_HINTS = (
    "opus",
    "sonnet",
    "haiku",
    "grok",
    "composer",
    "muse",
    "gpt",
    "gemini",
    "kimi",
)

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
SUBAGENT_TOOLS = frozenset({"Task", "Agent"})

# セッションの主題を表さない組み込みコマンド。起動コマンドの推定では読み飛ばす。
CONTROL_COMMANDS = frozenset(
    {
        "/clear",
        "/compact",
        "/context",
        "/cost",
        "/export",
        "/model",
        "/resume",
        "/rewind",
        "/status",
        "/usage",
    }
)

COMMAND_NAME_RE = re.compile(r"<command-name>([^<]*)</command-name>")
COMMAND_ARGS_RE = re.compile(r"<command-args>([^<]*)</command-args>")
USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
CAVEAT_RE = re.compile(r"^\s*<local-command-caveat>")
WHITESPACE_RE = re.compile(r"\s+")
CJK_TOKEN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uff00-\uffef\uac00-\ud7af]"
)

# Claude Code がローカル生成する通知行(セッション上限の告知など)。usage は全て 0 で
# API 呼び出しではないため、呼び出し数から除く。
SYNTHETIC_MODEL = "<synthetic>"

# json.loads を全行に掛けないための前置フィルタ。ログには添付や画像を含む巨大な行が
# 混ざるので、user / assistant 以外を先に落とす。Claude Code は `type`、Cursor の
# transcript は `role`。空白の入り方の揺れを許容し、通ってしまった行は json.loads
# 後に type / role で正しく判定する。
ROW_PREFILTER_RE = re.compile(r'"(?:type|role)"\s*:\s*"(?:user|assistant)"')


def log(msg):
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# 数値の短縮表記
# --------------------------------------------------------------------------


def fmt_mega(value):
    """100 万単位。1M 以上は小数 1 桁、それ未満は 2 桁(0.31M のように出す)。"""
    v = (value or 0) / 1000000.0
    if abs(v) >= 1.0:
        return "%.1fM" % v
    return "%.2fM" % v


def fmt_kilo(value):
    """千単位の整数表記(240k)。"""
    return "%dk" % round((value or 0) / 1000.0)


def fmt_short(value):
    """桁に応じて 192 / 1.5k / 240k / 22.7M を出し分ける。"""
    n = int(value or 0)
    a = abs(n)
    if a >= 1000000:
        return fmt_mega(n)
    if a >= 10000:
        return fmt_kilo(n)
    if a >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


def fmt_usd(value):
    v = value or 0.0
    if abs(v) >= 1.0:
        return "%.1f" % v
    return "%.2f" % v


def fmt_pct(numerator, denominator):
    if not denominator:
        return "0%"
    return "%d%%" % round(100.0 * numerator / denominator)


# --------------------------------------------------------------------------
# セッションログの所在
# --------------------------------------------------------------------------


def project_slug_candidates(*vault_roots):
    """vault の絶対パスから Claude Code のプロジェクトディレクトリ名候補を作る。

    Claude Code は絶対パスの区切りと一部の記号を `-` に潰す。潰す文字集合は版に
    よって揺れるので狭い候補から順に試す。また symlink 解決の前後でパスが変わる
    (macOS の `/tmp` → `/private/tmp` など)ため、渡された各パスについて候補を
    作り、重複を除いて並べる。
    """
    candidates = []
    for vault_root in vault_roots:
        if vault_root is None:
            continue
        raw = str(vault_root)
        for charset in ("/.", "/._"):
            slug = raw
            for ch in charset:
                slug = slug.replace(ch, "-")
            if slug not in candidates:
                candidates.append(slug)
    return candidates


def resolve_project_dir(args, quiet=False):
    if args.project_dir:
        path = Path(args.project_dir).expanduser()
        if not path.is_dir():
            log("ERR: --project-dir が存在しない: %s" % path)
            return None
        return path.resolve()

    home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    projects = home / "projects"
    if not projects.is_dir():
        if not quiet:
            log("ERR: プロジェクトディレクトリが無い: %s" % projects)
        return None
    for slug in project_slug_candidates(VAULT_ROOT_RAW, VAULT_ROOT):
        candidate = projects / slug
        if candidate.is_dir():
            return candidate.resolve()
    if not quiet:
        log(
            "ERR: %s の下に vault (%s) に対応するディレクトリが無い。--project-dir で指定する。"
            % (projects, VAULT_ROOT)
        )
    return None


def resolve_log_dirs(args):
    """Claude / Cursor / Codex のログディレクトリを返す。

    明示した Claude 側パスが実在しないときは Cursor / Codex に落とさず失敗する。
    既定の探索で Claude 側が無いだけなら、Cursor か Codex があれば続行する。
    """
    explicit_claude = bool(args.project_dir or args.claude_home)
    ok, cursor_dir = resolve_cursor_transcripts_dir(args)
    if not ok:
        return None, None, None, EXIT_MISSING
    ok, codex_dir = resolve_codex_sessions_dir(args)
    if not ok:
        return None, None, None, EXIT_MISSING

    project_dir = resolve_project_dir(args, quiet=not explicit_claude)
    if args.project_dir and project_dir is None:
        return None, None, None, EXIT_MISSING
    if explicit_claude and project_dir is None and cursor_dir is None and codex_dir is None:
        return None, None, None, EXIT_MISSING
    if project_dir is None and cursor_dir is None and codex_dir is None:
        resolve_project_dir(args, quiet=False)
        log(
            "ERR: Cursor の agent-transcripts も Codex の sessions も無い。"
            "--cursor-transcripts または --codex-sessions で指定する。"
        )
        return None, None, None, EXIT_MISSING
    return project_dir, cursor_dir, codex_dir, EXIT_OK


def list_session_files(project_dir):
    """更新時刻の新しい順に jsonl を並べる(選別を先に済ませて読む量を抑える)。"""
    files = []
    for path in project_dir.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        files.append((mtime, path))
    files.sort(key=lambda item: item[0], reverse=True)
    return files


def parse_since(text):
    try:
        day = _dt.datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return day.timestamp()


def current_session_id(env=None):
    """`--self` 用。Claude Code、Codex、Cursor の順。"""
    mapping = os.environ if env is None else env
    for key in SELF_SESSION_ENVS:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def cursor_project_slug_candidates(*vault_roots):
    """vault の絶対パスから Cursor のプロジェクトディレクトリ名候補を作る。

    Cursor は先頭の `/` を除いた絶対パスの `/` を `-` に潰す
    (`Users-y-tsubouchi-obsidian-research`)。symlink 解決の前後を両方試す。
    """
    candidates = []
    for vault_root in vault_roots:
        if vault_root is None:
            continue
        raw = str(vault_root).lstrip("/")
        slug = raw.replace("/", "-")
        if slug and slug not in candidates:
            candidates.append(slug)
    return candidates


def default_cursor_home(args):
    if args.cursor_home:
        return Path(args.cursor_home).expanduser()
    return Path.home() / ".cursor"


def default_cursor_state_db(args):
    if args.cursor_state_db:
        return Path(args.cursor_state_db).expanduser()
    env = os.environ.get("CURSOR_STATE_DB")
    if env:
        return Path(env).expanduser()
    mac = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    linux = Path.home() / ".config/Cursor/User/globalStorage/state.vscdb"
    if mac.is_file():
        return mac
    if linux.is_file():
        return linux
    return mac


def resolve_cursor_transcripts_dir(args):
    """Cursor の agent-transcripts ディレクトリを返す。無ければ None。

    `--project-dir` や `--claude-home` だけを渡した呼び出しは Claude 側の
    テスト隔離なので、明示の `--cursor-transcripts` が無い限りここは探さない。
    """
    if args.cursor_transcripts:
        path = Path(args.cursor_transcripts).expanduser()
        if not path.is_dir():
            log("ERR: --cursor-transcripts が存在しない: %s" % path)
            return False, None
        return True, path.resolve()

    if args.project_dir or args.claude_home:
        return True, None

    env = os.environ.get("AGENT_TRANSCRIPTS")
    if env:
        path = Path(env).expanduser()
        if path.is_dir():
            return True, path.resolve()

    home = default_cursor_home(args)
    projects = home / "projects"
    if not projects.is_dir():
        return True, None
    for slug in cursor_project_slug_candidates(VAULT_ROOT_RAW, VAULT_ROOT):
        candidate = projects / slug / "agent-transcripts"
        if candidate.is_dir():
            return True, candidate.resolve()
    return True, None


def default_codex_home(args):
    if args.codex_home:
        return Path(args.codex_home).expanduser()
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def resolve_codex_sessions_dir(args):
    """Codex の sessions ディレクトリを返す。無ければ None。

    `--project-dir` や `--claude-home` だけを渡した呼び出しは Claude 側の
    テスト隔離なので、明示の `--codex-sessions` / `--codex-home` が無い限り
    ここは探さない。
    """
    if args.codex_sessions:
        path = Path(args.codex_sessions).expanduser()
        if not path.is_dir():
            log("ERR: --codex-sessions が存在しない: %s" % path)
            return False, None
        return True, path.resolve()

    if args.codex_home:
        sessions = default_codex_home(args) / "sessions"
        if not sessions.is_dir():
            log("ERR: --codex-home の sessions が無い: %s" % sessions)
            return False, None
        return True, sessions.resolve()

    if args.project_dir or args.claude_home:
        return True, None

    home = default_codex_home(args)
    sessions = home / "sessions"
    if sessions.is_dir():
        return True, sessions.resolve()
    return True, None


def list_codex_session_files(sessions_dir):
    """`rollout-*.jsonl` を更新時刻の新しい順で返す。日付シャードと直置きの両方。"""
    files = []
    if sessions_dir is None:
        return files
    seen = set()
    candidates = list(sessions_dir.glob("rollout-*.jsonl"))
    candidates.extend(sessions_dir.glob("*/*/*/rollout-*.jsonl"))
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        files.append((mtime, path))
    files.sort(key=lambda item: item[0], reverse=True)
    return files


def codex_session_id_from_path(path):
    """`rollout-<timestamp>-<session-id>.jsonl` からセッション ID を抜く。"""
    name = path.stem
    if not name.startswith("rollout-"):
        return None
    matches = CODEX_SESSION_UUID_RE.findall(name)
    if not matches:
        return None
    return matches[0]


def looks_like_codex_log(path):
    if path.name.startswith("rollout-") and path.suffix == ".jsonl":
        return True
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return False
    with handle:
        for index, line in enumerate(handle):
            if index > 8:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("type") in (
                "session_meta",
                "token_usage_record",
            ):
                return True
    return False


def peek_codex_session_meta(path):
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return {}
    with handle:
        for index, line in enumerate(handle):
            if index > 20:
                break
            if "session_meta" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("type") == "session_meta":
                payload = row.get("payload")
                return payload if isinstance(payload, dict) else {}
    return {}


def codex_cwd_matches_vault(path):
    cwd = peek_codex_session_meta(path).get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        cwd_path = Path(cwd).expanduser().resolve()
    except OSError:
        cwd_path = Path(cwd).expanduser()
    for vault in (VAULT_ROOT, VAULT_ROOT_RAW):
        if vault is None:
            continue
        try:
            vault_path = Path(vault).resolve()
        except OSError:
            vault_path = Path(vault)
        if cwd_path == vault_path:
            return True
        try:
            cwd_path.relative_to(vault_path)
            return True
        except ValueError:
            continue
    return False


def session_match_keys(path):
    keys = [path.stem]
    sid = codex_session_id_from_path(path)
    if sid and sid not in keys:
        keys.append(sid)
    return keys


def file_matches_session(path, query):
    if not query:
        return False
    return any(key.startswith(query) for key in session_match_keys(path))


def list_cursor_session_files(transcripts_dir):
    """`<id>/<id>.jsonl` を更新時刻の新しい順で返す。"""
    files = []
    if transcripts_dir is None:
        return files
    for path in transcripts_dir.glob("*/*.jsonl"):
        if path.stem != path.parent.name:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        files.append((mtime, path))
    files.sort(key=lambda item: item[0], reverse=True)
    return files


def looks_like_cursor_log(path):
    """パスまたは中身から Cursor の transcript かを判定する。"""
    if path.parent.name == "agent-transcripts":
        return True
    if path.parent.name == path.stem:
        return True
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return False
    with handle:
        for line in handle:
            if not ROW_PREFILTER_RE.search(line):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("type") in ("user", "assistant"):
                return False
            if row.get("role") in ("user", "assistant"):
                return True
    return False


def looks_like_a_path(value):
    """`--session` の引数をパスとして扱うかどうか。

    `72dbdbc8` のような区切りを持たない ID は、たまたま同名ファイルが cwd に
    あってもパスと解釈しない(ID 前方一致を優先する)。パスと見なすのは絶対
    パス、`/` を含むもの、`.jsonl` で終わるものに限る。
    """
    expanded = os.path.expanduser(value)
    if os.path.isabs(expanded):
        return True
    if "/" in expanded or os.sep in expanded:
        return True
    return expanded.endswith(".jsonl")


def session_file_override(args):
    """`--session` に実在するファイルパスが渡されていればそれを返す。

    この経路はプロジェクトディレクトリの解決を経ないので、`--project-dir` の外に
    あるログや別プロジェクトのログもそのまま読める(仕様上許容している)。
    """
    if not args.session or not looks_like_a_path(args.session):
        return None
    target = Path(args.session).expanduser()
    if target.is_file():
        return target.resolve()
    return None


def select_files(args, project_dir, cursor_dir, since_threshold, codex_dir=None):
    """モードに応じて対象ファイルと「一覧モードか」を返す。"""
    files = []
    if project_dir is not None:
        files.extend(list_session_files(project_dir))
    if cursor_dir is not None:
        files.extend(list_cursor_session_files(cursor_dir))
    if codex_dir is not None:
        files.extend(list_codex_session_files(codex_dir))
    files.sort(key=lambda item: item[0], reverse=True)
    if args.session:
        matches = [p for _, p in files if file_matches_session(p, args.session)]
        if not matches:
            log("ERR: セッションが見つからない: %s" % args.session)
            return None, False
        if len(matches) > 1:
            ids = [codex_session_id_from_path(path) or path.stem for path in matches]
            if len(set(ids)) == 1:
                mtimes = {path: mtime for mtime, path in files}
                matches = [max(matches, key=lambda path: mtimes.get(path, 0))]
            else:
                log(
                    "ERR: 前方一致が %d 件ある。より長い ID を渡す: %s"
                    % (len(matches), ", ".join(path.stem[:12] for path in matches[:5]))
                )
                return None, False
        return matches, False
    # 一覧と --latest は、他リポジトリの Codex スレッドを混ぜない。
    # `--codex-sessions` を明示したときはテスト用のディレクトリなので絞らない。
    if not args.codex_sessions:
        files = [
            item
            for item in files
            if not looks_like_codex_log(item[1]) or codex_cwd_matches_vault(item[1])
        ]
    if args.since:
        return [p for mtime, p in files if mtime >= since_threshold], True
    if args.all:
        return [p for _, p in files], True
    if not files:
        log("ERR: セッションログが無い: %s" % (project_dir or cursor_dir or codex_dir))
        return None, False
    return [files[0][1]], False


# --------------------------------------------------------------------------
# 1 セッションの解析
# --------------------------------------------------------------------------


def parse_timestamp(text):
    """ISO 8601 の時刻を tz 付き datetime にする。読めなければ None。

    ログには末尾 `Z` の付いた時刻と付かない時刻が混ざりうる。tz を欠く値には
    UTC を補って必ず aware に揃える。naive と aware が混ざったまま減算すると
    `TypeError` になり、一覧モード全体が落ちるためだ。
    """
    if not isinstance(text, str) or not text:
        return None
    value = text[:-1] + "+00:00" if text.endswith("Z") else text
    moment = None
    try:
        moment = _dt.datetime.fromisoformat(value)
    except ValueError:
        for pattern in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                moment = _dt.datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=_dt.timezone.utc)
    return moment


def local_stamp(moment):
    """ログの UTC 時刻を実行環境のローカル時刻の短い表記に直す。"""
    if moment is None:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def read_category(tool_input):
    """Read の対象パスを細分カテゴリに割り当てる。"""
    path = ""
    if isinstance(tool_input, dict):
        path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path") or ""
    if not isinstance(path, str):
        path = ""
    lowered = path.lower()
    ext = os.path.splitext(lowered)[1]
    if ext in IMAGE_EXTS:
        return "image"
    if "/.raw/" in lowered or lowered.startswith(".raw/"):
        return "raw-source"
    if "wiki/meta/" in lowered:
        return "wiki-meta"
    if "/skills/" in lowered or lowered.startswith("skills/"):
        return "skill"
    if lowered.endswith(".md") and ("/wiki/" in lowered or lowered.startswith("wiki/")):
        return "wiki-page"
    return "other"


def tool_key(name, tool_input):
    if name == "Read":
        return "Read:%s" % read_category(tool_input)
    return name or "unknown"


def content_blocks(message):
    content = message.get("content")
    if isinstance(content, list):
        return content
    return []


def message_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def measure_tool_result(block):
    """tool_result の文字数と画像枚数を数える。"""
    chars = 0
    images = 0
    content = block.get("content")
    if isinstance(content, str):
        chars += len(content)
    elif isinstance(content, list):
        for entry in content:
            if not isinstance(entry, dict):
                chars += len(str(entry))
                continue
            kind = entry.get("type")
            if kind == "text":
                text = entry.get("text")
                if isinstance(text, str):
                    chars += len(text)
            elif kind == "image":
                images += 1
            else:
                chars += len(json.dumps(entry, ensure_ascii=False))
    elif content is not None:
        chars += len(str(content))
    return chars, images


def row_kind(row):
    """Claude Code は `type`、Cursor transcript は `role`。"""
    kind = row.get("type")
    if kind in ("user", "assistant"):
        return kind
    kind = row.get("role")
    if kind in ("user", "assistant"):
        return kind
    return None


def iter_rows(path):
    """壊れた行・空行を読み飛ばしつつ user / assistant 行だけを返す。"""
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        log("WARN: 読めないログを飛ばす: %s (%s)" % (path, exc))
        return
    with handle:
        for line in handle:
            if not ROW_PREFILTER_RE.search(line):
                continue
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            kind = row_kind(row)
            if kind is None:
                continue
            if row.get("type") != kind:
                row = dict(row)
                row["type"] = kind
            yield row


def pick_command(command_names, first_text):
    """セッションの起動コマンドを推す。

    `<command-name>` があればその値を使うが、`/clear` のような組み込みの制御
    コマンドは主題を表さないので読み飛ばす。制御コマンドしか無い場合はその先頭、
    `<command-name>` が無い場合は最初の非 caveat な user テキストの先頭に落とす。
    """
    candidates = [pair for pair in command_names if pair[0] not in CONTROL_COMMANDS]
    if not candidates:
        candidates = command_names
    if candidates:
        name, argtext = candidates[0]
        text = (name + " " + argtext) if argtext else name
    else:
        text = first_text or ""
    return WHITESPACE_RE.sub(" ", text).strip()[:100]


def estimate_tokens(text):
    """tiktoken 無しの文字数概算。CJK は 1 字 1 トークン、他は 4 文字で 1。"""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    cjk = 0
    other = 0
    for char in text:
        if CJK_TOKEN_RE.match(char):
            cjk += 1
        elif not char.isspace():
            other += 1
    return cjk + (other + 3) // 4


def pick_cursor_command(first_text):
    """Cursor の最初の user 本文から起動コマンド相当を抜く。"""
    if not first_text:
        return ""
    match = USER_QUERY_RE.search(first_text)
    if match:
        return WHITESPACE_RE.sub(" ", match.group(1)).strip()[:100]
    return pick_command([], first_text)


def assistant_turn_tokens(message):
    """1 つの assistant 行から出力トークンと thinking トークンを概算する。"""
    thinking = 0
    output = 0
    for block in content_blocks(message):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking":
            piece = block.get("thinking") or block.get("text") or ""
            thinking += estimate_tokens(piece)
            output += estimate_tokens(piece)
        elif kind == "text":
            output += estimate_tokens(block.get("text") or "")
        elif kind == "tool_use":
            output += estimate_tokens(json.dumps(block, ensure_ascii=False))
        else:
            output += estimate_tokens(json.dumps(block, ensure_ascii=False))
    if output == 0:
        output = estimate_tokens(message_text(message))
    return output, thinking


CURSOR_TOOL_ALIASES = {
    "read_file_v2": "Read",
    "read_file": "Read",
    "run_terminal_command_v2": "Shell",
    "run_terminal_command": "Shell",
    "edit_file_v2": "StrReplace",
    "edit_file": "StrReplace",
    "write_file": "Write",
    "glob_file_search": "Glob",
    "ripgrep_raw_search": "Grep",
    "web_fetch": "WebFetch",
    "todo_write": "TodoWrite",
    "await": "AwaitShell",
}


def decode_cursor_value(raw):
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    if isinstance(raw, dict):
        return raw
    return None


def load_cursor_kv(db_path, key):
    values = load_cursor_kv_many(db_path, [key])
    return values.get(key)


def load_cursor_kv_many(db_path, keys):
    result = {}
    if not db_path or not keys:
        return result
    path = Path(db_path).expanduser()
    if not path.is_file():
        return result
    unique = []
    seen = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
        try:
            for offset in range(0, len(unique), 200):
                chunk = unique[offset : offset + 200]
                placeholder = ",".join("?" * len(chunk))
                rows = con.execute(
                    "SELECT key, value FROM cursorDiskKV WHERE key IN (%s)" % placeholder,
                    chunk,
                ).fetchall()
                for key, raw in rows:
                    result[key] = decode_cursor_value(raw)
        finally:
            con.close()
    except Exception:
        return result
    return result


def parse_cursor_tool_input(raw_args, display_path=None):
    payload = {}
    if isinstance(raw_args, dict):
        payload = raw_args
    elif isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    path = (
        payload.get("path")
        or payload.get("file_path")
        or payload.get("targetDirectory")
        or display_path
    )
    if isinstance(path, str) and path and "file_path" not in payload:
        payload = dict(payload)
        payload["file_path"] = path
    return payload


def cursor_rows_from_bubbles(session_id, db_path, composer_data=None):
    """進行中セッションでは transcript に assistant がまだ無いので bubble から復元する。"""
    data = composer_data if isinstance(composer_data, dict) else load_cursor_kv(
        db_path, "composerData:%s" % session_id
    )
    if not isinstance(data, dict):
        return []
    headers = data.get("fullConversationHeadersOnly")
    if not isinstance(headers, list) or not headers:
        return []

    bubble_keys = []
    for header in headers:
        if not isinstance(header, dict):
            continue
        bubble_id = header.get("bubbleId")
        if isinstance(bubble_id, str) and bubble_id:
            bubble_keys.append("bubbleId:%s:%s" % (session_id, bubble_id))
    bubbles = load_cursor_kv_many(db_path, bubble_keys)

    rows = []
    pending = None

    def flush():
        nonlocal pending
        if pending is None:
            return
        content = []
        if pending["thinking"]:
            content.append({"type": "thinking", "thinking": pending["thinking"]})
        if pending["text"]:
            content.append({"type": "text", "text": pending["text"]})
        content.extend(pending["tools"])
        if content:
            rows.append({"role": "assistant", "message": {"content": content}})
        pending = None

    def ensure_pending():
        nonlocal pending
        if pending is None:
            pending = {"text": "", "thinking": "", "tools": []}
        return pending

    for header in headers:
        if not isinstance(header, dict):
            continue
        bubble_id = header.get("bubbleId")
        bubble = None
        if isinstance(bubble_id, str) and bubble_id:
            bubble = bubbles.get("bubbleId:%s:%s" % (session_id, bubble_id))
        grouping = header.get("grouping") if isinstance(header.get("grouping"), dict) else {}
        header_type = header.get("type")
        if header_type == 1:
            flush()
            text = ""
            if isinstance(bubble, dict):
                text = bubble.get("text") or ""
            if not text:
                text = grouping.get("textPreview") or ""
            if text:
                rows.append(
                    {
                        "role": "user",
                        "message": {"content": [{"type": "text", "text": text}]},
                    }
                )
            continue
        if header_type != 2:
            continue

        tool_data = (bubble or {}).get("toolFormerData") if isinstance(bubble, dict) else {}
        if not isinstance(tool_data, dict):
            tool_data = {}
        tool_name = tool_data.get("name")
        has_tool = bool(tool_name) or bool(grouping.get("toolCallCase") or grouping.get("isToolGroupable"))
        text = ""
        thinking = ""
        if isinstance(bubble, dict):
            text = bubble.get("text") or ""
            blocks = bubble.get("allThinkingBlocks")
            if isinstance(blocks, list):
                pieces = []
                for block in blocks:
                    if isinstance(block, dict):
                        pieces.append(str(block.get("text") or block.get("thinking") or ""))
                    elif isinstance(block, str):
                        pieces.append(block)
                thinking = "\n".join(piece for piece in pieces if piece)
        if not text:
            text = grouping.get("textPreview") or ""
        if grouping.get("hasThinking") and not thinking:
            thinking = " "

        starts_call = bool(text.strip() or thinking.strip() or grouping.get("hasText") or grouping.get("hasThinking"))
        if starts_call and not has_tool:
            flush()
            slot = ensure_pending()
            slot["text"] += text
            slot["thinking"] += thinking
            continue
        if has_tool:
            if starts_call:
                flush()
            slot = ensure_pending()
            if text:
                slot["text"] += text
            if thinking:
                slot["thinking"] += thinking
            alias = CURSOR_TOOL_ALIASES.get(tool_name, tool_name or grouping.get("toolCallCase") or "unknown")
            tool_input = parse_cursor_tool_input(
                tool_data.get("rawArgs") or tool_data.get("params"),
                grouping.get("toolDisplayPath"),
            )
            slot["tools"].append(
                {
                    "type": "tool_use",
                    "id": tool_data.get("toolCallId") or bubble_id or "tool",
                    "name": alias,
                    "input": tool_input,
                }
            )
            continue
        if starts_call:
            flush()
            slot = ensure_pending()
            slot["text"] += text
            slot["thinking"] += thinking
    flush()
    return rows


def load_cursor_composer_meta(session_id, db_path, composer_data=None):
    """state.vscdb の composerData から最終文脈長とモデル名を読む。失敗したら None。"""
    data = composer_data if isinstance(composer_data, dict) else load_cursor_kv(
        db_path, "composerData:%s" % session_id
    )
    if not isinstance(data, dict):
        return None

    model = None
    config = data.get("modelConfig")
    if isinstance(config, dict):
        name = config.get("modelName")
        if isinstance(name, str) and name.strip():
            model = name.strip()

    breakdown = data.get("promptTokenBreakdown")
    categories = []
    total_used = None
    if isinstance(breakdown, dict):
        if isinstance(breakdown.get("totalUsedTokens"), int):
            total_used = breakdown["totalUsedTokens"]
        raw_cats = breakdown.get("categories")
        if isinstance(raw_cats, list):
            categories = raw_cats

    conversation = 0
    overhead = 0
    for category in categories:
        if not isinstance(category, dict):
            continue
        tokens = category.get("estimatedTokens")
        if not isinstance(tokens, int):
            continue
        ident = category.get("id")
        if ident in ("conversation", "summarized_conversation"):
            conversation += tokens
        else:
            overhead += tokens

    context_used = data.get("contextTokensUsed")
    if not isinstance(context_used, int):
        context_used = total_used
    if isinstance(total_used, int) and conversation:
        overhead = max(0, total_used - conversation)
    elif isinstance(context_used, int) and conversation:
        overhead = max(0, context_used - conversation)

    started = data.get("createdAt")
    ended = data.get("lastUpdatedAt")
    return {
        "model": model,
        "context_tokens_used": context_used if isinstance(context_used, int) else None,
        "conversation_tokens": conversation or None,
        "overhead_tokens": overhead,
        "started_ms": started if isinstance(started, int) else None,
        "ended_ms": ended if isinstance(ended, int) else None,
    }


def cursor_timestamp(start_ms, end_ms, index, total):
    if not isinstance(start_ms, int) or total <= 0:
        return None
    if not isinstance(end_ms, int) or end_ms <= start_ms:
        end_ms = start_ms
    if total == 1:
        millis = start_ms
    else:
        millis = start_ms + int((end_ms - start_ms) * index / (total - 1))
    return _dt.datetime.fromtimestamp(millis / 1000.0, tz=_dt.timezone.utc).isoformat()


def synthesize_cursor_rows(raw_rows, cursor_meta=None):
    """usage の無い Cursor transcript を Claude Code 形の行に変換する。"""
    meta = cursor_meta or {}
    model = meta.get("model")
    first_user = None
    turns = []
    running = 0
    raw_convs = []

    for row in raw_rows:
        kind = row_kind(row) or row.get("type")
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        if kind == "user":
            text = message_text(message)
            if first_user is None and text and not CAVEAT_RE.match(text):
                first_user = text
            running += estimate_tokens(text)
            continue
        if kind != "assistant":
            continue
        output, thinking = assistant_turn_tokens(message)
        running += output
        raw_convs.append(running)
        turns.append({"message": message, "output": output, "thinking": thinking})

    if not turns:
        rows = []
        if first_user is not None:
            rows.append(
                {
                    "type": "user",
                    "message": {"role": "user", "content": first_user},
                }
            )
        return rows, True

    convs = list(raw_convs)
    official_conv = meta.get("conversation_tokens")
    if official_conv and convs[-1] > 0:
        scale = float(official_conv) / float(convs[-1])
        convs = [int(round(value * scale)) for value in convs]
        convs[-1] = official_conv

    overhead = meta.get("overhead_tokens") or 0
    ctxs = [overhead + value for value in convs]
    official_ctx = meta.get("context_tokens_used")
    if official_ctx and ctxs[-1] > 0:
        scale = float(official_ctx) / float(ctxs[-1])
        ctxs = [int(round(value * scale)) for value in ctxs]
        ctxs[-1] = official_ctx

    rows = []
    if first_user is not None:
        rows.append(
            {
                "type": "user",
                "message": {"role": "user", "content": first_user},
            }
        )

    previous = 0
    total = len(turns)
    for index, turn in enumerate(turns):
        ctx = ctxs[index]
        create = max(0, ctx - previous)
        read = previous if previous <= ctx else ctx
        previous = ctx
        usage = {
            "input_tokens": 0,
            "cache_creation_input_tokens": create,
            "cache_read_input_tokens": read,
            "output_tokens": turn["output"],
            "output_tokens_details": {"thinking_tokens": turn["thinking"]},
        }
        rows.append(
            {
                "type": "assistant",
                "uuid": "cursor-%d" % index,
                "timestamp": cursor_timestamp(
                    meta.get("started_ms"), meta.get("ended_ms"), index, total
                ),
                "message": {
                    "role": "assistant",
                    "id": "cursor-%d" % index,
                    "model": model,
                    "content": turn["message"].get("content"),
                    "usage": usage,
                },
            }
        )
    return rows, True


CODEX_TOOL_ALIASES = {
    "exec": "Shell",
    "wait": "AwaitShell",
    "apply_patch": "StrReplace",
    "read_file": "Read",
    "grep_files": "Grep",
    "list_dir": "Glob",
    "update_plan": "TodoWrite",
    "spawn_agent": "Task",
    "spawn_agents": "Task",
}

CODEX_BOOTSTRAP_PREFIXES = (
    "# AGENTS.md",
    "<INSTRUCTIONS>",
    "<environment_context>",
    "<skill>",
)


def is_codex_bootstrap_text(text):
    if not text:
        return True
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in CODEX_BOOTSTRAP_PREFIXES)


def pick_codex_command(first_text):
    """Codex の最初の実ユーザ発話から起動コマンド相当を抜く。"""
    if not first_text:
        return ""
    text = first_text.strip()
    if text.startswith("$"):
        text = text[1:].lstrip()
    return WHITESPACE_RE.sub(" ", text).strip()[:100]


def codex_message_text(payload):
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in ("input_text", "output_text", "text"):
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def codex_output_text(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(raw, dict):
        text = raw.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(raw, ensure_ascii=False)
    return str(raw)


def parse_codex_tool_input(payload):
    raw = payload.get("input")
    if raw is None:
        raw = payload.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        return {"command": raw[:500]}
    return {}


def map_codex_usage(usage):
    """Codex の usage を Claude Code 形に直す。

    Codex の `input_tokens` はキャッシュ込みの入力合計。Claude Code は
    非キャッシュ入力 / cache_create / cache_read を分けて持つ。
    """
    if not isinstance(usage, dict):
        usage = {}
    inp = usage["input_tokens"] if isinstance(usage.get("input_tokens"), int) else 0
    cached = (
        usage["cached_input_tokens"]
        if isinstance(usage.get("cached_input_tokens"), int)
        else 0
    )
    create = (
        usage["cache_write_input_tokens"]
        if isinstance(usage.get("cache_write_input_tokens"), int)
        else 0
    )
    out = usage["output_tokens"] if isinstance(usage.get("output_tokens"), int) else 0
    think = (
        usage["reasoning_output_tokens"]
        if isinstance(usage.get("reasoning_output_tokens"), int)
        else 0
    )
    cached = min(cached, inp)
    create = min(create, max(0, inp - cached))
    return {
        "input_tokens": max(0, inp - cached - create),
        "cache_creation_input_tokens": create,
        "cache_read_input_tokens": cached,
        "output_tokens": out,
        "output_tokens_details": {"thinking_tokens": think},
    }


def iter_codex_events(path):
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        log("WARN: 読めないログを飛ばす: %s (%s)" % (path, exc))
        return
    with handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("type"):
                yield row


def synthesize_codex_rows(path):
    """Codex の rollout を Claude Code 形の user / assistant 行に変換する。"""
    model = None
    session_id = None
    first_user = None
    pending_tools = []
    pending_results = []
    seen_response = set()
    rows = []

    def flush_results():
        if not pending_results:
            return
        blocks = []
        for use_id, text in pending_results:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use_id,
                    "content": text,
                }
            )
        pending_results.clear()
        rows.append(
            {
                "type": "user",
                "message": {"role": "user", "content": blocks},
            }
        )

    def attach_tools(message):
        content = message.get("content")
        if not isinstance(content, list):
            content = []
            message["content"] = content
        content.extend(pending_tools)
        pending_tools.clear()

    for row in iter_codex_events(path):
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        rtype = row.get("type")

        if rtype == "session_meta":
            sid = payload.get("session_id") or payload.get("id")
            if isinstance(sid, str) and sid:
                session_id = sid
            continue

        if rtype == "world_state":
            state = payload.get("state")
            if isinstance(state, dict):
                mode = state.get("collaboration_mode")
                if isinstance(mode, dict) and isinstance(mode.get("model"), str):
                    model = mode["model"]
            continue

        if rtype == "response_item":
            item = payload.get("type")
            if item == "message" and payload.get("role") == "user":
                text = codex_message_text(payload)
                if text and first_user is None and not is_codex_bootstrap_text(text):
                    first_user = text
            elif item in ("custom_tool_call", "function_call"):
                name = payload.get("name")
                alias = CODEX_TOOL_ALIASES.get(name, name or "unknown")
                use_id = payload.get("call_id") or payload.get("id") or "tool"
                pending_tools.append(
                    {
                        "type": "tool_use",
                        "id": use_id,
                        "name": alias,
                        "input": parse_codex_tool_input(payload),
                    }
                )
            elif item in ("custom_tool_call_output", "function_call_output"):
                use_id = payload.get("call_id") or payload.get("id")
                pending_results.append(
                    (use_id, codex_output_text(payload.get("output") or payload.get("content")))
                )
            continue

        if rtype != "token_usage_record":
            continue
        response_id = payload.get("response_id")
        if isinstance(response_id, str) and response_id:
            if response_id in seen_response:
                continue
            seen_response.add(response_id)
        message = {
            "role": "assistant",
            "id": response_id or ("codex-%d" % len(rows)),
            "model": model,
            "content": list(pending_tools),
            "usage": map_codex_usage(payload.get("usage")),
        }
        pending_tools.clear()
        rows.append(
            {
                "type": "assistant",
                "uuid": message["id"],
                "timestamp": row.get("timestamp"),
                "message": message,
            }
        )
        flush_results()

    if pending_tools:
        last_assistant = None
        for row in reversed(rows):
            if row.get("type") == "assistant":
                last_assistant = row
                break
        if last_assistant is not None:
            attach_tools(last_assistant["message"])
        else:
            rows.append(
                {
                    "type": "assistant",
                    "uuid": "codex-tools",
                    "message": {
                        "role": "assistant",
                        "id": "codex-tools",
                        "model": model,
                        "content": list(pending_tools),
                        "usage": map_codex_usage({}),
                    },
                }
            )
            pending_tools.clear()
    flush_results()

    if first_user is not None:
        rows.insert(
            0,
            {
                "type": "user",
                "message": {"role": "user", "content": first_user},
            },
        )
    if session_id is None:
        session_id = codex_session_id_from_path(path)
    return rows, False, session_id


def analyze_session(path, recache_threshold=RECACHE_THRESHOLD, cursor_meta=None, cursor_state_db=None):
    """1 つの jsonl を読み、セッション単位の指標を辞書で返す。"""
    calls = []  # 重複排除後の呼び出し (usage, timestamp, model)
    seen_ids = set()
    models = []
    tool_calls = {}
    tool_results = {}
    tool_owner = {}  # tool_use_id -> 集計キー
    subagents = 0
    result_images = 0
    sidechain_rows = 0
    synthetic_rows = 0
    unkeyed_rows = 0
    command_names = []
    first_text = None
    usage_estimated = False
    usage_basis = "claude-log"
    session_id = path.stem

    if looks_like_codex_log(path):
        raw_rows, usage_estimated, codex_sid = synthesize_codex_rows(path)
        usage_basis = "codex-log"
        if codex_sid:
            session_id = codex_sid
    else:
        raw_rows = list(iter_rows(path))
        if cursor_meta is not None or looks_like_cursor_log(path):
            has_assistant = any(row_kind(row) == "assistant" for row in raw_rows)
            if not has_assistant:
                bubble_rows = cursor_rows_from_bubbles(path.stem, cursor_state_db)
                if bubble_rows:
                    raw_rows = bubble_rows
            raw_rows, usage_estimated = synthesize_cursor_rows(raw_rows, cursor_meta)
            usage_basis = "cursor-estimate"

    for row in raw_rows:
        if row.get("isSidechain"):
            sidechain_rows += 1
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        kind = row.get("type")

        if kind == "assistant":
            if message.get("model") == SYNTHETIC_MODEL:
                synthetic_rows += 1
                continue
            key = message.get("id") or row.get("uuid")
            if key is None:
                # 識別子が無い行は重複判定ができない。落とすと呼び出しを取りこぼす
                # ので 1 行 = 1 呼び出しとして数え、件数だけ別に残す。
                unkeyed_rows += 1
                is_new_call = True
            else:
                is_new_call = key not in seen_ids
                seen_ids.add(key)
            # usage は分割された行すべてに同じ値が入るので初出の行だけ積む。
            # tool_use ブロックは行ごとに異なるため、重複行でも必ず走査する。
            if is_new_call:
                usage = message.get("usage")
                model = message.get("model")
                calls.append(
                    (
                        usage if isinstance(usage, dict) else {},
                        parse_timestamp(row.get("timestamp")),
                        model if isinstance(model, str) else None,
                    )
                )
                if isinstance(model, str) and model not in models:
                    models.append(model)
            for block in content_blocks(message):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                agg = tool_key(name, block.get("input"))
                tool_calls[agg] = tool_calls.get(agg, 0) + 1
                if name in SUBAGENT_TOOLS:
                    subagents += 1
                use_id = block.get("id")
                if isinstance(use_id, str):
                    tool_owner[use_id] = agg
            continue

        # kind == "user"
        if not row.get("isMeta"):
            text = message_text(message)
            if text and not CAVEAT_RE.match(text):
                if first_text is None:
                    first_text = text
                names = [m.group(1).strip() for m in COMMAND_NAME_RE.finditer(text)]
                if names:
                    args_match = COMMAND_ARGS_RE.search(text)
                    argtext = args_match.group(1).strip() if args_match else ""
                    # 引数は 1 テキストに 1 つなので先頭のコマンドにだけ添える。
                    command_names.extend(
                        (name, argtext if position == 0 else "")
                        for position, name in enumerate(names)
                    )
        for block in content_blocks(message):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            agg = tool_owner.get(block.get("tool_use_id"), "unknown")
            chars, images = measure_tool_result(block)
            bucket = tool_results.setdefault(agg, {"results": 0, "chars": 0, "images": 0})
            bucket["results"] += 1
            bucket["chars"] += chars
            bucket["images"] += images
            result_images += images

    if usage_estimated:
        command = pick_cursor_command(first_text)
    elif usage_basis == "codex-log":
        command = pick_codex_command(first_text)
    else:
        command = pick_command(command_names, first_text)
    return build_metrics(
        path=path,
        calls=calls,
        models=models,
        tool_calls=tool_calls,
        tool_results=tool_results,
        subagents=subagents,
        result_images=result_images,
        sidechain_rows=sidechain_rows,
        synthetic_rows=synthetic_rows,
        unkeyed_rows=unkeyed_rows,
        command=command,
        recache_threshold=recache_threshold,
        usage_estimated=usage_estimated,
        usage_basis=usage_basis,
        session_id=session_id,
    )


def usage_int(usage, key):
    value = usage.get(key)
    return value if isinstance(value, int) else 0


def build_metrics(
    path,
    calls,
    models,
    tool_calls,
    tool_results,
    subagents,
    result_images,
    sidechain_rows,
    synthetic_rows,
    unkeyed_rows,
    command,
    recache_threshold,
    usage_estimated=False,
    usage_basis=None,
    session_id=None,
):
    totals = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 0, "thinking": 0}
    contexts = []
    recache_events = 0
    recache_tokens = 0
    gaps = 0
    previous_ts = None
    timestamps = []

    for index, (usage, ts, _model) in enumerate(calls):
        inp = usage_int(usage, "input_tokens")
        create = usage_int(usage, "cache_creation_input_tokens")
        read = usage_int(usage, "cache_read_input_tokens")
        out = usage_int(usage, "output_tokens")
        details = usage.get("output_tokens_details")
        think = usage_int(details, "thinking_tokens") if isinstance(details, dict) else 0

        totals["input"] += inp
        totals["cache_create"] += create
        totals["cache_read"] += read
        totals["output"] += out
        totals["thinking"] += think
        contexts.append(inp + create + read)

        if index > 0 and create > recache_threshold:
            recache_events += 1
            recache_tokens += create
        if ts is not None:
            timestamps.append(ts)
            if previous_ts is not None and (ts - previous_ts).total_seconds() > GAP_SECONDS:
                gaps += 1
            previous_ts = ts

    ordered = sorted(contexts)
    context = {
        "first": contexts[0] if contexts else 0,
        "avg": sum(contexts) // len(contexts) if contexts else 0,
        "p50": ordered[len(ordered) // 2] if ordered else 0,
        "max": max(contexts) if contexts else 0,
        "last": contexts[-1] if contexts else 0,
    }

    start = timestamps[0] if timestamps else None
    end = timestamps[-1] if timestamps else None
    duration = (end - start).total_seconds() if start is not None and end is not None else None

    if usage_basis is None:
        usage_basis = "cursor-estimate" if usage_estimated else "claude-log"
    return {
        "session_id": session_id or path.stem,
        "path": str(path),
        "command": command,
        "models": models,
        "start": start.isoformat() if start is not None else None,
        "end": end.isoformat() if end is not None else None,
        "start_local": local_stamp(start),
        "end_local": local_stamp(end),
        "duration_sec": duration,
        "api_calls": len(calls),
        # input は「キャッシュに載らなかった入力」だけ。--log-line の new_in と
        # 突き合わせられるよう、input + cache_create を new_in として別に持つ。
        "input": totals["input"],
        "cache_create": totals["cache_create"],
        "cache_read": totals["cache_read"],
        "output": totals["output"],
        "thinking": totals["thinking"],
        "new_in": totals["input"] + totals["cache_create"],
        "thinking_ratio": (totals["thinking"] / totals["output"]) if totals["output"] else 0.0,
        "context": context,
        "recache": {
            "threshold": recache_threshold,
            "events": recache_events,
            "tokens": recache_tokens,
        },
        "gaps_over_5min": gaps,
        "tools": tool_calls,
        "tool_calls_total": sum(tool_calls.values()),
        "subagents": subagents,
        "tool_results": tool_results,
        "tool_result_images": result_images,
        "sidechain_rows": sidechain_rows,
        "synthetic_rows": synthetic_rows,
        "unkeyed_rows": unkeyed_rows,
        "usage_estimated": bool(usage_estimated),
        "usage_basis": usage_basis,
        "_calls": calls,
    }


# --------------------------------------------------------------------------
# 推定コスト(定価目安)
# --------------------------------------------------------------------------


def load_pricing(path):
    """既定の単価表に --pricing-json の内容を重ねる。"""
    pricing = {}
    for name, rates in DEFAULT_PRICING.items():
        pricing[name] = dict(rates)
    if not path:
        return pricing, None
    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "単価表を読めない: %s (%s)" % (path, exc)
    if not isinstance(raw, dict):
        return None, "単価表はオブジェクトである必要がある: %s" % path
    for name, rates in raw.items():
        if not isinstance(rates, dict):
            return None, "単価表の %s がオブジェクトでない" % name
        merged = dict(pricing.get(name) or DEFAULT_PRICING[FALLBACK_CLASS])
        for field, value in rates.items():
            if field not in PRICE_FIELDS:
                return None, "単価表の未知のキー: %s.%s" % (name, field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None, "単価表の %s.%s が数値でない" % (name, field)
            merged[field] = float(value)
        pricing[name] = merged
    return pricing, None


def classify_model(model, pricing):
    """モデル名の部分一致で単価表の区分を決める。判定不能なら None。"""
    if not model:
        return None
    lowered = model.lower()
    for name in pricing:
        if name.lower() in lowered:
            return name
    return None


def model_family_label(model, pricing_name, unknown):
    """未知モデルは系統名に `?` を付け、既知モデルは単価表の区分名を使う。"""
    if not unknown:
        return pricing_name
    lowered = (model or "").lower()
    for hint in MODEL_FAMILY_HINTS:
        if hint in lowered:
            return hint
    return pricing_name


def attach_cost(metrics, pricing):
    """呼び出しごとにモデル区分の単価を当てて推定コストを積む。"""
    total = 0.0
    classes = []
    unknown = False
    for usage, _ts, model in metrics.pop("_calls"):
        name = classify_model(model, pricing)
        if name is None:
            unknown = True
            display = model_family_label(model, FALLBACK_CLASS, True)
            name = FALLBACK_CLASS
        else:
            display = name
        if display not in classes:
            classes.append(display)
        rates = pricing[name]
        total += usage_int(usage, "input_tokens") / 1000000.0 * rates["input"]
        total += usage_int(usage, "cache_creation_input_tokens") / 1000000.0 * rates["cache_write"]
        total += usage_int(usage, "cache_read_input_tokens") / 1000000.0 * rates["cache_read"]
        total += usage_int(usage, "output_tokens") / 1000000.0 * rates["output"]

    label = "+".join(classes) if classes else FALLBACK_CLASS
    if unknown:
        label += "?"
    metrics["cost"] = {
        "estimated_usd": round(total, 4),
        "pricing_label": label,
        "pricing_classes": classes,
        "unknown_model": unknown,
        "basis": usage_cost_basis(metrics),
    }
    return metrics


def usage_source_label(metrics):
    """`--log-line` と詳細表示の末尾ラベル。"""
    basis = metrics.get("usage_basis")
    if metrics.get("usage_estimated") or basis == "cursor-estimate":
        return "Cursor概算"
    if basis == "codex-log":
        return "Codex定価目安"
    return "定価目安"


def usage_cost_basis(metrics):
    basis = metrics.get("usage_basis")
    if metrics.get("usage_estimated") or basis == "cursor-estimate":
        return "Cursor概算(定価目安)"
    if basis == "codex-log":
        return "Codex定価目安(実請求額ではない)"
    return "定価目安(実請求額ではない)"


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------


def analyze_safely(path, recache_threshold, pricing, tolerate, cursor_state_db=None):
    """1 ファイルを解析する。

    `tolerate` が真(一覧モード)なら、1 ファイルの異常で全体を落とさないよう
    警告を出して None を返す。単一セッションモードでは握り潰さずに再送出し、
    実装バグが終了コード 1 行に潰れないようにする。
    """
    try:
        cursor_meta = None
        if looks_like_cursor_log(path):
            cursor_meta = load_cursor_composer_meta(path.stem, cursor_state_db)
        return attach_cost(
            analyze_session(
                path,
                recache_threshold,
                cursor_meta=cursor_meta,
                cursor_state_db=cursor_state_db,
            ),
            pricing,
        )
    except Exception as exc:
        if not tolerate:
            raise
        log("WARN: 解析に失敗したセッションを飛ばす: %s (%s: %s)" % (path.name, type(exc).__name__, exc))
        return None


def log_line(metrics):
    cost = metrics["cost"]
    basis = usage_source_label(metrics)
    return (
        "- Usage: calls=%d avg_ctx=%s cache_read=%s new_in=%s out=%s think=%s "
        "subagents=%d est≈$%s(%s %s)"
        % (
            metrics["api_calls"],
            fmt_kilo(metrics["context"]["avg"]),
            fmt_mega(metrics["cache_read"]),
            fmt_mega(metrics["new_in"]),
            fmt_kilo(metrics["output"]),
            fmt_pct(metrics["thinking"], metrics["output"]),
            metrics["subagents"],
            fmt_usd(cost["estimated_usd"]),
            cost["pricing_label"],
            basis,
        )
    )


def short_day(metrics):
    if metrics["start_local"]:
        return metrics["start_local"][:10]
    return "?" * 10


def render_single(metrics, out):
    context = metrics["context"]
    recache = metrics["recache"]
    cost = metrics["cost"]
    total_input = metrics["input"] + metrics["cache_create"] + metrics["cache_read"]

    out.append("セッション %s" % metrics["session_id"])
    out.append("  ログ         %s" % metrics["path"])
    out.append("  起動コマンド %s" % (metrics["command"] or "(不明)"))
    out.append("  モデル       %s" % (", ".join(metrics["models"]) or "(不明)"))
    duration = metrics["duration_sec"]
    span = "%s → %s" % (metrics["start_local"] or "?", metrics["end_local"] or "?")
    if duration is not None:
        span += "(%.1f 分)" % (duration / 60.0)
    out.append("  期間         %s(ローカル時刻)" % span)
    out.append("")

    out.append("トークン(API 呼び出し %d 回、message.id で重複排除)" % metrics["api_calls"])
    out.append("  非キャッシュ入力    %8s" % fmt_short(metrics["input"]))
    out.append("  キャッシュ書き込み  %8s" % fmt_short(metrics["cache_create"]))
    out.append(
        "  キャッシュ読み戻し  %8s(入力全体の %s)"
        % (fmt_short(metrics["cache_read"]), fmt_pct(metrics["cache_read"], total_input))
    )
    out.append(
        "  新規入力            %8s(非キャッシュ入力 + キャッシュ書き込み。--log-line の new_in と同じ定義)"
        % fmt_short(metrics["new_in"])
    )
    out.append(
        "  出力                %8s(うち thinking %s = %s)"
        % (
            fmt_short(metrics["output"]),
            fmt_short(metrics["thinking"]),
            fmt_pct(metrics["thinking"], metrics["output"]),
        )
    )
    out.append(
        "  推定コスト          %8s(%s %s)"
        % (
            "$" + fmt_usd(cost["estimated_usd"]),
            cost["pricing_label"],
            usage_source_label(metrics),
        )
    )
    out.append("")

    out.append("常駐文脈長(input + cache_create + cache_read)")
    out.append(
        "  初回 %s / 平均 %s / p50 %s / 最大 %s / 最終 %s"
        % (
            fmt_short(context["first"]),
            fmt_short(context["avg"]),
            fmt_short(context["p50"]),
            fmt_short(context["max"]),
            fmt_short(context["last"]),
        )
    )
    out.append(
        "  再キャッシュ(2 回目以降で cache_create > %s): %d 件 / %s"
        % (fmt_short(recache["threshold"]), recache["events"], fmt_short(recache["tokens"]))
    )
    out.append("  直前呼び出しから 300 秒超の間隔: %d 件" % metrics["gaps_over_5min"])
    out.append("")

    out.append(
        "ツール呼び出し(合計 %d 回、サブエージェント起動 %d 回)"
        % (metrics["tool_calls_total"], metrics["subagents"])
    )
    if metrics["tools"]:
        for key, count in sorted(metrics["tools"].items(), key=lambda kv: (-kv[1], kv[0])):
            out.append("  %-22s %5d" % (key, count))
    else:
        out.append("  (なし)")
    out.append("")

    out.append("tool_result の分量(画像 %d 枚)" % metrics["tool_result_images"])
    if metrics["tool_results"]:
        out.append("  %-22s %6s %10s %6s" % ("カテゴリ", "件数", "文字数", "画像"))
        for key, bucket in sorted(
            metrics["tool_results"].items(), key=lambda kv: (-kv[1]["chars"], kv[0])
        ):
            out.append(
                "  %-22s %6d %10s %6d"
                % (key, bucket["results"], fmt_short(bucket["chars"]), bucket["images"])
            )
    else:
        out.append("  (なし)")

    if metrics["sidechain_rows"]:
        out.append("")
        out.append(
            "注: isSidechain の行 %d 件は集計から除いた(サブエージェント側の記録)。"
            % metrics["sidechain_rows"]
        )
    if metrics["synthetic_rows"]:
        out.append("")
        out.append(
            "注: %s の行 %d 件は集計から除いた(Claude Code のローカル通知で API 呼び出しではない)。"
            % (SYNTHETIC_MODEL, metrics["synthetic_rows"])
        )
    if metrics["unkeyed_rows"]:
        out.append("")
        out.append(
            "注: message.id も uuid も持たない行 %d 件は重複排除できないため 1 行 = 1 呼び出しとして数えた。"
            % metrics["unkeyed_rows"]
        )
    out.append("")
    if metrics.get("usage_estimated") or metrics.get("usage_basis") == "cursor-estimate":
        out.append("注: Cursor の transcript / composer からの概算。API の usage ログではない。")
    if metrics.get("usage_basis") == "codex-log":
        out.append(
            "注: Codex の token_usage_record の実測トークン。推定コストは定価目安で、"
            "Codex Plus の実請求額ではない。"
        )
    out.append("注: サブエージェントの使用量は親ログに含まれない。推定コストは定価目安。")


def render_list(sessions, out, selection):
    header = "%-10s %-8s %-13s %5s %8s %10s %9s %7s %8s  %s" % (
        "日付",
        "ID",
        "モデル",
        "呼出",
        "平均文脈",
        "cache_read",
        "新規入力",
        "出力",
        "推定$",
        "起動コマンド",
    )
    out.append(header)
    out.append("-" * len(header))
    total = 0.0
    for metrics in sessions:
        cost = metrics["cost"]
        total += cost["estimated_usd"]
        out.append(
            "%-10s %-8s %-13s %5d %8s %10s %9s %7s %8s  %s"
            % (
                short_day(metrics),
                metrics["session_id"][:8],
                cost["pricing_label"][:13],
                metrics["api_calls"],
                fmt_short(metrics["context"]["avg"]),
                fmt_short(metrics["cache_read"]),
                fmt_short(metrics["new_in"]),
                fmt_short(metrics["output"]),
                "$" + fmt_usd(cost["estimated_usd"]),
                (metrics["command"] or "")[:56],
            )
        )
    out.append("-" * len(header))
    if selection["truncated"]:
        # 打ち切ったときは表示分の合計だけを出すと母集団を見誤るので両方出す。
        out.append(
            "上位 %d 件 / 全 %d 件(表示分の合計 $%s、全体の合計 $%s。定価目安)"
            % (
                selection["shown"],
                selection["population"],
                fmt_usd(total),
                fmt_usd(selection["population_cost_usd"]),
            )
        )
    else:
        out.append("%d セッション / 推定コスト合計 $%s(定価目安)" % (len(sessions), fmt_usd(total)))
    if selection["skipped"]:
        out.append("注: 解析に失敗したセッション %d 件を飛ばした(警告は stderr)。" % selection["skipped"])
    out.append("注: サブエージェントの使用量は親ログに含まれない。")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="usage-report.py",
        description=(
            "Claude Code / Cursor / Codex のセッション使用量レポート。"
            "Claude Code は message.id で重複排除した API 呼び出しごとに常駐文脈長と"
            "コストを積む。Cursor は transcript と composer スナップショットから概算する。"
            "Codex は rollout の token_usage_record を積む。"
        ),
        epilog=(
            "既知の制限: サブエージェント(Task / Agent)の使用量は親セッションのログに"
            "含まれないため、本レポートはオーケストレータ側だけの値になる。"
            "推定コストは公表単価による目安で、実際の請求額ではない。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--self",
        action="store_true",
        help="このプロセスのセッション。CLAUDE_CODE_SESSION_ID / CODEX_THREAD_ID / CURSOR_CONVERSATION_ID。完了報告用",
    )
    mode.add_argument("--latest", action="store_true", help="更新時刻が最新のセッション 1 件(既定。並行時は使わない)")
    mode.add_argument("--session", metavar="ID_OR_PATH", help="セッション ID の前方一致またはログのパス")
    mode.add_argument("--since", metavar="YYYY-MM-DD", help="この日以降に更新されたセッションを一覧する")
    mode.add_argument("--all", action="store_true", help="すべてのセッションを一覧する")
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="一覧の表示件数(既定は打ち切らない。指定時はフッターに全体の合計も出す)",
    )
    parser.add_argument(
        "--min-calls",
        type=int,
        default=5,
        metavar="N",
        help="一覧モードで API 呼び出しが N 回未満のセッションを除く(既定 5)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="機械可読な完全出力")
    parser.add_argument("--log-line", action="store_true", help="完了報告に貼る 1 行だけを出す")
    parser.add_argument("--project-dir", metavar="DIR", help="Claude Code のセッションログディレクトリを直接指定する")
    parser.add_argument("--claude-home", metavar="DIR", help="~/.claude の代わりに使うディレクトリ")
    parser.add_argument(
        "--cursor-transcripts",
        metavar="DIR",
        help="Cursor の agent-transcripts ディレクトリを直接指定する",
    )
    parser.add_argument(
        "--cursor-home",
        metavar="DIR",
        help="~/.cursor の代わりに使うディレクトリ",
    )
    parser.add_argument(
        "--cursor-state-db",
        metavar="FILE",
        help="Cursor の state.vscdb(composerData)を直接指定する",
    )
    parser.add_argument(
        "--codex-sessions",
        metavar="DIR",
        help="Codex の sessions ディレクトリを直接指定する",
    )
    parser.add_argument(
        "--codex-home",
        metavar="DIR",
        help="~/.codex の代わりに使うディレクトリ",
    )
    parser.add_argument(
        "--recache-threshold",
        type=int,
        default=RECACHE_THRESHOLD,
        metavar="N",
        help="再キャッシュ事象と見なす cache_creation の下限(既定 %d)" % RECACHE_THRESHOLD,
    )
    parser.add_argument("--pricing-json", metavar="FILE", help="単価表を上書きする JSON ファイル")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.as_json and args.log_line:
        log("ERR: --json と --log-line は同時に使えない")
        return EXIT_USAGE
    if args.top is not None and args.top < 1:
        log("ERR: --top は 1 以上")
        return EXIT_USAGE
    if args.min_calls < 0:
        log("ERR: --min-calls は 0 以上")
        return EXIT_USAGE
    if args.recache_threshold < 0:
        log("ERR: --recache-threshold は 0 以上")
        return EXIT_USAGE

    # `--log-line` だけ(モード無し)は完了報告の打ち方なので --self にする。
    # 明示の --latest は人間のデバッグ用に残す。
    if args.log_line and not (
        args.self or args.latest or args.session or args.since or args.all
    ):
        args.self = True

    if args.self:
        sid = current_session_id()
        if sid is None:
            log(
                "ERR: --self は CLAUDE_CODE_SESSION_ID / CLAUDE_SESSION_ID / "
                "CODEX_THREAD_ID / CODEX_SESSION_ID / CURSOR_CONVERSATION_ID が必要。"
                "--latest は並行 ingest で他人を拾うので使わない"
            )
            return EXIT_USAGE
        args.session = sid

    since_threshold = None
    if args.since:
        since_threshold = parse_since(args.since)
        if since_threshold is None:
            log("ERR: --since は YYYY-MM-DD 形式: %s" % args.since)
            return EXIT_USAGE

    pricing, error = load_pricing(args.pricing_json)
    if error:
        log("ERR: " + error)
        return EXIT_USAGE

    # --session に実在するファイルパスが来たら project-dir 解決は要らない。
    direct = session_file_override(args)
    cursor_dir = None
    codex_dir = None
    if direct is not None:
        project_dir = None
        paths, is_list = [direct], False
    else:
        project_dir, cursor_dir, codex_dir, dir_error = resolve_log_dirs(args)
        if dir_error != EXIT_OK:
            return dir_error
        paths, is_list = select_files(
            args, project_dir, cursor_dir, since_threshold, codex_dir=codex_dir
        )
        if paths is None:
            return EXIT_MISSING

    sessions = []
    skipped = 0
    cursor_state_db = default_cursor_state_db(args)
    for path in paths:
        metrics = analyze_safely(
            path,
            args.recache_threshold,
            pricing,
            tolerate=is_list,
            cursor_state_db=cursor_state_db,
        )
        if metrics is None:
            skipped += 1
            continue
        sessions.append(metrics)

    if is_list:
        sessions = [s for s in sessions if s["api_calls"] >= args.min_calls]
        sessions.sort(key=lambda s: s["cost"]["estimated_usd"], reverse=True)
    population = len(sessions)
    population_cost = sum(s["cost"]["estimated_usd"] for s in sessions)
    if is_list and args.top is not None:
        sessions = sessions[: args.top]
    selection = {
        "mode": "list" if is_list else "single",
        "population": population,
        "shown": len(sessions),
        "truncated": len(sessions) < population,
        "shown_cost_usd": round(sum(s["cost"]["estimated_usd"] for s in sessions), 4),
        "population_cost_usd": round(population_cost, 4),
        "skipped": skipped,
        "min_calls": args.min_calls,
        "top": args.top,
    }

    if args.as_json:
        payload = {
            "project_dir": str(project_dir) if project_dir is not None else None,
            "cursor_transcripts_dir": str(cursor_dir) if cursor_dir is not None else None,
            "codex_sessions_dir": str(codex_dir) if codex_dir is not None else None,
            "vault_root": str(VAULT_ROOT),
            "mode": "list" if is_list else "single",
            "pricing": pricing,
            "selection": selection,
            "limits": {
                "subagent_usage_included": False,
                "cost_basis": "定価目安(実請求額ではない)",
            },
            "sessions": sessions,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK

    if args.log_line:
        for metrics in sessions:
            print(log_line(metrics))
        return EXIT_OK

    out = []
    if is_list:
        if not sessions:
            out.append("該当セッションなし(--min-calls %d で除外された可能性がある)" % args.min_calls)
        else:
            render_list(sessions, out, selection)
    else:
        for index, metrics in enumerate(sessions):
            if index:
                out.append("")
            render_single(metrics, out)
    print("\n".join(out))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
