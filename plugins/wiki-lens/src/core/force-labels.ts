import Graph from "graphology";
import { ClusterInfo, clusterNameRank } from "./cluster";
import { nodeWeight } from "./metrics";
import { isNodeTypeVisible } from "./type-visibility";

export interface PickForceLabelsOptions {
	/** Test-only cap. Far view names every cluster that still has a member. */
	limit?: number;
	/** When set, skip members whose `nodeType` is absent from the set. */
	includeTypes?: ReadonlySet<string>;
	/** Extra membership gate (time-visible, etc.). Runs after the type filter. */
	includeNode?: (node: string, attrs: Record<string, unknown>) => boolean;
}

/**
 * Pick one inspector label per cluster. Prefers a concept (then question,
 * entity, source) with max inCount. No default cap — far view names every
 * cluster. An explicit `limit` is only for tests.
 */
export function pickForceLabels(
	graph: Graph,
	clusters: ClusterInfo[],
	opts?: PickForceLabelsOptions
): string[] {
	const cap = opts?.limit ?? clusters.length;
	const includeTypes = opts?.includeTypes;
	const includeNode = opts?.includeNode;
	if (cap <= 0 || graph.order === 0 || clusters.length === 0) return [];

	const byCommunity = new Map<
		number,
		{ path: string; inCount: number; nodeType: string }[]
	>();
	graph.forEachNode((node, attrs) => {
		const nodeType = (attrs["nodeType"] as string) ?? "";
		if (!isNodeTypeVisible(nodeType, includeTypes)) return;
		if (includeNode && !includeNode(node, attrs)) return;
		const community = (attrs["community"] as number) ?? 0;
		const member = {
			path: node,
			inCount: nodeWeight(attrs),
			nodeType,
		};
		const list = byCommunity.get(community);
		if (list) list.push(member);
		else byCommunity.set(community, [member]);
	});

	const sorted = [...clusters].sort((a, b) => {
		if (b.size !== a.size) return b.size - a.size;
		return a.id - b.id;
	});

	const result: string[] = [];
	const seen = new Set<string>();
	for (const cluster of sorted) {
		const members = byCommunity.get(cluster.id);
		if (!members || members.length === 0) continue;
		let best = members[0];
		for (let i = 1; i < members.length; i++) {
			const m = members[i];
			const betterType =
				clusterNameRank(m.nodeType) < clusterNameRank(best.nodeType);
			const sameType =
				clusterNameRank(m.nodeType) === clusterNameRank(best.nodeType);
			if (
				betterType ||
				(sameType &&
					(m.inCount > best.inCount ||
						(m.inCount === best.inCount && m.path < best.path)))
			) {
				best = m;
			}
		}
		if (seen.has(best.path)) continue;
		result.push(best.path);
		seen.add(best.path);
		if (result.length === cap) break;
	}
	return result;
}

/** Max labels for nodes currently inside the inspector viewport when zoomed. */
export const ZOOM_LABEL_LIMIT = 40;

/**
 * Rank already-filtered viewport candidates by inbound weight. Used when
 * zoomed so the nodes on screen have names; does not inspect camera itself.
 */
export function rankViewportLabels(
	candidates: { path: string; inCount: number }[],
	limit = ZOOM_LABEL_LIMIT
): string[] {
	if (limit <= 0) return [];
	const sorted = [...candidates].sort((a, b) => {
		if (b.inCount !== a.inCount) return b.inCount - a.inCount;
		return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
	});
	const result: string[] = [];
	const seen = new Set<string>();
	for (const item of sorted) {
		if (seen.has(item.path)) continue;
		result.push(item.path);
		seen.add(item.path);
		if (result.length === limit) break;
	}
	return result;
}

/** Set `forceLabel` true only on `paths`. Clears the flag on every other node. */
export function applyForceLabels(graph: Graph, paths: Iterable<string>): void {
	const keep = paths instanceof Set ? paths : new Set(paths);
	graph.forEachNode((node) => {
		graph.setNodeAttribute(node, "forceLabel", keep.has(node));
	});
}
