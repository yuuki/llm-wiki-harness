# Wiki Lens

A **read-only** Obsidian plugin that visualizes the health, explorability, and growth of the LLM wiki layer (`wiki/{sources,entities,concepts,questions}/`) in this vault.

The plugin never writes to any note. It only reads Obsidian's `metadataCache` (plus one best-effort read of `wiki/log.md`), and it makes no network requests — everything runs locally.

On-screen Louvain (`src/core/cluster.ts`) is not the skill-world theme-chunk cache. Agents use undirected Blondel via `scripts/wiki-clusters.py` ([`docs/clusters-for-skills.md`](docs/clusters-for-skills.md), [`docs/theme-chunks-algorithm.md`](docs/theme-chunks-algorithm.md)).

## Views

| View | Icon | What it shows |
|---|---|---|
| Health Dashboard | `activity` | Type counts, status × type matrix, dead-link triage, growth candidates, orphan pages, duplicate basenames |
| Backbone Graph | `git-fork` | The link skeleton of the wiki as Louvain clusters (overview), all nodes, or one cluster's detail |
| Gap Finder | `unlink` | Missing-connection candidates: thin cluster pairs, co-cited but unlinked concepts, predicted links, and cited-but-missing literature |
| Local Lens | `scan-eye` | The neighborhood of the active note, ranked by Personalized PageRank, in concentric rings — with dead links shown as ghost nodes |
| Growth Timeline | `history` | Replay of the wiki's growth over time, or a two-point diff (new / updated pages, new links), plus a per-type node-count chart |
| 3D Layers | `layers` | A 3D multilayer network: one horizontal plane per page type, with inter-layer links emphasized |

Each view is available from a ribbon icon and from the command palette (`Wiki Lens: Open …`).

### Health Dashboard

The triage surface. Dead links are classified by cause (`Missing page`, `Missing @ prefix`, `Missing source`, `File reference`) and sorted by reference count, so the most impactful repairs float to the top. "Growth Candidates" lists `status: seed` pages with many inbound links — pages worth maturing next.

### Backbone Graph

Opens in **Overview** mode: Louvain communities collapsed into super-nodes, with only the strongest inter-cluster links drawn (hover a cluster to see the rest). Hover a cluster in the side panel to isolate it on the graph. Click a super-node or its label, or a cluster in the side panel, to drill into its members. **All Nodes** is a budgeted inspector of the full backbone (one concept-first label per cluster far; when zoomed, up to 40 names in the viewport; ≤200 skeleton edges far, ≤800 when zoomed; hover shows the neighborhood). A cache rebuild does not snap the ForceAtlas2 disk back onto the circular seed. Type checkboxes hide source / entity / concept / question nodes without re-laying-out; the skeleton and labels recompute on what remains, and at least one type stays checked. The last type selection is stored in the plugin's `data.json` and restored the next time Backbone Graph opens. "Show seed entities" rebuilds the shared backbone; the type checks persist across that rebuild because they are a display filter, not a cache key. Seed entity stubs (~94% of entities) are excluded by default. Nodes can be colored by cluster, type, or status. Clicking a node opens the note.

### Gap Finder

Surfaces connections that *should* exist but don't, from four structural signals: **Cluster gaps** (cluster pairs wired together far less than a degree-preserving null model predicts), **Concept pairs** (concepts co-cited by ≥3 common sources yet never linked), **Predicted links** (Adamic-Adar link prediction, cross-cluster by default), and **Bridge literature** (`@`-prefixed papers cited in the wiki but never ingested, ranked by demand). Candidates draw as dashed overlay lines — dashed because these links do not exist (yet); selecting a candidate isolates the involved clusters/nodes. **Copy report** exports the full analysis as Markdown for the vault's `wiki-gap` skill, which validates candidates against domain knowledge and recommends bridging literature verified via the Semantic Scholar / arXiv APIs (the plugin itself stays offline).

### Local Lens

Lives in the right sidebar and follows the active note (only wiki pages move the lens). Unlike Obsidian's core Local Graph, it selects the top-N most *relevant* nodes via Personalized PageRank rather than cutting off at an arbitrary BFS depth, dims seed stubs, and renders the center page's unresolved links as red ghost nodes — you can see exactly where a trail breaks off.

### Growth Timeline

**Replay** scrubs (or auto-plays) through time; pages appear on the day their `created` frontmatter says they were born, and recent appearances are highlighted. **Diff** compares two dates A → B and lists new pages, updated pages, and new links, cross-referenced with operation entries parsed from `wiki/log.md` (click an entry to highlight the pages it touched).

A **node-count chart** sits under the type bar: one line (or daily stacked bars) per page type, plus an optional total. The playhead follows Replay; click a day to seek (Replay only). Diff shades A→B. The chart counts every wiki page (Health's population), not the pruned backbone. **Exclude seed entities** drops seed stubs only — isolated pages stay, so the totals will not match the backbone node count. Its type legend is independent of the spatial type checkboxes. Pages with an unknown `created` date are counted as present before the first day (and sit in the first Daily bar together with that day's births).

The graph reuses All Nodes' *budget*, not its contract. Background roads are the final-wiki skeleton restricted to pages that already exist at *t*. Foreground is the current window's new pages and new links (Replay shows every new link in the recent-highlight window; Diff caps them at 200 and says how many it hid). Far view names one concept-first landmark per cluster that already has a living member, plus the top new pages; zoomed view adds up to 40 names on screen. Type checkboxes share All Nodes' stored selection. Color can be cluster (default, matching the list swatches), type, or status — same three modes as All Nodes; new / updated accents still win. Replay shows the cluster list (`visible / total`); hovering a row isolates that cluster. The ForceAtlas2 disk stays frozen while time moves; a cache rebuild mid-Replay does not snap nodes back onto the circular seed.

Accuracy note (also shown in the UI): link appearance time is estimated from the creation dates of both endpoint pages, and "updated" is a proxy from the `updated` frontmatter field.

### 3D Layers

The wiki's four page types stacked as horizontal planes (sources at the bottom, then entities, concepts, questions). XY positions reuse the same frozen 2D ForceAtlas2 layout as the backbone view; the Z axis carries meaning (type) instead of a free 3D force embedding. Inter-layer edges — e.g. source → concept — are drawn in an accent color and are the visual protagonist; intra-layer edges are faint. Orbit/zoom with the mouse, toggle layers, hover to highlight a node's neighborhood, click to open the note.

## Requirements

- Obsidian ≥ 1.5.0, desktop only (`isDesktopOnly: true`).
- WebGL (used by both sigma.js and three.js). If WebGL is unavailable, the graph views degrade to an in-view message.

## Development

```bash
cd .obsidian/plugins/wiki-lens
npm install
npm run dev     # watch build
npm run build   # type-check (tsc -noEmit) + minified production build
```

The build bundles everything into `main.js` (esbuild, CommonJS, ES2020). `package-lock.json` and `node_modules/` are gitignored.

Typical verification loop with the `obsidian` CLI (requires a running Obsidian):

```bash
obsidian plugin:reload id=wiki-lens
obsidian dev:errors
obsidian dev:screenshot path=shot.png
```

## Documentation

- [`docs/design.md`](docs/design.md) — goals, design principles, and per-view rationale (the *why* and *why not*)
- [`docs/implementation.md`](docs/implementation.md) — module map, data flow, algorithms, and performance notes (the *how*)

## Dependencies

Runtime: `graphology` (+ `layout`, `layout-forceatlas2`, `communities-louvain`), `sigma` v3 (2D views), `three` + `three-spritetext` (3D layer view). No force engine runs in the 3D view; three.js is used purely as a renderer.
