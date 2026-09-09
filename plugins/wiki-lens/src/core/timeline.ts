import Graph from "graphology";
import { INTER_CAP } from "./edge-skeleton";
import { GraphIndex } from "./graph-index";
import {
	TimeClass,
	TimeFilterContext,
	WIKI_PAGE_TYPES,
	WikiPageType,
} from "./types";
import { edgeTouchesHiddenType } from "./type-visibility";

/**
 * Derived model for the timeline features (Growth Timeline / two-point Diff).
 * Built as a pure function of frontmatter's created / updated, without
 * polluting GraphIndex (the single source of truth for structure) — a derived
 * layer on par with filters / metrics / cluster.
 *
 * Why not (design constraints):
 * - Edge time is approximated as the max of its two endpoint nodes' creation
 *   dates. This is because it guarantees monotonicity structurally with zero
 *   extra IO (the visible graph at time t = the induced subgraph of nodes born
 *   by t; edges never appear or disappear on their own). The direction of the
 *   error is toward "making appearance earlier" — a link added later between
 *   two existing pages can be shown as older than it really is. This is kept
 *   permanently as an info note in the UI to preserve honesty. Git history and
 *   log.md are not treated as the source of truth for edge time.
 * - classifyNode / classifyEdge are hot paths called every frame from the
 *   reducer, so they're judged by numeric comparison of attributes alone,
 *   without adding allocations.
 */

const MS_PER_DAY = 86_400_000;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export interface TimelineModel {
	/** Active days (unique days from born ∪ updated), ascending. Epoch day count (UTC) */
	days: number[];
	minDay: number; // days[0]
	maxDay: number; // days[days.length - 1]
	/** path -> bornDay. If created is invalid/missing, minDay - 1 (treated as present from the start) */
	bornDayOf: Map<string, number>;
	/** path -> updatedDay. If updated is invalid/missing, same value as bornDay */
	updatedDayOf: Map<string, number>;
	/** Number of nodes whose created couldn't be parsed (used for the UI's "n unknown time" display) */
	unknownCreatedCount: number;
}

/** "YYYY-MM-DD" (valid if the first 10 characters match this format) -> epoch day count. null if unparseable */
export function dayOf(dateStr: string): number | null {
	if (!dateStr) return null;
	const head = dateStr.slice(0, 10);
	if (!DAY_RE.test(head)) return null;
	const ms = Date.parse(head + "T00:00:00Z");
	if (Number.isNaN(ms)) return null;
	return Math.floor(ms / MS_PER_DAY);
}

/** Epoch day count -> "YYYY-MM-DD" */
export function dayToDate(day: number): string {
	return new Date(day * MS_PER_DAY).toISOString().slice(0, 10);
}

/** Build a timeline model from a GraphIndex (pure function, no side effects) */
export function buildTimeline(index: GraphIndex): TimelineModel {
	const bornDayOf = new Map<string, number>();
	const updatedDayOf = new Map<string, number>();
	const unknownCreated: string[] = [];
	const validDays = new Set<number>();

	// First pass: collect valid created / updated values to build the set of active days.
	for (const n of index.nodes.values()) {
		const born = dayOf(n.created);
		if (born === null) {
			unknownCreated.push(n.path);
		} else {
			bornDayOf.set(n.path, born);
			validDays.add(born);
		}
		const upd = dayOf(n.updated);
		if (upd !== null) {
			updatedDayOf.set(n.path, upd);
			validDays.add(upd);
		}
	}

	const days = [...validDays].sort((a, b) => a - b);
	// If there isn't a single valid date, minDay/maxDay are 0 (the caller disables this via days.length === 0).
	const minDay = days.length > 0 ? days[0] : 0;
	const maxDay = days.length > 0 ? days[days.length - 1] : 0;

	// Round unknown created to "before the oldest day" so it's always visible.
	const fallbackBorn = minDay - 1;
	for (const path of unknownCreated) bornDayOf.set(path, fallbackBorn);

	// Align unknown updated to the same value as bornDay (so it isn't misclassified as "updated" in a diff).
	for (const n of index.nodes.values()) {
		if (!updatedDayOf.has(n.path)) {
			updatedDayOf.set(n.path, bornDayOf.get(n.path) ?? fallbackBorn);
		}
	}

	return {
		days,
		minDay,
		maxDay,
		bornDayOf,
		updatedDayOf,
		unknownCreatedCount: unknownCreated.length,
	};
}

/**
 * Bake bornDay / updatedDay into the graphology graph's node attributes, and
 * bornDay (= the max of both endpoint nodes' bornDay) into its edge attributes.
 * This lets the reducer do a single numeric comparison per frame instead of a
 * Map lookup.
 */
