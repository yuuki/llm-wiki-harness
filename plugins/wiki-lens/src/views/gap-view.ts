import Graph from "graphology";
import { Notice, WorkspaceLeaf } from "obsidian";
import {
	BackboneCache,
	backboneOptsKey,
	BackboneReady,
} from "../core/backbone-cache";
import { ClusterInfo } from "../core/cluster";
import { DEFAULT_BACKBONE } from "../core/filters";
import {
	analyzeGaps,
	BridgeCandidate,
	buildGapReport,
	ClusterGap,
	ConceptPairGap,
	DEFAULT_GAP_OPTIONS,
	GapAnalysis,
	PredictedLink,
} from "../core/gap";
import { GraphIndex } from "../core/graph-index";
import { COMMUNITY_PALETTE } from "../core/palette";
import { SigmaBaseView } from "./sigma-base";

type SelectionKey =
	| { tab: "cluster-gaps"; labelA: string; labelB: string }
	| { tab: "concept-pairs"; a: string; b: string }
	| { tab: "predicted-links"; a: string; b: string }
	| { tab: "bridges"; target: string };

export const GAP_VIEW_TYPE = "wiki-lens-gap";

type GapTab =
	| "cluster-gaps"
	| "concept-pairs"
	| "predicted-links"
	| "bridges";

type LinkCandidate = ConceptPairGap | PredictedLink;

interface GapSelection {
	tab: GapTab;
	index: number;
}

interface Point {
	x: number;
	y: number;
}

const GAP_TABS: { id: GapTab; label: string }[] = [
	{ id: "cluster-gaps", label: "Cluster gaps" },
	{ id: "concept-pairs", label: "Concept pairs" },
	{ id: "predicted-links", label: "Predicted links" },
	{ id: "bridges", label: "Bridge literature" },
];

const DIM_COLOR = "rgba(90, 90, 90, 0.25)";
const GAP_LINE_FAINT = "rgba(200, 160, 60, 0.25)";
const GAP_LINE_BOLD = "rgba(232, 184, 75, 0.95)";

export class GapView extends SigmaBaseView {
	private index: GraphIndex;
	private cache: BackboneCache;
	private unsubReady: (() => void) | null = null;
	private activeTab: GapTab = "cluster-gaps";
	private selection: GapSelection | null = null;

	private backbone: Graph | null = null;
	private clusters: ClusterInfo[] = [];
	private analysis: GapAnalysis | null = null;
	private clusterCentroids = new Map<number, Point>();
	private selectedPairNodes: Set<string> | null = null;
	private selectedBridgeReferrers: Set<string> | null = null;

	private statsEl: HTMLElement | null = null;
	private containerDiv: HTMLElement | null = null;
	private panelEl: HTMLElement | null = null;
	private tabButtons: Partial<Record<GapTab, HTMLElement>> = {};
	private overlayCanvas: HTMLCanvasElement | null = null;
	private overlayCtx: CanvasRenderingContext2D | null = null;
	private overlayResizeObserver: ResizeObserver | null = null;
	private overlayRenderSubscribed = false;

	constructor(leaf: WorkspaceLeaf, index: GraphIndex, cache: BackboneCache) {
		super(leaf);
		this.index = index;
		this.cache = cache;
	}

	getViewType(): string {
		return GAP_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Wiki Lens: Gap Finder";
	}

	getIcon(): string {
		return "unlink";
	}

	async onOpen(): Promise<void> {
		const el = this.contentEl;
		el.empty();
		el.addClass("wiki-lens-graph");

		this.buildToolbar(el);
		const body = el.createDiv({ cls: "wiki-lens-graph-body" });
		this.containerDiv = body.createDiv({ cls: "wiki-lens-graph-container" });
		this.panelEl = body.createDiv({
			cls: "wiki-lens-cluster-panel wiki-lens-gap-panel",
		});

		this.registerEvent(this.index.on("updated", () => this.rebuild()));
		this.unsubReady = this.cache.onReady((ready) => this.onCacheReady(ready));
		this.rebuild();
	}

	async onClose(): Promise<void> {
		this.unsubReady?.();
		this.unsubReady = null;
		this.overlayResizeObserver?.disconnect();
		this.overlayResizeObserver = null;
		this.overlayCanvas?.remove();
		this.overlayCanvas = null;
		this.overlayCtx = null;
		this.overlayRenderSubscribed = false;
		await super.onClose();
	}

