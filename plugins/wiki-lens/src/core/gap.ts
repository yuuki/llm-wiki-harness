import Graph from "graphology";
import { GraphIndex } from "./graph-index";
import { ClusterInfo } from "./cluster";

export interface ClusterGap {
	a: number; b: number;            // cluster ids
	labelA: string; labelB: string;  // from ClusterInfo.label
	sizeA: number; sizeB: number;
	actual: number;    // actual inter-cluster edge weight (undirected, count-weighted)
	expected: number;  // configuration-model expectation
	ratio: number;     // actual / expected (0 when no edges)
}

export interface ConceptPairGap {
	a: string; b: string;            // concept node paths
	titleA: string; titleB: string;
	commonSources: string[];         // paths of sources linking to BOTH concepts
	clusterA: number; clusterB: number;
	crossCluster: boolean;
}

export interface PredictedLink {
	a: string; b: string;
	titleA: string; titleB: string;
	score: number;                   // Adamic-Adar
	clusterA: number; clusterB: number;
	crossCluster: boolean;
}

export interface BridgeCandidate {
	target: string;                  // unresolved link target (missing @-prefixed source)
	refs: number;                    // total reference count
	clusters: number[];              // distinct backbone cluster ids among referrers
	referrers: string[];             // ALL referrer paths (even those not in the backbone)
	score: number;
}

export interface GapAnalysis {
	clusterGaps: ClusterGap[];
	conceptPairs: ConceptPairGap[];
	predictedLinks: PredictedLink[];
	bridges: BridgeCandidate[];
	notes: string[];                 // coverage caps applied (e.g. skipped hub sources) — never silently truncate
}

export interface GapOptions {
	minClusterSize: number;          // 5
	maxClusterPairs: number;         // 30
	minCommonSources: number;        // 3
	maxConceptPairs: number;         // 50
	maxPredictedLinks: number;       // 50
	predictCrossClusterOnly: boolean;// true
	minBridgeClusters: number;       // 2
	maxBridges: number;              // 50
	maxSourceFanout: number;         // 60
}

export const DEFAULT_GAP_OPTIONS: GapOptions = {
	minClusterSize: 5,
	maxClusterPairs: 30,
	minCommonSources: 3,
	maxConceptPairs: 50,
	maxPredictedLinks: 50,
	predictCrossClusterOnly: true,
	// Empirically, missing sources are almost always cited from a single
	// community (their referrers are the paper's author stubs plus one source),
	// so 1 keeps the tab a demand-ranked ingest queue; genuinely cross-cluster
	// candidates outrank via the breadth-weighted score when they appear.
	minBridgeClusters: 1,
	maxBridges: 50,
	maxSourceFanout: 60,
};

// Null byte can never occur in a vault path, so it is a safe key separator for
// canonicalizing unordered path pairs without risk of collision.
const SEP = "\0";

/** Canonical, order-independent key for an unordered pair of node paths. */
function pathKey(a: string, b: string): string {
	return a < b ? a + SEP + b : b + SEP + a;
}

/** Canonical key for an unordered pair of cluster ids. */
function clusterKey(a: number, b: number): string {
	return a < b ? `${a}|${b}` : `${b}|${a}`;
}

/**
 * Compute the four structural gap signals over the backbone link graph. The
 * backbone is treated as undirected throughout: two thematic pages linked in
 * either direction are "connected", so directionality only distorts the
 * topology-driven gap heuristics. The backbone must already carry "community"
 * (louvain) node attributes; see assignCommunities.
 */
