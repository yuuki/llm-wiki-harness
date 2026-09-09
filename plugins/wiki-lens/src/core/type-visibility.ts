import Graph from "graphology";
import { WIKI_PAGE_TYPES, WikiPageType } from "./types";

/** Plugin / view persistence for the All Nodes / Growth Timeline type checkboxes. */
export interface TypeFilterStore {
	loadVisibleTypes(): Set<WikiPageType>;
	saveVisibleTypes(types: ReadonlySet<WikiPageType>): void;
}

/**
 * Recover a checkbox set from plugin data. Unknown entries are dropped.
 * Empty / malformed input falls back to every page type so the inspector
 * cannot boot with nothing visible.
 */
export function parseVisibleTypes(raw: unknown): Set<WikiPageType> {
	if (!Array.isArray(raw)) return new Set(WIKI_PAGE_TYPES);
	const next = new Set<WikiPageType>();
	for (const item of raw) {
		if ((WIKI_PAGE_TYPES as readonly string[]).includes(item as string)) {
			next.add(item as WikiPageType);
		}
	}
	return next.size === 0 ? new Set(WIKI_PAGE_TYPES) : next;
}

/** Stable order for `data.json`. */
export function serializeVisibleTypes(
	visible: ReadonlySet<string>
): WikiPageType[] {
	return WIKI_PAGE_TYPES.filter((type) => visible.has(type));
}

/**
 * Inspector type filter (All Nodes and Growth Timeline).
 *
 * `undefined` means no filter: every node is visible, including unknown types.
 * A set (including empty) means membership only — unknown types are hidden.
 */
export function isNodeTypeVisible(
	nodeType: unknown,
	includeTypes?: ReadonlySet<string>
): boolean {
	if (!includeTypes) return true;
	return typeof nodeType === "string" && includeTypes.has(nodeType);
}

/**
 * Collapse a checkbox set to the core filter value.
 * All four page types checked → `undefined` (no filter).
 */
export function includeTypesFromVisible(
	visible: ReadonlySet<string>
): ReadonlySet<string> | undefined {
	if (WIKI_PAGE_TYPES.every((type) => visible.has(type))) return undefined;
	return visible;
}

export function edgeTouchesHiddenType(
	graph: Graph,
	edge: string,
	includeTypes?: ReadonlySet<string>
): boolean {
	if (!includeTypes || !graph.hasEdge(edge)) return false;
	const [source, target] = graph.extremities(edge);
	return (
		!isNodeTypeVisible(graph.getNodeAttribute(source, "nodeType"), includeTypes) ||
		!isNodeTypeVisible(graph.getNodeAttribute(target, "nodeType"), includeTypes)
	);
}

export function visibleGraphStats(
	graph: Graph,
	includeTypes?: ReadonlySet<string>
): { nodes: number; edges: number; clusters: number } {
	let nodes = 0;
	const communities = new Set<number>();
	graph.forEachNode((_node, attrs) => {
		if (!isNodeTypeVisible(attrs["nodeType"], includeTypes)) return;
		nodes++;
		communities.add((attrs["community"] as number) ?? 0);
	});
	let edges = 0;
	graph.forEachEdge((edge) => {
		if (!edgeTouchesHiddenType(graph, edge, includeTypes)) edges++;
	});
	return { nodes, edges, clusters: communities.size };
}
