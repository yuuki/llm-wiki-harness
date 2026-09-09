import Graph from "graphology";
import { circular } from "graphology-layout";
import forceAtlas2, {
	ForceAtlas2Settings,
} from "graphology-layout-forceatlas2";
import { ItemView } from "obsidian";
import Sigma from "sigma";
import { computeLabelBox } from "../core/label-box";

/** Max characters for a label in normal rendering (excess is truncated with a trailing …). Hover shows the full text */
const LABEL_MAX_CHARS = 28;

/**
 * Base view shared across the sigma.js instance lifecycle (creation,
 * destruction, layout, hover-neighbor highlighting, opening a note on
 * click). Destruction must always go through here to avoid leaking the
 * WebGL context.
 */
/**
 * Theme-derived colors referenced every frame by the draw functions
 * (defaultDrawNodeLabel / defaultDrawNodeHover). getComputedStyle is not
 * called per frame; it's resolved only at sigma creation and on
 * css-change, then cached here.
 */
interface ThemeColors {
	/** Label text color (--text-normal) */
	labelText: string;
	/** Label background. A semi-transparent version of --background-primary */
	labelBg: string;
	/** Hover box background (--background-secondary, opaque) */
	hoverBg: string;
	/** Hover box border (--background-modifier-border) */
	hoverBorder: string;
	/** Default edge color. A constant that works for both themes */
	edge: string;
}

export abstract class SigmaBaseView extends ItemView {
	protected sigma: Sigma | null = null;
	protected graph: Graph | null = null;
	protected hoveredNode: string | null = null;
	protected hoveredNeighbors: Set<string> | null = null;
	private layoutFrame: number | null = null;

	/** Whether css-change has already been subscribed to (registered only once, at sigma creation) */
	private themeSubscribed = false;
	/** Watches the container's actual size changes and relays them to sigma */
	private resizeObserver: ResizeObserver | null = null;
	/** Initial value for color resolution. Overwritten by resolveThemeColors() at creation */
	protected themeColors: ThemeColors = {
		labelText: "#dcddde",
		labelBg: "rgba(30, 30, 30, 0.75)",
		hoverBg: "#2a2a2a",
		hoverBorder: "#444444",
		edge: "rgba(128, 128, 128, 0.12)",
	};

	/** Reusable 2D context for normalizing CSS colors to r,g,b */
	private static normCtx: CanvasRenderingContext2D | null = null;

	async onClose(): Promise<void> {
		this.stopLayout();
		this.resizeObserver?.disconnect();
		this.resizeObserver = null;
		this.sigma?.kill();
		this.sigma = null;
	}

	onResize(): void {
		// refresh() does not re-measure container dimensions. After a sidebar
		// width change or window resize, re-take the actual size with
		// resize() before redrawing
		this.sigma?.resize();
		this.refreshDisplay();
	}

	/**
	 * Re-run node/edge reducers without recomputing the spatial index.
	 * `refresh({ skipIndexation: true })` alone is not enough: without
	 * `partialGraph`, sigma treats it as a full refresh and calls process()
	 * (new extent / normalization). Hover, type filters, and time ticks
	 * must not do that.
	 */
	protected refreshDisplay(): void {
		const sigma = this.sigma;
		const graph = this.graph;
		if (!sigma || !graph) return;
		sigma.refresh({
			skipIndexation: true,
			partialGraph: {
				nodes: graph.nodes(),
				edges: graph.edges(),
			},
		});
	}

	/** Lock normalization to the current graph so later process() calls cannot reframe. */
	protected pinFrozenBBox(graph = this.graph): void {
		const sigma = this.sigma;
		if (!graph || !sigma || graph.order === 0) return;
		let xMin = Infinity;
		let xMax = -Infinity;
		let yMin = Infinity;
		let yMax = -Infinity;
		graph.forEachNode((_node, attrs) => {
			const x = attrs["x"];
			const y = attrs["y"];
			if (typeof x !== "number" || typeof y !== "number") return;
			if (x < xMin) xMin = x;
			if (x > xMax) xMax = x;
			if (y < yMin) yMin = y;
			if (y > yMax) yMax = y;
		});
		if (!Number.isFinite(xMin) || !Number.isFinite(yMin)) return;
		const padX = (xMax - xMin) * 0.02 || 1;
		const padY = (yMax - yMin) * 0.02 || 1;
		sigma.setCustomBBox({
			x: [xMin - padX, xMax + padX],
			y: [yMin - padY, yMax + padY],
		});
	}