export function analyzeGaps(
	index: GraphIndex,
	backbone: Graph,
	clusters: ClusterInfo[],
	opts: GapOptions
): GapAnalysis {
	const notes: string[] = [];

	// Per-node attributes snapshot (avoids repeated graphology attribute lookups
	// in the inner loops below).
	const community = new Map<string, number>();
	const nodeType = new Map<string, string>();
	const label = new Map<string, string>();
	backbone.forEachNode((node, attrs) => {
		community.set(node, (attrs["community"] as number) ?? 0);
		nodeType.set(node, (attrs["nodeType"] as string) ?? "");
		label.set(node, (attrs["label"] as string) ?? node);
	});

	// Single undirected pass over the backbone: total weight m, weighted degrees,
	// unweighted adjacency (distinct neighbor sets), and inter-cluster weights.
	let m = 0;
	const wdeg = new Map<string, number>();
	const adj = new Map<string, Set<string>>();
	const interWeight = new Map<string, number>();
	const link = (a: string, b: string) => {
		let set = adj.get(a);
		if (!set) { set = new Set<string>(); adj.set(a, set); }
		set.add(b);
	};
	backbone.forEachEdge((_edge, attrs, source, target) => {
		const c = (attrs["count"] as number) ?? 1;
		m += c;
		wdeg.set(source, (wdeg.get(source) ?? 0) + c);
		wdeg.set(target, (wdeg.get(target) ?? 0) + c);
		link(source, target);
		link(target, source);
		const cs = community.get(source) ?? 0;
		const ct = community.get(target) ?? 0;
		if (cs !== ct) {
			const key = clusterKey(cs, ct);
			interWeight.set(key, (interWeight.get(key) ?? 0) + c);
		}
	});

	// Full-index adjacency (either direction). Used as a stricter "already
	// linked" mask than the backbone, so co-cited/predicted pairs that are
	// connected only outside the pruned backbone are still excluded.
	const fullPairs = new Set<string>();
	for (const e of index.edges) fullPairs.add(pathKey(e.src, e.dst));

	const clusterGaps = computeClusterGaps(clusters, community, wdeg, interWeight, m, opts, notes);
	const conceptPairs = computeConceptPairs(backbone, adj, nodeType, label, community, fullPairs, opts, notes);
	const predictedLinks = computePredictedLinks(backbone, adj, label, community, fullPairs, conceptPairs, opts, notes);
	const bridges = computeBridges(index, community, opts, notes);

	return { clusterGaps, conceptPairs, predictedLinks, bridges, notes };
}

/**
 * Inter-cluster thinness against a configuration-model null. Expected weight for
 * a cluster pair is E = S_A * S_B / (2m) (degree-preserving random rewiring);
 * a low actual/expected ratio flags two established topics that "should" be
 * wired together but are not. Zero-edge pairs are the most anomalous, so they
 * are kept (ratio 0) as long as the expected weight is non-trivial.
 */
function computeClusterGaps(
	clusters: ClusterInfo[],
	community: Map<string, number>,
	wdeg: Map<string, number>,
	interWeight: Map<string, number>,
	m: number,
	opts: GapOptions,
	notes: string[]
): ClusterGap[] {
	if (m === 0) return [];

	// Weighted strength S_X per cluster = sum of members' weighted degrees.
	const strength = new Map<number, number>();
	for (const [node, c] of community) {
		strength.set(c, (strength.get(c) ?? 0) + (wdeg.get(node) ?? 0));
	}

	const eligible = clusters.filter((c) => c.size >= opts.minClusterSize);
	const gaps: ClusterGap[] = [];
	for (let i = 0; i < eligible.length; i++) {
		for (let j = i + 1; j < eligible.length; j++) {
			const A = eligible[i];
			const B = eligible[j];
			const expected = ((strength.get(A.id) ?? 0) * (strength.get(B.id) ?? 0)) / (2 * m);
			// Below ~2 expected edges, "thinness" is indistinguishable from noise.
			if (expected < 2) continue;
			const actual = interWeight.get(clusterKey(A.id, B.id)) ?? 0;
			gaps.push({
				a: A.id, b: B.id,
				labelA: A.label, labelB: B.label,
				sizeA: A.size, sizeB: B.size,
				actual,
				expected,
				ratio: actual / expected,
			});
		}
	}

	// Ascending ratio (thinnest first); bigger expected breaks ties, since a
	// large predicted-but-absent link is the more surprising gap.
	gaps.sort((x, y) => (x.ratio - y.ratio) || (y.expected - x.expected));
	return cap(gaps, opts.maxClusterPairs, "cluster gaps", notes);
}

