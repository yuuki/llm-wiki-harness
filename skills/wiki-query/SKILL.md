---
name: wiki-query
description: "Answer questions using the Obsidian wiki vault. Retrieve-first: never read index.md, _index.md, log.md, or full hot.md (except the trimmed hot window in quick mode). Synthesizes answers with citations. Files good answers back as wiki pages. Supports quick, standard, and deep modes. Triggers on: what do you know about, query:, what is, explain, summarize, find in wiki, search the wiki, based on the wiki, wiki query quick, wiki query deep."
allowed-tools: Read Glob Grep Bash
---

# wiki-query: Query the Wiki

The wiki has already done the synthesis work. Read strategically, answer precisely, and file good answers back so the knowledge compounds.

---

## Transport (v1.7+)

Reads should prefer the same transport the rest of the plugin uses. Consult `.vault-meta/transport.json` (auto-created by `bash scripts/detect-transport.sh`) and use the `preferred` entry:

- **cli** — `obsidian-cli read "$VAULT" "$NOTE"` and `obsidian-cli search "$VAULT" "<query>"` (Obsidian-native ranking); see [`skills/wiki-cli/SKILL.md`](../wiki-cli/SKILL.md)
- **mcp-obsidian** / **mcpvault** — `mcp__obsidian-vault__read_note`, `search_notes`; see [`skills/wiki/references/mcp-setup.md`](../wiki/references/mcp-setup.md)
- **filesystem** — Claude's `Read` and `Glob`/`Grep` tools (final floor; always works)

Full decision tree: [`wiki/references/transport-fallback.md`](../../wiki/references/transport-fallback.md). Quick mode (hot.md only) is transport-agnostic — always uses `Read`.

---

## Retrieval (v1.7+)

Standard and Deep modes start with `retrieve.py`, then excerpt — never `wiki/index.md`. Canonical policy: [`wiki/meta/token-discipline.md`](../../wiki/meta/token-discipline.md).

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
3. If not found, say "Not in quick cache. Run as standard query?" — do **not** Read `wiki/index.md`.

Do not open individual wiki pages in quick mode.

---

## Standard Query Workflow

1. **Retrieve first**: `python3 scripts/retrieve.py "<question>" --top 5`
2. **Excerpt** each candidate: `python3 scripts/wiki-excerpt.py <path> --tail 15 --budget-tokens 1800` — do not Read full fat concept pages.
3. If retrieve exits 10, fall back to `python3 scripts/wiki-resolve.py "<term>" --type any` + `rg`, then excerpt hits.
4. Follow wikilinks from excerpts to depth-2 for key entities (excerpt those too). No deeper.
5. **Synthesize** the answer in chat. Cite sources with wikilinks: `(Source: [[Page Name]])`.
6. **File by default** (save-first). A standard answer that synthesises two or more pages is saved to `wiki/questions/` without asking (see *Filing Answers Back*). Skip the save only when: the user said not to, the answer is a bare fact lookup, the answer is "not in the wiki" (go to Gap Handling), or one existing page already answers it verbatim (then link that page instead of duplicating). Say in one line where it was filed.
7. If the question reveals a **gap**: say "I don't have enough on X. Want to find a source?"

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

Read the minimum needed. Never Read catalog files (`index.md`, `_index.md`, `log.md`). See [`wiki/meta/token-discipline.md`](../../wiki/meta/token-discipline.md).

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

When filing an answer:

```yaml
---
type: question
title: "Short descriptive title"
question: "The exact query as asked."
answer_quality: solid
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: [question, <domain>]
related:
  - "[[Page referenced in answer]]"
sources:
  - "[[wiki/sources/relevant-source.md]]"
status: developing
---
```

Then write the answer as the page body. Include citations. Link every mentioned concept or entity. Write through `python3 scripts/wiki-page-write.py "wiki/questions/<title>.md" --content-file /tmp/answer.md` (allocates the address, validates frontmatter, takes the lock); never write the file directly. Frame the title as the question in Japanese 常体 (`〜か`), not as a topic. Quick mode never files.

**Close the loop on the concept side.** If the answer settles a bullet under a concept's `## 未解決の問い`, remove it and leave the trace: `python3 scripts/wiki-append.py "wiki/concepts/<concept>.md" --remove-question-containing "<問いの語>" --observation "[<主題節名>] <一文の答え> (回答 [[<question page>]])"`. If the answer raises a new open question, append it with `--question`. If the answer is really a *judgment on a proposition* (supported / contradicted with evidence on both sides), hand off to `wiki-thesis` instead of filing a `question` page.

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

python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD wiki-query | Short title\n- [[Question Title]]\n"
```

---

## Gap Handling

If the question cannot be answered from the wiki:

1. Say clearly: "I don't have enough in the wiki to answer this well."
2. Identify the specific gap: "I have nothing on [subtopic]."
3. Suggest: "Want to find a source on this? I can help you search or process one."
4. Do not fabricate. Do not answer from training data if the question is about the specific domain in this wiki.

---

## How to think (10-principle mapping)

When working on this skill, apply the 10-principle loop. See [`skills/think/SKILL.md`](../think/SKILL.md) for the canonical framework.

| # | Principle | Application here |
|---|-----------|-------------------|
| 1 | OBSERVE (ext) | Quick: trimmed hot window only. Standard/Deep: `retrieve.py` + `wiki-excerpt.py` per candidate. Never Read index.md. |
| 2 | OBSERVE (int) | Am I synthesizing from training-data memory when I should be citing wiki pages? Check the source of each claim. |
| 3 | LISTEN | What is the user's REAL question? The surface query is often a proxy for a deeper need. |
| 4 | THINK | Quick / standard / deep mode? Match depth to question complexity, not eagerness. |
| 5 | CONNECT (lat) | Are there pages I missed that would CHANGE the answer? Cross-check related pages before answering. |
| 6 | CONNECT (sys) | Trimmed hot (quick) + retrieve + excerpt + wiki-resolve fallback form a single retrieval pipeline. Catalogs are write-only for agents. |
| 7 | FEEL | Cite specific pages, not vague references. Future-me wants traceability back to the source page. |
| 8 | ACCEPT | When the wiki doesn't have the answer, say so explicitly. Don't fabricate from training data. |
| 9 | CREATE | The answer with citations + an offer to file the answer if it's worth keeping. |
| 10 | GROW | Questions the wiki can't answer are content gaps — log them as autoresearch inputs. |