	private buildToolbar(el: HTMLElement): void {
		const bar = el.createDiv({ cls: "wiki-lens-toolbar" });

		for (const tab of GAP_TABS) {
			const btn = bar.createEl("button", {
				cls: "wiki-lens-mode-btn",
				text: tab.label,
			});
			btn.onclick = () => this.setActiveTab(tab.id);
			this.tabButtons[tab.id] = btn;
		}

		const copy = bar.createEl("button", { text: "Copy report" });
		copy.onclick = () => void this.copyReport();

		this.statsEl = bar.createSpan({ cls: "wiki-lens-muted" });
		bar.createSpan({
			cls: "wiki-lens-gap-tab-note wiki-lens-muted",
			text: "Candidates are computed suggestions — these links do not exist (yet).",
		});

		this.updateToolbarState();
	}

	private rebuild(): void {
		if (!this.containerDiv) return;
		if (!this.index.built) {
			this.stopLayout();
			this.backbone = null;
			this.clusters = [];
			this.analysis = null;
			this.selection = null;
			this.refreshSelectionCaches();
			this.clusterCentroids.clear();
			this.statsEl?.setText("Building index…");
			this.renderPanel();
			this.clearOverlay();
			return;
		}

		this.stopLayout();
		const previous = this.captureSelection();
		this.clusterCentroids.clear();

		const handle = this.cache.acquire(this.index, DEFAULT_BACKBONE);
		if (!handle) {
			this.statsEl?.setText("Building index…");
			return;
		}
		handle.graph.updateEachNodeAttributes((_, attrs) => {
			const community = (attrs["community"] as number) ?? 0;
			attrs["color"] =
				COMMUNITY_PALETTE[community % COMMUNITY_PALETTE.length];
			return attrs;
		});

		this.backbone = handle.graph;
		this.clusters = handle.clusters;
		this.analysis = analyzeGaps(
			this.index,
			handle.graph,
			this.clusters,
			DEFAULT_GAP_OPTIONS
		);
		this.restoreSelection(previous);

		this.setSigmaGraph(handle.graph, this.containerDiv, null);
		this.ensureOverlay();
		this.ensureOverlayRenderHook();
		this.renderPanel();
		this.updateToolbarState();
		this.updateStats();
		if (handle.laidOut) this.recomputeClusterCentroids();
		this.redrawOverlay();
	}

	private onCacheReady(ready: BackboneReady): void {
		if (!this.graph || !this.index.stats) return;
		if (ready.builtAt !== this.index.stats.builtAt) return;
		if (ready.optsKey !== backboneOptsKey(DEFAULT_BACKBONE)) return;
		this.cache.applyPositions(DEFAULT_BACKBONE, this.graph);
		this.recomputeClusterCentroids();
		this.sigma?.refresh({ skipIndexation: true });
		this.redrawOverlay();
	}

	private captureSelection(): SelectionKey | null {
		if (!this.selection || !this.analysis) return null;
		if (this.selection.tab === "cluster-gaps") {
			const gap = this.analysis.clusterGaps[this.selection.index];
			return gap
				? { tab: "cluster-gaps", labelA: gap.labelA, labelB: gap.labelB }
				: null;
		}
		if (this.selection.tab === "concept-pairs") {
			const pair = this.analysis.conceptPairs[this.selection.index];
			return pair
				? { tab: "concept-pairs", a: pair.a, b: pair.b }
				: null;
		}
		if (this.selection.tab === "predicted-links") {
			const link = this.analysis.predictedLinks[this.selection.index];
			return link
				? { tab: "predicted-links", a: link.a, b: link.b }
				: null;
		}
		const bridge = this.analysis.bridges[this.selection.index];
		return bridge ? { tab: "bridges", target: bridge.target } : null;
	}

