import Graph from "graphology";
import { ItemView, WorkspaceLeaf } from "obsidian";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import SpriteText from "three-spritetext";
import {
	BackboneCache,
	backboneOptsKey,
	BackboneReady,
} from "../core/backbone-cache";
import { DEFAULT_BACKBONE } from "../core/filters";
import { GraphIndex } from "../core/graph-index";
import { nodeSize, nodeWeight } from "../core/metrics";
import { TYPE_COLORS } from "../core/palette";

export const LAYER_VIEW_TYPE = "wiki-lens-layers";

/** Vertical stacking order of the type planes, bottom to top */
const LAYER_ORDER = ["source", "entity", "concept", "question"] as const;
type LayerType = (typeof LAYER_ORDER)[number];

const LAYER_TITLES: Record<LayerType, string> = {
	source: "sources",
	entity: "entities",
	concept: "concepts",
	question: "questions",
};

/** Top-N highest inbound-instance nodes per layer get a permanent label */
const LABELS_PER_LAYER = 5;
/** Pointer movement below this (px) between down and up counts as a click, not an orbit drag */
const CLICK_SLOP_PX = 5;

interface NodeRec {
	path: string;
	label: string;
	layer: number;
	pos: THREE.Vector3;
	/** World-space sphere radius */
	radius: number;
	color: THREE.Color;
	inCount: number;
}

interface EdgeRec {
	a: number;
	b: number;
	inter: boolean;
}

/**
 * 3D multilayer view: each wiki page type is a horizontal plane
 * (sources at the bottom, then entities, concepts, questions), XY positions
 * come from the same frozen 2D ForceAtlas2 layout as the backbone view, and
 * the Z axis carries meaning (type) instead of a free 3D force embedding.
 * Inter-layer edges are the visual protagonist; intra-layer edges are faint.
 * Rendering is pure three.js. No 3D force engine; XY comes from the shared
 * 2D ForceAtlas2 cache (the same coordinates as the backbone view).
 */
export class LayerView extends ItemView {
	private index: GraphIndex;
	private cache: BackboneCache;
	private unsubReady: (() => void) | null = null;
	private awaiting: { builtAt: number; optsKey: string } | null = null;
	private latestPointer: PointerEvent | null = null;

	private renderer: THREE.WebGLRenderer | null = null;
	private scene: THREE.Scene | null = null;
	private camera: THREE.PerspectiveCamera | null = null;
	private controls: OrbitControls | null = null;
	private resizeObserver: ResizeObserver | null = null;
	private layoutFrame: number | null = null;
	private renderPending = false;

	private nodes: NodeRec[] = [];
	private edges: EdgeRec[] = [];
	/** node index -> neighbor node indices (for hover highlighting) */
	private neighbors: Map<number, Set<number>> = new Map();
	private nodeMesh: THREE.InstancedMesh | null = null;
	private intraLines: THREE.LineSegments | null = null;
	private interLines: THREE.LineSegments | null = null;
	private highlightLines: THREE.LineSegments | null = null;
	private hoverLabel: SpriteText | null = null;
	private fixedLabels: SpriteText[] = [];
	private planeMeshes: THREE.Mesh[] = [];
	private planeLabels: SpriteText[] = [];

	private hoveredIdx: number | null = null;
	private layerVisible: boolean[] = LAYER_ORDER.map(() => true);
	private interOnly = false;
	/** Fitted camera home position/target, restored by "Reset view" */
	private homePos = new THREE.Vector3();
	private homeTarget = new THREE.Vector3();

	private containerDiv: HTMLElement | null = null;
	private statsEl: HTMLElement | null = null;
	private layerToggles: { cb: HTMLInputElement; countEl: HTMLElement }[] = [];
	private raycaster = new THREE.Raycaster();
	private pointer = new THREE.Vector2();
	private downX = 0;
	private downY = 0;
	private pointerMovePending = false;

	constructor(leaf: WorkspaceLeaf, index: GraphIndex, cache: BackboneCache) {
		super(leaf);
		this.index = index;
		this.cache = cache;
	}

	getViewType(): string {
		return LAYER_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Wiki Lens: 3D Layers";
	}

	getIcon(): string {
		return "layers";
	}

	async onOpen(): Promise<void> {
		const el = this.contentEl;
		el.empty();
		el.addClass("wiki-lens-graph");

		this.buildToolbar(el);
		this.containerDiv = el.createDiv({ cls: "wiki-lens-graph-container" });

		this.registerEvent(this.index.on("updated", () => this.rebuild()));
		this.unsubReady = this.cache.onReady((ready) => this.onCacheReady(ready));
		this.rebuild();
	}

