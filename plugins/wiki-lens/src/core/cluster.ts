import Graph from "graphology";
import { circular } from "graphology-layout";
import type { ForceAtlas2Settings } from "graphology-layout-forceatlas2";
import { COMMUNITY_PALETTE } from "./palette";
import { nodeWeight } from "./metrics";

/**
 * Layer that aggregates the Backbone Graph's louvain clusters for the aggregate view.
 * Groups nodes by the "community" attribute and builds each cluster's representative
 * members, plus a meta-graph that collapses each cluster into a single node
 * (super-nodes + aggregated edges).
 */

export interface ClusterInfo {
	id: number;
	size: number;
	label: string;
	topMembers: { path: string; title: string; inDeg: number }[];
}

/** Keep this many strongest outgoing inter-cluster edges per cluster (plus the MST). */
export const META_MAX_OUT_DEGREE = 2;
const META_TITLE_CHARS = 16;

type NameMember = {
	path: string;
	title: string;
	inDeg: number;
	nodeType: string;
};

/**
 * Cluster display names prefer concepts, then questions, then entities,
 * then sources. Book-length source titles should not win just because
 * they have high inbound weight.
 */
export function clusterNameRank(nodeType: unknown): number {
	if (nodeType === "concept") return 0;
	if (nodeType === "question") return 1;
	if (nodeType === "entity") return 2;
	if (nodeType === "source") return 3;
	return 4;
}

function betterNameMember(a: NameMember, b: NameMember): boolean {
	const ra = clusterNameRank(a.nodeType);
	const rb = clusterNameRank(b.nodeType);
	if (ra !== rb) return ra < rb;
	if (a.inDeg !== b.inDeg) return a.inDeg > b.inDeg;
	return a.path < b.path;
}

/**
 * Overview ForceAtlas2. inferSettings turns on strongGravity and leaves
 * adjustSizes off, which collapses a dense meta-graph into an unreadable clump.
 */
export const META_FA2_SETTINGS: ForceAtlas2Settings = {
	adjustSizes: true,
	barnesHutOptimize: false,
	strongGravityMode: false,
	gravity: 0.55,
	scalingRatio: 40,
	slowDown: 5,
	outboundAttractionDistribution: false,
	edgeWeightInfluence: 0.4,
	linLogMode: false,
};

export const META_FA2_ITERATIONS = 420;

/** Build a meta-graph super-node id from a cluster id */
export function clusterNodeId(id: number): string {
	return `cluster:${id}`;
}

/**
 * Group nodes by the "community" attribute and return cluster info sorted by
 * descending size. topMembers is the top 5 by inbound link instances (panel).
 * label uses concept titles first (then question / entity / source).
 */
export function computeClusters(graph: Graph): ClusterInfo[] {
	const groups = new Map<number, NameMember[]>();
	graph.forEachNode((node, attrs) => {
		const community = (attrs["community"] as number) ?? 0;
		const member: NameMember = {
			path: node,
			title: (attrs["label"] as string) ?? node,
			inDeg: nodeWeight(attrs),
			nodeType: (attrs["nodeType"] as string) ?? "",
		};
		const list = groups.get(community);
		if (list) list.push(member);
		else groups.set(community, [member]);
	});

	const clusters: ClusterInfo[] = [];
	for (const [id, members] of groups) {
		const byWeight = [...members].sort((a, b) => b.inDeg - a.inDeg);
		const topMembers = byWeight.slice(0, 5).map(({ path, title, inDeg }) => ({
			path,
			title,
			inDeg,
		}));
		const named = [...members].sort((a, b) => {
			if (betterNameMember(a, b)) return -1;
			if (betterNameMember(b, a)) return 1;
			return 0;
		});
		const label =
			named
				.slice(0, 2)
				.map((m) => m.title)
				.join(" / ") || `Cluster ${id}`;
		clusters.push({ id, size: members.length, label, topMembers });
	}
	clusters.sort((a, b) => b.size - a.size);
	return clusters;
}

/** Visual radius of a cluster super-node (log of member count). */
export function clusterNodeSize(memberCount: number): number {
	return 5 + 2.2 * Math.log2(1 + memberCount);
}

