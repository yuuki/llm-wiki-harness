import { Plugin, WorkspaceLeaf } from "obsidian";
import { BackboneCache } from "./core/backbone-cache";
import { GraphIndex } from "./core/graph-index";
import { clearPprCache } from "./core/ppr";
import {
	parseVisibleTypes,
	serializeVisibleTypes,
	TypeFilterStore,
} from "./core/type-visibility";
import { WikiPageType } from "./core/types";
import { GAP_VIEW_TYPE, GapView } from "./views/gap-view";
import { GRAPH_VIEW_TYPE, GraphView } from "./views/graph-view";
import { HEALTH_VIEW_TYPE, HealthView } from "./views/health-view";
import { LAYER_VIEW_TYPE, LayerView } from "./views/layer-view";
import { LOCAL_LENS_VIEW_TYPE, LocalLensView } from "./views/local-lens-view";
import { TIME_VIEW_TYPE, TimeView } from "./views/time-view";

/**
 * Wiki Lens — a read-only visualization of the LLM wiki layer.
 * This codebase never introduces any vault-writing APIs.
 */
export default class WikiLensPlugin extends Plugin implements TypeFilterStore {
	index!: GraphIndex;
	cache!: BackboneCache;
	private visibleTypes: Set<WikiPageType> = new Set();

	async onload(): Promise<void> {
		const raw = (await this.loadData()) as { visibleTypes?: unknown } | null;
		this.visibleTypes = parseVisibleTypes(raw?.visibleTypes);
		this.index = new GraphIndex(this.app);
		this.cache = new BackboneCache();
		this.index.on("updated", () => {
			this.cache.invalidate();
			clearPprCache();
		});

		this.registerView(
			HEALTH_VIEW_TYPE,
			(leaf) => new HealthView(leaf, this.index)
		);

		this.registerView(
			GRAPH_VIEW_TYPE,
			(leaf) => new GraphView(leaf, this.index, this.cache, this)
		);

		this.addRibbonIcon("activity", "Wiki Lens: Health Dashboard", () => {
			void this.activateView(HEALTH_VIEW_TYPE);
		});
		this.addRibbonIcon("git-fork", "Wiki Lens: Backbone Graph", () => {
			void this.activateView(GRAPH_VIEW_TYPE);
		});
		this.addCommand({
			id: "open-health-view",
			name: "Open health dashboard",
			callback: () => void this.activateView(HEALTH_VIEW_TYPE),
		});
		this.addCommand({
			id: "open-graph-view",
			name: "Open backbone graph",
			callback: () => void this.activateView(GRAPH_VIEW_TYPE),
		});

		this.registerView(
			GAP_VIEW_TYPE,
			(leaf) => new GapView(leaf, this.index, this.cache)
		);
		this.addRibbonIcon("unlink", "Wiki Lens: Gap Finder", () => {
			void this.activateView(GAP_VIEW_TYPE);
		});
		this.addCommand({
			id: "open-gap-view",
			name: "Open gap finder (missing-connection candidates)",
			callback: () => void this.activateView(GAP_VIEW_TYPE),
		});

		this.registerView(
			LOCAL_LENS_VIEW_TYPE,
			(leaf) => new LocalLensView(leaf, this.index)
		);
		this.addRibbonIcon("scan-eye", "Wiki Lens: Local Lens", () => {
			void this.activateView(LOCAL_LENS_VIEW_TYPE, "right");
		});
		this.addCommand({
			id: "open-local-lens",
			name: "Open local lens (neighborhood of active note)",
			callback: () => void this.activateView(LOCAL_LENS_VIEW_TYPE, "right"),
		});

		this.registerView(
			TIME_VIEW_TYPE,
			(leaf) => new TimeView(leaf, this.index, this.cache, this)
		);
		this.addRibbonIcon("history", "Wiki Lens: Growth Timeline", () => {
			void this.activateView(TIME_VIEW_TYPE);
		});
		this.addCommand({
			id: "open-time-view",
			name: "Open growth timeline (replay / diff)",
			callback: () => void this.activateView(TIME_VIEW_TYPE),
		});

		this.registerView(
			LAYER_VIEW_TYPE,
			(leaf) => new LayerView(leaf, this.index, this.cache)
		);
		this.addRibbonIcon("layers", "Wiki Lens: 3D Layers", () => {
			void this.activateView(LAYER_VIEW_TYPE);
		});
		this.addCommand({
			id: "open-layer-view",
			name: "Open 3D layer view (type planes)",
			callback: () => void this.activateView(LAYER_VIEW_TYPE),
		});

		// metadataCache may still be unresolved at onload time, so we do the
		// initial build on layout ready, then debounce rebuilds afterward on
		// "resolved" (fires once all links are resolved, including after
		// batches of changes).
		this.app.workspace.onLayoutReady(() => {
			this.index.build();
			this.registerEvent(
				this.app.metadataCache.on("resolved", () =>
					this.index.scheduleRebuild()
				)
			);
			this.registerEvent(
				this.app.vault.on("rename", () => this.index.scheduleRebuild())
			);
			this.registerEvent(
				this.app.vault.on("delete", () => this.index.scheduleRebuild())
			);
		});
	}

	onunload(): void {
		this.cache.destroy();
		clearPprCache();
		this.index.destroy();
	}

	loadVisibleTypes(): Set<WikiPageType> {
		return new Set(this.visibleTypes);
	}

	saveVisibleTypes(types: ReadonlySet<WikiPageType>): void {
		this.visibleTypes = parseVisibleTypes([...types]);
		void this.saveData({
			visibleTypes: serializeVisibleTypes(this.visibleTypes),
		});
	}

	private async activateView(
		viewType: string,
		side: "tab" | "right" = "tab"
	): Promise<void> {
		const existing = this.app.workspace.getLeavesOfType(viewType);
		let leaf: WorkspaceLeaf | null;
		if (existing.length > 0) {
			leaf = existing[0];
		} else {
			leaf =
				side === "right"
					? this.app.workspace.getRightLeaf(false)
					: this.app.workspace.getLeaf("tab");
			if (!leaf) return;
			await leaf.setViewState({ type: viewType, active: true });
		}
		void this.app.workspace.revealLeaf(leaf);
	}
}
