---
name: wiki-refactor
description: Refactor and maintain the Obsidian LLM wiki layer, especially wiki/concepts. Use this skill whenever the user asks to refactor, consolidate, split, normalize, lint, health-check, or reorganize wiki concepts; merge duplicate concepts; decompose oversized concepts; repair concept frontmatter/headings/sources; sync wiki indexes/logs; or apply Karpathy-style compiled wiki knowledge maintenance.
---

# Wiki Refactor

Refactor the vault's LLM wiki layer as accumulated, compiled knowledge. The default target is `wiki/concepts/`. Catalog / log / index updates go through `wiki-catalog.py` only (do not Read/Edit `wiki/index.md`, `_index.md`, or `wiki/log.md`). Treat raw/source notes as evidence and concept pages as cross-source synthesis.

## Required Pre-Read

Before modifying or auditing the wiki, read these files in order:

1. `CLAUDE.md`
2. `wiki/CLAUDE.md`
3. `wiki/meta/conventions.md`
4. `wiki/meta/token-discipline.md`

Also use `obsidian-markdown` when creating or editing Obsidian Markdown, and respect local git and dirty-worktree instructions.

Do not read `wiki/index.md`, `wiki/hot.md`, `wiki/log.md`, or `_index.md` files by default. For discovery and reading, use `wiki-resolve.py`, `wiki-excerpt.py`, and `wiki-concept-stats.py` instead.

## Operating Model

Use Karpathy's LLM wiki idea as the organizing principle:

- Raw sources are immutable evidence, not workspaces for cleanup.
- The wiki is compiled knowledge that should improve over time, not a temporary RAG retrieval cache.
- Source pages preserve source-specific claims and provenance.
- Entity pages describe people, organizations, systems, datasets, repositories, or products.
- Concept pages synthesize definitions, relationships, contradictions, and unresolved questions across sources.

For this vault, concept pages should not become single-source detailed notes. If a concept mostly lists facts from one source, compress it and point back to the source page.

## Scope Boundaries

Default editable scope:

- `wiki/concepts/`
- catalog / log / index lines via `wiki-catalog.py` only (do not Read/Edit those files)
- skill/test files only when the user is explicitly creating or improving this skill

Do not rewrite existing `papers/`, `research/`, `structures/`, `notes/`, or `.raw/` content while performing wiki refactors. Link to those layers from the wiki when useful, but keep the direction one-way unless the user explicitly approves a specific non-wiki edit.

Log a refactor summary with `wiki-catalog.py prepend-log`. Do not edit historical log entries.

## Concept Page Contract

Every concept page must have this body shape (conventions §8):

```markdown
## 定義            ← 1 paragraph; hubs with 3+ topic sections add a 2nd "current understanding" paragraph

## 子概念          ← hubs only

## <topic 1>       ← free-form topic sections, named after the subject (2–7)
## <topic 2>
## ...

## 未解決の問い

## 未編纂の観察    ← inbox; the only section ingest appends to. Legacy name: `## 横断的知見`

## 関連

## 出典
```

Fixed sections are the six functional ones (`定義` `子概念` `未解決の問い` `未編纂の観察` `関連` `出典`). Everything else between `子概念` and `未解決の問い` is a topic section owned by the recompile step.

Topic-section rules (conventions §8 主題節の規則):

- Headings are **subject nouns** a reader could search for (「verifier の保証範囲」「成熟モデルの 2 軸」). Never use process words as headings: 知見 / 観察 / 考察 / 示唆 / まとめ / その他 / 補足 / 横断的.
- Each top-level bullet is a **claim**: a bold sentence stating what is true. Comparisons ("A vs B shows X") are evidence, not claims — nest them as `- 根拠: [[@A]] — one line`, with `- 反証:` / `- 留保:` in the same nest.
- No page self-reference ("既出", "本ページが集約してきた", "本頁に初めて追加する"). The comparison history lives in `wiki/log.md`.
- Skeleton starting points: history axis (前史→転回→現在), problem-structure axis (問題→解決→限界), consensus axis (合意→論争→未解決). Prefer matching the section layout of sibling concepts in the same domain over the skeletons.
- More than 7 topic sections → consider 親子化.
- Single-source facts do not belong on a concept page (except the definition's own source).

Hub concepts (roughly 50KB+, or pages that already have child topics) keep `## 定義` and `## 子概念` as the map. Do not flatten child-level insights into the parent; move concrete cross-source arguments into child concept pages and link them from `## 子概念`.

