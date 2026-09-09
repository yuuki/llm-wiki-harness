import { setIcon } from "obsidian";
import { TYPE_COLORS } from "../core/palette";
import { CountSeries, dayToDate, TimelineModel } from "../core/timeline";
import {
	TimeControlsState,
	WIKI_PAGE_TYPE_LABELS,
	WIKI_PAGE_TYPES,
	WikiPageType,
} from "../core/types";

const SVG_NS = "http://www.w3.org/2000/svg";

const PAD = { top: 8, right: 10, bottom: 18, left: 40 };

const TOTAL_KEY = "total";

type Metric = "cumulative" | "daily";
type SeriesKey = WikiPageType | typeof TOTAL_KEY;

export interface CountChartOptions {
	onSeek: (index: number) => void;
	onExcludeSeedChange: (exclude: boolean) => void;
}

/**
 * DOM + SVG strip for per-type page counts over TimelineModel.days.
 * Independent of the spatial type filter and of sigma / graphology.
 */
export class CountChart {
	private opts: CountChartOptions;

	private root: HTMLElement;
	private toggleBtn: HTMLButtonElement;
	private metricSelect: HTMLSelectElement;
	private seedCb: HTMLInputElement;
	private body: HTMLElement;
	private plotEl: HTMLElement;
	private svg: SVGSVGElement;
	private tooltip: HTMLElement;
	private legendEl: HTMLElement;
	private noteEl: HTMLElement;

	private series: CountSeries | null = null;
	private model: TimelineModel | null = null;
	private cursor: TimeControlsState | null = null;
	private hoverIndex: number | null = null;

	private metric: Metric = "cumulative";
	private excludeSeed = false;
	private expanded = true;
	private visibleSeries: Set<SeriesKey> = new Set(WIKI_PAGE_TYPES);

	private legendInputs: { key: SeriesKey; cb: HTMLInputElement; countEl: HTMLElement }[] =
		[];

	private resizeObs: ResizeObserver | null = null;
	private plotWidth = 0;
	private plotHeight = 0;

	constructor(parent: HTMLElement, opts: CountChartOptions) {
		this.opts = opts;

		this.root = parent.createDiv({ cls: "wiki-lens-count-chart" });

		const header = this.root.createDiv({ cls: "wiki-lens-count-chart-header" });
		this.toggleBtn = header.createEl("button", {
			cls: "wiki-lens-count-chart-toggle",
		});
		this.toggleBtn.setAttribute("aria-expanded", "true");
		this.toggleBtn.onclick = () => this.setExpanded(!this.expanded);
		this.syncToggle();

		this.metricSelect = header.createEl("select");
		this.metricSelect.createEl("option", {
			text: "Cumulative",
			attr: { value: "cumulative" },
		});
		this.metricSelect.createEl("option", {
			text: "Daily new",
			attr: { value: "daily" },
		});
		this.metricSelect.value = "cumulative";
		this.metricSelect.onchange = () => {
			this.metric = this.metricSelect.value as Metric;
			this.updateNote();
			this.draw();
		};

		const seedLabel = header.createEl("label", { cls: "wiki-lens-toggle" });
		this.seedCb = seedLabel.createEl("input", { type: "checkbox" });
		this.seedCb.checked = this.excludeSeed;
		seedLabel.createSpan({ text: "Exclude seed entities" });
		this.seedCb.onchange = () => {
			this.excludeSeed = this.seedCb.checked;
			this.opts.onExcludeSeedChange(this.excludeSeed);
		};

		this.body = this.root.createDiv({ cls: "wiki-lens-count-chart-body" });
		this.plotEl = this.body.createDiv({ cls: "wiki-lens-count-chart-plot" });
		this.svg = document.createElementNS(SVG_NS, "svg");
		this.svg.setAttribute("class", "wiki-lens-count-chart-svg");
		this.svg.setAttribute("role", "img");
		this.svg.setAttribute("aria-label", "Page counts over time");
		this.plotEl.appendChild(this.svg);

		this.tooltip = this.plotEl.createDiv({ cls: "wiki-lens-count-chart-tooltip" });
		this.tooltip.style.display = "none";

		this.legendEl = this.body.createDiv({
			cls: "wiki-lens-count-chart-legend",
		});
		this.legendEl.setAttr("role", "group");
		this.legendEl.setAttr("aria-label", "Count series");
		this.buildLegend();
		this.syncLegendDisabled();

		this.noteEl = this.root.createDiv({
			cls: "wiki-lens-muted wiki-lens-count-chart-note",
		});

		this.svg.addEventListener("pointermove", (e) => this.onPointerMove(e));
		this.svg.addEventListener("pointerleave", () => this.onPointerLeave());
		this.svg.addEventListener("click", (e) => this.onClick(e));

		this.resizeObs = new ResizeObserver(() => {
			const w = this.plotEl.clientWidth;
			const h = this.plotEl.clientHeight;
			if (w === this.plotWidth && h === this.plotHeight) return;
			this.draw();
		});
		this.resizeObs.observe(this.plotEl);
	}

