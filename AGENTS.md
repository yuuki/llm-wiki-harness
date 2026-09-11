# AGENTS.md

このリポジトリは LLM wiki の**ハーネス**である。知識ページ（論文要約・概念本文）は含まない。

作業前に [`README.md`](README.md) を読む。vault へ入れたあとの運用は、コピー先の `wiki/CLAUDE.md` と `wiki/meta/conventions.md` が正本である。雛形は [`templates/wiki-CLAUDE.md`](templates/wiki-CLAUDE.md) と [`conventions/`](conventions/) にある。

skill・スクリプト・規約・`install.sh`・wiki-lens の利用者向け差分は、実装と同時に [`CHANGELOG.md`](CHANGELOG.md) の `[Unreleased]` へ追記する。知識ページは対象外。タグを切るときだけ `[Unreleased]` をバージョン節へ移す。

応答は日本語でよい。規約の常体・用語寄せは `conventions/japanese-style.md` に従う。