	async onClose(): Promise<void> {
		this.unsubReady?.();
		this.unsubReady = null;
		this.stopLayout();
		this.resizeObserver?.disconnect();
		this.resizeObserver = null;
		this.disposeScene();
		this.controls?.dispose();
		this.controls = null;
		this.renderer?.dispose();
		this.renderer = null;
		this.camera = null;
	}

	private buildToolbar(el: HTMLElement): void {
		const bar = el.createDiv({ cls: "wiki-lens-toolbar" });

		this.layerToggles = [];
		for (let i = LAYER_ORDER.length - 1; i >= 0; i--) {
			const layer = i;
			const wrap = bar.createEl("label", { cls: "wiki-lens-toggle" });
			const cb = wrap.createEl("input", { type: "checkbox" });
			cb.checked = true;
			const swatch = wrap.createSpan({ cls: "wiki-lens-layer-swatch" });
			swatch.style.backgroundColor = TYPE_COLORS[LAYER_ORDER[layer]];
			wrap.createSpan({ text: LAYER_TITLES[LAYER_ORDER[layer]] });
			const countEl = wrap.createSpan({
				cls: "wiki-lens-muted",
				text: "",
			});
			cb.onchange = () => {
				this.layerVisible[layer] = cb.checked;
				this.applyVisibility();
			};
			this.layerToggles[layer] = { cb, countEl };
		}

		const interWrap = bar.createEl("label", { cls: "wiki-lens-toggle" });
		const interCb = interWrap.createEl("input", { type: "checkbox" });
		interCb.checked = this.interOnly;
		interWrap.createSpan({ text: " Inter-layer edges only" });
		interCb.onchange = () => {
			this.interOnly = interCb.checked;
			this.applyVisibility();
		};

		const reset = bar.createEl("button", { text: "Reset view" });
		reset.onclick = () => this.resetCamera();

		const rebuild = bar.createEl("button", { text: "Rebuild scene" });
		rebuild.title =
			"Rebuild the 3D scene from the shared layout. Does not re-run ForceAtlas2.";
		rebuild.onclick = () => this.rebuild();

		this.statsEl = bar.createSpan({ cls: "wiki-lens-muted" });
	}

	/* --- Graph construction: frozen 2D layout, then a one-time 3D scene build --- */

	private rebuild(): void {
		if (!this.containerDiv) return;
		if (!this.index.built) {
			this.statsEl?.setText("Building index…");
			return;
		}
		this.stopLayout();

		const handle = this.cache.acquire(this.index, DEFAULT_BACKBONE);
		if (!handle) {
			this.statsEl?.setText("Building index…");
			return;
		}
		if (!handle.laidOut) {
			this.awaiting = { builtAt: handle.builtAt, optsKey: handle.optsKey };
			this.statsEl?.setText("Computing layout…");
			return;
		}
		this.awaiting = null;
		this.buildScene(handle.graph);
	}

	private onCacheReady(ready: BackboneReady): void {
		if (!this.awaiting) return;
		if (ready.builtAt !== this.awaiting.builtAt) return;
		if (ready.optsKey !== this.awaiting.optsKey) return;
		if (ready.optsKey !== backboneOptsKey(DEFAULT_BACKBONE)) return;
		const handle = this.cache.acquire(this.index, DEFAULT_BACKBONE);
		if (!handle || !handle.laidOut) return;
		this.awaiting = null;
		this.buildScene(handle.graph);
	}

	private stopLayout(): void {
		if (this.layoutFrame !== null) {
			window.cancelAnimationFrame(this.layoutFrame);
			this.layoutFrame = null;
		}
	}

