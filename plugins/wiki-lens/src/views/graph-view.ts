import Graph from "graphology";
import { WorkspaceLeaf } from "obsidian";
import {
	BackboneCache,
	backboneOptsKey,
	BackboneReady,
} from "../core/backbone-cache";
import {
	buildMetaGraph,
	ClusterInfo,
	assignLabelAngles,
	clusterNodeId,
	initMetaLayout,
	META_FA2_ITERATIONS,
	META_FA2_SETTINGS,
	separateNodes,
} from "../core/cluster";
import {
	CAMERA_LOD_DEBOUNCE_MS,
	CAMERA_RESET_RATIO,
	LOD_RATIO_THRESHOLD,
	LodMode,
	applyEdgeSkeleton,
	selectVisibleEdges,
} from "../core/edge-skeleton";
import {
	applyForceLabels,
	pickForceLabels,
	rankViewportLabels,
} from "../core/force-labels";
import { resolveLabelAngles } from "../core/label-collision";
import { nodeWeight } from "../core/metrics";
import { computeLabelBox } from "../core/label-box";
import { BackboneOptions, DEFAULT_BACKBONE } from "../core/filters";
import { GraphIndex } from "../core/graph-index";
import {
	COMMUNITY_PALETTE,
	STATUS_COLORS,
	TYPE_COLORS,
} from "../core/palette";
import {
	WIKI_PAGE_TYPE_LABELS,
	WIKI_PAGE_TYPES,
	WikiPageType,
} from "../core/types";
import {
	edgeTouchesHiddenType,
	includeTypesFromVisible,
	isNodeTypeVisible,
	TypeFilterStore,
	visibleGraphStats,
} from "../core/type-visibility";
import { SigmaBaseView } from "./sigma-base";

export const GRAPH_VIEW_TYPE = "wiki-lens-graph";

export { COMMUNITY_PALETTE, STATUS_COLORS, TYPE_COLORS };

type ColorMode = "cluster" | "type" | "status";
/** clusters: meta-graph overview / nodes: full backbone / cluster-detail: induced subgraph of a single cluster */
type ViewMode = "clusters" | "nodes" | "cluster-detail";

export class GraphView extends SigmaBaseView {
	private index: GraphIndex;
	private cache: BackboneCache;
	private typeStore: TypeFilterStore;
	private unsubReady: (() => void) | null = null;
	private opts: BackboneOptions = { ...DEFAULT_BACKBONE };
	private colorMode: ColorMode = "cluster";
	private mode: ViewMode = "clusters";
	private focusedCluster: number | null = null;
	private focusedClusterLabel: string | null = null;

	/** Full backbone with cluster attributes. Reused across mode switches (avoids recomputing louvain) */
	private backbone: Graph | null = null;
	private clusters: ClusterInfo[] = [];

	private statsEl: HTMLElement | null = null;
	private containerDiv: HTMLElement | null = null;
	private panelEl: HTMLElement | null = null;

	// References to toolbar elements (used for mode-dependent enable/disable control)
	private overviewBtn: HTMLElement | null = null;
	private nodesBtn: HTMLElement | null = null;
	private backBtn: HTMLElement | null = null;
	private detailLabelEl: HTMLElement | null = null;
	private colorSelect: HTMLSelectElement | null = null;
	private seedToggle: HTMLInputElement | null = null;
	private typeFilterWrap: HTMLElement | null = null;
	private typeFilterInputs: { type: WikiPageType; cb: HTMLInputElement }[] =
		[];
	/** All Nodes presentation filter. Layout / communities stay on the full backbone. */
	private visibleTypes: Set<WikiPageType>;
	/** Cluster list hover. Null when the pointer is on the graph or off the panel. */
	private hoveredCluster: number | null = null;
	/** Ignore the trailing click of a double-click after a cluster expands. */
	private ignoreClicksUntil = 0;
	/** True after ForceAtlas2 coordinates have been shown. Blocks circular swaps. */
	private frozenLayout = false;
	/** Index/cache rebuild is running; keep the frozen graph on screen. */
	private waitingForLayout = false;

	/** In-memory skeleton (reducer reads this; skipIndexation stays valid). */
	private visibleEdges: Set<string> = new Set();
	/** Viewport-ranked labels while zoomed. Empty in far view. */
	private zoomLabelSet: Set<string> = new Set();
	private lodMode: LodMode = "far";
	private cameraBound = false;
	private cameraDebounce: number | null = null;
	private onCameraUpdated = (): void => {
		if (this.cameraDebounce !== null) window.clearTimeout(this.cameraDebounce);
		this.cameraDebounce = window.setTimeout(() => {
			this.cameraDebounce = null;
			this.onCameraLodTick();
		}, CAMERA_LOD_DEBOUNCE_MS);
	};

