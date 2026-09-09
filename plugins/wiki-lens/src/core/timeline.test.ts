import { describe, expect, it } from "vitest";
import Graph from "graphology";
import { INTER_CAP } from "./edge-skeleton";
import {
	buildTimeline,
	countSeries,
	selectNewEdges,
	timeEdgeShown,
} from "./timeline";
import { NodeMeta, TimeFilterContext, WikiPageType, WikiStatus } from "./types";

function graphWith(
	nodes: { id: string; nodeType?: string }[],
	edges: { source: string; target: string; bornDay: number; count?: number }[]
): Graph {
	const graph = new Graph({ type: "directed", multi: false });
	for (const n of nodes) {
		graph.addNode(n.id, { nodeType: n.nodeType ?? "concept" });
	}
	for (const e of edges) {
		graph.addEdge(e.source, e.target, {
			bornDay: e.bornDay,
			count: e.count ?? 1,
		});
	}
	return graph;
}

const replay: TimeFilterContext = {
	mode: "replay",
	t: 10,
	recentWindow: 3,
};

describe("selectNewEdges", () => {
	it("returns every new edge when under the cap", () => {
		const graph = graphWith(
			[{ id: "a" }, { id: "b" }, { id: "c" }],
			[
				{ source: "a", target: "b", bornDay: 9 },
				{ source: "b", target: "c", bornDay: 8 },
				{ source: "a", target: "c", bornDay: 1 },
			]
		);
		const visible = selectNewEdges(graph, replay);
		expect(visible.size).toBe(2);
		expect(visible.has(graph.edge("a", "b"))).toBe(true);
		expect(visible.has(graph.edge("b", "c"))).toBe(true);
		expect(visible.has(graph.edge("a", "c"))).toBe(false);
	});

	it("keeps the highest-count new edges when over the cap", () => {
		const nodes = [{ id: "hub" }];
		const edges: { source: string; target: string; bornDay: number; count?: number }[] = [];
		for (let i = 0; i < INTER_CAP + 5; i++) {
			nodes.push({ id: `n${i}` });
			edges.push({
				source: "hub",
				target: `n${i}`,
				bornDay: 10,
				count: i + 1,
			});
		}
		const graph = graphWith(nodes, edges);
		const visible = selectNewEdges(graph, replay);
		expect(visible.size).toBe(INTER_CAP);
		expect(visible.has(graph.edge("hub", `n${INTER_CAP + 4}`))).toBe(true);
		expect(visible.has(graph.edge("hub", "n0"))).toBe(false);
	});

	it("returns every new edge when limit is null", () => {
		const nodes = [{ id: "hub" }];
		const edges: { source: string; target: string; bornDay: number; count?: number }[] = [];
		for (let i = 0; i < INTER_CAP + 5; i++) {
			nodes.push({ id: `n${i}` });
			edges.push({
				source: "hub",
				target: `n${i}`,
				bornDay: 10,
				count: 1,
			});
		}
		const graph = graphWith(nodes, edges);
		expect(selectNewEdges(graph, replay, { limit: null }).size).toBe(
			INTER_CAP + 5
		);
	});

	it("caps by default so Diff full-range cannot fog the graph", () => {
		const nodes = [{ id: "hub" }];
		const edges: { source: string; target: string; bornDay: number; count?: number }[] = [];
		for (let i = 0; i < INTER_CAP + 5; i++) {
			nodes.push({ id: `n${i}` });
			edges.push({
				source: "hub",
				target: `n${i}`,
				bornDay: 10,
				count: 1,
			});
		}
		const graph = graphWith(nodes, edges);
		expect(selectNewEdges(graph, replay).size).toBe(INTER_CAP);
	});

	it("skips edges that touch a hidden type", () => {
		const graph = graphWith(
			[
				{ id: "c", nodeType: "concept" },
				{ id: "s", nodeType: "source" },
			],
			[{ source: "c", target: "s", bornDay: 10, count: 9 }]
		);
		const visible = selectNewEdges(graph, replay, {
			includeTypes: new Set(["concept"]),
		});
		expect(visible.size).toBe(0);
	});
});