export function truncateTitle(title: string, max = META_TITLE_CHARS): string {
	if (title.length <= max) return title;
	return title.slice(0, max - 1) + "…";
}

/** Short always-on label: concept-first name + member count. */
export function clusterOverviewLabel(c: ClusterInfo): string {
	const primary = (c.label.split(" / ")[0] || "").trim();
	const title = truncateTitle(primary || `Cluster ${c.id}`);
	return `${title} (${c.size})`;
}

function pairKey(a: number, b: number): string {
	return a < b ? `${a}\t${b}` : `${b}\t${a}`;
}

/**
 * Directed keys (`cs->ct`) that belong on the overview skeleton: a maximum
 * spanning tree over undirected pair weights, plus each cluster's strongest
 * outgoing edges. Everything else stays on the graph as a hover-only weak edge.
 */
export function selectStrongMetaEdges(
	directed: Map<string, number>,
	clusterIds: number[],
	maxOutDegree = META_MAX_OUT_DEGREE
): Set<string> {
	const strong = new Set<string>();
	if (clusterIds.length === 0) return strong;

	const undirected = new Map<string, { a: number; b: number; weight: number }>();
	const outgoing = new Map<number, { key: string; count: number }[]>();
	const byPair = new Map<string, { key: string; count: number }[]>();

	for (const [key, count] of directed) {
		const sep = key.indexOf("->");
		const cs = Number(key.slice(0, sep));
		const ct = Number(key.slice(sep + 2));
		const pk = pairKey(cs, ct);
		const prev = undirected.get(pk);
		if (!prev || count > prev.weight) {
			undirected.set(pk, { a: cs, b: ct, weight: count });
		}
		const out = outgoing.get(cs);
		if (out) out.push({ key, count });
		else outgoing.set(cs, [{ key, count }]);
		const pairList = byPair.get(pk);
		if (pairList) pairList.push({ key, count });
		else byPair.set(pk, [{ key, count }]);
	}

	const parent = new Map<number, number>();
	for (const id of clusterIds) parent.set(id, id);
	const find = (x: number): number => {
		let p = parent.get(x) ?? x;
		if (p !== x) {
			p = find(p);
			parent.set(x, p);
		}
		return p;
	};

	const sorted = [...undirected.values()].sort((a, b) => b.weight - a.weight);
	let components = clusterIds.length;
	for (const e of sorted) {
		if (!parent.has(e.a) || !parent.has(e.b)) continue;
		const pa = find(e.a);
		const pb = find(e.b);
		if (pa === pb) continue;
		parent.set(pa, pb);
		components--;
		const pairEdges = byPair.get(pairKey(e.a, e.b)) ?? [];
		let best = -1;
		for (const item of pairEdges) best = Math.max(best, item.count);
		for (const item of pairEdges) {
			if (item.count === best) strong.add(item.key);
		}
		if (components <= 1) break;
	}

	for (const list of outgoing.values()) {
		list.sort((a, b) => b.count - a.count);
		for (const item of list.slice(0, maxOutDegree)) strong.add(item.key);
	}

	return strong;
}

/**
 * Build a meta-graph collapsing each cluster into a single super-node.
 * Edges directionally aggregate the inter-cluster edge counts from the source
 * graph, discarding self-loops. The overview draws only a skeleton (MST +
 * top outgoing); remaining edges stay hidden until hover.
 */
