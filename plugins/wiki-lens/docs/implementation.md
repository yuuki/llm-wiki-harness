# Wiki Lens — Implementation Notes

Companion to [`design.md`](design.md). This documents the module layout, data flow, key algorithms, and performance-relevant details as implemented.

## 1. Module map

```
src/
  main.ts                 Plugin entry: view registration, ribbons, commands, index lifecycle
  core/                   UI-free logic
    types.ts              Shared types (NodeMeta, WikiEdge, DeadTarget, timeline types, …)
    classify.ts           Frontmatter/path normalization + dead-target cause rules
    graph-index.ts        GraphIndex: the single source of truth (nodes/edges/dead targets)
    filters.ts            buildBackbone / buildEgoGraph / buildLensGraph
    ppr.ts                Personalized PageRank (power iteration)
    metrics.ts            nodeSize / nodeWeight (log-scaled inbound instances)
    cluster.ts            Cluster grouping + meta-graph (super-nodes)
    force-labels.ts       pickForceLabels + rankViewportLabels (zoomed names)
    edge-skeleton.ts      selectVisibleEdges: inspector far/zoomed skeleton
    label-collision.ts    greedy 4-way labelAngle fallback (Overview / inspectors)
    type-visibility.ts    isNodeTypeVisible / includeTypesFromVisible / parseVisibleTypes
    palette.ts            Type / status / community colors (shared; not a view)
    rng.ts                Seeded mulberry32 for Louvain
    backbone-cache.ts     Shared backbone + Louvain + chunked ForceAtlas2
    gap.ts                Gap signals: cluster thinness, co-citation, Adamic-Adar, bridges
    timeline.ts           TimelineModel, classifiers, selectNewEdges, timeEdgeShown
    log-parser.ts         Best-effort parser for wiki/log.md (annotation layer)
  views/
    sigma-base.ts         SigmaBaseView: shared sigma.js lifecycle for the 2D graph views
    health-view.ts        Health Dashboard (plain DOM, no graph)
    graph-view.ts         Backbone Graph (clusters / nodes / cluster-detail)
    gap-view.ts           Gap Finder (4 candidate tabs + dashed overlay canvas)
    local-lens-view.ts    Local Lens (PPR top-N, concentric rings)
    time-view.ts          Growth Timeline (frozen layout + reducer-based time filter)
    time-controls.ts      TimeControls: time-bar DOM component (no sigma/graphology deps)
    layer-view.ts         3D Layers (pure three.js renderer, no force engine)
styles.css                All view styling (Obsidian CSS variables)
```

Color constants live in `core/palette.ts` (`TYPE_COLORS`, `STATUS_COLORS`, `COMMUNITY_PALETTE`) and are re-exported from `graph-view.ts`.

## 2. Index layer

### 2.1 `GraphIndex` (core/graph-index.ts)

- **Node targets**: markdown files directly under `wiki/{sources,entities,concepts,questions}/`, excluding `_index`. Catalog pages (`index.md`, `log.md`, `hot.md`, `meta/`, `folds/`) are not nodes (mega-hub exclusion).
- **Build**: one pass over `vault.getMarkdownFiles()` for nodes (frontmatter via `metadataCache.getFileCache`), one pass over `resolvedLinks` for edges, one pass over `unresolvedLinks` for dead targets. Never reads file bodies.
- Edges are unique `(src, dst)` pairs with a `count` of link instances; self-links are dropped. Links from a node to a non-node page increment the source's `outsideRefs` instead of creating an edge.
- Dead-link **referrers** are scanned across all of `wiki/` (not just node targets), because an audit should see everything.
- `basenameToPaths` supports duplicate-basename detection and the `missing-at-prefix` heuristic (an unresolved `X` whose `@X` exists).
- Rebuilds are debounced 500 ms (`scheduleRebuild`), triggered by `metadataCache "resolved"`, `vault "rename"`, and `vault "delete"` (wired in `main.ts` after `onLayoutReady`, because the cache may be unresolved at `onload`). Each build fires `"updated"`: `main.ts` invalidates `BackboneCache` and the PPR adjacency cache first, then views re-acquire. Graph / Gap / Time / 3D share one layout per index generation + option key; they do not each rerun ForceAtlas2.
- `stats` records build time, counts, and `builtAt`; `built === false` makes views show a "Building index…" placeholder.

