import Graph from "graphology";
import { TFile, WorkspaceLeaf } from "obsidian";
import {
	buildLensGraph,
	DEFAULT_LENS,
	LensOptions,
} from "../core/filters";
import { GraphIndex } from "../core/graph-index";
import { nodeSize, nodeWeight } from "../core/metrics";
import { TYPE_COLORS } from "../core/palette";
import { SigmaBaseView } from "./sigma-base";

export const LOCAL_LENS_VIEW_TYPE = "wiki-lens-local";

const GHOST_EDGE_COLOR = "rgba(192, 85, 85, 0.45)";
/** Ring spacing for the concentric ring layout (px) */
const RING_RADIUS = 140;

function dim(hex: string, alpha: number): string {
	const r = parseInt(hex.slice(1, 3), 16);
	const g = parseInt(hex.slice(3, 5), 16);
	const b = parseInt(hex.slice(5, 7), 16);
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Local Lens: from the active note, selects the top N nodes by relevance
 * using Personalized PageRank (PPR) and displays them in a concentric ring
 * layout. What distinguishes this from Obsidian core's Local Graph is that
 * it encodes the wiki's type/status, dims stub seed nodes, and renders dead
 * links from the center page as ghost nodes, making it immediately visible
 * where the trail breaks off.
 *
 * We used to build the ego graph with undirected BFS (depth 1-3), but for
 * hub concepts (200+ inbound links) that hit maxNodes at depth 2, making the
 * cutoff feel arbitrary. PPR lets us evaluate "depth of relevance from the
 * center" as a continuous value, so instead of an arbitrary cutoff we can
 * treat this as selecting the top N.
 */
export class LocalLensView extends SigmaBaseView {
	private index: GraphIndex;
	private opts: LensOptions = { ...DEFAULT_LENS };
	private center: string | null = null;
	private statsEl: HTMLElement | null = null;
	private containerDiv: HTMLElement | null = null;

	constructor(leaf: WorkspaceLeaf, index: GraphIndex) {
		super(leaf);
		this.index = index;
	}

	getViewType(): string {
		return LOCAL_LENS_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Wiki Lens: Local Lens";
	}

	getIcon(): string {
		return "scan-eye";
	}

	async onOpen(): Promise<void> {
		const el = this.contentEl;
		el.empty();
		el.addClass("wiki-lens-graph");

		this.buildToolbar(el);
		this.containerDiv = el.createDiv({ cls: "wiki-lens-graph-container" });

		this.registerEvent(this.index.on("updated", () => this.rebuild()));
		this.registerEvent(
			this.app.workspace.on("file-open", (file) => {
				if (file) this.setCenter(file);
			})
		);
		const active = this.app.workspace.getActiveFile();
		if (active) this.setCenter(active);
		else this.rebuild();
	}

	/** Only switch the center when a wiki page is opened (ignore notes from other layers) */
	private setCenter(file: TFile): void {
		if (!this.index.nodes.has(file.path)) return;
		if (this.center === file.path) return;
		this.center = file.path;
		this.rebuild();
	}

	private buildToolbar(el: HTMLElement): void {
		const bar = el.createDiv({ cls: "wiki-lens-toolbar" });

		bar.createSpan({ text: "Display count" });
		const select = bar.createEl("select");
		for (const n of [50, 100, 150, 300]) {
			select.createEl("option", { value: String(n), text: String(n) });
		}
		select.value = String(this.opts.maxNodes);
		select.onchange = () => {
			this.opts.maxNodes = Number(select.value);
			this.rebuild();
		};

		const wrap = bar.createEl("label", { cls: "wiki-lens-toggle" });
		const cb = wrap.createEl("input", { type: "checkbox" });
		cb.checked = this.opts.showGhosts;
		cb.onchange = () => {
			this.opts.showGhosts = cb.checked;
			this.rebuild();
		};
		wrap.createSpan({ text: " Show dead links" });

		this.statsEl = bar.createSpan({ cls: "wiki-lens-muted" });
	}

	private rebuild(): void {
		if (!this.containerDiv) return;
		if (!this.index.built) {
			this.statsEl?.setText("Building index…");
			return;
		}
		if (!this.center || !this.index.nodes.has(this.center)) {
			this.center = null;
			this.statsEl?.setText("Open a wiki page to show its neighborhood");
			return;
		}
		this.stopLayout();

		const centerMeta = this.index.nodes.get(this.center);
		const { graph, candidateCount, ghostCount } = buildLensGraph(
			this.index,
			this.center,
			this.opts
		);

		graph.updateEachNodeAttributes((node, attrs) => {
			const type = attrs["nodeType"] as string;
			const base = TYPE_COLORS[type] ?? "#888888";
			const isSeedStub =
				type === "entity" && attrs["status"] === "seed";
			attrs["color"] = attrs["isGhost"]
				? dim(TYPE_COLORS["ghost"], 0.8)
				: isSeedStub
					? dim(base, 0.3)
					: base;
			attrs["size"] = nodeSize(nodeWeight(attrs));
			if (node === this.center) {
				attrs["size"] = Math.max((attrs["size"] as number) * 1.6, 12);
				attrs["color"] = "#ffffff";
				// The center may be a source / entity too, but always force its label to show
				attrs["forceLabel"] = true;
			}
			return attrs;
		});
		graph.updateEachEdgeAttributes((_, attrs) => {
			if (attrs["isGhostEdge"]) attrs["color"] = GHOST_EDGE_COLOR;
			return attrs;
		});

		this.layoutConcentric(graph, this.center);
		// The layout is already finalized as concentric rings, so pass null for layoutInit to
		// skip the circular initialization, and don't call runLayout() (ForceAtlas2) either
		this.setSigmaGraph(graph, this.containerDiv, null);

		const parts = [
			`Center: ${centerMeta?.title ?? this.center}`,
			`Shown ${graph.order}`,
			`Candidates ${candidateCount}`,
			`Unresolved ${ghostCount}`,
		];
		this.statsEl?.setText(parts.join(" / "));
	}

	/**
	 * Concentric ring layout based on the depth attribute. The center is placed
	 * at (0,0), and nodes at depth d are arranged on a circle of radius
	 * d * RING_RADIUS. Depth 1 nodes are placed at equal intervals in descending
	 * order of ppr. From depth 2 onward, nodes are first sorted by the angle of
	 * their "neighbor in the inner ring with the highest ppr (the parent)" and
	 * then placed at equal intervals, which reduces edge crossings between
	 * rings. Nodes for which no parent can be found are placed at the end.
	 */
	private layoutConcentric(graph: Graph, center: string): void {
		graph.setNodeAttribute(center, "x", 0);
		graph.setNodeAttribute(center, "y", 0);

		const byDepth = new Map<number, string[]>();
		graph.forEachNode((node, attrs) => {
			if (node === center) return;
			const d = attrs["depth"] as number;
			const list = byDepth.get(d);
			if (list) list.push(node);
			else byDepth.set(d, [node]);
		});
		if (byDepth.size === 0) return;

		const angleOf = new Map<string, number>([[center, 0]]);
		const maxDepth = Math.max(...byDepth.keys());

		for (let d = 1; d <= maxDepth; d++) {
			const ring = byDepth.get(d);
			if (!ring || ring.length === 0) continue;

			let ordered: string[];
			if (d === 1) {
				ordered = [...ring].sort(
					(a, b) =>
						(graph.getNodeAttribute(b, "ppr") as number) -
						(graph.getNodeAttribute(a, "ppr") as number)
				);
			} else {
				const withParent: { node: string; angle: number }[] = [];
				const withoutParent: string[] = [];
				for (const node of ring) {
					let bestParent: string | null = null;
					let bestPpr = -Infinity;
					graph.forEachNeighbor(node, (nb) => {
						if (!angleOf.has(nb)) return;
						const nbDepth = graph.getNodeAttribute(nb, "depth") as number;
						if (nbDepth !== d - 1) return;
						const nbPpr = graph.getNodeAttribute(nb, "ppr") as number;
						if (nbPpr > bestPpr) {
							bestPpr = nbPpr;
							bestParent = nb;
						}
					});
					if (bestParent !== null) {
						withParent.push({ node, angle: angleOf.get(bestParent) as number });
					} else {
						withoutParent.push(node);
					}
				}
				withParent.sort((a, b) => a.angle - b.angle);
				ordered = [...withParent.map((x) => x.node), ...withoutParent];
			}

			const radius = d * RING_RADIUS;
			const count = ordered.length;
			ordered.forEach((node, i) => {
				const angle = (2 * Math.PI * i) / count;
				angleOf.set(node, angle);
				graph.setNodeAttribute(node, "x", radius * Math.cos(angle));
				graph.setNodeAttribute(node, "y", radius * Math.sin(angle));
			});
		}
	}
}
