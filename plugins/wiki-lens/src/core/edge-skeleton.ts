import Graph from "graphology";
import { edgeTouchesHiddenType } from "./type-visibility";

/** Max inter-cluster edges shown in both far and zoomed inspectors. */
export const INTER_CAP = 200;
/** Max visible skeleton edges in zoomed inspectors (inter reserved first). */
export const ZOOMED_TOTAL = 800;
/** sigma Camera default / animatedReset. LOD threshold is half of this. */
export const CAMERA_RESET_RATIO = 1;
export const LOD_RATIO_THRESHOLD = CAMERA_RESET_RATIO * 0.5;
export const CAMERA_LOD_DEBOUNCE_MS = 150;

export type LodMode = "far" | "zoomed";

export interface SelectVisibleEdgesOptions {
	/** When set, skip edges whose either endpoint `nodeType` is absent. */
	includeTypes?: ReadonlySet<string>;
}

function pairKey(a: number, b: number): string {
	return a < b ? `${a}\t${b}` : `${b}\t${a}`;
}

function countOf(attrs: Record<string, unknown>): number {
	return typeof attrs["count"] === "number" ? (attrs["count"] as number) : 1;
}

function communityOf(graph: Graph, node: string): number {
	return (graph.getNodeAttribute(node, "community") as number) ?? 0;
}

/**
 * Select the inspector skeleton. Caps are module constants so a numeric
 * third argument cannot inflate inter. Optional `includeTypes` drops edges
 * that touch a hidden page type.
 *
 * Inter: one max-count edge per undirected cluster pair, then INTER_CAP.
 * Far: that inter set only. Zoomed: same inter set, fill with intra to
 * ZOOMED_TOTAL. Non-selected edges stay on the graph; applyEdgeSkeleton
 * marks them weak/hidden.
 */
export function selectVisibleEdges(
	graph: Graph,
	mode: LodMode,
	opts?: SelectVisibleEdgesOptions
): Set<string> {
	const includeTypes = opts?.includeTypes;
	const interBest = new Map<string, { edgeId: string; count: number }>();
	const intra: { edgeId: string; count: number }[] = [];

	graph.forEachEdge((edge, attrs, source, target) => {
		if (edgeTouchesHiddenType(graph, edge, includeTypes)) return;
		const cs = communityOf(graph, source);
		const ct = communityOf(graph, target);
		const count = countOf(attrs);
		if (cs === ct) {
			intra.push({ edgeId: edge, count });
			return;
		}
		const key = pairKey(cs, ct);
		const prev = interBest.get(key);
		if (
			!prev ||
			count > prev.count ||
			(count === prev.count && edge < prev.edgeId)
		) {
			interBest.set(key, { edgeId: edge, count });
		}
	});

	const inter = [...interBest.values()].sort((a, b) => {
		if (b.count !== a.count) return b.count - a.count;
		return a.edgeId < b.edgeId ? -1 : a.edgeId > b.edgeId ? 1 : 0;
	});
	const visibleInter = inter.slice(0, Math.min(INTER_CAP, inter.length));
	const visible = [...visibleInter];

	if (mode === "zoomed" && visible.length < ZOOMED_TOTAL) {
		const taken = new Set(visible.map((item) => item.edgeId));
		const intraRest = intra
			.filter((item) => !taken.has(item.edgeId))
			.sort((a, b) => {
				if (b.count !== a.count) return b.count - a.count;
				return a.edgeId < b.edgeId ? -1 : a.edgeId > b.edgeId ? 1 : 0;
			});
		visible.push(
			...intraRest.slice(0, ZOOMED_TOTAL - visible.length)
		);
	}

	return new Set(visible.map((item) => item.edgeId));
}

/** Inter-cluster edge ids inside a visible set (for far/zoomed identity tests). */
export function interEdgesIn(
	graph: Graph,
	visible: Set<string>
): Set<string> {
	const inter = new Set<string>();
	for (const edge of visible) {
		if (!graph.hasEdge(edge)) continue;
		const [source, target] = graph.extremities(edge);
		if (communityOf(graph, source) !== communityOf(graph, target)) {
			inter.add(edge);
		}
	}
	return inter;
}

/**
 * Write weak/hidden from a visible set. Does not drop edges.
 * Display still needs a reducer (or full reindex) because sigma may cache attrs.
 */
export function applyEdgeSkeleton(graph: Graph, visible: Set<string>): void {
	graph.forEachEdge((edge) => {
		const on = visible.has(edge);
		graph.setEdgeAttribute(edge, "weak", !on);
		graph.setEdgeAttribute(edge, "hidden", !on);
	});
}
