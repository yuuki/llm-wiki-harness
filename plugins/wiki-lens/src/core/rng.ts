/**
 * Deterministic mulberry32. Used as the Louvain rng so community ids stay
 * stable across index rebuilds of the same topology.
 */
export function mulberry32(seed: number): () => number {
	let a = seed >>> 0;
	return () => {
		a = (a + 0x6d2b79f5) | 0;
		let t = Math.imul(a ^ (a >>> 15), a | 1);
		t = (t + Math.imul(t ^ (t >>> 7), t | 61)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

/** Fixed seed for community assignment ("wiki" as four ASCII bytes). */
export const LOUVAIN_SEED = 0x77696b69;
