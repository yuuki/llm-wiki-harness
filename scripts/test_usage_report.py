#!/usr/bin/env python3
"""scripts/usage-report.py の単体テスト。

合成した jsonl を TemporaryDirectory に置いて検証する。実際の `~/.claude` は
読まない(`--claude-home` と `--project-dir` で差し替える)。

実行: python3 scripts/test_usage_report.py
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MODULE_PATH = SCRIPTS / "usage-report.py"

_spec = importlib.util.spec_from_file_location("usage_report", MODULE_PATH)
usage_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage_report)


# --------------------------------------------------------------------------
# 合成ログの組み立て
# --------------------------------------------------------------------------

BASE_TS = "2026-09-01T00:00:00.000Z"


def stamp(offset_sec, suffix="Z"):
    """BASE_TS から offset 秒後の ISO 8601 を返す。ミリ秒まで表せる。

    `suffix=""` にすると tz を持たない naive な時刻になる(tz 混在の検証用)。
    """
    whole = int(offset_sec)
    millis = int(round((offset_sec - whole) * 1000))
    minutes, seconds = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    return "2026-09-01T%02d:%02d:%02d.%03d%s" % (hours, minutes, seconds, millis, suffix)


def usage(input_tokens=0, cache_create=0, cache_read=0, output=0, thinking=None):
    payload = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_create,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output,
    }
    if thinking is not None:
        payload["output_tokens_details"] = {"thinking_tokens": thinking}
    return payload


def assistant_row(
    message_id, content, usage_payload, offset_sec, model="claude-sonnet-5", tz_suffix="Z", **extra
):
    row = {
        "type": "assistant",
        "uuid": "u-%s-%s" % (message_id, offset_sec),
        "timestamp": stamp(offset_sec, tz_suffix),
        "message": {
            "role": "assistant",
            "id": message_id,
            "model": model,
            "content": content,
        },
    }
    if usage_payload is not None:
        row["message"]["usage"] = usage_payload
    row.update(extra)
    return row


def user_row(content, offset_sec=0, **extra):
    row = {
        "type": "user",
        "uuid": "uu-%s" % offset_sec,
        "timestamp": stamp(offset_sec),
        "message": {"role": "user", "content": content},
    }
    row.update(extra)
    return row


def write_session(directory, session_id, rows, trailing_garbage=None):
    path = Path(directory) / ("%s.jsonl" % session_id)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if trailing_garbage is not None:
            handle.write(trailing_garbage)
    return path


def basic_session_rows():
    """1 呼び出しが 3 行に分割された例を含む 3 呼び出しのセッション。

    3 回目は直前から 400 秒後で、cache_creation が既定閾値を超える(再キャッシュ)。
    """
    return [
        user_row("<local-command-caveat>Caveat: ...</local-command-caveat>", 0, isMeta=True),
        user_row("<command-name>/clear</command-name>\n<command-args></command-args>", 0),
        user_row(
            "<command-name>/wiki-ingest-paper</command-name>\n"
            "<command-args>https://arxiv.org/abs/2310.05063</command-args>",
            0,
        ),
        # 呼び出し 1 — thinking / text / tool_use の 3 行が同じ message.id を持つ。
        assistant_row("msg_1", [{"type": "thinking", "thinking": "x"}], usage(2, 100000, 0, 100, 40), 0),
        assistant_row("msg_1", [{"type": "text", "text": "hello"}], usage(2, 100000, 0, 100, 40), 0),
        assistant_row(
            "msg_1",
            [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}],
            usage(2, 100000, 0, 100, 40),
            0,
        ),
        user_row([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], 5),
        # 呼び出し 2
        assistant_row("msg_2", [{"type": "text", "text": "again"}], usage(1, 0, 100000, 200, 50), 10),
        # 呼び出し 3 — 400 秒後 & cache_creation 50000。
        assistant_row("msg_3", [{"type": "text", "text": "done"}], usage(1, 50000, 100000, 300, 60), 410),
    ]


def tool_session_rows():
    """Read の細分カテゴリと tool_result の分量を確かめるためのセッション。"""
    reads = [
        ("r-image", "/Users/x/obsidian/research/wiki/sources/_attachments/a/fig.PNG"),
        ("r-raw", "/Users/x/obsidian/research/.raw/papers/arxiv-1.txt"),
        ("r-meta", "/Users/x/obsidian/research/wiki/meta/conventions.md"),
        ("r-skill", "/Users/x/.claude/skills/wiki-ingest/SKILL.md"),
        ("r-page", "/Users/x/obsidian/research/wiki/concepts/foo.md"),
        ("r-other", "/Users/x/obsidian/research/README.txt"),
    ]
    blocks = [
        {"type": "tool_use", "id": use_id, "name": "Read", "input": {"file_path": path}}
        for use_id, path in reads
    ]
    blocks.append({"type": "tool_use", "id": "t-task", "name": "Task", "input": {"prompt": "go"}})
    blocks.append({"type": "tool_use", "id": "t-agent", "name": "Agent", "input": {"prompt": "go"}})
    return [
        user_row("plain prompt without a slash command", 0),
        assistant_row("msg_a", blocks, usage(0, 1000, 0, 10, 5), 0),
        user_row(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "r-image",
                    "content": [{"type": "image", "source": {}}, {"type": "text", "text": "abc"}],
                },
                {"type": "tool_result", "tool_use_id": "r-raw", "content": "abcde"},
                {
                    "type": "tool_result",
                    "tool_use_id": "r-page",
                    "content": [{"type": "text", "text": "0123456789"}],
                },
                {"type": "tool_result", "tool_use_id": "never-seen", "content": "1234"},
            ],
            5,
        ),
    ]


# --------------------------------------------------------------------------
# 実行ヘルパー
# --------------------------------------------------------------------------


def run_cli(argv):
    """main() を呼び、(終了コード, 標準出力) を返す。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        with contextlib.redirect_stderr(io.StringIO()):
            code = usage_report.main(argv)
    return code, buffer.getvalue()