/**
 * Bipartite co-citation coupling: concept pairs reached from the same sources
 * (undirected) yet with no direct link. This is the strongest purely-local
 * proxy for "these two concepts are related" — sources act as human-authored
 * evidence of relatedness.
 */
function computeConceptPairs(
	backbone: Graph,
	adj: Map<string, Set<string>>,
	nodeType: Map<string, string>,
	label: Map<string, string>,
	community: Map<string, number>,
	fullPairs: Set<string>,
	opts: GapOptions,
	notes: string[]
): ConceptPairGap[] {
	const pairs = new Map<string, { a: string; b: string; sources: string[] }>();
	let skippedHubSources = 0;

	backbone.forEachNode((node) => {
		if (nodeType.get(node) !== "source") return;
		const concepts: string[] = [];
		for (const nb of adj.get(node) ?? []) {
			if (nodeType.get(nb) === "concept") concepts.push(nb);
		}
		// A hub source touching every concept contributes O(fanout^2) noise pairs
		// and drowns the signal; skip and account for it rather than truncate.
		if (concepts.length > opts.maxSourceFanout) { skippedHubSources++; return; }
		concepts.sort();
		for (let i = 0; i < concepts.length; i++) {
			for (let j = i + 1; j < concepts.length; j++) {
				const key = concepts[i] + SEP + concepts[j];
				let entry = pairs.get(key);
				if (!entry) { entry = { a: concepts[i], b: concepts[j], sources: [] }; pairs.set(key, entry); }
				entry.sources.push(node);
			}
		}
	});
	if (skippedHubSources > 0) {
		notes.push(`co-citation: skipped ${skippedHubSources} hub sources (> ${opts.maxSourceFanout} concepts)`);
	}

	const result: ConceptPairGap[] = [];
	for (const entry of pairs.values()) {
		if (entry.sources.length < opts.minCommonSources) continue;
		if (fullPairs.has(pathKey(entry.a, entry.b))) continue;
		const clusterA = community.get(entry.a) ?? -1;
		const clusterB = community.get(entry.b) ?? -1;
		result.push({
			a: entry.a, b: entry.b,
			titleA: label.get(entry.a) ?? entry.a,
			titleB: label.get(entry.b) ?? entry.b,
			commonSources: entry.sources,
			clusterA, clusterB,
			crossCluster: clusterA !== clusterB,
		});
	}

	// Cross-cluster pairs first (they bridge otherwise separate topics), then by
	// strength of the co-citation evidence.
	result.sort((x, y) =>
		Number(y.crossCluster) - Number(x.crossCluster) ||
		y.commonSources.length - x.commonSources.length
	);
	return cap(result, opts.maxConceptPairs, "concept pairs", notes);
}

/**
 * Adamic-Adar link prediction on the undirected backbone. Each common neighbor
 * w of a pair contributes 1/log2(deg(w)), so rare shared neighbors weigh more
 * than hubs. Enumerated by intermediate node (for each w, over pairs of its
 * neighbors) which is far cheaper than all-pairs. High-degree intermediates are
 * skipped to bound the O(deg^2) inner loop.
 */