	constructor(
		leaf: WorkspaceLeaf,
		index: GraphIndex,
		cache: BackboneCache,
		typeStore: TypeFilterStore
	) {
		super(leaf);
		this.index = index;
		this.cache = cache;
		this.typeStore = typeStore;
		this.visibleTypes = typeStore.loadVisibleTypes();
	}

	getViewType(): string {
		return GRAPH_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Wiki Lens: Backbone Graph";
	}

	getIcon(): string {
		return "git-fork";
	}

	async onOpen(): Promise<void> {
		const el = this.contentEl;
		el.empty();
		el.addClass("wiki-lens-graph");

		this.buildToolbar(el);
		const body = el.createDiv({ cls: "wiki-lens-graph-body" });
		this.containerDiv = body.createDiv({ cls: "wiki-lens-graph-container" });
		this.panelEl = body.createDiv({ cls: "wiki-lens-cluster-panel" });

		this.registerEvent(this.index.on("updated", () => this.rebuild()));
		this.unsubReady = this.cache.onReady((ready) => this.onCacheReady(ready));
		this.rebuild();
	}

	async onClose(): Promise<void> {
		this.unbindCameraLod();
		this.unsubReady?.();
		this.unsubReady = null;
		await super.onClose();
	}

	onResize(): void {
		super.onResize();
		if (this.mode === "nodes") this.refreshNodesCollision();
	}

	protected onSigmaContainerResize(): void {
		if (this.mode === "nodes") this.refreshNodesCollision();
	}

	private buildToolbar(el: HTMLElement): void {
		const bar = el.createDiv({ cls: "wiki-lens-toolbar" });

		// Switch mode (Overview / All Nodes)
		this.overviewBtn = bar.createEl("button", {
			cls: "wiki-lens-mode-btn",
			text: "Overview",
		});
		this.overviewBtn.onclick = () => this.setMode("clusters");
		this.nodesBtn = bar.createEl("button", {
			cls: "wiki-lens-mode-btn",
			text: "All Nodes",
		});
		this.nodesBtn.onclick = () => this.setMode("nodes");

		// "Back to overview" button + cluster name for cluster-detail
		this.backBtn = bar.createEl("button", { text: "← To overview" });
		this.backBtn.onclick = () => this.setMode("clusters");
		this.detailLabelEl = bar.createSpan({ cls: "wiki-lens-detail-label" });

		const mkToggle = (
			label: string,
			checked: boolean,
			onChange: (v: boolean) => void
		): HTMLInputElement => {
			const wrap = bar.createEl("label", { cls: "wiki-lens-toggle" });
			const cb = wrap.createEl("input", { type: "checkbox" });
			cb.checked = checked;
			cb.onchange = () => onChange(cb.checked);
			wrap.createSpan({ text: ` ${label}` });
			return cb;
		};

		this.seedToggle = mkToggle(
			"Show seed entities",
			this.opts.includeSeedEntities,
			(v) => {
				this.opts.includeSeedEntities = v;
				this.rebuild();
			}
		);
		mkToggle("Show orphan nodes", !this.opts.dropIsolated, (v) => {
			this.opts.dropIsolated = !v;
			this.rebuild();
		});

		this.typeFilterWrap = bar.createDiv({ cls: "wiki-lens-type-filters" });
		this.typeFilterWrap.style.display = "none";
		this.typeFilterWrap.setAttr("role", "group");
		this.typeFilterWrap.setAttr("aria-label", "Node types");
		this.typeFilterWrap.createSpan({
			cls: "wiki-lens-muted",
			text: "Types",
		});
		this.typeFilterInputs = [];
		for (const type of WIKI_PAGE_TYPES) {
			const wrap = this.typeFilterWrap.createEl("label", {
				cls: "wiki-lens-toggle",
			});
			const cb = wrap.createEl("input", { type: "checkbox" });
			cb.checked = this.visibleTypes.has(type);
			const swatch = wrap.createSpan({ cls: "wiki-lens-layer-swatch" });
			swatch.style.backgroundColor = TYPE_COLORS[type];
			wrap.createSpan({ text: WIKI_PAGE_TYPE_LABELS[type] });
			cb.onchange = () => this.setTypeVisible(type, cb.checked);
			this.typeFilterInputs.push({ type, cb });
		}

		const select = bar.createEl("select");
		for (const [v, label] of [
			["cluster", "Color: cluster (louvain)"],
			["type", "Color: type"],
			["status", "Color: status"],
		]) {
			select.createEl("option", { value: v, text: label });
		}
		select.value = this.colorMode;
		select.onchange = () => {
			this.colorMode = select.value as ColorMode;
			if (this.graph && this.mode !== "clusters") {
				this.applyColorsOn(this.graph);
			}
			this.refreshDisplay();
		};
		this.colorSelect = select;

		const search = bar.createEl("input", {
			type: "search",
			placeholder: "Search node (Enter)",
		});
		search.onkeydown = (e) => {
			if (e.key === "Enter") this.focusSearch(search.value);
		};

		const relayout = bar.createEl("button", { text: "Re-layout" });
		relayout.onclick = () => this.relayout();

		this.statsEl = bar.createSpan({ cls: "wiki-lens-muted" });
	}