describe("timeEdgeShown", () => {
	const skeleton = new Set(["sk"]);
	const accent = new Set(["fresh"]);

	it("hides time-hidden edges even if they sit on the skeleton", () => {
		expect(timeEdgeShown("hidden", "sk", skeleton, accent)).toBe(false);
	});

	it("shows skeleton edges that already exist", () => {
		expect(timeEdgeShown("existing", "sk", skeleton, accent)).toBe(true);
	});

	it("shows accent new edges that applyEdgeSkeleton marked hidden", () => {
		expect(timeEdgeShown("new", "fresh", skeleton, accent)).toBe(true);
	});

	it("hides new edges that missed both the skeleton and the accent cap", () => {
		expect(timeEdgeShown("new", "other", skeleton, accent)).toBe(false);
	});
});

function page(opts: {
	path: string;
	type: WikiPageType;
	created: string;
	status?: WikiStatus;
	updated?: string;
}): NodeMeta {
	return {
		path: opts.path,
		basename: opts.path.split("/").pop() ?? opts.path,
		title: opts.path,
		type: opts.type,
		status: opts.status ?? "developing",
		subType: "",
		isLintStub: false,
		created: opts.created,
		updated: opts.updated ?? "",
		inDeg: 0,
		outDeg: 0,
		inCount: 0,
		outCount: 0,
		outsideRefs: 0,
	};
}

function indexOf(nodes: NodeMeta[]): { nodes: Map<string, NodeMeta> } {
	return { nodes: new Map(nodes.map((n) => [n.path, n])) };
}

