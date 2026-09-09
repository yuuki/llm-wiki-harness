import { setIcon } from "obsidian";
import { TimeControlsState, TimeMode } from "../core/types";

const DEFAULT_RECENT_WINDOW = 3;
const DEFAULT_SPEED_MS = 400;

const SPEED_OPTIONS: { ms: number; label: string }[] = [
	{ ms: 200, label: "Fast" },
	{ ms: 400, label: "Normal" },
	{ ms: 800, label: "Slow" },
];

const RECENT_WINDOW_OPTIONS = [1, 3, 7];

/** Number of days to look back for the "1 week" diff preset */
const DIFF_WEEK_DAYS = 7;

/**
 * Permanent note that link appearance time and the "updated" classification
 * are estimated/proxy signals. Edge time is estimated from the created date
 * of both endpoint nodes, so it can diverge from the actual time the link
 * was added. Shown always visible on the time-bar to avoid misleading users.
 */
const TIME_ACCURACY_NOTE =
	"Link appearance time is estimated from the creation date of both endpoint pages; the actual link may have been added later. " +
	"'Updated' is a proxy signal derived from the updated frontmatter field and does not necessarily reflect content growth.";

export interface TimeControlsOptions {
	/** Notified with the full state on every state change (including each step of the playback timer) */
	onChange: (state: TimeControlsState) => void;
}

/**
 * DOM component for the time-bar (tabs, slider, playback, presets).
 * Does not depend on sigma / graphology; state changes are only notified outward via callback.
 *
 * The control row's content is entirely swapped depending on the mode (replay row / diff row).
 * In diff mode there's no concept of "picking a single point in time", so the slider's meaning
 * would change; the entire replay row (including the slider) is hidden instead
 * (simpler than hiding just the play button and recent-highlight select individually — the diff
 * row can then be self-contained with just the date selects, swap, and presets, as designed).
 */
export class TimeControls {
	private opts: TimeControlsOptions;

	private days: number[] = [];
	private dayToDateFn: (day: number) => string = (d) => String(d);
	private eventDays: Set<number> = new Set();

	private mode: TimeMode = "replay";
	private tIndex = 0;
	private aIndex = 0;
	private bIndex = 0;
	private recentWindowDays = DEFAULT_RECENT_WINDOW;

	private enabled = true;
	private playing = false;
	private timer: number | null = null;
	private speedMs = DEFAULT_SPEED_MS;

	private replayTabBtn: HTMLButtonElement;
	private diffTabBtn: HTMLButtonElement;
	private replayPanel: HTMLElement;
	private diffPanel: HTMLElement;

	private playBtn: HTMLButtonElement;
	private sliderInput: HTMLInputElement;
	private markersEl: HTMLElement;
	private dateLabelEl: HTMLElement;
	private speedSelect: HTMLSelectElement;
	private recentSelect: HTMLSelectElement;

	private aSelect: HTMLSelectElement;
	private bSelect: HTMLSelectElement;
	private swapBtn: HTMLButtonElement;
	private presetRecentBtn: HTMLButtonElement;
	private presetWeekBtn: HTMLButtonElement;
	private presetAllBtn: HTMLButtonElement;

	private statusEl: HTMLElement;

