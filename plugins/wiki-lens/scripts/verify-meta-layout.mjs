/**
 * Offline check that the overview skeleton + layout keeps super-nodes apart.
 * Run from the plugin root: node scripts/verify-meta-layout.mjs
 */
import { execSync } from "child_process";
import { createRequire } from "module";
import { unlinkSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import Graph from "graphology";
import { circular } from "graphology-layout";
import forceAtlas2 from "graphology-layout-forceatlas2";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, ".tmp-cluster.cjs");
execSync(
	`npx esbuild src/core/cluster.ts --bundle --platform=node --format=cjs --outfile=${JSON.stringify(out)}`,
	{ cwd: root, stdio: "pipe" }
);
process.on("exit", () => {
	try {
		unlinkSync(out);
	} catch {
		/* ignore */
	}
});
const {
	buildMetaGraph,
	initMetaLayout,
	separateNodes,
	selectStrongMetaEdges,
	META_FA2_SETTINGS,
	META_FA2_ITERATIONS,
} = createRequire(import.meta.url)(out);

function overlapPairs(graph, padding = 0) {
	const nodes = graph.nodes();
	let n = 0;
	let minRatio = Infinity;
	for (let i = 0; i < nodes.length; i++) {
		const ai = graph.getNodeAttributes(nodes[i]);
		for (let j = i + 1; j < nodes.length; j++) {
			const aj = graph.getNodeAttributes(nodes[j]);
			const dist = Math.hypot(ai.x - aj.x, ai.y - aj.y);
			const min = (ai.size ?? 8) + (aj.size ?? 8) + padding;
			if (min > 0) minRatio = Math.min(minRatio, dist / min);
			if (dist < min) n++;
		}
	}
	return { n, minRatio };
}

function fakeClusters(k) {
	const sizes = [
		482, 455, 210, 188, 160, 142, 118, 96, 84, 72, 61, 54, 48, 41, 36,
		31, 28, 25, 22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9, 8, 8, 7, 6, 5, 5,
	].slice(0, k);
	return sizes.map((size, id) => ({
		id,
		size,
		label: `Topic ${id} / Other ${id}`,
		topMembers: [
			{ path: `n${id}`, title: `Topic ${id}`, inDeg: size },
			{ path: `m${id}`, title: `Other ${id}`, inDeg: size / 2 },
		],
	}));
}

function fakeBackbone(clusters, edgeCount) {
	const g = new Graph({ type: "directed", multi: false });
	for (const c of clusters) {
		g.addNode(`n${c.id}`, { community: c.id, label: c.label });
	}
	let added = 0;
	let seed = 1;
	const rnd = () => {
		seed = (seed * 1664525 + 1013904223) >>> 0;
		return seed / 0xffffffff;
	};
	while (added < edgeCount) {
		const a = Math.floor(rnd() * clusters.length);
		const b = Math.floor(rnd() * clusters.length);
		if (a === b) continue;
		const key = `n${a}`;
		const dest = `n${b}`;
		if (g.hasEdge(key, dest)) continue;
		g.addEdge(key, dest, { count: 1 + Math.floor(rnd() * 80) });
		added++;
	}
	return g;
}

function layoutOld(meta) {
	circular.assign(meta, { scale: 500 });
	forceAtlas2.assign(meta, {
		iterations: 150 + Math.floor(meta.order / 10),
		settings: forceAtlas2.inferSettings(meta),
	});
}

function layoutNew(meta) {
	initMetaLayout(meta);
	forceAtlas2.assign(meta, {
		iterations: META_FA2_ITERATIONS,
		settings: { ...forceAtlas2.inferSettings(meta), ...META_FA2_SETTINGS },
		getEdgeWeight: "weight",
	});
	separateNodes(meta);
}

const clusters = fakeClusters(35);
const backbone = fakeBackbone(clusters, 373);
const metaNew = buildMetaGraph(backbone, clusters);
const shown = metaNew.getAttribute("interEdgeShown");
const total = metaNew.getAttribute("interEdgeTotal");

const directed = new Map();
backbone.forEachEdge((_e, attrs, s, t) => {
	const cs = backbone.getNodeAttribute(s, "community");
	const ct = backbone.getNodeAttribute(t, "community");
	if (cs === ct) return;
	const key = `${cs}->${ct}`;
	directed.set(key, (directed.get(key) ?? 0) + (attrs.count ?? 1));
});
const strong = selectStrongMetaEdges(directed, clusters.map((c) => c.id));

const metaOld = metaNew.copy();
metaOld.forEachNode((node, attrs) => {
	metaOld.setNodeAttribute(node, "size", 8 + 4 * Math.log2(clusters.find((c) => `cluster:${c.id}` === node)?.size ?? 8));
	void attrs;
});
layoutOld(metaOld);
layoutNew(metaNew);

const oldO = overlapPairs(metaOld);
const newO = overlapPairs(metaNew);
const newPad = overlapPairs(metaNew, 18);

const lines = [
	`clusters ${metaNew.order}`,
	`inter-cluster directed ${total} (skeleton ${shown}, strong keys ${strong.size})`,
	`old overlaps ${oldO.n} (min dist/sumR ${oldO.minRatio.toFixed(2)})`,
	`new overlaps ${newO.n} (min dist/sumR ${newO.minRatio.toFixed(2)})`,
	`new overlaps with pad 18: ${newPad.n}`,
];
console.log(lines.join("\n"));

if (shown >= total) {
	console.error("FAIL: skeleton did not sparsify");
	process.exit(1);
}
if (newO.n > 0) {
	console.error("FAIL: nodes still overlap after separateNodes");
	process.exit(1);
}
if (newO.minRatio < 1) {
	console.error("FAIL: min distance still below radii sum");
	process.exit(1);
}
console.log("OK");
