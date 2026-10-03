#!/usr/bin/env python3
"""論文 PDF から Figure/Table をキャプション基準でクロップする。

`wiki-ingest-paper` のベクター図手順。ブロック先頭の `startswith("Fig.")` や
「直前テキストの y1 からページ下端」は、二段組み ACM で本文を巻き込む。
キャプションは `search_for("Figure N:")` 系だけを使い、全幅にするかどうかは
キャプション行が段をまたぐかで決める。

使い方:
  uv run --with pymupdf --cache-dir "$TMPDIR/uv-cache" python3 scripts/crop-paper-figures.py \\
    --pdf .raw/papers/<slug>.pdf \\
    --out wiki/sources/_attachments/<slug> \\
    --id fig:1:fig01-architecture.png \\
    --id tab:2:table2-results.png

終了コード: 0 成功(一部スキップ可) / 2 使い方 / 1 クロップ失敗が 1 件以上。
各 `--id` について JSON 1 行を stdout に出す。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCALE = 3.0
SKIP_NAME_RE = re.compile(r"^(slide-|fig-page|page-|contact|poster)", re.I)
_CAP_Y_RE = re.compile(
    r"(?:Figure|Fig\.|Fig|Table|Tab\.)\s+(\d+)\s*[:.]",
    re.I,
)
ID_RE = re.compile(
    r"^(fig(?:ure)?|tab(?:le)?|tbl):(\d+):(.+\.png)$",
    re.I,
)


def caption_queries(num: int, table: bool) -> list[str]:
    """キャプション検索語。本文言及(`Figure 2 presents`)は含めない。"""
    n = str(num)
    if table:
        return [
            f"Table {n}:",
            f"Table {n}.",
            f"TABLE {n}:",
            f"TABLE {n}.",
            f"Tab. {n}:",
            f"Tab. {n}.",
        ]
    return [
        f"Figure {n}:",
        f"Figure {n}.",
        f"Fig. {n}:",
        f"Fig. {n}.",
        f"Fig {n}:",
        f"Fig {n}.",
        f"FIGURE {n}:",
    ]


def is_skip_name(name: str) -> bool:
    return bool(SKIP_NAME_RE.match(Path(name).name))


def min_render_height(table: bool) -> int:
    return 200 if table else 110


def grow_caption_line(
    cap_x0: float,
    cap_y0: float,
    cap_x1: float,
    cap_y1: float,
    frags: list[tuple[float, float, float, float]],
    page_width: float,
) -> tuple[float, float, float, float]:
    """同じ行のキャプション続きだけを伸ばす。隣カラムは段間で切る。"""
    mid = page_width / 2.0
    same = [
        f for f in frags
        if abs(f[1] - cap_y0) <= 3.5 and f[0] >= cap_x0 - 8
    ]
    same.sort(key=lambda t: t[0])
    x0, x1, y1 = cap_x0, cap_x1, cap_y1
    for lx0, _ly0, lx1, ly1 in same:
        if lx0 > x1 + 6:
            break
        if x1 < mid - 8 and lx0 > mid + 2:
            break
        x0 = min(x0, lx0)
        x1 = max(x1, lx1)
        y1 = max(y1, ly1)
    return x0, cap_y0, x1, y1


def caption_is_full_width(
    line_x0: float, line_x1: float, page_width: float
) -> bool:
    mid = page_width / 2.0
    width = line_x1 - line_x0
    crosses = line_x0 < mid - 20 and line_x1 > mid + 20
    return crosses or width >= 0.50 * page_width


def clamp_column(
    x0: float,
    x1: float,
    cap_cx: float,
    page_width: float,
    full_width: bool,
) -> tuple[float, float]:
    if full_width:
        return 44.0, page_width - 44.0
    mid = page_width / 2.0
    if cap_cx < mid:
        return max(36.0, x0), min(x1, mid - 4.0)
    return max(x0, mid + 4.0), min(page_width - 36.0, x1)


def looks_like_body_leak(width_px: int, height_px: int) -> bool:
    """ACM 全幅スライスなど、本文混入の強いサイズ合図。目視対象。"""
    if width_px >= 1180 and height_px >= 800:
        return True
    if abs(width_px - 1286) <= 16 and height_px >= 700:
        return True
    if width_px >= 1480 and height_px >= 480:
        return True
    return False


def _load_fitz():
    try:
        import pymupdf as fitz  # type: ignore[import-not-found]
        return fitz
    except ImportError:
        pass
    try:
        import fitz  # type: ignore[import-not-found]
        return fitz
    except ImportError as error:
        raise SystemExit(
            "PyMuPDF が必要。例: uv run --with pymupdf "
            '--cache-dir "$TMPDIR/uv-cache" python3 scripts/crop-paper-figures.py ...'
        ) from error


def find_caption(page, num: int, table: bool):
    hits = []
    for query in caption_queries(num, table):
        try:
            hits.extend(page.search_for(query))
        except Exception:
            continue
    if not hits:
        return None
    hits.sort(key=lambda r: (r.height > 18, r.y0, r.x0))
    return hits[0]


def _graphic_rects(page, fitz, include_tables: bool):
    pr = page.rect
    rects = []
    for drawing in page.get_drawings():
        rect = drawing["rect"] & pr
        if rect.width < 3 and rect.height < 3:
            continue
        if rect.height < 0.8:
            continue
        rects.append(rect)
    try:
        for image in page.get_image_info():
            bbox = image.get("bbox")
            if not bbox:
                continue
            rect = fitz.Rect(bbox) & pr
            if not rect.is_empty:
                rects.append(rect)
    except Exception:
        pass
    if include_tables:
        try:
            for table in page.find_tables().tables:
                rect = fitz.Rect(table.bbox) & pr
                if not rect.is_empty:
                    rects.append(rect)
        except Exception:
            pass
    return rects


def _caption_ys(page) -> list[float]:
    ys = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            bbox = line.get("bbox")
            if not bbox:
                continue
            text = "".join(s.get("text", "") for s in (line.get("spans") or []))
            if _CAP_Y_RE.search(text) and (bbox[3] - bbox[1]) <= 18:
                ys.append(bbox[1])
    return sorted(ys)


def _caption_line_box(page, cap, fitz):
    frags = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            bbox = line.get("bbox")
            if bbox:
                frags.append(tuple(bbox[:4]))
    x0, y0, x1, y1 = grow_caption_line(
        cap.x0, cap.y0, cap.x1, cap.y1, frags, page.rect.width
    )
    return fitz.Rect(x0, y0, x1, y1)


def _text_cluster_top(page, cap, x0: float, x1: float) -> float | None:
    col = []
    for block in page.get_text("blocks"):
        bx0, by0, bx1, by1 = block[:4]
        if by1 > cap.y0 + 2 or by0 < 46:
            continue
        if bx1 < x0 + 8 or bx0 > x1 - 8:
            continue
        col.append((by0, by1))
    col.sort(key=lambda t: -t[1])
    cluster = []
    for by0, by1 in col:
        if not cluster:
            if cap.y0 - by1 < 28:
                cluster.append((by0, by1))
            continue
        top = min(item[0] for item in cluster)
        if by1 >= top - 20:
            cluster.append((by0, by1))
        else:
            break
    if not cluster:
        return None
    return min(item[0] for item in cluster) - 4


def crop_rect(page, cap, table: bool):
    fitz = _load_fitz()
    pw, ph = page.rect.width, page.rect.height
    mid = pw / 2.0
    cap_line = _caption_line_box(page, cap, fitz)
    cx0, cy0, cx1, cy1 = cap_line.x0, cap.y0, cap_line.x1, cap_line.y1
    cap_cx = 0.5 * (cap.x0 + cap.x1)
    y_bot = min(ph - 24.0, cy1 + 4.0)
    full_w = caption_is_full_width(cx0, cx1, pw)

    gizmos = []
    for rect in _graphic_rects(page, fitz, include_tables=table):
        if rect.y0 > cy0 + 2 or rect.y1 < 46:
            continue
        if rect.y0 < 46 and rect.height < 16:
            continue
        gizmos.append(rect)

    near = [r for r in gizmos if r.y0 > cy0 - 300 and r.y1 > cy0 - 220]
    if page.number == 0:
        lower = [r for r in near if r.y0 > 145]
        if lower:
            near = lower
    if not near:
        near = [r for r in gizmos if r.y0 > cy0 - 340]

    left_near = [r for r in near if r.x1 < mid + 18]
    right_near = [r for r in near if r.x0 > mid - 18]
    if full_w:
        chosen = near
    elif cap_cx < mid:
        chosen = left_near or near
    else:
        chosen = right_near or near

    substantial = [r for r in chosen if r.height >= 8 or r.width * r.height >= 500]
    if not substantial:
        substantial = chosen

    cluster = []
    for rect in sorted(substantial, key=lambda r: -r.y1):
        if rect.y1 > cy0 + 6:
            continue
        if not cluster:
            if cy0 - rect.y1 < 90:
                cluster.append(rect)
            continue
        top = min(item.y0 for item in cluster)
        if rect.y1 >= top - 22 or (rect.y0 < top and rect.y1 > top - 22):
            cluster.append(rect)
        else:
            break
    if not cluster:
        cluster = substantial

    if cluster:
        y_top = min(r.y0 for r in cluster) - 6
        gx0 = min(r.x0 for r in cluster) - 8
        gx1 = max(r.x1 for r in cluster) + 8
        x0, x1 = clamp_column(gx0, gx1, cap_cx, pw, full_w)
        if not full_w:
            x0 = max(36.0, min(x0, cx0 - 6))
            x1 = min(pw - 36.0, max(x1, cx1 + 6))
            x0, x1 = clamp_column(x0, x1, cap_cx, pw, False)
        strong = max(r.height for r in cluster) >= 14
    else:
        strong = False
        y_top = max(58.0, cy0 - 250.0)
        x0, x1 = clamp_column(40.0, pw - 40.0, cap_cx, pw, False)

    if not strong:
        tx0, tx1 = clamp_column(40.0, pw - 40.0, cap_cx, pw, full_w)
        ttop = _text_cluster_top(page, cap, tx0, tx1)
        if ttop is not None:
            y_top = ttop
            x0, x1 = tx0, tx1

    for _ in range(3):
        lifted = None
        for block in page.get_text("blocks"):
            bx0, by0, bx1, by1 = block[:4]
            if by1 > y_top + 2 or y_top - by1 > 20 or by0 < 72:
                continue
            if by1 - by0 > 26:
                continue
            if bx1 < x0 + 8 or bx0 > x1 - 8:
                continue
            lifted = by0 if lifted is None else min(lifted, by0)
        if lifted is None:
            break
        y_top = lifted - 3

    prev = [y for y in _caption_ys(page) if y < cy0 - 10]
    if prev:
        y_top = max(y_top, max(prev) + 18)

    y_top = max(48.0, y_top)
    x0 = min(x0, cx0 - 4)
    x1 = max(x1, cx1 + 4)
    if table:
        try:
            best = None
            for item in page.find_tables().tables:
                rect = fitz.Rect(item.bbox) & page.rect
                if rect.is_empty or rect.height < 40:
                    continue
                if rect.y1 > cy0 + 10 or cy0 - rect.y1 > 90:
                    continue
                if best is None or rect.height > best.height:
                    best = rect
            if best is not None:
                x0 = min(x0, best.x0 - 4)
                x1 = max(x1, best.x1 + 4)
                y_top = min(y_top, best.y0 - 4)
        except Exception:
            pass
    if y_bot - y_top < 48:
        y_top = max(48.0, cy0 - 220.0)
    if y_bot - y_top < 36 or x1 - x0 < 40:
        return None
    return fitz.Rect(x0, y_top, x1, y_bot)


def crop_one(pdf: Path, out_dir: Path, kind: str, num: int, filename: str) -> dict:
    if is_skip_name(filename):
        return {"file": filename, "ok": False, "reason": "skip_name"}
    fitz = _load_fitz()
    table = kind.lower().startswith("tab")
    doc = fitz.open(str(pdf))
    try:
        found = None
        for index in range(len(doc)):
            cap = find_caption(doc[index], num, table)
            if cap is not None:
                found = (index, cap)
                break
        if found is None:
            return {"file": filename, "ok": False, "reason": "no_caption"}
        page = doc[found[0]]
        rect = crop_rect(page, found[1], table)
        if rect is None:
            return {
                "file": filename,
                "ok": False,
                "reason": "thin",
                "page": found[0] + 1,
            }
        pix = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), clip=rect)
        if pix.height < min_render_height(table) or pix.width < 80:
            return {
                "file": filename,
                "ok": False,
                "reason": "too_short",
                "page": found[0] + 1,
                "px": [pix.width, pix.height],
            }
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / filename
        pix.save(str(dest))
        return {
            "file": filename,
            "ok": True,
            "page": found[0] + 1,
            "px": [pix.width, pix.height],
            "rect": [round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1)],
            "review": looks_like_body_leak(pix.width, pix.height),
        }
    finally:
        doc.close()


def parse_id(spec: str) -> tuple[str, int, str]:
    match = ID_RE.match(spec.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"--id は fig:N:filename.png または tab:N:filename.png: {spec}"
        )
    return match.group(1), int(match.group(2)), match.group(3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--id",
        dest="ids",
        action="append",
        required=True,
        help="fig:1:fig01-architecture.png を複数回",
    )
    args = parser.parse_args(argv)
    if not args.pdf.exists():
        print(f"pdf が無い: {args.pdf}", file=sys.stderr)
        return 2

    failed = 0
    for spec in args.ids:
        kind, num, filename = parse_id(spec)
        result = crop_one(args.pdf, args.out, kind, num, filename)
        print(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