Legacy `## 横断的知見` is treated as the old name of the inbox. Do not mass-rename it. It disappears from a page only when that page is recompiled (its bullets are folded into topic sections; leftovers move to `## 未編纂の観察`). A legacy heading whose body is only the one-line empty placeholder may be renamed mechanically to `## 未編纂の観察`.

Frontmatter must follow `wiki/meta/conventions.md`, especially:

- `type: concept`
- `title`
- `date`, `created`, `updated`
- `aliases`
- `tags`, with date tag first and `concept` tag included
- `status`
- `related`
- `sources`
- concept-specific fields such as `complexity` and `domain` when already used locally

Populate `sources` from source links used in `## 出典` or claim provenance. Quote wikilinks in YAML.

## Refactor Decisions

Use these decisions consistently:

- **統合**: Same concept split by alias, alternate translation, or same-source duplication. Merge into one canonical page, preserve old names in `aliases`, update wikilinks, and delete only when all references are updated.
- **親子化**: An umbrella concept contains stages, methods, evaluation design, implementation tactics, or domain variants. Keep the parent as a map with `## 子概念`; move concrete arguments into child concept pages. Parent `## 横断的知見` holds only observations that span children.
- **分解**: A page exceeds roughly 100 lines or contains multiple independent cross-source arguments. Split into focused children and leave the parent with the definition, map, and synthesis.
- **圧縮**: A concept is mostly single-source fact listing. Keep only the definition, cross-source insight if any, unresolved questions, and source pointers.
- **補完**: A page is a lint stub, lacks required headings, has missing `sources`, stale indexes, weak related links, or unresolved wikilinks. Repair without inventing unsupported claims.
- **再編纂 (recompile)**: The inbox (`## 未編纂の観察` / legacy `## 横断的知見`) holds 5+ observations, or the page has no topic section and 15+ observations (`wiki-concept-stats.py --compile-debt`). Fold the inbox into topic-section claims, rewrite the `## 定義` summary paragraph on hubs, and leave the three traces required by conventions §8 update rule 4 (git commit, `log.md` entry, folded originals parked in a collapsed `> [!note]- 編纂前の観察 YYYY-MM` callout). Full procedure: [`references/recompile.md`](references/recompile.md). This is the **only** decision that may rewrite, merge, reorder, or delete existing items; ingest never does.

Prefer preserving provenance over producing a neat taxonomy. When evidence is weak, state the uncertainty in `## 未解決の問い` rather than smoothing it away.

## Workflow

1. **Inventory**
   - Prefer `python3 scripts/wiki-concept-stats.py --json` for hub candidates, long pages, and structural debt over reading index files.
   - `python3 scripts/wiki-concept-stats.py --compile-debt [--min-inbox N]` lists recompile candidates sorted by inbox size (`inbox_bullets`, `topic_sections`, `legacy_heading`).
   - `python3 scripts/wiki-excerpt.py <page> --outline` shows a page's shape (headings, bullet counts, bold claim lines) without body text.
   - List concept files with `rg --files wiki/concepts -g '*.md'`.
   - Count long pages, missing required headings, empty or missing `sources`, `lint-stub` tags, duplicate index rows, and dead wikilinks.
   - Identify duplicates with `wiki-resolve.py`, aliases, source overlap, and repeated related links.

2. **Design the refactor**
   - Produce a short action table before editing: action, target pages, reason, expected index/log changes, and deletion risk.
   - Keep open-ended taxonomy design in the main context. Delegate only independent mechanical checks when subagents are available and appropriate.

3. **Edit pages**
   - Keep edits scoped to the requested layer.
   - Preserve source links on every claim that depends on a source.
   - Use Obsidian wikilinks for internal pages.
   - For merged pages, add old titles to `aliases` and update all non-historical references.
   - Delete a page only after confirming every wikilink to it has been updated or intentionally preserved as an alias/reference.