function computePredictedLinks(
	backbone: Graph,
	adj: Map<string, Set<string>>,
	label: Map<string, string>,
	community: Map<string, number>,
	fullPairs: Set<string>,
	conceptPairs: ConceptPairGap[],
	opts: GapOptions,
	notes: string[]
): PredictedLink[] {
	const scores = new Map<string, { a: string; b: string; score: number }>();
	let skippedHubNodes = 0;

	backbone.forEachNode((w) => {
		const nbrSet = adj.get(w);
		if (!nbrSet) return;
		const d = nbrSet.size;
		if (d < 2) return;
		if (d > 150) { skippedHubNodes++; return; }
		const contrib = 1 / Math.log2(d);
		const nbrs = Array.from(nbrSet).sort();
		for (let i = 0; i < nbrs.length; i++) {
			for (let j = i + 1; j < nbrs.length; j++) {
				const key = nbrs[i] + SEP + nbrs[j];
				const entry = scores.get(key);
				if (entry) entry.score += contrib;
				else scores.set(key, { a: nbrs[i], b: nbrs[j], score: contrib });
			}
		}
	});
	if (skippedHubNodes > 0) {
		notes.push(`adamic-adar: skipped ${skippedHubNodes} hub intermediates (degree > 150)`);
	}

	// Pairs already surfaced as co-citation gaps are dropped to avoid duplicate
	// recommendations across the two signals.
	const alreadyReported = new Set<string>();
	for (const p of conceptPairs) alreadyReported.add(pathKey(p.a, p.b));

	const result: PredictedLink[] = [];
	for (const entry of scores.values()) {
		const key = pathKey(entry.a, entry.b);
		if (fullPairs.has(key)) continue;
		if (alreadyReported.has(key)) continue;
		const clusterA = community.get(entry.a) ?? -1;
		const clusterB = community.get(entry.b) ?? -1;
		const crossCluster = clusterA !== clusterB;
		if (opts.predictCrossClusterOnly && !crossCluster) continue;
		result.push({
			a: entry.a, b: entry.b,
			titleA: label.get(entry.a) ?? entry.a,
			titleB: label.get(entry.b) ?? entry.b,
			score: entry.score,
			clusterA, clusterB,
			crossCluster,
		});
	}

	result.sort((x, y) => y.score - x.score);
	return cap(result, opts.maxPredictedLinks, "predicted links", notes);
}

/**
 * Missing @-prefixed sources already cited from multiple clusters. A dead source
 * target referenced across distinct thematic communities is literature the vault
 * demonstrably wants but has not yet created — a fully-local recommendation.
 * Referrers are frequently seed entity stubs (author pages) that the backbone
 * excludes, so a referrer without its own community is attributed the majority
 * community among its full-index neighbors that do have one. Referrers that
 * can't be attributed still count toward refs and stay in referrers.
 */
function computeBridges(
	index: GraphIndex,
	community: Map<string, number>,
	opts: GapOptions,
	notes: string[]
): BridgeCandidate[] {
	// Full-index undirected adjacency, built lazily on the first referrer that
	// needs neighbor-based community attribution.
	let indexAdj: Map<string, string[]> | null = null;
	const buildIndexAdj = (): Map<string, string[]> => {
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
		return adj;
	};
	const attributed = new Map<string, number | undefined>();
	const communityOf = (r: string): number | undefined => {
		const own = community.get(r);
		if (own !== undefined) return own;
		if (attributed.has(r)) return attributed.get(r);
		if (!indexAdj) indexAdj = buildIndexAdj();
		const votes = new Map<number, number>();
		for (const nb of indexAdj.get(r) ?? []) {
			const c = community.get(nb);
			if (c === undefined) continue;
			votes.set(c, (votes.get(c) ?? 0) + 1);
		}
		let best: number | undefined;
		let bestCount = 0;
		for (const [c, n] of votes) {
			if (n > bestCount) { best = c; bestCount = n; }
		}
		attributed.set(r, best);
		return best;
	};

	const result: BridgeCandidate[] = [];
	let unattributed = 0;
	for (const dt of index.deadTargets.values()) {
		if (dt.cause !== "missing-source") continue;
		const clusterSet = new Set<number>();
		for (const r of dt.referrers) {
			const c = communityOf(r);
			if (c !== undefined) clusterSet.add(c);
		}
		if (clusterSet.size === 0) unattributed++;
		if (clusterSet.size < opts.minBridgeClusters) continue;
		result.push({
			target: dt.target,
			refs: dt.refs,
			clusters: Array.from(clusterSet).sort((a, b) => a - b),
			referrers: dt.referrers.slice(),
			// Reward breadth (distinct clusters) more than raw citation volume.
			score: clusterSet.size * Math.log2(1 + dt.refs),
		});
	}

	if (unattributed > 0) {
		notes.push(
			`bridges: ${unattributed} missing sources excluded (referrers have no attributable cluster; see the Health view's dead-link table for the full list)`
		);
	}

	result.sort((x, y) => y.score - x.score);
	return cap(result, opts.maxBridges, "bridges", notes);
}

