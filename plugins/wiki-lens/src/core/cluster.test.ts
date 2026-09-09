import { describe, expect, it } from "vitest";
import Graph from "graphology";
import {
	clusterNameRank,
	clusterOverviewLabel,
	computeClusters,
} from "./cluster";

function graphWith(
	nodes: {
		path: string;
		community: number;
		inCount: number;
		nodeType: string;
		title?: string;
	}[]
): Graph {
	const graph = new Graph({ type: "directed", multi: false });
	for (const n of nodes) {
		graph.addNode(n.path, {
			community: n.community,
			inCount: n.inCount,
			nodeType: n.nodeType,
			label: n.title ?? n.path,
		});
	}
	return graph;
}

describe("clusterNameRank", () => {
	it("ranks concept ahead of source and entity", () => {
		expect(clusterNameRank("concept")).toBeLessThan(clusterNameRank("entity"));
		expect(clusterNameRank("entity")).toBeLessThan(clusterNameRank("source"));
	});
});

describe("computeClusters", () => {
	it("names a cluster from concepts even when a source has higher inCount", () => {
		const clusters = computeClusters(
			graphWith([
				{
					path: "wiki/sources/@book-chapter.md",
					community: 1,
					inCount: 200,
					nodeType: "source",
					title: "A Very Long Book Chapter Title About Algorithms",
				},
				{
					path: "wiki/entities/Author.md",
					community: 1,
					inCount: 80,
					nodeType: "entity",
					title: "Famous Author",
				},
				{
					path: "wiki/concepts/最短路.md",
					community: 1,
					inCount: 12,
					nodeType: "concept",
					title: "最短路",
				},
				{
					path: "wiki/concepts/グラフ.md",
					community: 1,
					inCount: 8,
					nodeType: "concept",
					title: "グラフ",
				},
			])
		);
		expect(clusters).toHaveLength(1);
		expect(clusters[0].label).toBe("最短路 / グラフ");
		expect(clusterOverviewLabel(clusters[0]).startsWith("最短路")).toBe(true);
		expect(clusters[0].topMembers[0].title).toBe(
			"A Very Long Book Chapter Title About Algorithms"
		);
	});

	it("falls back to entity then source when a cluster has no concept", () => {
		const clusters = computeClusters(
			graphWith([
				{
					path: "wiki/sources/@paper.md",
					community: 2,
					inCount: 50,
					nodeType: "source",
					title: "Some Paper",
				},
				{
					path: "wiki/entities/Lab.md",
					community: 2,
					inCount: 3,
					nodeType: "entity",
					title: "Lab",
				},
			])
		);
		expect(clusters[0].label).toBe("Lab / Some Paper");
	});
});