	private buildScene(graph: Graph): void {
		const container = this.containerDiv;
		if (!container) return;

		if (!this.renderer && !this.initRenderer(container)) return;
		this.disposeScene();

		// Collect nodes; FA2 (x, y) becomes world (x, layerY, z=y)
		const layerOf = new Map<string, number>();
		LAYER_ORDER.forEach((t, i) => layerOf.set(t, i));

		const idxOf = new Map<string, number>();
		const nodes: NodeRec[] = [];
		let minX = Infinity,
			maxX = -Infinity,
			minZ = Infinity,
			maxZ = -Infinity;
		graph.forEachNode((node, attrs) => {
			const layer = layerOf.get(attrs["nodeType"] as string);
			if (layer === undefined) return;
			const x = attrs["x"] as number;
			const z = attrs["y"] as number;
			minX = Math.min(minX, x);
			maxX = Math.max(maxX, x);
			minZ = Math.min(minZ, z);
			maxZ = Math.max(maxZ, z);
			idxOf.set(node, nodes.length);
			nodes.push({
				path: node,
				label: attrs["label"] as string,
				layer,
				pos: new THREE.Vector3(x, 0, z),
				radius: 0,
				color: new THREE.Color(
					TYPE_COLORS[attrs["nodeType"] as string] ?? "#888888"
				),
				inCount: nodeWeight(attrs),
			});
		});
		if (nodes.length === 0) {
			this.statsEl?.setText("No nodes");
			return;
		}

		// Center the XZ footprint at the origin and derive all world scales
		// from its span, so node/label/plane sizes stay proportionate no
		// matter how far FA2 spread the layout
		const cx = (minX + maxX) / 2;
		const cz = (minZ + maxZ) / 2;
		const span = Math.max(maxX - minX, maxZ - minZ, 1);
		const layerGap = span * 0.35;
		const nodeScale = span / 900;
		for (const n of nodes) {
			n.pos.x -= cx;
			n.pos.z -= cz;
			n.pos.y = n.layer * layerGap;
			n.radius = nodeSize(n.inCount) * nodeScale;
		}

		const edges: EdgeRec[] = [];
		const neighbors = new Map<number, Set<number>>();
		graph.forEachEdge((_e, _attrs, src, dst) => {
			const a = idxOf.get(src);
			const b = idxOf.get(dst);
			if (a === undefined || b === undefined) return;
			edges.push({ a, b, inter: nodes[a].layer !== nodes[b].layer });
			if (!neighbors.has(a)) neighbors.set(a, new Set());
			if (!neighbors.has(b)) neighbors.set(b, new Set());
			neighbors.get(a)?.add(b);
			neighbors.get(b)?.add(a);
		});

		this.nodes = nodes;
		this.edges = edges;
		this.neighbors = neighbors;
		this.hoveredIdx = null;

		const scene = new THREE.Scene();
		this.scene = scene;
		scene.add(new THREE.AmbientLight(0xffffff, 1.2));
		const dir = new THREE.DirectionalLight(0xffffff, 1.0);
		dir.position.set(1, 2, 1.5);
		scene.add(dir);

		// Nodes as one instanced low-poly sphere mesh (a few thousand instances)
		const sphereGeo = new THREE.SphereGeometry(1, 12, 8);
		const nodeMat = new THREE.MeshLambertMaterial();
		const mesh = new THREE.InstancedMesh(sphereGeo, nodeMat, nodes.length);
		mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
		this.nodeMesh = mesh;
		this.writeInstanceTransforms();
		this.writeInstanceColors();
		scene.add(mesh);

		// Layer planes + their name labels
		const half = span * 0.62;
		const textColor = this.cssVar("--text-normal", "#dcddde");
		const counts = LAYER_ORDER.map(() => 0);
		for (const n of nodes) counts[n.layer]++;
		this.planeMeshes = [];
		this.planeLabels = [];
		for (let i = 0; i < LAYER_ORDER.length; i++) {
			if (counts[i] === 0) continue;
			const planeGeo = new THREE.PlaneGeometry(half * 2, half * 2);
			const planeMat = new THREE.MeshBasicMaterial({
				color: new THREE.Color(TYPE_COLORS[LAYER_ORDER[i]]),
				transparent: true,
				opacity: 0.05,
				side: THREE.DoubleSide,
				depthWrite: false,
			});
			const plane = new THREE.Mesh(planeGeo, planeMat);
			plane.rotation.x = -Math.PI / 2;
			plane.position.y = i * layerGap;
			plane.renderOrder = -1;
			plane.userData["layer"] = i;
			scene.add(plane);
			this.planeMeshes.push(plane);

			// Far corner from the home camera (which sits at +x/+z), so the
			// label reads as a plane caption instead of looming over the scene
			const label = new SpriteText(
				`${LAYER_TITLES[LAYER_ORDER[i]]} (${counts[i]})`,
				span * 0.022,
				textColor
			);
			label.position.set(-half, i * layerGap + span * 0.02, -half);
			label.material.depthTest = false;
			label.userData["layer"] = i;
			scene.add(label);
			this.planeLabels.push(label);
			this.layerToggles[i]?.countEl.setText(` ${counts[i]}`);
		}

		this.rebuildEdgeLines();

		// Permanent labels for the top-N inbound-instance nodes of each layer
		this.fixedLabels = [];
		const byLayer = new Map<number, NodeRec[]>();
		nodes.forEach((n) => {
			if (!byLayer.has(n.layer)) byLayer.set(n.layer, []);
			byLayer.get(n.layer)?.push(n);
		});
		for (const list of byLayer.values()) {
			list.sort((a, b) => b.inCount - a.inCount);
			for (const n of list.slice(0, LABELS_PER_LAYER)) {
				const label = new SpriteText(n.label, span * 0.018, textColor);
				label.position.copy(n.pos);
				label.position.y += n.radius + span * 0.015;
				label.material.depthTest = false;
				label.userData["layer"] = n.layer;
				scene.add(label);
				this.fixedLabels.push(label);
			}
		}

		// Hover label (single reusable sprite)
		const hover = new SpriteText("", span * 0.022, textColor);
		hover.backgroundColor = this.cssVar(
			"--background-secondary",
			"#2a2a2a"
		);
		hover.padding = 2;
		hover.borderRadius = 3;
		hover.material.depthTest = false;
		hover.visible = false;
		scene.add(hover);
		this.hoverLabel = hover;

		// Camera framing: look at the stack's center from a raised diagonal
		const maxLayer = LAYER_ORDER.length - 1;
		const center = new THREE.Vector3(0, (maxLayer * layerGap) / 2, 0);
		const fit = span * 1.35;
		this.homeTarget.copy(center);
		this.homePos.set(fit, center.y + fit * 0.55, fit);
		if (!this.camera) {
			this.camera = new THREE.PerspectiveCamera(50, 1, span / 1000, span * 20);
			this.attachControls();
			this.resetCamera();
		} else {
			this.camera.near = span / 1000;
			this.camera.far = span * 20;
			this.camera.updateProjectionMatrix();
		}

		this.applyVisibility();
		this.updateStats();
		this.requestRender();
	}

