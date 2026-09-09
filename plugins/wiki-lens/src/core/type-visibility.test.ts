import { describe, expect, it } from "vitest";
import Graph from "graphology";
import { WIKI_PAGE_TYPES } from "./types";
import {
	edgeTouchesHiddenType,
	includeTypesFromVisible,
	isNodeTypeVisible,
	parseVisibleTypes,
	serializeVisibleTypes,
	visibleGraphStats,
} from "./type-visibility";

describe("isNodeTypeVisible", () => {
	it("shows everything when includeTypes is omitted", () => {
		expect(isNodeTypeVisible("concept")).toBe(true);
		expect(isNodeTypeVisible("ghost")).toBe(true);
		expect(isNodeTypeVisible(undefined)).toBe(true);
		expect(isNodeTypeVisible(1)).toBe(true);
	});

	it("hides unknown and non-string types when a filter is set", () => {
		const include = new Set(["concept"]);
		expect(isNodeTypeVisible("concept", include)).toBe(true);
		expect(isNodeTypeVisible("source", include)).toBe(false);
		expect(isNodeTypeVisible("ghost", include)).toBe(false);
		expect(isNodeTypeVisible("", include)).toBe(false);
		expect(isNodeTypeVisible(undefined, include)).toBe(false);
	});

	it("hides every type when includeTypes is empty", () => {
		const include = new Set<string>();
		expect(isNodeTypeVisible("concept", include)).toBe(false);
		expect(isNodeTypeVisible("source", include)).toBe(false);
		expect(isNodeTypeVisible(undefined, include)).toBe(false);
	});
});

describe("includeTypesFromVisible", () => {
	it("returns undefined when all page types are checked", () => {
		expect(includeTypesFromVisible(new Set(WIKI_PAGE_TYPES))).toBeUndefined();
	});

	it("returns the set when any page type is missing", () => {
		const visible = new Set(["source", "concept"]);
		expect(includeTypesFromVisible(visible)).toBe(visible);
	});

	it("returns the empty set instead of undefined when none are checked", () => {
		const visible = new Set<string>();
		expect(includeTypesFromVisible(visible)).toBe(visible);
	});
});

describe("edgeTouchesHiddenType", () => {
	function pair(): { graph: Graph; edge: string } {
		const graph = new Graph({ type: "directed", multi: false });
		graph.addNode("a", { nodeType: "source" });
		graph.addNode("b", { nodeType: "concept" });
		const edge = graph.addEdge("a", "b");
		return { graph, edge };
	}

	it("is false when there is no filter", () => {
		const { graph, edge } = pair();
		expect(edgeTouchesHiddenType(graph, edge)).toBe(false);
	});

	it("is true when either endpoint is excluded", () => {
		const { graph, edge } = pair();
		expect(edgeTouchesHiddenType(graph, edge, new Set(["source"]))).toBe(
			true
		);
		expect(edgeTouchesHiddenType(graph, edge, new Set(["concept"]))).toBe(
			true
		);
	});

	it("is false when both endpoints are included", () => {
		const { graph, edge } = pair();
		expect(
			edgeTouchesHiddenType(graph, edge, new Set(["source", "concept"]))
		).toBe(false);
	});
});

describe("visibleGraphStats", () => {
	function sample(): Graph {
		const graph = new Graph({ type: "directed", multi: false });
		graph.addNode("s", { nodeType: "source", community: 0 });
		graph.addNode("c", { nodeType: "concept", community: 1 });
		graph.addNode("e", { nodeType: "entity", community: 1 });
		graph.addEdge("s", "c");
		graph.addEdge("c", "e");
		return graph;
	}

	it("counts the full graph when unfiltered", () => {
		expect(visibleGraphStats(sample())).toEqual({
			nodes: 3,
			edges: 2,
			clusters: 2,
		});
	});

	it("drops hidden-type nodes, edges, and empty clusters", () => {
		expect(visibleGraphStats(sample(), new Set(["concept"]))).toEqual({
			nodes: 1,
			edges: 0,
			clusters: 1,
		});
	});

	it("is empty when includeTypes is empty", () => {
		expect(visibleGraphStats(sample(), new Set())).toEqual({
			nodes: 0,
			edges: 0,
			clusters: 0,
		});
	});
});

describe("parseVisibleTypes / serializeVisibleTypes", () => {
	it("falls back to every page type when the payload is missing or empty", () => {
		expect([...parseVisibleTypes(undefined)]).toEqual([...WIKI_PAGE_TYPES]);
		expect([...parseVisibleTypes(null)]).toEqual([...WIKI_PAGE_TYPES]);
		expect([...parseVisibleTypes({})]).toEqual([...WIKI_PAGE_TYPES]);
		expect([...parseVisibleTypes([])]).toEqual([...WIKI_PAGE_TYPES]);
	});

	it("keeps only known page types", () => {
		expect(
			serializeVisibleTypes(
				parseVisibleTypes(["concept", "ghost", "source"])
			)
		).toEqual(["source", "concept"]);
	});

	it("serializes in WIKI_PAGE_TYPES order", () => {
		expect(
			serializeVisibleTypes(new Set(["question", "source"]))
		).toEqual(["source", "question"]);
	});
});
