import { App } from "obsidian";
import { GraphIndex } from "./graph-index";
import { LogEvent, LogParseResult } from "./types";

/**
 * wiki/log.md is an operational log appended to by both humans and LLMs, so
 * heading notation inevitably varies (full-width/half-width brackets, presence
 * or absence of annotations, `[[A|display name]]` aliases, path-style links
 * like `wiki/index`, `@`-prefixed basenames, bullet lists mixed with Japanese
 * punctuation, etc). This module is a best-effort annotation-layer parser
 * dedicated to that real-world input, used only for the event markers on the
 * slider and for displaying the day's operation title. Even if parsing fails
 * partially or entirely here, no exception is thrown, and the timeline
 * feature (Replay / Diff) keeps working fully on frontmatter (created/updated)
 * alone.
 */

const LOG_PATH = "wiki/log.md";

/** `## [YYYY-MM-DD] <op> | <title>` heading. Tolerant of surrounding whitespace and mixed full-width brackets */
const HEADING_RE = /^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*([^|]+?)\s*\|\s*(.+)$/;

/**
 * Best-effort parse of wiki/log.md. Never throws on failure
 * (if everything fails, returns events: [] and the timeline feature degrades
 * gracefully to frontmatter only).
 */
export async function parseWikiLog(
	app: App,
	index: GraphIndex
): Promise<LogParseResult> {
	const events: LogEvent[] = [];
	let failedCount = 0;

	try {
		const file = app.vault.getFileByPath(LOG_PATH);
		if (!file) return { events, failedCount };

		const content = await app.vault.cachedRead(file);
		const lines = content.split("\n");

		let i = 0;
		while (i < lines.length) {
			const headingMatch = HEADING_RE.exec(lines[i]);
			if (!headingMatch) {
				i++;
				continue;
			}

			// Everything up to the next "## " heading is this entry's range
			let j = i + 1;
			const bodyLines: string[] = [];
			while (j < lines.length && !lines[j].trimStart().startsWith("## ")) {
				bodyLines.push(lines[j]);
				j++;
			}

			try {
				const event = parseEntry(headingMatch, bodyLines, index);
				if (event) events.push(event);
				else failedCount++;
			} catch {
				// Isolate individual entry parse failures and keep parsing other entries
				failedCount++;
			}

			i = j;
		}
	} catch (err) {
		console.warn("wiki-lens: failed to parse wiki/log.md (continuing without annotations)", err);
	}

	return { events, failedCount };
}

/** Build a LogEvent from one heading + its body lines. Returns null if date is invalid */
function parseEntry(
	headingMatch: RegExpExecArray,
	bodyLines: string[],
	index: GraphIndex
): LogEvent | null {
	const [, date, opRaw, titleRaw] = headingMatch;
	if (!isValidDate(date)) return null;

	const op = opRaw.trim();
	const title = titleRaw.trim();
	if (!op || !title) return null;

	const createdPaths = new Set<string>();
	const updatedPaths = new Set<string>();

	for (const line of bodyLines) {
		const trimmed = line.trim();
		if (trimmed.startsWith("- Pages created:")) {
			for (const p of extractResolvedPaths(trimmed, index)) createdPaths.add(p);
		} else if (trimmed.startsWith("- Pages updated:")) {
			for (const p of extractResolvedPaths(trimmed, index)) updatedPaths.add(p);
		}
	}

	return {
		date,
		op,
		title,
		createdPaths: [...createdPaths],
		updatedPaths: [...updatedPaths],
	};
}

/** Extract all `[[...]]` in a single line and return only those that resolve, as node paths */
function extractResolvedPaths(line: string, index: GraphIndex): string[] {
	const paths: string[] = [];
	for (const m of line.matchAll(/\[\[([^\]]+)\]\]/g)) {
		const resolved = resolveTarget(m[1], index);
		if (resolved) paths.push(resolved);
	}
	return paths;
}

/**
 * Resolve a wikilink target to a node path.
 * (1) For `[[A|display name]]`, take the left side of `|` and try a path match
 *     (also trying with a `wiki/` prefix added).
 * (2) If that doesn't match, look up index.basenameToPaths as a basename
 *     (multiple hits take the first).
 * If both fail, silently drop it (index/hot/_index and the like are not node
 * targets so they naturally fail to resolve -- that's the correct behavior).
 */
function resolveTarget(raw: string, index: GraphIndex): string | undefined {
	const pipeIdx = raw.indexOf("|");
	const target = (pipeIdx === -1 ? raw : raw.slice(0, pipeIdx)).trim();
	if (!target) return undefined;

	const withMd = target.endsWith(".md") ? target : `${target}.md`;
	if (index.nodes.has(withMd)) return withMd;

	if (!withMd.startsWith("wiki/")) {
		const withWikiPrefix = `wiki/${withMd}`;
		if (index.nodes.has(withWikiPrefix)) return withWikiPrefix;
	}

	const basename = target.split("/").pop()?.trim() ?? "";
	const candidates = basename ? index.basenameToPaths.get(basename) : undefined;
	if (candidates && candidates.length > 0) return candidates[0];

	return undefined;
}

/** Check whether "YYYY-MM-DD" is a real date (including validity such as leap days) */
function isValidDate(date: string): boolean {
	return !Number.isNaN(Date.parse(`${date}T00:00:00Z`));
}