	private restoreSelection(key: SelectionKey | null): void {
		this.selection = null;
		if (!key || !this.analysis) {
			this.refreshSelectionCaches();
			return;
		}
		if (key.tab === "cluster-gaps") {
			const index = this.analysis.clusterGaps.findIndex(
				(g) =>
					(g.labelA === key.labelA && g.labelB === key.labelB) ||
					(g.labelA === key.labelB && g.labelB === key.labelA)
			);
			if (index >= 0) this.selection = { tab: key.tab, index };
		} else if (key.tab === "concept-pairs") {
			const index = this.analysis.conceptPairs.findIndex(
				(p) =>
					(p.a === key.a && p.b === key.b) ||
					(p.a === key.b && p.b === key.a)
			);
			if (index >= 0) this.selection = { tab: key.tab, index };
		} else if (key.tab === "predicted-links") {
			const index = this.analysis.predictedLinks.findIndex(
				(p) =>
					(p.a === key.a && p.b === key.b) ||
					(p.a === key.b && p.b === key.a)
			);
			if (index >= 0) this.selection = { tab: key.tab, index };
		} else {
			const index = this.analysis.bridges.findIndex(
				(b) => b.target === key.target
			);
			if (index >= 0) this.selection = { tab: key.tab, index };
		}
		this.refreshSelectionCaches();
	}

	private setActiveTab(tab: GapTab): void {
		if (this.activeTab === tab) return;
		this.activeTab = tab;
		this.selection = null;
		this.refreshSelectionCaches();
		this.updateToolbarState();
		this.renderPanel();
		this.updateStats();
		this.sigma?.refresh({ skipIndexation: true });
		this.redrawOverlay();
	}

	private toggleSelection(tab: GapTab, index: number): void {
		if (
			this.selection?.tab === tab &&
			this.selection.index === index
		) {
			this.selection = null;
		} else {
			this.selection = { tab, index };
		}
		this.refreshSelectionCaches();
		this.renderPanel();
		this.sigma?.refresh({ skipIndexation: true });
		this.redrawOverlay();
	}

	private async copyReport(): Promise<void> {
		if (!this.analysis) return;
		if (!navigator.clipboard) {
			new Notice("Clipboard unavailable");
			return;
		}
		const generatedAt = new Date().toISOString().slice(0, 10);
		await navigator.clipboard.writeText(
			buildGapReport(this.analysis, generatedAt)
		);
		new Notice("Gap report copied");
	}

	private renderPanel(): void {
		const panel = this.panelEl;
		if (!panel) return;
		panel.empty();
		panel.createDiv({
			cls: "wiki-lens-cluster-panel-title",
			text: this.panelTitle(),
		});

		if (!this.analysis) {
			panel.createDiv({
				cls: "wiki-lens-gap-empty wiki-lens-muted",
				text: this.index.built ? "No analysis available" : "Building index…",
			});
			return;
		}

		if (this.activeTab === "cluster-gaps") {
			this.renderClusterGaps(panel, this.analysis.clusterGaps);
		} else if (this.activeTab === "concept-pairs") {
			this.renderConceptPairs(panel, this.analysis.conceptPairs);
		} else if (this.activeTab === "predicted-links") {
			this.renderPredictedLinks(panel, this.analysis.predictedLinks);
		} else {
			this.renderBridges(panel, this.analysis.bridges);
		}

		if (this.analysis.notes.length > 0) {
			const notes = panel.createDiv({ cls: "wiki-lens-gap-notes" });
			for (const note of this.analysis.notes) {
				notes.createDiv({
					cls: "wiki-lens-gap-note wiki-lens-muted",
					text: note,
				});
			}
		}
	}

	private renderClusterGaps(
		panel: HTMLElement,
		items: ClusterGap[]
	): void {
		for (let i = 0; i < items.length; i++) {
			const gap = items[i];
			const row = this.createCandidateRow(panel, "cluster-gaps", i);
			row.createDiv({
				cls: "wiki-lens-gap-title",
				text: `${gap.labelA} ⇢ ${gap.labelB}`,
			});
			row.createDiv({
				cls: "wiki-lens-gap-second wiki-lens-muted",
				text: `expected ${gap.expected.toFixed(1)} / actual ${gap.actual} / ratio ${gap.ratio.toFixed(2)}`,
			});
		}
	}

	private renderConceptPairs(
		panel: HTMLElement,
		items: ConceptPairGap[]
	): void {
		for (let i = 0; i < items.length; i++) {
			const pair = items[i];
			const parts = [`${pair.commonSources.length} common sources`];
			if (pair.crossCluster) parts.push("cross-cluster");
			this.renderLinkPairRow(
				panel,
				"concept-pairs",
				i,
				pair,
				parts.join(" · ")
			);
		}
	}