export function annotateGraphTime(graph: Graph, model: TimelineModel): void {
	// Nodes not covered by the model, such as ghost nodes (isGhost attribute), are rounded to before the oldest day.
	const fallback = model.minDay - 1;
	graph.updateEachNodeAttributes((node, attrs) => {
		const born = model.bornDayOf.get(node);
		attrs["bornDay"] = born !== undefined ? born : fallback;
		const upd = model.updatedDayOf.get(node);
		attrs["updatedDay"] = upd !== undefined ? upd : attrs["bornDay"];
		return attrs;
	});
	// Edge time is approximated as the max of both endpoint nodes' creation dates (this guarantees monotonicity).
	graph.updateEachEdgeAttributes(
		(_edge, attrs, _s, _t, sourceAttrs, targetAttrs) => {
			const sb = sourceAttrs["bornDay"] as number;
			const tb = targetAttrs["bornDay"] as number;
			attrs["bornDay"] = sb > tb ? sb : tb;
			return attrs;
		}
	);
}

/**
 * Cumulative visible node count / edge count (bornDay <= day) at each point
 * in days. Used against a graph that has already been through
 * annotateGraphTime. For O(1) lookup of per-timepoint stats.
 */
export function cumulativeCounts(
	graph: Graph,
	days: number[]
): { nodes: number[]; edges: number[] } {
	const nodeBorn: number[] = [];
	graph.forEachNode((_node, attrs) => {
		nodeBorn.push(attrs["bornDay"] as number);
	});
	const edgeBorn: number[] = [];
	graph.forEachEdge((_edge, attrs) => {
		edgeBorn.push(attrs["bornDay"] as number);
	});
	nodeBorn.sort((a, b) => a - b);
	edgeBorn.sort((a, b) => a - b);

	const nodes = new Array<number>(days.length);
	const edges = new Array<number>(days.length);
	// O(N log N + D) via ascending bornDay sort + two pointers (days is also ascending).
	let ni = 0;
	let ei = 0;
	for (let d = 0; d < days.length; d++) {
		const day = days[d];
		while (ni < nodeBorn.length && nodeBorn[ni] <= day) ni++;
		while (ei < edgeBorn.length && edgeBorn[ei] <= day) ei++;
		nodes[d] = ni;
		edges[d] = ei;
	}
	return { nodes, edges };
}

export interface CountSeries {
	/** Cumulative page count at each `model.days` index (all included types). */
	total: number[];
	/** Cumulative page count per wiki page type. */
	byType: Record<WikiPageType, number[]>;
	/** New pages on that day (`total[i] - total[i-1]`; index 0 is the opening step). */
	dailyTotal: number[];
	/** New pages on that day, per type. */
	dailyByType: Record<WikiPageType, number[]>;
	/**
	 * Nodes in this series whose `created` could not be parsed.
	 * Recalculated after the seed filter so the chart footnote matches
	 * the population actually plotted.
	 */
	unknownCreatedCount: number;
}

export interface CountSeriesOptions {
	/** Drop `entity` + `status: seed` only. Isolated pages stay. */
	excludeSeedEntities?: boolean;
}

/**
 * Per-day page counts from the full GraphIndex (not the pruned backbone).
 * X axis is `model.days` so a playhead index matches TimeControls.
 * Unknown `created` uses the model's fallback bornDay (`minDay - 1`) and
 * therefore appears in the opening step of both cumulative and daily series.
 */
export function countSeries(
	index: Pick<GraphIndex, "nodes">,
	model: TimelineModel,
	opts?: CountSeriesOptions
): CountSeries {
	const days = model.days;
	const byTypeBorn = emptyTypeBuckets();
	const allBorn: number[] = [];
	const excludeSeed = opts?.excludeSeedEntities === true;
	let unknownCreatedCount = 0;

	for (const n of index.nodes.values()) {
		if (excludeSeed && n.type === "entity" && n.status === "seed") continue;
		const born = model.bornDayOf.get(n.path);
		if (born === undefined) continue;
		if (dayOf(n.created) === null) unknownCreatedCount++;
		byTypeBorn[n.type].push(born);
		allBorn.push(born);
	}

	const total = accumulateBorn(allBorn, days);
	const byType = emptyTypeSeries(days.length);
	for (const type of WIKI_PAGE_TYPES) {
		byType[type] = accumulateBorn(byTypeBorn[type], days);
	}
	return {
		total,
		byType,
		dailyTotal: dailyFromCumulative(total),
		dailyByType: {
			source: dailyFromCumulative(byType.source),
			entity: dailyFromCumulative(byType.entity),
			concept: dailyFromCumulative(byType.concept),
			question: dailyFromCumulative(byType.question),
		},
		unknownCreatedCount,
	};
}