	excludeSeedEntities(): boolean {
		return this.excludeSeed;
	}

	/** Freeze the seed toggle while TimeView is waiting on ForceAtlas2. */
	setSeedLocked(locked: boolean): void {
		this.seedCb.disabled = locked;
		this.seedCb.title = locked
			? "Unavailable while the graph layout is still running"
			: "";
	}

	setData(series: CountSeries | null, model: TimelineModel | null): void {
		this.series = series;
		this.model = model;
		this.hoverIndex = null;
		this.tooltip.style.display = "none";
		this.updateNote();
		this.syncLegendCounts();
		this.draw();
	}

	setCursor(state: TimeControlsState | null): void {
		const prev = this.cursor;
		this.cursor = state;
		this.syncLegendCounts();
		if (this.patchReplayHead(prev, state)) return;
		this.draw();
	}

	destroy(): void {
		this.resizeObs?.disconnect();
		this.resizeObs = null;
		this.root.detach();
	}

	private setExpanded(expanded: boolean): void {
		this.expanded = expanded;
		this.body.style.display = expanded ? "" : "none";
		this.noteEl.style.display = expanded ? "" : "none";
		this.syncToggle();
		if (expanded) this.draw();
	}

	private syncToggle(): void {
		this.toggleBtn.empty();
		const icon = this.toggleBtn.createSpan({
			cls: "wiki-lens-count-chart-chevron",
		});
		setIcon(icon, this.expanded ? "chevron-down" : "chevron-right");
		this.toggleBtn.createSpan({ text: "Node counts" });
		this.toggleBtn.setAttribute("aria-expanded", this.expanded ? "true" : "false");
	}

	private buildLegend(): void {
		this.legendEl.empty();
		this.legendInputs = [];
		this.addLegendItem(TOTAL_KEY, "total", "var(--text-normal)", true);
		for (const type of WIKI_PAGE_TYPES) {
			this.addLegendItem(type, WIKI_PAGE_TYPE_LABELS[type], TYPE_COLORS[type], false);
		}
	}

	private addLegendItem(
		key: SeriesKey,
		label: string,
		color: string,
		dashed: boolean
	): void {
		const item = this.legendEl.createEl("label", {
			cls: "wiki-lens-count-chart-legend-item",
		});
		const cb = item.createEl("input", { type: "checkbox" });
		cb.checked = this.visibleSeries.has(key);
		const swatch = item.createSpan({ cls: "wiki-lens-count-chart-swatch" });
		swatch.style.backgroundColor = dashed ? "transparent" : color;
		swatch.style.borderColor = color;
		if (dashed) swatch.addClass("wiki-lens-count-chart-swatch-dashed");
		item.createSpan({ text: label });
		const countEl = item.createSpan({
			cls: "wiki-lens-muted wiki-lens-count-chart-legend-count",
			text: "",
		});
		cb.onchange = () => {
			if (
				!cb.checked &&
				this.visibleSeries.size === 1 &&
				this.visibleSeries.has(key)
			) {
				cb.checked = true;
				return;
			}
			if (cb.checked) this.visibleSeries.add(key);
			else this.visibleSeries.delete(key);
			this.syncLegendDisabled();
			this.draw();
		};
		this.legendInputs.push({ key, cb, countEl });
	}