	private rebuild(): void {
		if (!this.containerDiv) return;
		if (!this.index.built) {
			this.statsEl?.setText("Building index…");
			return;
		}

		const handle = this.cache.acquire(this.index, this.opts);
		if (!handle) {
			this.statsEl?.setText("Building index…");
			return;
		}
		if (!handle.laidOut && this.frozenLayout && this.backbone && this.sigma) {
			this.waitingForLayout = true;
			return;
		}
		this.stopLayout();
		this.backbone = handle.graph;
		this.clusters = handle.clusters;
		if (handle.laidOut) {
			this.frozenLayout = true;
			this.waitingForLayout = false;
		}
		this.rebindFocusedCluster();
		this.applyLodToBackbone(this.modeForBackbone());
		this.render();
		if (this.mode === "nodes") this.refreshNodesCollision();
	}

	private rebindFocusedCluster(): void {
		if (this.focusedCluster === null) {
			this.focusedClusterLabel = null;
			return;
		}
		if (this.clusters.some((c) => c.id === this.focusedCluster)) {
			this.focusedClusterLabel =
				this.clusters.find((c) => c.id === this.focusedCluster)?.label ??
				null;
			return;
		}
		const byLabel = this.focusedClusterLabel
			? this.clusters.find((c) => c.label === this.focusedClusterLabel)
			: undefined;
		if (byLabel) {
			this.focusedCluster = byLabel.id;
			this.focusedClusterLabel = byLabel.label;
			return;
		}
		this.focusedCluster = null;
		this.focusedClusterLabel = null;
		if (this.mode === "cluster-detail") {
			this.mode = "clusters";
			this.clearHover();
		}
	}

	private onCacheReady(ready: BackboneReady): void {
		if (!this.index.stats) return;
		if (ready.builtAt !== this.index.stats.builtAt) return;
		if (ready.optsKey !== backboneOptsKey(this.opts)) return;
		if (this.waitingForLayout || !this.backbone) {
			this.waitingForLayout = false;
			this.rebuild();
			return;
		}
		this.cache.applyPositions(this.opts, this.backbone);
		this.frozenLayout = true;
		if (this.mode === "clusters") return;
		if (this.graph && this.graph !== this.backbone) {
			this.cache.applyPositions(this.opts, this.graph);
		}
		this.pinFrozenBBox();
		if (this.mode === "nodes") this.refreshNodesCollision();
		else this.refreshDisplay();
	}