function emptyTypeBuckets(): Record<WikiPageType, number[]> {
	return { source: [], entity: [], concept: [], question: [] };
}

function emptyTypeSeries(len: number): Record<WikiPageType, number[]> {
	return {
		source: new Array<number>(len).fill(0),
		entity: new Array<number>(len).fill(0),
		concept: new Array<number>(len).fill(0),
		question: new Array<number>(len).fill(0),
	};
}

/** Sorted bornDays + two pointers, same contract as `cumulativeCounts`. */
function accumulateBorn(born: number[], days: number[]): number[] {
	born.sort((a, b) => a - b);
	const out = new Array<number>(days.length);
	let i = 0;
	for (let d = 0; d < days.length; d++) {
		const day = days[d];
		while (i < born.length && born[i] <= day) i++;
		out[d] = i;
	}
	return out;
}

function dailyFromCumulative(cum: number[]): number[] {
	const out = new Array<number>(cum.length);
	let prev = 0;
	for (let i = 0; i < cum.length; i++) {
		out[i] = cum[i] - prev;
		prev = cum[i];
	}
	return out;
}

/** Classify a node's attributes (including bornDay/updatedDay) within the time context */
export function classifyNode(
	attrs: { bornDay: number; updatedDay: number },
	ctx: TimeFilterContext
): TimeClass {
	if (ctx.mode === "replay") {
		if (attrs.bornDay > ctx.t) return "hidden";
		if (attrs.bornDay > ctx.t - ctx.recentWindow) return "new";
		return "existing";
	}
	if (attrs.bornDay > ctx.b) return "hidden";
	if (attrs.bornDay > ctx.a) return "new";
	if (attrs.updatedDay > ctx.a && attrs.updatedDay <= ctx.b) return "updated";
	return "existing";
}

/** Classify an edge's attributes (bornDay). Never returns "updated" */
export function classifyEdge(
	attrs: { bornDay: number },
	ctx: TimeFilterContext
): TimeClass {
	if (ctx.mode === "replay") {
		if (attrs.bornDay > ctx.t) return "hidden";
		if (attrs.bornDay > ctx.t - ctx.recentWindow) return "new";
		return "existing";
	}
	if (attrs.bornDay > ctx.b) return "hidden";
	if (attrs.bornDay > ctx.a) return "new";
	return "existing";
}

export interface SelectNewEdgesOptions {
	/**
	 * Diff default is INTER_CAP (anti-fog). `null` means no cap — Replay
	 * uses this; the recent-highlight window is the natural bound.
	 */
	limit?: number | null;
	includeTypes?: ReadonlySet<string>;
}

/**
 * New edges under `ctx`, ranked by link-instance count. Diff full-range
 * can mark almost every edge "new"; INTER_CAP keeps that from becoming a
 * white fog. Replay passes `limit: null`.
 */
export function selectNewEdges(
	graph: Graph,
	ctx: TimeFilterContext,
	opts?: SelectNewEdgesOptions
): Set<string> {
	const uncapped = opts?.limit === null;
	const limit = uncapped ? Number.POSITIVE_INFINITY : (opts?.limit ?? INTER_CAP);
	const includeTypes = opts?.includeTypes;
	if (!uncapped && limit <= 0) return new Set();

	const news: { edge: string; count: number }[] = [];
	graph.forEachEdge((edge, attrs) => {
		if (edgeTouchesHiddenType(graph, edge, includeTypes)) return;
		if (classifyEdge(attrs as { bornDay: number }, ctx) !== "new") return;
		const count = typeof attrs["count"] === "number" ? (attrs["count"] as number) : 1;
		news.push({ edge, count });
	});
	if (news.length <= limit) return new Set(news.map((item) => item.edge));
	news.sort((a, b) => {
		if (b.count !== a.count) return b.count - a.count;
		return a.edge < b.edge ? -1 : a.edge > b.edge ? 1 : 0;
	});
	return new Set(news.slice(0, limit).map((item) => item.edge));
}

/**
 * Whether a time-classified edge should draw. `applyEdgeSkeleton` writes
 * `hidden` on the graph; the reducer must not trust that attribute for
 * accent (new) edges — it has to set hidden from this predicate.
 */
export function timeEdgeShown(
	cls: TimeClass,
	edge: string,
	skeleton: ReadonlySet<string>,
	accent: ReadonlySet<string>
): boolean {
	if (cls === "hidden") return false;
	if (skeleton.has(edge)) return true;
	return cls === "new" && accent.has(edge);
}
