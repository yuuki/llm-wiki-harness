#!/usr/bin/env python3
"""wiki-manifest.py — `.raw/.manifest.json` の原本エントリを 1 件ずつ読み書きする。

manifest は 2 MB を超えるので、全文を Read したり手で書き戻したりしない。
原本エントリの正の形は、最上位に `.raw/...` をキーとして置く形である
(`wiki-doctor.py` の manifest 検査も `address_map` も同じ階層を前提にする)。
skill の例が `"sources": {...}` で包んでいた時期があり、その下にもエントリが
残っている。`get` は両方を見る。`put` は最上位へ書き、同じキーが `sources`
配下にあれば取り除く。`normalize` は `sources` 配下をまとめて最上位へ移す。

書き込みは `wiki-lock.sh` と同じロックの内側で行う。ロックを取らずに manifest を
書き換える書き手もいるので、読んでから書くまでの間にファイルが変わっていたら
読み直す。3 回続けて変わったら、書かずに終了 75 で止まる。

使い方:
  python3 scripts/wiki-manifest.py get ".raw/papers/<slug>.pdf"
  python3 scripts/wiki-manifest.py put ".raw/papers/<slug>.pdf" --json '{"hash": "...", "ingested_at": "YYYY-MM-DD", "pages_created": [...], "pages_updated": [...]}'
  python3 scripts/wiki-manifest.py put ".raw/books/<slug>" --json-file "$TMPDIR/entry.json"
  python3 scripts/wiki-manifest.py normalize            # 件数を出すだけ(dry-run)
  python3 scripts/wiki-manifest.py normalize --write

`put` は既存エントリに欄を上書きで足す(渡さなかった欄は残る)。

終了コード: 0 成功 / 1 manifest を読めない・引数の誤り / 3 get でキーが無い / 75 他の書き手と競合
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

import wiki_lock  # noqa: E402

REL = ".raw/.manifest.json"
EXIT_ABSENT = 3
EXIT_BUSY = 75
RETRIES = 3


def manifest_path():
    return wiki_lock.vault_root() / REL


def load(path):
    """(dict, 元のテキスト, mtime_ns)。ファイルが無ければ空の manifest として扱う。"""
    if not path.is_file():
        return {}, "", None
    stat = path.stat()
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("manifest の最上位が JSON オブジェクトでない")
    return data, raw, stat.st_mtime_ns


def lookup(data, key):
    if isinstance(data.get(key), dict):
        return data[key]
    nested = data.get("sources")
    if isinstance(nested, dict) and isinstance(nested.get(key), dict):
        return nested[key]
    return None


def put_entry(data, key, fields):
    """最上位へ書く。`sources` 配下に同じキーがあれば、その欄を引き継いで取り除く。"""
    out = dict(data)
    nested = out.get("sources")
    base = {}
    if isinstance(nested, dict) and key in nested:
        if isinstance(nested[key], dict):
            base = dict(nested[key])
        rest = {k: v for k, v in nested.items() if k != key}
        if rest:
            out["sources"] = rest
        else:
            del out["sources"]
    if isinstance(out.get(key), dict):
        base = dict(out[key])
    base.update(fields)
    out[key] = base
    return out


def normalize(data):
    """(正規化後の dict, 移した件数, 衝突の一覧) を返す。data は変更しない。"""
    nested = data.get("sources")
    if not isinstance(nested, dict):
        return data, 0, []
    out = {k: v for k, v in data.items() if k != "sources"}
    moved, conflicts, leftover = 0, [], {}
    for key, entry in nested.items():
        if not key.startswith(".raw/"):
            leftover[key] = entry
            continue
        if key in out:
            keep_nested = _ingested_at(entry) > _ingested_at(out[key])
            conflicts.append((key, "sources" if keep_nested else "top"))
            if not keep_nested:
                continue
        out[key] = entry
        moved += 1
    if leftover:
        out["sources"] = leftover
    return out, moved, conflicts


def _ingested_at(entry):
    return str(entry.get("ingested_at") or "") if isinstance(entry, dict) else ""


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def locked_update(transform):
    """transform(data) -> 新しい dict(変更なしなら None)。ロックの内側で読み、変わっていなければ書く。"""
    path = manifest_path()
    token = wiki_lock.acquire(REL)
    try:
        for _ in range(RETRIES):
            data, raw, mtime = load(path)
            out = transform(data)
            if out is None:
                return 0
            current = path.stat().st_mtime_ns if path.is_file() else None
            if current != mtime:
                continue  # 読んだあとに他の書き手が更新した。読み直す
            # 既存の書式(indent=2、末尾改行の有無)を保つ
            atomic_write(path, json.dumps(out, ensure_ascii=False, indent=2) + ("\n" if raw.endswith("\n") else ""))
            return 0
        print("manifest が書き込み中に更新され続けた。書かずに止める。再実行する", file=sys.stderr)
        return EXIT_BUSY
    finally:
        wiki_lock.release(REL, token)


def cmd_get(args):
    data, _, _ = load(manifest_path())
    entry = lookup(data, args.key)
    if entry is None:
        print("manifest に無い: %s" % args.key, file=sys.stderr)
        return EXIT_ABSENT
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def cmd_put(args):
    if not args.key.startswith(".raw/"):
        print("キーは `.raw/...` の vault 相対パスにする: %s" % args.key, file=sys.stderr)
        return 1
    text = Path(args.json_file).read_text(encoding="utf-8") if args.json_file else args.json
    fields = json.loads(text)
    if not isinstance(fields, dict):
        print("エントリは JSON オブジェクトで渡す", file=sys.stderr)
        return 1
    rc = locked_update(lambda data: put_entry(data, args.key, fields))
    if rc == 0:
        print("manifest put: %s" % args.key)
    return rc


def cmd_normalize(args):
    data, _, _ = load(manifest_path())
    _, moved, conflicts = normalize(data)
    top = sum(1 for k in data if k.startswith(".raw/"))
    print("最上位 %d 件、sources 配下から移す %d 件、衝突 %d 件" % (top, moved, len(conflicts)))
    for key, kept in conflicts:
        print("  衝突: %s → %s 側を残す" % (key, kept))
    if not args.write:
        print("dry-run。書き込むには --write")
        return 0
    if moved == 0 and not conflicts:
        print("変更なし")
        return 0

    def transform(current):
        out, n, c = normalize(current)
        return out if (n or c) else None

    rc = locked_update(transform)
    if rc == 0:
        print("書き込んだ: %s" % REL)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="1 件のエントリを JSON で出す(無ければ終了 3)")
    g.add_argument("key")
    g.set_defaults(func=cmd_get)
    p = sub.add_parser("put", help="1 件のエントリを最上位へ書く(既存の欄に上書きで足す)")
    p.add_argument("key")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="エントリの JSON オブジェクト")
    src.add_argument("--json-file", help="エントリの JSON を書いたファイル")
    p.set_defaults(func=cmd_put)
    n = sub.add_parser("normalize", help="sources 配下のエントリを最上位へ移す")
    n.add_argument("--write", action="store_true", help="移して書き込む(省略時は dry-run)")
    n.set_defaults(func=cmd_normalize)
    args = ap.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print("manifest を処理できない: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