	private syncLegendDisabled(): void {
		const lastOn = this.visibleSeries.size === 1;
		for (const { key, cb } of this.legendInputs) {
			const on = this.visibleSeries.has(key);
			cb.disabled = lastOn && on;
			cb.title = lastOn && on ? "At least one series must stay visible" : "";
		}
	}

	private cursorIndex(): number {
		const n = this.model?.days.length ?? 0;
		if (n === 0) return 0;
		const state = this.cursor;
		if (!state) return n - 1;
		if (state.mode === "diff") return state.bIndex;
		return state.tIndex;
	}

	private syncLegendCounts(): void {
		const series = this.series;
		if (!series || !this.model || this.model.days.length === 0) {
			for (const { countEl } of this.legendInputs) countEl.setText("");
			return;
		}
		const i = clamp(this.cursorIndex(), 0, this.model.days.length - 1);
		const values = this.metric === "daily" ? this.dailyOf(series) : this.cumOf(series);
		for (const { key, countEl } of this.legendInputs) {
			countEl.setText(fmt(values[key][i] ?? 0));
		}
	}

	private cumOf(series: CountSeries): Record<SeriesKey, number[]> {
		return { total: series.total, ...series.byType };
	}

	private dailyOf(series: CountSeries): Record<SeriesKey, number[]> {
		return { total: series.dailyTotal, ...series.dailyByType };
	}

	private updateNote(): void {
		const model = this.model;
		const series = this.series;
		if (!model || model.days.length === 0) {
			this.noteEl.setText("No time data");
			return;
		}
		const parts: string[] = [];
		if (this.excludeSeed) {
			parts.push(
				"Counts exclude seed entities; isolated pages remain, so totals will not match the backbone graph"
			);
		} else {
			parts.push(
				"Counts include every wiki page, not just the backbone graph"
			);
		}
		const unknown = series?.unknownCreatedCount ?? 0;
		if (unknown > 0) {
			if (this.metric === "daily") {
				parts.push(
					`${fmt(unknown)} with unknown created date sit in the first Daily bar together with first-day births`
				);
			} else {
				parts.push(
					`${fmt(unknown)} with unknown created date counted as present before the first day`
				);
			}
		}
		this.noteEl.setText(parts.join(". ") + ".");
	}

	private onPointerMove(e: PointerEvent): void {
		const index = this.indexFromClientX(e.clientX);
		if (index === null) return;
		if (index === this.hoverIndex) {
			this.showTooltip(index, e);
			return;
		}
		this.hoverIndex = index;
		this.patchHead(index);
		this.showTooltip(index, e);
	}

	private onPointerLeave(): void {
		this.hoverIndex = null;
		this.tooltip.style.display = "none";
		if (this.cursor?.mode === "replay") this.patchHead(this.cursor.tIndex);
		else this.removeHead();
	}

	private onClick(e: PointerEvent): void {
		if (this.cursor?.mode === "diff") return;
		const index = this.indexFromClientX(e.clientX);
		if (index === null) return;
		this.opts.onSeek(index);
	}

	private indexFromClientX(clientX: number): number | null {
		const model = this.model;
		if (!model || model.days.length === 0) return null;
		const rect = this.svg.getBoundingClientRect();
		const width = rect.width;
		const plotW = width - PAD.left - PAD.right;
		if (plotW <= 0) return null;
		if (model.days.length === 1) return 0;
		const t = (clientX - rect.left - PAD.left) / plotW;
		return clamp(Math.round(t * (model.days.length - 1)), 0, model.days.length - 1);
	}