### 2.2 Classification rules (core/classify.ts)

- `type` prefers frontmatter but falls back to the folder; `status` outside the known set becomes `"unknown"`.
- `subType` reads a per-type frontmatter key: `source_type` / `entity_type` / `complexity` / `answer_quality`.
- Dead-target causes, in priority order: `.raw/` or `_attachments/` paths and non-`.md` file extensions → `file-ref`; `@X` exists for target `X` → `missing-at-prefix`; target starts with `@` → `missing-source`; otherwise `missing-page`.

## 3. Derived graph layer

### 3.1 `buildBackbone` (core/filters.ts)

graphology directed simple graph from the index; excludes seed entities by default (`includeSeedEntities: false`) and drops isolated nodes after filtering (`dropIsolated: true`). Node attributes: `label`, `nodeType`, `status`, `inDeg` (unique neighbors), `inCount` (link instances), `isLintStub`.

### 3.2 `buildLensGraph` (core/filters.ts) + PPR (core/ppr.ts)

- PPR: power iteration, α = 0.15, 30 iterations, over the index treated as an **undirected** weighted graph (each directed edge laid in both directions, weight = link count). Dangling mass returns to the center. Flat arrays (`adjNodes`/`adjWeights`, `Float64Array` rank vectors). Adjacency is cached per `GraphIndex` identity + `builtAt` and cleared on `"updated"`; a note switch reruns the 30 iterations, not the ~9k-node assembly. The walk itself is still synchronous and unsplit.
- The lens takes the top `maxNodes − 1` scored nodes plus the center, builds the induced subgraph, then computes undirected BFS depth *within that subgraph* (disconnected nodes rounded to depth 3) as the ring number.
- Ghost nodes: the center's dead targets (cause ≠ `file-ref`) are added as `ghost:<target>` nodes with an `isGhostEdge` edge from the center.
- `buildEgoGraph` (BFS ego with a node cap) is retained but no longer used by the Local Lens — see design §5.3.

### 3.3 Communities and meta-graph (core/metrics.ts, core/cluster.ts)

- `assignCommunities`: Louvain with `getEdgeWeight: "count"` and a fixed `rng` writes a `community` attribute. Graph / Gap / Time / 3D share one `BackboneCache` so ForceAtlas2 runs once per index generation and option set.
- `computeClusters` groups by community, labels each cluster with up to two concept-first titles (concept > question > entity > source), and keeps the top-5 members by inbound instances for the panel.
- `buildMetaGraph` collapses each cluster into a super-node (`cluster:<id>`, size `5 + 2.2·log₂(1+size)`). Inter-cluster `count` is summed directionally (self-loops dropped). The overview draws a skeleton — a maximum spanning tree plus each cluster's 2 strongest outgoing edges — so a dense meta-graph (hundreds of edges on tens of clusters) does not collapse into a hairball. Remaining edges stay on the graph as `weak`/`hidden` and appear only when a super-node is hovered. Always-on labels are every cluster, shortened to the concept-first title; hover shows the full top-2 label. Labels sit outside the node on the ray from the layout centroid (viewport Y is flipped; the background box is derived from the same anchor as the text, with a 4-way fallback if two boxes collide). Layout is a circumference-fit circle, then ForceAtlas2 with `adjustSizes` and no strong gravity, then a pairwise `separateNodes` pass that gives labeled super-nodes extra clearance.
- `nodeWeight(attrs)` prefers `inCount` (link instances) over unique-neighbor `inDeg`. `nodeSize(w) = 2 + 2.2·log₂(1 + w)` is the shared size function.

### 3.4 Gap signals (core/gap.ts)

