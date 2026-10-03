#!/usr/bin/env python3
"""address-backfill.py — `address:` を持たない既存 wiki ページに c- 番号を後付けする。

DragonScale 導入後も、`wiki-page-write.py` を通らずに作られたページが残る
(導入前の手書き ingest、ヘルパーを通らない旧手順など)。
lint はこれを post-rollout 欠落として報告するだけなので、人間が補填を
決めたときに本スクリプトを使う。

採番は `wiki-page-write.py` と同じ経路(`allocate-address.sh`、
WIKI_ALLOCATE_OK=1)で行い、ページごとに `wiki_lock` を取って
アトミックに書く。番号は `created:` → パスの順に振るので、
採番順は作成順におおむね沿う(厳密な作成順ではない)。

対象外: meta / fold / ask / brief、カタログ類、`wiki/meta/`・`wiki/folds/`・
`wiki/asks/`・`wiki/briefs/`。frontmatter が壊れているページは報告して飛ばす。

使い方:
  python3 scripts/address-backfill.py            # 対象を数えるだけ(dry-run)
  python3 scripts/address-backfill.py --write    # 採番して書き込む
  python3 scripts/address-backfill.py --write --paths-out /tmp/changed.txt
"""

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))
import wiki_lock  # noqa: E402

_spec = importlib.util.spec_from_file_location("wiki_page_write", _SCRIPTS / "wiki-page-write.py")
wpw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wpw)

VAULT = wpw.VAULT_ROOT
SKIP_DIRS = ("wiki/meta/", "wiki/folds/", "wiki/asks/", "wiki/briefs/", "wiki/log/")
SKIP_NAMES = {"_index.md", "index.md", "log.md", "hot.md", "overview.md", "dashboard.md",
              "Wiki Map.md", "getting-started.md", "CLAUDE.md"}
SKIP_TYPES = {"meta", "fold", "ask", "brief"}
ANY_ADDRESS_RE = re.compile(r"^address:", re.M)


def targets():
    out = []
    for dp, _, fns in os.walk(VAULT / "wiki"):
        for fn in fns:
            if not fn.endswith(".md") or fn in SKIP_NAMES:
                continue
            path = Path(dp) / fn
            rel = path.relative_to(VAULT).as_posix()
            if rel.startswith(SKIP_DIRS) or path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8")
            parts = wpw.split_frontmatter(text)
            if parts is None:
                out.append((None, rel, "frontmatter が読めない"))
                continue
            keys = wpw.frontmatter_keys(parts[0])
            if wpw.unquote(str(keys.get("type", ""))) in SKIP_TYPES:
                continue
            # 形式違反の address も「持っている」扱いにして上書きしない(lint が別途報告する)
            if ANY_ADDRESS_RE.search("\n".join(parts[0])):
                continue
            out.append((wpw.unquote(str(keys.get("created", ""))), rel, None))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="採番して書き込む(省略時は dry-run)")
    ap.add_argument("--paths-out", help="書き換えたパスを 1 行ずつ書き出すファイル")
    args = ap.parse_args()

    found = targets()
    broken = [rel for _, rel, err in found if err]
    todo = sorted((c or "9999", rel) for c, rel, err in found if not err)
    print("対象 %d 頁、frontmatter 不良で除外 %d 頁" % (len(todo), len(broken)))
    for rel in broken:
        print("  skip: %s" % rel)
    if not args.write:
        return 0

    done, failed = [], []
    for _, rel in todo:
        token = wiki_lock.acquire(rel)
        if not token:
            failed.append((rel, "ロック取得失敗"))
            continue
        try:
            path = VAULT / rel
            text = path.read_text(encoding="utf-8")
            if ANY_ADDRESS_RE.search("\n".join(wpw.split_frontmatter(text)[0])):
                continue  # 並行して誰かが付けた
            addr, err = wpw.allocate_address()
            if addr is None:
                failed.append((rel, err))
                break  # 採番器の故障は全体を止める
            wpw.atomic_write(path, wpw.insert_address(text, addr))
            done.append(rel)
        finally:
            wiki_lock.release(rel, token)

    print("採番 %d 頁、失敗 %d 頁" % (len(done), len(failed)))
    for rel, why in failed:
        print("  fail: %s — %s" % (rel, why))
    if args.paths_out:
        Path(args.paths_out).write_text("\n".join(done) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
