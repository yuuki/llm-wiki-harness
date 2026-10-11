---
name: wiki-ingest
description: "Generic wiki ingest for sources that have no specialized skill: web articles, blog posts, local text or Markdown files, images, pasted text, and batches of those. Reads the source, creates or updates source / entity / concept pages, cross-references them, and logs the operation. Papers go to wiki-ingest-paper, dissertations and long surveys to wiki-ingest-thesis, books to wiki-ingest-book, slide decks to wiki-ingest-slides, videos to wiki-ingest-video. Triggers on: ingest, process this source, add this to the wiki, read and file this, batch ingest, ingest this url."
---

# wiki-ingest: Source Ingestion

Read the source. Write the wiki. Cross-reference everything. A single source typically touches 8-15 wiki pages.

**Syntax standard**: Write all Obsidian Markdown using proper Obsidian Flavored Markdown. Wikilinks as `[[Note Name]]`, callouts as `> [!type] Title`, embeds as `![[file]]`, properties as YAML frontmatter. If the kepano/obsidian-skills plugin is installed, prefer its canonical obsidian-markdown skill for Obsidian syntax reference. Otherwise, follow the guidance in this skill.

## Commit after ingest

**取り込み成功後は必ず、今回のファイルだけをコミットする。**
`git add -- <file-list>` → staged diff確認 → `git commit -m "wiki: ingest | <source title>" -- <file-list>` → `git status` の順で実行する。バッチは `wiki: ingest | batch (<N> sources)` とする。明示的な保留または失敗時のみコミットしない。

---

## Writing pages

This vault's transport is fixed to the filesystem (wiki/CLAUDE.md rule 6). Wiki page writes go through the write helpers `python3 scripts/wiki-page-write.py` (new pages) and `python3 scripts/wiki-append.py` (existing pages), which take the per-file lock themselves. Do not `Write` a new source / entity / concept page if a helper fails; stop and report. `Edit` on an existing page is reserved for surgical fixes the helpers cannot express, and only those go inside a manual `wiki-lock.sh acquire`/`release`. Do not wrap a helper call in `wiki-lock.sh`: the helper takes the same lock, so the call deadlocks on itself. See [Concurrency](#concurrency).

## Page paths

- Source pages: `wiki/sources/@YYYY__SOURCE__Title.md` (conventions §4; the leading `@` is required).
- Entity and concept pages: the original name under `wiki/entities/` and `wiki/concepts/`. `python3 scripts/wiki-mode.py route entity "<name>"` / `route concept "<name>"` prints the path.

---

## Concurrency

**Multi-writer is safe:** per-file advisory locking prevents parallel writers from trampling the same page. Every wiki page write holds the per-file lock.

**Use the write helpers; they take the lock for you** (and collapse allocate → acquire → write → release into ONE call):

```bash
# New pages: address allocation + convention checks + lock + atomic write, batched
python3 scripts/wiki-page-write.py --batch "$TMPDIR/pages.json"

# Existing pages: inbox / questions / 関連 / 出典 / frontmatter sources, batched
python3 scripts/wiki-append.py --batch "$TMPDIR/appends.json"
```

Both exit `75` for the pages whose lock was held and keep going on the rest; each page reports JSON on stdout. Re-run the same batch to retry only the skipped pages (the appends are idempotent), or `wiki-catalog.py prepend-log` a `skipped lock` entry and move on. If a helper fails, do not `Write` a new source / entity / concept page; stop and report. The manual `wiki-lock.sh acquire` → `Edit` → `release` sequence is only for a surgical fix on an existing page that the helpers cannot express. Never wrap a helper call in `wiki-lock.sh` (self-deadlock). Treat rc=75 as "another writer is in flight; retry the same helper once after 2s, then skip rather than overwrite."

