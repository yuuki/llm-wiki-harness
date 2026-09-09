#!/usr/bin/env bash
# vault へハーネスを重ねる。知識ページは作らない（空の骨格だけ）。
set -euo pipefail

usage() {
  echo "使い方: $0 <vault-root>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
VAULT="$(cd "$1" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"

copy_dir() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  cp -R "$src/." "$dest/"
}

echo "vault: $VAULT"

copy_dir "$HERE/skills" "$VAULT/.claude/skills"
mkdir -p "$VAULT/.agents/skills"
copy_dir "$HERE/skills/wiki-refactor" "$VAULT/.agents/skills/wiki-refactor"

mkdir -p "$VAULT/scripts" "$VAULT/bin"
cp -R "$HERE/scripts/." "$VAULT/scripts/"
cp -R "$HERE/bin/." "$VAULT/bin/"
chmod +x "$VAULT/scripts/"*.sh "$VAULT/bin/"*.sh 2>/dev/null || true

mkdir -p "$VAULT/wiki/meta" \
  "$VAULT/wiki/sources" \
  "$VAULT/wiki/entities" \
  "$VAULT/wiki/concepts" \
  "$VAULT/wiki/questions" \
  "$VAULT/wiki/surveys" \
  "$VAULT/.raw/papers" \
  "$VAULT/.raw/books" \
  "$VAULT/.raw/theses" \
  "$VAULT/.raw/slides" \
  "$VAULT/.raw/articles"

cp "$HERE/conventions/"*.md "$VAULT/wiki/meta/"

if [[ ! -f "$VAULT/wiki/CLAUDE.md" ]]; then
  cp "$HERE/templates/wiki-CLAUDE.md" "$VAULT/wiki/CLAUDE.md"
fi

for name in overview.md index.md log.md hot.md; do
  if [[ ! -f "$VAULT/wiki/$name" ]]; then
    cp "$HERE/templates/wiki-scaffold/$name" "$VAULT/wiki/$name"
  fi
done
for sub in sources entities concepts; do
  if [[ ! -f "$VAULT/wiki/$sub/_index.md" ]]; then
    cp "$HERE/templates/wiki-scaffold/_index.md" "$VAULT/wiki/$sub/_index.md"
  fi
done

mkdir -p "$VAULT/.vault-meta"
if [[ ! -f "$VAULT/.vault-meta/mode.json" ]]; then
  cp "$HERE/templates/vault-meta/mode.json" "$VAULT/.vault-meta/mode.json"
fi
if [[ ! -f "$VAULT/.vault-meta/transport.json" ]]; then
  cp "$HERE/templates/vault-meta/transport.json" "$VAULT/.vault-meta/transport.json"
fi
if [[ ! -f "$VAULT/.vault-meta/address-counter.txt" ]]; then
  echo 1 > "$VAULT/.vault-meta/address-counter.txt"
fi

bash "$HERE/plugins/wiki-lens/install-to-vault.sh" "$VAULT"

echo "完了。次に vault で claude-obsidian を入れ、wiki-retrieve が要るなら bin/setup-retrieve.sh を走らせる。"
