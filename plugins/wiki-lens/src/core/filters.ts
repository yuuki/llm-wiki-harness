import Graph from "graphology";
import { GraphIndex } from "./graph-index";
import { personalizedPageRank } from "./ppr";

export interface BackboneOptions {
	/** Whether to include seed entities (the stub layer). Default false */
	includeSeedEntities: boolean;
	/** Whether to drop nodes with no in/out edges after filtering. Default true */
	dropIsolated: boolean;
}

export const DEFAULT_BACKBONE: BackboneOptions = {
	includeSeedEntities: false,
	dropIsolated: true,
};

/**
 * Build a display-ready Backbone Graph subgraph (graphology) from a GraphIndex.
 * By default, excludes seed entity stubs (most entities) and returns a
 * skeleton of only concepts / sources / questions / matured entities.
 */
export function buildBackbone(
	index: GraphIndex,
	opts: BackboneOptions
): Graph {
	const graph = new Graph({ type: "directed", multi: false });

	for (const n of index.nodes.values()) {
		if (
			n.type === "entity" &&
			n.status === "seed" &&
			!opts.includeSeedEntities
		) {
			continue;
		}
		graph.addNode(n.path, {
			label: n.title,
			nodeType: n.type,
			status: n.status,
			inDeg: n.inDeg,
			inCount: n.inCount,
			isLintStub: n.isLintStub,
		});
	}

	for (const e of index.edges) {
		if (!graph.hasNode(e.src) || !graph.hasNode(e.dst)) continue;
		if (graph.hasEdge(e.src, e.dst)) continue;
		graph.addEdge(e.src, e.dst, { count: e.count });
	}

	if (opts.dropIsolated) {
		const isolated: string[] = [];
		graph.forEachNode((node) => {
			if (graph.degree(node) === 0) isolated.push(node);
		});
		for (const node of isolated) graph.dropNode(node);
	}

	return graph;
}

export interface EgoOptions {
	depth: number;
	/** Show the center page's unresolved links as ghost nodes */
	showGhosts: boolean;
	/** Upper bound on BFS expansion. Prevents depth 3 from exploding on hub concepts (avg. 50 links) */
	maxNodes: number;
}

export const DEFAULT_EGO: EgoOptions = {
	depth: 2,
	showGhosts: true,
	maxNodes: 800,
};

export interface EgoResult {
	graph: Graph;
	truncated: boolean;
	ghostCount: number;
}

/**
 * Build the depth-k neighborhood (ego graph) from the center page via
 * undirected BFS. Nodes are connected as an induced subgraph (including edges
 * between neighbors), and seed stubs are not excluded either (in a local
 * context we prioritize completeness over pruning, dimming them on the display
 * side instead). When showGhosts is set, add the center page's dead links as
 * isGhost nodes to show right there "where this trail breaks."
 */
export function buildEgoGraph(
	index: GraphIndex,
	center: string,
	opts: EgoOptions
): EgoResult {
	// Undirected adjacency table (scanning 29k edges only takes a few ms even every time)
	const adj = new Map<string, string[]>();
	const push = (a: string, b: string) => {
		const list = adj.get(a);
		if (list) list.push(b);
		else adj.set(a, [b]);
	};
	for (const e of index.edges) {
		push(e.src, e.dst);
		push(e.dst, e.src);
	}

	const depthOf = new Map<string, number>([[center, 0]]);
	let frontier = [center];
	let truncated = false;
	for (let d = 1; d <= opts.depth && frontier.length > 0; d++) {
		const next: string[] = [];
		for (const node of frontier) {
			for (const nb of adj.get(node) ?? []) {
				if (depthOf.has(nb)) continue;
				if (depthOf.size >= opts.maxNodes) {
					truncated = true;
					break;
				}
				depthOf.set(nb, d);
				next.push(nb);
			}
			if (truncated) break;
		}
		if (truncated) break;
		frontier = next;
	}

	const graph = new Graph({ type: "directed", multi: false });
	for (const [path, depth] of depthOf) {
		const n = index.nodes.get(path);
		if (!n) continue;
		graph.addNode(path, {
			label: n.title,
			nodeType: n.type,
			status: n.status,
			inDeg: n.inDeg,
			inCount: n.inCount,
			depth,
			isGhost: false,
		});
	}
	for (const e of index.edges) {
		if (!graph.hasNode(e.src) || !graph.hasNode(e.dst)) continue;
		if (graph.hasEdge(e.src, e.dst)) continue;
		graph.addEdge(e.src, e.dst, { count: e.count });
	}

	let ghostCount = 0;
	if (opts.showGhosts) {
		for (const dead of index.deadTargets.values()) {
			if (dead.cause === "file-ref") continue;
			if (!dead.referrers.includes(center)) continue;
			const id = `ghost:${dead.target}`;
			graph.addNode(id, {
				label: `${dead.target} (not yet created)`,
				nodeType: "ghost",
				status: "unknown",
				inDeg: dead.refs,
				depth: 1,
				isGhost: true,
			});
			graph.addEdge(center, id, { count: 1, isGhostEdge: true });
			ghostCount++;
		}
	}

	return { graph, truncated, ghostCount };
}

