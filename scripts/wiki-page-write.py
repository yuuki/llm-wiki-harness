#!/usr/bin/env python3
"""wiki-page-write.py — 新規 wiki ページの作成を 1 呼び出しに束ねる。

従来の手順「`allocate-address.sh` で採番 → `wiki-lock.sh acquire` →
Write ツールで書き込み → `wiki-lock.sh release`」は Bash 3 回 + Write 1 回の
計 4 ターンを消費する。入力トークンの大半はキャッシュ読み戻し
(呼び出し時点の常駐文脈長の総和)なので、ターン数の削減がそのまま
トークン削減になる。本スクリプトは採番・検証・ロック・アトミック書き込みを
1 プロセスで完結させ、`--batch` で複数ページを 1 ターンにまとめる。

ロックは `wiki_lock.acquire()` 経由で `scripts/wiki-lock.sh` と同じ
ロックファイル(`.vault-meta/locks/<sha1(相対パス)>.lock`)を使う。
そのため `wiki-lock.sh` を直接使う他のエージェントとも排他が成立する。

使い方:
  python3 scripts/wiki-page-write.py wiki/concepts/Foo.md --content-file /tmp/foo.md
  cat /tmp/foo.md | python3 scripts/wiki-page-write.py wiki/concepts/Foo.md
  python3 scripts/wiki-page-write.py --batch /tmp/pages.json

`--batch` の SPEC.json は操作のリスト。キーは
`path`(必須)・`content_file` または `content`・`address`・`force`。

  [{"path": "wiki/sources/@2026__X__Y.md", "content_file": "/tmp/a.md"},
   {"path": "wiki/entities/Z.md", "content": "---\\ntype: entity\\n..."}]

環境変数 `WIKI_VAULT_ROOT` で vault ルートを差し替えられる(試験用)。

終了コード:
  0  — 全ページ成功
  2  — 使い方の誤り
  65 — 検証違反・既存ファイル・入出力エラー(ロック失敗なし)
  75 — ロック取得失敗を含む(EX_TEMPFAIL 相当。他ページの処理は続行する)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import wiki_lock

VAULT_ROOT = Path(
    os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent
).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DATA = 65
EXIT_LOCKED = 75

# `--lock-wait-sec 0` を素のまま wiki_lock に渡すと 1 回も試行せず必ず失敗する。
# 0 は「1 回だけ試す」の意味に読み替え、この下限に丸める。
MIN_LOCK_WAIT_SEC = 0.1

ADDRESS_RE = re.compile(r"^c-\d{6}$")
DATE_TAG_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
REQUIRED_KEYS = ("type", "title", "created", "updated", "date", "tags")

# カタログは専用スクリプトの領分(conventions §11 / wiki/CLAUDE.md 鉄則 8)。
# `wiki/overview.md` は含めない。wiki-catalog.py に対応する副命令が無く、
# 通常ページとして書くのが正しい経路である。
CATALOG_FILES = ("wiki/index.md", "wiki/hot.md", "wiki/log.md")
CATALOG_HINT = (
    "カタログファイルは wiki-catalog.py で更新する"
    "(prepend-log / prepend-hot / prepend-master / add-catalog-line)"
)


def log(msg):
    print(msg, file=sys.stderr)


def normalize_rel(raw):
    """vault 相対 posix パスを返す。vault 外・`..`・非 .md は None。"""
    if raw is None:
        return None, "パスが空である"
    text = str(raw).strip()
    if not text:
        return None, "パスが空である"
    if text.startswith("/"):
        try:
            rel = Path(text).resolve().relative_to(VAULT_ROOT).as_posix()
        except ValueError:
            return None, "パスが vault の外を指している: %s" % text
    else:
        rel = text
    if ".." in Path(rel).parts:
        return None, "パスに '..' を含められない: %s" % text
    rel = Path(rel).as_posix()
    if not rel.endswith(".md"):
        return None, "wiki ページは .md でなければならない: %s" % text
    parts = Path(rel).parts
    if not parts or parts[0] != "wiki":
        return None, "wiki/ 配下のパスだけを受け付ける: %s" % text
    if rel in CATALOG_FILES or Path(rel).name == "_index.md":
        return None, "%s はカタログである。%s" % (rel, CATALOG_HINT)
    # シンボリックリンクを辿った先が wiki/ の外なら、ロック層で 75 になる前に
    # パスエラーとして落とす(wiki-lock.sh の validate_path と同じ判定)。
    real = Path(os.path.realpath(str(VAULT_ROOT / rel)))
    wiki_real = Path(os.path.realpath(str(VAULT_ROOT / "wiki")))
    try:
        real.relative_to(wiki_real)
    except ValueError:
        return None, "シンボリックリンクを解決すると wiki/ の外を指す: %s" % text
    return rel, None


def frontmatter_error(text):
    """frontmatter が取れない理由を事実に合わせて述べる。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "frontmatter が `---` で始まっていない(conventions §2)"
    return "frontmatter の閉じ `---` が無い(conventions §2)"