	private initRenderer(container: HTMLElement): boolean {
		let renderer: THREE.WebGLRenderer;
		try {
			renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
		} catch (e) {
			console.warn("wiki-lens: WebGL init failed", e);
			container.createDiv({
				cls: "wiki-lens-loading",
				text: "WebGL is unavailable; the 3D layer view cannot render.",
			});
			return false;
		}
		renderer.setPixelRatio(window.devicePixelRatio);
		renderer.setClearColor(0x000000, 0);
		renderer.domElement.style.width = "100%";
		renderer.domElement.style.height = "100%";
		renderer.domElement.style.display = "block";
		container.appendChild(renderer.domElement);
		this.renderer = renderer;

		this.resizeObserver = new ResizeObserver(() => this.handleResize());
		this.resizeObserver.observe(container);
		this.handleResize();

		renderer.domElement.addEventListener("pointermove", (ev) =>
			this.onPointerMove(ev)
		);
		renderer.domElement.addEventListener("pointerdown", (ev) => {
			this.downX = ev.clientX;
			this.downY = ev.clientY;
		});
		renderer.domElement.addEventListener("pointerup", (ev) => {
			if (
				Math.abs(ev.clientX - this.downX) > CLICK_SLOP_PX ||
				Math.abs(ev.clientY - this.downY) > CLICK_SLOP_PX
			) {
				return;
			}
			const idx = this.pickNode(ev);
			if (idx !== null) {
				void this.app.workspace.openLinkText(
					this.nodes[idx].path,
					"/",
					false
				);
			}
		});
		return true;
	}

	private attachControls(): void {
		if (!this.camera || !this.renderer) return;
		this.controls = new OrbitControls(this.camera, this.renderer.domElement);
		// Damping needs a continuous render loop; this view renders on demand
		this.controls.enableDamping = false;
		this.controls.addEventListener("change", () => this.requestRender());
	}

	/* --- Rendering: on demand only (no persistent rAF loop) --- */

	private requestRender(): void {
		if (this.renderPending) return;
		this.renderPending = true;
		window.requestAnimationFrame(() => {
			this.renderPending = false;
			if (this.renderer && this.scene && this.camera) {
				this.renderer.render(this.scene, this.camera);
			}
		});
	}