`analyzeGaps(index, backbone, clusters, opts)` computes four candidate lists in one call. The backbone (with `community` attributes) is treated as **undirected** throughout; one edge pass builds total weight `m`, weighted degrees, an unweighted adjacency (distinct-neighbor sets — graphology's `degree()` would double-count mutual A→B/B→A pairs), and per-cluster-pair inter-weights. Unordered pair keys are `a + "\0" + b` (paths contain spaces, so a space separator would collide). A `fullPairs` set built from `index.edges` (either direction) is the "already linked" mask — stricter than the backbone, so pairs connected only via pruned pages are still excluded.

- **clusterGaps** — configuration-model null: `expected(A,B) = S_A·S_B / 2m` (S = summed weighted degree). Pairs with `expected ≥ 2` and cluster size ≥ `minClusterSize`, sorted by ascending `actual/expected` (ties: larger expected first). O(E + C²).
- **conceptPairs** — for each source node, accumulate its unordered concept-neighbor pairs; sources with fanout > `maxSourceFanout` are skipped *and noted*. Keep pairs with ≥ `minCommonSources` common sources and no direct link; cross-cluster pairs sort first. O(Σ fanout²), bounded by the hub skip.
- **predictedLinks** — Adamic-Adar enumerated by intermediate node (`1/log₂(deg w)` per common neighbor w; intermediates with degree > 150 skipped and noted). Deduped against the reported conceptPairs; `predictCrossClusterOnly` (default true) keeps only cross-community pairs. O(Σ deg²) with the cap.
- **bridges** — `missing-source` dead targets mapped to referrer communities. Referrers are frequently seed author stubs excluded from the backbone, so an unclustered referrer is attributed the **majority community of its full-index neighbors** (adjacency built lazily). `score = clusters·log₂(1 + refs)`. Empirically no missing source spans ≥ 2 communities in this vault yet, so `minBridgeClusters` defaults to 1 (a demand-ranked ingest queue; breadth outranks when it appears) and unattributable targets are counted in a note.

Every truncation or skip appends to `notes[]` (never silent). `buildGapReport` renders the analysis as LLM-friendly Markdown for the clipboard hand-off to the `wiki-gap` skill.

## 4. 2D view infrastructure — `SigmaBaseView`

Shared by GraphView, GapView, LocalLensView, TimeView:

- **Lifecycle**: `setSigmaGraph(graph, container, layoutInit)` creates sigma lazily on first call and swaps graphs afterwards (with an explicit `refresh()`, since sigma v3's `setGraph` does not reprocess in all paths). `layoutInit: null` skips circular initialization for pre-laid-out graphs (shared backbone copies, Local Lens). After a ForceAtlas2 disk is shown, GraphView / TimeView call `pinFrozenBBox()`; Overview calls `clearFrozenBBox()` before swapping to the meta-graph. `onClose` always kills sigma to avoid leaking the WebGL context. A `ResizeObserver` on the container handles the "leaf created at 1px height" startup case; `onResize` calls `sigma.resize()` because `refresh()` alone does not re-measure.
- **Layout**: the default backbone path uses `BackboneCache` (chunked ForceAtlas2, 5 iterations per rAF, `min(600, 150 + order/10)`). `SigmaBaseView.runLayout()` accepts optional settings/iterations (used by the GraphView meta-graph) and remains the user-facing **Re-layout** button (local; does not write back to the shared cache). TimeView no longer waits on `onLayoutFinished` for the shared layout — controls enable after acquire / `onReady`.
- **Hover model**: `enterNode`/`leaveNode` set `hoveredNode` + neighbor set; the node reducer dims non-neighbors and clears their labels, the edge reducer hides non-incident edges and accents incident ones. Refreshes use `refreshDisplay()` (`partialGraph` + `skipIndexation`) so sigma v3 does not `process()`.
- **Label suppression**: source/entity labels are cleared in the reducer unless hovered / neighbor / `forceLabel` / highlighted.
- **Custom label drawing**: `defaultDrawNodeLabel` and `defaultDrawNodeHover` are replaced by one routine that draws a rounded, theme-colored background box (labels truncate at 28 chars; hover shows full text). Because it runs every frame, it reads only cached `themeColors`, resolved from CSS variables at creation and on `css-change` (arbitrary CSS colors normalized to rgb via a reusable canvas 2D context).
- **Click**: opens the note via `workspace.openLinkText` (ghost nodes ignored). `clickStage` is a hook for subclasses (GraphView uses it to hit-test overview labels). Double-click remains a hook for subclasses.

## 5. Views

### 5.1 HealthView

Pure DOM re-render on every index update: summary chips, status × type matrix (seed/mature cells tinted), dead-link table with cause filter buttons and incremental "Show more" paging, growth candidates (seed + `inCount` > 0, lint-stub toggle; column is link instances), orphan / duplicate-basename lists with the same Show more. All page references are click-to-open links.

### 5.2 GraphView

State: `mode` (`clusters` | `nodes` | `cluster-detail`), `colorMode` (`cluster` | `type` | `status`), backbone options. `rebuild()` acquires a copy from `BackboneCache` (shared Louvain + ForceAtlas2). If ForceAtlas2 coordinates are already on screen and the cache copy is still the circular seed, rebuild returns without swapping (`waitingForLayout`); `onReady` then rebuilds from the laid-out copy. First open still shows the seed until `onReady` copies FA2 positions. All Nodes / cluster-detail pin `customBBox` to that disk; Overview clears it before building the meta-graph. `render()` only swaps which graph sigma shows (meta-graph / backbone / induced subgraph) — mode switches never recompute communities. Hover and type-filter refreshes use `refreshDisplay()` (`partialGraph` + `skipIndexation`) so sigma v3 does not `process()`. The overview meta-graph still runs a local ForceAtlas2, but with overview-specific settings (`META_FA2_SETTINGS`: `adjustSizes`, no strong gravity, layout edge weight = `log₂(1+count)`) and a post-pass that separates overlapping super-nodes. The stats line reports skeleton edges vs. the full inter-cluster total. Cluster ids can change if topology changes; the focused cluster rebinds by label. **Re-layout** is local to the visible graph and can diverge from the shared coordinates used by Gap / Time / 3D. Search: in clusters mode it finds the matching member, enters its cluster, then focuses; otherwise it focuses in the current graph (camera animate to ratio 0.15 + simulated hover). Clicking a super-node or its offset label enters cluster-detail; a short click-suppress window avoids the trailing half of a double-click opening a member note. Pointer enter/leave on a cluster-list row sets `hoveredCluster` and calls `refreshDisplay()`; reducers dim non-members and reveal that cluster's edges while `hoveredNode` is null. The color select and seed toggle are disabled in clusters mode where they are meaningless.

Mode switches (`setMode`) and a lost cluster-detail rebind call `clearHover` so a leftover `hoveredNode` from All Nodes search cannot make `SigmaBaseView.edgeReducer` hide every Overview / detail edge (those graphs do not contain the old backbone path). cluster-detail copies nodes with `forceLabel: false` and edges with `hidden/weak` cleared.

All Nodes is a budgeted inspector on the GraphView-owned backbone copy. `pickForceLabels` marks one concept-first representative per cluster (`forceLabel`). When zoomed, `rankViewportLabels` names up to 40 nodes inside the current viewport (debounced with `camera.updated`); far view keeps the set empty. `selectVisibleEdges(graph, mode, opts?)` (caps are module constants, not arguments) keeps one max-`count` edge per undirected cluster pair, cuts inter at 200, and in `zoomed` fills intra up to 800 without changing the inter set. Optional `includeTypes` is produced by `includeTypesFromVisible` and consumed by `isNodeTypeVisible` / `edgeTouchesHiddenType` in labels, skeleton, reducers, search, stats, `acceptHover`, and click. The type toggles live only in All Nodes, never rebuild the shared backbone, and refuse to uncheck the last remaining type. `GraphView` reads/writes them through `TypeFilterStore` (`parseVisibleTypes` / `serializeVisibleTypes`); `main.ts` persists `{ visibleTypes }` with `loadData`/`saveData`. Corrupt or empty payloads fall back to all four types. The reducer hides filtered-out nodes and any edge that touches them, then hides non-skeleton edges unless they are incident to the hovered node; `SigmaBaseView.acceptHover` is the enterNode gate so a hidden node cannot become `hoveredNode` (a rejected enter leaves the previous hover in place). Filtered stats report visible clusters, not the full Louvain count. An in-memory `visibleEdges` set is the display source of truth so camera-threshold updates do not depend on sigma reindex. Camera: entering All Nodes resets to `{x:0.5,y:0.5,ratio:1}`; `ratio < 0.5` is zoomed. `camera.updated` is debounced 150ms and ignored unless `mode === "nodes"`. Resize never changes the edge budget; it re-runs viewport labels (if zoomed) and label collision. cluster-detail copies edges with `hidden/weak` cleared and does not apply the skeleton reducer. Overview's `weak`/`hidden` still come only from `buildMetaGraph`. `minEdgeThickness` is 0.4 in All Nodes only.

### 5.3 LocalLensView

Follows `workspace "file-open"` but only re-centers for paths present in the index. Rebuild: `buildLensGraph` → color/size attributes (seed stubs dimmed to 30% alpha, ghosts reddish, center forced white/enlarged with `forceLabel`) → `layoutConcentric` → `setSigmaGraph(…, null)` with **no** ForceAtlas2. Concentric layout: ring radius `depth × 140`; ring 1 ordered by descending PPR; outer rings sort each node by the angle of its highest-PPR parent in the inner ring (reduces crossings), parentless nodes appended last.

### 5.4 TimeView + TimeControls

- `timeline.ts` builds the model from frontmatter only: `days` = sorted unique valid `created` ∪ `updated` days (epoch-day integers, UTC); unknown `created` rounds to `minDay − 1` (always visible, counted for the UI); unknown `updated` aligns to `bornDay` so diffs don't misclassify. `annotateGraphTime` bakes `bornDay`/`updatedDay` into node attributes and `bornDay = max(endpoints)` into edge attributes, so the per-frame classifiers (`classifyNode`/`classifyEdge`) are pure numeric comparisons with zero allocation. `cumulativeCounts` gives O(1) per-timepoint node/edge counts on the backbone graph via sort + two pointers (status line). `countSeries` does the same two-pointer scan per page type over the full `GraphIndex` (optional seed-entity drop; isolated non-seeds stay) and derives daily increments as adjacent diffs of the cumulative arrays. `unknownCreatedCount` is recounted on the filtered population so the chart footnote cannot name pages the series dropped.
- `TimeView.rebuild()` (index updates only): acquire the shared backbone → bake base/dim colors and sizes → build + bake the time model → apply the inspector LOD (`pickForceLabels` / `selectVisibleEdges`) → `setSigmaGraph(..., null)` → `setDays(..., preserve)` remaps t/A/B onto the new day list. If ForceAtlas2 coordinates are already on screen and the cache copy is still the circular seed, rebuild returns without swapping the graph (`waitingForLayout`); `onReady` then rebuilds from the laid-out copy. First open still shows the seed until `onReady` copies FA2 positions. After a laid-out graph is shown, `setCustomBBox` pins the full-disk extent. Time ticks never move x/y or ForceAtlas2. They swap `this.ctx` and recompute `newLabelSet` / `timeLabelSet` / `selectNewEdges` / visible counts, then `refreshDisplay()` (`partialGraph` + `skipIndexation`). Zoomed + paused: rewrite `labelAngle` only when the labeled fingerprint changes. Type-filter and camera-threshold changes re-run LOD on the TimeView-owned copy only.
- Display layers: background = final skeleton ∩ time-visible; foreground = new edges (Replay uncapped, Diff `INTER_CAP` with `shown of total`); landmarks = `pickForceLabels(..., includeNode: not time-hidden)` held in `timeLabelSet` (no per-tick attribute writes). The reducer sets `forceLabel` on `timeLabelSet` ∪ `newLabelSet` for every time class so `labelRenderedSizeThreshold` (7) cannot drop a small existing landmark. Type checkboxes read/write the shared `TypeFilterStore`. A color select (cluster / type / status, default cluster) rebakes `colorBase`/`colorDim` without re-layout; new / updated accents still override. Replay cluster sizes are `visible / total`. `minEdgeThickness` is 0.4.
- Reducer layering (hot path): type filter → time classification → event highlight or cluster-list hover → the base hover reducer → label keep (`timeLabelSet` / `newLabelSet` / `zoomLabelSet` / hover). Accent visibility is `timeEdgeShown` (explicit `hidden = !shown`); the function ignores graph `hidden` left by `applyEdgeSkeleton`. Existing edges use the skeleton unless hover/cluster isolation reveals them. A hovered node that becomes time-hidden on the next tick is cleared.
- `wiki/log.md` parsing is async and off the critical path, guarded by a generation counter (`rebuildGen`) so stale results from overlapping rebuilds are discarded. The parser matches `## [YYYY-MM-DD] <op> | <title>` headings, extracts `[[…]]` from `- Pages created/updated:` lines, and resolves them path-first (with `wiki/` prefix retry) then by basename; failures are isolated per entry and never throw.
- `TimeControls` is a plain DOM component (no sigma/graphology imports) reporting state via one `onChange` callback: Replay tab (play/pause with speed presets, range slider with event-marker dots, recent-highlight window) and Diff tab (A/B selects with `aIndex < bIndex` enforced by a single correction path, swap, presets Yesterday→Today / 1 week / Full range). Playback speed is intentionally not part of `TimeControlsState`. `seekTo(index)` stops playback and jumps the Replay index (used by the count chart).
- `CountChart` (`views/count-chart.ts`) is the same DOM layer: a collapsible SVG strip under the type bar. `TimeView.rebuild` assigns `this.model` and calls `setDays` in the same successful path — a `waitingForLayout` / `!handle` return keeps the previous model so the slider and chart stay on one `days` array. The seed toggle recalls `countSeries`; Replay ticks patch the playhead line instead of wiping the SVG. Legend series and Cumulative/Daily are local to the chart (at least one series stays checked). Click maps x → nearest day index → `seekTo` in Replay only, and `seekTo` no-ops while controls are disabled or Diff is showing. Daily bars use the same `xAt` as the playhead. No `saveData` — collapse and series visibility die with the view.

### 5.5 LayerView (3D)

Not a `SigmaBaseView`; plain `ItemView` + three.js.

- **Geometry pipeline**: acquire the shared backbone (same ForceAtlas2 coordinates as Graph / Gap / Time). If layout is still running, wait for `onReady` then `buildScene`. FA2 `(x, y)` maps to world `(x, layer·layerGap, z = y)`; the XZ footprint is centered at the origin. All world dimensions derive from `span` (the footprint's larger side): `layerGap = span·0.35`, node scale `span/900`, label heights `span·0.018–0.022`, camera fit `span·1.35`, near/far `span/1000`–`span·20` — sizes stay proportionate however far FA2 spreads. **Rebuild scene** re-reads the shared layout; it does not rerun ForceAtlas2. An existing camera is kept across rebuilds; **Reset view** is the explicit home.
- **Nodes**: one `InstancedMesh` of a low-poly sphere (`SphereGeometry(1, 12, 8)`, `MeshLambertMaterial`), per-instance matrix (position + radius `nodeSize(inCount) · nodeScale`) and color. **Hidden layers collapse instances to zero scale** — invisible and unpickable without rebuilding the mesh.
- **Edges**: three `LineSegments`, each a single `BufferGeometry`: intra-layer (gray `0x808080`, opacity 0.07), inter-layer (accent `0xc8a03c`, 0.28), and hover-highlight (rebuilt per hover, `0xe8b84b`, 0.9). Layer toggles rebuild the intra/inter geometries; the "Inter-layer edges only" toggle just flips `intraLines.visible`.
- **Planes & labels**: one translucent `PlaneGeometry` per non-empty layer (opacity 0.05, `depthWrite: false`, `renderOrder = −1`) with a `SpriteText` caption placed at the corner *far* from the home camera (which sits at +x/+z) so it reads as a caption instead of looming. Per layer, the top-5 inbound-instance nodes get permanent labels; one reusable hover sprite (themed background) shows the hovered node's title. Text color comes from `--text-normal`; the renderer uses `alpha: true` + transparent clear color so Obsidian's background shows through.
- **Rendering is on demand**: `requestRender()` coalesces to one rAF; OrbitControls (`enableDamping = false`, since damping needs a continuous loop) triggers it via its `change` event. Idle cost is zero.
- **Picking**: `Raycaster.intersectObject(instancedMesh)` → `instanceId`. `pointermove` stores the latest event; one rAF pick uses that event (not the first of a burst). Click vs. orbit-drag is disambiguated by pointer travel between `pointerdown` and `pointerup` (`CLICK_SLOP_PX = 5`). Hover dims non-neighbors (via instance colors), rebuilds highlight lines, and moves the hover sprite; click opens the note.
- **Resources**: `disposeScene()` traverses and disposes every geometry/material/sprite texture; the renderer survives rebuilds and is disposed in `onClose` along with the controls. WebGL initialization failure degrades to an in-view message.
- **Known limitation**: the stats line (`Nodes n / edges m (inter-layer k)`) reflects layer visibility but not the intra-line visibility toggle.

### 5.6 GapView

State: `activeTab` (one of the four signals) and `selection` (`{tab, index}`, toggled by row clicks; reset on tab switch). `rebuild()` acquires the shared backbone, recaptures the selection by label/path/target, then `analyzeGaps`. Tab and selection changes only call `refresh({skipIndexation: true})` plus an overlay redraw — the layout is never re-run. Bridge rows show `N on graph / M stubs`. `buildGapReport` includes node paths, common source paths, predicted-link paths, and bridge referrers.

- **Selection emphasis (reducers)**: applied only while `hoveredNode` is null, so the base hover model always wins. The node reducer keeps color on matching nodes (both clusters of a cluster gap / the two pair endpoints / a bridge's referrers — pair endpoints also get `highlighted` + `forceLabel`) and dims everything else; the edge reducer hides edges unrelated to the selection (for cluster gaps: edges not internal to the two clusters), mirroring how hover hides non-incident edges.
- **Dashed overlay**: one 2D `<canvas>` absolutely positioned over the sigma container (`pointer-events: none`), DPR-aware, size-synced by its own `ResizeObserver`. Redrawn on sigma's `"afterRender"` (pan/zoom) and after every selection/tab change; positions project through `sigma.graphToViewport`. Cluster-gap lines connect cluster centroids (recomputed in `rebuild` / `onCacheReady`); pair tabs draw all candidates faint + the selected one bold; the bridge tab draws only the selected candidate as a dashed "ghost paper" circle at the centroid of its in-graph referrers, with dashed lines to each and a themed label.
- **Copy report** writes `buildGapReport` output to `navigator.clipboard` and confirms with a `Notice`. The toolbar permanently shows the estimate disclaimer required by design principle 6.

## 6. Build & workflow

- `npm run dev` — esbuild watch (inline sourcemaps). `npm run build` — `tsc -noEmit -skipLibCheck` then minified esbuild production bundle.
- esbuild: `src/main.ts` → `main.js`, CJS, ES2020, obsidian/electron/codemirror externals. The three.js dependency puts the production bundle at roughly 830 KB (~500 KB of it is three).
- tsconfig: `moduleResolution: "bundler"`, `strictNullChecks` + `noImplicitAny`, `isolatedModules`.
- Verification loop against a running Obsidian: `obsidian plugin:reload id=wiki-lens` → `obsidian dev:errors` → `obsidian dev:screenshot` / `obsidian eval` for DOM assertions. `npm test` runs vitest on `src/core/*.test.ts` (`pickForceLabels`, `selectVisibleEdges`, `type-visibility`, `selectNewEdges`, `timeEdgeShown`, `countSeries`). Views stay screenshot-checked.
