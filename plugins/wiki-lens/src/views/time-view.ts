import { WorkspaceLeaf } from "obsidian";
import {
	BackboneCache,
	backboneOptsKey,
	BackboneReady,
} from "../core/backbone-cache";
import { ClusterInfo } from "../core/cluster";
import {
	CAMERA_LOD_DEBOUNCE_MS,
	LOD_RATIO_THRESHOLD,
	LodMode,
	applyEdgeSkeleton,
	selectVisibleEdges,
} from "../core/edge-skeleton";
import { DEFAULT_BACKBONE } from "../core/filters";
import {
	applyForceLabels,
	pickForceLabels,
	rankViewportLabels,
} from "../core/force-labels";
import { GraphIndex } from "../core/graph-index";
import { resolveLabelAngles } from "../core/label-collision";
import { parseWikiLog } from "../core/log-parser";
import { nodeSize, nodeWeight } from "../core/metrics";
import {
	COMMUNITY_PALETTE,
	STATUS_COLORS,
	TYPE_COLORS,
} from "../core/palette";
import {
	annotateGraphTime,
	buildTimeline,
	classifyEdge,
	classifyNode,
	countSeries,
	cumulativeCounts,
	dayOf,
	dayToDate,
	selectNewEdges,
	timeEdgeShown,
	TimelineModel,
} from "../core/timeline";
import {
	LogEvent,
	TimeControlsState,
	TimeFilterContext,
	WIKI_PAGE_TYPE_LABELS,
	WIKI_PAGE_TYPES,
	WikiPageType,
} from "../core/types";
import {
	edgeTouchesHiddenType,
	includeTypesFromVisible,
	isNodeTypeVisible,
	TypeFilterStore,
} from "../core/type-visibility";
import { CountChart } from "./count-chart";
import { TimeControls } from "./time-controls";
import { SigmaBaseView } from "./sigma-base";

export const TIME_VIEW_TYPE = "wiki-lens-time";

type ColorMode = "cluster" | "type" | "status";

/**
 * Rendering colors for time classification (constants, not theme variables, since they're passed to sigma).
 * New = warm accent, Updated = cyan-ish, New links = translucent warm.
 */
const NEW_COLOR = "#f5a623";
const UPDATED_COLOR = "#38b6ab";
const NEW_EDGE_COLOR = "rgba(245, 166, 35, 0.55)";
/** Dimmed gray for the diff panel legend's "Existing" swatch (individual node colors use colorDim) */
const LEGEND_EXISTING = "rgba(128, 128, 128, 0.35)";
/** Display cutoff count for each breakdown list */
const LIST_LIMIT = 50;
/** Count of top inbound-instance new nodes that get forceLabel */
const NEW_LABEL_LIMIT = 12;

/**
 * Growth Timeline view. Builds the backbone once and converges it to the final
 * coordinates for the entire period (frozen layout); after that, point-in-time
 * operations are expressed purely by swapping the reducer's time filter. Neither
 * the graph nor the layout is rebuilt until the next index update.
 *
 * Display layers, after dropping the false "All Nodes copy" analogy:
 * - Background: the final-wiki skeleton ∩ time-visible (destination roads).
 * - Foreground: new nodes / new edges of the current window. Replay does
 *   not cap new edges; Diff caps at INTER_CAP and says "shown of total".
 * - Landmarks: one concept-first label per *time-visible* cluster, held
 *   in memory (`timeLabelSet`) so a tick never writes forceLabel.
 * Type checkboxes and cluster-list hover stay. Time classification still
 * wins on color. The reducer, not graph `hidden` attrs, decides accent
 * visibility (`timeEdgeShown`).
 *
 * Why not (design that avoids coupling):
 * - This is kept as a separate view instead of piggybacking on graph-view
 *   (3 modes + louvain cache). The replay's lifeline is the "frozen layout",
 *   and it's simpler to keep it separate than to arbitrate re-layout, search,
 *   and mode switching against the existing view.
 * - Point-in-time operations never move x/y or rerun ForceAtlas2. Far
 *   ticks only swap ctx + derived sets then refreshDisplay (partialGraph
 *   + skipIndexation). A skipIndexation-only refresh is a full refresh
 *   in sigma v3 and calls process(). Zoomed ticks rewrite labelAngle
 *   only when paused and the labeled set actually changes.
 * - After ForceAtlas2 is on screen, rebuild must not swap in a cache
 *   copy that is still on the circular seed. That is the U-shaped
 *   collapse during Replay.
 */
export class TimeView extends SigmaBaseView {
	private index: GraphIndex;
	private cache: BackboneCache;
	private typeStore: TypeFilterStore;
	private unsubReady: (() => void) | null = null;
	private controls!: TimeControls;
	private countChart!: CountChart;
	private containerDiv: HTMLElement | null = null;
	private panelEl: HTMLElement | null = null;
	private typeFilterInputs: { type: WikiPageType; cb: HTMLInputElement }[] =
		[];
	private visibleTypes: Set<WikiPageType>;
	/** Same three modes as All Nodes. Default cluster so the list swatches match. */
	private colorMode: ColorMode = "cluster";

