#!/usr/bin/env bash
# Wiki Lens だけを Obsidian vault に置く。
set -euo pipefail

usage() {
  echo "使い方: $0 <vault-root>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
VAULT="$(cd "$1" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$VAULT/.obsidian/plugins/wiki-lens"

mkdir -p "$DEST"
# 配布物。開発用の node_modules / 実行時 data.json は置かない。
while IFS= read -r rel; do
  src="$HERE/$rel"
  dest="$DEST/$rel"
  mkdir -p "$(dirname "$dest")"
  cp -p "$src" "$dest"
done < <(
  cd "$HERE"
  find . \( -path './node_modules' -o -path './node_modules/*' -o -name data.json -o -path './.vite' -o -path './.vite/*' -o -name 'install-to-vault.sh' \) -prune -o -type f -print | sed 's|^\./||'
)

python3 - "$VAULT/.obsidian/community-plugins.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
ids = []
if path.exists():
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        ids = raw
if "wiki-lens" not in ids:
    ids.append("wiki-lens")
path.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
PY

echo "Wiki Lens を $DEST に置いた。"
echo "Obsidian を再読み込みし、コミュニティプラグインで有効化する（community-plugins.json には wiki-lens を追記済み）。"