	protected clearFrozenBBox(): void {
		this.sigma?.setCustomBBox(null);
	}

	/**
	 * Swaps in a new graph. If sigma hasn't been created yet, creates it on
	 * the container. Passing null for layoutInit skips the initial
	 * (circular) placement (used when the caller has already set x/y).
	 */
	protected setSigmaGraph(
		graph: Graph,
		container: HTMLElement,
		layoutInit: { scale: number } | null = { scale: 500 }
	): void {
		if (layoutInit) circular.assign(graph, layoutInit);
		this.graph = graph;
		if (this.sigma) {
			this.sigma.setGraph(graph);
			// sigma v3's setGraph does not automatically trigger reprocessing.
			// In views that don't receive attribute-update events (e.g. local
			// lenses that pass already-laid-out coordinates), the canvas
			// stays blank unless refresh is called explicitly
			this.sigma.refresh();
			return;
		}
		this.themeColors = this.resolveThemeColors();
		this.sigma = new Sigma(graph, container, {
			allowInvalidContainer: true,
			labelColor: { color: this.themeColors.labelText },
			labelSize: 11,
			defaultEdgeColor: this.themeColors.edge,
			labelRenderedSizeThreshold: 7,
			nodeReducer: (node, data) => this.nodeReducer(node, data),
			edgeReducer: (edge, data) => this.edgeReducer(edge, data),
			// Labels are drawn ourselves with a background. Plain text would
			// be unreadable over dense edges (arguments follow sigma's
			// draw-function signature)
			defaultDrawNodeLabel: (context, data, settings) =>
				this.drawLabelBox(context, data, settings, false),
			// The default hover is a white rounded box that's too bright for
			// the dark theme, so replace it
			defaultDrawNodeHover: (context, data, settings) =>
				this.drawLabelBox(context, data, settings, true),
		});
		// Re-resolves colors on theme (light <-> dark) switches. Only needs
		// to be subscribed once, the first time sigma is created
		if (!this.themeSubscribed) {
			this.themeSubscribed = true;
			this.registerEvent(
				this.app.workspace.on("css-change", () =>
					this.refreshThemeColors()
				)
			);
		}
		this.sigma.on("clickNode", ({ node }) => this.onNodeClick(node));
		this.sigma.on("clickStage", ({ event }) => this.onStageClick(event));
		this.sigma.on("doubleClickNode", (payload) => {
			payload.event.preventSigmaDefault();
			this.onNodeDoubleClick(payload.node);
		});
		this.sigma.on("enterNode", ({ node }) => {
			if (!this.acceptHover(node)) return;
			this.hoveredNode = node;
			this.hoveredNeighbors = new Set(this.graph?.neighbors(node));
			this.refreshDisplay();
		});
		this.sigma.on("leaveNode", () => {
			this.hoveredNode = null;
			this.hoveredNeighbors = null;
			this.refreshDisplay();
		});
		// If the leaf is created at a moment before layout has settled (e.g.
		// a 1px height), it stays blank at that size. Obsidian's onResize
		// doesn't fire when the initial layout settles, so watch the
		// container's actual size changes directly via ResizeObserver
		this.resizeObserver = new ResizeObserver(() => {
			this.sigma?.resize();
			this.refreshDisplay();
			this.onSigmaContainerResize();
		});
		this.resizeObserver.observe(container);
	}

	protected onNodeClick(node: string): void {
		if (this.graph?.getNodeAttribute(node, "isGhost")) return;
		void this.app.workspace.openLinkText(node, "/", false);
	}

	/** Container size changed (ResizeObserver). Default is a no-op. */
	protected onSigmaContainerResize(): void {}

	/** Click on empty canvas / labels. Default is a no-op. */
	protected onStageClick(_event: { x: number; y: number }): void {}

	/** No default behavior for double-click. Derived views use it for drill-down etc. */
	protected onNodeDoubleClick(_node: string): void {}

	/** Gate for enterNode. Hidden / filtered nodes should return false. */
	protected acceptHover(_node: string): boolean {
		return true;
	}

