import Graph from "graphology";
import louvain from "graphology-communities-louvain";
import { LOUVAIN_SEED, mulberry32 } from "./rng";

/**
 * Assign louvain communities as the "community" attribute and return the
 * community count. Since the domain field (300+ free-text variants) can't be
 * used as a classification axis, thematic clusters are computed from the link
 * structure instead. rng is fixed so community ids stay stable across rebuilds
 * of the same topology.
 */
export function assignCommunities(graph: Graph): number {
	if (graph.order === 0) return 0;
	louvain.assign(graph, {
		nodeCommunityAttribute: "community",
		getEdgeWeight: "count",
		rng: mulberry32(LOUVAIN_SEED),
	});
	const seen = new Set<number>();
	graph.forEachNode((_, attrs) => seen.add(attrs["community"] as number));
	return seen.size;
}

/** Prefer weighted inbound instances (inCount) when present. */
export function nodeWeight(attrs: Record<string, unknown>): number {
	if (typeof attrs["inCount"] === "number") return attrs["inCount"] as number;
	if (typeof attrs["inDeg"] === "number") return attrs["inDeg"] as number;
	return 0;
}

/** Node size: log scale of inbound link instances */
export function nodeSize(weight: number): number {
	return 2 + 2.2 * Math.log2(1 + weight);
}
