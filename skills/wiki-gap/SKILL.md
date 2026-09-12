---
name: wiki-gap
description: "Validate structural gap candidates in the LLM wiki layer against human domain knowledge and recommend bridging literature. Consumes the Gap report copied from the wiki-lens Gap Finder view (or computes bridge candidates itself), judges which candidate connections are real knowledge gaps, recommends canonical papers to ingest, and verifies recommendations via arXiv / DBLP / Crossref (identifier-first; see bibliography-lookup.md). Triggers on: gap analysis, wiki gap, validate gaps, ギャップ分析, 橋渡し文献, recommend bridging literature."
---

# wiki-gap: Gap Validation and Bridging-Literature Recommendation

The wiki-lens plugin detects *structural* gap candidates (cluster pairs that are thinner than a null model predicts, concept pairs co-cited by many sources but never linked, high Adamic-Adar non-edges, missing sources cited from multiple clusters). Structure alone cannot tell a real knowledge gap from a deliberate separation. This skill adds the two things the plugin deliberately does not do: **judgment against human domain knowledge** and **literature recommendation with external verification**.

Division of labor (fixed by design):

- **wiki-lens (plugin)** — structural detection only. Read-only, no network, no LLM.
- **wiki-gap (this skill)** — semantic triage, recommendation, API verification, report. Writes only `wiki/meta/gap-report-*.md` and a `wiki-catalog.py prepend-log` entry. **Never edits concept/source/entity pages, never Reads/Edits catalog files, and never ingests anything itself** — ingestion is handed off to `wiki-ingest-paper` with explicit user approval.

## Before anything

1. Read `wiki/CLAUDE.md` and `wiki/meta/conventions.md` (iron rule for any wiki/ operation).
2. Report prose is Japanese 常体 per `wiki/meta/japanese-style.md`. Paper titles, APIs, and identifiers stay in the original language.

`wiki-clusters.py` を呼ばない。Gap Finder 報告の `#id` を `clusters.json` および `members` と突合しない。間隙の正本は Copy report のままである。

## Step 1 — Obtain candidates

Preferred input: the Markdown report produced by the Gap Finder view's **Copy report** button (the user pastes it, or points to a file). It contains four sections: cluster gaps, concept pairs (co-citation, with page paths and the common source paths), predicted links (Adamic-Adar, with page paths), bridge literature (missing `@`-prefixed sources; the plugin default is ≥1 cluster, ranked by demand), plus coverage notes.

Fallback when no report is provided: compute **bridge candidates only** directly — scan wiki pages for `[[@...]]` wikilinks whose target file does not exist under `wiki/sources/`:

```bash
rg -o '\[\[@[^\]|#]+' wiki/ --no-filename -N | sort | uniq -c | sort -rn
ls wiki/sources/ | sed 's/\.md$//'
```

Targets appearing in the first list but not the second are cited-but-missing sources; rank by reference count. Tell the user the cluster-level signals need the plugin view.

## Step 2 — Semantic triage (LLM knowledge)

For each cluster gap / concept pair / predicted link, judge with your own domain knowledge and assign one verdict:

- **real-gap** — human knowledge connects these (shared methods, one field routinely cites the other, a survey covering both exists). State the connecting idea in 1–2 sentences.
- **weak** — plausibly related but the connection is generic (e.g. both "use ML"). Not worth a bridge.
- **intentional** — the vault deliberately separates them (different projects, different abstraction layers). Check `structures/000 Index.md` MOC boundaries before using this verdict.

Be skeptical by default: a co-citation count of 3 can be an artifact of one broad survey. Look at *which* sources co-cite (listed in the report) before crediting the signal.

## Step 3 — Recommend bridging literature

For each **real-gap**, recommend literature in priority order:

1. **Tier 0 — already cited in the wiki**: matching entries from the report's bridge-literature section. Zero hallucination risk (the wiki already cites them); recommend ingestion first.
2. **Tier 1 — from your knowledge**: canonical surveys or origin papers that connect the two sides. 1–3 per gap, prefer surveys. Mark every Tier 1 item **未検証** until Step 4 confirms it.

## Step 4 — Verify via external APIs

Verify every Tier 1 recommendation before it enters the report. Follow [`.claude/skills/bibliography-lookup.md`](../bibliography-lookup.md): identifier-first, arXiv / DBLP / Crossref. **Do not use the Semantic Scholar Graph API.** **Privacy rule: queries may contain only concept names and candidate paper titles — never note contents, vault paths, or anything from `z99_private/`.**

Confirm exact title, year, venue, and DOI or arXiv id. Treat venue (and whether the item is a survey) as canonicity evidence; do not use citation counts. A landing page (`arxiv.org/abs/…` or `doi.org/…`) is enough for a handful of items. If a host is blocked, surface the permission prompt instead of working around it. A recommendation that cannot be verified stays in the report but keeps the **未検証** label — never fabricate identifiers.

## Step 5 — Report

Write `wiki/meta/gap-report-YYYY-MM-DD.md` (frontmatter mirrors the lint reports: `type: meta`, title, date, created/updated, tags `[YYYY/MM/DD, meta, gap]`, `status: developing`, related `[[index]]`). Structure:

1. **Summary** — candidates received / verdict counts / recommendations verified.
2. **実在ギャップ** — per gap: the two sides, structural evidence (score, common sources), 判定根拠, recommended literature with verified DOI/arXiv id and venue, and the suggested action (`wiki-ingest-paper <url>` for papers; 概念ページ間リンクの追記は推奨として記載するのみ).
3. **弱い/意図的な分離** — one line each, so the next run doesn't re-litigate them.
4. **未検証の推薦** — anything Step 4 could not confirm.
5. **Coverage notes** — carried over from the plugin report.

Then `wiki-catalog.py prepend-log` in the existing format (`## [YYYY-MM-DD] gap-analysis | <short title>` with Source / Pages created / Key insight lines). Do not Read/Edit `wiki/log.md`. Commit per vault convention: `wiki: add gap report YYYY-MM-DD`.

## Boundaries

- This skill recommends; it does not act. No edits to `wiki/{sources,entities,concepts,questions}/`, no ingestion, no link insertion. Hand off to `wiki-ingest-paper` only when the user picks a recommendation.
- A **real-gap** row whose bridging literature is thin or absent (no canonical paper connects the two sides) is a research-idea seed, not only a reading gap. Say so in the report row (「着想の種: wiki-ideate へ」) and hand off to `wiki-ideate` when the user asks; this skill never drafts ideas itself.
- Existing primary-layer notes (`papers/`, `research/`, `notes/`, `structures/`) are out of scope entirely.
- API queries follow the privacy rule above; no other network use.