	private handleResize(): void {
		const container = this.containerDiv;
		if (!container || !this.renderer) return;
		const w = container.clientWidth;
		const h = container.clientHeight;
		if (w === 0 || h === 0) return;
		this.renderer.setSize(w, h, false);
		if (this.camera) {
			this.camera.aspect = w / h;
			this.camera.updateProjectionMatrix();
		}
		this.requestRender();
	}

	private resetCamera(): void {
		if (!this.camera || !this.controls) return;
		this.camera.position.copy(this.homePos);
		this.controls.target.copy(this.homeTarget);
		this.controls.update();
		this.requestRender();
	}

	/* --- Instance + edge geometry updates --- */

	private writeInstanceTransforms(): void {
		const mesh = this.nodeMesh;
		if (!mesh) return;
		const m = new THREE.Matrix4();
		this.nodes.forEach((n, i) => {
			// Hidden layers collapse to zero scale: invisible and unpickable
			// without rebuilding the instanced mesh
			const s = this.layerVisible[n.layer] ? n.radius : 0;
			m.makeScale(s, s, s);
			m.setPosition(n.pos);
			mesh.setMatrixAt(i, m);
		});
		mesh.instanceMatrix.needsUpdate = true;
		mesh.computeBoundingSphere();
	}

	private writeInstanceColors(): void {
		const mesh = this.nodeMesh;
		if (!mesh) return;
		const dim = new THREE.Color(0x3c3c3c);
		const hovered = this.hoveredIdx;
		const nbs = hovered !== null ? this.neighbors.get(hovered) : null;
		this.nodes.forEach((n, i) => {
			const keep =
				hovered === null || i === hovered || nbs?.has(i) === true;
			mesh.setColorAt(i, keep ? n.color : dim);
		});
		if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
	}

	private rebuildEdgeLines(): void {
		const scene = this.scene;
		if (!scene) return;
		if (this.intraLines) {
			scene.remove(this.intraLines);
			this.intraLines.geometry.dispose();
			(this.intraLines.material as THREE.Material).dispose();
			this.intraLines = null;
		}
		if (this.interLines) {
			scene.remove(this.interLines);
			this.interLines.geometry.dispose();
			(this.interLines.material as THREE.Material).dispose();
			this.interLines = null;
		}

		const intra: number[] = [];
		const inter: number[] = [];
		for (const e of this.edges) {
			const na = this.nodes[e.a];
			const nb = this.nodes[e.b];
			if (!this.layerVisible[na.layer] || !this.layerVisible[nb.layer]) {
				continue;
			}
			const out = e.inter ? inter : intra;
			out.push(na.pos.x, na.pos.y, na.pos.z, nb.pos.x, nb.pos.y, nb.pos.z);
		}

		const mkLines = (
			coords: number[],
			color: number,
			opacity: number
		): THREE.LineSegments => {
			const geo = new THREE.BufferGeometry();
			geo.setAttribute(
				"position",
				new THREE.Float32BufferAttribute(coords, 3)
			);
			const mat = new THREE.LineBasicMaterial({
				color,
				transparent: true,
				opacity,
			});
			const lines = new THREE.LineSegments(geo, mat);
			scene.add(lines);
			return lines;
		};
		this.intraLines = mkLines(intra, 0x808080, 0.07);
		this.interLines = mkLines(inter, 0xc8a03c, 0.28);
		this.intraLines.visible = !this.interOnly;
	}

	private applyVisibility(): void {
		this.writeInstanceTransforms();
		this.rebuildEdgeLines();
		for (const plane of this.planeMeshes) {
			plane.visible = this.layerVisible[plane.userData["layer"] as number];
		}
		for (const label of this.planeLabels) {
			label.visible = this.layerVisible[label.userData["layer"] as number];
		}
		for (const label of this.fixedLabels) {
			label.visible = this.layerVisible[label.userData["layer"] as number];
		}
		this.clearHover();
		this.updateStats();
		this.requestRender();
	}

	private updateStats(): void {
		let nodeCount = 0;
		for (const n of this.nodes) {
			if (this.layerVisible[n.layer]) nodeCount++;
		}
		let edgeCount = 0;
		let interCount = 0;
		for (const e of this.edges) {
			if (
				!this.layerVisible[this.nodes[e.a].layer] ||
				!this.layerVisible[this.nodes[e.b].layer]
			) {
				continue;
			}
			edgeCount++;
			if (e.inter) interCount++;
		}
		this.statsEl?.setText(
			`Nodes ${nodeCount} / edges ${edgeCount} (inter-layer ${interCount})`
		);
	}