	/**
	 * Runs ForceAtlas2 in chunks via rAF.
	 * Moving it to a Worker would require esbuild bundle splitting, so
	 * chunked execution on the main thread is sufficient at this scale.
	 */
	protected runLayout(opts?: {
		settings?: ForceAtlas2Settings;
		iterations?: number;
		getEdgeWeight?: string;
	}): void {
		const graph = this.graph;
		if (!graph || graph.order === 0) return;
		this.stopLayout();

		const settings = opts?.settings
			? { ...forceAtlas2.inferSettings(graph), ...opts.settings }
			: forceAtlas2.inferSettings(graph);
		const totalIterations =
			opts?.iterations ??
			Math.min(600, 150 + Math.floor(graph.order / 10));
		const getEdgeWeight = opts?.getEdgeWeight ?? "weight";
		let done = 0;
		const step = () => {
			forceAtlas2.assign(graph, {
				iterations: 5,
				settings,
				getEdgeWeight,
			});
			done += 5;
			if (done < totalIterations) {
				this.layoutFrame = window.requestAnimationFrame(step);
			} else {
				this.layoutFrame = null;
				this.onLayoutFinished();
			}
		};
		this.layoutFrame = window.requestAnimationFrame(step);
	}

	/** Called once when layout iteration finishes. Default is a no-op */
	protected onLayoutFinished(): void {}

	protected stopLayout(): void {
		if (this.layoutFrame !== null) {
			window.cancelAnimationFrame(this.layoutFrame);
			this.layoutFrame = null;
		}
	}

	protected nodeReducer(
		node: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const res = { ...data };
		if (
			this.hoveredNode &&
			node !== this.hoveredNode &&
			!this.hoveredNeighbors?.has(node)
		) {
			res["color"] = "rgba(90, 90, 90, 0.25)";
			res["label"] = "";
		}
		if (this.hoveredNode === node) {
			res["highlighted"] = true;
		}
		// source / entity nodes are numerous and low information value, so
		// their labels are hidden unless an explicit condition is met.
		// concept/question/ghost/nodeType-unset (e.g. supernodes) keep the
		// previous behavior. It's fine to clear the label again even if the
		// hover dimming above already cleared it
		const nodeType = res["nodeType"];
		if (nodeType === "source" || nodeType === "entity") {
			const keep =
				this.hoveredNode === node ||
				this.hoveredNeighbors?.has(node) === true ||
				res["forceLabel"] === true ||
				res["highlighted"] === true;
			if (!keep) res["label"] = "";
		}
		return res;
	}

	protected edgeReducer(
		edge: string,
		data: Record<string, unknown>
	): Record<string, unknown> {
		const res = { ...data };
		if (this.hoveredNode && this.graph) {
			if (!this.graph.hasExtremity(edge, this.hoveredNode)) {
				res["hidden"] = true;
			} else {
				res["color"] = "rgba(200, 160, 60, 0.6)";
			}
		}
		return res;
	}

	/**
	 * Draws a label on top of a rounded box with a background. Shared
	 * drawing routine called from both sigma's defaultDrawNodeLabel and
	 * defaultDrawNodeHover. hover=false is the normal label (semi-transparent
	 * background, no border); hover=true is the hover box (opaque
	 * background, bordered). data/settings structurally accept only the
	 * fields needed from sigma's NodeDisplayData / Settings (avoids
	 * importing sigma's types and keeps the contract minimal).
	 * Since this function is called every frame, it does not resolve colors
	 * itself — it only reads the cached themeColors.
	 */
	private drawLabelBox(
		context: CanvasRenderingContext2D,
		data: {
			x: number;
			y: number;
			size: number;
			label: string | null;
			labelAngle?: number;
		},
		settings: { labelSize: number; labelFont: string; labelWeight: string },
		hover: boolean
	): void {
		let label = data.label;
		if (typeof label !== "string" || label === "") return;
		// In normal rendering, a long label's background box eats up area
		// and hurts visibility, so it's truncated. Hover (hover=true) wants
		// to show the full text, so it's left untouched
		if (!hover && label.length > LABEL_MAX_CHARS) {
			label = label.slice(0, LABEL_MAX_CHARS - 1) + "…";
		}

		const colors = this.themeColors;
		const size = settings.labelSize;
		context.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;

		const textWidth = context.measureText(label).width;
		const placed = computeLabelBox({
			x: data.x,
			y: data.y,
			nodeSize: data.size,
			angle: data.labelAngle,
			textWidth,
			fontSize: size,
			viewportY: true,
		});
		const { textX, textY, boxX, boxY, boxW, boxH } = placed;

		context.fillStyle = hover ? colors.hoverBg : colors.labelBg;
		this.tracePath(context, boxX, boxY, boxW, boxH, 3);
		context.fill();
		if (hover) {
			context.strokeStyle = colors.hoverBorder;
			context.lineWidth = 1;
			context.stroke();
		}

		context.fillStyle = colors.labelText;
		context.fillText(label, textX, textY);
	}