	/** Updates the sigma graph, panel, and stats according to the current mode */
	private render(): void {
		if (!this.containerDiv || !this.backbone) return;
		this.stopLayout();

		if (this.mode === "clusters") {
			this.clearFrozenBBox();
			const meta = buildMetaGraph(this.backbone, this.clusters);
			initMetaLayout(meta);
			this.setSigmaGraph(meta, this.containerDiv, null);
			this.applyModeSettings();
			const shown = (meta.getAttribute("interEdgeShown") as number) ?? 0;
			const total = (meta.getAttribute("interEdgeTotal") as number) ?? meta.size;
			this.statsEl?.setText(
				`Clusters ${meta.order} / ${shown} of ${total} inter-cluster edges`
			);
			this.runLayout({
				settings: META_FA2_SETTINGS,
				iterations: META_FA2_ITERATIONS,
				getEdgeWeight: "weight",
			});
		} else if (
			this.mode === "cluster-detail" &&
			this.focusedCluster !== null
		) {
			const sub = this.inducedSubgraph(this.focusedCluster);
			this.applyColorsOn(sub);
			this.setSigmaGraph(sub, this.containerDiv, null);
			this.applyModeSettings();
			if (this.frozenLayout) this.pinFrozenBBox();
			const info = this.clusters.find(
				(c) => c.id === this.focusedCluster
			);
			this.statsEl?.setText(
				`Cluster "${info?.label ?? this.focusedCluster}": nodes ${sub.order} / edges ${sub.size}`
			);
		} else {
			this.applyColorsOn(this.backbone);
			this.setSigmaGraph(this.backbone, this.containerDiv, null);
			this.applyModeSettings();
			if (this.frozenLayout) this.pinFrozenBBox();
			this.updateNodesStats();
		}

		this.renderPanel();
		this.updateToolbarState();
		this.bindCameraLod();
		if (this.mode === "nodes") this.refreshNodesCollision();
	}

	private setMode(mode: ViewMode, cluster: number | null = null): void {
		this.clearHover();
		const enteringNodes = mode === "nodes" && this.mode !== "nodes";
		this.mode = mode;
		this.focusedCluster = mode === "cluster-detail" ? cluster : null;
		this.focusedClusterLabel =
			cluster !== null
				? (this.clusters.find((c) => c.id === cluster)?.label ?? null)
				: null;
		if (enteringNodes) {
			this.resetAllNodesCamera();
			this.applyLodToBackbone("far");
		}
		this.render();
	}

	private enterClusterDetail(community: number): void {
		this.setMode("cluster-detail", community);
	}

	/** Builds an induced subgraph containing only nodes of a given community from the backbone */
	private inducedSubgraph(community: number): Graph {
		const bb = this.backbone;
		const sub = new Graph({ type: "directed", multi: false });
		if (!bb) return sub;
		bb.forEachNode((node, attrs) => {
			if ((attrs["community"] as number) === community) {
				sub.addNode(node, { ...attrs, forceLabel: false });
			}
		});
		bb.forEachEdge((_edge, attrs, source, target) => {
			if (
				sub.hasNode(source) &&
				sub.hasNode(target) &&
				!sub.hasEdge(source, target)
			) {
				sub.addEdge(source, target, {
					...attrs,
					weak: false,
					hidden: false,
				});
			}
		});
		return sub;
	}

	private applyColorsOn(graph: Graph): void {
		graph.updateEachNodeAttributes(
			(_, attrs) => {
				let color: string;
				if (this.colorMode === "cluster") {
					color =
						COMMUNITY_PALETTE[
							((attrs["community"] as number) ?? 0) %
								COMMUNITY_PALETTE.length
						];
				} else if (this.colorMode === "type") {
					color = TYPE_COLORS[attrs["nodeType"] as string] ?? "#888888";
				} else {
					color = STATUS_COLORS[attrs["status"] as string] ?? "#888888";
				}
				attrs["color"] = color;
				return attrs;
			},
			{ attributes: ["color"] }
		);
	}

	private renderPanel(): void {
		const panel = this.panelEl;
		if (!panel) return;
		panel.empty();
		panel.createDiv({
			cls: "wiki-lens-cluster-panel-title",
			text: "Cluster list",
		});

		for (const c of this.clusters) {
			const item = panel.createDiv({ cls: "wiki-lens-cluster-item" });
			if (c.id === this.focusedCluster) {
				item.addClass("wiki-lens-cluster-active");
			}

			const header = item.createDiv({ cls: "wiki-lens-cluster-header" });
			const swatch = header.createSpan({ cls: "wiki-lens-cluster-swatch" });
			swatch.style.backgroundColor =
				COMMUNITY_PALETTE[c.id % COMMUNITY_PALETTE.length];
			header.createSpan({
				cls: "wiki-lens-cluster-name",
				text: c.label,
			});
			header.createSpan({
				cls: "wiki-lens-cluster-size wiki-lens-muted",
				text: `${c.size}`,
			});
			header.onclick = () => this.enterClusterDetail(c.id);
			item.addEventListener("pointerenter", () =>
				this.setHoveredCluster(c.id)
			);
			item.addEventListener("pointerleave", () => {
				if (this.hoveredCluster === c.id) this.setHoveredCluster(null);
			});

			const members = item.createDiv({ cls: "wiki-lens-cluster-members" });
			for (const m of c.topMembers) {
				const row = members.createDiv({
					cls: "wiki-lens-cluster-member wiki-lens-link",
					text: m.title,
				});
				row.onclick = () =>
					void this.app.workspace.openLinkText(m.path, "/", false);
			}
		}
	}

