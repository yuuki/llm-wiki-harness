import { ItemView, WorkspaceLeaf } from "obsidian";
import { GraphIndex } from "../core/graph-index";
import {
	DEAD_CAUSE_LABELS,
	DeadCause,
	WikiPageType,
	WikiStatus,
} from "../core/types";

export const HEALTH_VIEW_TYPE = "wiki-lens-health";

const TYPES: WikiPageType[] = ["source", "entity", "concept", "question"];
const STATUSES: WikiStatus[] = [
	"seed",
	"developing",
	"mature",
	"evergreen",
	"unknown",
];
const CAUSES: DeadCause[] = [
	"missing-page",
	"missing-at-prefix",
	"missing-source",
	"file-ref",
];

export class HealthView extends ItemView {
	private index: GraphIndex;
	private causeFilter: DeadCause | "all" = "all";
	private hideLintStubs = true;
	private deadLimit = 100;
	private promoLimit = 50;
	private orphanLimit = 50;
	private dupeLimit = 30;

	constructor(leaf: WorkspaceLeaf, index: GraphIndex) {
		super(leaf);
		this.index = index;
	}

	getViewType(): string {
		return HEALTH_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Wiki Lens: Health";
	}

	getIcon(): string {
		return "activity";
	}

	async onOpen(): Promise<void> {
		this.registerEvent(this.index.on("updated", () => this.render()));
		this.render();
	}

	private openPath(path: string): void {
		void this.app.workspace.openLinkText(path, "/", false);
	}

	private render(): void {
		const el = this.contentEl;
		el.empty();
		el.addClass("wiki-lens-health");

		if (!this.index.built) {
			el.createEl("div", {
				cls: "wiki-lens-loading",
				text: "Building index… (waiting for metadataCache resolution to complete)",
			});
			return;
		}

		this.renderHeader(el);
		this.renderSummary(el);
		this.renderMatrix(el);
		this.renderDeadLinks(el);
		this.renderPromotion(el);
		this.renderOrphans(el);
		this.renderDuplicates(el);
	}

	private renderHeader(el: HTMLElement): void {
		const stats = this.index.stats;
		if (!stats) return;
		const header = el.createDiv({ cls: "wiki-lens-header" });
		header.createEl("h2", { text: "Wiki Health Dashboard" });
		const info = header.createDiv({ cls: "wiki-lens-buildinfo" });
		info.createSpan({
			text: `Built ${new Date(stats.builtAt).toLocaleTimeString()} / ${stats.buildMs.toFixed(0)}ms`,
		});
		const btn = info.createEl("button", { text: "Rebuild" });
		btn.onclick = () => this.index.build();
	}

	private renderSummary(el: HTMLElement): void {
		const stats = this.index.stats;
		if (!stats) return;
		const counts: Record<string, number> = {};
		let orphans = 0;
		for (const n of this.index.nodes.values()) {
			counts[n.type] = (counts[n.type] ?? 0) + 1;
			if (n.inDeg === 0 && n.outDeg === 0) orphans++;
		}
		const chips = el.createDiv({ cls: "wiki-lens-chips" });
		const chip = (label: string, value: string | number) => {
			const c = chips.createDiv({ cls: "wiki-lens-chip" });
			c.createSpan({ cls: "wiki-lens-chip-value", text: String(value) });
			c.createSpan({ cls: "wiki-lens-chip-label", text: label });
		};
		for (const t of TYPES) chip(t, counts[t] ?? 0);
		chip("Edges (unique)", stats.edgeCount);
		chip("Dead links (refs)", stats.deadRefCount);
		chip("Dead targets", stats.deadTargetCount);
		chip("Orphan pages", orphans);
	}

	private renderMatrix(el: HTMLElement): void {
		const section = el.createDiv({ cls: "wiki-lens-section" });
		section.createEl("h3", { text: "status × type matrix" });
		const m = new Map<string, number>();
		for (const n of this.index.nodes.values()) {
			const k = `${n.type}:${n.status}`;
			m.set(k, (m.get(k) ?? 0) + 1);
		}
		const table = section.createEl("table", { cls: "wiki-lens-table" });
		const head = table.createEl("tr");
		head.createEl("th", { text: "type \\ status" });
		for (const s of STATUSES) head.createEl("th", { text: s });
		for (const t of TYPES) {
			const row = table.createEl("tr");
			row.createEl("td", { text: t });
			for (const s of STATUSES) {
				const v = m.get(`${t}:${s}`) ?? 0;
				const td = row.createEl("td", { text: v ? String(v) : "·" });
				if (s === "seed" && v > 0) td.addClass("wiki-lens-seed");
				if ((s === "mature" || s === "evergreen") && v > 0)
					td.addClass("wiki-lens-mature");
			}
		}
	}