	/** Draws a rounded-rectangle path (doesn't depend on roundRect, uses only arcTo) */
	private tracePath(
		context: CanvasRenderingContext2D,
		x: number,
		y: number,
		w: number,
		h: number,
		r: number
	): void {
		const rr = Math.min(r, w / 2, h / 2);
		context.beginPath();
		context.moveTo(x + rr, y);
		context.arcTo(x + w, y, x + w, y + h, rr);
		context.arcTo(x + w, y + h, x, y + h, rr);
		context.arcTo(x, y + h, x, y, rr);
		context.arcTo(x, y, x + w, y, rr);
		context.closePath();
	}

	/** Re-resolves colors on css-change, applies them to sigma's settings, and redraws */
	private refreshThemeColors(): void {
		this.themeColors = this.resolveThemeColors();
		if (!this.sigma) return;
		// Our own drawing reads themeColors directly, so updating settings
		// isn't strictly necessary, but keep the settings in sync too so
		// falling back to default rendering doesn't break
		this.sigma.setSetting("labelColor", { color: this.themeColors.labelText });
		this.sigma.setSetting("defaultEdgeColor", this.themeColors.edge);
		this.sigma.refresh();
	}

	/** Resolves the full set of drawing colors from the current theme (called only at creation and on css-change) */
	private resolveThemeColors(): ThemeColors {
		return {
			labelText: this.cssVar("--text-normal", "#dcddde"),
			labelBg: this.cssVarRgba("--background-primary", "30, 30, 30", 0.75),
			hoverBg: this.cssVar("--background-secondary", "#2a2a2a"),
			hoverBorder: this.cssVar("--background-modifier-border", "#444444"),
			edge: "rgba(128, 128, 128, 0.12)",
		};
	}

	/** Returns an rgba string with alpha applied to a CSS variable's color. Uses fallback if it can't be resolved */
	private cssVarRgba(name: string, fallbackRgb: string, alpha: number): string {
		const rgb = this.toRgb(this.cssVar(name, "")) ?? fallbackRgb;
		return `rgba(${rgb}, ${alpha})`;
	}

	/**
	 * Normalizes an arbitrary CSS color string (hex/rgb/hsl/name) to
	 * "r, g, b". Canvas's fillStyle normalizes the color on assignment, so
	 * this leverages that to let the browser handle parsing. Returns null
	 * for invalid values.
	 */
	private toRgb(color: string): string | null {
		if (!color) return null;
		let ctx = SigmaBaseView.normCtx;
		if (!ctx) {
			const c = document.createElement("canvas");
			c.width = 1;
			c.height = 1;
			ctx = c.getContext("2d");
			SigmaBaseView.normCtx = ctx;
		}
		if (!ctx) return null;
		// Invalid values are ignored on assignment, so set a known value first before trying
		ctx.fillStyle = "#000000";
		ctx.fillStyle = color;
		const normalized = ctx.fillStyle;
		if (normalized.startsWith("#")) {
			const r = parseInt(normalized.slice(1, 3), 16);
			const g = parseInt(normalized.slice(3, 5), 16);
			const b = parseInt(normalized.slice(5, 7), 16);
			return `${r}, ${g}, ${b}`;
		}
		const m = normalized.match(/rgba?\(([^)]+)\)/);
		if (m) {
			const parts = m[1].split(",").map((s) => s.trim());
			if (parts.length >= 3) return `${parts[0]}, ${parts[1]}, ${parts[2]}`;
		}
		return null;
	}

	protected cssVar(name: string, fallback: string): string {
		const v = getComputedStyle(document.body).getPropertyValue(name).trim();
		return v || fallback;
	}
}