	private draw(): void {
		const svg = this.svg;
		while (svg.firstChild) svg.removeChild(svg.firstChild);
		if (!this.expanded) return;

		const width = Math.max(this.plotEl.clientWidth, 1);
		const height = Math.max(this.plotEl.clientHeight, 1);
		this.plotWidth = width;
		this.plotHeight = height;
		svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
		svg.setAttribute("preserveAspectRatio", "none");

		const model = this.model;
		const series = this.series;
		if (!model || !series || model.days.length === 0) return;

		const plotW = width - PAD.left - PAD.right;
		const plotH = height - PAD.top - PAD.bottom;
		if (plotW <= 0 || plotH <= 0) return;

		const values = this.metric === "daily" ? this.dailyOf(series) : this.cumOf(series);
		const keys = visibleKeys(this.visibleSeries);
		const yMax = niceMax(scaleMax(values, keys, this.metric));
		const n = model.days.length;
		const xAt = (i: number): number =>
			n === 1 ? PAD.left + plotW / 2 : PAD.left + (i / (n - 1)) * plotW;
		const yAt = (v: number): number => PAD.top + plotH - (v / yMax) * plotH;

		appendSvg(svg, "rect", {
			x: PAD.left,
			y: PAD.top,
			width: plotW,
			height: plotH,
			class: "wiki-lens-count-chart-plot-bg",
		});

		const cursor = this.cursor;
		if (cursor?.mode === "diff" && n > 1) {
			const x0 = xAt(Math.min(cursor.aIndex, cursor.bIndex));
			const x1 = xAt(Math.max(cursor.aIndex, cursor.bIndex));
			appendSvg(svg, "rect", {
				x: x0,
				y: PAD.top,
				width: Math.max(x1 - x0, 1),
				height: plotH,
				class: "wiki-lens-count-chart-band",
			});
		}

		for (const tick of yTicks(yMax)) {
			const y = yAt(tick);
			appendSvg(svg, "line", {
				x1: PAD.left,
				y1: y,
				x2: PAD.left + plotW,
				y2: y,
				class: "wiki-lens-count-chart-grid",
			});
			const label = appendSvg(svg, "text", {
				x: PAD.left - 4,
				y: y + 3,
				class: "wiki-lens-count-chart-axis",
				"text-anchor": "end",
			});
			label.textContent = fmt(tick);
		}

		const first = appendSvg(svg, "text", {
			x: PAD.left,
			y: height - 4,
			class: "wiki-lens-count-chart-axis",
		});
		first.textContent = dayToDate(model.days[0]);
		if (n > 1) {
			const last = appendSvg(svg, "text", {
				x: PAD.left + plotW,
				y: height - 4,
				class: "wiki-lens-count-chart-axis",
				"text-anchor": "end",
			});
			last.textContent = dayToDate(model.days[n - 1]);
		}

		if (this.metric === "daily") {
			this.drawDaily(svg, values, keys, n, xAt, yAt, plotW);
		} else {
			this.drawLines(svg, values, keys, n, xAt, yAt);
		}

		const head =
			this.hoverIndex ??
			(cursor?.mode === "replay" ? cursor.tIndex : null);
		if (head !== null) {
			const x = xAt(clamp(head, 0, n - 1));
			appendSvg(svg, "line", {
				x1: x,
				y1: PAD.top,
				x2: x,
				y2: PAD.top + plotH,
				class: "wiki-lens-count-chart-head",
			});
		}

		this.syncLegendCounts();
	}