	private renderPredictedLinks(
		panel: HTMLElement,
		items: PredictedLink[]
	): void {
		for (let i = 0; i < items.length; i++) {
			const link = items[i];
			const parts = [`AA ${link.score.toFixed(2)}`];
			if (link.crossCluster) parts.push("cross-cluster");
			this.renderLinkPairRow(
				panel,
				"predicted-links",
				i,
				link,
				parts.join(" · ")
			);
		}
	}

	private renderBridges(
		panel: HTMLElement,
		items: BridgeCandidate[]
	): void {
		for (let i = 0; i < items.length; i++) {
			const bridge = items[i];
			const row = this.createCandidateRow(panel, "bridges", i);
			row.createDiv({
				cls: "wiki-lens-gap-title wiki-lens-target",
				text: bridge.target,
			});
			const onGraph = this.backbone
				? bridge.referrers.filter((r) => this.backbone?.hasNode(r))
						.length
				: 0;
			const stubs = bridge.referrers.length - onGraph;
			row.createDiv({
				cls: "wiki-lens-gap-second wiki-lens-muted",
				text: `${bridge.refs} refs · ${onGraph} on graph / ${stubs} stubs · ${bridge.clusters.length} clusters`,
			});
		}
	}

	private renderLinkPairRow(
		panel: HTMLElement,
		tab: "concept-pairs" | "predicted-links",
		index: number,
		pair: LinkCandidate,
		secondLine: string
	): void {
		const row = this.createCandidateRow(panel, tab, index);
		const title = row.createDiv({
			cls: "wiki-lens-gap-title wiki-lens-gap-link-title",
		});
		this.createNoteLink(title, pair.a, pair.titleA);
		title.createSpan({ cls: "wiki-lens-gap-arrow", text: " ⇢ " });
		this.createNoteLink(title, pair.b, pair.titleB);
		row.createDiv({
			cls: "wiki-lens-gap-second wiki-lens-muted",
			text: secondLine,
		});
	}

	private createCandidateRow(
		panel: HTMLElement,
		tab: GapTab,
		index: number
	): HTMLElement {
		const row = panel.createDiv({ cls: "wiki-lens-gap-row" });
		if (this.isSelected(tab, index)) {
			row.addClass("wiki-lens-cluster-active");
		}
		row.onclick = () => this.toggleSelection(tab, index);
		return row;
	}

	private createNoteLink(
		parent: HTMLElement,
		path: string,
		title: string
	): void {
		const link = parent.createSpan({
			cls: "wiki-lens-link wiki-lens-gap-note-link",
			text: title,
		});
		link.onclick = (event) => {
			event.stopPropagation();
			void this.app.workspace.openLinkText(path, "/", false);
		};
	}

	private panelTitle(): string {
		const tab = GAP_TABS.find((t) => t.id === this.activeTab);
		return tab?.label ?? "Candidates";
	}

	private updateToolbarState(): void {
		for (const tab of GAP_TABS) {
			this.tabButtons[tab.id]?.toggleClass(
				"wiki-lens-mode-active",
				tab.id === this.activeTab
			);
		}
	}

	private updateStats(): void {
		if (!this.index.built) {
			this.statsEl?.setText("Building index…");
			return;
		}
		const count = this.activeCandidateCount();
		const nodes = this.backbone?.order ?? 0;
		this.statsEl?.setText(
			`${count} ${this.activeCandidateLabel()} · ${nodes} nodes analyzed`
		);
	}

	private activeCandidateCount(): number {
		if (!this.analysis) return 0;
		if (this.activeTab === "cluster-gaps") {
			return this.analysis.clusterGaps.length;
		}
		if (this.activeTab === "concept-pairs") {
			return this.analysis.conceptPairs.length;
		}
		if (this.activeTab === "predicted-links") {
			return this.analysis.predictedLinks.length;
		}
		return this.analysis.bridges.length;
	}

	private activeCandidateLabel(): string {
		if (this.activeTab === "cluster-gaps") return "cluster pairs";
		if (this.activeTab === "concept-pairs") return "concept pairs";
		if (this.activeTab === "predicted-links") return "predicted links";
		return "bridge candidates";
	}