export interface LensOptions {
	/** Max number of nodes to display (including the center) */
	maxNodes: number;
	/** Show the center page's unresolved links as ghost nodes */
	showGhosts: boolean;
}

export const DEFAULT_LENS: LensOptions = {
	maxNodes: 150,
	showGhosts: true,
};

export interface LensResult {
	graph: Graph;
	/** Total number of candidate nodes (excluding the center) with a PPR score greater than 0 */
	candidateCount: number;
	ghostCount: number;
}

/**
 * Select the top N nodes by relevance from the center page using Personalized
 * PageRank (PPR) and return them as an induced subgraph. Rather than an
 * arbitrary cutoff by BFS depth, this mechanically selects "the nodes most
 * deeply related to the center" up to the display count cap. Within the
 * selected subgraph, computes the undirected BFS depth from the center and
 * uses it as the ring number for the concentric ring layout (rounded to depth
 * 3 if selected nodes are disconnected from each other).
 */
export function buildLensGraph(
	index: GraphIndex,
	center: string,
	opts: LensOptions
): LensResult {
	const scores = personalizedPageRank(index, center);

	const candidates = Array.from(scores.entries()).filter(
		([path]) => path !== center
	);
	candidates.sort((a, b) => b[1] - a[1]);
	const top = candidates.slice(0, Math.max(0, opts.maxNodes - 1));

	const selected = new Set<string>([center, ...top.map(([path]) => path)]);

	// Compute the undirected BFS depth within the subgraph, using only induced edges between selected nodes
	const adj = new Map<string, string[]>();
	const push = (a: string, b: string) => {
		const list = adj.get(a);
		if (list) list.push(b);
		else adj.set(a, [b]);
	};
	for (const e of index.edges) {
		if (!selected.has(e.src) || !selected.has(e.dst)) continue;
		push(e.src, e.dst);
		push(e.dst, e.src);
	}
	const depthOf = new Map<string, number>([[center, 0]]);
	let frontier = [center];
	for (let d = 1; frontier.length > 0; d++) {
		const next: string[] = [];
		for (const node of frontier) {
			for (const nb of adj.get(node) ?? []) {
				if (depthOf.has(nb)) continue;
				depthOf.set(nb, d);
				next.push(nb);
			}
		}
		frontier = next;
	}

	const graph = new Graph({ type: "directed", multi: false });
	for (const path of selected) {
		const n = index.nodes.get(path);
		if (!n) continue;
		graph.addNode(path, {
			label: n.title,
			nodeType: n.type,
			status: n.status,
			inDeg: n.inDeg,
			inCount: n.inCount,
			ppr: scores.get(path) ?? 0,
			depth: depthOf.get(path) ?? 3,
			isGhost: false,
		});
	}
	for (const e of index.edges) {
		if (!graph.hasNode(e.src) || !graph.hasNode(e.dst)) continue;
		if (graph.hasEdge(e.src, e.dst)) continue;
		graph.addEdge(e.src, e.dst, { count: e.count });
	}

	let ghostCount = 0;
	if (opts.showGhosts) {
		for (const dead of index.deadTargets.values()) {
			if (dead.cause === "file-ref") continue;
			if (!dead.referrers.includes(center)) continue;
			const id = `ghost:${dead.target}`;
			graph.addNode(id, {
				label: `${dead.target} (not yet created)`,
				nodeType: "ghost",
				status: "unknown",
				inDeg: dead.refs,
				ppr: 0,
				depth: 1,
				isGhost: true,
			});
			graph.addEdge(center, id, { count: 1, isGhostEdge: true });
			ghostCount++;
		}
	}

	return { graph, candidateCount: candidates.length, ghostCount };
}
