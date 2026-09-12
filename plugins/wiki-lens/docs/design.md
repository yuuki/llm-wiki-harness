# Wiki Lens — Design Document

This document records the goals, constraints, and design decisions behind Wiki Lens, including the alternatives that were considered and rejected. For the module-level *how*, see [`implementation.md`](implementation.md).

## 1. Context and problem

This vault has two layers:

- a **primary note layer** (`papers/`, `research/`, `notes/`, `structures/`, …) curated by a human, and
- an **LLM wiki layer** (`wiki/{sources,entities,concepts,questions}/`) grown by `claude-obsidian` ingestion skills.

The wiki layer grows fast and mechanically (thousands of pages, tens of thousands of links), which raises questions that plain file browsing cannot answer:

- *Is the wiki healthy?* — dead links, orphan pages, stubs that deserve promotion, ambiguous basenames.
- *What is its shape?* — thematic clusters, hubs, the skeleton behind the page count.
- *What is related to what I am reading right now?*
- *How is it growing?* — what appeared or matured between two points in time.
- *How do the type strata connect?* — which sources feed which concepts.

Wiki Lens answers each question with a dedicated view over a single shared index.

## 2. Design principles

1. **Strictly read-only.** The plugin contains no vault-writing API calls of any kind. It is a lens, not an editor. This is a hard boundary: repairs suggested by the Health view are executed by the human (or by wiki skills), never by this plugin. Presentation prefs (All Nodes type checkboxes) use Obsidian `saveData` into the plugin's `data.json` only.
2. **Index from `metadataCache` only.** The index never reads file bodies; it consumes frontmatter, `resolvedLinks`, and `unresolvedLinks` that Obsidian has already computed. A full rebuild of ~4k pages takes on the order of tens of milliseconds, so we can afford to rebuild from scratch on every change (debounced) instead of maintaining incremental state. The single exception is the timeline's best-effort read of `wiki/log.md`, which is annotation-only (see §5.4).
3. **Local only.** No network requests. The vault contains private material (`z99_private/`), so everything must run inside the Obsidian process.
4. **Exclude catalog mega-hubs from the graph.** `index.md`, `log.md`, `hot.md`, `_index` files, `meta/`, and `folds/` link to nearly everything; counting them would distort degrees, communities, and layouts. They are excluded from node targets. Only the dead-link *referrer* scan covers all of `wiki/` — an audit should see everything.
5. **Prune stubs by default, keep them reachable.** Seed entity stubs are ~94% of all entities and carry almost no information. Global views exclude them by default (with a toggle); the Local Lens includes them but dims them, because in a local context completeness beats pruning.
6. **Be honest about estimated data.** Where a signal is a proxy (edge appearance time, "updated" classification), the UI says so permanently, not in a dismissible tooltip.
7. **Match the Obsidian theme.** Colors are resolved from CSS variables (`--text-normal`, `--background-*`) at creation time and on `css-change`, never per frame.
8. **One source of truth, pure derivations.** `GraphIndex` holds structure; everything else (backbone filter, PPR, communities, timeline model) is a pure function over it. Views own only presentation state.

## 3. Architecture overview

```
                 Obsidian metadataCache
                          │
                    GraphIndex  (core/graph-index) — nodes, edges, dead targets
                          │  "updated" event
      ┌──────────┬────────┼──────────┬─────────────┬─────────────┐
      │          │        │          │             │             │
 HealthView  GraphView  GapView   LocalLens     TimeView     LayerView
 (tables)    (sigma)    (sigma)   (sigma)       (sigma)      (three.js)
                │          │         │             │
          buildBackbone  buildBackbone buildLens  buildBackbone + buildTimeline
          + louvain      + louvain     (PPR)      + log-parser (annotation)
                         + analyzeGaps
```

