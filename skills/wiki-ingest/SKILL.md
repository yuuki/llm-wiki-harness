---
name: wiki-ingest
description: "Ingest sources into the Obsidian wiki vault. Reads a source, extracts entities and concepts, creates or updates wiki pages, cross-references, and logs the operation. Supports files, URLs, and batch mode. Triggers on: ingest, process this source, add this to the wiki, read and file this, batch ingest, ingest all of these, ingest this url."
---

# wiki-ingest: Source Ingestion

Read the source. Write the wiki. Cross-reference everything. A single source typically touches 8-15 wiki pages.

**Syntax standard**: Write all Obsidian Markdown using proper Obsidian Flavored Markdown. Wikilinks as `[[Note Name]]`, callouts as `> [!type] Title`, embeds as `![[file]]`, properties as YAML frontmatter. If the kepano/obsidian-skills plugin is installed, prefer its canonical obsidian-markdown skill for Obsidian syntax reference. Otherwise, follow the guidance in this skill.

## Commit after ingest

**取り込み成功後は必ず、今回のファイルだけをコミットする。**
`git add -- <file-list>` → staged diff確認 → `git commit -m "wiki: ingest | <source title>"` → `git status` の順で実行する。バッチは `wiki: ingest | batch (<N> sources)` とする。明示的な保留または失敗時のみコミットしない。

---

## Transport (v1.7+)

Before mutating any vault file, consult `.vault-meta/transport.json` (auto-created by `bash scripts/detect-transport.sh`). Use the `preferred` transport per the fallback chain:

- **cli** — `obsidian-cli write "$VAULT" "$NOTE" < content.md` (or `append`, `property:set`); see [`skills/wiki-cli/SKILL.md`](../wiki-cli/SKILL.md)
- **mcp-obsidian** / **mcpvault** — `mcp__obsidian-vault__write_note` and friends; see [`skills/wiki/references/mcp-setup.md`](../wiki/references/mcp-setup.md)
- **filesystem** (this vault; final floor, always works) — wiki page writes go through the write helpers `python3 scripts/wiki-page-write.py` (new pages) and `python3 scripts/wiki-append.py` (existing pages), which take the per-file lock themselves. Do not `Write` a new source / entity / concept page if a helper fails; stop and report. `Edit` on an existing page is reserved for surgical fixes the helpers cannot express, and only those go inside a manual `wiki-lock.sh acquire`/`release`. NEVER wrap a helper call in `wiki-lock.sh` (self-deadlock). See [Concurrency](#concurrency-v17).

Full decision tree: [`wiki/references/transport-fallback.md`](../../wiki/references/transport-fallback.md).

---

## Mode awareness (v1.8+)

Before creating any new wiki page, consult the vault's methodology mode via `python3 scripts/wiki-mode.py route <type> "<name>"`. The router returns the vault-relative path where the page should be filed.

```bash
SRC_PATH=$(python3 scripts/wiki-mode.py route source "Karpathy 2025 LLM Wiki essay")
# generic:      wiki/sources/Karpathy-2025-LLM-Wiki-essay.md
# lyt:          wiki/notes/Karpathy-2025-LLM-Wiki-essay.md  (also update relevant MOC)
# para:         wiki/resources/incoming/Karpathy-2025-LLM-Wiki-essay.md
# zettelkasten: wiki/20260517123456-Karpathy-2025-LLM-Wiki-essay.md

ENT_PATH=$(python3 scripts/wiki-mode.py route entity "Andrej Karpathy")
CON_PATH=$(python3 scripts/wiki-mode.py route concept "Compounding Vault Pattern")
```

If `.vault-meta/mode.json` is absent, the router returns mode=generic paths (identical to v1.7 behavior). No special-casing needed in this skill.

Mode-specific follow-up:
- **LYT**: after filing the atomic note, update the relevant MOC (`wiki/mocs/<topic>-moc.md`) to link the new note. If no MOC exists for the topic, create one using `skills/wiki-mode/templates/lyt/moc-template.md`.
- **Zettelkasten**: filename already includes the timestamp ID. Populate the `id:` frontmatter field to match.
- **PARA**: new ingests land in `wiki/resources/incoming/` by default. Do NOT auto-guess the topic; leave in incoming/ for user review.

## Concurrency (v1.7+)

**Multi-writer is safe in v1.7.** The latent corruption bug from v1.6 — where two parallel sub-agents writing to the same page could silently trample each other — is closed by per-file advisory locking. Every wiki page write MUST hold the per-file lock.

**Use the write helpers; they take the lock for you** (and collapse allocate → acquire → write → release into ONE call):

```bash
# New pages: address allocation + convention checks + lock + atomic write, batched
python3 scripts/wiki-page-write.py --batch /tmp/pages.json

# Existing pages: inbox / questions / 関連 / 出典 / frontmatter sources, batched
python3 scripts/wiki-append.py --batch /tmp/appends.json
```

Both exit `75` for the pages whose lock was held and keep going on the rest; each page reports JSON on stdout. Re-run the same batch to retry only the skipped pages (the appends are idempotent), or `wiki-catalog.py prepend-log` a `skipped lock` entry and move on. If a helper fails, do not `Write` a new source / entity / concept page; stop and report. The manual `wiki-lock.sh acquire` → `Edit` → `release` sequence is only for a surgical fix on an existing page that the helpers cannot express. Never wrap a helper call in `wiki-lock.sh` (self-deadlock). Treat rc=75 as "another writer is in flight; retry the same helper once after 2s, then skip rather than overwrite."

Properties:
- **Per-file granularity.** Locks key on `sha1(<vault-relative-path>)`; concurrent writes to DIFFERENT pages run in parallel.
- **Age-based staleness.** Default `STALE_AFTER_SEC=60`. A crashed holder unblocks in ≤60 seconds without manual intervention. See `scripts/wiki-lock.sh` header for the full semantics.
- **Cross-process release.** Release is `rm -f` (no PID match required). Skill authors are trusted to release locks they acquire; cross-skill release is allowed by design (a janitor running `wiki-lock clear-stale --max-age 0` is the canonical recovery path).
- **The PostToolUse hook now defers `git add` if any locks are currently held**, so the auto-commit doesn't fire mid-ingest and produce torn commits. See `hooks/hooks.json`.

`wiki-lock` is unconditional in v1.7+ — there is no feature gate, no fallback. Skills that don't acquire locks are racing against any other writer. The script is in core, not opt-in.

Sub-agent rules: *sub-agents MAY write pages, but MUST hold the lock* — which `wiki-page-write.py` / `wiki-append.py` do for them. Agents MUST NOT run `scripts/allocate-address.sh` themselves (allocate without the helper fails). A sub-agent going through `wiki-page-write.py` may allocate safely (the helper calls allocate inside its lock). `--address c-NNNNNN` is only for an already-reserved value, not a reason to call the script.

---

## Delta Tracking

Before ingesting any file, check `.raw/.manifest.json` to avoid re-processing unchanged sources.

```bash
# Check if manifest exists
[ -f .raw/.manifest.json ] && echo "exists" || echo "no manifest yet"
```

**Manifest format** (create if missing):
```json
{
  "sources": {
    ".raw/articles/article-slug-2026-04-08.md": {
      "hash": "abc123",
      "ingested_at": "2026-04-08",
      "pages_created": ["wiki/sources/article-slug.md", "wiki/entities/Person.md"],
      "pages_updated": ["wiki/concepts/Foo.md"]
    }
  }
}
```

**Before ingesting a file:**
1. Compute a hash: `md5sum [file] | cut -d' ' -f1` (or `sha256sum` on Linux).
2. Check if the path exists in `.manifest.json` with the same hash.
3. If hash matches, skip. Report: "Already ingested (unchanged). Use `force` to re-ingest."
4. If missing or hash differs, proceed with ingest.

**After ingesting a file:**
1. Record `{hash, ingested_at, pages_created, pages_updated}` in `.manifest.json`.
2. Write the updated manifest back.

Skip delta checking if the user says "force ingest" or "re-ingest".

---

## URL Ingestion

Trigger: user passes a URL starting with `https://`.

Steps:

1. **Fetch** the page using WebFetch.
2. **Clean** (optional): if `defuddle` is available (`which defuddle 2>/dev/null`), run `defuddle [url]` to strip ads, nav, and clutter. Typically saves 40-60% tokens. Fall back to raw WebFetch output if not installed.
3. **Derive slug** from the URL path (last segment, lowercased, spaces→hyphens, strip query strings).
4. **Save** to `.raw/articles/[slug]-[YYYY-MM-DD].md` with a frontmatter header:
   ```markdown
   ---
   source_url: [url]
   fetched: [YYYY-MM-DD]
   ---
   ```
5. **Collect and download images** (see § Image Download & Embedding below) before writing the source page, so Step 3 of Single Source Ingest can embed them.
6. Proceed with **Single Source Ingest** starting at step 2 (file is now in `.raw/`).

---

## Image Download & Embedding (URL sources)

**Do not skip this for articles/blog posts that contain figures, diagrams, charts, or screenshots.** Text-only sources (opinion pieces, pure prose) can skip it — use judgment, don't force images where none add value.

### 1. Collect candidate image URLs

From the raw WebFetch/defuddle output (or by re-fetching the page HTML if the cleaned text stripped `<img>` tags), extract candidate image URLs:

- `<img src="...">` (prefer the largest `srcset` variant if present).
- `og:image` / `twitter:image` meta tags as a fallback when the body has no usable figures.
- Resolve relative URLs against the source URL.

Filter out noise before downloading anything:

- Data URIs (`data:image/...`).
- Filenames/paths matching `logo|icon|avatar|favicon|badge|pixel|tracking|sprite|button`.
- Decorative SVGs that are clearly UI chrome, not content figures.
- Anything below a rough 200×200px hint (from explicit `width`/`height` attributes) when that hint is available.

### 2. Download

```bash
mkdir -p ".raw/articles/<slug>/images"
curl -sL --max-time 20 --max-filesize 10485760 \
  -A "Mozilla/5.0" \
  -o ".raw/articles/<slug>/images/img-01.png" \
  "<image-url>"
```

- Follow redirects (`-L`), cap time and size so one bad URL can't hang the ingest.
- If a request fails (403/404, non-image content-type, truncated file), skip that image and move on — do not fail the whole ingest over one figure.
- If WebFetch's UA gets a 403 on the image host, retry with the browser UA above (see memory: WebFetch 403 UA fallback pattern applies to image downloads too).
- Verify each download is actually an image (`file "<path>"` or check magic bytes) before treating it as valid — HTML error pages saved with a `.png` extension are a common failure mode.

### 3. Select representative images (target: 2-6)

Not every downloaded image belongs in the note. Open each with the Read tool and judge:

| Priority | Keep | Skip |
|---|---|---|
| High | Architecture/system diagrams, flowcharts, taxonomy figures, key result charts/tables-as-image | Author headshots, ads, unrelated stock photos |
| Medium | Screenshots that are the article's actual subject (UI walkthroughs, dashboards) | Generic hero/banner images with no informational content |
| Low | Supplementary illustrations | Duplicate or near-duplicate images |

### 4. Copy selected images to the attachment folder

```bash
mkdir -p "wiki/sources/_attachments/<slug>"
cp ".raw/articles/<slug>/images/img-01.png" \
   "wiki/sources/_attachments/<slug>/fig01-architecture.png"
```

Rename to a content-describing filename (`figNN-<short-description>.png`), not the raw download name. `<slug>` matches the source's slug so images are discoverable next to the page that uses them. Delete the unused downloads under `.raw/articles/<slug>/images/` from the copy step onward — only kept selections need to survive in `wiki/sources/_attachments/`; the `.raw/` staging copy MAY be left as-is since `.raw/` is treated as immutable working material for the ingest, not something to prune further.

### 5. Embed in the source page

Place each image right after the paragraph/section it illustrates (not all bunched at the end), with a one-line Japanese caption:

```markdown
![[_attachments/<slug>/fig01-architecture.png]]
(記事内の図。何を示す図かを1〜2文で正確に説明する。)
```

If the source page context makes the image self-explanatory, a shorter caption is fine — but always caption in Japanese, consistent with the rest of the page (`japanese-style` conventions apply here too). Source ページ自身に由来する本文の主張と画像キャプションには、冗長な `(Source: …)` インライン引用を付けない。出典は `sources:` frontmatter、`## 出典`、raw ソースへのリンクで保持し、ページ番号が必要な画像キャプションは `p.N` のように記す。外部ソースとの比較、矛盾、source 自身から遡及できない主張には明示的な出典リンクを付ける。entity / concept ページの出典表記はこの省略ルールの対象外である。

### Sandbox note

Image downloads via `curl` are external egress. If the sandbox blocks the image host, retry with sandbox disabled (this will prompt the user for approval) rather than silently giving up on images.

---

## Image / Vision Ingestion

Trigger: user passes an image file path (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.avif`).

Steps:

1. **Read** the image file using the Read tool. Claude can process images natively.
2. **Describe** the image contents: extract all text (OCR), identify key concepts, entities, diagrams, and data visible in the image.
3. **Save** the description to `.raw/images/[slug]-[YYYY-MM-DD].md`:
   ```markdown
   ---
   source_type: image
   original_file: [original path]
   fetched: YYYY-MM-DD
   ---
   # Image: [slug]

   [Full description of image contents, transcribed text, entities visible, etc.]
   ```
4. Copy the image to `_attachments/images/[slug].[ext]` if it's not already in the vault.
5. Proceed with **Single Source Ingest** on the saved description file.

Use cases: whiteboard photos, screenshots, diagrams, infographics, document scans.

---

## Single Source Ingest

Trigger: user drops a file into `.raw/` or pastes content.

Steps:

1. **Read** the source completely. Do not skim.
2. **Discuss** key takeaways with the user. Ask: "What should I emphasize? How granular?" Skip this if the user says "just ingest it."
3. **Create** source summary in `wiki/sources/`. Use the source frontmatter schema from `references/frontmatter.md`. Assign an address per the **Address Assignment** section below. If this is a URL source with downloaded/selected images (§ Image Download & Embedding), embed them inline near the sections they support — do this in the same pass as writing the page, not as a follow-up edit.
   - Source ページ本文から自ページへの自己リンク(`[[@YYYY__SOURCE__Title]]`相当)を作らない。source 自身に由来する本文の主張と画像キャプションには`(Source: …)`インライン引用を付けず、出典は`sources:` frontmatter・`## 出典`・raw ソースへのリンクで保持する。外部 source との比較、矛盾、source 自身から遡及できない主張には明示的な出典リンクを付ける。他ページから source を参照するときだけ`@`付き source リンクを使う。
4. **Create or update** entity pages for every person, org, product, and repo mentioned. One page per entity. Assign addresses to new entity pages.
5. **Create or update** concept pages for significant ideas and frameworks. Assign addresses to new concept pages.
   - Steps 3–5 write through two helpers, one call each: **new** pages via `wiki-page-write.py --batch` (address + convention checks + lock + atomic write), **existing** pages via `wiki-append.py --batch` (inbox, questions, `## 関連`, `## 出典`, frontmatter `sources`, `updated`, date tag). See § Token Discipline → Bundle writes.
6. **Update** relevant domain page(s) if the big picture changed. Do NOT read or edit `_index.md` files — use `wiki-catalog.py` (step 10).
7. **Update** `wiki/overview.md` if the big picture changed.
8. **Check for contradictions.** If new info conflicts with existing pages, add `> [!contradiction]` callouts on both pages.
9. **Refresh retrieve index** for all created/updated pages:
   ```bash
   python3 scripts/wiki-retrieve-refresh.py --pages wiki/sources/Foo.md wiki/concepts/Bar.md --no-llm
   ```
10. **Update shared catalogs via scripts** (never `Read`+`Edit` on catalog files). `wiki-catalog.py` locks itself — do not wrap it in `wiki-lock.sh`. See § Token Discipline for full procedure:
    ```bash
    python3 scripts/wiki-catalog.py prepend-log --text "$(cat <<'EOF'
    ## [YYYY-MM-DD] ingest | Source Title
    - Source: `.raw/articles/filename.md`
    - Summary: [[Source Title]]
    - Pages created: [[Page 1]], [[Page 2]]
    - Pages updated: [[Page 3]], [[Page 4]]
    - Key insight: One sentence on what is new.
    EOF
    )"

    python3 scripts/wiki-catalog.py prepend-hot --text "$(cat <<'EOF'
    ## YYYY-MM-DD | ingest | Source Title
    - Focus: [[Source Title]]. Key takeaway.
    - Key insight: One sentence.
    - New: [[Page 1]], [[Page 2]]
    - Updated: [[Page 3]], [[Page 4]]
    EOF
    )"

    python3 scripts/wiki-catalog.py prepend-changelog \
      --file wiki/concepts/_index.md --text "### YYYY-MM-DD ingest | Source Title\n- New concept: [[Page 1]]\n"

    python3 scripts/wiki-catalog.py add-catalog-line \
      --file wiki/concepts/_index.md \
      --section "現行コンセプトカタログ" \
      --line "- [[New Concept]]"

    python3 scripts/wiki-catalog.py prepend-master --text "### YYYY-MM-DD ingest | Source Title\n- Source: [[Source Title]]\n"
    ```
    Repeat `prepend-changelog` for `wiki/entities/_index.md` and `wiki/sources/_index.md` as needed. Log overflow concept candidates as `Deferred:` in the log entry.

---

## Batch Ingest

Trigger: user drops multiple files or says "ingest all of these." The user does NOT need to ask for parallelism — 3+ independent sources default to parallel mode below.

Steps:

1. List all files to process. Confirm with user before starting.
2. Choose the execution mode:
   - **1-2 sources, or sources that heavily share pages**: process sequentially following the single ingest flow. Defer cross-referencing between sources until step 3.
   - **3+ independent sources**: fan out subagents, one per source (protocol below).
3. After all sources: do a cross-reference pass. Look for connections between the newly ingested sources.
4. Update shared catalogs once at the end via `wiki-catalog.py` (not per-source). See § Token Discipline.
5. Report: "Processed N sources. Created X pages, updated Y pages. Here are the key connections I found."

Batch ingest is less interactive. For 30+ sources, expect significant processing time. Check in with the user after every 10 sources.

### Parallel mode (subagents)

Division of labor: subagents do per-source work; the orchestrator owns all shared state. This split exists because the shared files (indexes, log, manifest) are what parallel writers corrupt.

- Each subagent handles exactly one source end-to-end: fetch/raw storage, source page, entity/concept pages. Every wiki page write goes through `wiki-page-write.py` (new pages) or `wiki-append.py` (existing pages), which hold the per-file lock. Do not `Write` new pages if a helper fails. `wiki-lock.sh acquire`/`release` by hand is only for a surgical `Edit` on an existing page.
- Subagents MUST NOT touch the shared files: `wiki/index.md`, `wiki/{sources,entities,concepts}/_index.md`, `wiki/hot.md`, `wiki/log.md`, `wiki/overview.md`, `.raw/.manifest.json`. The orchestrator updates all of them in one pass after every subagent finishes.
- Subagents MUST NOT call `scripts/allocate-address.sh` directly (allocate without the helper fails). They MAY allocate when they write through `wiki-page-write.py`. Do not `Write` new source/entity/concept pages. Re-resolve shared person/org entities immediately before write; if they exist, `wiki-append.py` only. If another session overwrote a page, stop and ask — do not merge-recover a pile of entity files.
- CPU/network-heavy steps (PDF download, `pdftoppm` rendering, media transcription) contend for the same machine: run at most 3-4 subagents at a time; queue the rest.
- Each subagent returns a compact result: pages created/updated (paths), 1-sentence key insight, and any contradiction it noticed — the orchestrator needs these for the cross-reference pass, the log entries, and the report.
- One commit at the end covering the whole batch (respect the same commit policy as single ingest).
- Generic-article parallelism may pass this SKILL (or the helper / git / catalog rules from it). Do not apply the book/paper/thesis "no full SKILL" rule here.

---

## Token Discipline

Full procedure: [`wiki/meta/token-discipline.md`](../../wiki/meta/token-discipline.md). **Never `Read` the full text of** `wiki/index.md`, `wiki/log.md`, `wiki/hot.md`, or `wiki/{sources,entities,concepts}/_index.md`. Catalogs are human/Obsidian artifacts, not agent input. For figures, run `python3 scripts/contact-sheet.py --manifest … --out "$TMPDIR/cs-<slug>" --index-txt` once and Read the sheets **before** any individual Read. Do not Read `image-*.png` or attachments without a sheet. Do not Read attachments whose figure number is already on the sheet or `index.txt`. `individual-reads` is the actual image-Read count excluding `sheet-*.png`. Zero sheets (no embedded images) means zero individual Reads.

For **book / paper / thesis fan-out only**, do not pass a full ingest SKILL.md to a subagent; use `.claude/skills/wiki-ingest-book/references/subagent-brief.md`, `.claude/skills/wiki-ingest-paper/references/subagent-brief.md`, or `.claude/skills/wiki-ingest-thesis/references/subagent-brief.md` (or only the procedures written there). Generic-article parallelism may still receive this SKILL.

**Bundle resolve and excerpt too, not just writes.** Both take many arguments in one call: resolve every name of this ingest in a single `wiki-resolve.py ... --compact` call and read every page you need in a single `wiki-excerpt.py P1 P2 P3` call. Never call either one name / one page at a time — that is one resident-context read per name, plus 4k characters of unused `aliases`/`one_liner` per default-shaped query.

### Discover existing pages

```bash
# Every entity and concept name of this source in ONE call. Prefixes override --type.
python3 scripts/wiki-resolve.py entity:"Hellerstein" entity:"Meta FAIR" \
  concept:"RDMA" concept:"異常検知" any:"RoCEv2" --compact
python3 scripts/wiki-resolve.py "RoCEv2" --type any --top 8   # one name → full JSON
```

`--compact` prints one TSV line per query — `<query>\t<type>\tHIT|NONE\t<path>(<match> <score>); ...`, paths relative to `wiki/`, top 3 by default. Full JSON keeps `candidates[].path` (`{results:[...]}` when several queries are given). `--paths-only` prints a `# <query>` header then path lines; pass **only the path lines** to `wiki-excerpt.py` (`#` lines are query separators, not paths). Zero hits → new page candidate.

### Read page bodies (excerpt only)

```bash
python3 scripts/wiki-excerpt.py "wiki/concepts/異常検知.md" "wiki/concepts/RDMA.md" --outline   # maps of both (headings + bold claim lines)
python3 scripts/wiki-excerpt.py "wiki/concepts/異常検知.md" "wiki/concepts/RDMA.md" \
  --sections 定義,子概念,未解決の問い,未編纂の観察 --tail 15 --budget-tokens 1800
```

If you use `--total-budget`, set it to at least `N * --budget-tokens` (9000 or more when the update cap is 5 and no theme-chunk hubs are added). Do not treat a skipped page as read. Pages are separated by a `=== <path> ===` line; `--fm-keys none` drops frontmatter when you only want bodies.

### Theme chunks (`wiki-clusters.py`) — concept `## 関連` only

Do **not** Read `.vault-meta/clusters.json`. If the CLI fails, fall back to resolve / retrieve only. Never open the JSON to fill gaps.

Before writing new or updated **concept** pages, propose `- 概念:` links from the skill-world theme chunk. Do not lookup entity or source. Do not write `community:` or frontmatter `related:`. Do not add reciprocal related on the hub side.

1. Take up to 5 existing **concept** hits from this ingest's `wiki-resolve.py --compact` (score descending). `lookup` each.
2. Trust the last refresh cache. Run `python3 scripts/wiki-clusters.py build` once only when `lookup` exits 3 because the cache is missing. Do not rebuild for staleness mid-ingest. If that refresh reported `clusters_ok=false`, skip theme chunks.
3. For each `on_backbone: true` id, run `python3 scripts/wiki-clusters.py members <id> --type concept --top 20`. Drop self, this ingest's new names, and names already on `- 概念:`.
4. Of hubs not already in the resolve set, take at most 3 in `members` order. Do not open a 4th. Do not re-read a hub already on the excerpt list. Put extras on the **same** `wiki-excerpt.py` call as the concepts you are updating. `--budget-tokens 1800`. `--total-budget` ≥ `(updated concepts + extra hubs) * 1800`. A skipped page is unread; do not add it.
5. Add a hub to `- 概念:` only when the excerpt actually touches this source's subject. Cap: 5 concept related per page including existing; at most 3 of those from theme chunks. These 3 do not consume the new-3 / update-5 concept page budget.
6. New pages: put `- 概念: [[...]] / [[...]]` in the initial `wiki-page-write.py` body. Updates: the same page's `wiki-append.py --batch` `related.概念`. One batch with inbox / sources.

If the cache is missing, every lookup is off-backbone, or no excerpted hub touches the source, keep resolve-only related. Never open the JSON to fill gaps.

Never read a full fat concept/entity page. Patch the relevant section tail; do not re-read the whole file to edit one field.

### Bundle writes into one call

Input tokens are dominated by cache reads, so cost ≈ Σ(resident context at each API call): **every tool call you remove saves one whole context read.** Writes must therefore be batched, not drip-fed.

- New pages → `python3 scripts/wiki-page-write.py --batch /tmp/pages.json` (one call for all of them; replaces allocate → acquire → Write → release per page).
- Existing pages → `python3 scripts/wiki-append.py --batch /tmp/appends.json` (one call for all of them; inbox, questions, `## 関連`, `## 出典`, frontmatter `sources`, `updated` and the date tag are applied together).
- **Never issue multiple `Edit` calls against one page.** Collect the whole delta for a page into a single helper operation. `--dry-run` on `wiki-append.py` shows the unified diff without writing.

### Bundle verification into one call

Post-ingest checks — frontmatter keys, H1, wikilink and embed resolution, unused attachments, duplicate addresses, expected page count, and the staged-set reconciliation before commit — run as one read-only call: `python3 scripts/wiki-verify-ingest.py --pages <paths> [--attachments-slug SLUG] [--match "<title>" --classify] [--staged-check]`, one line per finding. **Do not hand-roll bash loops for any of this**: each extra call costs a whole resident-context read, and verbose bash output is billed into that context too.

### Concept append target (conventions §8)

Concept bodies are **topic sections** (free-form `##` headings named after the subject) compiled by the recompile step, not by ingest. Ingest appends only to two fixed sections:

- `## 未編纂の観察` — the inbox. Legacy pages still have `## 横断的知見`; treat it as the same inbox (the scripts alias it). If neither exists, create `## 未編纂の観察` right before `## 関連`.
- `## 未解決の問い` — the work list.

Run `--outline` first; if the new observation supports or contradicts an existing claim, prefix it with `[<topic heading>]`. **Never edit topic sections or `## 定義` from ingest.** Never write page self-references ("既出", "本ページが集約してきた"). Do not add generic headings such as 知見 / 観察 / 考察 / まとめ / 横断的.

### Concept limits per ingest

- **Max 3 new** concept pages, **max 5 updates**. Overflow → log as `Deferred:` in the log entry; grow on the next related source. Also record each overflow candidate in the ledger: `python3 scripts/concept-candidates.py add --name <candidate> --source "[[@<this source>]]" --reason "cap exceeded"`. If `wiki-resolve.py` answered `ledger:<name>(<k> docs, pending)` the name is already waiting: add the mention with the same command, and if it is `ready` (2 independent documents) create it first within this ingest's new-concept budget and `promote` it (conventions §12 rule 5). When torn between creating and deferring, check `python3 scripts/wiki-profile.py` (a 25-line digest; do not Read the profile file itself; exit 3 means skip interest gating): out-of-scope terms stay on the source page, core-interest candidates get created first at their second document (conventions §12 rule 3).
- **Create threshold**: single-source terms stay on the source page. Create a concept only when resolve misses AND the term will cross sources (or is an obvious future hub).
- **Hub concepts** (have `## 子概念` or are huge): write insight to the nearest child, not the parent — unless the insight spans children.

### Entity stub / full

- First-seen people/orgs: `entity_tier: stub` in frontmatter + 2–3 lines (affiliation/role). Do not grow stub bodies.
- Promote to `entity_tier: full` on 2nd source, or start full for hubs (books, theses, recurring orgs/products).
- Existing pages without `entity_tier` are treated as `full`; do not retroactively rename.

### Shared catalog updates (orchestrator only)

Subagents MUST NOT touch shared catalog files. The orchestrator calls `wiki-catalog.py` after all subagents finish:

- `prepend-log` → `wiki/log.md`
- `prepend-hot` → `wiki/hot.md` (auto-trims to ~2000 tokens / 5 entries)
- `prepend-changelog --file wiki/{sources,entities,concepts}/_index.md`
- `add-catalog-line --section "現行コンセプトカタログ"` when adding a new concept
- `prepend-master` → `wiki/index.md` changelog section

`wiki-catalog.py` takes its own lock. Do not `wiki-lock.sh acquire` around catalog commands.

### After ingest

```bash
python3 scripts/wiki-retrieve-refresh.py --pages <created/updated paths> --no-llm
```

Keep wiki pages short (100–300 lines). Split or child-out when a page grows beyond 300 lines.

### 計測

コミット後に `python3 scripts/usage-report.py --self --log-line` を実行し、出力の 1 行を**チャットの完了報告に含める**。`wiki/log.md` には書かない(log エントリはセッション終了前に書かれるため、そこへ載せた数値は確定値にならない)。`--self` は `CLAUDE_CODE_SESSION_ID`(Claude Code)、`CODEX_THREAD_ID`(Codex。無ければ `CODEX_SESSION_ID`)、`CURSOR_CONVERSATION_ID`(Cursor)をこの順で見る。どれも空なら `--session <このセッションの ID>`。Cursor / Codex 経由でも `--latest` には落とさない。Cursor の数値は transcript と composer スナップショットからの概算で、行末が `Cursor概算` になる。Codex のトークンは `~/.codex/sessions/**/rollout-*.jsonl` の `token_usage_record` の実測で、行末が `Codex定価目安` になる。Codex Plus の実請求ではなく、Claude / Cursor の数値と混ぜて前後比較しない。

- 複数 ingest をまたぐ振り返りと改善の前後比較には `python3 scripts/usage-report.py --since YYYY-MM-DD` を使う。
- subagent(Task / Agent)の使用量は親セッションのログに含まれない。並列 ingest ではこの数値はオーケストレータ側だけの値になる。

---

## Contradictions

> [!note] Custom callout dependency
> The `[!contradiction]` callout type used below is a **custom callout** defined in `.obsidian/snippets/vault-colors.css` (auto-installed by `/wiki` scaffold). It renders with reddish-brown styling and an alert-triangle icon when the snippet is enabled. If the snippet is missing, Obsidian falls back to default callout styling, so the page still works without the visual flourish. See [[skills/wiki/references/css-snippets.md]] for the four custom callouts (`contradiction`, `gap`, `key-insight`, `stale`).

When new info contradicts an existing wiki page:

On the existing page, add:
```markdown
> [!contradiction] Conflict with [[New Source]]
> [[Existing Page]] claims X. [[New Source]] says Y.
> Needs resolution. Check dates, context, and primary sources.
```

On the new source summary, reference it:
```markdown
> [!contradiction] Contradicts [[Existing Page]]
> This source says Y, but existing wiki says X. See [[Existing Page]] for details.
```

Do not silently overwrite old claims. Flag and let the user decide.

---

## What Not to Do

- **Source files under `.raw/` are immutable.** Do not modify the files that users drop there (articles, transcripts, images). The `.raw/.manifest.json` delta tracker and its `address_map` (DragonScale Mechanism 2) are the only files under `.raw/` that `wiki-ingest` itself maintains. Treat every other file under `.raw/` as read-only source content.
- Do not create duplicate pages. Always run `wiki-resolve.py` before creating.
- Do not skip the log entry. Every ingest must be recorded via `wiki-catalog.py prepend-log`.
- Do not skip the hot cache update via `wiki-catalog.py prepend-hot`. It is what keeps future sessions fast.
- Do not `Read`+`Edit` catalog files (`index.md`, `hot.md`, `log.md`, `_index.md`) — use `wiki-catalog.py`.
- Do not embed every downloaded image indiscriminately. Selection is editorial judgment (§ Image Download & Embedding) — icons, ads, and decorative images stay out.
- Do not treat a failed image download as a reason to abort the whole ingest. Skip that one image, keep going.

---

## Address Assignment (DragonScale Mechanism 2 MVP)

**Opt-in feature**. DragonScale address assignment runs only if `scripts/allocate-address.sh` is present AND `.vault-meta/` exists. Otherwise, skip this entire section and proceed with ingest normally.

**Feature detection (run at start of every ingest)**:

```bash
if [ -x ./scripts/allocate-address.sh ] && [ -d ./.vault-meta ]; then
  DRAGONSCALE_ADDRESSES=1
else
  DRAGONSCALE_ADDRESSES=0
fi
```

When `DRAGONSCALE_ADDRESSES=0`, pages are created without an `address:` frontmatter field, and `wiki-lint`'s Address Validation section is skipped entirely (missing addresses are not flagged in any severity). This preserves default plugin behavior for vaults that have not adopted DragonScale.

When `DRAGONSCALE_ADDRESSES=1`, proceed with the rest of this section.

---

Every **newly created non-meta wiki page** gets a stable address in its frontmatter:

```yaml
address: c-000042
```

Format: `c-<6-digit-counter>`. The `c-` prefix stands for "creation-order counter." Zero-padded.

Rollout baseline: **2026-04-23** (Phase 2 ship date). Pages with `created:` >= this date are post-rollout and MUST have an address (unless excluded below). Pages with `created:` earlier are legacy-exempt until a deliberate backfill pass assigns `l-NNNNNN` addresses.

### Required tool: `scripts/allocate-address.sh`

Address allocation is delegated to an atomic Bash helper. The helper uses `flock` on `.vault-meta/.address.lock` to prevent read-use-increment races and recovers the counter by scanning existing frontmatter if the counter file is missing.

Ingest agents do not run `allocate-address.sh`. Allocate without the helper fails. `wiki-page-write.py` calls it inside its lock. `--address` is only for an already-reserved value.

**CRITICAL**: never use the Write or Edit tool on `.vault-meta/address-counter.txt`. That would fire the PostToolUse hook, which runs `git add wiki/ .raw/` and can accidentally commit unrelated pending wiki changes under a generic message. Counter mutation is **only** permitted through `wiki-page-write.py`.

### Helper modes

- `./scripts/allocate-address.sh --peek` — prints the next value without reserving (safe, read-only). Used by wiki-lint.
- `./scripts/allocate-address.sh --rebuild` — recomputes the counter from the highest observed `c-NNNNNN` in existing frontmatter. Never resets to 1 silently if pages already have addresses. Run this if the counter file is suspected corrupt.

### Assignment procedure (per new page)

`wiki-page-write.py` does all three steps in the same call that writes the page — it allocates when the content has no `address:`, inserts it as the first frontmatter key, and records `address_map` when asked:

```bash
python3 scripts/wiki-page-write.py --batch /tmp/pages.json --record-address-map
```

If `wiki-page-write.py` is unavailable, stop and report. Do not allocate by hand and do not `Write` the new page.

`--record-address-map` is opt-in because the manifest is edited by other processes; taking its lock on every page write serializes unrelated writes. `--address c-XXXXXX` is only for an already-reserved value.

### `address_map` in `.raw/.manifest.json`

```json
{
  "sources": { ... },
  "address_map": {
    "wiki/concepts/Example.md": "c-000042",
    "wiki/entities/Another.md": "c-000043"
  }
}
```

On re-ingest of the same source (whether by `--force` or a changed hash), always consult `address_map` first. If the target page path has a prior address, REUSE it. Do not allocate a new one.

On a page rename, the skill must update the `address_map` key (old path -> new path) while preserving the address value.

### Exclusions (do NOT assign an address to)

- Meta files: `_index.md`, `index.md`, `log.md`, `hot.md`, `overview.md`, `dashboard.md`, `dashboard.base`, `Wiki Map.md`, `getting-started.md`.
- Fold pages under `wiki/folds/` (they use their own deterministic `fold_id`).
- Pre-rollout legacy pages (`created:` < 2026-04-23). Legacy pages get `l-NNNNNN` addresses only via a deliberate backfill operation.

### Idempotency rules

- If a page being (re)written already has an `address:` field in its current content, REUSE it. Do not allocate a new one.
- If a source is re-ingested and `address_map` has a mapping for the target path, reuse that mapping.
- If the source has been ingested before AND the target page has no address AND the page `created:` date is post-rollout, allocate an address and record it. This covers the case where an older ingest produced a page before Phase 2 rollout; the rollout cutoff still applies (pages dated pre-2026-04-23 stay legacy).

### Concurrency policy

- **Single-writer only** in Phase 2. Do not run parallel ingests from multiple Claude sessions or sub-agents that assign addresses. The `flock` in the helper prevents counter corruption but does not serialize page writes themselves.
- Sub-agents (codex, general-purpose) that are dispatched for research or review MUST NOT call the allocator. They are read-only in this respect.
- Multi-writer support is a deferred feature.

### Batch ingest

Assign addresses sequentially during single-source-ingest for each source. Do not pre-reserve a block of counter values. The helper is cheap (one lock, one integer read/write).

---

## git commit

**既定動作**: 取り込みが成功したら、ユーザーが「コミットしない」「コミットを保留」「do not commit」などと明示しない限り、自動的にひとつのコミットを作成する。単一ソースはそのソースの取り込み完了後、バッチは全ソースの cross-reference pass 完了後にコミットする。失敗・delta tracking によるスキップ・ユーザーが明示した保留時はコミットしない。

コミットには、この取り込みで作成・更新した wiki ページ、`.raw/` の新規素材、選択した画像添付、`.raw/.manifest.json`、および共有 index / hot / log / overview の変更だけを含める。`git add wiki/ .raw/` のようなディレクトリ全体の blanket stage は使わず、既存の無関係な変更や他の作業者の変更をコミットに巻き込まない。

```bash
git add -- <ingestで作成・更新したファイル一覧>
git commit -m "wiki: ingest | <ソースタイトル>"
```

コミットメッセージの形式: `wiki: ingest | <ソースタイトル>`。タイトルは source ページの `title:` フィールドの値をそのまま使う。バッチは `wiki: ingest | batch (<N> sources)` とする。コミット前に staged diff がこの取り込みのファイルだけであることを確認し、コミット後に `git status` で検証する。

---

## How to think (10-principle mapping)

When working on this skill, apply the 10-principle loop. See [`skills/think/SKILL.md`](../think/SKILL.md) for the canonical framework.

| # | Principle | Application here |
|---|-----------|-------------------|
| 1 | OBSERVE (ext) | Read the source file completely before extracting anything. No shortcuts on long sources. |
| 2 | OBSERVE (int) | Am I biased toward the source's framing? Where do my disagreements live? Note them as contradiction callouts. |
| 3 | LISTEN | The user's source-selection intent — what made THIS source worth ingesting, and what is the user hoping to extract? |
| 4 | THINK | Which entities deserve pages? Which concepts? What cross-references? What contradictions with existing pages? |
| 5 | CONNECT (lat) | This source's claims vs other sources already in the wiki. Contradictions are the highest-signal finding. |
| 6 | CONNECT (sys) | `wiki-mode.py route` for paths + `wiki-lock.sh` for safety + `wiki-resolve.py`/`wiki-excerpt.py` for discovery + `wiki-catalog.py` for catalog visibility. |
| 7 | FEEL | A page that compounds — useful in 6 months, not just today. Skip filler; favor synthesis over transcription. |
| 8 | ACCEPT | Not every claim is wiki-worthy. Editorial judgment is part of ingest, not a bug to remove. |
| 9 | CREATE | Source + entity + concept pages with full frontmatter; cross-references; contradiction callouts where needed. |
| 10 | GROW | Contradictions found mid-ingest are the most valuable wiki signal. File them as questions for follow-up, not silently. |