def allocate_script():
    """書き込み先 vault 直下の allocate-address.sh だけを使う。

    `allocate-address.sh` は自分の置き場所からカウンタのパスを決める。
    本スクリプトの同居ディレクトリへフォールバックすると、
    `WIKI_VAULT_ROOT` で別の vault を指しているときに無関係な vault の
    カウンタを消費してしまうので、そのフォールバックは持たない。
    """
    cand = VAULT_ROOT / "scripts" / "allocate-address.sh"
    return cand if cand.is_file() else None


def dragonscale_enabled():
    script = allocate_script()
    if script is None or not os.access(str(script), os.X_OK):
        return None
    if not (VAULT_ROOT / ".vault-meta").is_dir():
        return None
    return script


def allocate_address():
    """`c-NNNNNN` を採番して返す。失敗時は (None, 理由)。"""
    script = dragonscale_enabled()
    if script is None:
        return None, "DragonScale が無効(allocate-address.sh または .vault-meta が無い)"
    try:
        env = os.environ.copy()
        env["WIKI_ALLOCATE_OK"] = "1"
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=str(VAULT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, "allocate-address.sh を起動できない: %s" % exc
    if proc.returncode != 0:
        return None, "allocate-address.sh が失敗した(%d): %s" % (
            proc.returncode, (proc.stderr or "").strip())
    value = (proc.stdout or "").strip().splitlines()
    value = value[-1].strip() if value else ""
    if not ADDRESS_RE.match(value):
        return None, "allocate-address.sh の出力が address 形式でない: %r" % value
    return value, None


def split_frontmatter(text):
    """(frontmatter 行, 本文行, 終端 index) を返す。frontmatter が無ければ None。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], lines[i + 1:], i
    return None


def frontmatter_keys(fm_lines):
    keys = {}
    key = None
    for line in fm_lines:
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item is not None and key is not None:
            keys.setdefault(key, []).append(item.group(1).strip())
            continue
        kv = re.match(r"^([A-Za-z0-9_/-]+):\s*(.*)$", line)
        if kv is None:
            continue
        key = kv.group(1)
        raw = kv.group(2).strip()
        if raw in ("", "[]"):
            keys.setdefault(key, [])
        else:
            keys[key] = raw
    return keys


def unquote(value):
    s = str(value).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def count_h1(body_lines):
    fence = None
    count = 0
    for line in body_lines:
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        if line.startswith("# "):
            count += 1
    return count


def check_content(rel, text):
    """(errors, warnings) を返す。conventions §2・§3・§9 の軽い検証。"""
    errors = []
    warnings = []
    parts = split_frontmatter(text)
    if parts is None:
        errors.append(frontmatter_error(text))
        return errors, warnings
    fm_lines, body_lines, _ = parts
    fm = frontmatter_keys(fm_lines)
    for key in REQUIRED_KEYS:
        if key not in fm:
            errors.append("frontmatter に `%s:` が無い(conventions §2)" % key)
    tags = fm.get("tags")
    if isinstance(tags, list):
        if not tags:
            errors.append("`tags` が空である。先頭に日付タグ YYYY/MM/DD が必要(conventions §2)")
        elif not DATE_TAG_RE.match(unquote(tags[0])):
            errors.append(
                "`tags` の先頭が日付タグ YYYY/MM/DD でない: %r(conventions §2)" % tags[0])
    elif tags is not None:
        errors.append("`tags` は `- item` 形式のリストで書く(conventions §2 ルール 4)")
    if unquote(fm.get("type") or "") == "source" \
            and unquote(fm.get("source_type") or "") == "book":
        if unquote(fm.get("publish") or "").lower() != "false":
            errors.append("書籍の章 source には `publish: false` が必須(conventions §9)")
    h1 = count_h1(body_lines)
    if h1 == 0:
        errors.append("H1 見出し(`# タイトル`)が無い")
    elif h1 > 1:
        errors.append("H1 見出しが %d 個ある(1 つにする)" % h1)
    if len(text.split("\n")) > 300 and "/concepts/" not in rel:
        warnings.append("300 行を超えている(conventions §7: source / entity は分割を検討する)")
    return errors, warnings


def has_address(text):
    parts = split_frontmatter(text)
    if parts is None:
        return False
    return "address" in frontmatter_keys(parts[0])


def insert_address(text, address):
    """`---` 直後の最初のキーとして `address:` を挿入する。"""
    lines = text.split("\n")
    lines.insert(1, "address: %s" % address)
    return "\n".join(lines)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_address_map(entries):
    """`.raw/.manifest.json` の `address_map` に path→address を記録する。

    既定で無効(`--record-address-map` が必要)なのは、manifest を
    ingest 本体・図表ヘルパーなど他プロセスが頻繁に編集するためである。
    ページ書き込みのたびに manifest を掴むと、本来無関係な書き込み同士が
    manifest ロックで直列化され、競合と待ちを無用に増やす。記録が必要な
    ときだけ、まとめて 1 回呼ぶ運用にしている。
    """
    if not entries:
        return None
    rel = ".raw/.manifest.json"
    path = VAULT_ROOT / rel
    try:
        token = wiki_lock.acquire(rel)
    except RuntimeError as exc:
        return "address_map を記録できない(ロック失敗): %s" % exc
    try:
        data = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError) as exc:
                return "manifest を読めない: %s" % exc
        if not isinstance(data, dict):
            return "manifest の最上位が JSON オブジェクトでない"
        amap = data.get("address_map")
        if not isinstance(amap, dict):
            amap = {}
        amap.update(entries)
        data["address_map"] = amap
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        wiki_lock.release(rel, token)
    return None


def read_content(spec):
    if "content" in spec and spec["content"] is not None:
        return str(spec["content"]), None
    src = spec.get("content_file")
    if src is None:
        return None, "content も content_file も指定されていない"
    try:
        return Path(src).read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, "content_file を読めない: %s" % exc


def write_one(spec, opts):
    """1 ページを書く。(結果 dict, exit コード寄与) を返す。

    既存判定・`action` の確定・address の採番は**ロックの内側**で行う。
    ロックの外で存在を判定すると、同一の未存在パスへ 2 プロセスが同時に
    入って両方が `created` を返し、後着が先着を黙って上書きする。
    採番も同じ理由で内側に置く。拒否される経路でアドレスを消費すると、
    どのページにも属さない孤児アドレスがカウンタに残る。
    """
    rel, err = normalize_rel(spec.get("path"))
    if rel is None:
        return {"path": spec.get("path"), "error": err}, EXIT_DATA

    text, err = read_content(spec)
    if err is not None:
        return {"path": rel, "error": err}, EXIT_DATA
    if not text.strip():
        return {"path": rel, "error": "内容が空である"}, EXIT_DATA

    address = spec.get("address") or opts.address
    if address is not None:
        address = str(address).strip()
        if not ADDRESS_RE.match(address):
            return {"path": rel,
                    "error": "--address の形式が c-NNNNNN でない: %r" % address}, EXIT_DATA

    warnings = []
    allocate = False
    has_fm = split_frontmatter(text) is not None
    if not has_fm:
        if opts.check:
            return {"path": rel, "error": frontmatter_error(text)}, EXIT_DATA
        warnings.append("frontmatter が無いため address 挿入と検証を省略した")
        address = None
    else:
        if has_address(text):
            if address is not None:
                warnings.append("内容に既に address があるため --address を無視した")
            address = None
            allocate = False
        else:
            allocate = address is None and opts.allocate
        # 検証は採番より前に済ませ、違反で拒否するときにアドレスを消費しない。
        errors, checks = check_content(rel, text)
        warnings.extend(checks)
        if errors:
            if opts.check:
                return {"path": rel, "error": "検証違反", "violations": errors,
                        "warnings": warnings}, EXIT_DATA
            warnings.extend("検証違反(--no-check で降格): %s" % e for e in errors)

    path = VAULT_ROOT / rel
    force = bool(spec.get("force", opts.force))
    try:
        token = wiki_lock.acquire(rel)
    except RuntimeError as exc:
        return {"path": rel, "error": "ロック取得に失敗した: %s" % exc,
                "warnings": warnings}, EXIT_LOCKED
    try:
        exists = path.exists()
        if exists and not force:
            return {"path": rel,
                    "error": "既存ファイルがある(上書きするなら --force)",
                    "warnings": warnings}, EXIT_DATA
        action = "overwritten" if exists else "created"
        if has_fm and allocate:
            allocated, err = allocate_address()
            if allocated is None:
                warnings.append("address を採番しなかった: %s" % err)
            else:
                address = allocated
        if address is not None:
            text = insert_address(text, address)
        try:
            atomic_write(path, text)
        except OSError as exc:
            return {"path": rel, "error": "書き込みに失敗した: %s" % exc,
                    "warnings": warnings}, EXIT_DATA
    finally:
        wiki_lock.release(rel, token)

    payload = text if text.endswith("\n") else text + "\n"
    result = {"path": rel, "address": address, "action": action,
              "lines": len(payload.rstrip("\n").split("\n"))}
    if warnings:
        result["warnings"] = warnings
    return result, EXIT_OK


def load_batch(spec_path):
    try:
        data = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except OSError as exc:
        log("ERR: --batch を読めない: %s" % exc)
        return None
    except ValueError as exc:
        log("ERR: --batch が JSON として不正: %s" % exc)
        return None
    if not isinstance(data, list) or not data:
        log("ERR: --batch は操作の非空リストでなければならない")
        return None
    for item in data:
        if not isinstance(item, dict):
            log("ERR: --batch の要素はオブジェクトでなければならない")
            return None
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="新規 wiki ページの採番・検証・ロック・書き込みを 1 呼び出しで行う。")
    parser.add_argument("path", nargs="?", help="vault 相対パス(wiki/ 配下の .md)")
    parser.add_argument("--content-file", help="本文のファイル。省略時は標準入力")
    parser.add_argument("--batch", help="複数ページの SPEC.json")
    parser.add_argument("--force", action="store_true", help="既存ファイルを上書きする")
    parser.add_argument("--address", help="事前採番済みの c-NNNNNN を使う")
    parser.add_argument("--no-allocate", dest="allocate", action="store_false",
                        help="address の採番を省略する")
    parser.add_argument("--no-check", dest="check", action="store_false",
                        help="検証違反を error から warning に降格する")
    parser.add_argument("--quiet", action="store_true", help="成功時は path だけを出力する")
    parser.add_argument("--record-address-map", action="store_true",
                        help=".raw/.manifest.json の address_map に path→address を記録する")
    parser.add_argument("--lock-wait-sec", type=float, default=None,
                        help="ロック待ちの上限秒(既定は wiki_lock の %d 秒)。"
                             "0 は「1 回だけ試す」の意味で %s 秒に丸める"
                             % (wiki_lock.LOCK_WAIT_SEC, MIN_LOCK_WAIT_SEC))
    args = parser.parse_args(argv)

    if args.lock_wait_sec is not None:
        if args.lock_wait_sec < 0:
            log("ERR: --lock-wait-sec は 0 以上である")
            return EXIT_USAGE
        wiki_lock.LOCK_WAIT_SEC = max(MIN_LOCK_WAIT_SEC, args.lock_wait_sec)

    if args.batch and args.path:
        log("ERR: --batch と位置引数のパスは併用できない")
        return EXIT_USAGE
    if args.batch and args.content_file:
        log("ERR: --batch と --content-file は併用できない")
        return EXIT_USAGE

    if args.batch:
        specs = load_batch(args.batch)
        if specs is None:
            return EXIT_USAGE
    else:
        if not args.path:
            log("ERR: パスか --batch のどちらかが必要である")
            return EXIT_USAGE
        spec = {"path": args.path}
        if args.content_file:
            spec["content_file"] = args.content_file
        else:
            spec["content"] = sys.stdin.read()
        specs = [spec]

    status = EXIT_OK
    address_entries = {}
    for spec in specs:
        result, code = write_one(spec, args)
        if code == EXIT_LOCKED or (code == EXIT_DATA and status != EXIT_LOCKED):
            status = code
        if result.get("address"):
            address_entries[result["path"]] = result["address"]
        if args.quiet and "error" not in result and not result.get("warnings"):
            print(result["path"])
        else:
            print(json.dumps(result, ensure_ascii=False))

    if args.record_address_map:
        err = record_address_map(address_entries)
        if err is not None:
            log("WARN: %s" % err)
            if status == EXIT_OK:
                status = EXIT_DATA
    return status


if __name__ == "__main__":
    sys.exit(main())
