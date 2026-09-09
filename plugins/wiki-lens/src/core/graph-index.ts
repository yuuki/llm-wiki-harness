import { App, Events } from "obsidian";
import {
	classifyDeadTarget,
	normalizeStatus,
	normalizeType,
	subTypeOf,
	tagsOf,
	typeFromPath,
} from "./classify";
import { DeadTarget, IndexStats, NodeMeta, WikiEdge } from "./types";

const WIKI_ROOT = "wiki/";
const REBUILD_DEBOUNCE_MS = 500;

/**
 * Read-only index of the wiki layer (single source of truth).
 * Only scans metadataCache's in-memory data; never reads file bodies.
 *
 * Node targets: md files directly under wiki/{sources,entities,concepts,questions}/
 * (excluding _index). index.md / log.md / hot.md / meta/ / folds/ are excluded
 * from node targets, to prevent catalog/log mega-hubs from distorting degree,
 * clustering, and layout. Only the dead link referrer scan covers the whole of
 * wiki/ (an audit should see everything).
 */
export class GraphIndex extends Events {
	nodes = new Map<string, NodeMeta>();
	edges: WikiEdge[] = [];
	deadTargets = new Map<string, DeadTarget>();
	/** Node basename -> set of paths (used for duplicate-basename detection and @ completion checks) */
	basenameToPaths = new Map<string, string[]>();
	stats: IndexStats | null = null;

	private app: App;
	private rebuildTimer: number | null = null;

	constructor(app: App) {
		super();
		this.app = app;
	}

	get built(): boolean {
		return this.stats !== null;
	}

	scheduleRebuild(): void {
		if (this.rebuildTimer !== null) window.clearTimeout(this.rebuildTimer);
		this.rebuildTimer = window.setTimeout(() => {
			this.rebuildTimer = null;
			this.build();
		}, REBUILD_DEBOUNCE_MS);
	}

	destroy(): void {
		if (this.rebuildTimer !== null) window.clearTimeout(this.rebuildTimer);
	}

	build(): void {
		const t0 = performance.now();
		const cache = this.app.metadataCache;

		const nodes = new Map<string, NodeMeta>();
		const basenameToPaths = new Map<string, string[]>();

		for (const file of this.app.vault.getMarkdownFiles()) {
			const folderType = typeFromPath(file.path, WIKI_ROOT);
			if (folderType === null) continue;
			if (file.basename === "_index") continue;

			const fm = cache.getFileCache(file)?.frontmatter as
				| Record<string, unknown>
				| undefined;
			const type = normalizeType(fm?.["type"], folderType);
			const tags = tagsOf(fm);
			nodes.set(file.path, {
				path: file.path,
				basename: file.basename,
				title:
					typeof fm?.["title"] === "string" && fm["title"]
						? (fm["title"] as string)
						: file.basename,
				type,
				status: normalizeStatus(fm?.["status"]),
				subType: subTypeOf(type, fm),
				isLintStub: tags.includes("lint-stub"),
				created: typeof fm?.["created"] === "string" ? (fm["created"] as string) : "",
				updated: typeof fm?.["updated"] === "string" ? (fm["updated"] as string) : "",
				inDeg: 0,
				outDeg: 0,
				inCount: 0,
				outCount: 0,
				outsideRefs: 0,
			});
			const list = basenameToPaths.get(file.basename);
			if (list) list.push(file.path);
			else basenameToPaths.set(file.basename, [file.path]);
		}

		// Resolved edges: only between node-target pages. Links originating from non-target pages (index.md etc.) are automatically dropped.
		const edges: WikiEdge[] = [];
		let edgeInstances = 0;
		for (const [src, targets] of Object.entries(cache.resolvedLinks)) {
			const srcNode = nodes.get(src);
			if (!srcNode) continue;
			for (const [dst, count] of Object.entries(targets)) {
				const dstNode = nodes.get(dst);
				if (!dstNode) {
					srcNode.outsideRefs += count;
					continue;
				}
				if (dst === src) continue;
				edges.push({ src, dst, count });
				edgeInstances += count;
				srcNode.outDeg += 1;
				dstNode.inDeg += 1;
				srcNode.outCount += count;
				dstNode.inCount += count;
			}
		}

		// Unresolved links: referrers span the whole of wiki/ (index.md and meta are also within audit scope).
		const deadTargets = new Map<string, DeadTarget>();
		let deadRefCount = 0;
		for (const [src, targets] of Object.entries(cache.unresolvedLinks)) {
			if (!src.startsWith(WIKI_ROOT)) continue;
			for (const [target, count] of Object.entries(targets)) {
				let entry = deadTargets.get(target);
				if (!entry) {
					const base = target.split("/").pop() ?? target;
					const hasAtVariant =
						!base.startsWith("@") && basenameToPaths.has("@" + base);
					entry = {
						target,
						cause: classifyDeadTarget(target, hasAtVariant),
						refs: 0,
						referrers: [],
					};
					deadTargets.set(target, entry);
				}
				entry.refs += count;
				deadRefCount += count;
				if (!entry.referrers.includes(src)) entry.referrers.push(src);
			}
		}

		this.nodes = nodes;
		this.edges = edges;
		this.deadTargets = deadTargets;
		this.basenameToPaths = basenameToPaths;
		this.stats = {
			builtAt: Date.now(),
			buildMs: performance.now() - t0,
			fileCount: nodes.size,
			edgeCount: edges.length,
			edgeInstanceCount: edgeInstances,
			deadTargetCount: deadTargets.size,
			deadRefCount,
		};
		this.trigger("updated");
	}
}
