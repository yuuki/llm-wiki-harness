import { WikiPageType, WikiStatus, DeadCause } from "./types";

const TYPE_BY_FOLDER: Record<string, WikiPageType> = {
	sources: "source",
	entities: "entity",
	concepts: "concept",
	questions: "question",
};

const VALID_TYPES = new Set<string>([
	"source",
	"entity",
	"concept",
	"question",
]);

const VALID_STATUS = new Set<string>([
	"seed",
	"developing",
	"mature",
	"evergreen",
]);

/** Folder name directly under the wiki root that is a node target -> page type. null if out of scope */
export function typeFromPath(
	path: string,
	wikiRoot: string
): WikiPageType | null {
	if (!path.startsWith(wikiRoot)) return null;
	const rest = path.slice(wikiRoot.length);
	const slash = rest.indexOf("/");
	if (slash < 0) return null;
	const folder = rest.slice(0, slash);
	return TYPE_BY_FOLDER[folder] ?? null;
}

/** Prefer frontmatter's type; fill in invalid/missing values from the folder */
export function normalizeType(
	fmType: unknown,
	fallback: WikiPageType
): WikiPageType {
	if (typeof fmType === "string" && VALID_TYPES.has(fmType)) {
		return fmType as WikiPageType;
	}
	return fallback;
}

export function normalizeStatus(fmStatus: unknown): WikiStatus {
	if (typeof fmStatus === "string" && VALID_STATUS.has(fmStatus)) {
		return fmStatus as WikiStatus;
	}
	return "unknown";
}

export function subTypeOf(
	type: WikiPageType,
	fm: Record<string, unknown> | undefined
): string {
	if (!fm) return "";
	const key =
		type === "source"
			? "source_type"
			: type === "entity"
				? "entity_type"
				: type === "concept"
					? "complexity"
					: "answer_quality";
	const v = fm[key];
	return typeof v === "string" ? v : "";
}

export function tagsOf(fm: Record<string, unknown> | undefined): string[] {
	if (!fm) return [];
	const raw = fm["tags"];
	if (Array.isArray(raw)) return raw.filter((t): t is string => typeof t === "string");
	if (typeof raw === "string") return [raw];
	return [];
}

const FILE_EXT_RE = /\.[a-zA-Z0-9]{1,6}$/;

/**
 * Classify the cause of an unresolved link target.
 * hasAtVariant: whether "@" + target (basename) matches an existing page
 */
export function classifyDeadTarget(
	target: string,
	hasAtVariant: boolean
): DeadCause {
	if (target.startsWith(".raw/") || target.includes("_attachments/")) {
		return "file-ref";
	}
	if (FILE_EXT_RE.test(target) && !target.endsWith(".md")) {
		return "file-ref";
	}
	if (target.endsWith(".md")) return "file-ref";
	if (hasAtVariant) return "missing-at-prefix";
	if (target.startsWith("@")) return "missing-source";
	return "missing-page";
}
