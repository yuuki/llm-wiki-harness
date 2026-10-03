---
name: wiki-query
description: "Answer a single question from the Obsidian wiki with citations: a synthesis across pages, a deep explanation of one source, or the difference between two things. Retrieve-first (`retrieve.py`, then excerpts; never index.md, _index.md, log.md or the full hot.md). Standard and deep answers are filed back to `wiki/questions/`; quick mode is not. Triggers on: what do you know about, query:, what is, explain, find in wiki, based on the wiki, wiki query quick, wiki query deep. 単一 source を Slack 等へ貼る紹介文や、配布意図のある「この論文を要約して」は wiki-brief-source、渡された 1 source への軽い確認は wiki-ask-source、開いているページの隣は wiki-related、命題の判定は wiki-thesis、長編編纂は wiki-survey。"
allowed-tools: Read Glob Grep Bash
---

# wiki-query: Query the Wiki

The wiki has already done the synthesis work. Read strategically, answer precisely, and file good answers back so the knowledge compounds.

単一 source の配布用紹介文(Slack に貼る箇条書き、「この論文を要約して」で共有したいとき)は `wiki-brief-source` に渡し、本 skill では答えない。`wiki/questions/` にも保存しない。

渡された 1 source への軽い確認(「ここが分からない」)は `wiki-ask-source` に渡す。単一 source でも、引用付きの深掘り解説を求める問いは本 skill で扱う(wiki/CLAUDE.md の判断基準)。

今見ているページの隣・関連・配役は `wiki-related` に渡す。種が問い文なら本 skill、種がページパスなら `wiki-related` である。`retrieve.py` で隣を作るな。

---

## Retrieval