	private updateToolbarState(): void {
		this.overviewBtn?.toggleClass(
			"wiki-lens-mode-active",
			this.mode === "clusters"
		);
		this.nodesBtn?.toggleClass(
			"wiki-lens-mode-active",
			this.mode === "nodes"
		);

		const detail = this.mode === "cluster-detail";
		if (this.backBtn) this.backBtn.style.display = detail ? "" : "none";
		if (this.detailLabelEl) {
			this.detailLabelEl.style.display = detail ? "" : "none";
			if (detail) {
				const info = this.clusters.find(
					(c) => c.id === this.focusedCluster
				);
				this.detailLabelEl.setText(info ? info.label : "");
			}
		}

		// Color mode and seed toggle are meaningless in clusters mode, so disable them
		const disable = this.mode === "clusters";
		if (this.colorSelect) this.colorSelect.disabled = disable;
		if (this.seedToggle) this.seedToggle.disabled = disable;
		if (this.typeFilterWrap) {
			this.typeFilterWrap.style.display =
				this.mode === "nodes" ? "" : "none";
		}
		this.syncTypeFilterInputs();
	}

	private setTypeVisible(type: WikiPageType, visible: boolean): void {
		if (
			!visible &&
			this.visibleTypes.size === 1 &&
			this.visibleTypes.has(type)
		) {
			this.syncTypeFilterInputs();
			return;
		}
		if (visible) this.visibleTypes.add(type);
		else this.visibleTypes.delete(type);
		this.typeStore.saveVisibleTypes(this.visibleTypes);
		this.syncTypeFilterInputs();
		if (this.hoveredNode && this.graph) {
			const hoveredType = this.graph.getNodeAttribute(
				this.hoveredNode,
				"nodeType"
			);
			if (!isNodeTypeVisible(hoveredType, this.includeTypesOpt())) {
				this.clearHover();
			}
		}
		if (this.mode !== "nodes" || !this.backbone) return;
		this.applyLodToBackbone(this.modeForBackbone());
		this.updateNodesStats();
		this.refreshNodesCollision();
	}

	private syncTypeFilterInputs(): void {
		const lastOn = this.visibleTypes.size === 1;
		for (const { type, cb } of this.typeFilterInputs) {
			const on = this.visibleTypes.has(type);
			cb.checked = on;
			cb.disabled = lastOn && on;
			cb.title = lastOn && on ? "At least one type must stay visible" : "";
		}
	}

	private includeTypesOpt(): ReadonlySet<string> | undefined {
		return includeTypesFromVisible(this.visibleTypes);
	}

	private updateNodesStats(): void {
		if (!this.backbone) return;
		const includeTypes = this.includeTypesOpt();
		if (!includeTypes) {
			this.statsEl?.setText(
				`Nodes ${this.backbone.order} / edges ${this.backbone.size} (skeleton ${this.visibleEdges.size}) / clusters ${this.clusters.length}`
			);
			return;
		}
		const stats = visibleGraphStats(this.backbone, includeTypes);
		this.statsEl?.setText(
			`Nodes ${stats.nodes} of ${this.backbone.order} / edges ${stats.edges} of ${this.backbone.size} (skeleton ${this.visibleEdges.size}) / clusters ${stats.clusters} of ${this.clusters.length}`
		);
	}

	private applyModeSettings(): void {
		if (!this.sigma) return;
		if (this.mode === "clusters") {
			this.sigma.setSetting("stagePadding", 72);
			this.sigma.setSetting("labelSize", 12);
			this.sigma.setSetting("labelRenderedSizeThreshold", 0);
			this.sigma.setSetting("labelDensity", 8);
			this.sigma.setSetting("minEdgeThickness", 1.1);
			return;
		}
		this.sigma.setSetting("stagePadding", 30);
		this.sigma.setSetting("labelSize", 11);
		this.sigma.setSetting("labelRenderedSizeThreshold", 7);
		this.sigma.setSetting("labelDensity", 1);
		this.sigma.setSetting(
			"minEdgeThickness",
			this.mode === "nodes" ? 0.4 : 1.7
		);
	}