	private drawLines(
		svg: SVGSVGElement,
		values: Record<SeriesKey, number[]>,
		keys: SeriesKey[],
		n: number,
		xAt: (i: number) => number,
		yAt: (v: number) => number
	): void {
		for (const key of keys) {
			const pts = values[key]
				.map((v, i) => `${xAt(i)},${yAt(v)}`)
				.join(" ");
			if (!pts) continue;
			const line = appendSvg(svg, "polyline", {
				points: pts,
				fill: "none",
				class:
					key === TOTAL_KEY
						? "wiki-lens-count-chart-line wiki-lens-count-chart-line-total"
						: "wiki-lens-count-chart-line",
			});
			if (key !== TOTAL_KEY) {
				line.setAttribute("stroke", TYPE_COLORS[key]);
			}
			if (n === 1) {
				appendSvg(svg, "circle", {
					cx: xAt(0),
					cy: yAt(values[key][0] ?? 0),
					r: 3,
					class: "wiki-lens-count-chart-dot",
					fill: key === TOTAL_KEY ? "var(--text-normal)" : TYPE_COLORS[key],
				});
			}
		}
	}

	private drawDaily(
		svg: SVGSVGElement,
		values: Record<SeriesKey, number[]>,
		keys: SeriesKey[],
		n: number,
		xAt: (i: number) => number,
		yAt: (v: number) => number,
		plotW: number
	): void {
		const typeKeys = keys.filter((k): k is WikiPageType => k !== TOTAL_KEY);
		const slot = n <= 1 ? plotW : plotW / Math.max(n - 1, 1);
		const barW = Math.max(1, Math.min(10, slot * 0.65));

		if (typeKeys.length === 0 && keys.includes(TOTAL_KEY)) {
			for (let i = 0; i < n; i++) {
				const v = values.total[i] ?? 0;
				if (v <= 0) continue;
				this.drawBar(svg, xAt(i), yAt(v), yAt(0), barW, plotW, null);
			}
			return;
		}

		for (let i = 0; i < n; i++) {
			let acc = 0;
			for (const key of typeKeys) {
				const v = values[key][i] ?? 0;
				if (v <= 0) continue;
				this.drawBar(
					svg,
					xAt(i),
					yAt(acc + v),
					yAt(acc),
					barW,
					plotW,
					TYPE_COLORS[key]
				);
				acc += v;
			}
		}
	}

	/** Bars sit on the same xAt scale as the playhead and hit-test. */
	private drawBar(
		svg: SVGSVGElement,
		x: number,
		y1: number,
		y0: number,
		barW: number,
		plotW: number,
		fill: string | null
	): void {
		const left = Math.max(PAD.left, x - barW / 2);
		const right = Math.min(PAD.left + plotW, x + barW / 2);
		const attrs: Record<string, string | number> = {
			x: left,
			y: y1,
			width: Math.max(right - left, 1),
			height: Math.max(y0 - y1, 1),
			class:
				fill === null
					? "wiki-lens-count-chart-bar-total"
					: "wiki-lens-count-chart-bar",
		};
		if (fill !== null) attrs["fill"] = fill;
		appendSvg(svg, "rect", attrs);
	}

	private patchReplayHead(
		prev: TimeControlsState | null,
		next: TimeControlsState | null
	): boolean {
		if (!this.svg.firstChild || !this.model || !this.series) return false;
		if (!prev || !next) return false;
		if (prev.mode !== "replay" || next.mode !== "replay") return false;
		if (prev.aIndex !== next.aIndex || prev.bIndex !== next.bIndex) return false;
		if (this.hoverIndex !== null) return true;
		if (prev.tIndex === next.tIndex) return true;
		this.patchHead(next.tIndex);
		return true;
	}

	private patchHead(index: number): void {
		const model = this.model;
		if (!model || model.days.length === 0) return;
		const plotW = this.plotWidth - PAD.left - PAD.right;
		const plotH = this.plotHeight - PAD.top - PAD.bottom;
		if (plotW <= 0 || plotH <= 0) return;
		const n = model.days.length;
		const i = clamp(index, 0, n - 1);
		const x =
			n === 1 ? PAD.left + plotW / 2 : PAD.left + (i / (n - 1)) * plotW;
		const existing = this.svg.querySelector(
			".wiki-lens-count-chart-head"
		) as SVGLineElement | null;
		if (existing) {
			existing.setAttribute("x1", String(x));
			existing.setAttribute("x2", String(x));
			return;
		}
		appendSvg(this.svg, "line", {
			x1: x,
			y1: PAD.top,
			x2: x,
			y2: PAD.top + plotH,
			class: "wiki-lens-count-chart-head",
		});
	}

