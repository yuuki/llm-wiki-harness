import { GraphIndex } from "./graph-index";

export interface PprOptions {
	/** Restart probability toward the center. Default 0.15 */
	alpha?: number;
	/** Number of power-iteration rounds. Default 30 */
	iterations?: number;
}

const DEFAULT_ALPHA = 0.15;
const DEFAULT_ITERATIONS = 30;

interface PprAdjacency {
	index: GraphIndex;
	builtAt: number;
	paths: string[];
	idx: Map<string, number>;
	adjNodes: number[][];
	adjWeights: number[][];
	rowSum: Float64Array;
}

let adjCache: PprAdjacency | null = null;

/**
 * Compute Personalized PageRank (PPR) from the center node via power iteration.
 * index.edges is directed, but here it's treated as an undirected graph with
 * edges laid in both directions, in order to measure "depth of relevance."
 * Edge weight uses count (the number of resolved links), and transition
 * probability is proportional to adjacent edge weight (links with higher
 * weight are more likely to be chosen).
 *
 * Adjacency is cached per index generation so a note switch only reruns the
 * 30-iteration walk, not the full ~9k-node assembly.
 */
export function personalizedPageRank(
	index: GraphIndex,
	center: string,
	opts?: PprOptions
): Map<string, number> {
	const alpha = opts?.alpha ?? DEFAULT_ALPHA;
	const iterations = opts?.iterations ?? DEFAULT_ITERATIONS;
	const adj = getAdjacency(index);

	const centerIdx = adj.idx.get(center);
	if (centerIdx === undefined) return new Map();

	const n = adj.paths.length;
	let r = new Float64Array(n);
	r[centerIdx] = 1;

	for (let t = 0; t < iterations; t++) {
		const newR = new Float64Array(n);
		let danglingMass = 0;
		for (let i = 0; i < n; i++) {
			const ri = r[i];
			if (ri === 0) continue;
			const rs = adj.rowSum[i];
			if (rs === 0) {
				// Mass leaving an orphan node (no neighbors) is returned to the center
				danglingMass += ri;
				continue;
			}
			const share = ((1 - alpha) * ri) / rs;
			const neighbors = adj.adjNodes[i];
			const weights = adj.adjWeights[i];
			for (let k = 0; k < neighbors.length; k++) {
				newR[neighbors[k]] += share * weights[k];
			}
		}
		newR[centerIdx] += (1 - alpha) * danglingMass + alpha;
		r = newR;
	}

	const result = new Map<string, number>();
	for (let i = 0; i < n; i++) {
		if (r[i] > 0) result.set(adj.paths[i], r[i]);
	}
	return result;
}

export function clearPprCache(): void {
	adjCache = null;
}

function getAdjacency(index: GraphIndex): PprAdjacency {
	const builtAt = index.stats?.builtAt ?? 0;
	if (
		adjCache &&
		adjCache.index === index &&
		adjCache.builtAt === builtAt
	) {
		return adjCache;
	}

	const paths = Array.from(index.nodes.keys());
	const idx = new Map<string, number>();
	paths.forEach((p, i) => idx.set(p, i));

	const n = paths.length;
	const adjNodes: number[][] = Array.from({ length: n }, () => []);
	const adjWeights: number[][] = Array.from({ length: n }, () => []);

	for (const e of index.edges) {
		const i = idx.get(e.src);
		const j = idx.get(e.dst);
		if (i === undefined || j === undefined) continue;
		adjNodes[i].push(j);
		adjWeights[i].push(e.count);
		adjNodes[j].push(i);
		adjWeights[j].push(e.count);
	}

	const rowSum = new Float64Array(n);
	for (let i = 0; i < n; i++) {
		let s = 0;
		const w = adjWeights[i];
		for (let k = 0; k < w.length; k++) s += w[k];
		rowSum[i] = s;
	}

	adjCache = { index, builtAt, paths, idx, adjNodes, adjWeights, rowSum };
	return adjCache;
}