Properties:
- **Per-file granularity.** Locks key on `sha1(<vault-relative-path>)`; concurrent writes to DIFFERENT pages run in parallel.
- **Age-based staleness.** Default `STALE_AFTER_SEC=60`. A crashed holder unblocks in ≤60 seconds without manual intervention. See `scripts/wiki-lock.sh` header for the full semantics.
- **Cross-process release.** Release is `rm -f` (no PID match required). Skill authors are trusted to release locks they acquire; cross-skill release is allowed by design (a janitor running `wiki-lock clear-stale --max-age 0` is the canonical recovery path).

Sub-agent rules: sub-agents write pages only through `wiki-page-write.py` / `wiki-append.py`, which hold the lock for them. Agents do not run `scripts/allocate-address.sh` themselves (allocation outside the helper fails); a sub-agent going through `wiki-page-write.py` allocates safely, because the helper calls the allocator inside its lock. `--address c-NNNNNN` is only for an already-reserved value, not a reason to call the script.

---

## Delta Tracking

`.raw/.manifest.json` records what has been ingested, so unchanged sources are not re-processed. The file is over 2 MB and other sessions write to it: do not Read it and do not rewrite it by hand. `scripts/wiki-manifest.py` reads and writes one entry at a time under the manifest lock.

**Before ingesting a file:**

```bash
md5 -q <file> 2>/dev/null || md5sum <file> | cut -d' ' -f1
python3 scripts/wiki-manifest.py get ".raw/articles/<slug>.md"     # exit 3: not ingested yet
```

- The entry exists with the same `hash` → skip. Report: "Already ingested (unchanged). Use `force` to re-ingest."
- Missing, or the hash differs → proceed with ingest.

**After ingesting a file:**

```bash
python3 scripts/wiki-manifest.py put ".raw/articles/<slug>.md" --json '{"hash": "<md5>", "ingested_at": "YYYY-MM-DD", "pages_created": ["wiki/sources/@YYYY__SOURCE__Title.md", "wiki/entities/Person.md"], "pages_updated": ["wiki/concepts/Foo.md"]}'
```

Entries live at the top level of the manifest, keyed by the vault-relative `.raw/...` path. `put` merges the given fields into an existing entry; pass `--json-file <path>` when the JSON is long. Skip delta checking if the user says "force ingest" or "re-ingest".

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