	private model: TimelineModel | null = null;
	private cumCounts: { nodes: number[]; edges: number[] } | null = null;
	private clusters: ClusterInfo[] = [];
	/** Time context the reducer reads every frame. null means uninitialized (no time data), passthrough */
	private ctx: TimeFilterContext | null = null;
	private state: TimeControlsState | null = null;
	/** forceLabel targets: "new" nodes with top inbound instances. Recomputed only when ctx changes */
	private newLabelSet: Set<string> = new Set();
	/** Time-visible cluster landmarks. In-memory; ticks do not write forceLabel. */
	private timeLabelSet: Set<string> = new Set();
	/** New edges that bypass the skeleton. Replay: uncapped. Diff: INTER_CAP. */
	private newEdgeSet: Set<string> = new Set();
	private newEdgeTotal = 0;
	private visibleCounts = { nodes: 0, edges: 0, skeleton: 0 };
	private clusterVisible = new Map<number, number>();
	private clusterSizeEls = new Map<number, HTMLElement>();
	/** Last labeled-set fingerprint; zoomed ticks skip collision when unchanged. */
	private labeledKey: string | null = null;
	/** True after ForceAtlas2 coordinates have been shown. Blocks circular swaps. */
	private frozenLayout = false;
	/** Index/cache rebuild is running; keep the frozen graph on screen. */
	private waitingForLayout = false;

	private events: LogEvent[] = [];
	private eventsByDay: Map<number, LogEvent[]> = new Map();
	private logFailedCount = 0;
	/** Generation counter for async log loading. Incremented on every rebuild; stale results are discarded */
	private rebuildGen = 0;

	/** Key of the (aIndex, bIndex) pair the diff panel was last built for. null means it needs rebuilding */
	private diffPanelKey: string | null = null;
	private panelKind: "clusters" | "diff" | null = null;
	private diffCounts = { newNodes: 0, updatedNodes: 0, newEdges: 0 };

	/** Set of nodes targeted for event highlighting (null means no highlight) */
	private eventHighlight: Set<string> | null = null;
	private activeEventRow: HTMLElement | null = null;
	/** Cluster list hover. Null when the pointer is on the graph or off the panel. */
	private hoveredCluster: number | null = null;