	private isSelected(tab: GapTab, index: number): boolean {
		return this.selection?.tab === tab && this.selection.index === index;
	}

	private refreshSelectionCaches(): void {
		this.selectedPairNodes = null;
		this.selectedBridgeReferrers = null;

		const link = this.activeLinkSelection();
		if (link) {
			this.selectedPairNodes = new Set([link.a, link.b]);
			return;
		}

		const bridge = this.activeBridgeSelection();
		if (bridge) {
			this.selectedBridgeReferrers = new Set(bridge.referrers);
		}
	}

	private hasActiveSelection(): boolean {
		if (!this.selection || this.selection.tab !== this.activeTab) return false;
		if (!this.analysis) return false;
		return (
			this.selection.index >= 0 &&
			this.selection.index < this.countForTab(this.selection.tab)
		);
	}

	private countForTab(tab: GapTab): number {
		if (!this.analysis) return 0;
		if (tab === "cluster-gaps") return this.analysis.clusterGaps.length;
		if (tab === "concept-pairs") return this.analysis.conceptPairs.length;
		if (tab === "predicted-links") return this.analysis.predictedLinks.length;
		return this.analysis.bridges.length;
	}

	private activeClusterGapSelection(): ClusterGap | null {
		if (
			!this.analysis ||
			!this.selection ||
			this.selection.tab !== "cluster-gaps" ||
			this.activeTab !== "cluster-gaps"
		) {
			return null;
		}
		return this.analysis.clusterGaps[this.selection.index] ?? null;
	}

	private activeLinkSelection(): LinkCandidate | null {
		if (!this.analysis || !this.selection) return null;
		if (
			this.selection.tab === "concept-pairs" &&
			this.activeTab === "concept-pairs"
		) {
			return this.analysis.conceptPairs[this.selection.index] ?? null;
		}
		if (
			this.selection.tab === "predicted-links" &&
			this.activeTab === "predicted-links"
		) {
			return this.analysis.predictedLinks[this.selection.index] ?? null;
		}
		return null;
	}

	private activeBridgeSelection(): BridgeCandidate | null {
		if (
			!this.analysis ||
			!this.selection ||
			this.selection.tab !== "bridges" ||
			this.activeTab !== "bridges"
		) {
			return null;
		}
		return this.analysis.bridges[this.selection.index] ?? null;
	}

	private nodeMatchesSelection(
		node: string,
		data: Record<string, unknown>
	): boolean {
		const clusterGap = this.activeClusterGapSelection();
		if (clusterGap) {
			const community = data["community"];
			return community === clusterGap.a || community === clusterGap.b;
		}

		if (this.selectedPairNodes) return this.selectedPairNodes.has(node);
		if (this.selectedBridgeReferrers) {
			return this.selectedBridgeReferrers.has(node);
		}
		return false;
	}

	protected nodeReducer(
		node: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const res = super.nodeReducer(node, data);
		if (this.hoveredNode !== null || !this.hasActiveSelection()) return res;

		if (this.nodeMatchesSelection(node, data)) {
			res["color"] = data["color"] ?? res["color"];
			if (!this.activeClusterGapSelection()) {
				res["highlighted"] = true;
				res["forceLabel"] = true;
				if (typeof data["label"] === "string") res["label"] = data["label"];
			}
		} else {
			res["color"] = DIM_COLOR;
			res["label"] = "";
			res["forceLabel"] = false;
		}
		return res;
	}

	/**
	 * Mirrors the base hover behavior for selections: edges unrelated to the
	 * selected candidate are hidden, so the dashed overlay and the involved
	 * neighborhoods stand out instead of drowning in the full edge cloud.
	 * Hover (handled by super) takes precedence over selection.
	 */
	protected edgeReducer(
		edge: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const res = super.edgeReducer(edge, data);
		if (this.hoveredNode !== null || !this.hasActiveSelection()) return res;
		const graph = this.graph;
		if (!graph) return res;

		const source = graph.source(edge);
		const target = graph.target(edge);
		const clusterGap = this.activeClusterGapSelection();
		if (clusterGap) {
			// Keep only edges internal to the two selected clusters.
			const cs = graph.getNodeAttribute(source, "community") as number;
			const ct = graph.getNodeAttribute(target, "community") as number;
			const inPair = (c: number) => c === clusterGap.a || c === clusterGap.b;
			if (!inPair(cs) || !inPair(ct)) res["hidden"] = true;
			return res;
		}

		const selected = this.selectedPairNodes ?? this.selectedBridgeReferrers;
		if (selected && !selected.has(source) && !selected.has(target)) {
			res["hidden"] = true;
		}
		return res;
	}