export function buildMetaGraph(graph: Graph, clusters: ClusterInfo[]): Graph {
	const meta = new Graph({ type: "directed", multi: false });

	clusters.forEach((c) => {
		const label = clusterOverviewLabel(c);
		meta.addNode(clusterNodeId(c.id), {
			label,
			fullLabel: `${c.label} (${c.size})`,
			size: clusterNodeSize(c.size),
			color: COMMUNITY_PALETTE[c.id % COMMUNITY_PALETTE.length],
			clusterId: c.id,
			isCluster: true,
			forceLabel: true,
		});
	});

	const counts = new Map<string, number>();
	graph.forEachEdge((_edge, attrs, source, target) => {
		const cs = graph.getNodeAttribute(source, "community") as number;
		const ct = graph.getNodeAttribute(target, "community") as number;
		if (cs === ct) return;
		const key = `${cs}->${ct}`;
		const weight = (attrs["count"] as number) ?? 1;
		counts.set(key, (counts.get(key) ?? 0) + weight);
	});

	const clusterIds = clusters.map((c) => c.id);
	const strong = selectStrongMetaEdges(counts, clusterIds);
	let shown = 0;

	for (const [key, count] of counts) {
		const sep = key.indexOf("->");
		const cs = Number(key.slice(0, sep));
		const ct = Number(key.slice(sep + 2));
		const sid = clusterNodeId(cs);
		const tid = clusterNodeId(ct);
		if (!meta.hasNode(sid) || !meta.hasNode(tid)) continue;
		if (meta.hasEdge(sid, tid)) continue;
		const isStrong = strong.has(key);
		if (isStrong) shown++;
		meta.addEdge(sid, tid, {
			size: isStrong
				? Math.min(2.2, 0.45 + 0.3 * Math.log2(1 + count))
				: 0.5,
			count,
			weight: isStrong ? Math.log2(1 + count) : 0,
			weak: !isStrong,
			hidden: !isStrong,
			color: isStrong
				? "rgba(170, 170, 170, 0.32)"
				: "rgba(160, 160, 160, 0.1)",
		});
	}

	meta.setAttribute("interEdgeTotal", counts.size);
	meta.setAttribute("interEdgeShown", shown);

	return meta;
}

/** Place super-nodes on a circle whose circumference fits their diameters. */
export function initMetaLayout(graph: Graph): void {
	if (graph.order === 0) return;
	let circumference = 0;
	graph.forEachNode((_, attrs) => {
		circumference += 2 * (((attrs["size"] as number) ?? 8) + 22);
	});
	const radius = Math.max(280, circumference / (2 * Math.PI));
	circular.assign(graph, { scale: radius });
}

/**
 * Push overlapping super-nodes apart. ForceAtlas2 adjustSizes is approximate;
 * this is the readability guarantee after layout settles.
 */
export function separateNodes(
	graph: Graph,
	padding = 18,
	rounds = 80
): void {
	const nodes = graph.nodes();
	const n = nodes.length;
	if (n < 2) return;

	for (let r = 0; r < rounds; r++) {
		let maxOverlap = 0;
		for (let i = 0; i < n; i++) {
			for (let j = i + 1; j < n; j++) {
				const ai = graph.getNodeAttributes(nodes[i]);
				const aj = graph.getNodeAttributes(nodes[j]);
				const xi = ai["x"] as number;
				const yi = ai["y"] as number;
				const xj = aj["x"] as number;
				const yj = aj["y"] as number;
				const si = (ai["size"] as number) ?? 8;
				const sj = (aj["size"] as number) ?? 8;
				let dx = xi - xj;
				let dy = yi - yj;
				let dist = Math.hypot(dx, dy);
				const labeled =
					ai["forceLabel"] === true && aj["forceLabel"] === true
						? 28
						: 0;
				const min = si + sj + padding + labeled;
				if (dist < 1e-6) {
					dx = 1;
					dy = 0;
					dist = 1;
				}
				if (dist >= min) continue;
				const push = (min - dist) / 2;
				const ux = dx / dist;
				const uy = dy / dist;
				graph.setNodeAttribute(nodes[i], "x", xi + ux * push);
				graph.setNodeAttribute(nodes[i], "y", yi + uy * push);
				graph.setNodeAttribute(nodes[j], "x", xj - ux * push);
				graph.setNodeAttribute(nodes[j], "y", yj - uy * push);
				maxOverlap = Math.max(maxOverlap, min - dist);
			}
		}
		if (maxOverlap < 0.4) break;
	}
}

/** Unit outward angle from the layout centroid, used to park labels off the node. */
export function assignLabelAngles(graph: Graph): void {
	if (graph.order === 0) return;
	let cx = 0;
	let cy = 0;
	graph.forEachNode((_, attrs) => {
		cx += attrs["x"] as number;
		cy += attrs["y"] as number;
	});
	cx /= graph.order;
	cy /= graph.order;
	graph.forEachNode((node, attrs) => {
		const dx = (attrs["x"] as number) - cx;
		const dy = (attrs["y"] as number) - cy;
		graph.setNodeAttribute(node, "labelAngle", Math.atan2(dy, dx));
	});
}