def run_script(argv, env_overrides=None, cwd=None):
    """スクリプトを別プロセスで実行する。環境変数と cwd の効き方を検証するのに使う。"""
    env = os.environ.copy()
    env.pop("WIKI_VAULT_ROOT", None)
    # 実セッションの中でテストを回しても --self が宿主を拾わないようにする。
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("CLAUDE_SESSION_ID", None)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, str(MODULE_PATH)] + list(argv),
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def analyze(path, **kwargs):
    metrics = usage_report.analyze_session(Path(path), **kwargs)
    pricing, error = usage_report.load_pricing(None)
    assert error is None
    return usage_report.attach_cost(metrics, pricing)


class TempProject(unittest.TestCase):
    """`<tmp>/claude/projects/<slug>/` を用意する共通の下地。"""

    SLUG = "-tmp-fake-vault"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.claude_home = self.root / "claude"
        self.project_dir = self.claude_home / "projects" / self.SLUG
        self.project_dir.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def base_argv(self):
        return ["--project-dir", str(self.project_dir)]


# --------------------------------------------------------------------------
# 1. message.id による重複排除
# --------------------------------------------------------------------------


class DedupTest(TempProject):
    def test_same_message_id_counts_once(self):
        path = write_session(self.project_dir, "aaaa1111-dedup", basic_session_rows())
        metrics = analyze(path)
        # 生の assistant 行は 5 行だが、msg_1 の 3 行は 1 呼び出しに畳まれる。
        self.assertEqual(metrics["api_calls"], 3)
        self.assertEqual(metrics["input"], 4)
        self.assertEqual(metrics["cache_create"], 150000)
        self.assertEqual(metrics["cache_read"], 200000)
        self.assertEqual(metrics["output"], 600)
        self.assertEqual(metrics["thinking"], 150)
        # new_in は非キャッシュ入力 + キャッシュ書き込み(--log-line の new_in と同義)。
        self.assertEqual(metrics["new_in"], 150004)
        self.assertEqual(metrics["unkeyed_rows"], 0)

    def test_split_rows_keep_their_tool_use_blocks(self):
        """重複行を飛ばして tool_use を取りこぼしていないこと。"""
        rows = [
            assistant_row("msg_x", [{"type": "thinking", "thinking": "t"}], usage(0, 10, 0, 1), 0),
            assistant_row(
                "msg_x",
                [{"type": "tool_use", "id": "tx", "name": "Bash", "input": {"command": "ls"}}],
                usage(0, 10, 0, 1),
                0,
            ),
            assistant_row(
                "msg_x",
                [{"type": "tool_use", "id": "ty", "name": "Grep", "input": {"pattern": "a"}}],
                usage(0, 10, 0, 1),
                0,
            ),
        ]
        path = write_session(self.project_dir, "aaaa5555-split", rows)
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 1)
        self.assertEqual(metrics["cache_create"], 10)
        self.assertEqual(metrics["tools"], {"Bash": 1, "Grep": 1})

    def test_rows_without_any_identifier_are_counted_once_each(self):
        rows = [
            {
                "type": "assistant",
                "timestamp": stamp(0),
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "a"}],
                    "usage": usage(0, 5, 0, 1),
                },
            },
            {
                "type": "assistant",
                "timestamp": stamp(1),
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "b"}],
                    "usage": usage(0, 7, 0, 1),
                },
            },
        ]
        path = write_session(self.project_dir, "aaaa6666-unkeyed", rows)
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 2)
        self.assertEqual(metrics["cache_create"], 12)
        self.assertEqual(metrics["unkeyed_rows"], 2)

    def test_unkeyed_rows_surface_in_json_and_notes(self):
        rows = [
            {
                "type": "assistant",
                "timestamp": stamp(0),
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "a"}],
                    "usage": usage(0, 5, 0, 1),
                },
            }
        ]
        write_session(self.project_dir, "aaaa7777-unkeyed", rows)
        code, out = run_cli(self.base_argv() + ["--session", "aaaa7777", "--json"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertEqual(json.loads(out)["sessions"][0]["unkeyed_rows"], 1)
        code, out = run_cli(self.base_argv() + ["--session", "aaaa7777"])
        self.assertIn("message.id も uuid も持たない行 1 件", out)

    def test_rows_without_message_id_fall_back_to_uuid(self):
        rows = [
            {
                "type": "assistant",
                "uuid": "only-uuid-1",
                "timestamp": stamp(0),
                "message": {"role": "assistant", "model": "claude-sonnet-5", "usage": usage(0, 10, 0, 1)},
            },
            {
                "type": "assistant",
                "uuid": "only-uuid-2",
                "timestamp": stamp(1),
                "message": {"role": "assistant", "model": "claude-sonnet-5", "usage": usage(0, 10, 0, 1)},
            },
        ]
        path = write_session(self.project_dir, "aaaa2222-uuid", rows)
        self.assertEqual(analyze(path)["api_calls"], 2)

    def test_launch_command_skips_control_commands(self):
        path = write_session(self.project_dir, "aaaa3333-cmd", basic_session_rows())
        metrics = analyze(path)
        self.assertEqual(
            metrics["command"], "/wiki-ingest-paper https://arxiv.org/abs/2310.05063"
        )

    def test_launch_command_falls_back_to_first_user_text(self):
        path = write_session(self.project_dir, "aaaa4444-txt", tool_session_rows())
        self.assertEqual(analyze(path)["command"], "plain prompt without a slash command")


# --------------------------------------------------------------------------
# 2. 常駐文脈長・再キャッシュ・ギャップ
# --------------------------------------------------------------------------


class ContextTest(TempProject):
    def setUp(self):
        super().setUp()
        self.path = write_session(self.project_dir, "bbbb1111-ctx", basic_session_rows())

    def test_context_statistics(self):
        context = analyze(self.path)["context"]
        # 各呼び出しの input + cache_create + cache_read。
        self.assertEqual(context["first"], 100002)
        self.assertEqual(context["avg"], (100002 + 100001 + 150001) // 3)
        self.assertEqual(context["p50"], 100002)
        self.assertEqual(context["max"], 150001)
        self.assertEqual(context["last"], 150001)

    def test_recache_events_skip_the_first_call(self):
        recache = analyze(self.path)["recache"]
        # 呼び出し 1 の cache_create 100000 は初回なので数えない。
        self.assertEqual(recache["events"], 1)
        self.assertEqual(recache["tokens"], 50000)
        self.assertEqual(recache["threshold"], usage_report.RECACHE_THRESHOLD)

    def test_recache_threshold_is_configurable(self):
        loose = analyze(self.path, recache_threshold=60000)
        self.assertEqual(loose["recache"]["events"], 0)
        # 閾値を下げても呼び出し 2 は cache_create が 0 なので数えず、初回も対象外。
        tight = analyze(self.path, recache_threshold=100)
        self.assertEqual(tight["recache"]["events"], 1)
        self.assertEqual(tight["recache"]["tokens"], 50000)

    def test_gaps_over_five_minutes(self):
        # 呼び出し 2 → 3 が 400 秒。呼び出し 1 → 2 は 10 秒。
        self.assertEqual(analyze(self.path)["gaps_over_5min"], 1)

    def test_duration_and_local_stamps(self):
        metrics = analyze(self.path)
        self.assertEqual(metrics["duration_sec"], 410.0)
        self.assertIsNotNone(metrics["start_local"])
        self.assertIsNotNone(metrics["end_local"])


class TimezoneMixTest(TempProject):
    """H1: naive と aware の時刻が混ざっても落ちないこと。"""

    def mixed_rows(self):
        return [
            # 末尾 Z あり(aware)。
            assistant_row("msg_aware", [{"type": "text", "text": "a"}], usage(0, 10, 0, 1), 0),
            # 末尾 Z なし(naive)。UTC を補って揃える。
            assistant_row(
                "msg_naive", [{"type": "text", "text": "b"}], usage(0, 10, 0, 1), 600, tz_suffix=""
            ),
            # 明示的な別オフセット。
            assistant_row(
                "msg_offset",
                [{"type": "text", "text": "c"}],
                usage(0, 10, 0, 1),
                660,
                tz_suffix="+00:00",
            ),
        ]

    def test_naive_and_aware_timestamps_do_not_raise(self):
        path = write_session(self.project_dir, "1111aaaa-tzmix", self.mixed_rows())
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 3)
        self.assertEqual(metrics["duration_sec"], 660.0)
        # 0 秒 → 600 秒が 300 秒超、600 秒 → 660 秒は 60 秒。
        self.assertEqual(metrics["gaps_over_5min"], 1)

    def test_parse_timestamp_always_returns_aware(self):
        aware = usage_report.parse_timestamp("2026-09-01T00:00:00.000Z")
        naive = usage_report.parse_timestamp("2026-09-01T00:10:00")
        naive_ms = usage_report.parse_timestamp("2026-09-01T00:10:00.500")
        for moment in (aware, naive, naive_ms):
            self.assertIsNotNone(moment)
            self.assertIsNotNone(moment.tzinfo)
        # 混在しても減算できる。
        self.assertEqual((naive - aware).total_seconds(), 600.0)
        self.assertIsNone(usage_report.parse_timestamp("not-a-timestamp"))
        self.assertIsNone(usage_report.parse_timestamp(None))

    def test_list_mode_survives_a_timezone_mixed_file(self):
        write_session(self.project_dir, "1111bbbb-tzmix", self.mixed_rows())
        write_session(self.project_dir, "1111cccc-plain", basic_session_rows())
        code, out = run_cli(self.base_argv() + ["--all", "--min-calls", "1"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("1111bbbb", out)
        self.assertIn("1111cccc", out)

    def test_one_broken_file_does_not_block_the_others(self):
        write_session(self.project_dir, "1111dddd-boom", basic_session_rows())
        write_session(self.project_dir, "1111eeee-fine", basic_session_rows())
        original = usage_report.analyze_session

        def exploding(path, *args, **kwargs):
            if "boom" in path.name:
                raise TypeError("can't subtract offset-naive and offset-aware datetimes")
            return original(path, *args, **kwargs)

        usage_report.analyze_session = exploding
        try:
            code, out = run_cli(self.base_argv() + ["--all", "--min-calls", "1"])
        finally:
            usage_report.analyze_session = original
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("1111eeee", out)
        self.assertNotIn("1111dddd", out)
        self.assertIn("解析に失敗したセッション 1 件", out)

    def test_single_mode_propagates_the_exception(self):
        """LOW: 単一セッションモードでは実装バグを握り潰さず traceback を出す。"""
        write_session(self.project_dir, "1111ffff-boom", basic_session_rows())
        original = usage_report.analyze_session

        def exploding(path, *args, **kwargs):
            raise RuntimeError("実装バグ")

        usage_report.analyze_session = exploding
        try:
            with self.assertRaises(RuntimeError):
                run_cli(self.base_argv() + ["--session", "1111ffff"])
            with self.assertRaises(RuntimeError):
                run_cli(self.base_argv() + ["--latest"])
        finally:
            usage_report.analyze_session = original


class BoundaryTest(TempProject):
    """M4: 閾値ちょうどの値の扱い。"""

    def _recache_events(self, second_call_cache_create):
        rows = [
            assistant_row("msg_1", [{"type": "text", "text": "a"}], usage(0, 1000, 0, 1), 0),
            assistant_row(
                "msg_2",
                [{"type": "text", "text": "b"}],
                usage(0, second_call_cache_create, 0, 1),
                10,
            ),
        ]
        path = write_session(self.project_dir, "2222aaaa-recache-%d" % second_call_cache_create, rows)
        return analyze(path)["recache"]

    def test_recache_threshold_is_exclusive(self):
        # 閾値ちょうどは「超えて」いないので数えない。
        exact = self._recache_events(usage_report.RECACHE_THRESHOLD)
        self.assertEqual(exact["events"], 0)
        self.assertEqual(exact["tokens"], 0)
        over = self._recache_events(usage_report.RECACHE_THRESHOLD + 1)
        self.assertEqual(over["events"], 1)
        self.assertEqual(over["tokens"], usage_report.RECACHE_THRESHOLD + 1)

    def _gaps(self, second_offset):
        rows = [
            assistant_row("msg_1", [{"type": "text", "text": "a"}], usage(0, 10, 0, 1), 0),
            assistant_row("msg_2", [{"type": "text", "text": "b"}], usage(0, 10, 0, 1), second_offset),
        ]
        path = write_session(self.project_dir, "2222bbbb-gap-%s" % second_offset, rows)
        return analyze(path)

    def test_gap_threshold_is_exclusive(self):
        exact = self._gaps(300.0)
        self.assertEqual(exact["duration_sec"], 300.0)
        # ちょうど 300 秒は「超えて」いないので数えない。
        self.assertEqual(exact["gaps_over_5min"], 0)
        over = self._gaps(300.001)
        self.assertEqual(over["gaps_over_5min"], 1)
        self.assertEqual(usage_report.GAP_SECONDS, 300.0)


# --------------------------------------------------------------------------
# 3. ツール内訳と tool_result の分量
# --------------------------------------------------------------------------


class ToolBreakdownTest(TempProject):
    def setUp(self):
        super().setUp()
        self.path = write_session(self.project_dir, "cccc1111-tools", tool_session_rows())
        self.metrics = analyze(self.path)

    def test_read_subcategories(self):
        tools = self.metrics["tools"]
        self.assertEqual(tools["Read:image"], 1)
        self.assertEqual(tools["Read:raw-source"], 1)
        self.assertEqual(tools["Read:wiki-meta"], 1)
        self.assertEqual(tools["Read:skill"], 1)
        self.assertEqual(tools["Read:wiki-page"], 1)
        self.assertEqual(tools["Read:other"], 1)

    def test_image_extension_wins_over_path_category(self):
        # wiki/ 配下の画像でも wiki-page ではなく image に入る(大文字拡張子も同じ)。
        self.assertEqual(
            usage_report.read_category({"file_path": "/v/wiki/sources/_attachments/a/fig.PNG"}),
            "image",
        )

    def test_subagent_tools_counted_separately(self):
        self.assertEqual(self.metrics["subagents"], 2)
        self.assertEqual(self.metrics["tools"]["Task"], 1)
        self.assertEqual(self.metrics["tools"]["Agent"], 1)

    def test_tool_result_chars_and_images(self):
        results = self.metrics["tool_results"]
        # 画像 1 枚 + テキスト "abc"。
        self.assertEqual(results["Read:image"]["images"], 1)
        self.assertEqual(results["Read:image"]["chars"], 3)
        self.assertEqual(results["Read:raw-source"]["chars"], 5)
        self.assertEqual(results["Read:wiki-page"]["chars"], 10)
        # 対応する tool_use が見つからない結果は unknown へ落ちる。
        self.assertEqual(results["unknown"]["chars"], 4)
        self.assertEqual(self.metrics["tool_result_images"], 1)
        self.assertEqual(sum(b["results"] for b in results.values()), 4)


# --------------------------------------------------------------------------
# 4. 単価表と推定コスト
# --------------------------------------------------------------------------


class PricingTest(TempProject):
    def setUp(self):
        super().setUp()
        self.rows = basic_session_rows()

    def _cost(self, model):
        rows = []
        for row in self.rows:
            if row["type"] == "assistant":
                row = json.loads(json.dumps(row))
                row["message"]["model"] = model
            rows.append(row)
        path = write_session(self.project_dir, "dddd1111-price", rows)
        return analyze(path)["cost"]

    def test_sonnet_pricing(self):
        cost = self._cost("claude-sonnet-5")
        # input 4 / cache_write 150000 / cache_read 200000 / output 600
        expected = 4 / 1e6 * 3 + 150000 / 1e6 * 3.75 + 200000 / 1e6 * 0.30 + 600 / 1e6 * 15
        self.assertAlmostEqual(cost["estimated_usd"], round(expected, 4), places=6)
        self.assertEqual(cost["pricing_label"], "sonnet")
        self.assertFalse(cost["unknown_model"])

    def test_opus_pricing(self):
        cost = self._cost("claude-opus-4-1-20250805")
        expected = 4 / 1e6 * 15 + 150000 / 1e6 * 18.75 + 200000 / 1e6 * 1.5 + 600 / 1e6 * 75
        self.assertAlmostEqual(cost["estimated_usd"], round(expected, 4), places=6)
        self.assertEqual(cost["pricing_label"], "opus")

    def test_haiku_pricing(self):
        cost = self._cost("claude-3-5-haiku-latest")
        self.assertEqual(cost["pricing_label"], "haiku")

    def test_unknown_model_falls_back_to_sonnet_with_marker(self):
        cost = self._cost("some-other-llm-v9")
        sonnet = self._cost("claude-sonnet-5")
        self.assertEqual(cost["estimated_usd"], sonnet["estimated_usd"])
        self.assertEqual(cost["pricing_label"], "sonnet?")
        self.assertTrue(cost["unknown_model"])

    def test_synthetic_rows_are_excluded(self):
        rows = self.rows + [
            assistant_row(
                "msg_synthetic",
                [{"type": "text", "text": "You've hit your session limit"}],
                usage(0, 0, 0, 0),
                500,
                model=usage_report.SYNTHETIC_MODEL,
            )
        ]
        path = write_session(self.project_dir, "dddd2222-synth", rows)
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 3)
        self.assertEqual(metrics["synthetic_rows"], 1)
        self.assertEqual(metrics["cost"]["pricing_label"], "sonnet")

    def test_sidechain_rows_are_excluded(self):
        rows = self.rows + [
            assistant_row("msg_side", [{"type": "text", "text": "sub"}], usage(0, 999, 0, 9), 500, isSidechain=True)
        ]
        path = write_session(self.project_dir, "dddd3333-side", rows)
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 3)
        self.assertEqual(metrics["cache_create"], 150000)
        self.assertEqual(metrics["sidechain_rows"], 1)

    def test_pricing_json_override(self):
        override = self.root / "pricing.json"
        override.write_text(
            json.dumps({"sonnet": {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}}),
            encoding="utf-8",
        )
        pricing, error = usage_report.load_pricing(str(override))
        self.assertIsNone(error)
        self.assertEqual(pricing["sonnet"]["cache_read"], 0.0)
        # 上書きしなかった区分は既定値のまま。
        self.assertEqual(pricing["opus"]["cache_read"], 1.5)

        path = write_session(self.project_dir, "dddd4444-zero", self.rows)
        metrics = usage_report.attach_cost(usage_report.analyze_session(path), pricing)
        self.assertEqual(metrics["cost"]["estimated_usd"], 0.0)

    def test_pricing_json_rejects_bad_input(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"sonnet": {"nope": 1}}), encoding="utf-8")
        pricing, error = usage_report.load_pricing(str(bad))
        self.assertIsNone(pricing)
        self.assertIn("未知のキー", error)

        missing = self.root / "missing.json"
        pricing, error = usage_report.load_pricing(str(missing))
        self.assertIsNone(pricing)
        self.assertIn("読めない", error)

    def test_pricing_json_allows_new_model_class(self):
        extra = self.root / "extra.json"
        extra.write_text(
            json.dumps({"zeta": {"input": 1, "cache_write": 1, "cache_read": 1, "output": 1}}),
            encoding="utf-8",
        )
        pricing, error = usage_report.load_pricing(str(extra))
        self.assertIsNone(error)
        self.assertEqual(usage_report.classify_model("vendor-zeta-2", pricing), "zeta")