	private renderDeadLinks(el: HTMLElement): void {
		const section = el.createDiv({ cls: "wiki-lens-section" });
		section.createEl("h3", { text: "Dead Link Triage" });

		const all = [...this.index.deadTargets.values()];
		const byCause = new Map<DeadCause, number>();
		for (const d of all) byCause.set(d.cause, (byCause.get(d.cause) ?? 0) + 1);

		const filters = section.createDiv({ cls: "wiki-lens-filters" });
		const mkFilter = (key: DeadCause | "all", label: string, count: number) => {
			const b = filters.createEl("button", { text: `${label} (${count})` });
			if (this.causeFilter === key) b.addClass("wiki-lens-filter-active");
			b.onclick = () => {
				this.causeFilter = key;
				this.deadLimit = 100;
				this.render();
			};
		};
		mkFilter("all", "All", all.length);
		for (const c of CAUSES) mkFilter(c, DEAD_CAUSE_LABELS[c], byCause.get(c) ?? 0);

		const shown = (
			this.causeFilter === "all"
				? all
				: all.filter((d) => d.cause === this.causeFilter)
		).sort((a, b) => b.refs - a.refs);

		const table = section.createEl("table", { cls: "wiki-lens-table" });
		const head = table.createEl("tr");
		for (const h of ["Target", "Cause", "Refs", "Referrers (e.g.)"])
			head.createEl("th", { text: h });
		for (const d of shown.slice(0, this.deadLimit)) {
			const row = table.createEl("tr");
			row.createEl("td", { text: d.target, cls: "wiki-lens-target" });
			row.createEl("td", { text: DEAD_CAUSE_LABELS[d.cause] });
			row.createEl("td", { text: String(d.refs) });
			const refCell = row.createEl("td");
			this.pathLink(refCell, d.referrers[0]);
			if (d.referrers.length > 1)
				refCell.createSpan({
					cls: "wiki-lens-muted",
					text: ` +${d.referrers.length - 1} more pages`,
				});
		}
		if (shown.length > this.deadLimit) {
			const more = section.createEl("button", {
				text: `Show more (${shown.length - this.deadLimit} remaining)`,
			});
			more.onclick = () => {
				this.deadLimit += 200;
				this.render();
			};
		}
	}

	private renderPromotion(el: HTMLElement): void {
		const section = el.createDiv({ cls: "wiki-lens-section" });
		section.createEl("h3", {
			text: "Growth Candidates (pages with many inbound links but status: seed)",
		});
		const toggle = section.createEl("label", { cls: "wiki-lens-toggle" });
		const cb = toggle.createEl("input", { type: "checkbox" });
		cb.checked = this.hideLintStubs;
		cb.onchange = () => {
			this.hideLintStubs = cb.checked;
			this.render();
		};
		toggle.createSpan({ text: " Exclude lint-stub" });

		const allCandidates = [...this.index.nodes.values()]
			.filter(
				(n) =>
					n.status === "seed" &&
					n.inCount > 0 &&
					!(this.hideLintStubs && n.isLintStub)
			)
			.sort((a, b) => b.inCount - a.inCount);
		const candidates = allCandidates.slice(0, this.promoLimit);

		section.createEl("p", {
			cls: "wiki-lens-muted",
			text: "Inbound / outbound counts are link instances, not unique neighbors.",
		});
		const table = section.createEl("table", { cls: "wiki-lens-table" });
		const head = table.createEl("tr");
		for (const h of [
			"Page",
			"type",
			"Subtype",
			"Inbound links",
			"Outbound links",
		])
			head.createEl("th", { text: h });
		for (const n of candidates) {
			const row = table.createEl("tr");
			const cell = row.createEl("td");
			this.pathLink(cell, n.path, n.title);
			if (n.isLintStub)
				cell.createSpan({ cls: "wiki-lens-badge", text: "lint-stub" });
			row.createEl("td", { text: n.type });
			row.createEl("td", { text: n.subType || "—" });
			row.createEl("td", { text: String(n.inCount) });
			row.createEl("td", { text: String(n.outCount) });
		}
		if (allCandidates.length > this.promoLimit) {
			const more = section.createEl("button", {
				text: `Show more (${allCandidates.length - this.promoLimit} remaining)`,
			});
			more.onclick = () => {
				this.promoLimit += 50;
				this.render();
			};
		}
	}

	private renderOrphans(el: HTMLElement): void {
		const orphans = [...this.index.nodes.values()].filter(
			(n) => n.inDeg === 0 && n.outDeg === 0
		);
		const section = el.createDiv({ cls: "wiki-lens-section" });
		section.createEl("h3", {
			text: `Orphan Pages (zero inbound/outbound links, ${orphans.length})`,
		});
		section.createEl("p", {
			cls: "wiki-lens-muted",
			text: "Inbound links from catalog-layer pages such as index.md and log.md are not counted toward degree (mega-hub exclusion), so this list is broader than the lint report.",
		});
		const list = section.createEl("ul");
		for (const n of orphans.slice(0, this.orphanLimit)) {
			const li = list.createEl("li");
			this.pathLink(li, n.path, n.title);
			li.createSpan({ cls: "wiki-lens-muted", text: ` (${n.type}/${n.status})` });
		}
		if (orphans.length > this.orphanLimit) {
			const more = section.createEl("button", {
				text: `Show more (${orphans.length - this.orphanLimit} remaining)`,
			});
			more.onclick = () => {
				this.orphanLimit += 50;
				this.render();
			};
		}
	}

	private renderDuplicates(el: HTMLElement): void {
		const dupes = [...this.index.basenameToPaths.entries()].filter(
			([, paths]) => paths.length > 1
		);
		const section = el.createDiv({ cls: "wiki-lens-section" });
		section.createEl("h3", {
			text: `Duplicate Basenames (ambiguous link resolution, ${dupes.length})`,
		});
		const list = section.createEl("ul");
		for (const [base, paths] of dupes.slice(0, this.dupeLimit)) {
			const li = list.createEl("li");
			li.createSpan({ text: `${base}: ` });
			paths.forEach((p, i) => {
				if (i > 0) li.createSpan({ text: " / " });
				this.pathLink(li, p, p);
			});
		}
		if (dupes.length > this.dupeLimit) {
			const more = section.createEl("button", {
				text: `Show more (${dupes.length - this.dupeLimit} remaining)`,
			});
			more.onclick = () => {
				this.dupeLimit += 30;
				this.render();
			};
		}
	}

	private pathLink(parent: HTMLElement, path?: string, label?: string): void {
		if (!path) return;
		const a = parent.createEl("a", {
			cls: "wiki-lens-link",
			text: label ?? path,
		});
		a.onclick = (e) => {
			e.preventDefault();
			this.openPath(path);
		};
	}
}