	private relayout(): void {
		if (this.mode === "clusters" && this.graph) {
			initMetaLayout(this.graph);
		}
		if (this.mode === "clusters") {
			this.runLayout({
				settings: META_FA2_SETTINGS,
				iterations: META_FA2_ITERATIONS,
				getEdgeWeight: "weight",
			});
			return;
		}
		this.runLayout();
	}

	protected onLayoutFinished(): void {
		if (this.mode === "clusters" && this.graph) {
			separateNodes(this.graph);
			assignLabelAngles(this.graph);
			this.resolveOverviewLabelAngles();
			this.sigma?.refresh();
			return;
		}
		this.frozenLayout = true;
		this.pinFrozenBBox();
		if (this.mode === "nodes") this.refreshNodesCollision();
	}

	/**
	 * Greedy 4-way fallback when two radial labels would land on top of
	 * each other. Uses viewport pixels so it matches what the user sees.
	 */
	private resolveOverviewLabelAngles(): void {
		const graph = this.graph;
		const sigma = this.sigma;
		if (!graph || !sigma) return;
		const labeled: string[] = [];
		graph.forEachNode((node, attrs) => {
			if (attrs["forceLabel"] === true || this.zoomLabelSet.has(node)) {
				labeled.push(node);
			}
		});
		resolveLabelAngles(graph, labeled, (x, y) =>
			sigma.graphToViewport({ x, y })
		);
	}

	protected nodeReducer(
		node: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const res = super.nodeReducer(node, data);
		if (this.mode === "clusters" && res["isCluster"] === true) {
			const shortLabel = (data["label"] as string) ?? "";
			const fullLabel = (data["fullLabel"] as string) ?? shortLabel;
			if (
				this.hoveredNode === node ||
				this.hoveredNeighbors?.has(node) === true
			) {
				res["label"] = fullLabel;
			} else if (data["forceLabel"] === true) {
				res["label"] = shortLabel;
			} else {
				res["label"] = "";
			}
			if (this.clusterHoverActive()) {
				if (this.nodeInHoveredCluster(data)) {
					res["highlighted"] = true;
					res["label"] = fullLabel;
				} else {
					res["color"] = "rgba(90, 90, 90, 0.25)";
					res["label"] = "";
				}
			}
			return res;
		}
		if (this.mode === "nodes") {
			if (!isNodeTypeVisible(data["nodeType"], this.includeTypesOpt())) {
				res["hidden"] = true;
				res["label"] = "";
				return res;
			}
			if (this.clusterHoverActive()) {
				if (!this.nodeInHoveredCluster(data)) {
					res["color"] = "rgba(90, 90, 90, 0.25)";
					res["label"] = "";
					return res;
				}
			}
			const keep =
				this.hoveredNode === node ||
				this.hoveredNeighbors?.has(node) === true ||
				res["forceLabel"] === true ||
				res["highlighted"] === true ||
				this.zoomLabelSet.has(node);
			if (keep) {
				if (
					!res["label"] &&
					typeof data["label"] === "string"
				) {
					res["label"] = data["label"];
				}
			} else {
				res["label"] = "";
			}
		}
		return res;
	}

	protected edgeReducer(
		edge: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const res = super.edgeReducer(edge, data);
		if (this.mode === "clusters") {
			if (this.hoveredNode && this.graph) {
				if (this.graph.hasExtremity(edge, this.hoveredNode)) {
					res["hidden"] = false;
				}
				return res;
			}
			if (this.clusterHoverActive() && this.graph) {
				const clusterNode = clusterNodeId(this.hoveredCluster as number);
				if (
					this.graph.hasNode(clusterNode) &&
					this.graph.hasExtremity(edge, clusterNode)
				) {
					res["hidden"] = false;
					res["color"] = "rgba(200, 160, 60, 0.6)";
				} else {
					res["hidden"] = true;
				}
				return res;
			}
			if (res["weak"] === true) res["hidden"] = true;
			return res;
		}
		if (this.mode === "nodes") {
			if (
				this.graph &&
				edgeTouchesHiddenType(
					this.graph,
					edge,
					this.includeTypesOpt()
				)
			) {
				res["hidden"] = true;
				return res;
			}
			if (this.hoveredNode && this.graph) {
				if (this.graph.hasExtremity(edge, this.hoveredNode)) {
					res["hidden"] = false;
				}
				return res;
			}
			if (this.clusterHoverActive() && this.graph) {
				const [source, target] = this.graph.extremities(edge);
				const intra =
					this.graph.getNodeAttribute(source, "community") ===
						this.hoveredCluster &&
					this.graph.getNodeAttribute(target, "community") ===
						this.hoveredCluster;
				if (intra) {
					res["hidden"] = false;
					res["color"] = "rgba(200, 160, 60, 0.45)";
				} else {
					res["hidden"] = true;
				}
				return res;
			}
			if (!this.visibleEdges.has(edge)) res["hidden"] = true;
			return res;
		}
		return res;
	}