4. **Normalize concepts**
   - Ensure all concept pages have the required fixed headings (`定義`, `未解決の問い`, `未編纂の観察` or legacy `横断的知見`, `関連`, `出典`) in the correct order; topic sections sit between `子概念` and `未解決の問い`.
   - Topic-section headings must be subject nouns (no 知見 / 観察 / 考察 / まとめ / 横断的); top-level bullets in topic sections start with a bold claim.
   - Ensure body source links and frontmatter `sources` are synchronized.
   - Ensure `related` links point to meaningful source/entity/concept/MOC pages.
   - Keep Japanese prose in 常体 and follow `wiki/meta/japanese-style.md` when wording matters.

5. **Sync indexes and log**
   - Prefer incremental updates via `wiki-catalog.py add-catalog-line` and `prepend-changelog` / `prepend-log`. Do not read full index files for routine sync.
   - Full catalog regeneration is explicit maintenance work. In that case only, you may read the specific index file being regenerated.
   - Prepend a concise entry to `wiki/log.md` via `wiki-catalog.py prepend-log` summarizing integrations, splits, compressions, supplements, and verification.

6. **Verify**
   - Run deterministic checks before claiming completion.
   - Separate pre-existing global wiki problems from problems introduced by the refactor.
   - If the user asked for commit/merge, stage only relevant files and leave unrelated dirty files untouched.

## Useful Checks

Use these as starting points, adapting paths and merged names to the task.

```bash
python3 scripts/wiki-concept-stats.py --json
python3 scripts/wiki-concept-stats.py --compile-debt --limit 30
python3 scripts/wiki-excerpt.py "wiki/concepts/<page>.md" --outline
rg --files wiki/concepts -g '*.md'
rg 'lint-stub' wiki/concepts
rg '\[\[(OldConceptA|OldConceptB)\]\]' wiki
rg -n '^## (.*)(知見|観察|考察|まとめ|横断的)' wiki/concepts/<page>.md   # generic headings left after recompile (未編纂の観察 / legacy 横断的知見 are the only allowed hits)
rg -n '既出|本ページ|本頁|本 wiki' wiki/concepts/<page>.md | rg -v '^[0-9]+:>'   # self-refs must be 0 after recompile (callout lines may hit)
git status --short
```

For structural checks, prefer a small script over ad hoc visual scanning. The script should verify at least:

- actual concept files (`rg --files wiki/concepts`) match names resolved via `wiki-resolve.py` (do not Read `_index.md` / `index.md`)
- required headings missing count is zero
- concept pages with body source links have non-empty frontmatter `sources`
- duplicate concept filenames are absent
- changed-page wikilinks resolve or are documented as pre-existing/global debt

When parsing source filenames, do not use `Path.stem` for source pages with dots in their names; strip only the final `.md` suffix.

## Log Entry Shape

Prepend a dated entry like:

```markdown
2026-06-06: **wiki/concepts リファクタリング** — [[A]] と [[B]] を [[C]] に統合し、[[Parent]] を親ページ化、[[Child1]]・[[Child2]] を分解。lint stub N 件を補完し、concept index と `sources` frontmatter を同期。検証: 必須見出し欠落 0、index 差分 0、対象旧リンク 0、lint-stub 0。
```

Use plain text for obsolete page names if keeping historical names as wikilinks would break the current verification target.

Recompile entries name the page, the fold ratio, and the resulting sections:

```markdown
## [2026-09-02] recompile | eBPF
- Page: [[eBPF]]
- Folded: 30 観察 → 11 命題 / 4 主題節(verifier の保証範囲, カーネル拡張面としての struct_ops, 観測から制御への重心移動, 表現力と検証可能性の緊張)
- Kept in inbox: 2 (単一ソース・要再確認)
- Parked: `> [!note]- 編纂前の観察 2026-09`(30 件)
- Summary paragraph: rewritten
```

## Pitfalls

- Do not make every page a parent page. Parent pages are maps; children are concrete arguments.
- Do not hide contradictions by merging prose. Use contradiction callouts or unresolved questions.
- Do not recompile from an ingest session, and do not batch several pages into one recompile commit. One page = one commit.
- Do not drop a `(Source: ...)` while folding. Every claim keeps every source its folded observations cited.
- Do not rewrite topic sections during 統合 / 親子化 / 分解 without also applying the recompile traces; structural moves that reword claims are recompiles.
- Do not let index summaries contain nested wikilinks that break list parsing.
- Do not introduce duplicate `sources:` frontmatter blocks.
- Do not treat a global dead-link count as a refactor failure unless changed pages introduced the dead links.
- Do not stage unrelated dirty files from the user's vault.