	constructor(parent: HTMLElement, opts: TimeControlsOptions) {
		this.opts = opts;

		const bar = parent.createDiv({ cls: "wiki-lens-time-bar" });

		// --- Tab row + note icon ---
		const tabRow = bar.createDiv({ cls: "wiki-lens-time-row" });
		this.replayTabBtn = tabRow.createEl("button", {
			cls: "wiki-lens-mode-btn",
			text: "Replay",
		});
		this.replayTabBtn.onclick = () => this.setMode("replay");
		this.diffTabBtn = tabRow.createEl("button", {
			cls: "wiki-lens-mode-btn",
			text: "Diff",
		});
		this.diffTabBtn.onclick = () => this.setMode("diff");

		const noteIcon = tabRow.createSpan({ cls: "wiki-lens-time-note" });
		setIcon(noteIcon, "info");
		noteIcon.setAttribute("aria-label", TIME_ACCURACY_NOTE);

		// --- Replay row ---
		this.replayPanel = bar.createDiv();
		const replayRow1 = this.replayPanel.createDiv({ cls: "wiki-lens-time-row" });

		this.playBtn = replayRow1.createEl("button");
		setIcon(this.playBtn, "play");
		this.playBtn.setAttribute("aria-label", "Play");
		this.playBtn.onclick = () => this.togglePlay();

		const sliderWrap = replayRow1.createDiv({ cls: "wiki-lens-time-slider-wrap" });
		this.sliderInput = sliderWrap.createEl("input", {
			type: "range",
			cls: "wiki-lens-time-slider",
		});
		this.sliderInput.min = "0";
		this.sliderInput.max = "0";
		this.sliderInput.value = "0";
		this.sliderInput.oninput = () => this.onSliderInput();
		this.markersEl = sliderWrap.createDiv({ cls: "wiki-lens-time-markers" });

		this.dateLabelEl = replayRow1.createSpan({ cls: "wiki-lens-time-date" });

		const replayRow2 = this.replayPanel.createDiv({ cls: "wiki-lens-time-row" });
		replayRow2.createSpan({ text: "Speed" });
		this.speedSelect = replayRow2.createEl("select");
		for (const s of SPEED_OPTIONS) {
			this.speedSelect.createEl("option", {
				value: String(s.ms),
				text: s.label,
			});
		}
		this.speedSelect.value = String(this.speedMs);
		this.speedSelect.onchange = () => this.onSpeedChange();

		replayRow2.createSpan({ text: "Recent highlight" });
		this.recentSelect = replayRow2.createEl("select");
		for (const n of RECENT_WINDOW_OPTIONS) {
			this.recentSelect.createEl("option", {
				value: String(n),
				text: `${n} day${n === 1 ? "" : "s"}`,
			});
		}
		this.recentSelect.value = String(this.recentWindowDays);
		this.recentSelect.onchange = () => {
			this.recentWindowDays = Number(this.recentSelect.value);
			this.emitChange();
		};

		// --- Diff row ---
		this.diffPanel = bar.createDiv();
		const diffRow1 = this.diffPanel.createDiv({ cls: "wiki-lens-time-row" });
		diffRow1.createSpan({ text: "A" });
		this.aSelect = diffRow1.createEl("select");
		this.aSelect.onchange = () => {
			this.setAB(Number(this.aSelect.value), this.bIndex);
			this.syncDiffSelectValues();
			this.emitChange();
		};

		this.swapBtn = diffRow1.createEl("button");
		setIcon(this.swapBtn, "arrow-left-right");
		this.swapBtn.setAttribute("aria-label", "Swap A/B");
		this.swapBtn.onclick = () => {
			this.setAB(this.bIndex, this.aIndex);
			this.syncDiffSelectValues();
			this.emitChange();
		};

		diffRow1.createSpan({ text: "B" });
		this.bSelect = diffRow1.createEl("select");
		this.bSelect.onchange = () => {
			this.setAB(this.aIndex, Number(this.bSelect.value));
			this.syncDiffSelectValues();
			this.emitChange();
		};

		const diffRow2 = this.diffPanel.createDiv({ cls: "wiki-lens-time-row" });
		this.presetRecentBtn = diffRow2.createEl("button", { text: "Yesterday → Today" });
		this.presetRecentBtn.onclick = () => this.applyPresetRecent();
		this.presetWeekBtn = diffRow2.createEl("button", { text: "1 week" });
		this.presetWeekBtn.onclick = () => this.applyPresetWeek();
		this.presetAllBtn = diffRow2.createEl("button", { text: "Full range" });
		this.presetAllBtn.onclick = () => this.applyPresetAll();

		// --- Status row ---
		const statusRow = bar.createDiv({ cls: "wiki-lens-time-row" });
		this.statusEl = statusRow.createSpan({ cls: "wiki-lens-muted" });

		this.updateModeUI();
		this.applyEnabledState();
	}

	/**
	 * (Re)configures the axis. By default resets to the last day. When
	 * `preserve` is true, remaps the previous t/A/B day values onto the new
	 * axis so an index rebuild does not jump the scrubber.
	 */
	setDays(
		days: number[],
		dayToDate: (day: number) => string,
		preserve = false
	): void {
		this.stopPlayback();
		const prevDays = this.days;
		const prevState = this.getState();
		this.days = [...days];
		this.dayToDateFn = dayToDate;

		if (this.days.length === 0) {
			this.mode = "replay";
			this.tIndex = 0;
			this.aIndex = 0;
			this.bIndex = 0;
			this.recentWindowDays = DEFAULT_RECENT_WINDOW;
			this.updateModeUI();
			this.rebuildForDays();
			this.applyEnabledState();
			this.setStatus("No time data");
			return;
		}

		if (preserve && prevDays.length > 0) {
			this.mode = prevState.mode;
			this.recentWindowDays = prevState.recentWindowDays;
			this.tIndex = nearestDayIndex(
				this.days,
				prevDays[prevState.tIndex],
				this.days.length - 1
			);
			this.bIndex = nearestDayIndex(
				this.days,
				prevDays[prevState.bIndex],
				this.days.length - 1
			);
			this.aIndex = nearestDayIndex(
				this.days,
				prevDays[prevState.aIndex],
				Math.max(0, this.bIndex - 1)
			);
			if (this.aIndex >= this.bIndex) {
				this.aIndex = Math.max(0, this.bIndex - 1);
			}
			this.recentSelect.value = String(this.recentWindowDays);
		} else {
			this.mode = "replay";
			this.tIndex = this.days.length - 1;
			this.bIndex = this.days.length - 1;
			this.aIndex = this.days.length >= 2 ? this.days.length - 2 : 0;
			this.recentWindowDays = DEFAULT_RECENT_WINDOW;
		}

		this.updateModeUI();
		this.rebuildForDays();
		this.applyEnabledState();
		this.emitChange();
	}