**Do not skip this for articles/blog posts that contain figures, diagrams, charts, or screenshots.** Diagrams written as code (` ```mermaid ` blocks) count too: see § 6. Text-only sources (opinion pieces, pure prose) can skip it — use judgment, don't force images where none add value.

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
- If WebFetch's UA gets a 403 on the image host, retry with the browser UA above.
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

### 6. Mermaid / コードで描かれた図も掲載する

記事の図が画像ファイルでなく ` ```mermaid ` などの図コードで埋め込まれていることがある(defuddle / curl で得た本文に `graph TD` 等が残る)。画像と同じく**図は掲載対象**であり、「画像が無いから図なし」と判断しない。raw の本文に mermaid ブロックがあれば、次の手順で source ページへ載せる。

- 原本(`.raw/articles/<slug>-<date>.md`)のコードブロックをそのまま使う。図のコードを創作・補完しない。ノード・矢印・`style` 行は原本どおりに保つ。
- ノードとエッジのラベルは和訳してよい(`japanese-style` に従う)。構造と意味は変えない。
- 該当する記述の直後に ` ```mermaid ` ブロックを置き、その下に画像と同じ形式で 1 行の日本語キャプションを付ける(「元記事の図。何を示すかを 1〜2 文で」)。
- 図専用セクションは作らない。装飾だけの図は除外してよい(画像の選別基準と同じ)。
- 図コードが載っていることは `wiki-verify-ingest.py` の `embeds=` には数えられない。ブロック数をチャット報告に書く。

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
3. **Create** source summary in `wiki/sources/`. Use the source frontmatter schema from `wiki/meta/conventions.md` §2–§3. Assign an address per the **Address Assignment** section below. If this is a URL source with downloaded/selected images (§ Image Download & Embedding), embed them inline near the sections they support — do this in the same pass as writing the page, not as a follow-up edit.
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
      --file wiki/concepts/_index.md --text "$(cat <<'EOF'
    ### YYYY-MM-DD ingest | Source Title
    - New concept: [[Page 1]]
    EOF
    )"

    python3 scripts/wiki-catalog.py add-catalog-line \
      --file wiki/concepts/_index.md \
      --section "現行コンセプトカタログ" \
      --line "- [[New Concept]]"

    python3 scripts/wiki-catalog.py prepend-master --text "$(cat <<'EOF'
    ### YYYY-MM-DD ingest | Source Title
    - Source: [[@YYYY__SOURCE__Title]]
    EOF
    )"
    ```
    Pass multi-line `--text` through a heredoc. A `\n` inside double quotes is not a newline in bash and lands in the catalog as literal text.
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

Batch ingest is less interactive. For 30+ sources, expect significant processing time.

### Parallel mode (subagents)

Division of labor: subagents do per-source work; the orchestrator owns all shared state. This split exists because the shared files (indexes, log, manifest) are what parallel writers corrupt.

- Each subagent handles exactly one source end-to-end: fetch/raw storage, source page, entity/concept pages. Every wiki page write goes through `wiki-page-write.py` (new pages) or `wiki-append.py` (existing pages), which hold the per-file lock. Do not `Write` new pages if a helper fails. `wiki-lock.sh acquire`/`release` by hand is only for a surgical `Edit` on an existing page.
- Subagents do not touch the shared files: `wiki/index.md`, `wiki/{sources,entities,concepts}/_index.md`, `wiki/hot.md`, `wiki/log.md`, `wiki/overview.md`, `.raw/.manifest.json`. The orchestrator updates all of them in one pass after every subagent finishes.
- Subagents do not call `scripts/allocate-address.sh` directly (allocate without the helper fails). They may allocate when they write through `wiki-page-write.py`. Do not `Write` new source/entity/concept pages. Re-resolve shared person/org entities immediately before write; if they exist, `wiki-append.py` only. If another session overwrote a page, stop and ask — do not merge-recover a pile of entity files.
- CPU/network-heavy steps (PDF download, `pdftoppm` rendering, media transcription) contend for the same machine: run at most 3-4 subagents at a time; queue the rest.
- Each subagent returns a compact result: pages created/updated (paths), 1-sentence key insight, and any contradiction it noticed — the orchestrator needs these for the cross-reference pass, the log entries, and the report.
- One commit at the end covering the whole batch (respect the same commit policy as single ingest).
- Generic-article parallelism may pass this SKILL (or the helper / git / catalog rules from it). Do not apply the book/paper/thesis "no full SKILL" rule here.

---

## Token Discipline

Full procedure: [`wiki/meta/token-discipline.md`](../../../wiki/meta/token-discipline.md). **Never `Read` the full text of** `wiki/index.md`, `wiki/log.md`, `wiki/hot.md`, or `wiki/{sources,entities,concepts}/_index.md`. Catalogs are human/Obsidian artifacts, not agent input. For figures, run `python3 scripts/contact-sheet.py --manifest … --out "$TMPDIR/cs-<slug>" --index-txt` once and Read the sheets **before** any individual Read. Do not Read `image-*.png` or attachments without a sheet. Do not Read attachments whose figure number is already on the sheet or `index.txt`. `individual-reads` is the actual image-Read count excluding `sheet-*.png`. Zero sheets (no embedded images) means zero individual Reads.

For **book / paper / thesis fan-out only**, do not pass a full ingest SKILL.md to a subagent; use `.claude/skills/wiki-ingest-book/references/subagent-brief.md`, `.claude/skills/wiki-ingest-paper/references/subagent-brief.md`, or `.claude/skills/wiki-ingest-thesis/references/subagent-brief.md` (or only the procedures written there). Generic-article parallelism may still receive this SKILL. Paper vector crops go through `scripts/crop-paper-figures.py` (see `wiki-ingest-paper/references/figures.md`); do not hand-clip a full page.

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

- New pages → `python3 scripts/wiki-page-write.py --batch "$TMPDIR/pages.json"` (one call for all of them; replaces allocate → acquire → Write → release per page).
- Existing pages → `python3 scripts/wiki-append.py --batch "$TMPDIR/appends.json"` (one call for all of them; inbox, questions, `## 関連`, `## 出典`, frontmatter `sources`, `updated` and the date tag are applied together).
- **Never issue multiple `Edit` calls against one page.** Collect the whole delta for a page into a single helper operation. `--dry-run` on `wiki-append.py` shows the unified diff without writing.

### Bundle verification into one call

Post-ingest checks — frontmatter keys, H1, wikilink and embed resolution, unused attachments, duplicate addresses, expected page count, and the staged-set reconciliation before commit — run as one read-only call: `python3 scripts/wiki-verify-ingest.py --pages <paths> [--attachments-slug SLUG] [--match "<title>" --classify] [--staged-check]`, one line per finding. **Do not hand-roll bash loops for any of this**: each extra call costs a whole resident-context read, and verbose bash output is billed into that context too.

### Concept append target (conventions §8)

Concept bodies are **topic sections** (free-form `##` headings named after the subject) compiled by the recompile step, not by ingest. Ingest appends only to two fixed sections:

- `## 未編纂の観察` — the inbox. Legacy pages still have `## 横断的知見`; treat it as the same inbox (the scripts alias it). If neither exists, create `## 未編纂の観察` right before `## 関連`.
- `## 未解決の問い` — the work list.

Run `--outline` first; if the new observation supports or contradicts an existing claim, prefix it with `[<topic heading>]`. **Never edit topic sections or `## 定義` from ingest.** Never write page self-references ("既出", "本ページが集約してきた"). Do not add generic headings such as 知見 / 観察 / 考察 / まとめ / 横断的.

### Concept limits per ingest

- **Max 3 new** concept pages, **max 5 updates**. Overflow → log as `Deferred:` in the log entry; grow on the next related source. Also record each overflow candidate in the ledger: `python3 scripts/concept-candidates.py add --name <candidate> --source "[[@<this source>]]" --reason "cap exceeded"`. If `wiki-resolve.py` answered `ledger:<name>(<k> docs, pending)` the name is already waiting: add the mention with the same command, and only if it is `ready` (2 independent documents), the resolve output carries no `dialect-gate`, and it is not `is_create_blocked`, create it first within this ingest's new-concept budget and `promote` it (conventions §12 rule 5). When resolve finds no concept and returns `dialect-gate` (`dialect-gate:<n> pages` under `--compact`), do not create the concept: keep the observation on the source page and record it with `concept-candidates.py add --reason "dialect-gate"`. Do not use the research-interest profile (`wiki-profile.py`) to decide whether to create or defer a concept (conventions §12 rule 3).
- **Create threshold**: single-source terms stay on the source page. Create a concept only when resolve misses AND the term will cross sources (or is an obvious future hub).
- **Hub concepts** (have `## 子概念` or are huge): write insight to the nearest child, not the parent — unless the insight spans children.

### Entity stub / full

- First-seen people/orgs: `entity_tier: stub` in frontmatter + 2–3 lines (affiliation/role). Do not grow stub bodies.
- Promote to `entity_tier: full` on 2nd source, or start full for hubs (books, theses, recurring orgs/products).
- Existing pages without `entity_tier` are treated as `full`; do not retroactively rename.

### Shared catalog updates (orchestrator only)

Subagents do not touch shared catalog files. The orchestrator calls `wiki-catalog.py` after all subagents finish:

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
> The `[!contradiction]` callout type used below is not an Obsidian built-in. This vault has no CSS snippet for it, so it renders with the default callout styling.

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

- **Source files under `.raw/` are immutable.** Do not modify the files that users drop there (articles, transcripts, images). The `.raw/.manifest.json` delta tracker and its `address_map` are the only files under `.raw/` that `wiki-ingest` itself maintains. Treat every other file under `.raw/` as read-only source content.
- Do not create duplicate pages. Always run `wiki-resolve.py` before creating.
- Do not skip the log entry. Every ingest must be recorded via `wiki-catalog.py prepend-log`.
- Do not skip the hot cache update via `wiki-catalog.py prepend-hot`. It is what keeps future sessions fast.
- Do not `Read`+`Edit` catalog files (`index.md`, `hot.md`, `log.md`, `_index.md`) — use `wiki-catalog.py`.
- Do not embed every downloaded image indiscriminately. Selection is editorial judgment (§ Image Download & Embedding) — icons, ads, and decorative images stay out.
- Do not treat a failed image download as a reason to abort the whole ingest. Skip that one image, keep going.

---

## Address Assignment

Every newly created non-meta wiki page gets a stable address as the first key of its frontmatter:

```yaml
address: c-000042
```

`wiki-page-write.py` allocates it inside its lock, in the same call that writes the page, and records `address_map` when asked:

```bash
python3 scripts/wiki-page-write.py --batch "$TMPDIR/pages.json" --record-address-map
```

- Leave `address:` out of the content you pass for a new page. Do not run `scripts/allocate-address.sh` (allocation outside the helper fails) and do not Write or Edit `.vault-meta/address-counter.txt`: the counter changes only through `wiki-page-write.py`. If the helper is unavailable, stop and report; do not allocate by hand and do not `Write` the new page. `--address c-XXXXXX` is only for an already-reserved value.
- `--record-address-map` is opt-in because the manifest is edited by other processes; taking its lock on every page write serializes unrelated writes.
- Reuse, never re-allocate. A page that already has `address:` keeps it. On re-ingest of the same source (by `--force` or a changed hash), consult `address_map` in `.raw/.manifest.json` first and reuse the prior address for the target path. On a page rename, update the `address_map` key (old path → new path) and keep the value.
- No address for meta files (`_index.md`, `index.md`, `log.md`, `hot.md`, `overview.md`, `dashboard.md`, `dashboard.base`) or for fold pages under `wiki/folds/` (they use their own `fold_id`).
- In a batch, addresses are assigned page by page as each source is written; do not pre-reserve a block of counter values. Sub-agents dispatched only for research or review do not write pages and do not allocate.

---

## git commit

**既定動作**: 取り込みが成功したら、ユーザーが「コミットしない」「コミットを保留」「do not commit」などと明示しない限り、自動的にひとつのコミットを作成する。単一ソースはそのソースの取り込み完了後、バッチは全ソースの cross-reference pass 完了後にコミットする。失敗・delta tracking によるスキップ・ユーザーが明示した保留時はコミットしない。

コミットには、この取り込みで作成・更新した wiki ページ、`.raw/` の新規素材、選択した画像添付、`.raw/.manifest.json`、および共有 index / hot / log / overview の変更だけを含める。`git add wiki/ .raw/` のようなディレクトリ全体の blanket stage は使わず、既存の無関係な変更や他の作業者の変更をコミットに巻き込まない。

```bash
git add -- <ingestで作成・更新したファイル一覧>
git add -f .vault-meta/concept-candidates.json   # 候補台帳に積んだときだけ
git commit -m "wiki: ingest | <ソースタイトル>" -- <stage したのと同じパス>
```

- `.vault-meta/` は `.gitignore` の対象だが、台帳は追跡されている。`-f` を付けないと `git add` 全体が ignored で止まる。
- `git commit` にも stage したのと同じパスを渡して対象を限定する(wiki/CLAUDE.md 鉄則 8)。並行セッションが stage した変更を巻き込まないためである。

コミットメッセージの形式: `wiki: ingest | <ソースタイトル>`。タイトルは source ページの `title:` フィールドの値をそのまま使う。バッチは `wiki: ingest | batch (<N> sources)` とする。コミット前に staged diff がこの取り込みのファイルだけであることを確認し、コミット後に `git status` で検証する。