	protected onLayoutFinished(): void {
		this.recomputeClusterCentroids();
		this.redrawOverlay();
	}

	private ensureOverlay(): void {
		const container = this.containerDiv;
		if (!container || this.overlayCanvas) return;

		const canvas = container.createEl("canvas", {
			cls: "wiki-lens-gap-overlay",
		});
		const ctx = canvas.getContext("2d");
		if (!ctx) {
			canvas.remove();
			return;
		}

		this.overlayCanvas = canvas;
		this.overlayCtx = ctx;
		this.overlayResizeObserver = new ResizeObserver(() => {
			this.syncOverlaySize();
			this.redrawOverlay();
		});
		this.overlayResizeObserver.observe(container);
		this.syncOverlaySize();
	}

	private ensureOverlayRenderHook(): void {
		if (!this.sigma || this.overlayRenderSubscribed) return;
		this.sigma.on("afterRender", () => this.redrawOverlay());
		this.overlayRenderSubscribed = true;
	}

	private syncOverlaySize(): void {
		const container = this.containerDiv;
		const canvas = this.overlayCanvas;
		if (!container || !canvas) return;

		const rect = container.getBoundingClientRect();
		const dpr = window.devicePixelRatio || 1;
		const width = Math.max(1, Math.round(rect.width * dpr));
		const height = Math.max(1, Math.round(rect.height * dpr));
		if (canvas.width !== width) canvas.width = width;
		if (canvas.height !== height) canvas.height = height;
		canvas.style.width = `${rect.width}px`;
		canvas.style.height = `${rect.height}px`;
	}

	private clearOverlay(): void {
		const canvas = this.overlayCanvas;
		const ctx = this.overlayCtx;
		if (!canvas || !ctx) return;
		ctx.setTransform(1, 0, 0, 1, 0, 0);
		ctx.clearRect(0, 0, canvas.width, canvas.height);
	}

	private redrawOverlay(): void {
		const canvas = this.overlayCanvas;
		const ctx = this.overlayCtx;
		if (!canvas || !ctx) return;

		this.syncOverlaySize();
		ctx.save();
		ctx.setTransform(1, 0, 0, 1, 0, 0);
		ctx.clearRect(0, 0, canvas.width, canvas.height);

		const dpr = window.devicePixelRatio || 1;
		ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
		if (!this.sigma || !this.graph || !this.analysis) {
			ctx.restore();
			return;
		}

		if (this.activeTab === "cluster-gaps") {
			this.drawClusterGapOverlay(ctx, this.analysis.clusterGaps);
		} else if (this.activeTab === "concept-pairs") {
			this.drawLinkOverlay(ctx, "concept-pairs", this.analysis.conceptPairs);
		} else if (this.activeTab === "predicted-links") {
			this.drawLinkOverlay(
				ctx,
				"predicted-links",
				this.analysis.predictedLinks
			);
		} else {
			const bridge = this.activeBridgeSelection();
			if (bridge) this.drawBridgeOverlay(ctx, bridge);
		}
		ctx.restore();
	}

	private drawClusterGapOverlay(
		ctx: CanvasRenderingContext2D,
		items: ClusterGap[]
	): void {
		for (let i = 0; i < items.length; i++) {
			const gap = items[i];
			const a = this.clusterCentroids.get(gap.a);
			const b = this.clusterCentroids.get(gap.b);
			if (!a || !b) continue;
			this.drawDashedLine(ctx, a, b, this.isSelected("cluster-gaps", i));
		}
	}

	private drawLinkOverlay(
		ctx: CanvasRenderingContext2D,
		tab: "concept-pairs" | "predicted-links",
		items: LinkCandidate[]
	): void {
		for (let i = 0; i < items.length; i++) {
			const item = items[i];
			const a = this.nodeGraphPoint(item.a);
			const b = this.nodeGraphPoint(item.b);
			if (!a || !b) continue;
			this.drawDashedLine(ctx, a, b, this.isSelected(tab, i));
		}
	}