describe("countSeries", () => {
	it("splits cumulative counts by type along model.days", () => {
		const index = indexOf([
			page({
				path: "wiki/sources/@a.md",
				type: "source",
				created: "2026-01-01",
			}),
			page({
				path: "wiki/entities/E.md",
				type: "entity",
				created: "2026-01-02",
			}),
			page({
				path: "wiki/concepts/C.md",
				type: "concept",
				created: "2026-01-02",
			}),
			page({
				path: "wiki/questions/Q.md",
				type: "question",
				created: "2026-01-04",
			}),
		]);
		const model = buildTimeline(index);
		const series = countSeries(index, model);

		expect(model.days).toHaveLength(3);
		expect(series.total).toEqual([1, 3, 4]);
		expect(series.byType.source).toEqual([1, 1, 1]);
		expect(series.byType.entity).toEqual([0, 1, 1]);
		expect(series.byType.concept).toEqual([0, 1, 1]);
		expect(series.byType.question).toEqual([0, 0, 1]);
		expect(series.dailyTotal).toEqual([1, 2, 1]);
		expect(series.dailyByType.concept).toEqual([0, 1, 0]);
		expect(series.dailyByType.question).toEqual([0, 0, 1]);
		expect(series.total).toHaveLength(model.days.length);
		expect(
			series.total.map(
				(_t, i) =>
					series.byType.source[i] +
					series.byType.entity[i] +
					series.byType.concept[i] +
					series.byType.question[i]
			)
		).toEqual(series.total);
		expect(series.unknownCreatedCount).toBe(0);
	});

	it("drops seed entities when excludeSeedEntities is set", () => {
		const index = indexOf([
			page({
				path: "wiki/entities/Seed.md",
				type: "entity",
				created: "2026-01-01",
				status: "seed",
			}),
			page({
				path: "wiki/entities/Mature.md",
				type: "entity",
				created: "2026-01-02",
				status: "mature",
			}),
			page({
				path: "wiki/concepts/C.md",
				type: "concept",
				created: "2026-01-02",
			}),
		]);
		const model = buildTimeline(index);
		const all = countSeries(index, model);
		const pruned = countSeries(index, model, { excludeSeedEntities: true });

		expect(all.byType.entity).toEqual([1, 2]);
		expect(all.total).toEqual([1, 3]);
		expect(pruned.byType.entity).toEqual([0, 1]);
		expect(pruned.total).toEqual([0, 2]);
		expect(pruned.byType.concept).toEqual(all.byType.concept);
	});

	it("counts unknown created as the opening step on the first day", () => {
		const index = indexOf([
			page({
				path: "wiki/concepts/Old.md",
				type: "concept",
				created: "",
			}),
			page({
				path: "wiki/concepts/New.md",
				type: "concept",
				created: "2026-03-10",
			}),
			page({
				path: "wiki/sources/@later.md",
				type: "source",
				created: "2026-03-11",
			}),
		]);
		const model = buildTimeline(index);
		expect(model.unknownCreatedCount).toBe(1);
		const series = countSeries(index, model);

		expect(series.total[0]).toBe(2);
		expect(series.byType.concept[0]).toBe(2);
		expect(series.dailyTotal[0]).toBe(2);
		expect(series.dailyTotal[1]).toBe(1);
		expect(series.total[series.total.length - 1]).toBe(3);
	});

	it("stays flat on update-only days so the playhead index still matches", () => {
		const index = indexOf([
			page({
				path: "wiki/concepts/C.md",
				type: "concept",
				created: "2026-01-01",
				updated: "2026-01-05",
			}),
		]);
		const model = buildTimeline(index);
		const series = countSeries(index, model);

		expect(model.days).toHaveLength(2);
		expect(series.total).toEqual([1, 1]);
		expect(series.dailyTotal).toEqual([1, 0]);
	});

	it("returns empty series when the index has no dated pages", () => {
		const index = indexOf([]);
		const model = buildTimeline(index);
		const series = countSeries(index, model);
		expect(model.days).toEqual([]);
		expect(series.total).toEqual([]);
		expect(series.dailyTotal).toEqual([]);
		expect(series.unknownCreatedCount).toBe(0);
	});

	it("can drop every node when the index is only seed entities", () => {
		const index = indexOf([
			page({
				path: "wiki/entities/Seed.md",
				type: "entity",
				created: "2026-01-01",
				status: "seed",
			}),
		]);
		const model = buildTimeline(index);
		const series = countSeries(index, model, { excludeSeedEntities: true });
		expect(series.total).toEqual([0]);
		expect(series.byType.entity).toEqual([0]);
		expect(series.unknownCreatedCount).toBe(0);
	});

	it("does not count unknown-created seeds in the footnote population", () => {
		const index = indexOf([
			page({
				path: "wiki/entities/Seed.md",
				type: "entity",
				created: "",
				status: "seed",
			}),
			page({
				path: "wiki/concepts/C.md",
				type: "concept",
				created: "2026-04-01",
			}),
		]);
		const model = buildTimeline(index);
		expect(model.unknownCreatedCount).toBe(1);
		const all = countSeries(index, model);
		const pruned = countSeries(index, model, { excludeSeedEntities: true });
		expect(all.unknownCreatedCount).toBe(1);
		expect(pruned.unknownCreatedCount).toBe(0);
		expect(pruned.total[0]).toBe(1);
	});

	it("keeps isolated non-seed pages after excluding seeds", () => {
		const index = indexOf([
			page({
				path: "wiki/concepts/Orphan.md",
				type: "concept",
				created: "2026-01-01",
			}),
			page({
				path: "wiki/entities/Seed.md",
				type: "entity",
				created: "2026-01-01",
				status: "seed",
			}),
		]);
		const model = buildTimeline(index);
		const pruned = countSeries(index, model, { excludeSeedEntities: true });
		expect(pruned.total).toEqual([1]);
		expect(pruned.byType.concept).toEqual([1]);
	});
});
