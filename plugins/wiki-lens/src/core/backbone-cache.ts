import Graph from "graphology";
import { circular } from "graphology-layout";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { ClusterInfo, computeClusters } from "./cluster";
import { BackboneOptions, buildBackbone } from "./filters";
import { GraphIndex } from "./graph-index";
import { assignCommunities, nodeSize, nodeWeight } from "./metrics";

export function backboneOptsKey(opts: BackboneOptions): string {
	return `${opts.includeSeedEntities ? 1 : 0}:${opts.dropIsolated ? 1 : 0}`;
}

export interface BackboneReady {
	builtAt: number;
	opts: BackboneOptions;
	optsKey: string;
}

export interface BackboneHandle {
	builtAt: number;
	opts: BackboneOptions;
	optsKey: string;
	/** Caller-owned copy. Safe to color and hand to sigma. */
	graph: Graph;
	clusters: ClusterInfo[];
	laidOut: boolean;
}

interface Entry {
	builtAt: number;
	opts: BackboneOptions;
	working: Graph;
	clusters: ClusterInfo[];
	laidOut: boolean;
	layoutFrame: number | null;
}

/**
 * Shared backbone + Louvain + ForceAtlas2. Graph / Gap / Time / 3D each used
 * to rebuild this independently; one chunked FA2 loop now serves all of them.
 * Layout still runs 5 iterations per animation frame so the UI stays responsive.
 */
export class BackboneCache {
	private entries = new Map<string, Entry>();
	private listeners = new Set<(ready: BackboneReady) => void>();

	onReady(cb: (ready: BackboneReady) => void): () => void {
		this.listeners.add(cb);
		return () => {
			this.listeners.delete(cb);
		};
	}

	invalidate(): void {
		for (const entry of this.entries.values()) this.stop(entry);
		this.entries.clear();
	}

	destroy(): void {
		this.invalidate();
		this.listeners.clear();
	}

	acquire(index: GraphIndex, opts: BackboneOptions): BackboneHandle | null {
		if (!index.built || !index.stats) return null;
		const builtAt = index.stats.builtAt;
		const key = backboneOptsKey(opts);
		let entry = this.entries.get(key);
		if (!entry || entry.builtAt !== builtAt) {
			if (entry) this.stop(entry);
			entry = this.buildEntry(index, opts, builtAt);
			this.entries.set(key, entry);
			if (!entry.laidOut) this.runLayout(entry);
		}
		return {
			builtAt,
			opts: entry.opts,
			optsKey: key,
			graph: entry.working.copy(),
			clusters: entry.clusters,
			laidOut: entry.laidOut,
		};
	}

	/** Copy current working (x, y) onto dest nodes that exist in both graphs. */
	applyPositions(opts: BackboneOptions, dest: Graph): void {
		const entry = this.entries.get(backboneOptsKey(opts));
		if (!entry) return;
		const working = entry.working;
		dest.updateEachNodeAttributes(
			(node, attrs) => {
				if (!working.hasNode(node)) return attrs;
				attrs["x"] = working.getNodeAttribute(node, "x");
				attrs["y"] = working.getNodeAttribute(node, "y");
				return attrs;
			},
			{ attributes: ["x", "y"] }
		);
	}

	private buildEntry(
		index: GraphIndex,
		opts: BackboneOptions,
		builtAt: number
	): Entry {
		const graph = buildBackbone(index, opts);
		assignCommunities(graph);
		graph.updateEachNodeAttributes((_, attrs) => {
			attrs["size"] = nodeSize(nodeWeight(attrs));
			return attrs;
		});
		if (graph.order > 0) circular.assign(graph, { scale: 500 });
		return {
			builtAt,
			opts: { ...opts },
			working: graph,
			clusters: computeClusters(graph),
			laidOut: graph.order === 0,
			layoutFrame: null,
		};
	}

	private runLayout(entry: Entry): void {
		const graph = entry.working;
		this.stop(entry);
		const settings = forceAtlas2.inferSettings(graph);
		const totalIterations = Math.min(
			600,
			150 + Math.floor(graph.order / 10)
		);
		let done = 0;
		const step = () => {
			forceAtlas2.assign(graph, { iterations: 5, settings });
			done += 5;
			if (done < totalIterations) {
				entry.layoutFrame = window.requestAnimationFrame(step);
			} else {
				entry.layoutFrame = null;
				entry.laidOut = true;
				this.emit(entry);
			}
		};
		entry.layoutFrame = window.requestAnimationFrame(step);
	}

	private emit(entry: Entry): void {
		const ready: BackboneReady = {
			builtAt: entry.builtAt,
			opts: entry.opts,
			optsKey: backboneOptsKey(entry.opts),
		};
		for (const cb of this.listeners) cb(ready);
	}

	private stop(entry: Entry): void {
		if (entry.layoutFrame !== null) {
			window.cancelAnimationFrame(entry.layoutFrame);
			entry.layoutFrame = null;
		}
	}
}