- **core/** is UI-free and testable in isolation: index, classification rules, graph filters, PPR, community/meta-graph aggregation, timeline model, log parser.
- **views/** are Obsidian `ItemView`s. The three 2D graph views share `SigmaBaseView` (sigma.js lifecycle, chunked ForceAtlas2, hover highlighting, themed labels). The 3D view is deliberately *not* a `SigmaBaseView` — it shares nothing with sigma.

## 4. Cross-cutting decisions

### 4.1 Rebuild-from-scratch indexing

*Why not incremental updates?* At this scale a full scan is tens of milliseconds; incremental maintenance of degrees, dead-target maps, and basename tables would add invariants that can silently drift. A 500 ms debounce on `resolved`/`rename`/`delete` events absorbs bursts.

### 4.2 Louvain communities instead of the `domain` field

The wiki's `domain` frontmatter has 300+ free-text variants and is useless as a classification axis. Thematic structure is therefore *computed* from the link topology (Louvain, edge weight = link count) and labeled by each cluster's top-2 in-degree members.

This on-screen Directed Louvain is not the skill-world theme-chunk recipe. Agents read undirected Blondel from `scripts/wiki-clusters.py` ([`clusters-for-skills.md`](clusters-for-skills.md), [`theme-chunks-algorithm.md`](theme-chunks-algorithm.md)); mismatched partitions are not a defect.

### 4.3 ForceAtlas2 on the main thread, chunked

*Why not a Web Worker?* It would require esbuild bundle splitting for a workload that completes in a few seconds at worst. Instead, layout runs 5 iterations per animation frame (total iterations scale with graph order, capped at 600), keeping the UI responsive.

### 4.4 Label policy

Dense graphs drown in labels. Source/entity labels are hidden unless hovered, neighboring a hovered node, or explicitly forced. Cluster names prefer concept titles (then question, entity, source) so long source titles do not become the label. In All Nodes the far view labels one concept-first node per cluster (plus hover/highlight). When zoomed (`ratio < 0.5`), up to 40 nodes inside the viewport are labeled by inbound weight so the visible nodes are identifiable. Overview labels every super-node. Labels are drawn with a themed background box because plain text is unreadable over dense edges. Collision resolution is best-effort (4-way greedy at assignment time); hover labels are allowed to overlap.

## 5. Per-view design

### 5.1 Health Dashboard

Plain DOM tables, no graph. Dead links are classified into four causes (`file-ref`, `missing-at-prefix`, `missing-source`, `missing-page`) because each cause has a different repair action; sorting by reference count orders the queue by impact. "Growth candidates" = `status: seed` with inbound links > 0, with a toggle to exclude lint-generated stubs. The orphan list is intentionally broader than the wiki lint report and says why (mega-hub exclusion).

### 5.2 Backbone Graph

Three modes: **clusters** (meta-graph of super-nodes), **nodes** (budgeted inspector of the full backbone), **cluster-detail** (induced subgraph of one community). Spatial summary is Overview's job: the current vault's backbone is ~4.6k nodes / ~36k edges / ~35 clusters, which is not a readable poster. All Nodes answers "where is this hub, and what is its neighborhood" under a hard display budget — one concept-first label per cluster and ≤200 skeleton edges when the camera ratio is ≥ 0.5; when zoomed, ≤800 skeleton edges plus up to 40 viewport labels so on-screen nodes stay named, with hover revealing incident edges. Type checkboxes (source / entity / concept / question) are a presentation filter only: the ForceAtlas2 coordinates and Louvain assignment stay on the full backbone, while hidden types drop out of labels, the skeleton budget, search, and hover. Visibility is one predicate (`isNodeTypeVisible`): no filter shows every node, a set (including empty) is membership-only so unknown types disappear when any checkbox is off. At least one type stays checked. The last checkbox set is loaded from plugin `data.json` so it survives closing the view and restarting Obsidian. It is not a second Overview; far view is not required to show 35 separated cluster masses.

Drawing every inter-cluster edge on the overview (hundreds, on a few dozen super-nodes) makes ForceAtlas2 collapse and the labels unreadable, so the overview shows a spanning-tree-plus-top-k skeleton and reveals the rest on hover. Drill-down is by clicking a super-node or its label, or the always-visible cluster panel (All Nodes does not change that contract). Hovering a row in the cluster panel isolates that cluster on the graph (super-node plus its inter-cluster edges in Overview; member nodes plus intra-cluster edges in All Nodes). Graph-node hover still wins if both would apply. The backbone (with its Louvain assignment) is cached across mode switches so drilling in and out never recomputes communities. After ForceAtlas2 is on screen, an index or option rebuild must not swap in the circular seed — that is the U-shaped collapse in All Nodes. The view keeps the laid-out copy until the cache finishes, then pins `customBBox` to the disk (Overview clears it because the meta-graph has its own coordinates). All Nodes LOD writes `forceLabel` / `weak` / `hidden` on that cached copy's GraphView-owned clone only; Overview rebuilds its own meta-graph edges, and cluster-detail ignores skeleton `hidden`.

### 5.3 Local Lens

*Why PPR instead of a BFS ego graph?* The first implementation used undirected BFS (depth 1–3) with a node cap. For hub concepts (200+ inbound links) the cap triggered mid-depth, making the cutoff arbitrary. Personalized PageRank evaluates "depth of relevance" as a continuous score, so "top N most relevant" is a principled selection with the same display budget. BFS depth *within the selected subgraph* is still used — as the ring number of the concentric layout, where ring order is chosen to reduce edge crossings (children sort by their best parent's angle).

Ghost nodes render the center page's unresolved links (excluding file references) in red directly in the neighborhood — the trail visibly breaks where a page is missing.

### 5.4 Growth Timeline

The load-bearing decision is the **frozen layout**: the backbone is laid out once for the full period. Time scrubbing never moves x/y or reruns ForceAtlas2. Far ticks only swap a filter context (and derived label/edge sets) then a reducer-only refresh (`partialGraph` + `skipIndexation`). In sigma v3, `refresh({skipIndexation: true})` without `partialGraph` is a full refresh and calls `process()`. Zoomed ticks may rewrite `labelAngle` when playback is paused and the labeled set changes. Re-laying-out per time step would make nodes jump and destroy the perception of growth. If the shared cache is invalidated mid-Replay, the view keeps the already-laid-out graph until ForceAtlas2 finishes; swapping in the circular seed is what collapsed the disk into a U.

The inspector is the same scale as All Nodes (~4.6k nodes / ~36k edges), so it reuses the *budget*, not the All Nodes contract wholesale. Three layers:

- **Background** — the final-wiki far/zoomed skeleton ∩ time-visible edges. This is the destination map; early Replay may show few background roads because those strong edges are not born yet. That is honest, not a bug.
- **Foreground** — new nodes and new edges of the current window. Replay does not cap new edges (the recent-highlight window is the bound). Diff caps new edges at the inter-cluster budget and writes `shown of total` when it truncates.
- **Landmarks** — one concept-first label per *time-visible* cluster, plus the top-12 new nodes. These live in memory so a time tick never writes `forceLabel`. Zoomed view adds up to 40 viewport names.

Type checkboxes and their `data.json` store are shared with All Nodes — a presentation filter, not a cache key. Node color is the same three-way select as All Nodes (cluster / type / status; default cluster so the list swatches match); time class still paints new / updated on top. Replay shows the cluster list (sizes are `visible / total` until the cluster has fully arrived) with the same hover-isolate contract; Diff keeps the breakdown panel. Graph-node hover still wins over cluster hover. The reducer, not graph `hidden` attributes, decides whether an accent edge draws (`timeEdgeShown`), because `applyEdgeSkeleton` marks off-skeleton edges hidden and time ticks do not rewrite those attributes.

Time ticks never move x/y or rerun ForceAtlas2. Far ticks only swap the filter context and derived sets, then refresh reducers without `process()`. Zoomed ticks rewrite `labelAngle` only when playback is paused and the labeled set actually changes. After the first ForceAtlas2 disk is on screen, the view pins `customBBox` to that extent so later `process()` calls cannot reframe.

*Why is edge time estimated?* Edge appearance is approximated as `max(bornDay of endpoints)`. This costs zero extra IO and is structurally monotone: the graph at time *t* is exactly the induced subgraph of nodes born by *t*, so edges never flicker in and out. The error direction is "appears earlier than reality" (a link added later between old pages shows as old). Git history and `wiki/log.md` were considered as edge-time sources and rejected: git blame per link is expensive and fragile, and log.md is not guaranteed complete. Instead the estimate is *labeled as an estimate* permanently in the UI.

`wiki/log.md` is used only as an **annotation layer** (event markers on the slider, operation lists in the diff panel, click-to-highlight). Its notation varies because humans and LLMs both append to it, so the parser is best-effort and failure-isolated: if every entry fails to parse, the timeline still works fully on frontmatter alone.

The count chart answers a different question from the spatial replay: *how many pages existed, by type?* It is a collapsible SVG strip on the same `model.days` axis (so the playhead index matches the slider). Series come from the full `GraphIndex`, not the backbone — seed entities are ~94% of entities and would be invisible if we counted only the laid-out graph. That mismatch is labeled in the note. **Exclude seed entities** drops `entity`+`seed` only; it does *not* apply `dropIsolated`, so the line will not equal the backbone `Nodes` count. Chart type checkboxes are *not* the All Nodes type filter: one hides nodes on the graph, the other chooses which count series to scale. Daily increments are the adjacent-day diff of the cumulative series (no second pass over the index); bucket 0 is an opening step that also holds unknown-`created` pages. No charting library: the strip is the same class of plain DOM as `TimeControls`. Chart clicks seek only in Replay, and only while the time controls are enabled (ForceAtlas2 still running disables both the slider and seek).

*Why a separate view instead of extending the Backbone Graph?* The replay's lifeline is the frozen layout; arbitrating that against the backbone view's re-layout, search, and three modes would couple two state machines for no user benefit.

### 5.5 3D Layers

The brief was "visualize the wiki in 3D". The essential fork: does the Z axis *mean* something, or is it a free third dimension for the force embedding? Free 3D force layouts look impressive but read poorly (occlusion, lost viewpoints, no stable mental map). The chosen design is a **multilayer network**: Z = page type, XY = the same frozen 2D ForceAtlas2 layout as the backbone. The view then has exactly one job 2D cannot do: showing **inter-layer coupling** (which sources feed which concepts) as physical verticality, so inter-layer edges get the accent color and intra-layer edges are faint.

*Why plain three.js instead of `3d-force-graph`?* That library bundles d3-force-3d and assumes it drives the simulation; with all coordinates frozen we would import a force engine only to disable it — an unusual usage of the library that fights its design. A direct three.js scene (~600 lines) is the simpler solution: one instanced sphere mesh, three line-segment geometries, sprite labels, orbit controls.

Rendering is **on demand** (no persistent rAF loop): a scene that changes only on interaction should cost zero GPU/CPU when idle. This is also why OrbitControls damping is disabled — damping requires a continuous loop.

Scope explicitly deferred: time × layer composition (mounting `TimeControls`), per-layer re-layout, and search-to-camera focus.

### 5.6 Gap Finder

The question is the inverse of every other view: not "what is connected" but **"what should be connected — per human knowledge — and isn't?"** Answering "should" requires knowledge beyond the vault, which splits the problem into three tiers:

1. evidence already latent *inside* the wiki (fully local),
2. the LLM's parametric knowledge,
3. external bibliographic APIs (Semantic Scholar / arXiv).

The plugin implements **tier 1 only**; tiers 2–3 live in the vault's `wiki-gap` skill, which consumes the report exported by the *Copy report* button, judges candidates against domain knowledge, and verifies recommended literature against the APIs. This keeps the no-network and no-LLM boundaries of the plugin intact while still delivering an end-to-end recommendation pipeline.

Four complementary structural signals (see implementation §3.4 for the math):

- **Cluster thinness** — inter-cluster weight far below a configuration-model expectation. Catches "two established topics that ignore each other".
- **Concept co-citation** — concept pairs repeatedly reached from the same sources yet never linked. Sources are human-authored evidence of relatedness, making this the strongest local proxy for "humanity's knowledge graph says these touch".
- **Adamic-Adar prediction** — pairs with many rare shared neighbors, deduped against co-citation and restricted to cross-cluster pairs by default. Same-layer complement to the bipartite signal.
- **Bridge literature** — `missing-source` dead links (`@`-prefixed papers cited but never ingested). This is simultaneously gap evidence *and* the tier-0 recommendation: the vault already names the literature it wants. Empirically these are cited from a single community today (their referrers are the paper's author stubs plus one source page), so the default threshold is 1 cluster — the tab reads as a demand-ranked ingest queue in which genuinely cross-cluster papers outrank automatically.

*Why a dashed canvas overlay instead of sigma edges?* Candidates are links that do **not** exist; rendering them as ordinary edges would forge the very structure the view is questioning. Sigma v3 has no dashed edge program, so candidates draw on a plain 2D canvas above the WebGL canvases (`graphToViewport` projection, redrawn on `afterRender`) — dashed, in an accent color used by nothing else. Selecting a candidate dims unrelated nodes and hides unrelated edges, deliberately mirroring the hover interaction users already know; hover always takes precedence.

*Why a separate view instead of a Backbone Graph mode?* The backbone view answers "what is here" with three modes of its own; a candidate list + selection overlay is a different interaction contract (list-driven, frozen layout), and per §5.4's precedent, coupled state machines in one view cost more than a sixth ribbon icon.

- **No writes, ever.** Not even "add a backlink" conveniences.
- **No network.** No remote fonts, tiles, or telemetry.
- **No coverage of the primary note layer.** The lens looks at `wiki/` only; the primary layer is out of scope by vault policy.
- **Desktop only.** WebGL + pointer interactions; mobile is not a target.