	/** Shows a marker dot on days that have a log event (only effective for days included in `days`) */
	setEventDays(eventDays: Set<number>): void {
		this.eventDays = eventDays;
		this.renderMarkers();
	}

	/** Displays a status message */
	setStatus(text: string): void {
		this.statusEl.setText(text);
	}

	/** Disables the controls, e.g. while computing layout. setEnabled(false) also stops playback */
	setEnabled(enabled: boolean): void {
		this.enabled = enabled;
		if (!enabled) this.stopPlayback();
		this.applyEnabledState();
	}

	isPlaying(): boolean {
		return this.playing;
	}

	/** Jump Replay to a day index. Stops playback if it is running. */
	seekTo(index: number): void {
		if (!this.enabled || this.days.length === 0) return;
		if (this.mode !== "replay") return;
		const next = Math.max(0, Math.min(Math.round(index), this.days.length - 1));
		if (this.playing) this.stopPlayback();
		if (next === this.tIndex) return;
		this.tIndex = next;
		this.sliderInput.value = String(this.tIndex);
		this.updateDateLabel();
		this.emitChange();
	}

	getState(): TimeControlsState {
		return {
			mode: this.mode,
			tIndex: this.tIndex,
			aIndex: this.aIndex,
			bIndex: this.bIndex,
			recentWindowDays: this.recentWindowDays,
		};
	}

	/** Stops playback and disposes of the timer (call from the view's onClose) */
	destroy(): void {
		this.stopPlayback();
	}

	// --- Mode switching ---

	private setMode(mode: TimeMode): void {
		if (this.mode === mode) return;
		this.stopPlayback();
		this.mode = mode;
		this.updateModeUI();
		this.emitChange();
	}

	private updateModeUI(): void {
		this.replayTabBtn.toggleClass(
			"wiki-lens-mode-active",
			this.mode === "replay"
		);
		this.diffTabBtn.toggleClass("wiki-lens-mode-active", this.mode === "diff");
		this.replayPanel.style.display = this.mode === "replay" ? "" : "none";
		this.diffPanel.style.display = this.mode === "diff" ? "" : "none";
	}

	// --- Replay: slider, playback ---

	private onSliderInput(): void {
		if (this.days.length === 0) return;
		const next = Number(this.sliderInput.value);
		if (next === this.tIndex) return;
		if (this.playing) this.stopPlayback();
		this.tIndex = next;
		this.updateDateLabel();
		this.emitChange();
	}

	private togglePlay(): void {
		if (this.days.length <= 1) return;
		if (this.playing) {
			this.stopPlayback();
			return;
		}

		if (this.tIndex >= this.days.length - 1) {
			this.tIndex = 0;
			this.sliderInput.value = "0";
			this.updateDateLabel();
			this.emitChange();
		}

		this.playing = true;
		setIcon(this.playBtn, "pause");
		this.playBtn.setAttribute("aria-label", "Pause");
		this.timer = window.setInterval(() => this.stepPlay(), this.speedMs);
	}

	private stepPlay(): void {
		if (this.tIndex >= this.days.length - 1) {
			this.stopPlayback();
			return;
		}
		this.tIndex += 1;
		this.sliderInput.value = String(this.tIndex);
		this.updateDateLabel();
		this.emitChange();
		if (this.tIndex >= this.days.length - 1) {
			this.stopPlayback();
		}
	}

	private stopPlayback(): void {
		if (this.timer !== null) {
			window.clearInterval(this.timer);
			this.timer = null;
		}
		if (this.playing) {
			this.playing = false;
			setIcon(this.playBtn, "play");
			this.playBtn.setAttribute("aria-label", "Play");
		}
	}

