/** Shared type / status / community colors. Kept in core so cluster aggregation does not import a view. */

export const COMMUNITY_PALETTE = [
	"#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
	"#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
	"#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000",
	"#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
];

export const TYPE_COLORS: Record<string, string> = {
	concept: "#f58231",
	source: "#4363d8",
	entity: "#3cb44b",
	question: "#f032e6",
	ghost: "#c05555",
};

export const STATUS_COLORS: Record<string, string> = {
	seed: "#888888",
	developing: "#42d4f4",
	mature: "#3cb44b",
	evergreen: "#ffe119",
	unknown: "#555555",
};