# --------------------------------------------------------------------------
# 5. CLI の出力形式と選別
# --------------------------------------------------------------------------


class CliTest(TempProject):
    def setUp(self):
        super().setUp()
        self.session_id = "eeee1111-2222-3333-4444-555566667777"
        self.path = write_session(self.project_dir, self.session_id, basic_session_rows())

    def test_self_reads_official_env_not_latest(self):
        """並行で新しいログがあっても --self は環境変数の ID だけを見る。"""
        newer = write_session(self.project_dir, "ffff9999-newer-session", basic_session_rows())
        newer_mtime = self.path.stat().st_mtime + 10
        os.utime(newer, (newer_mtime, newer_mtime))
        code, out, err = run_script(
            self.base_argv() + ["--self", "--json"],
            env_overrides={"CLAUDE_CODE_SESSION_ID": self.session_id},
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        payload = json.loads(out)
        self.assertEqual(payload["sessions"][0]["session_id"], self.session_id)
        latest_code, latest_out = run_cli(self.base_argv() + ["--latest", "--json"])
        self.assertEqual(latest_code, usage_report.EXIT_OK)
        latest = json.loads(latest_out)
        self.assertEqual(latest["sessions"][0]["session_id"], newer.stem)

    def test_self_prefers_official_env_over_legacy(self):
        other = write_session(self.project_dir, "aabbccdd-legacy-env", basic_session_rows())
        code, out, err = run_script(
            self.base_argv() + ["--self", "--json"],
            env_overrides={
                "CLAUDE_CODE_SESSION_ID": self.session_id,
                "CLAUDE_SESSION_ID": other.stem,
            },
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        payload = json.loads(out)
        self.assertEqual(payload["sessions"][0]["session_id"], self.session_id)

    def test_self_falls_back_to_legacy_env(self):
        code, out, err = run_script(
            self.base_argv() + ["--self", "--log-line"],
            env_overrides={"CLAUDE_SESSION_ID": self.session_id},
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        self.assertIn("calls=3", out)

    def test_self_without_env_is_usage_error(self):
        code, _out, err = run_script(self.base_argv() + ["--self", "--log-line"])
        self.assertEqual(code, usage_report.EXIT_USAGE)
        self.assertIn("CLAUDE_CODE_SESSION_ID", err)
        self.assertIn("--latest", err)

    def test_self_unknown_id_is_missing(self):
        code, _out, err = run_script(
            self.base_argv() + ["--self"],
            env_overrides={"CLAUDE_CODE_SESSION_ID": "deadbeef-missing"},
        )
        self.assertEqual(code, usage_report.EXIT_MISSING)
        self.assertIn("見つからない", err)

    def test_current_session_id_helper(self):
        self.assertIsNone(usage_report.current_session_id({}))
        self.assertIsNone(usage_report.current_session_id({"CLAUDE_CODE_SESSION_ID": "  "}))
        self.assertEqual(
            usage_report.current_session_id({"CLAUDE_CODE_SESSION_ID": " abc "}),
            "abc",
        )
        self.assertEqual(
            usage_report.current_session_id({"CLAUDE_SESSION_ID": "old"}),
            "old",
        )

    def test_log_line_format(self):
        code, out = run_cli(self.base_argv() + ["--session", "eeee1111", "--log-line"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertEqual(
            out.strip(),
            "- Usage: calls=3 avg_ctx=117k cache_read=0.20M new_in=0.15M out=1k "
            "think=25% subagents=0 est≈$0.63(sonnet 定価目安)",
        )

    def test_short_number_formatting(self):
        self.assertEqual(usage_report.fmt_short(22688688), "22.7M")
        self.assertEqual(usage_report.fmt_short(239614), "240k")
        self.assertEqual(usage_report.fmt_short(61202), "61k")
        self.assertEqual(usage_report.fmt_short(192), "192")
        self.assertEqual(usage_report.fmt_mega(314265), "0.31M")
        self.assertEqual(usage_report.fmt_kilo(239614), "240k")

    def test_json_keys(self):
        code, out = run_cli(self.base_argv() + ["--session", "eeee1111", "--json"])
        self.assertEqual(code, usage_report.EXIT_OK)
        payload = json.loads(out)
        for key in ("sessions", "pricing", "project_dir", "mode", "limits", "selection"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["sessions"]), 1)
        session = payload["sessions"][0]
        for key in (
            "session_id",
            "api_calls",
            "input",
            "cache_create",
            "cache_read",
            "output",
            "thinking",
            "thinking_ratio",
            "new_in",
            "context",
            "recache",
            "gaps_over_5min",
            "tools",
            "subagents",
            "tool_results",
            "tool_result_images",
            "cost",
            "command",
            "models",
            "start",
            "end",
            "duration_sec",
            "sidechain_rows",
            "synthetic_rows",
            "unkeyed_rows",
        ):
            self.assertIn(key, session)
        # 新規入力は非キャッシュ入力 + キャッシュ書き込みで、log-line と一致する。
        self.assertEqual(session["new_in"], session["input"] + session["cache_create"])
        for key in ("first", "avg", "p50", "max", "last"):
            self.assertIn(key, session["context"])
        # 内部用の生データは JSON に漏らさない。
        self.assertNotIn("_calls", session)

    def test_session_prefix_match(self):
        code, out = run_cli(self.base_argv() + ["--session", "eeee"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn(self.session_id, out)

    def test_session_accepts_a_path(self):
        code, out = run_cli(self.base_argv() + ["--session", str(self.path), "--log-line"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("calls=3", out)

    def test_session_ambiguous_prefix_is_an_error(self):
        write_session(self.project_dir, "eeee9999-other", basic_session_rows())
        code, _out = run_cli(self.base_argv() + ["--session", "eeee"])
        self.assertEqual(code, usage_report.EXIT_MISSING)

    def test_session_not_found(self):
        code, _out = run_cli(self.base_argv() + ["--session", "zzzz"])
        self.assertEqual(code, usage_report.EXIT_MISSING)

    def test_latest_picks_the_newest_file(self):
        newer = write_session(self.project_dir, "ffff1111-newer", basic_session_rows())
        os.utime(self.path, (1000000, 1000000))
        os.utime(newer, (2000000, 2000000))
        code, out = run_cli(self.base_argv() + ["--latest"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("ffff1111-newer", out)
        self.assertNotIn(self.session_id, out)

    def test_since_filters_by_file_mtime(self):
        old = write_session(self.project_dir, "ffff2222-old", basic_session_rows())
        old_ts = usage_report.parse_since("2026-08-01")
        new_ts = usage_report.parse_since("2026-09-03")
        os.utime(old, (old_ts, old_ts))
        os.utime(self.path, (new_ts, new_ts))

        argv = self.base_argv() + ["--since", "2026-09-01", "--min-calls", "1"]
        code, out = run_cli(argv)
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("eeee1111", out)
        self.assertNotIn("ffff2222", out)
        self.assertIn("1 セッション", out)

    def test_since_rejects_a_bad_date_as_a_usage_error(self):
        for bad in ("2026/09/01", "not-a-date", "2026-13-40"):
            code, _out = run_cli(self.base_argv() + ["--since", bad])
            self.assertEqual(code, usage_report.EXIT_USAGE)

    def test_since_without_top_lists_everything(self):
        for index in range(4):
            write_session(self.project_dir, "aaaa%04d-many" % index, basic_session_rows())
        argv = self.base_argv() + ["--since", "2000-01-01", "--min-calls", "1"]
        code, out = run_cli(argv)
        self.assertEqual(code, usage_report.EXIT_OK)
        # 元の 1 件 + 追加 4 件。--top 未指定なので打ち切らない。
        self.assertIn("5 セッション / 推定コスト合計", out)
        self.assertNotIn("上位", out)
        for index in range(4):
            self.assertIn("aaaa%04d" % index, out)

    def test_top_footer_reports_both_totals(self):
        write_session(self.project_dir, "bbbb0001-second", basic_session_rows())
        argv = self.base_argv() + ["--all", "--min-calls", "1", "--top", "1"]
        code, out = run_cli(argv)
        self.assertEqual(code, usage_report.EXIT_OK)
        footer = [line for line in out.splitlines() if line.startswith("上位 ")]
        self.assertEqual(len(footer), 1)
        # 2 セッションが同一内容なので、表示分は全体の半額になる。
        one = analyze(self.path)["cost"]["estimated_usd"]
        self.assertIn("上位 1 件 / 全 2 件", footer[0])
        self.assertIn("表示分の合計 $%s" % usage_report.fmt_usd(one), footer[0])
        self.assertIn("全体の合計 $%s" % usage_report.fmt_usd(one * 2), footer[0])

    def test_selection_block_in_json(self):
        write_session(self.project_dir, "bbbb0002-second", basic_session_rows())
        code, out = run_cli(
            self.base_argv() + ["--all", "--min-calls", "1", "--top", "1", "--json"]
        )
        self.assertEqual(code, usage_report.EXIT_OK)
        selection = json.loads(out)["selection"]
        self.assertEqual(selection["population"], 2)
        self.assertEqual(selection["shown"], 1)
        self.assertTrue(selection["truncated"])
        self.assertAlmostEqual(
            selection["population_cost_usd"], selection["shown_cost_usd"] * 2, places=4
        )
        self.assertEqual(selection["skipped"], 0)

    def test_top_is_ignored_in_single_session_mode(self):
        code, out = run_cli(self.base_argv() + ["--session", "eeee1111", "--top", "1"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("API 呼び出し 3 回", out)

    def test_min_calls_excludes_small_sessions_in_list_mode(self):
        code, out = run_cli(self.base_argv() + ["--all", "--min-calls", "4"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("該当セッションなし", out)
        # 単一セッション指定は --min-calls で落とさない。
        code, out = run_cli(self.base_argv() + ["--session", "eeee1111", "--min-calls", "99"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("API 呼び出し 3 回", out)

    def test_top_limits_and_sorts_by_cost(self):
        cheap = json.loads(json.dumps(basic_session_rows()))
        write_session(self.project_dir, "ffff3333-cheap", cheap[:4])
        code, out = run_cli(self.base_argv() + ["--all", "--min-calls", "1", "--top", "1"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("上位 1 件 / 全 2 件", out)
        # 高い方(3 呼び出しの完全なセッション)だけが残る。
        self.assertIn("eeee1111", out)
        self.assertNotIn("ffff3333", out)

    def test_log_line_without_mode_is_self_not_latest(self):
        """`--log-line` だけは完了報告扱い。新しい他人を拾わない。"""
        newer = write_session(self.project_dir, "ffff9999-newer-session", tool_session_rows())
        newer_mtime = self.path.stat().st_mtime + 10
        os.utime(newer, (newer_mtime, newer_mtime))
        code, out, err = run_script(
            self.base_argv() + ["--log-line"],
            env_overrides={"CLAUDE_CODE_SESSION_ID": self.session_id},
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        self.assertIn("calls=3", out)
        latest_code, latest_out = run_cli(self.base_argv() + ["--latest", "--log-line"])
        self.assertEqual(latest_code, usage_report.EXIT_OK)
        self.assertIn("calls=1", latest_out)
        self.assertNotEqual(out, latest_out)

    def test_self_and_latest_are_mutually_exclusive(self):
        code, _out, err = run_script(self.base_argv() + ["--self", "--latest"])
        self.assertEqual(code, usage_report.EXIT_USAGE)
        self.assertTrue(err.strip())

    def test_json_and_log_line_are_mutually_exclusive(self):
        code, _out = run_cli(self.base_argv() + ["--json", "--log-line"])
        self.assertEqual(code, usage_report.EXIT_USAGE)

    def test_bad_numeric_options(self):
        for argv in (["--top", "0"], ["--min-calls", "-1"], ["--recache-threshold", "-5"]):
            code, _out = run_cli(self.base_argv() + argv)
            self.assertEqual(code, usage_report.EXIT_USAGE)

    def test_missing_claude_home_is_reported(self):
        code, _out = run_cli(["--claude-home", str(self.root / "nope")])
        self.assertEqual(code, usage_report.EXIT_MISSING)

    def test_session_path_skips_project_dir_resolution(self):
        """M2: 実在するパスを渡せば project-dir 解決の失敗に巻き込まれない。"""
        code, out, _err = run_script(
            ["--project-dir", str(self.root / "does-not-exist"), "--session", str(self.path), "--log-line"]
        )
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("calls=3", out)

    def test_session_path_works_without_any_project_dir(self):
        # ~/.claude が無い環境でもパス指定なら読める。
        code, out, _err = run_script(
            ["--claude-home", str(self.root / "nope"), "--session", str(self.path), "--log-line"]
        )
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("calls=3", out)

    def test_session_path_outside_project_dir(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        other = write_session(outside, "dddd8888-outside", basic_session_rows())
        code, out, _err = run_script(
            ["--project-dir", str(self.project_dir), "--session", str(other), "--log-line"]
        )
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("calls=3", out)

    def test_bare_id_is_not_shadowed_by_a_file_in_cwd(self):
        """M1: cwd に ID と同名のファイルがあっても ID 前方一致を優先する。"""
        cwd = self.root / "cwd"
        cwd.mkdir()
        # `--session eeee1111` と同名の無関係なファイルを cwd に置く。
        (cwd / "eeee1111").write_text("これはセッションログではない\n", encoding="utf-8")
        code, out, err = run_script(
            ["--project-dir", str(self.project_dir), "--session", "eeee1111", "--log-line"],
            cwd=cwd,
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        self.assertIn("calls=3", out)

    def test_looks_like_a_path_rules(self):
        for value in ("/abs/path.jsonl", "dir/name", "./x", "name.jsonl", "~/a.jsonl"):
            self.assertTrue(usage_report.looks_like_a_path(value), value)
        for value in ("72dbdbc8", "eeee1111-2222", "abc.def"):
            self.assertFalse(usage_report.looks_like_a_path(value), value)

    def test_relative_path_ending_in_jsonl_is_read_as_a_file(self):
        cwd = self.root / "cwd2"
        cwd.mkdir()
        write_session(cwd, "ffff7777-rel", basic_session_rows())
        code, out, err = run_script(
            ["--project-dir", str(self.root / "missing"), "--session", "ffff7777-rel.jsonl", "--log-line"],
            cwd=cwd,
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        self.assertIn("calls=3", out)

    def test_single_session_human_output_has_all_blocks(self):
        code, out = run_cli(self.base_argv() + ["--session", "eeee1111"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("常駐文脈長", out)
        self.assertIn("ツール呼び出し", out)
        self.assertIn("tool_result の分量", out)
        self.assertIn("サブエージェントの使用量は親ログに含まれない", out)

    def test_detail_output_distinguishes_the_two_input_figures(self):
        """M3: 非キャッシュ入力と新規入力(= new_in)を取り違えないこと。"""
        code, out = run_cli(self.base_argv() + ["--session", "eeee1111"])
        self.assertEqual(code, usage_report.EXIT_OK)
        metrics = analyze(self.path)
        detail = {}
        for line in out.splitlines():
            for label in ("非キャッシュ入力", "新規入力"):
                if line.strip().startswith(label):
                    detail[label] = line
        self.assertIn("非キャッシュ入力", detail)
        self.assertIn("新規入力", detail)
        self.assertIn(usage_report.fmt_short(metrics["input"]), detail["非キャッシュ入力"])
        self.assertIn(usage_report.fmt_short(metrics["new_in"]), detail["新規入力"])
        self.assertIn("new_in と同じ定義", detail["新規入力"])
        # log-line 側の new_in と同じ値を指している。
        _code, line_out = run_cli(self.base_argv() + ["--session", "eeee1111", "--log-line"])
        self.assertIn("new_in=%s" % usage_report.fmt_mega(metrics["new_in"]), line_out)


class SlugDerivationTest(unittest.TestCase):
    """M1: WIKI_VAULT_ROOT の slug 導出は resolve 前後の両方を候補にする。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_candidates_cover_pre_and_post_resolve_paths(self):
        candidates = usage_report.project_slug_candidates(
            Path("/tmp/my.vault"), Path("/private/tmp/my.vault")
        )
        self.assertIn("-tmp-my-vault", candidates)
        self.assertIn("-private-tmp-my-vault", candidates)
        # 同じ入力を重ねても候補は増えない。
        self.assertEqual(
            usage_report.project_slug_candidates(Path("/a/b"), Path("/a/b")), ["-a-b"]
        )
        self.assertEqual(usage_report.project_slug_candidates(None), [])

    def slug_of(self, path):
        return str(path).replace("/", "-").replace(".", "-")

    def test_env_var_slug_resolved_through_the_cli(self):
        """symlink 越しの WIKI_VAULT_ROOT でも slug が当たること。"""
        real_vault = self.root / "real" / "my.vault"
        real_vault.mkdir(parents=True)
        link = self.root / "link.vault"
        link.symlink_to(real_vault, target_is_directory=True)
        self.assertNotEqual(str(link.resolve()), str(link))

        claude_home = self.root / "claude"
        # resolve 前(symlink 側)のパスから作った slug でディレクトリを置く。
        project_dir = claude_home / "projects" / self.slug_of(link)
        project_dir.mkdir(parents=True)
        write_session(project_dir, "cccc9999-env", basic_session_rows())

        code, out, err = run_script(
            ["--claude-home", str(claude_home), "--latest", "--log-line"],
            env_overrides={"WIKI_VAULT_ROOT": str(link)},
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        self.assertIn("calls=3", out)

    def test_env_var_slug_from_the_resolved_path(self):
        """resolve 後のパスから作った slug でも当たること。"""
        real_vault = self.root / "real" / "my.vault"
        real_vault.mkdir(parents=True)
        link = self.root / "link.vault"
        link.symlink_to(real_vault, target_is_directory=True)

        claude_home = self.root / "claude"
        project_dir = claude_home / "projects" / self.slug_of(link.resolve())
        project_dir.mkdir(parents=True)
        write_session(project_dir, "cccc8888-env", basic_session_rows())

        code, out, err = run_script(
            ["--claude-home", str(claude_home), "--latest", "--log-line"],
            env_overrides={"WIKI_VAULT_ROOT": str(link)},
        )
        self.assertEqual(code, usage_report.EXIT_OK, err)
        self.assertIn("calls=3", out)

    def test_unmatched_vault_root_is_reported(self):
        claude_home = self.root / "claude"
        (claude_home / "projects").mkdir(parents=True)
        code, _out, err = run_script(
            ["--claude-home", str(claude_home), "--latest"],
            env_overrides={"WIKI_VAULT_ROOT": str(self.root / "nowhere")},
        )
        self.assertEqual(code, usage_report.EXIT_MISSING)
        self.assertIn("--project-dir", err)


# --------------------------------------------------------------------------
# 6. 壊れた入力への耐性
# --------------------------------------------------------------------------


class RobustnessTest(TempProject):
    def test_truncated_trailing_line_is_skipped(self):
        path = write_session(
            self.project_dir,
            "9999aaaa-trunc",
            basic_session_rows(),
            trailing_garbage='{"type": "assistant", "message": {"id": "msg_4", "usa',
        )
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 3)

    def test_missing_usage_is_treated_as_zero(self):
        rows = [
            assistant_row("msg_no_usage", [{"type": "text", "text": "x"}], None, 0),
            assistant_row("msg_empty_usage", [{"type": "text", "text": "y"}], {}, 1),
            assistant_row(
                "msg_partial", [{"type": "text", "text": "z"}], {"output_tokens": 7}, 2
            ),
        ]
        path = write_session(self.project_dir, "9999bbbb-nousage", rows)
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 3)
        self.assertEqual(metrics["output"], 7)
        self.assertEqual(metrics["thinking"], 0)
        self.assertEqual(metrics["context"]["max"], 0)
        self.assertEqual(metrics["cost"]["estimated_usd"], round(7 / 1e6 * 15, 4))

    def test_empty_file_does_not_crash(self):
        path = Path(self.project_dir) / "9999cccc-empty.jsonl"
        path.write_text("", encoding="utf-8")
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 0)
        self.assertEqual(metrics["context"]["avg"], 0)
        self.assertEqual(metrics["cost"]["estimated_usd"], 0.0)
        code, out = run_cli(self.base_argv() + ["--session", "9999cccc"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("API 呼び出し 0 回", out)

    def test_empty_file_log_line_does_not_crash(self):
        path = Path(self.project_dir) / "9999dddd-empty.jsonl"
        path.write_text("\n\n", encoding="utf-8")
        code, out = run_cli(self.base_argv() + ["--session", "9999dddd", "--log-line"])
        self.assertEqual(code, usage_report.EXIT_OK)
        self.assertIn("calls=0", out)

    def test_unparseable_timestamps_and_string_content(self):
        rows = [
            user_row("just text", 0),
            {
                "type": "assistant",
                "uuid": "u-bad-ts",
                "timestamp": "not-a-timestamp",
                "message": {
                    "role": "assistant",
                    "id": "msg_bad_ts",
                    "model": "claude-sonnet-5",
                    "content": "plain string content",
                    "usage": usage(0, 10, 0, 1),
                },
            },
        ]
        path = write_session(self.project_dir, "9999eeee-badts", rows)
        metrics = analyze(path)
        self.assertEqual(metrics["api_calls"], 1)
        self.assertIsNone(metrics["start"])
        self.assertIsNone(metrics["duration_sec"])
        self.assertEqual(metrics["gaps_over_5min"], 0)

    def test_non_session_row_types_are_ignored(self):
        rows = [
            {"type": "mode", "mode": "normal"},
            {"type": "attachment", "content": {"type": "text", "text": "x"}},
            {"type": "cost-state", "value": 1},
        ]
        path = write_session(self.project_dir, "9999ffff-other", rows)
        self.assertEqual(analyze(path)["api_calls"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
