#!/usr/bin/env python3
"""Repair backfilled paper figure embeds.

The first backfill pass added a trailing "## 図表" section with generic
"図候補" entries. This script turns those generated embeds into normal
paper-note figure blocks:

- find notes that reference generated ``fig-pageNNN-NN.png`` images;
- re-crop figures from the PDF around real Figure/Table captions with PyMuPDF;
- remove the generic trailing section and generated figure blocks;
- insert ``Figure/Table`` blocks into semantically related sections.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "wiki" / "sources"
ATTACHMENTS = SOURCES / "_attachments"
RAW_PAPERS = ROOT / ".raw" / "papers"
TODAY = "2026-06-22"

GENERATED_EMBED_RE = re.compile(
    r"!\[\[_attachments/(?P<slug>[^/\]]+)/fig-page\d{3}-\d{2}\.png\]\]"
)
FIGURE_SECTION_RE = re.compile(r"\n## 図表\n.*?(?=\n## |\Z)", re.S)
OLD_BLOCK_RE = re.compile(
    r"\*\*図候補\s+(?P<idx>\d+)（p\.\s*(?P<page>\d+)）\*\*\n"
    r"(?P<embed>!\[\[[^\n]+\]\])"
    r"(?:\n（(?:原キャプション:\s*(?P<caption>[^）]+)|キャプション対応は自動抽出では特定できなかった。)）)?",
    re.S,
)
NORMALIZED_GENERATED_BLOCK_RE = re.compile(
    r"\n{0,2}\*\*(?:Figure|Table)\s+[0-9][^*\n]*\*\*\n"
    r"!\[\[_attachments/[^/\]]+/fig-page\d{3}-\d{2}\.png\]\]\n"
    r"[^\n]*(?:\n|$)",
    re.S,
)
CAPTION_LABEL_RE = re.compile(
    r"\b(?P<kind>Fig\.|Figure|Table)\s*(?P<num>[0-9][0-9A-Za-z.\-]*)",
    re.I,
)
CAPTION_RE = re.compile(
    r"(?is)\b(?:Fig\.|Figure|Table)\s*[0-9][0-9A-Za-z.\-]*\s*[:.\-]\s*"
    r".{8,420}?(?=(?:\n\s*\n)|(?:\n\s*(?:Fig\.|Figure|Table)\s*[0-9])|\Z)"
)

SECTION_ORDER = [
    "問題設定",
    "提案手法",
    "手法",
    "アーキテクチャ",
    "実験設定",
    "評価",
    "実験結果",
    "考察",
    "概要",
]

TITLE_RULES = [
    (("architecture", "architectural"), "アーキテクチャ", "アーキテクチャを示す。"),
    (("overall framework", "framework"), "フレームワーク", "全体フレームワークを示す。"),
    (("overview",), "全体像", "全体像を示す。"),
    (("workflow", "pipeline", "process", "procedure"), "ワークフロー", "処理フローを示す。"),
    (("algorithm",), "アルゴリズム", "アルゴリズムの流れを示す。"),
    (("model",), "モデル構成", "モデル構成を示す。"),
    (("module", "component"), "コンポーネント構成", "コンポーネント構成を示す。"),
    (("comparison", "compare", "versus", " vs "), "比較", "比較関係を示す。"),
    (("dataset", "benchmark"), "データセット", "評価データセットを示す。"),
    (("setup", "configuration", "workload"), "実験設定", "実験設定を示す。"),
    (("result", "evaluation", "performance"), "評価結果", "評価結果を示す。"),
    (("accuracy", "precision", "recall", "f1", "auc"), "精度評価", "精度評価の結果を示す。"),
    (("latency", "throughput", "overhead"), "性能評価", "性能評価の結果を示す。"),
    (("ablation",), "アブレーション結果", "アブレーション結果を示す。"),
    (("case study", "case", "example"), "事例", "事例を示す。"),
    (("timeline", "time line"), "タイムライン", "時系列の流れを示す。"),
    (("taxonomy", "classification"), "分類", "分類を示す。"),
    (("distribution",), "分布", "分布を示す。"),
    (("contributor", "outage", "failure"), "障害要因", "障害要因の内訳を示す。"),
]

TARGET_RULES = [
    (
        (
            "architecture",
            "framework",
            "overview",
            "workflow",
            "pipeline",
            "algorithm",
            "module",
            "component",
            "model",
            "procedure",
            "process",
        ),
        "提案手法",
    ),
    (("dataset", "benchmark", "setup", "configuration", "workload"), "実験設定"),
    (
        (
            "result",
            "evaluation",
            "performance",
            "accuracy",
            "precision",
            "recall",
            "latency",
            "throughput",
            "overhead",
            "comparison",
            "ablation",
            "scalability",
            "sensitivity",
            "case study",
            "successfully",
        ),
        "実験結果",
    ),
    (("motivation", "example", "incident", "failure", "anomaly", "problem"), "問題設定"),
    (("taxonomy", "classification", "survey", "landscape"), "概要"),
]


@dataclass
class OldCandidate:
    index: int
    page: int
    embed: str
    caption: str


@dataclass
class CaptionHit:
    label: str
    kind: str
    number: str
    page: int
    caption: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float


@dataclass
class FigureBlock:
    label: str
    kind: str
    number: str
    page: int
    caption: str
    title: str
    summary: str
    target_section: str
    filename: str
    image_path: Path


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def generated_notes() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(SOURCES.glob("@*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if GENERATED_EMBED_RE.search(text) or ("\n## 図表\n" in text and "図候補" in text):
            paths.append(path)
    return paths


def clean_slug(value: str) -> str:
    value = value.strip().strip("`").strip()
    value = value.split("`", 1)[0].strip()
    value = value.split("（", 1)[0].strip()
    value = value.split("(", 1)[0].strip()
    return value


def raw_slugs(text: str) -> list[str]:
    slugs: list[str] = []
    for pattern in (
        r"\.raw/papers/([^\]\)\"\n]+?)\.pdf",
        r"\.raw/papers/([^\]\)\"\n]+?)\.txt",
        r"\.raw/papers/([^\]\)\"\n]+?)/images/images\.json",
    ):
        for match in re.findall(pattern, text):
            slug = clean_slug(match)
            if slug and slug not in slugs:
                slugs.append(slug)
    return slugs


def embed_slugs(text: str) -> list[str]:
    slugs: list[str] = []
    for match in GENERATED_EMBED_RE.finditer(text):
        slug = match.group("slug")
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def choose_slug(text: str) -> str:
    slugs = embed_slugs(text) or raw_slugs(text)
    for slug in slugs:
        if (RAW_PAPERS / f"{slug}.pdf").exists():
            return slug
    return slugs[0] if slugs else ""


def clean_caption(caption: str) -> str:
    caption = caption.replace("\u00a0", " ")
    caption = " ".join(caption.replace("\n", " ").split())
    caption = re.sub(r"\s+", " ", caption)
    caption = re.sub(r"\bI\s+(?=[a-z])", "", caption)
    caption = re.sub(r"\s+([,.;:])", r"\1", caption)
    return caption.strip(" .")


def normalize_label(caption: str, fallback: int) -> tuple[str, str, str]:
    match = CAPTION_LABEL_RE.search(caption)
    if not match:
        return f"Figure {fallback}", "Figure", str(fallback)
    kind = "Table" if match.group("kind").lower().startswith("table") else "Figure"
    number = match.group("num").strip(".-")
    return f"{kind} {number}", kind, number


def caption_without_label(caption: str) -> str:
    body = re.sub(
        r"^(?:Fig\.|Figure|Table)\s*[0-9][0-9A-Za-z.\-]*\s*[:.\-]\s*",
        "",
        caption,
        flags=re.I,
    )
    body = re.sub(r"^(?:the|an|a)\s+", "", body, flags=re.I)
    return clean_caption(body)


def label_key(label: str) -> str:
    label = label.lower().replace("fig.", "figure")
    return re.sub(r"[^a-z0-9]+", "", label)


def caption_quality(caption: str, image_area: int = 0) -> int:
    score = image_area // 20_000
    if re.match(r"^(?:Fig\.|Figure|Table)\s*[0-9]", caption, re.I):
        score += 100
    if 25 <= len(caption) <= 260:
        score += 25
    if len(caption) > 360:
        score -= 20
    if re.search(r"\b(?:abstract|introduction|references|keywords)\b", caption, re.I):
        score -= 40
    score -= caption.count("|") * 5
    return score


def old_candidates(text: str) -> list[OldCandidate]:
    match = FIGURE_SECTION_RE.search(text)
    if not match:
        return []
    candidates: list[OldCandidate] = []
    for block in OLD_BLOCK_RE.finditer(match.group(0)):
        candidates.append(
            OldCandidate(
                index=int(block.group("idx")),
                page=int(block.group("page")),
                embed=block.group("embed"),
                caption=clean_caption(block.group("caption") or ""),
            )
        )
    return candidates


def desired_labels(candidates: list[OldCandidate]) -> list[str]:
    labels: list[str] = []
    for candidate in candidates:
        if not candidate.caption:
            continue
        label, _kind, _number = normalize_label(candidate.caption, candidate.index)
        key = label_key(label)
        if key not in {label_key(item) for item in labels}:
            labels.append(label)
    return labels


def extract_caption_hits(pdf: Path) -> list[CaptionHit]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ModuleNotFoundError as error:
        raise SystemExit(
            "PyMuPDF is required. Run with: "
            'uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python scripts/normalize-paper-figure-embeds.py'
        ) from error

    hits: list[CaptionHit] = []
    seen: set[tuple[str, int, str]] = set()
    doc = fitz.open(str(pdf))
    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_no = page_idx + 1
            rect = page.rect
            for block in page.get_text("blocks"):
                if len(block) < 7:
                    continue
                x0, y0, x1, y1, text, _block_no, block_type = block[:7]
                if block_type != 0:
                    continue
                block_text = str(text)
                for match in CAPTION_RE.finditer(block_text):
                    caption = clean_caption(match.group(0))
                    label, kind, number = normalize_label(caption, len(hits) + 1)
                    key = (label_key(label), page_no, re.sub(r"\W+", "", caption.lower())[:80])
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(
                        CaptionHit(
                            label=label,
                            kind=kind,
                            number=number,
                            page=page_no,
                            caption=caption,
                            x0=float(x0),
                            y0=float(y0),
                            x1=float(x1),
                            y1=float(y1),
                            page_width=float(rect.width),
                            page_height=float(rect.height),
                        )
                    )
    finally:
        doc.close()
    hits.sort(key=lambda hit: (hit.page, label_sort_key(hit.label), hit.y0))
    return hits


def label_sort_key(label: str) -> tuple[int, str]:
    match = re.search(r"([0-9]+)", label)
    return (int(match.group(1)) if match else 9999, label)


def selected_hits(hits: list[CaptionHit], old: list[OldCandidate], max_figures: int) -> list[CaptionHit]:
    labels = desired_labels(old)
    old_count = len(old)
    selected: list[CaptionHit] = []
    seen_labels: set[str] = set()

    if labels:
        wanted = {label_key(label) for label in labels}
        for hit in hits:
            key = label_key(hit.label)
            if key in wanted and key not in seen_labels:
                selected.append(hit)
                seen_labels.add(key)

    for hit in hits:
        key = label_key(hit.label)
        if key in seen_labels:
            continue
        if labels and len(selected) >= max_figures:
            break
        if not labels and old_count and len(selected) >= min(max_figures, old_count):
            break
        selected.append(hit)
        seen_labels.add(key)
        if len(selected) >= max_figures:
            break

    return selected[:max_figures]


def english_subject(body: str) -> str:
    body = re.sub(r"\s+due to .*$", "", body, flags=re.I)
    body = re.sub(r"\s+across .*$", "", body, flags=re.I)
    body = re.sub(r"\s+with .*$", "", body, flags=re.I)
    body = re.sub(r"\s+for later analysis.*$", "", body, flags=re.I)
    body = re.sub(r"\s+", " ", body).strip(" .")
    return body[:120].strip(" .")


def phrase_from_caption(caption: str, kind: str) -> tuple[str, str]:
    lower = caption.lower()
    body = caption_without_label(caption)
    subject = english_subject(body)

    if "overall framework of " in lower or "framework of " in lower:
        name = re.sub(r"^.*framework of\s+", "", body, flags=re.I).strip(" .")
        name = english_subject(name)
        return "フレームワーク", f"{name} の全体フレームワークを示す。"
    if "architecture of " in lower:
        name = re.sub(r"^.*architecture of\s+", "", body, flags=re.I).strip(" .")
        name = english_subject(name)
        return "アーキテクチャ", f"{name} のアーキテクチャを示す。"
    if "workflow of " in lower:
        name = re.sub(r"^.*workflow of\s+", "", body, flags=re.I).strip(" .")
        name = english_subject(name)
        return "ワークフロー", f"{name} のワークフローを示す。"
    if "comparison between " in lower:
        name = re.sub(r"^.*comparison between\s+", "", body, flags=re.I).strip(" .")
        name = english_subject(name)
        return "比較", f"{name} の比較を示す。"
    if "contributors to " in lower and "outage" in lower:
        return "停止要因", "停止要因の内訳を示す。"

    for keys, title, summary in TITLE_RULES:
        if any(key in lower for key in keys):
            if subject and len(subject) > 4 and title not in {"障害要因", "評価結果", "性能評価", "精度評価"}:
                return title, f"{subject} に関する{summary}"
            return title, summary

    if kind == "Table":
        return "表", "論文中の主要な表を示す。"
    if subject:
        return "図", f"{subject} を示す。"
    return "図", "論文中の主要な図を示す。"


def title_and_summary(label: str, kind: str, caption: str) -> tuple[str, str]:
    phrase, summary = phrase_from_caption(caption, kind)
    title = f"{label}: {phrase}"
    return title, f"{label}. {summary}"


def target_from_caption(caption: str, kind: str, existing_sections: set[str]) -> str:
    lower = caption.lower()
    if kind == "Table":
        if any(key in lower for key in ("dataset", "setting", "configuration", "workload")):
            return first_existing(("実験設定", "評価", "実験結果"), existing_sections)
        if any(key in lower for key in ("result", "performance", "accuracy", "comparison")):
            return first_existing(("実験結果", "評価", "実験設定"), existing_sections)
        return first_existing(("実験結果", "評価", "提案手法", "概要"), existing_sections)
    for keys, section in TARGET_RULES:
        if any(key in lower for key in keys):
            if section == "実験結果":
                return first_existing(("実験結果", "評価", "考察"), existing_sections)
            return first_existing((section, "手法" if section == "提案手法" else section), existing_sections)
    return first_existing(("提案手法", "手法", "概要"), existing_sections)


def first_existing(candidates: tuple[str, ...], existing_sections: set[str]) -> str:
    for candidate in candidates:
        if candidate in existing_sections:
            return candidate
        for section in existing_sections:
            if section.startswith(f"{candidate}:") or section.startswith(f"{candidate}（") or section.startswith(f"{candidate}("):
                return section
    for candidate in SECTION_ORDER:
        if candidate in existing_sections:
            return candidate
        for section in existing_sections:
            if section.startswith(f"{candidate}:") or section.startswith(f"{candidate}（") or section.startswith(f"{candidate}("):
                return section
    return "概要"


def safe_filename(label: str, title: str, used: set[str]) -> str:
    prefix = "table" if label.lower().startswith("table") else "fig"
    number = re.sub(r"[^0-9A-Za-z]+", "-", label.split(" ", 1)[1]).strip("-").lower()
    keyword = title.split(":", 1)[1].strip()
    keyword_slug = {
        "アーキテクチャ": "architecture",
        "フレームワーク": "framework",
        "全体像": "overview",
        "ワークフロー": "workflow",
        "処理フロー": "workflow",
        "アルゴリズム": "algorithm",
        "モデル構成": "model",
        "コンポーネント構成": "components",
        "比較": "comparison",
        "データセット": "dataset",
        "実験設定": "setup",
        "評価結果": "results",
        "精度評価": "accuracy",
        "性能評価": "performance",
        "アブレーション結果": "ablation",
        "事例": "case-study",
        "タイムライン": "timeline",
        "分類": "taxonomy",
        "分布": "distribution",
        "障害要因": "failure-factors",
        "停止要因": "outage-factors",
        "表": "table",
        "図": "figure",
    }.get(keyword, "figure")
    name = f"{prefix}{number}-{keyword_slug}.png"
    if name not in used:
        used.add(name)
        return name
    digest = hashlib.sha1(f"{label}-{title}".encode("utf-8")).hexdigest()[:6]
    name = f"{prefix}{number}-{keyword_slug}-{digest}.png"
    used.add(name)
    return name


def crop_rect(hit: CaptionHit):
    import fitz  # type: ignore[import-not-found]

    margin_x = 36
    left = max(0, hit.x0 - margin_x)
    right = min(hit.page_width, hit.x1 + margin_x)
    if right - left < hit.page_width * 0.55:
        left = max(0, hit.page_width * 0.08)
        right = min(hit.page_width, hit.page_width * 0.92)

    if hit.kind == "Table":
        if hit.y0 > hit.page_height * 0.35:
            top = max(0, hit.y0 - 360)
            bottom = min(hit.page_height, hit.y1 + 10)
        else:
            top = max(0, hit.y0 - 10)
            bottom = min(hit.page_height, hit.y1 + 360)
    else:
        top = max(0, hit.y0 - 330)
        bottom = min(hit.page_height, hit.y1 + 30)

    if bottom - top < 120:
        top = max(0, hit.y0 - 420)
        bottom = min(hit.page_height, hit.y1 + 60)
    return fitz.Rect(left, top, right, bottom)


def render_crop(pdf: Path, hit: CaptionHit, dest: Path) -> tuple[int, int]:
    import fitz  # type: ignore[import-not-found]

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf))
    try:
        page = doc[hit.page - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=crop_rect(hit), alpha=False)
        pix.save(str(dest))
        return pix.width, pix.height
    finally:
        doc.close()


def build_blocks(path: Path, text: str, slug: str, max_figures: int, dry_run: bool) -> list[FigureBlock]:
    pdf = RAW_PAPERS / f"{slug}.pdf"
    if not pdf.exists():
        return []
    old = old_candidates(text)
    hits = selected_hits(extract_caption_hits(pdf), old, max_figures)
    if not hits:
        return fallback_blocks_from_old(text, slug, max_figures)

    existing_sections = set(re.findall(r"^##\s+(.+)$", text, re.M))
    blocks: list[FigureBlock] = []
    used_files: set[str] = set()
    for hit in hits:
        title, summary = title_and_summary(hit.label, hit.kind, hit.caption)
        filename = safe_filename(hit.label, title, used_files)
        image_path = ATTACHMENTS / slug / filename
        if not dry_run:
            width, height = render_crop(pdf, hit, image_path)
            if width < 250 or height < 120:
                continue
        blocks.append(
            FigureBlock(
                label=hit.label,
                kind=hit.kind,
                number=hit.number,
                page=hit.page,
                caption=hit.caption,
                title=title,
                summary=summary,
                target_section=target_from_caption(hit.caption, hit.kind, existing_sections),
                filename=filename,
                image_path=image_path,
            )
        )
    return blocks


def fallback_blocks_from_old(text: str, slug: str, max_figures: int) -> list[FigureBlock]:
    existing_sections = set(re.findall(r"^##\s+(.+)$", text, re.M))
    blocks: list[FigureBlock] = []
    used_files: set[str] = set()
    seen_labels: set[str] = set()
    for candidate in old_candidates(text):
        label, kind, number = normalize_label(candidate.caption, candidate.index)
        key = label_key(label)
        if key in seen_labels:
            continue
        seen_labels.add(key)
        title, summary = title_and_summary(label, kind, candidate.caption)
        filename = re.search(r"/([^/\]]+)\]\]$", candidate.embed)
        name = filename.group(1) if filename else safe_filename(label, title, used_files)
        blocks.append(
            FigureBlock(
                label=label,
                kind=kind,
                number=number,
                page=candidate.page,
                caption=candidate.caption,
                title=title,
                summary=summary,
                target_section=target_from_caption(candidate.caption, kind, existing_sections),
                filename=name,
                image_path=ATTACHMENTS / slug / name,
            )
        )
        if len(blocks) >= max_figures:
            break
    return blocks


def render_block(block: FigureBlock, slug: str) -> str:
    return "\n".join(
        [
            f"**{block.title}**",
            f"![[_attachments/{slug}/{block.filename}]]",
            f"({block.summary})",
        ]
    )


def strip_generated_blocks(text: str) -> str:
    text = FIGURE_SECTION_RE.sub("", text)
    text = NORMALIZED_GENERATED_BLOCK_RE.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def section_bounds(text: str) -> dict[str, tuple[int, int]]:
    matches = list(re.finditer(r"^##\s+(.+)$", text, re.M))
    bounds: dict[str, tuple[int, int]] = {}
    for idx, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        bounds[name] = (start, end)
    return bounds


def insert_blocks(text: str, blocks: list[FigureBlock], slug: str) -> str:
    text = strip_generated_blocks(text)
    grouped: dict[str, list[FigureBlock]] = {}
    for block in blocks:
        grouped.setdefault(block.target_section, []).append(block)

    for section, section_blocks in sorted(
        grouped.items(),
        key=lambda item: section_bounds(text).get(item[0], (len(text), len(text)))[1],
        reverse=True,
    ):
        bounds = section_bounds(text)
        insertion = "\n\n".join(render_block(block, slug) for block in section_blocks)
        if section not in bounds:
            text = text.rstrip() + f"\n\n## {section}\n\n{insertion}\n"
            continue
        start, end = bounds[section]
        section_text = text[start:end].rstrip()
        replacement = f"{section_text}\n\n{insertion}\n\n"
        text = text[:start] + replacement + text[end:].lstrip("\n")
    return update_frontmatter_date(text.rstrip() + "\n")


def update_frontmatter_date(text: str) -> str:
    return re.sub(r"^updated:\s*\d{4}-\d{2}-\d{2}", f"updated: {TODAY}", text, count=1, flags=re.M)


def lock_path(path: Path) -> str:
    return str(path.relative_to(ROOT))


def acquire(path: Path) -> bool:
    result = run(["bash", "scripts/wiki-lock.sh", "acquire", lock_path(path)], check=False)
    return result.returncode == 0


def release(path: Path) -> None:
    run(["bash", "scripts/wiki-lock.sh", "release", lock_path(path)], check=False)


def normalize_file(path: Path, dry_run: bool, max_figures: int) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    slug = choose_slug(text)
    if not slug:
        return False, f"skip:no-slug\t{path}"

    blocks = build_blocks(path, text, slug, max_figures, dry_run)
    if not blocks:
        return False, f"skip:no-blocks\t{path}\t{slug}"

    new_text = insert_blocks(text, blocks, slug)
    if new_text == text:
        return False, f"skip:unchanged\t{path}\t{slug}"

    if not dry_run:
        if not acquire(path):
            return False, f"skip:locked\t{path}\t{slug}"
        try:
            path.write_text(new_text, encoding="utf-8")
        finally:
            release(path)

    moved = ", ".join(f"{block.label}->{block.target_section}" for block in blocks[:8])
    if len(blocks) > 8:
        moved += ", ..."
    return True, f"normalized:{len(blocks)}\t{path}\t{slug}\t{moved}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-figures", type=int, default=5)
    args = parser.parse_args()

    paths = generated_notes()
    if args.limit:
        paths = paths[: args.limit]
    changed = 0
    for path in paths:
        did_change, message = normalize_file(path, args.dry_run, args.max_figures)
        if did_change:
            changed += 1
        print(message)
    print(f"summary changed={changed} total={len(paths)} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
