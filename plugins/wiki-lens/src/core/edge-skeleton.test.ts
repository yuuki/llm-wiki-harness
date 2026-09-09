import { describe, expect, it } from "vitest";
import Graph from "graphology";
import {
	INTER_CAP,
	ZOOMED_TOTAL,
	applyEdgeSkeleton,
	interEdgesIn,
	selectVisibleEdges,
} from "./edge-skeleton";

function addNode(
	graph: Graph,
	id: string,
	community: number,
	nodeType = "concept"
): void {
	graph.addNode(id, { community, label: id, inCount: 1, nodeType });
}

describe("selectVisibleEdges", () => {
	it("excludes intra-cluster edges in far mode", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "a", 0);
		addNode(graph, "b", 0);
		addNode(graph, "c", 1);
		graph.addEdge("a", "b", { count: 9 });
		graph.addEdge("a", "c", { count: 2 });
		const visible = selectVisibleEdges(graph, "far");
		expect(visible.size).toBe(1);
		expect(visible.has(graph.edge("a", "c"))).toBe(true);
		expect(visible.has(graph.edge("a", "b"))).toBe(false);
	});

	it("keeps one edge per undirected cluster pair (bidirectional)", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "a", 0);
		addNode(graph, "b", 1);
		const weak = graph.addEdge("a", "b", { count: 3 });
		const strong = graph.addEdge("b", "a", { count: 5 });
		const visible = selectVisibleEdges(graph, "far");
		expect(visible.size).toBe(1);
		expect(visible.has(strong)).toBe(true);
		expect(visible.has(weak)).toBe(false);
		expect(graph.hasEdge(weak)).toBe(true);
		expect(graph.hasEdge(strong)).toBe(true);
	});

	it("caps far inter edges at INTER_CAP", () => {
		const n = 22;
		const graph = new Graph({ type: "directed", multi: false });
		for (let i = 0; i < n; i++) addNode(graph, `n${i}`, i);
		for (let i = 0; i < n; i++) {
			for (let j = i + 1; j < n; j++) {
				graph.addEdge(`n${i}`, `n${j}`, { count: 1 });
			}
		}
		const pairCount = (n * (n - 1)) / 2;
		expect(pairCount).toBeGreaterThan(INTER_CAP);
		const visible = selectVisibleEdges(graph, "far");
		expect(visible.size).toBe(INTER_CAP);
		expect(interEdgesIn(graph, visible).size).toBe(INTER_CAP);
	});

	it("uses the same inter set for far and zoomed", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "a0", 0);
		addNode(graph, "a1", 0);
		addNode(graph, "b0", 1);
		addNode(graph, "c0", 2);
		graph.addEdge("a0", "a1", { count: 8 });
		graph.addEdge("a0", "b0", { count: 4 });
		graph.addEdge("b0", "a0", { count: 1 });
		graph.addEdge("a0", "c0", { count: 2 });
		const far = selectVisibleEdges(graph, "far");
		const zoomed = selectVisibleEdges(graph, "zoomed");
		expect([...interEdgesIn(graph, far)].sort()).toEqual(
			[...interEdgesIn(graph, zoomed)].sort()
		);
		expect(zoomed.has(graph.edge("a0", "a1"))).toBe(true);
		expect(far.has(graph.edge("a0", "a1"))).toBe(false);
	});

	it("fills zoomed with intra edges without exceeding ZOOMED_TOTAL", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "x", 0);
		addNode(graph, "y", 1);
		graph.addEdge("x", "y", { count: 1 });
		for (let i = 0; i < 10; i++) {
			addNode(graph, `i${i}`, 0);
			graph.addEdge("x", `i${i}`, { count: 1 });
		}
		const zoomed = selectVisibleEdges(graph, "zoomed");
		expect(zoomed.size).toBeLessThanOrEqual(ZOOMED_TOTAL);
		expect(zoomed.size).toBe(11);
	});

	it("hard-caps zoomed at ZOOMED_TOTAL with inter reserved first", () => {
		const n = 22;
		const graph = new Graph({ type: "directed", multi: false });
		for (let i = 0; i < n; i++) addNode(graph, `n${i}`, i);
		for (let i = 0; i < n; i++) {
			for (let j = i + 1; j < n; j++) {
				graph.addEdge(`n${i}`, `n${j}`, { count: 2 });
			}
		}
		for (let i = 0; i < 700; i++) {
			addNode(graph, `intra${i}`, 0);
			graph.addEdge("n0", `intra${i}`, { count: 1 });
		}
		const far = selectVisibleEdges(graph, "far");
		const zoomed = selectVisibleEdges(graph, "zoomed");
		expect(far.size).toBe(INTER_CAP);
		expect(zoomed.size).toBe(ZOOMED_TOTAL);
		expect([...interEdgesIn(graph, far)].sort()).toEqual(
			[...interEdgesIn(graph, zoomed)].sort()
		);
		expect(interEdgesIn(graph, zoomed).size).toBe(INTER_CAP);
	});

	it("ignores edges that touch an excluded node type", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "src", 0, "source");
		addNode(graph, "idea", 1, "concept");
		addNode(graph, "other", 1, "source");
		const hidden = graph.addEdge("src", "idea", { count: 10 });
		const keep = graph.addEdge("src", "other", { count: 2 });
		const visible = selectVisibleEdges(graph, "far", {
			includeTypes: new Set(["source"]),
		});
		expect(visible.has(keep)).toBe(true);
		expect(visible.has(hidden)).toBe(false);
		expect(visible.size).toBe(1);
	});

	it("returns no edges when includeTypes is empty", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "a", 0, "concept");
		addNode(graph, "b", 1, "concept");
		graph.addEdge("a", "b", { count: 4 });
		expect(
			selectVisibleEdges(graph, "zoomed", { includeTypes: new Set() }).size
		).toBe(0);
	});

	it("fills zoomed intra only among included types", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "a", 0, "source");
		addNode(graph, "b", 0, "source");
		addNode(graph, "c", 0, "concept");
		addNode(graph, "d", 1, "source");
		const intraKeep = graph.addEdge("a", "b", { count: 8 });
		const intraDrop = graph.addEdge("a", "c", { count: 9 });
		const inter = graph.addEdge("a", "d", { count: 2 });
		const zoomed = selectVisibleEdges(graph, "zoomed", {
			includeTypes: new Set(["source"]),
		});
		expect(zoomed.has(inter)).toBe(true);
		expect(zoomed.has(intraKeep)).toBe(true);
		expect(zoomed.has(intraDrop)).toBe(false);
	});
});

describe("applyEdgeSkeleton", () => {
	it("marks non-visible edges weak/hidden without dropping them", () => {
		const graph = new Graph({ type: "directed", multi: false });
		addNode(graph, "a", 0);
		addNode(graph, "b", 1);
		addNode(graph, "c", 1);
		const keep = graph.addEdge("a", "b", { count: 2 });
		const drop = graph.addEdge("b", "c", { count: 9 });
		applyEdgeSkeleton(graph, new Set([keep]));
		expect(graph.hasEdge(keep)).toBe(true);
		expect(graph.hasEdge(drop)).toBe(true);
		expect(graph.getEdgeAttribute(keep, "hidden")).toBe(false);
		expect(graph.getEdgeAttribute(keep, "weak")).toBe(false);
		expect(graph.getEdgeAttribute(drop, "hidden")).toBe(true);
		expect(graph.getEdgeAttribute(drop, "weak")).toBe(true);
	});
});