/**
 * Truncate to a cap, recording a coverage note when items are dropped so the
 * caller never silently loses candidates.
 */
function cap<T>(items: T[], max: number, name: string, notes: string[]): T[] {
	if (items.length > max) {
		notes.push(`${name}: showing top ${max} of ${items.length}`);
		return items.slice(0, max);
	}
	return items;
}

/** Markdown report for clipboard export (consumed by an LLM skill, so structure > strict format). */
export function buildGapReport(analysis: GapAnalysis, generatedAt: string): string {
	const lines: string[] = [];
	lines.push("# Wiki gap report");
	lines.push("");
	lines.push(`Generated: ${generatedAt}`);
	lines.push("");
	lines.push(
		"All items below are *computed suggestions*: connections the link structure implies but that do **not** currently exist as links. Treat them as candidates to verify, not as facts."
	);

	lines.push("");
	lines.push("## Cluster gaps");
	lines.push(
		"Pairs of thematic clusters wired together far less than a configuration-model null predicts (low actual/expected). Candidates for a missing bridge between two established topics."
	);
	if (analysis.clusterGaps.length === 0) {
		lines.push("None.");
	} else {
		lines.push("");
		lines.push("| Cluster A | Cluster B | actual | expected | ratio |");
		lines.push("| --- | --- | ---: | ---: | ---: |");
		for (const g of analysis.clusterGaps) {
			lines.push(
				`| ${g.labelA} (#${g.a}, ${g.sizeA}) | ${g.labelB} (#${g.b}, ${g.sizeB}) | ${g.actual} | ${g.expected.toFixed(2)} | ${g.ratio.toFixed(2)} |`
			);
		}
	}

	lines.push("");
	lines.push("## Concept co-citation pairs");
	lines.push(
		"Concept pairs reached from the same sources (bibliographic coupling) yet with no direct link. The strongest local evidence that two concepts are related."
	);
	if (analysis.conceptPairs.length === 0) {
		lines.push("None.");
	} else {
		lines.push("");
		for (const p of analysis.conceptPairs) {
			const cross = p.crossCluster ? " [cross-cluster]" : "";
			lines.push(
				`- ${p.titleA} (${p.a}) — ${p.titleB} (${p.b}): ${p.commonSources.length} common sources${cross}`
			);
			for (const src of p.commonSources) {
				lines.push(`  - ${src}`);
			}
		}
	}

	lines.push("");
	lines.push("## Predicted links (Adamic-Adar)");
	lines.push(
		"Node pairs with many rare shared neighbors on the backbone, so structurally likely to be linked but currently unlinked."
	);
	if (analysis.predictedLinks.length === 0) {
		lines.push("None.");
	} else {
		lines.push("");
		for (const p of analysis.predictedLinks) {
			const cross = p.crossCluster ? " [cross-cluster]" : "";
			lines.push(
				`- ${p.titleA} (${p.a}) — ${p.titleB} (${p.b}): score ${p.score.toFixed(2)}${cross}`
			);
		}
	}

	lines.push("");
	lines.push("## Bridge candidates");
	lines.push(
		"Uncreated @-prefixed sources already cited from one or more clusters — literature the vault wants but has not yet created. Default ranking is demand (refs) with breadth as a tie-breaker."
	);
	if (analysis.bridges.length === 0) {
		lines.push("None.");
	} else {
		lines.push("");
		for (const b of analysis.bridges) {
			lines.push(
				`- ${b.target}: ${b.refs} refs · clusters ${b.clusters.join(", ") || "—"} · score ${b.score.toFixed(2)}`
			);
			for (const r of b.referrers) {
				lines.push(`  - ${r}`);
			}
		}
	}

	lines.push("");
	lines.push("## Coverage notes");
	if (analysis.notes.length === 0) {
		lines.push("No caps applied; all computed candidates are shown.");
	} else {
		for (const n of analysis.notes) lines.push(`- ${n}`);
	}

	lines.push("");
	return lines.join("\n");
}