	private removeHead(): void {
		this.svg.querySelector(".wiki-lens-count-chart-head")?.remove();
	}

	private showTooltip(index: number, e: PointerEvent): void {
		const model = this.model;
		const series = this.series;
		if (!model || !series) return;
		const values = this.metric === "daily" ? this.dailyOf(series) : this.cumOf(series);
		const dateLabel =
			this.metric === "daily" &&
			index === 0 &&
			series.unknownCreatedCount > 0
				? `${dayToDate(model.days[index])} (opening step)`
				: dayToDate(model.days[index]);
		const lines = [dateLabel];
		for (const key of visibleKeys(this.visibleSeries)) {
			const label = key === TOTAL_KEY ? "total" : WIKI_PAGE_TYPE_LABELS[key];
			lines.push(`${label} ${fmt(values[key][index] ?? 0)}`);
		}
		this.tooltip.setText(lines.join(" · "));
		this.tooltip.style.display = "";
		const plot = this.plotEl.getBoundingClientRect();
		const left = e.clientX - plot.left + 10;
		const top = e.clientY - plot.top + 10;
		this.tooltip.style.left = `${Math.min(left, Math.max(8, plot.width - 180))}px`;
		this.tooltip.style.top = `${Math.min(top, Math.max(8, plot.height - 36))}px`;
	}
}

function visibleKeys(visible: Set<SeriesKey>): SeriesKey[] {
	const keys: SeriesKey[] = [];
	if (visible.has(TOTAL_KEY)) keys.push(TOTAL_KEY);
	for (const type of WIKI_PAGE_TYPES) {
		if (visible.has(type)) keys.push(type);
	}
	return keys;
}

function maxOf(values: Record<SeriesKey, number[]>, keys: SeriesKey[]): number {
	let max = 0;
	for (const key of keys) {
		const arr = values[key];
		for (const v of arr) if (v > max) max = v;
	}
	return max;
}

/** Daily stacks types, so Y must fit the per-day sum, not the tallest series. */
function scaleMax(
	values: Record<SeriesKey, number[]>,
	keys: SeriesKey[],
	metric: Metric
): number {
	if (metric !== "daily") return maxOf(values, keys);
	const typeKeys = keys.filter((k): k is WikiPageType => k !== TOTAL_KEY);
	if (typeKeys.length === 0) return maxOf(values, keys);
	const n = values[typeKeys[0]]?.length ?? 0;
	let max = 0;
	for (let i = 0; i < n; i++) {
		let sum = 0;
		for (const key of typeKeys) sum += values[key][i] ?? 0;
		if (sum > max) max = sum;
	}
	return max;
}

function yTicks(yMax: number): number[] {
	if (yMax <= 1) return [0, yMax];
	if (yMax % 2 === 0) return [0, yMax / 2, yMax];
	return [0, yMax];
}

function niceMax(n: number): number {
	if (n <= 0) return 1;
	const exp = 10 ** Math.floor(Math.log10(n));
	const f = n / exp;
	const steps = [1, 2, 3, 4, 5, 6, 8, 10];
	const nice = steps.find((s) => f <= s) ?? 10;
	return nice * exp;
}

function clamp(n: number, lo: number, hi: number): number {
	return Math.max(lo, Math.min(hi, n));
}

function fmt(n: number): string {
	return n.toLocaleString();
}

function appendSvg<K extends keyof SVGElementTagNameMap>(
	parent: SVGElement,
	name: K,
	attrs: Record<string, string | number>
): SVGElementTagNameMap[K] {
	const el = document.createElementNS(SVG_NS, name);
	for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
	parent.appendChild(el);
	return el;
}