	/** Click a super-node (or its label via onStageClick) to drill into the cluster. */
	protected onNodeClick(node: string): void {
		if (Date.now() < this.ignoreClicksUntil) return;
		if (this.graph?.getNodeAttribute(node, "isCluster")) {
			this.expandCluster(node);
			return;
		}
		if (
			this.mode === "nodes" &&
			this.graph &&
			!isNodeTypeVisible(
				this.graph.getNodeAttribute(node, "nodeType"),
				this.includeTypesOpt()
			)
		) {
			return;
		}
		super.onNodeClick(node);
	}

	protected acceptHover(node: string): boolean {
		if (this.mode !== "nodes" || !this.graph) return true;
		return isNodeTypeVisible(
			this.graph.getNodeAttribute(node, "nodeType"),
			this.includeTypesOpt()
		);
	}

	protected onNodeDoubleClick(node: string): void {
		if (this.graph?.getNodeAttribute(node, "isCluster")) {
			this.expandCluster(node);
		}
	}

	/** Labels sit outside the disc, so a click on the box is a stage click. */
	protected onStageClick(event: { x: number; y: number }): void {
		if (this.mode !== "clusters") return;
		if (Date.now() < this.ignoreClicksUntil) return;
		const node = this.hitTestOverviewLabel(event.x, event.y);
		if (node) this.expandCluster(node);
	}

	private expandCluster(node: string): void {
		if (!this.graph) return;
		const cid = this.graph.getNodeAttribute(node, "clusterId") as number;
		if (typeof cid !== "number") return;
		this.ignoreClicksUntil = Date.now() + 350;
		this.enterClusterDetail(cid);
	}

	private hitTestOverviewLabel(x: number, y: number): string | null {
		const graph = this.graph;
		const sigma = this.sigma;
		if (!graph || !sigma) return null;
		const fontSize = sigma.getSetting("labelSize") ?? 12;
		let hit: string | null = null;
		graph.forEachNode((node, attrs) => {
			if (hit || attrs["isCluster"] !== true) return;
			const display = sigma.getNodeDisplayData(node);
			const label = (display?.["label"] as string) ?? "";
			if (!label) return;
			const vp = sigma.graphToViewport({
				x: attrs["x"] as number,
				y: attrs["y"] as number,
			});
			const rawSize =
				(display?.size as number | undefined) ??
				((attrs["size"] as number) || 8);
			const box = computeLabelBox({
				x: vp.x,
				y: vp.y,
				nodeSize: sigma.scaleSize(rawSize),
				angle: attrs["labelAngle"] as number | undefined,
				textWidth: Math.min(28, label.length) * fontSize * 0.62,
				fontSize,
				viewportY: true,
			});
			if (
				x >= box.boxX &&
				x <= box.boxX + box.boxW &&
				y >= box.boxY &&
				y <= box.boxY + box.boxH
			) {
				hit = node;
			}
		});
		return hit;
	}

	private clearHover(): void {
		this.hoveredNode = null;
		this.hoveredNeighbors = null;
		this.hoveredCluster = null;
	}

	private clusterHoverActive(): boolean {
		return this.hoveredCluster !== null && this.hoveredNode === null;
	}

	private nodeInHoveredCluster(data: Record<string, unknown>): boolean {
		if (this.hoveredCluster === null) return false;
		if (data["isCluster"] === true) {
			return data["clusterId"] === this.hoveredCluster;
		}
		return data["community"] === this.hoveredCluster;
	}

	private setHoveredCluster(id: number | null): void {
		if (this.hoveredCluster === id) return;
		this.hoveredCluster = id;
		this.refreshDisplay();
	}

	private modeForBackbone(): LodMode {
		if (this.mode === "nodes" && this.sigma) {
			return this.sigma.getCamera().ratio < LOD_RATIO_THRESHOLD
				? "zoomed"
				: "far";
		}
		return "far";
	}