Standard and Deep modes start with `retrieve.py`, then excerpt — never `wiki/index.md`. Canonical policy: [`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md).

```bash
python3 scripts/retrieve.py "<the user's question verbatim>" --top 5   # Deep: --top 10
python3 scripts/wiki-excerpt.py "<absolute_path from candidate>" --tail 15 --budget-tokens 1800
```

Output is JSON with a `candidates` array. Each candidate has `absolute_path`, a `snippet`, and `bm25_score` + `rerank_score`. When the page graph is built (`.vault-meta/graph.json`), candidates also carry `channels` (`["bm25"]`, `["graph"]`, or both) and the `strategy` string ends in `+graph`; a `["graph"]`-only candidate is a link / co-citation neighbour of the lexical hits that the query never named — excerpt it like any other, but cite it only if its excerpt actually answers the question. Excerpt each candidate page — do **not** Read full fat concept pages.

If `retrieve.py` exits 10 (feature not provisioned), fall back to `wiki-resolve.py` + `rg`, then excerpt. Never Read `wiki/index.md` as fallback.

When the question asks whether two things conflict (矛盾 / 対立 / 食い違い / 両立 / どちらが正しい), also query the contradiction index before excerpting — it returns every `> [!contradiction]` callout whose text or wikilinks match all terms, with host page, section, and a status guess:

```bash
python3 scripts/contradiction-index.py --query <語1> [<語2> ...] --limit 10
```

Cite the host page and quote the callout; if `status` is `open`, say so instead of resolving it yourself. If the index has no hit, the wiki has not recorded that disagreement — say that explicitly rather than inferring one.

Quick mode skips retrieval (trimmed hot window only — see Quick Mode below).

Full spec: [`skills/wiki-retrieve/SKILL.md`](../wiki-retrieve/SKILL.md). Setup: `bash bin/setup-retrieve.sh`.

---

## Query Modes

Three depths. Choose based on the question complexity.

| Mode | Trigger | Reads | Token cost | Best for |
|------|---------|-------|------------|---------|
| **Quick** | `query quick: ...` or simple factual Q | Trimmed `wiki/hot.md` window only (≤2,000 tokens / 5 entries) | ≤2,000 | "What is X?", date lookups, quick facts |
| **Standard** | default (no flag) | `retrieve.py --top 5` + 3–5 excerpts (each ≤1,800) | ~6,000–11,000 | Most questions |
| **Deep** | `query deep: ...` or "thorough", "comprehensive" | `retrieve.py --top 10` + more excerpts + optional web | ~10,000–20,000 | "Compare A vs B across everything", synthesis, gap analysis |

Never Read `wiki/index.md`, `wiki/log.md`, or `wiki/*/_index.md`. Those files exist for humans and Obsidian only.

---

## Quick Mode

Use when the answer is likely in the recent hot cache. `wiki/hot.md` is maintained as a token-capped window (scripts keep it ≤2,000 tokens / 5 entries via `wiki-catalog.py trim-hot`).

1. Read the current `wiki/hot.md` (a script-trimmed window of ≤2,000 tokens / 5 entries).
2. If it answers the question, respond immediately.
3. If not found, say in Japanese that the quick cache does not have it and offer a standard query — do **not** Read `wiki/index.md`.

Do not open individual wiki pages in quick mode.

---

## Standard Query Workflow

1. **Retrieve first**: `python3 scripts/retrieve.py "<question>" --top 5`
2. **Excerpt** each candidate: `python3 scripts/wiki-excerpt.py <path> --tail 15 --budget-tokens 1800` — do not Read full fat concept pages.
3. If retrieve exits 10, fall back to `python3 scripts/wiki-resolve.py "<term>" --type any` + `rg`, then excerpt hits.
4. Follow wikilinks from excerpts to depth-2 for key entities (excerpt those too). No deeper.
5. **Synthesize** the answer in chat. Cite sources with wikilinks: `(Source: [[Page Name]])`.
6. **File by default** (save-first). A standard answer that synthesises two or more pages is saved to `wiki/questions/` without asking (see *Filing Answers Back*). Skip the save only when: the user said not to, the answer is a bare fact lookup, the answer is "not in the wiki" (go to Gap Handling), one existing page already answers it verbatim (then link that page instead of duplicating), **or the request is a single-source Slack/配布紹介文** (hand to `wiki-brief-source`; do not file here). Say in one line where it was filed.
7. If the question reveals a **gap**: name the missing subtopic in Japanese and offer to find a source.

---

## Deep Mode

Use for synthesis questions, comparisons, or "tell me everything about X."

**Hand off long-form compilation.** Judge by what the request asks for, not by output size. If it asks for a systematic map of a design space (textbook), a map of a literature population (survey), or a cross-referenced picture of a subject built from specs, implementations, measurements, and papers (investigation) — typical asks: 「〜の教科書を作成して」「網羅的かつ体系的に整理して」「サーベイ論文調にまとめて」「文献横断調査」「〜について関連文献を調査して」 — stop here and use the `wiki-survey` skill. It owns population gating, axis design, parallel close-reading, and the separate figure pass.

Do not route back. If `wiki-survey` finds the population too thin, its own gate sends the work to `autoresearch` / `wiki-ingest-*`, not to this skill. Deep mode stays for single questions answerable in one pass.

1. **Retrieve first**: `python3 scripts/retrieve.py "<question>" --top 10`
2. **Excerpt** every candidate and follow wikilinks to related concepts, entities, and sources (excerpt those too — never Read full pages).
3. If retrieve exits 10, fall back to `wiki-resolve.py` + `rg`, then excerpt.
4. If wiki coverage is thin, offer to supplement with web search.
4b. Frame the synthesis with `python3 scripts/wiki-profile.py` (25-line digest; never Read the profile file itself). Follow the profile's 評価の癖. If the script exits 3 (no profile in this vault), skip framing. Standard mode skips this step.
5. Synthesize a comprehensive answer with full citations.
6. Always file the result back as a wiki page (same procedure as standard; no opt-out short of the user saying so). Deep answers are too valuable to lose.

---

## Token Discipline

Read the minimum needed. Never Read catalog files (`index.md`, `_index.md`, `log.md`). See [`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md).

| Step | Cost (approx) | When to stop |
|------|---------------|--------------|
| Trimmed hot window (quick only) | ~2,000 tokens | If it has the answer |
| `retrieve.py --top 5` | ~200 tokens JSON | Standard mode first step |
| 3-5 excerpts (`--tail 15 --budget-tokens 1800`) | ≤1,800 tokens each | Usually sufficient |
| `retrieve.py --top 10` + 10 excerpts (deep) | ~8,000+ | Synthesis only |

If the trimmed hot window has the answer (quick mode), respond without reading further. Standard/Deep never open `index.md` (~360k tokens avoided).

---

## Catalog files (human-only)

`wiki/index.md`, `wiki/log.md`, and `wiki/{sources,entities,concepts}/_index.md` exist for humans and Obsidian navigation. Agents must not Read them. Use `retrieve.py`, `wiki-resolve.py`, and `wiki-excerpt.py` for discovery and reading; use `wiki-catalog.py` for catalog updates.

---

## Filing Answers Back

Good answers compound into the wiki. Don't let insights disappear into chat history.

単一 source の配布用紹介文はここへ保存しない。`wiki-brief-source` が `wiki/briefs/` に書く。

When filing an answer:

```yaml
---
type: question
title: "Short descriptive title"
question: "The exact query as asked."
answer_quality: solid
date: YYYY-MM-DD HH:mm
created: YYYY-MM-DD
updated: YYYY-MM-DD
aliases: []
tags:
  - YYYY/MM/DD
  - question
  - <domain-tag>
status: developing
related:
  - "[[Page referenced in answer]]"
sources:
  - "[[@YYYY__SOURCE__Title]]"
---
```

The frontmatter follows conventions §2: `date:` is required, `tags` is a block list whose first item is the date tag, and source links carry the `@` prefix. `wiki-page-write.py` rejects the page otherwise.

Then write the answer as the page body, starting with one H1 that repeats the title (`# <title>`). Include citations. Link every mentioned concept or entity. Write through `python3 scripts/wiki-page-write.py "wiki/questions/<title>.md" --content-file "$TMPDIR/answer.md"` (allocates the address, validates frontmatter, takes the lock); never write the file directly. Frame the title as the question in Japanese 常体 (`〜か`), not as a topic. Quick mode never files.

**Close the loop on the concept side.** If the answer settles a bullet under a concept's `## 未解決の問い`, remove it and leave the trace: `python3 scripts/wiki-append.py --page "wiki/concepts/<concept>.md" --remove-question-containing "<問いの語>" --observation "[<主題節名>] <一文の答え> (回答 [[<question page>]])"`. If the answer raises a new open question, append it with `--question`. If the answer is really a *judgment on a proposition* (supported / contradicted with evidence on both sides), hand off to `wiki-thesis` instead of filing a `question` page.

After filing, update catalogs via scripts — do not Read+Edit `wiki/index.md` or `wiki/log.md`:

```bash
python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
## [YYYY-MM-DD] wiki-query | Short title
- Question: [[Question Title]]
- Key insight: One sentence.
EOF
)"

python3 scripts/wiki-catalog.py add-catalog-line \
  --file wiki/index.md \
  --section "Questions" \
  --line "- [[Question Title]]: answer summary"

python3 scripts/wiki-catalog.py prepend-master --text "$(cat <<'EOF'
### YYYY-MM-DD wiki-query | Short title
- [[Question Title]]
EOF
)"
```

Pass multi-line `--text` through a heredoc. A `\n` inside double quotes is not a newline in bash and lands in the catalog as literal text.

---

## Gap Handling

If the question cannot be answered from the wiki:

1. State in Japanese which subtopic the wiki lacks, and offer to find or ingest a source.
2. Do not fabricate. Do not answer from training data if the question is about the specific domain in this wiki.
