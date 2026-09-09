/** Pixel padding around the label text. */
const PAD_X = 4;
const PAD_Y = 2;
/** Gap between the node disc and the nearest edge of the label box. */
const GAP = 10;

export interface LabelBox {
	textX: number;
	textY: number;
	boxX: number;
	boxY: number;
	boxW: number;
	boxH: number;
}

/**
 * Place a label box so it wraps the text. With an angle, the box grows
 * away from the node (not centered on a point just outside it).
 * `viewportY` flips sin() because sigma's viewport Y grows downward
 * while graph-space angles use the layout's Y-up coordinates.
 */
export function computeLabelBox(opts: {
	x: number;
	y: number;
	nodeSize: number;
	angle?: number;
	textWidth: number;
	fontSize: number;
	viewportY?: boolean;
}): LabelBox {
	const { x, y, nodeSize, angle, textWidth, fontSize, viewportY = false } =
		opts;
	const boxW = textWidth + PAD_X * 2;
	const boxH = fontSize + PAD_Y * 2;

	if (typeof angle !== "number") {
		const textX = x + nodeSize + 3;
		const textY = y + fontSize / 3;
		return {
			textX,
			textY,
			boxX: textX - PAD_X,
			boxY: y - boxH / 2,
			boxW,
			boxH,
		};
	}

	const ox = Math.cos(angle);
	const oy = (viewportY ? -1 : 1) * Math.sin(angle);
	const ax = x + ox * (nodeSize + GAP);
	const ay = y + oy * (nodeSize + GAP);

	if (Math.abs(ox) >= Math.abs(oy)) {
		const textX = ox >= 0 ? ax : ax - textWidth;
		const textY = ay + fontSize / 3;
		return {
			textX,
			textY,
			boxX: textX - PAD_X,
			boxY: ay - boxH / 2,
			boxW,
			boxH,
		};
	}

	const textX = ax - textWidth / 2;
	const boxY = oy >= 0 ? ay : ay - boxH;
	return {
		textX,
		textY: boxY + PAD_Y + fontSize * 0.8,
		boxX: textX - PAD_X,
		boxY,
		boxW,
		boxH,
	};
}