	/* --- Picking & hover --- */

	private pickNode(ev: PointerEvent): number | null {
		const renderer = this.renderer;
		const camera = this.camera;
		const mesh = this.nodeMesh;
		if (!renderer || !camera || !mesh) return null;
		const rect = renderer.domElement.getBoundingClientRect();
		this.pointer.set(
			((ev.clientX - rect.left) / rect.width) * 2 - 1,
			-((ev.clientY - rect.top) / rect.height) * 2 + 1
		);
		this.raycaster.setFromCamera(this.pointer, camera);
		const hits = this.raycaster.intersectObject(mesh);
		const id = hits.length > 0 ? hits[0].instanceId : undefined;
		return id === undefined ? null : id;
	}

	private onPointerMove(ev: PointerEvent): void {
		// Raycasting against thousands of instances is not free; coalesce
		// pointermove bursts to one pick per frame, using the latest event
		this.latestPointer = ev;
		if (this.pointerMovePending) return;
		this.pointerMovePending = true;
		window.requestAnimationFrame(() => {
			this.pointerMovePending = false;
			const latest = this.latestPointer;
			if (!latest) return;
			const idx = this.pickNode(latest);
			if (idx === this.hoveredIdx) return;
			this.hoveredIdx = idx;
			if (this.renderer) {
				this.renderer.domElement.style.cursor =
					idx === null ? "" : "pointer";
			}
			this.writeInstanceColors();
			this.updateHighlightLines();
			this.updateHoverLabel();
			this.requestRender();
		});
	}

	private clearHover(): void {
		if (this.hoveredIdx === null) return;
		this.hoveredIdx = null;
		this.writeInstanceColors();
		this.updateHighlightLines();
		this.updateHoverLabel();
	}

	private updateHoverLabel(): void {
		const label = this.hoverLabel;
		if (!label) return;
		if (this.hoveredIdx === null) {
			label.visible = false;
			return;
		}
		const n = this.nodes[this.hoveredIdx];
		label.text = n.label;
		label.position.copy(n.pos);
		label.position.y += n.radius * 2.5;
		label.visible = true;
	}

	private updateHighlightLines(): void {
		const scene = this.scene;
		if (!scene) return;
		if (this.highlightLines) {
			scene.remove(this.highlightLines);
			this.highlightLines.geometry.dispose();
			(this.highlightLines.material as THREE.Material).dispose();
			this.highlightLines = null;
		}
		if (this.hoveredIdx === null) return;

		const coords: number[] = [];
		for (const e of this.edges) {
			if (e.a !== this.hoveredIdx && e.b !== this.hoveredIdx) continue;
			const na = this.nodes[e.a];
			const nb = this.nodes[e.b];
			if (!this.layerVisible[na.layer] || !this.layerVisible[nb.layer]) {
				continue;
			}
			coords.push(na.pos.x, na.pos.y, na.pos.z, nb.pos.x, nb.pos.y, nb.pos.z);
		}
		const geo = new THREE.BufferGeometry();
		geo.setAttribute("position", new THREE.Float32BufferAttribute(coords, 3));
		const mat = new THREE.LineBasicMaterial({
			color: 0xe8b84b,
			transparent: true,
			opacity: 0.9,
		});
		this.highlightLines = new THREE.LineSegments(geo, mat);
		scene.add(this.highlightLines);
	}

	/* --- Cleanup --- */

	/** Frees all scene-owned GPU resources. The renderer itself survives rebuilds */
	private disposeScene(): void {
		const scene = this.scene;
		if (!scene) return;
		scene.traverse((obj) => {
			if (obj instanceof THREE.Mesh || obj instanceof THREE.LineSegments) {
				obj.geometry.dispose();
				const mat = obj.material as THREE.Material | THREE.Material[];
				if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
				else mat.dispose();
			}
			if (obj instanceof THREE.Sprite) {
				obj.material.map?.dispose();
				obj.material.dispose();
			}
		});
		scene.clear();
		this.scene = null;
		this.nodeMesh = null;
		this.intraLines = null;
		this.interLines = null;
		this.highlightLines = null;
		this.hoverLabel = null;
		this.fixedLabels = [];
		this.planeMeshes = [];
		this.planeLabels = [];
	}

	private cssVar(name: string, fallback: string): string {
		const v = getComputedStyle(document.body).getPropertyValue(name).trim();
		return v || fallback;
	}
}
