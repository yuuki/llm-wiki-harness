import { describe, expect, it } from "vitest";
import Graph from "graphology";
import { ClusterInfo } from "./cluster";
import {
	ZOOM_LABEL_LIMIT,
	applyForceLabels,
	pickForceLabels,
	rankViewportLabels,
} from "./force-labels";

function cluster(id: number, size: number): ClusterInfo {
	return { id, size, label: `c${id}`, topMembers: [] };
}

function graphWith(
	nodes: {
		path: string;
		community: number;
		inCount: number;
		nodeType?: string;
	}[]
): Graph {
	const graph = new Graph({ type: "directed", multi: false });
	for (const n of nodes) {
		graph.addNode(n.path, {
			community: n.community,
			inCount: n.inCount,
			nodeType: n.nodeType ?? "concept",
			label: n.path,
		});
	}
	return graph;
}

describe("pickForceLabels", () => {
	it("labels every cluster when no limit is passed", () => {
		const nodes = [];
		const clusters: ClusterInfo[] = [];
		for (let i = 0; i < 15; i++) {
			nodes.push({ path: `n${i}`, community: i, inCount: i + 1 });
			clusters.push(cluster(i, 20 - i));
		}
		const picked = pickForceLabels(graphWith(nodes), clusters);
		expect(picked).toHaveLength(15);
	});

	it("prefers a concept over a heavier source in the same cluster", () => {
		const graph = graphWith([
			{
				path: "book",
				community: 1,
				inCount: 200,
				nodeType: "source",
			},
			{
				path: "最短路",
				community: 1,
				inCount: 4,
				nodeType: "concept",
			},
		]);
		expect(pickForceLabels(graph, [cluster(1, 2)])).toEqual(["最短路"]);
	});

	it("does not fill leftover slots when fewer clusters than the limit", () => {
		const graph = graphWith([
			{ path: "a", community: 1, inCount: 100 },
			{ path: "b", community: 1, inCount: 90 },
			{ path: "c", community: 2, inCount: 80 },
			{ path: "d", community: 3, inCount: 70 },
		]);
		const picked = pickForceLabels(graph, [
			cluster(1, 10),
			cluster(2, 5),
			cluster(3, 2),
		]);
		expect(picked).toEqual(["a", "c", "d"]);
	});

	it("walks clusters by descending size, then id", () => {
		const graph = graphWith([
			{ path: "small", community: 1, inCount: 50 },
			{ path: "big-b", community: 2, inCount: 1 },
			{ path: "big-a", community: 3, inCount: 1 },
		]);
		const picked = pickForceLabels(
			graph,
			[cluster(1, 2), cluster(2, 10), cluster(3, 10)],
			{ limit: 2 }
		);
		expect(picked).toEqual(["big-b", "big-a"]);
	});

	it("picks the max inCount member in a cluster; path asc on ties", () => {
		const graph = graphWith([
			{ path: "z-hub", community: 1, inCount: 9 },
			{ path: "a-hub", community: 1, inCount: 9 },
			{ path: "low", community: 1, inCount: 1 },
			{ path: "other", community: 2, inCount: 3 },
		]);
		const picked = pickForceLabels(graph, [cluster(1, 3), cluster(2, 1)]);
		expect(picked[0]).toBe("a-hub");
		expect(picked[1]).toBe("other");
	});

	it("skips a duplicate path and does not exceed the limit", () => {
		const graph = graphWith([{ path: "only", community: 1, inCount: 1 }]);
		const picked = pickForceLabels(graph, [cluster(1, 1), cluster(1, 1)]);
		expect(picked).toEqual(["only"]);
	});

	it("falls back to the next type rank when concepts are excluded", () => {
		const graph = graphWith([
			{
				path: "idea",
				community: 1,
				inCount: 4,
				nodeType: "concept",
			},
			{
				path: "who",
				community: 1,
				inCount: 2,
				nodeType: "entity",
			},
			{
				path: "book",
				community: 1,
				inCount: 200,
				nodeType: "source",
			},
		]);
		expect(
			pickForceLabels(graph, [cluster(1, 3)], {
				includeTypes: new Set(["entity", "source"]),
			})
		).toEqual(["who"]);
	});

	it("excludes an unknown nodeType when a filter is set", () => {
		const graph = graphWith([
			{
				path: "ghosty",
				community: 1,
				inCount: 99,
				nodeType: "ghost",
			},
			{
				path: "idea",
				community: 1,
				inCount: 1,
				nodeType: "concept",
			},
		]);
		expect(
			pickForceLabels(graph, [cluster(1, 2)], {
				includeTypes: new Set(["concept"]),
			})
		).toEqual(["idea"]);
	});

	it("returns no labels when includeTypes is empty", () => {
		const graph = graphWith([
			{ path: "idea", community: 1, inCount: 3, nodeType: "concept" },
		]);
		expect(
			pickForceLabels(graph, [cluster(1, 1)], {
				includeTypes: new Set(),
			})
		).toEqual([]);
	});

	it("drops members rejected by includeNode and picks the next best", () => {
		const graph = graphWith([
			{
				path: "future",
				community: 1,
				inCount: 20,
				nodeType: "concept",
			},
			{
				path: "present",
				community: 1,
				inCount: 3,
				nodeType: "concept",
			},
		]);
		expect(
			pickForceLabels(graph, [cluster(1, 2)], {
				includeNode: (node) => node !== "future",
			})
		).toEqual(["present"]);
	});

	it("skips a cluster that has no remaining included types", () => {
		const graph = graphWith([
			{
				path: "paper",
				community: 1,
				inCount: 10,
				nodeType: "source",
			},
			{
				path: "idea",
				community: 2,
				inCount: 3,
				nodeType: "concept",
			},
		]);
		expect(
			pickForceLabels(graph, [cluster(1, 1), cluster(2, 1)], {
				includeTypes: new Set(["concept"]),
			})
		).toEqual(["idea"]);
	});
});

describe("rankViewportLabels", () => {
	it("caps at ZOOM_LABEL_LIMIT and ranks by inCount then path", () => {
		const candidates = [];
		for (let i = 0; i < 50; i++) {
			candidates.push({ path: `n${String(i).padStart(2, "0")}`, inCount: i });
		}
		const picked = rankViewportLabels(candidates);
		expect(picked).toHaveLength(ZOOM_LABEL_LIMIT);
		expect(picked[0]).toBe("n49");
		expect(picked[1]).toBe("n48");
	});

	it("returns every candidate when the viewport has fewer than the limit", () => {
		expect(
			rankViewportLabels([
				{ path: "b", inCount: 1 },
				{ path: "a", inCount: 1 },
			])
		).toEqual(["a", "b"]);
	});

	it("dedupes paths", () => {
		expect(
			rankViewportLabels([
				{ path: "same", inCount: 3 },
				{ path: "same", inCount: 3 },
			])
		).toEqual(["same"]);
	});
});

describe("applyForceLabels", () => {
	it("sets forceLabel only on the picked paths", () => {
		const graph = graphWith([
			{ path: "keep", community: 1, inCount: 2 },
			{ path: "drop", community: 2, inCount: 1 },
		]);
		applyForceLabels(graph, ["keep"]);
		expect(graph.getNodeAttribute("keep", "forceLabel")).toBe(true);
		expect(graph.getNodeAttribute("drop", "forceLabel")).toBe(false);
	});
});
