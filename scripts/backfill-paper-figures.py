#!/usr/bin/env python3
"""Backfill representative figure embeds for old wiki-ingest-paper notes.

This is a maintenance helper for source notes created before figure embedding
became mandatory in wiki-ingest-paper. It only touches paper source notes that do
not already contain `_attachments/` image embeds.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "wiki" / "sources"
ATTACHMENTS = SOURCES / "_attachments"
RAW_PAPERS = ROOT / ".raw" / "papers"
TODAY = "2026-06-21"


@dataclass
class SourceNote:
    path: Path
    text: str
    created: str
    slugs: list[str]


@dataclass
class ImageCandidate:
    src: Path
    page: int
    file: str
    width: int
    height: int
    score: int
    caption: str


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def clean_slug(value: str) -> str:
    value = value.strip().strip("`").strip()
    value = value.split("`", 1)[0].strip()
    value = value.split("（", 1)[0].strip()
    value = value.split("(", 1)[0].strip()
    return value


def source_date(text: str) -> str:
    created = re.search(r"^created:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    if created:
        return created.group(1)
    date = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return date.group(1) if date else ""


def raw_slugs(text: str) -> list[str]:
    slugs: list[str] = []
    patterns = [
        r"\.raw/papers/([^\]\)\"\n]+?)\.pdf",
        r"\.raw/papers/([^\]\)\"\n]+?)\.txt",
        r"\.raw/papers/([^\]\)\"\n]+?)/images/images\.json",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            slug = clean_slug(match)
            if slug and slug not in slugs:
                slugs.append(slug)
    return slugs


def load_sources(cutoff: str) -> list[SourceNote]:
    notes: list[SourceNote] = []
    for path in sorted(SOURCES.glob("@*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "source_type: paper" not in text:
            continue
        if "_attachments/" in text:
            continue
        created = source_date(text)
        if created and created >= cutoff:
            continue
        notes.append(SourceNote(path=path, text=text, created=created, slugs=raw_slugs(text)))
    return notes


def choose_pdfs(slugs: list[str]) -> list[tuple[str, Path]]:
    pdfs: list[tuple[str, Path]] = []
    for slug in slugs:
        pdf = RAW_PAPERS / f"{slug}.pdf"
        if pdf.exists():
            pdfs.append((slug, pdf))
    return pdfs


def cleanup_manifest(images_dir: Path) -> list[dict]:
    manifest = images_dir / "images.json"
    if not manifest.exists():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    images = [
        img
        for img in data.get("images", [])
        if img.get("kind") == "embedded" and str(img.get("file", "")).startswith("image-")
    ]
    for page_render in images_dir.glob("page-*.png"):
        page_render.unlink()
    data["images"] = images
    data["images_count"] = len(images)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return images


def ensure_images(slug: str, pdf: Path, refresh: bool) -> list[dict]:
    images_dir = RAW_PAPERS / slug / "images"
    manifest = images_dir / "images.json"
    if refresh or not manifest.exists():
        run(["node", "scripts/extract-paper-images.mjs", str(pdf), str(images_dir)])
    return cleanup_manifest(images_dir)


def split_pages(slug: str) -> list[str]:
    txt = RAW_PAPERS / f"{slug}.txt"
    if not txt.exists():
        pdf = RAW_PAPERS / f"{slug}.pdf"
        if pdf.exists():
            run(["pdftotext", "-layout", str(pdf), str(txt)], check=False)
    if not txt.exists():
        return []
    return txt.read_text(encoding="utf-8", errors="replace").split("\f")


def captions_by_page(slug: str) -> dict[int, list[str]]:
    pages = split_pages(slug)
    out: dict[int, list[str]] = {}
    caption_re = re.compile(
        r"(?im)^\s*((?:Fig\.|Figure|Table)\s*[0-9][0-9A-Za-z.\-]*"
        r"[\s:.\-]+[^\n]{8,220})"
    )
    for idx, page in enumerate(pages, start=1):
        caps = []
        for match in caption_re.findall(page):
            cap = " ".join(match.split())
            if cap not in caps:
                caps.append(cap)
        if caps:
            out[idx] = caps
    return out


def image_score(img: dict, page_captions: dict[int, list[str]]) -> int:
    width = int(img.get("width") or 0)
    height = int(img.get("height") or 0)
    area = width * height
    score = area
    page = int(img.get("page") or 0)
    if page in page_captions:
        score += 2_000_000
    if width < 140 or height < 100 or area < 35_000:
        score -= 5_000_000
    aspect = width / height if height else 0
    if aspect > 12 or aspect < 0.08:
        score -= 2_000_000
    return score


def select_images(slug: str, images: list[dict], limit: int) -> list[ImageCandidate]:
    captions = captions_by_page(slug)
    candidates: list[ImageCandidate] = []
    images_dir = RAW_PAPERS / slug / "images"
    for img in images:
        file = str(img.get("file") or "")
        page = int(img.get("page") or 0)
        width = int(img.get("width") or 0)
        height = int(img.get("height") or 0)
        score = image_score(img, captions)
        if score <= 0:
            continue
        cap = captions.get(page, [""])[0]
        candidates.append(
            ImageCandidate(
                src=images_dir / file,
                page=page,
                file=file,
                width=width,
                height=height,
                score=score,
                caption=cap,
            )
        )
    candidates.sort(key=lambda c: (-c.score, c.page, c.file))

    selected: list[ImageCandidate] = []
    per_page: dict[int, int] = {}
    for candidate in candidates:
        if per_page.get(candidate.page, 0) >= 2:
            continue
        selected.append(candidate)
        per_page[candidate.page] = per_page.get(candidate.page, 0) + 1
        if len(selected) >= limit:
            break
    selected.sort(key=lambda c: (c.page, c.file))
    return selected


def crop_candidates(slug: str, pdf: Path, tmp_dir: Path, limit: int) -> list[ImageCandidate]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return []

    candidates: list[ImageCandidate] = []
    caption_re = re.compile(
        r"(?im)((?:Fig\.|Figure|Table)\s*[0-9][0-9A-Za-z.\-]*[\s:.\-]+[^\n]{8,220})"
    )
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
                match = caption_re.search(str(text))
                if not match:
                    continue
                caption = " ".join(match.group(1).split())
                left = max(rect.x0 + 40, 0)
                right = min(rect.x1 - 40, rect.x1)
                if y0 < rect.height * 0.25:
                    top = max(rect.y0 + 35, y0 - 18)
                    bottom = min(rect.y1 - 35, y1 + 270)
                else:
                    top = max(rect.y0 + 35, y0 - 320)
                    bottom = min(rect.y1 - 35, y1 + 8)
                if bottom - top < 80 or right - left < 120:
                    continue
                clip = fitz.Rect(left, top, right, bottom)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=clip, alpha=False)
                if pix.width < 300 or pix.height < 180:
                    continue
                file = f"crop-page{page_no:03d}-{len(candidates) + 1:02d}.png"
                out = tmp_dir / file
                pix.save(str(out))
                candidates.append(
                    ImageCandidate(
                        src=out,
                        page=page_no,
                        file=file,
                        width=pix.width,
                        height=pix.height,
                        score=pix.width * pix.height,
                        caption=caption,
                    )
                )
                if len(candidates) >= limit * 2:
                    break
            if len(candidates) >= limit * 2:
                break
    finally:
        doc.close()

    candidates.sort(key=lambda c: (c.page, -c.score))
    selected: list[ImageCandidate] = []
    used_pages: set[int] = set()
    for candidate in candidates:
        if candidate.page in used_pages and len(selected) < max(2, limit // 2):
            continue
        selected.append(candidate)
        used_pages.add(candidate.page)
        if len(selected) >= limit:
            break
    return selected


def ensure_manifest_source(text: str, slug: str) -> str:
    manifest_link = f'  - "[[.raw/papers/{slug}/images/images.json]]"'
    if manifest_link in text or f".raw/papers/{slug}/images/images.json" in text:
        return text

    lines = text.splitlines()
    insert_at = None
    in_sources = False
    for idx, line in enumerate(lines):
        if line.strip() == "sources:":
            in_sources = True
            insert_at = idx + 1
            continue
        if in_sources:
            if line.startswith("  - "):
                insert_at = idx + 1
                continue
            break
    if insert_at is None:
        return text
    lines.insert(insert_at, manifest_link)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def update_frontmatter_date(text: str) -> str:
    return re.sub(r"^updated:\s*\d{4}-\d{2}-\d{2}", f"updated: {TODAY}", text, count=1, flags=re.M)


def render_section(slug: str, selected: list[ImageCandidate]) -> str:
    lines = [
        "## 図表",
        "PDF から後追い抽出した代表的な埋め込み画像である。キャプションは抽出テキストから対応できたものだけを添える。",
        "",
    ]
    for idx, image in enumerate(selected, start=1):
        name = f"fig-page{image.page:03d}-{idx:02d}.png"
        lines.append(f"**図候補 {idx}（p. {image.page}）**")
        lines.append(f"![[_attachments/{slug}/{name}]]")
        if image.caption:
            lines.append(f"（原キャプション: {image.caption}）")
        else:
            lines.append("（キャプション対応は自動抽出では特定できなかった。）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def insert_section(text: str, section: str) -> str:
    if "\n## 図表\n" in text:
        return text
    markers = ["\n## 関連", "\n## 出典", "\n## References"]
    positions = [text.find(marker) for marker in markers if text.find(marker) != -1]
    if positions:
        pos = min(positions)
        return text[:pos].rstrip() + "\n\n" + section + "\n" + text[pos:].lstrip()
    return text.rstrip() + "\n\n" + section


def lock_path(path: Path) -> str:
    return str(path.relative_to(ROOT))


def acquire(path: Path) -> bool:
    result = run(["bash", "scripts/wiki-lock.sh", "acquire", lock_path(path)], check=False)
    return result.returncode == 0


def release(path: Path) -> None:
    run(["bash", "scripts/wiki-lock.sh", "release", lock_path(path)], check=False)


def process(note: SourceNote, args: argparse.Namespace) -> str:
    pdfs = choose_pdfs(note.slugs)
    if not pdfs:
        return f"skip:no-pdf\t{note.path}"
    last_slug = pdfs[-1][0]
    for slug, pdf in pdfs:
        try:
            images = ensure_images(slug, pdf, args.refresh_images)
        except subprocess.CalledProcessError as error:
            print(f"warn:extract-failed\t{note.path}\t{slug}\t{error.stderr.strip()}")
            images = []

        with tempfile.TemporaryDirectory(prefix=f"paper-figures-{slug}-") as tmp:
            tmp_dir = Path(tmp)
            selected = select_images(slug, images, args.images_per_paper) if images else []
            source = "embedded"
            if not selected and args.crop_figures:
                selected = crop_candidates(slug, pdf, tmp_dir, args.images_per_paper)
                source = "crop"
            if not selected:
                continue

            if args.dry_run:
                return f"would-update:{len(selected)}:{source}\t{note.path}\t{slug}"

            if not acquire(note.path):
                return f"skip:locked\t{note.path}\t{slug}"
            try:
                attach_dir = ATTACHMENTS / slug
                attach_dir.mkdir(parents=True, exist_ok=True)
                for idx, image in enumerate(selected, start=1):
                    dest = attach_dir / f"fig-page{image.page:03d}-{idx:02d}.png"
                    shutil.copy2(image.src, dest)

                text = note.path.read_text(encoding="utf-8", errors="replace")
                text = ensure_manifest_source(text, slug)
                text = update_frontmatter_date(text)
                text = insert_section(text, render_section(slug, selected))
                note.path.write_text(text, encoding="utf-8")
                return f"updated:{len(selected)}:{source}\t{note.path}\t{slug}"
            finally:
                release(note.path)
    return f"skip:no-selected-images\t{note.path}\t{last_slug}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", default="2026-06-19")
    parser.add_argument("--images-per-paper", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh-images", action="store_true")
    parser.add_argument("--no-crop-figures", dest="crop_figures", action="store_false")
    parser.set_defaults(crop_figures=True)
    args = parser.parse_args()

    notes = load_sources(args.cutoff)
    if args.limit:
        notes = notes[: args.limit]
    counts: dict[str, int] = {}
    for note in notes:
        status = process(note, args)
        key = status.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
        print(status)
    print("summary", json.dumps(counts, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