	private applyLodToBackbone(mode: LodMode): void {
		if (!this.backbone) return;
		const includeTypes = this.includeTypesOpt();
		applyForceLabels(
			this.backbone,
			pickForceLabels(this.backbone, this.clusters, { includeTypes })
		);
		const visible = selectVisibleEdges(this.backbone, mode, {
			includeTypes,
		});
		applyEdgeSkeleton(this.backbone, visible);
		this.visibleEdges = visible;
		this.lodMode = mode;
	}

	private resetAllNodesCamera(): void {
		if (!this.sigma) return;
		this.sigma.getCamera().setState({
			x: 0.5,
			y: 0.5,
			ratio: CAMERA_RESET_RATIO,
		});
		this.lodMode = "far";
	}

	private refreshNodesCollision(): void {
		if (this.mode !== "nodes" || !this.sigma) return;
		this.syncZoomLabels();
		this.resolveOverviewLabelAngles();
		this.refreshDisplay();
	}

	private syncZoomLabels(): void {
		if (this.lodMode !== "zoomed" || !this.sigma || !this.graph) {
			this.zoomLabelSet = new Set();
			return;
		}
		const sigma = this.sigma;
		const dims = sigma.getDimensions();
		const pad = 40;
		const candidates: { path: string; inCount: number }[] = [];
		this.graph.forEachNode((node, attrs) => {
			if (!isNodeTypeVisible(attrs["nodeType"], this.includeTypesOpt())) {
				return;
			}
			const x = attrs["x"];
			const y = attrs["y"];
			if (typeof x !== "number" || typeof y !== "number") return;
			const vp = sigma.graphToViewport({ x, y });
			if (
				vp.x < -pad ||
				vp.y < -pad ||
				vp.x > dims.width + pad ||
				vp.y > dims.height + pad
			) {
				return;
			}
			candidates.push({ path: node, inCount: nodeWeight(attrs) });
		});
		this.zoomLabelSet = new Set(rankViewportLabels(candidates));
	}

	private bindCameraLod(): void {
		if (!this.sigma || this.cameraBound) return;
		this.cameraBound = true;
		this.sigma.getCamera().on("updated", this.onCameraUpdated);
	}

	private unbindCameraLod(): void {
		if (this.sigma && this.cameraBound) {
			this.sigma.getCamera().off("updated", this.onCameraUpdated);
		}
		this.cameraBound = false;
		if (this.cameraDebounce !== null) {
			window.clearTimeout(this.cameraDebounce);
			this.cameraDebounce = null;
		}
	}

	private onCameraLodTick(): void {
		if (this.mode !== "nodes" || !this.backbone || !this.sigma) return;
		const next: LodMode =
			this.sigma.getCamera().ratio < LOD_RATIO_THRESHOLD
				? "zoomed"
				: "far";
		const crossed = next !== this.lodMode;
		if (crossed) this.applyLodToBackbone(next);
		if (crossed || next === "zoomed") this.refreshNodesCollision();
	}

	private focusSearch(query: string): void {
		if (!query || !this.backbone) return;
		const q = query.toLowerCase();

		if (this.mode === "clusters") {
			// Find a member matching the label in the backbone, then enter that cluster's detail
			let foundNode: string | null = null;
			let foundCommunity: number | null = null;
			this.backbone.forEachNode((node, attrs) => {
				if (foundNode) return;
				const label = attrs["label"] as string;
				if (label && label.toLowerCase().includes(q)) {
					foundNode = node;
					foundCommunity = attrs["community"] as number;
				}
			});
			if (foundNode === null || foundCommunity === null) return;
			this.enterClusterDetail(foundCommunity);
			this.focusNode(foundNode);
			return;
		}

		this.focusNode(this.findInGraph(q));
	}

	private findInGraph(q: string): string | null {
		let found: string | null = null;
		this.graph?.forEachNode((node, attrs) => {
			if (found) return;
			if (
				this.mode === "nodes" &&
				!isNodeTypeVisible(attrs["nodeType"], this.includeTypesOpt())
			) {
				return;
			}
			const label = attrs["label"] as string;
			if (label && label.toLowerCase().includes(q)) found = node;
		});
		return found;
	}

	private focusNode(node: string | null): void {
		if (!node || !this.graph || !this.sigma) return;
		const pos = this.sigma.getNodeDisplayData(node);
		if (!pos) return;
		this.hoveredNode = node;
		this.hoveredNeighbors = new Set(this.graph.neighbors(node));
		this.sigma.getCamera().animate(
			{ x: pos.x, y: pos.y, ratio: 0.15 },
			{ duration: 500 }
		);
		this.refreshDisplay();
	}
}