	private drawBridgeOverlay(
		ctx: CanvasRenderingContext2D,
		bridge: BridgeCandidate
	): void {
		const points: Point[] = [];
		for (const referrer of bridge.referrers) {
			const point = this.nodeGraphPoint(referrer);
			if (point) points.push(point);
		}
		if (points.length === 0) return;

		const ghost = meanPoint(points);
		for (const point of points) {
			this.drawDashedLine(ctx, point, ghost, true);
		}

		const view = this.project(ghost);
		if (!view) return;
		ctx.save();
		ctx.setLineDash([6, 4]);
		ctx.strokeStyle = GAP_LINE_BOLD;
		ctx.lineWidth = 2;
		ctx.beginPath();
		ctx.arc(view.x, view.y, 8, 0, Math.PI * 2);
		ctx.stroke();
		ctx.setLineDash([]);
		this.drawBridgeLabel(ctx, view, bridge.target);
		ctx.restore();
	}

	private drawDashedLine(
		ctx: CanvasRenderingContext2D,
		a: Point,
		b: Point,
		selected: boolean
	): void {
		const av = this.project(a);
		const bv = this.project(b);
		if (!av || !bv) return;

		ctx.save();
		ctx.setLineDash([6, 4]);
		ctx.strokeStyle = selected ? GAP_LINE_BOLD : GAP_LINE_FAINT;
		ctx.lineWidth = selected ? 2 : 1;
		ctx.beginPath();
		ctx.moveTo(av.x, av.y);
		ctx.lineTo(bv.x, bv.y);
		ctx.stroke();
		ctx.restore();
	}

	private drawBridgeLabel(
		ctx: CanvasRenderingContext2D,
		view: Point,
		label: string
	): void {
		const text = truncate(label, 44);
		const x = view.x + 12;
		const y = view.y;
		ctx.font = `10px ${this.cssVar("--font-interface", "sans-serif")}`;
		ctx.textBaseline = "middle";
		const width = ctx.measureText(text).width;
		const padX = 4;
		const h = 16;
		ctx.fillStyle = this.themeColors.labelBg;
		ctx.fillRect(x - padX, y - h / 2, width + padX * 2, h);
		ctx.fillStyle = this.themeColors.labelText;
		ctx.fillText(text, x, y);
	}

	private project(point: Point): Point | null {
		if (!this.sigma) return null;
		const view = this.sigma.graphToViewport(point);
		if (!Number.isFinite(view.x) || !Number.isFinite(view.y)) return null;
		return view;
	}

	private nodeGraphPoint(node: string): Point | null {
		const graph = this.graph;
		if (!graph || !graph.hasNode(node)) return null;
		const x = graph.getNodeAttribute(node, "x");
		const y = graph.getNodeAttribute(node, "y");
		if (typeof x !== "number" || typeof y !== "number") return null;
		return { x, y };
	}

	private recomputeClusterCentroids(): void {
		const graph = this.graph;
		this.clusterCentroids.clear();
		if (!graph) return;

		const sums = new Map<number, { x: number; y: number; count: number }>();
		graph.forEachNode((_, attrs) => {
			const community = attrs["community"];
			const x = attrs["x"];
			const y = attrs["y"];
			if (
				typeof community !== "number" ||
				typeof x !== "number" ||
				typeof y !== "number"
			) {
				return;
			}
			const sum = sums.get(community) ?? { x: 0, y: 0, count: 0 };
			sum.x += x;
			sum.y += y;
			sum.count += 1;
			sums.set(community, sum);
		});

		for (const [community, sum] of sums) {
			if (sum.count === 0) continue;
			this.clusterCentroids.set(community, {
				x: sum.x / sum.count,
				y: sum.y / sum.count,
			});
		}
	}
}

function meanPoint(points: Point[]): Point {
	let x = 0;
	let y = 0;
	for (const point of points) {
		x += point.x;
		y += point.y;
	}
	return { x: x / points.length, y: y / points.length };
}

function truncate(text: string, max: number): string {
	if (text.length <= max) return text;
	return text.slice(0, max - 1) + "…";
}
