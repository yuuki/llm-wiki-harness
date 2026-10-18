---
name: wiki-lint
description: >
  Health check the Obsidian wiki vault. Finds orphan pages, dead wikilinks, stale claims,
  missing cross-references, frontmatter gaps, and empty sections, and writes a lint
  report under wiki/meta/. Detects and reports; changes to page content (recompile,
  merges, splits) belong to wiki-refactor. Triggers on: "lint", "health check",
  "check the wiki", "wiki maintenance", "find orphans", "wiki audit".
---

# wiki-lint: Wiki Health Check

Run lint after every 10-15 ingests, or weekly. Output a lint report to `wiki/meta/lint-report-YYYY-MM-DD.md`. Lint observes: rebuilding derived caches, reaping stale locks, and the `paper-ids.py backfill` of frontmatter IDs are applied without asking; any fix that changes page content is shown in the report first and applied only after the user confirms (see *Before Auto-Fixing*).

> Token discipline: never Read `wiki/index.md`, `wiki/log.md`, `wiki/*/_index.md`, or a historical-sized `wiki/hot.md`. Discover pages with `wiki-resolve.py` / `rg` on content pages. Catalog writes go through `wiki-catalog.py`. See [`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md).

---

## Lint Checks

Run check 18 (doctor) first, then work through 1–17 and 19 in order:

1. **Orphan pages**. Wiki pages with no inbound wikilinks. They exist but nothing points to them.
2. **Dead links**. Wikilinks that reference a page that does not exist.
3. **Stale claims**. Assertions on older pages that newer sources have contradicted or updated.
4. **Missing pages**. Concepts or entities mentioned in multiple pages but lacking their own page.
5. **Missing cross-references**. Entities mentioned in a page but not linked.
6. **Frontmatter gaps**. Pages missing required fields (type, status, created, updated, tags).
7. **Empty sections**. Headings with no content underneath.
8. **Missing page files**. From wikilinks collected while scanning **content** pages (not catalogs), confirm each target with `python3 scripts/wiki-resolve.py "<name>" --type any`. Empty candidates mean a dead link. Do **not** Read `wiki/index.md` to hunt stale catalog lines; that file is a human derivative.
9. **Address validity**. For every page that has an `address:` frontmatter field, validate the format and the manifest's `address_map`. See the **Address Validation** section below.
10. **Semantic tiling**. Flag candidate duplicate pages (across all scanned types, not just concepts) via embedding cosine similarity. See the **Semantic Tiling** section below.
11. **Contradiction index** (conventions §5). Regenerate the index first, then read its counts and the one-sided candidates — never grep callouts by hand:

    ```bash
    python3 scripts/contradiction-index.py --write      # wiki/meta/contradictions.md + .vault-meta/contradictions.json
    python3 scripts/contradiction-index.py --one-sided  # pairs where only one page carries the callout
    ```

    Report the counts (callouts / pages / open / explained) and list the one-sided candidates under **## Contradictions**. §5 requires a contradiction to be marked on *both* pages; a one-sided pair is a candidate for the human to add the counterpart callout, not an automatic fix. Do not edit callouts from lint. The **Stale Claims** check (item 3) stays a human comparison; the index only tells you where the wiki already knows it disagrees with itself.
    **Concept structure.** `python3 scripts/wiki-concept-stats.py --structure --json` lists concept pages missing `# タイトル`, `## 定義`, or `## 出典`, and pages whose body cites `[[@` while frontmatter `sources:` is empty. Report the counts and the pages under **## Structure**; repairs belong to wiki-refactor (補完).
12. **Recompile queue** (conventions §8 update rule 6). Sync the compile-debt scan into the persistent queue and paste its report:

    ```bash
    python3 scripts/recompile-queue.py refresh   # new debt → pending, cleared debt → done, done pages with new inbox → pending again
    python3 scripts/recompile-queue.py report    # markdown block: counts by state, waiting bullets, top 10, blocked
    ```

    Paste the `report` output as the **## Recompile Queue** section. Lint never recompiles and never changes queue states other than through `refresh`; `blocked` pages (three human rejections) are listed for the human to `reopen` or `skip`. Stage the queue with `git add -f .vault-meta/recompile-queue.json` in the lint commit.
13. **Claim audit** (sampled, conventions §5). Check that recompiled propositions still say what their cited sources say. Sample, judge each packet per [`wiki-refactor/references/claim-audit.md`](../../../.agents/skills/wiki-refactor/references/claim-audit.md) §2, record, and paste the summary:

    ```bash
    W="$(mktemp -d "${TMPDIR:-/tmp}/claim-audit.XXXXXX")"   # per-run dir: fixed names under /tmp collide across parallel sessions
    python3 scripts/claim-audit.py sample --recompiled --limit 5 --per-page 2 --seed "$(date +%Y%m%d)" > "$W/claim-sample.json"
    # judge → "$W/claim-verdicts.json"
    python3 scripts/claim-audit.py record --verdicts "$W/claim-verdicts.json"
    python3 scripts/claim-audit.py report      # paste as ## Claim Audit
    ```

    Lint records verdicts but does not edit propositions; `not_in_source` / `unsupported` findings go to the report as **Needs review** for a wiki-refactor pass (留保 line or weakened claim, one page per commit). Stage `.vault-meta/claim-audit.json` (with `-f`) and the day's `wiki/meta/claim-audit-YYYY-MM-DD.md`.
14. **Concept candidates** (conventions §12). Deferred concept candidates live in `.vault-meta/concept-candidates.json`; ingest adds mentions, this check reports which ones have reached the creation threshold (2 independent documents; chapter sources of one book count once):

    ```bash
    python3 scripts/concept-candidates.py seed-from-log      # idempotent: pick up any `- Deferred:` lines not yet in the ledger
    python3 scripts/concept-candidates.py report              # paste as ## Concept Candidates
    ```

    The **Missing Pages** check (item 3 in the report) finds names mentioned without a page; this check is the complement for names an ingest *already judged* worth a concept but deferred. `ready` candidates are a proposal for the human (create via the next related ingest, or `reject --reason`), not an automatic fix. "promote し忘れ" rows (a same-named concept already exists) are fixed with `promote`. Stage the ledger with `git add -f .vault-meta/concept-candidates.json`.

15. **Paper IDs and duplicate papers** (conventions §3). Source pages carry `arxiv_id:` / `doi:` so the same paper is never ingested twice under a different slug. This check rebuilds the ID index, backfills IDs derivable from `url:` / `sources:` / body, and lists papers that exist as two or more documents (chapter-split sources fold into one document):

    ```bash
    python3 scripts/paper-ids.py scan --write-index          # .vault-meta/paper-ids.json (derived cache, not tracked)
    python3 scripts/paper-ids.py backfill --write            # skips pages dirty in the git worktree; does not bump updated:
    python3 scripts/paper-ids.py report                      # paste as ## Paper IDs
    ```

    Backfill is safe to auto-fix (it only adds frontmatter lines). Duplicate groups and "1 枚もの + 章分割" coexistence are **Needs review**: merge or delete is a human decision (keep the page with more inbound links, move its `related:`, and leave a `related:` bridge if both stay, e.g. a reading-series note next to the original).

16. **Paper ref candidates** (conventions §3). Bibliography identifiers from ingested paper raw texts live in `.vault-meta/paper-ref-candidates.json`. This check runs **after** Paper IDs so the live index exists. It reports ready rows only (pending, demand ≥ 2, not a HIT in the readable paper-ids index). Do **not** run `backfill` here.

    ```bash
    python3 scripts/paper-refs.py report --write      # stdout + wiki/meta/paper-ref-candidates.md
    ```

    Paste stdout as ## Paper Ref Candidates. `ready` rows are a proposal for the human (`/wiki-ingest-paper <URL>`), not an automatic ingest. Titles are bibliography leftovers, not API titles; do not invent titles. If report exits 1, write that the ledger is unreadable（台帳が読めない）and continue the rest of lint. If it exits 75, wait 2s and retry the same command once; if still 75, paste stdout and do not `git add` the generated note. Do not reject. Do **not** run `backfill`. If the ledger is new or updated, `git add -f .vault-meta/paper-ref-candidates.json`. If the generated note was written, `git add wiki/meta/paper-ref-candidates.md`.

17. **Entity aliases** (conventions §12 rule 6). Detect entity pages that name the same person or organisation (English vs Japanese title, legal suffix, `J. Dean` vs `Jeff Dean`, `MIT` vs its full name, alias collisions where one page's title sits in another page's `aliases`):

    ```bash
    python3 scripts/entity-resolve.py report --limit 30     # paste as ## Entity Aliases
    python3 scripts/concept-resolve.py report --limit 30    # paste as ## Concept Aliases (immediately after Entity Aliases)
    ```

    Read-only; every row is **Needs review** for a wiki-refactor 統合 pass (`entity-resolve.py plan --keep A --drop B` shows the affected pages). Surname-only and `Y. Li`-style aliases are ignored by the detector. Two different organisations sharing a short acronym are fixed by removing one alias, not by merging. After the human decides, `decide --merged` / `decide --rejected --reason` records it; stage `.vault-meta/entity-merges.json` with `git add -f` once it exists. Concept pairs are decided with `concept-resolve.py decide` and staged as `.vault-meta/concept-merges.json` (`git add -f`). They do not go into `entity-merges.json`. Concept pairs come from `concept-resolve.py report` (unfinished merges and page-less alias-only pairs are required; body capped at 30).

18. **Machine state (doctor)**. It runs before checks 1–17 because a stale retrieval index or a duplicate address distorts the other checks (dead-link hunting against chunks that point at moved pages, tiling over unchunked pages):

    ```bash
    python3 scripts/wiki-doctor.py            # OK / WARN / FAIL table with a fix: command per finding; exit 1 on any FAIL
    ```

    Read-only. Apply the printed `fix:` commands yourself only for the derived caches (`wiki-retrieve-refresh.py`, `wiki-graph.py build`, `wiki-clusters.py build`, `paper-ids.py scan --write-index`, `contradiction-index.py --write`) and stale locks (`wiki-lock.sh reap`); everything else (duplicate `address:`, manifest gaps, `*.tmp` residue that may belong to a concurrent ingest, corrupt ledgers) is **Needs review**. Paste the table as **## Doctor** at the top of the report.

19. **Retractions** (conventions §3). Source pages carry `doi:`; an ingested paper that was later retracted must not keep supporting concept claims silently. This check asks OpenAlex (`is_retracted`) for every `doi:` in `wiki/sources/` in batches of 50 (about 20 requests, within the free daily budget; no key needed). Only DOIs leave the vault.

    ```bash
    python3 scripts/bib-lookup.py retracted        # paste as ## Retractions
    ```

    Each `RETRACTED` row is **Needs review**: open the source page, mark it with a `> [!warning]` retraction note, and check the concept pages that cite it (wiki-refactor 命題を再検証). Never delete the page. Exit 4 (free budget exhausted) or 5 (network) means write 「撤回照会を実行できなかった」 and continue; do not retry in a loop. The sandbox needs `api.openalex.org`. When `paper-ids.py report` lists an arXiv-only source whose published version may exist, `python3 scripts/bib-lookup.py pair <published-doi>` resolves the arXiv id from OpenAlex `locations` (one-way: published DOI to arXiv; an arXiv DOI itself returns 404).

---

## Lint Report Format

Create at `wiki/meta/lint-report-YYYY-MM-DD.md`:

```markdown
---
type: meta
title: "Lint Report YYYY-MM-DD"
date: YYYY-MM-DD HH:mm
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags:
  - YYYY/MM/DD
  - meta
  - lint
status: developing
---

# Lint Report: YYYY-MM-DD

## Doctor
(paste `python3 scripts/wiki-doctor.py` verbatim; derived-cache and stale-lock fixes are applied before the other checks, the rest is Needs review)

## Summary
- Pages scanned: N
- Issues found: N
- Auto-fixed: N
- Needs review: N

## Orphan Pages
- [[Page Name]]: no inbound links. Suggest: link from [[Related Page]] or delete.

## Dead Links
- [[Missing Page]]: referenced in [[Source Page]] but does not exist. Suggest: create stub or remove link.

## Missing Pages
- "concept name": mentioned in [[Page A]], [[Page B]], [[Page C]]. Suggest: create a concept page.

## Frontmatter Gaps
- [[Page Name]]: missing fields: status, tags

## Stale Claims
- [[Page Name]]: claim "X" may conflict with newer source [[Newer Source]].

## Contradictions
- Index regenerated: [[contradictions]] — N callouts on M pages (open N, explained N).
- One-sided: [[Host Page]] marks a contradiction with [[Party Page]] but the party carries no counterpart callout. Suggest: add the callout on [[Party Page]] or mark `(status: explained)` on the host.

## Cross-Reference Gaps
- [[Entity Name]] mentioned in [[Page A]] without a wikilink.

## Recompile Queue
(paste `python3 scripts/recompile-queue.py report` verbatim)

## Claim Audit
(paste `python3 scripts/claim-audit.py report` verbatim; list `not_in_source` / `unsupported` findings under Needs review)

## Concept Candidates
(paste `python3 scripts/concept-candidates.py report` verbatim; `ready` rows are proposals for the human, not auto-fixes)

## Paper IDs
(paste `python3 scripts/paper-ids.py report` verbatim; duplicate groups are Needs review, backfill counts are auto-fixed)

## Paper Ref Candidates
(paste `python3 scripts/paper-refs.py report --write` stdout verbatim; `ready` rows are proposals for the human, not auto-ingest. If the command exits 1, write 「台帳が読めない」 and continue. If it exits 75 after one retry, paste stdout and do not add the generated note)

## Retractions
(paste `python3 scripts/bib-lookup.py retracted` stdout verbatim; every `RETRACTED` row is Needs review. On exit 4 or 5 write 「撤回照会を実行できなかった」)

## Entity Aliases
(paste `python3 scripts/entity-resolve.py report` verbatim; every pair is Needs review for wiki-refactor 統合, never auto-merged)

## Concept Aliases
(paste `python3 scripts/concept-resolve.py report --limit 30` verbatim; unfinished merges and page-less pairs are required)
```

---

## Naming Conventions

命名は `wiki/meta/conventions.md` §4 に従う。ask / brief は source と同じ basename を持つので、ベアリンクではなくパス修飾リンクになっているかを検査する。

---

## Writing Style Check

During lint, flag pages that violate `wiki/meta/japanese-style.md` (常体, no code-switching), claims without source citations, uncertainty not flagged with `> [!gap]`, and contradictions not flagged with `> [!contradiction]`.

---

## Address Validation

Every non-meta wiki page carries `address: c-NNNNNN` (wiki/CLAUDE.md rule 7). Check 18 (doctor) already reports counter drift, duplicate addresses, and knowledge pages that have no address at all. This check adds the two things doctor does not look at.

Not applicable: pages under `wiki/folds/` (they use `fold_id`) and the meta files `_index.md`, `index.md`, `log.md`, `hot.md`, `overview.md`, `dashboard.md`, `dashboard.base`.

1. **Format**: any `address:` value must match `^c-[0-9]{6}$`. Anything else is an error (doctor skips malformed values instead of flagging them).
2. **Address-map consistency** (`.raw/.manifest.json`): for every page path in `address_map`, the page must exist and its frontmatter `address` must match the mapping. A mismatch is an error: a rename dropped the map update, or a manual edit diverged.

Lint only observes. Do not assign or change addresses during lint. Pages without an address are repaired with the command doctor prints (`python3 scripts/address-backfill.py --write`, after the creation path that bypassed `wiki-page-write.py` is closed), and counter drift with `bash scripts/allocate-address.sh --rebuild`; both go under **Needs review**.

### Output section in the lint report

```markdown
## Address Validation

- Counter, duplicates, pages without address: see ## Doctor
- Format errors: N
- Address-map mismatches: N

### Errors
- [[Page Name]]: invalid address format `{value}`. Expected `c-NNNNNN`.
- `.raw/.manifest.json` maps `wiki/foo.md` -> `c-000010` but page frontmatter has `c-000012`. Resolve mismatch.
```

---

## Semantic Tiling

`scripts/tiling-check.py` flags candidate duplicate pages by embedding cosine similarity (local ollama, `nomic-embed-text`). It is read-only: duplicates are listed, never merged. The helper hard-fails above 5,000 scanned pages and needs a running ollama, so on this vault it normally cannot complete. Probe once and report the outcome as it is; do not work around a non-zero exit.

```bash
./scripts/tiling-check.py --peek > "$TMPDIR/tiling-peek.json" 2>/dev/null; echo "exit=$?"
```

| Exit | Meaning | What goes under `## Semantic Tiling` in the report |
|---|---|---|
| 0 | ready | run `./scripts/tiling-check.py --report wiki/meta/tiling-report-YYYY-MM-DD.md`, then embed the counts below |
| 4 | more pages than the hard limit | `skipped: scale hard-fail (exit 4)` |
| 10 | ollama unreachable | `skipped: ollama not reachable (exit 10)` |
| 11 | embedding model missing | `skipped: model missing (exit 11)` |
| 2, 3, other | usage error, corrupt cache (`.vault-meta/tiling-cache.json`), unexpected | report the exit code as an error; `$TMPDIR/tiling-peek.json` holds the diagnostics |

Report each exit code as it is rather than folding them into "unknown". `--report` can return the same codes if ollama goes away between the two calls. The helper posts page bodies as embedding input, so it only talks to `127.0.0.1` unless `--allow-remote-ollama` is given; pass that flag only when the user asks.

When a report was written:

```markdown
## Semantic Tiling
See [[tiling-report-YYYY-MM-DD]] for the full pair listing.
- Errors (>=0.90): N pairs
- Review (0.80-0.90): M pairs
- Calibrated: true|false
```

The bands and the `calibrated` flag come from `.vault-meta/tiling-thresholds.json` (defaults: error at 0.90 and above, review from 0.80, uncalibrated). Pages under `wiki/folds/`, `wiki/meta/`, `wiki/asks/` and `wiki/briefs/` are not scanned.

---

## Before Auto-Fixing

Always show the lint report first. Ask: "Should I fix these automatically, or do you want to review each one?"

Safe to fix once the user agrees to automatic fixes:
- Adding missing frontmatter fields with placeholder values
- Adding wikilinks for unlinked mentions

Needs review before fixing:
- Creating pages for missing entities or concepts. New pages go through `wiki-page-write.py` (address, validation), and whether a page should exist is an ingest / refactor decision
- Deleting orphan pages (they might be intentionally isolated)
- Resolving contradictions (requires human judgment)
- Merging duplicate pages