	private onSpeedChange(): void {
		this.speedMs = Number(this.speedSelect.value);
		// Speed isn't part of the state (TimeControlsState), so onChange isn't fired;
		// if currently playing, just re-arm the timer to reflect the new interval
		if (this.playing && this.timer !== null) {
			window.clearInterval(this.timer);
			this.timer = window.setInterval(() => this.stepPlay(), this.speedMs);
		}
	}

	// --- Diff: A/B, swap, presets ---

	/**
	 * Always keeps aIndex < bIndex. Whether called from an individual select change,
	 * swap, or a preset, the same correction (pulling A to one before B) suffices,
	 * so the update path is unified into one.
	 */
	private setAB(newA: number, newB: number): void {
		let a = newA;
		const b = newB;
		if (a >= b) a = Math.max(0, b - 1);
		this.aIndex = a;
		this.bIndex = b;
	}

	private syncDiffSelectValues(): void {
		this.aSelect.value = String(this.aIndex);
		this.bSelect.value = String(this.bIndex);
	}

	private applyPresetRecent(): void {
		if (this.days.length === 0) return;
		const b = this.days.length - 1;
		const a = this.days.length >= 2 ? b - 1 : 0;
		this.setAB(a, b);
		this.syncDiffSelectValues();
		this.emitChange();
	}

	private applyPresetWeek(): void {
		if (this.days.length === 0) return;
		const b = this.days.length - 1;
		const threshold = this.days[b] - DIFF_WEEK_DAYS;
		let a = 0;
		for (let i = b; i >= 0; i--) {
			if (this.days[i] <= threshold) {
				a = i;
				break;
			}
		}
		this.setAB(a, b);
		this.syncDiffSelectValues();
		this.emitChange();
	}

	private applyPresetAll(): void {
		if (this.days.length === 0) return;
		this.setAB(0, this.days.length - 1);
		this.syncDiffSelectValues();
		this.emitChange();
	}

	// --- Axis rebuild, display update ---

	private rebuildForDays(): void {
		const last = Math.max(this.days.length - 1, 0);
		this.sliderInput.min = "0";
		this.sliderInput.max = String(last);
		this.sliderInput.value = String(this.tIndex);
		this.updateDateLabel();
		this.renderMarkers();
		this.rebuildDiffSelects();
	}

	private updateDateLabel(): void {
		if (this.days.length === 0) {
			this.dateLabelEl.setText("");
			return;
		}
		this.dateLabelEl.setText(this.dayToDateFn(this.days[this.tIndex]));
	}

	private renderMarkers(): void {
		this.markersEl.empty();
		if (this.days.length === 0) return;

		const last = this.days.length - 1;
		if (last === 0) {
			if (this.eventDays.has(this.days[0])) {
				const dot = this.markersEl.createDiv({ cls: "wiki-lens-time-marker" });
				dot.style.left = "0%";
			}
			return;
		}

		for (let i = 0; i <= last; i++) {
			const day = this.days[i];
			if (!this.eventDays.has(day)) continue;
			const dot = this.markersEl.createDiv({ cls: "wiki-lens-time-marker" });
			dot.style.left = `${(i / last) * 100}%`;
		}
	}

	private rebuildDiffSelects(): void {
		this.aSelect.empty();
		this.bSelect.empty();
		for (let i = 0; i < this.days.length; i++) {
			const label = this.dayToDateFn(this.days[i]);
			this.aSelect.createEl("option", { value: String(i), text: label });
			this.bSelect.createEl("option", { value: String(i), text: label });
		}
		this.syncDiffSelectValues();
	}

	private applyEnabledState(): void {
		const hasData = this.days.length > 0;
		const on = this.enabled && hasData;

		this.replayTabBtn.disabled = !on;
		this.diffTabBtn.disabled = !on;

		this.playBtn.disabled = !on || this.days.length <= 1;
		this.sliderInput.disabled = !on;
		this.speedSelect.disabled = !on;
		this.recentSelect.disabled = !on;

		this.aSelect.disabled = !on;
		this.bSelect.disabled = !on;
		this.swapBtn.disabled = !on;
		this.presetRecentBtn.disabled = !on;
		this.presetWeekBtn.disabled = !on;
		this.presetAllBtn.disabled = !on;
	}

	private emitChange(): void {
		this.opts.onChange(this.getState());
	}
}

function nearestDayIndex(
	days: number[],
	day: number | undefined,
	fallback: number
): number {
	if (day === undefined || days.length === 0) return fallback;
	const exact = days.indexOf(day);
	if (exact >= 0) return exact;
	let best = 0;
	let bestDist = Math.abs(days[0] - day);
	for (let i = 1; i < days.length; i++) {
		const dist = Math.abs(days[i] - day);
		if (dist < bestDist) {
			best = i;
			bestDist = dist;
		}
	}
	return best;
}