	private visibleEdges: Set<string> = new Set();
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
		return TIME_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Wiki Lens: Growth Timeline";
	}

	getIcon(): string {
		return "history";
	}

	async onOpen(): Promise<void> {
		const el = this.contentEl;
		el.empty();
		el.addClass("wiki-lens-graph");

		const controlsParent = el.createDiv();
		this.controls = new TimeControls(controlsParent, {
			onChange: (s) => this.onControlsChange(s),
		});

		this.buildTypeFilterBar(el);

		this.countChart = new CountChart(el, {
			onSeek: (index) => this.controls.seekTo(index),
			onExcludeSeedChange: () => this.refreshCountChart(),
		});

		const body = el.createDiv({ cls: "wiki-lens-graph-body" });
		this.containerDiv = body.createDiv({ cls: "wiki-lens-graph-container" });
		// Replay shows the cluster list; Diff reuses the same panel for the breakdown.
		this.panelEl = body.createDiv({ cls: "wiki-lens-cluster-panel" });

		this.registerEvent(this.index.on("updated", () => this.rebuild()));
		this.unsubReady = this.cache.onReady((ready) => this.onCacheReady(ready));
		this.rebuild();
	}

	async onClose(): Promise<void> {
		this.unbindCameraLod();
		this.unsubReady?.();
		this.unsubReady = null;
		this.countChart?.destroy();
		this.controls?.destroy();
		await super.onClose();
	}

	onResize(): void {
		super.onResize();
		if (!this.controls?.isPlaying()) this.refreshLabelCollision();
	}

	protected onSigmaContainerResize(): void {
		if (!this.controls?.isPlaying()) this.refreshLabelCollision();
	}

	/** Call only on index update, never on point-in-time operations (preserves the frozen layout) */
	private rebuild(): void {
		if (!this.containerDiv) return;
		if (!this.index.built) {
			this.controls.setStatus("Building index…");
			return;
		}
		this.stopLayout();
		const preserve = this.state !== null;

		const handle = this.cache.acquire(this.index, DEFAULT_BACKBONE);
		if (!handle) {
			this.controls.setStatus("Building index…");
			return;
		}
		if (!handle.laidOut && this.frozenLayout && this.graph && this.sigma) {
			this.waitingForLayout = true;
			this.countChart?.setSeedLocked(true);
			return;
		}
		const gen = ++this.rebuildGen;
		const graph = handle.graph;
		this.clusters = handle.clusters;
		graph.updateEachNodeAttributes((_node, attrs) => {
			attrs["size"] = nodeSize(nodeWeight(attrs));
			return attrs;
		});
		this.applyBaseColors(graph);

		const model = buildTimeline(this.index);
		annotateGraphTime(graph, model);
		this.model = model;
		this.cumCounts = cumulativeCounts(graph, model.days);

		this.events = [];
		this.eventsByDay = new Map();
		this.logFailedCount = 0;
		this.eventHighlight = null;
		this.activeEventRow = null;
		this.diffPanelKey = null;
		this.panelKind = null;
		this.newLabelSet = new Set();
		this.timeLabelSet = new Set();
		this.newEdgeSet = new Set();
		this.newEdgeTotal = 0;
		this.clusterVisible = new Map();
		this.clusterSizeEls = new Map();
		this.labeledKey = null;
		this.hoveredCluster = null;
		this.controls.setEventDays(new Set());

		this.applyLod(this.lodModeFromCamera(), graph);
		this.setSigmaGraph(graph, this.containerDiv, null);
		this.applyInspectorSettings();
		this.bindCameraLod();
		if (handle.laidOut) {
			this.frozenLayout = true;
			this.waitingForLayout = false;
			this.pinFrozenBBox();
		}
		this.controls.setDays(model.days, dayToDate, preserve);
		this.controls.setEnabled(handle.laidOut);
		this.countChart?.setSeedLocked(false);
		this.refreshCountChart();
		this.refreshLabelCollision();

		void this.loadLog(gen);
	}

	private onCacheReady(ready: BackboneReady): void {
		if (!this.index.stats) return;
		if (ready.builtAt !== this.index.stats.builtAt) return;
		if (ready.optsKey !== backboneOptsKey(DEFAULT_BACKBONE)) return;
		if (this.waitingForLayout || !this.graph) {
			this.waitingForLayout = false;
			this.rebuild();
			return;
		}
		this.cache.applyPositions(DEFAULT_BACKBONE, this.graph);
		this.frozenLayout = true;
		this.pinFrozenBBox();
		this.controls?.setEnabled(true);
		this.refreshLabelCollision();
	}

	private async loadLog(gen: number): Promise<void> {
		const result = await parseWikiLog(this.app, this.index);
		// Discard stale parse results that arrive during consecutive rebuilds
		if (gen !== this.rebuildGen) return;

		this.events = result.events;
		this.logFailedCount = result.failedCount;

		const eventDays = new Set<number>();
		const byDay = new Map<number, LogEvent[]>();
		for (const e of result.events) {
			const d = dayOf(e.date);
			if (d === null) continue;
			eventDays.add(d);
			const list = byDay.get(d);
			if (list) list.push(e);
			else byDay.set(d, [e]);
		}
		this.eventsByDay = byDay;
		this.controls.setEventDays(eventDays);

		// Reflect the loaded events into the status line and diff panel
		this.diffPanelKey = null;
		if (this.state) this.onControlsChange(this.state);
	}

	// --- Translate control state changes into the time filter ---

	private onControlsChange(state: TimeControlsState): void {
		this.state = state;
		this.countChart?.setCursor(state);
		const model = this.model;
		const panel = this.panelEl;

		if (!model || model.days.length === 0) {
			// With no time data, skip filtering and show everything
			this.ctx = null;
			this.newLabelSet = new Set();
			this.timeLabelSet = new Set();
			this.newEdgeSet = new Set();
			this.newEdgeTotal = 0;
			this.clusterVisible = new Map();
			if (panel) this.renderClusterPanel();
			this.controls.setStatus("No time data");
			this.refreshDisplay();
			return;
		}

		if (state.mode === "replay") {
			this.ctx = {
				mode: "replay",
				t: model.days[state.tIndex],
				recentWindow: state.recentWindowDays,
			};
			this.eventHighlight = null;
			this.activeEventRow = null;
			this.renderClusterPanel();
			this.refreshTimeDerived();
			this.updateReplayStatus(state);
		} else {
			this.ctx = {
				mode: "diff",
				a: model.days[state.aIndex],
				b: model.days[state.bIndex],
			};
			this.hoveredCluster = null;
			this.refreshTimeDerived();
			this.updateDiffPanelIfNeeded(state);
			this.updateDiffStatus(state);
		}

		this.refreshAfterTimeChange();
	}

	/**
	 * Recomputes per-tick derived sets (new labels, time-visible landmarks,
	 * accent edges, visible counts). Does not write graph attributes.
	 */
	private refreshTimeDerived(): void {
		const graph = this.graph;
		const ctx = this.ctx;
		this.newLabelSet = new Set();
		this.timeLabelSet = new Set();
		this.newEdgeSet = new Set();
		this.newEdgeTotal = 0;
		this.visibleCounts = { nodes: 0, edges: 0, skeleton: 0 };
		this.clusterVisible = new Map();
		if (!graph || !ctx) return;

		const includeTypes = this.includeTypesOpt();
		const news: { node: string; weight: number }[] = [];
		graph.forEachNode((node, attrs) => {
			if (!isNodeTypeVisible(attrs["nodeType"], includeTypes)) return;
			const cls = classifyNode(
				attrs as { bornDay: number; updatedDay: number },
				ctx
			);
			if (cls === "hidden") return;
			this.visibleCounts.nodes++;
			const community = (attrs["community"] as number) ?? 0;
			this.clusterVisible.set(
				community,
				(this.clusterVisible.get(community) ?? 0) + 1
			);
			if (cls === "new") news.push({ node, weight: nodeWeight(attrs) });
		});
		news.sort((a, b) => b.weight - a.weight);
		const limit = Math.min(NEW_LABEL_LIMIT, news.length);
		for (let i = 0; i < limit; i++) this.newLabelSet.add(news[i].node);

		this.timeLabelSet = new Set(
			pickForceLabels(graph, this.clusters, {
				includeTypes,
				includeNode: (_node, attrs) =>
					classifyNode(
						attrs as { bornDay: number; updatedDay: number },
						ctx
					) !== "hidden",
			})
		);

		this.newEdgeSet = selectNewEdges(graph, ctx, {
			includeTypes,
			limit: ctx.mode === "replay" ? null : undefined,
		});
		graph.forEachEdge((edge, attrs) => {
			if (edgeTouchesHiddenType(graph, edge, includeTypes)) return;
			const cls = classifyEdge(attrs as { bornDay: number }, ctx);
			if (cls === "hidden") return;
			this.visibleCounts.edges++;
			if (cls === "new") this.newEdgeTotal++;
			if (this.visibleEdges.has(edge)) this.visibleCounts.skeleton++;
		});

		if (this.hoveredNode && !this.acceptHover(this.hoveredNode)) {
			this.clearHover();
		}
		this.syncClusterSizes();
	}

	/** Time ticks never reprocess x/y. Labels may overlap while playing. */
	private refreshAfterTimeChange(): void {
		if (this.lodMode === "zoomed" && !this.controls?.isPlaying()) {
			this.syncZoomLabels();
			const key = this.labeledFingerprint();
			if (key !== this.labeledKey) {
				this.labeledKey = key;
				this.refreshLabelCollision();
				return;
			}
		}
		this.refreshDisplay();
	}

	private labeledFingerprint(): string {
		const parts = [
			...this.timeLabelSet,
			...this.newLabelSet,
			...this.zoomLabelSet,
		];
		parts.sort();
		return parts.join("\0");
	}

	private updateReplayStatus(state: TimeControlsState): void {
		const model = this.model;
		const cum = this.cumCounts;
		if (!model || !cum) return;
		const day = model.days[state.tIndex];
		const includeTypes = this.includeTypesOpt();
		const v = this.visibleCounts;
		let counts = includeTypes
			? `Nodes ${fmt(v.nodes)} of ${fmt(cum.nodes[state.tIndex])} / Edges ${fmt(v.edges)} of ${fmt(cum.edges[state.tIndex])}`
			: `Nodes ${fmt(cum.nodes[state.tIndex])} / Edges ${fmt(cum.edges[state.tIndex])}`;
		counts += ` (skeleton ${fmt(v.skeleton)})`;
		if (this.newEdgeTotal > 0) {
			counts += ` / new links ${fmt(this.newEdgeSet.size)}`;
		}
		let text = `${dayToDate(day)} | ${counts}`;
		const evs = this.eventsByDay.get(day);
		if (evs && evs.length > 0) {
			text += ` | ${evs[0].op}: ${evs[0].title}`;
			if (evs.length > 1) text += ` and ${evs.length - 1} more`;
		}
		this.controls.setStatus(text);
	}

	private updateDiffStatus(state: TimeControlsState): void {
		const model = this.model;
		if (!model) return;
		const aStr = dayToDate(model.days[state.aIndex]);
		const bStr = dayToDate(model.days[state.bIndex]);
		const c = this.diffCounts;
		const includeTypes = this.includeTypesOpt();
		const newLinks =
			this.newEdgeSet.size < c.newEdges
				? `New links ${fmt(this.newEdgeSet.size)} of ${fmt(c.newEdges)}`
				: `New links ${fmt(c.newEdges)}`;
		let extra = ` (skeleton ${fmt(this.visibleCounts.skeleton)})`;
		if (includeTypes && this.graph) {
			extra = ` | showing ${fmt(this.visibleCounts.nodes)} of ${fmt(this.graph.order)}${extra}`;
		}
		this.controls.setStatus(
			`${aStr} → ${bStr} | New ${fmt(c.newNodes)} / Updated ${fmt(c.updatedNodes)} / ${newLinks}${extra}`
		);
	}

	// --- Type filter (same store / contract as All Nodes) ---

	private buildTypeFilterBar(el: HTMLElement): void {
		const bar = el.createDiv({ cls: "wiki-lens-toolbar" });
		const wrap = bar.createDiv({ cls: "wiki-lens-type-filters" });
		wrap.setAttr("role", "group");
		wrap.setAttr("aria-label", "Node types");
		wrap.createSpan({
			cls: "wiki-lens-muted",
			text: "Types",
		});
		this.typeFilterInputs = [];
		for (const type of WIKI_PAGE_TYPES) {
			const label = wrap.createEl("label", {
				cls: "wiki-lens-toggle",
			});
			const cb = label.createEl("input", { type: "checkbox" });
			cb.checked = this.visibleTypes.has(type);
			const swatch = label.createSpan({ cls: "wiki-lens-layer-swatch" });
			swatch.style.backgroundColor = TYPE_COLORS[type];
			label.createSpan({ text: WIKI_PAGE_TYPE_LABELS[type] });
			cb.onchange = () => this.setTypeVisible(type, cb.checked);
			this.typeFilterInputs.push({ type, cb });
		}
		this.syncTypeFilterInputs();

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
			this.applyBaseColors();
			this.refreshDisplay();
		};
	}

	private baseColorOf(attrs: Record<string, unknown>): string {
		if (this.colorMode === "cluster") {
			return COMMUNITY_PALETTE[
				((attrs["community"] as number) ?? 0) % COMMUNITY_PALETTE.length
			];
		}
		if (this.colorMode === "type") {
			return TYPE_COLORS[attrs["nodeType"] as string] ?? "#888888";
		}
		return STATUS_COLORS[attrs["status"] as string] ?? "#888888";
	}

	/** Presentation only. Does not touch layout, LOD, or the time context. */
	private applyBaseColors(graph = this.graph): void {
		if (!graph) return;
		graph.updateEachNodeAttributes(
			(_, attrs) => {
				const base = this.baseColorOf(attrs);
				attrs["colorBase"] = base;
				attrs["colorDim"] = dimColor(base);
				attrs["color"] = base;
				return attrs;
			},
			{ attributes: ["colorBase", "colorDim", "color"] }
		);
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
		this.diffPanelKey = null;
		this.panelKind = null;
		if (this.hoveredNode && this.graph) {
			const hoveredType = this.graph.getNodeAttribute(
				this.hoveredNode,
				"nodeType"
			);
			if (!isNodeTypeVisible(hoveredType, this.includeTypesOpt())) {
				this.clearHover();
			}
		}
		if (!this.graph) return;
		this.applyLod(this.lodModeFromCamera(), this.graph);
		if (this.state) this.onControlsChange(this.state);
		else this.refreshLabelCollision();
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

	private refreshCountChart(): void {
		if (!this.countChart) return;
		if (this.waitingForLayout) return;
		const model = this.model;
		if (!model || model.days.length === 0) {
			this.countChart.setData(null, model);
			this.countChart.setCursor(this.state);
			return;
		}
		this.countChart.setData(
			countSeries(this.index, model, {
				excludeSeedEntities: this.countChart.excludeSeedEntities(),
			}),
			model
		);
		this.countChart.setCursor(this.state);
	}

	// --- Diff / cluster panel ---

	private updateDiffPanelIfNeeded(state: TimeControlsState): void {
		const key = `${state.aIndex},${state.bIndex}`;
		if (this.diffPanelKey === key && this.panelKind === "diff") return;
		this.diffPanelKey = key;
		this.buildDiffPanel(state);
	}

	private buildDiffPanel(state: TimeControlsState): void {
		const panel = this.panelEl;
		const graph = this.graph;
		const model = this.model;
		if (!panel || !graph || !model) return;

		panel.empty();
		this.panelKind = "diff";
		this.eventHighlight = null;
		this.activeEventRow = null;

		const ctx: TimeFilterContext = {
			mode: "diff",
			a: model.days[state.aIndex],
			b: model.days[state.bIndex],
		};
		const includeTypes = this.includeTypesOpt();

		const newNodes: string[] = [];
		const updatedNodes: string[] = [];
		graph.forEachNode((node, attrs) => {
			if (!isNodeTypeVisible(attrs["nodeType"], includeTypes)) return;
			const cls = classifyNode(
				attrs as { bornDay: number; updatedDay: number },
				ctx
			);
			if (cls === "new") newNodes.push(node);
			else if (cls === "updated") updatedNodes.push(node);
		});
		let newEdges = 0;
		graph.forEachEdge((edge, attrs) => {
			if (edgeTouchesHiddenType(graph, edge, includeTypes)) return;
			if (classifyEdge(attrs as { bornDay: number }, ctx) === "new") {
				newEdges++;
			}
		});
		this.diffCounts = {
			newNodes: newNodes.length,
			updatedNodes: updatedNodes.length,
			newEdges,
		};

		const legend = panel.createDiv({ cls: "wiki-lens-legend" });
		addLegend(legend, NEW_COLOR, "New");
		addLegend(legend, UPDATED_COLOR, "Updated");
		addLegend(legend, LEGEND_EXISTING, "Existing");
		addLegend(legend, NEW_EDGE_COLOR, "New links");

		const summary = panel.createDiv({ cls: "wiki-lens-diff-summary" });
		const shown = this.newEdgeSet.size;
		const linkText =
			shown < newEdges
				? `New links ${fmt(shown)} of ${fmt(newEdges)} (estimated)`
				: `New links ${fmt(newEdges)} (estimated)`;
		summary.createSpan({
			text: `New ${fmt(newNodes.length)} pages / Updated ${fmt(updatedNodes.length)} pages / ${linkText}`,
		});

		this.renderPathGroup(panel, "New pages", newNodes);
		this.renderPathGroup(panel, "Updated pages", updatedNodes);
		this.renderEventGroup(panel, ctx);

		const note = panel.createDiv({ cls: "wiki-lens-muted" });
		let noteText = "Link timestamps are estimated from the creation dates of both endpoint pages";
		const parts: string[] = [];
		if (model.unknownCreatedCount > 0) {
			parts.push(`${fmt(model.unknownCreatedCount)} with unknown creation date`);
		}
		if (this.logFailedCount > 0) {
			parts.push(`${fmt(this.logFailedCount)} unparseable log entries`);
		}
		if (parts.length > 0) noteText += " / " + parts.join(" / ");
		note.setText(noteText);
	}

	private renderClusterPanel(): void {
		const panel = this.panelEl;
		if (!panel) return;
		if (this.panelKind === "clusters") return;
		panel.empty();
		this.panelKind = "clusters";
		this.diffPanelKey = null;
		this.clusterSizeEls = new Map();

		panel.createDiv({
			cls: "wiki-lens-cluster-panel-title",
			text: "Cluster list",
		});

		for (const c of this.clusters) {
			const item = panel.createDiv({ cls: "wiki-lens-cluster-item" });
			const header = item.createDiv({ cls: "wiki-lens-cluster-header" });
			const swatch = header.createSpan({ cls: "wiki-lens-cluster-swatch" });
			swatch.style.backgroundColor =
				COMMUNITY_PALETTE[c.id % COMMUNITY_PALETTE.length];
			header.createSpan({
				cls: "wiki-lens-cluster-name",
				text: c.label,
			});
			const sizeEl = header.createSpan({
				cls: "wiki-lens-cluster-size wiki-lens-muted",
				text: this.clusterSizeText(c.id, c.size),
			});
			this.clusterSizeEls.set(c.id, sizeEl);
			header.onclick = () => this.focusCluster(c.id);
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

	private clusterSizeText(id: number, total: number): string {
		const visible = this.clusterVisible.get(id) ?? 0;
		return visible === total ? `${total}` : `${visible} / ${total}`;
	}

	private syncClusterSizes(): void {
		for (const c of this.clusters) {
			const el = this.clusterSizeEls.get(c.id);
			if (el) el.setText(this.clusterSizeText(c.id, c.size));
		}
	}

	private renderPathGroup(
		panel: HTMLElement,
		title: string,
		paths: string[]
	): void {
		if (paths.length === 0) return;
		panel.createDiv({
			cls: "wiki-lens-diff-group",
			text: `${title}(${fmt(paths.length)})`,
		});
		for (const p of paths.slice(0, LIST_LIMIT)) {
			const row = panel.createDiv({
				cls: "wiki-lens-diff-item",
				text: this.titleOf(p),
			});
			row.onclick = () =>
				void this.app.workspace.openLinkText(p, "/", false);
		}
		if (paths.length > LIST_LIMIT) {
			panel.createDiv({
				cls: "wiki-lens-muted",
				text: `…and ${fmt(paths.length - LIST_LIMIT)} more`,
			});
		}
	}

	private renderEventGroup(
		panel: HTMLElement,
		ctx: TimeFilterContext
	): void {
		if (ctx.mode !== "diff") return;
		const evs = this.events.filter((e) => {
			const d = dayOf(e.date);
			return d !== null && d > ctx.a && d <= ctx.b;
		});
		if (evs.length === 0) return;

		panel.createDiv({
			cls: "wiki-lens-diff-group",
			text: `Operation log (${fmt(evs.length)})`,
		});
		for (const e of evs) {
			const row = panel.createDiv({
				cls: "wiki-lens-diff-item",
				text: `[${e.date}] ${e.op} | ${e.title}`,
			});
			row.onclick = () => this.toggleEventHighlight(e, row);
		}
	}

	private toggleEventHighlight(event: LogEvent, row: HTMLElement): void {
		const graph = this.graph;
		if (!graph) return;

		if (this.activeEventRow === row) {
			this.clearEventHighlight();
			this.refreshDisplay();
			return;
		}

		const targets = new Set<string>();
		for (const p of event.createdPaths) if (graph.hasNode(p)) targets.add(p);
		for (const p of event.updatedPaths) if (graph.hasNode(p)) targets.add(p);

		if (this.activeEventRow) {
			this.setRowActive(this.activeEventRow, false);
		}
		this.eventHighlight = targets;
		this.activeEventRow = row;
		this.setRowActive(row, true);
		this.refreshDisplay();
	}

	private clearEventHighlight(): void {
		if (this.activeEventRow) this.setRowActive(this.activeEventRow, false);
		this.eventHighlight = null;
		this.activeEventRow = null;
	}

	/**
	 * Toggles the selected row's appearance. Since .wiki-lens-cluster-active's
	 * background color only applies inside .wiki-lens-cluster-item, diff rows
	 * visualize the selected state by applying the same CSS variable inline
	 * (styles.css is intentionally left unchanged).
	 */
	private setRowActive(row: HTMLElement, active: boolean): void {
		row.toggleClass("wiki-lens-cluster-active", active);
		row.style.background = active ? "var(--background-secondary)" : "";
	}

	private titleOf(path: string): string {
		if (this.graph?.hasNode(path)) {
			const l = this.graph.getNodeAttribute(path, "label");
			if (typeof l === "string" && l) return l;
		}
		return this.index.nodes.get(path)?.title ?? path;
	}

	// --- LOD / labels / camera (All Nodes inspector contract) ---

	private applyInspectorSettings(): void {
		if (!this.sigma) return;
		this.sigma.setSetting("stagePadding", 30);
		this.sigma.setSetting("labelSize", 11);
		this.sigma.setSetting("labelRenderedSizeThreshold", 7);
		this.sigma.setSetting("labelDensity", 1);
		this.sigma.setSetting("minEdgeThickness", 0.4);
	}

	private applyLod(mode: LodMode, graph = this.graph): void {
		if (!graph) return;
		const includeTypes = this.includeTypesOpt();
		applyForceLabels(
			graph,
			pickForceLabels(graph, this.clusters, { includeTypes })
		);
		const visible = selectVisibleEdges(graph, mode, { includeTypes });
		applyEdgeSkeleton(graph, visible);
		this.visibleEdges = visible;
		this.lodMode = mode;
	}

	private lodModeFromCamera(): LodMode {
		if (this.sigma) {
			return this.sigma.getCamera().ratio < LOD_RATIO_THRESHOLD
				? "zoomed"
				: "far";
		}
		return this.lodMode;
	}

	private refreshLabelCollision(): void {
		if (!this.sigma || !this.graph) return;
		this.syncZoomLabels();
		const labeled = new Set<string>(this.newLabelSet);
		for (const node of this.timeLabelSet) labeled.add(node);
		for (const node of this.zoomLabelSet) labeled.add(node);
		this.graph.forEachNode((node, attrs) => {
			if (attrs["forceLabel"] === true && !this.nodeTimeHidden(attrs)) {
				labeled.add(node);
			}
		});
		const sigma = this.sigma;
		resolveLabelAngles(this.graph, labeled, (x, y) =>
			sigma.graphToViewport({ x, y })
		);
		this.labeledKey = this.labeledFingerprint();
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
		const includeTypes = this.includeTypesOpt();
		const ctx = this.ctx;
		const candidates: { path: string; inCount: number }[] = [];
		this.graph.forEachNode((node, attrs) => {
			if (!isNodeTypeVisible(attrs["nodeType"], includeTypes)) return;
			if (
				ctx &&
				classifyNode(
					attrs as { bornDay: number; updatedDay: number },
					ctx
				) === "hidden"
			) {
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
		if (!this.graph || !this.sigma) return;
		const next: LodMode =
			this.sigma.getCamera().ratio < LOD_RATIO_THRESHOLD
				? "zoomed"
				: "far";
		const crossed = next !== this.lodMode;
		if (crossed) this.applyLod(next);
		if (crossed || next === "zoomed") this.refreshLabelCollision();
	}

	private clearHover(): void {
		this.hoveredNode = null;
		this.hoveredNeighbors = null;
		this.hoveredCluster = null;
	}

	private clusterHoverActive(): boolean {
		return this.hoveredCluster !== null && this.hoveredNode === null;
	}

	private setHoveredCluster(id: number | null): void {
		if (this.hoveredCluster === id) return;
		this.hoveredCluster = id;
		this.refreshDisplay();
	}

	private focusCluster(community: number): void {
		if (!this.graph) return;
		let landmark: string | null = null;
		let fallback: string | null = null;
		this.graph.forEachNode((node, attrs) => {
			if ((attrs["community"] as number) !== community) return;
			if (!this.acceptHover(node)) return;
			if (!fallback) fallback = node;
			if (!landmark && this.timeLabelSet.has(node)) landmark = node;
		});
		this.focusNode(landmark ?? fallback);
	}

	private focusNode(node: string | null): void {
		if (!node || !this.graph || !this.sigma) return;
		if (!this.acceptHover(node)) return;
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

	private nodeTimeHidden(data: Record<string, unknown>): boolean {
		const ctx = this.ctx;
		if (!ctx) return false;
		return (
			classifyNode(
				data as { bornDay: number; updatedDay: number },
				ctx
			) === "hidden"
		);
	}

	protected acceptHover(node: string): boolean {
		if (!this.graph) return true;
		const attrs = this.graph.getNodeAttributes(node);
		if (!isNodeTypeVisible(attrs["nodeType"], this.includeTypesOpt())) {
			return false;
		}
		return !this.nodeTimeHidden(attrs);
	}

	protected onNodeClick(node: string): void {
		if (!this.acceptHover(node)) return;
		super.onNodeClick(node);
	}

	// --- reducer (time → type → event/cluster → base hover) ---

	protected nodeReducer(
		node: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		if (!isNodeTypeVisible(data["nodeType"], this.includeTypesOpt())) {
			return { ...data, hidden: true, label: "" };
		}

		const ctx = this.ctx;
		if (!ctx) return super.nodeReducer(node, data);

		const cls = classifyNode(
			data as { bornDay: number; updatedDay: number },
			ctx
		);
		if (cls === "hidden") return { ...data, hidden: true, label: "" };

		const adjusted: Record<string, unknown> = { ...data };
		if (this.timeLabelSet.has(node) || this.newLabelSet.has(node)) {
			adjusted["forceLabel"] = true;
		}
		if (cls === "new") {
			adjusted["color"] = NEW_COLOR;
			adjusted["size"] = ((data["size"] as number) ?? 0) * 1.4;
		} else if (cls === "updated") {
			adjusted["color"] = UPDATED_COLOR;
		} else {
			adjusted["color"] = data["colorDim"];
		}

		if (this.eventHighlight) {
			if (this.eventHighlight.has(node)) {
				adjusted["highlighted"] = true;
				adjusted["forceLabel"] = true;
			} else {
				adjusted["color"] = data["colorDim"];
				adjusted["label"] = "";
				adjusted["forceLabel"] = false;
			}
		} else if (this.clusterHoverActive()) {
			if (data["community"] !== this.hoveredCluster) {
				adjusted["color"] = "rgba(90, 90, 90, 0.25)";
				adjusted["label"] = "";
				adjusted["forceLabel"] = false;
				return super.nodeReducer(node, adjusted);
			}
			adjusted["highlighted"] = true;
		}

		const res = super.nodeReducer(node, adjusted);
		if (this.eventHighlight && !this.eventHighlight.has(node)) {
			res["label"] = "";
			return res;
		}
		const keep =
			this.hoveredNode === node ||
			this.hoveredNeighbors?.has(node) === true ||
			res["forceLabel"] === true ||
			res["highlighted"] === true ||
			this.newLabelSet.has(node) ||
			this.timeLabelSet.has(node) ||
			this.zoomLabelSet.has(node);
		if (keep) {
			if (!res["label"] && typeof data["label"] === "string") {
				res["label"] = data["label"];
			}
		} else {
			res["label"] = "";
		}
		return res;
	}

	protected edgeReducer(
		edge: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const ctx = this.ctx;
		if (
			this.graph &&
			edgeTouchesHiddenType(this.graph, edge, this.includeTypesOpt())
		) {
			return { ...data, hidden: true };
		}
		if (!ctx) return super.edgeReducer(edge, data);

		const cls = classifyEdge(data as { bornDay: number }, ctx);
		if (cls === "hidden") return { ...data, hidden: true };

		const adjusted: Record<string, unknown> = { ...data };
		if (cls === "new") adjusted["color"] = NEW_EDGE_COLOR;

		const res = super.edgeReducer(edge, adjusted);
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
				res["color"] =
					cls === "new" ? NEW_EDGE_COLOR : "rgba(200, 160, 60, 0.45)";
			} else {
				res["hidden"] = true;
			}
			return res;
		}
		res["hidden"] = !timeEdgeShown(
			cls,
			edge,
			this.visibleEdges,
			this.newEdgeSet
		);
		return res;
	}
}

/** Formats a number with thousands separators */
function fmt(n: number): string {
	return n.toLocaleString();
}

/** Converts "#rrggbb" into an rgba with alpha 0.25 (dim color for existing nodes) */
function dimColor(hex: string): string {
	const r = parseInt(hex.slice(1, 3), 16);
	const g = parseInt(hex.slice(3, 5), 16);
	const b = parseInt(hex.slice(5, 7), 16);
	return `rgba(${r}, ${g}, ${b}, 0.25)`;
}

/** Adds one round swatch + label entry to the legend */
function addLegend(parent: HTMLElement, color: string, label: string): void {
	const item = parent.createSpan();
	const swatch = item.createSpan({ cls: "wiki-lens-legend-swatch" });
	swatch.style.backgroundColor = color;
	item.createSpan({ text: label });
}
