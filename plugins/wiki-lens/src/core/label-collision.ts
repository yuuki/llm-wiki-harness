import Graph from "graphology";
import { computeLabelBox } from "./label-box";

/**
 * Greedy 4-way fallback when two radial labels would land on top of
 * each other. Uses projected viewport pixels so it matches what the
 * user sees. Writes `labelAngle` on each labeled node.
 */
export function resolveLabelAngles(
	graph: Graph,
	labeled: Iterable<string>,
	project: (x: number, y: number) => { x: number; y: number },
	fontSize = 12
): void {
	const placed: { x: number; y: number; w: number; h: number }[] = [];
	const candidates = [0, Math.PI, Math.PI / 2, -Math.PI / 2];
	const nodes = [...labeled].filter((node) => graph.hasNode(node));
	nodes.sort((a, b) => {
		const sa = (graph.getNodeAttribute(a, "size") as number) ?? 0;
		const sb = (graph.getNodeAttribute(b, "size") as number) ?? 0;
		return sb - sa;
	});

	const boxAt = (
		x: number,
		y: number,
		size: number,
		angle: number,
		w: number
	) => {
		const box = computeLabelBox({
			x,
			y,
			nodeSize: size,
			angle,
			textWidth: w,
			fontSize,
			viewportY: true,
		});
		return { x: box.boxX, y: box.boxY, w: box.boxW, h: box.boxH };
	};
	const hits = (
		a: { x: number; y: number; w: number; h: number },
		b: { x: number; y: number; w: number; h: number }
	) =>
		a.x < b.x + b.w &&
		a.x + a.w > b.x &&
		a.y < b.y + b.h &&
		a.y + a.h > b.y;

	const angles = new Map<string, number>();
	for (const node of nodes) {
		const attrs = graph.getNodeAttributes(node);
		const x = attrs["x"];
		const y = attrs["y"];
		if (typeof x !== "number" || typeof y !== "number") continue;
		const vp = project(x, y);
		const size = (attrs["size"] as number) ?? 8;
		const label = (attrs["label"] as string) ?? "";
		const w = Math.min(22, label.length) * 7.2;
		const radial = (attrs["labelAngle"] as number) ?? 0;
		let chosen = radial;
		const options = [radial, ...candidates];
		for (const angle of options) {
			const box = boxAt(vp.x, vp.y, size, angle, w);
			if (!placed.some((p) => hits(box, p))) {
				chosen = angle;
				placed.push(box);
				break;
			}
		}
		angles.set(node, chosen);
	}
	if (angles.size === 0) return;
	graph.updateEachNodeAttributes(
		(node, attrs) => {
			const angle = angles.get(node);
			if (angle !== undefined) attrs["labelAngle"] = angle;
			return attrs;
		},
		{ attributes: ["labelAngle"] }
	);
}
