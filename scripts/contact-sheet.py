#!/usr/bin/env python3
"""contact-sheet.py — 縮小グリッド(コンタクトシート)を一時ディレクトリに作る。

論文・書籍の埋め込み画像を数枚のシートに並べ、Read 回数を減らす。
原本は読んだだけで変更しない。出力の既定は一時ディレクトリであり、
`.raw/` 配下への書き込みは拒否する。

使い方:
  python3 scripts/contact-sheet.py --dir .raw/papers/<slug>/images [--out DIR]
  python3 scripts/contact-sheet.py --glob '.raw/papers/<slug>/images/image-*.png' --out DIR
  python3 scripts/contact-sheet.py --manifest .raw/papers/<slug>/images/images.json --out DIR

Pillow が無い処理系では、環境変数 CONTACT_SHEET_REEXEC=1 を立てて
`uv run --with pillow` で自己再実行する(無限ループ防止)。
"""

from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REEXEC = 3

MAX_CELL = 1024
MAX_COLS = 8
MAX_PER_SHEET = 48
COUNT_WARN = 24

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

MARGIN = 12
GAP = 10
LABEL_GAP = 4
LABEL_H = 18
SHEET_BG = (248, 248, 248)
CELL_BG = (255, 255, 255)
CELL_BORDER = (200, 200, 200)
LABEL_FG = (40, 40, 40)
UNREADABLE_BG = (230, 230, 230)
UNREADABLE_FG = (160, 40, 40)


def vault_root():
    env = os.environ.get("WIKI_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def resolve_user_path(value):
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (vault_root() / path).resolve()


def is_under_raw(path):
    """出力先が `.raw/` 配下なら真。パス成分と vault の `.raw` の両方を見る。"""
    resolved = path.resolve()
    if ".raw" in resolved.parts:
        return True
    raw = vault_root() / ".raw"
    if raw.exists() or raw.is_symlink():
        try:
            resolved.relative_to(raw.resolve())
            return True
        except ValueError:
            pass
    return False


def check_output_dest(dest, out_dir):
    """書き込み先が安全なら None。拒否するなら理由を返す。

    シンボリックリンクは追わず、リンク自体を拒否する。resolve 先が
    `--out` の外、または `.raw/` 配下なら拒否する。
    """
    dest = Path(dest)
    if dest.is_symlink() or os.path.islink(str(dest)):
        return "出力パスがシンボリックリンクである: %s" % dest
    if dest.exists() and dest.is_dir():
        return "出力パスがディレクトリである: %s" % dest
    try:
        resolved = dest.resolve()
    except OSError:
        return "出力パスを解決できない: %s" % dest
    if is_under_raw(resolved):
        return "出力の実体が .raw/ 配下である: %s" % dest
    try:
        resolved.relative_to(out_dir.resolve())
    except ValueError:
        return "出力の実体が --out の外である: %s" % dest
    return None


def atomic_replace(dest, data, out_dir):
    """一時ファイルへ書いてから `os.replace` する。拒否したらそのファイルは書かない。"""
    warning = check_output_dest(dest, out_dir)
    if warning:
        return warning
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".cs-tmp-", suffix=".part", dir=str(out_dir)
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        warning = check_output_dest(dest, out_dir)
        if warning:
            return warning
        os.replace(tmp_name, str(dest))
        tmp_name = None
    except OSError as exc:
        return "出力を書けない: %s (%s)" % (dest, exc)
    finally:
        if tmp_name is not None and os.path.lexists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return None


def die(message, code=EXIT_USAGE):
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(code)


def ensure_pillow():
    try:
        import PIL  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("CONTACT_SHEET_REEXEC") == "1":
        die("Pillow を読み込めない。CONTACT_SHEET_REEXEC=1 のため再実行しない。")
    uv = shutil.which("uv")
    if not uv:
        die("Pillow が無く、uv も見つからない。", EXIT_REEXEC)
    tmpdir = os.environ.get("TMPDIR") or tempfile.gettempdir()
    cache = os.path.join(tmpdir, "uv-cache")
    env = os.environ.copy()
    env["CONTACT_SHEET_REEXEC"] = "1"
    script = str(Path(__file__).resolve())
    argv = [
        uv,
        "run",
        "--with",
        "pillow",
        "--cache-dir",
        cache,
        "python3",
        script,
    ]
    argv.extend(sys.argv[1:])
    try:
        os.execvpe(uv, argv, env)
    except OSError as exc:
        die("uv の起動に失敗した: %s" % exc, EXIT_REEXEC)


def slug_from_images_dir(directory):
    if directory.name == "images":
        parent = directory.parent.name
        return parent or "images"
    return directory.name or "images"


def infer_slug(dir_paths, manifest_paths, image_paths):
    for raw in manifest_paths:
        mp = resolve_user_path(raw)
        return slug_from_images_dir(mp.parent)
    for raw in dir_paths:
        return slug_from_images_dir(resolve_user_path(raw))
    if image_paths:
        return slug_from_images_dir(image_paths[0].parent)
    return "images"


def collect_dir(raw_dir, include_all):
    directory = resolve_user_path(raw_dir)
    if not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if include_all:
            if suffix in IMAGE_EXTS:
                found.append(path)
            continue
        if path.name.startswith("image-") and suffix == ".png":
            found.append(path)
    return found


def collect_glob(pattern):
    if not Path(pattern).is_absolute():
        pattern = str(vault_root() / pattern)
    found = []
    for match in globlib.glob(pattern):
        path = Path(match)
        if path.is_file():
            found.append(path)
    return found


def collect_manifest(raw_manifest, include_all, page_map):
    manifest_path = resolve_user_path(raw_manifest)
    if not manifest_path.is_file():
        die("manifest が無い: %s" % manifest_path)
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        die("manifest を読めない: %s" % exc)
    if not isinstance(data, dict):
        die("manifest の根がオブジェクトではない。")
    hint = data.get("images_dir")
    if hint:
        images_dir = resolve_user_path(hint)
    else:
        images_dir = manifest_path.parent
    found = []
    for item in data.get("images") or []:
        if not isinstance(item, dict):
            continue
        rel = item.get("file")
        if not rel:
            continue
        kind = item.get("kind")
        name = str(rel)
        if not include_all:
            if kind == "page-render":
                continue
            if Path(name).name.startswith("page-"):
                continue
        path = images_dir / name
        found.append(path)
        page = item.get("page")
        if page is not None and page != "":
            page_map[str(path.resolve())] = page
    return found


def dedupe_sort(paths):
    seen = {}
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen[key] = path
    return sorted(seen.values(), key=lambda p: p.name)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="埋め込み画像のコンタクトシートを一時ディレクトリに作る。原本は変更しない。"
    )
    parser.add_argument(
        "--dir",
        action="append",
        default=[],
        dest="dirs",
        help="画像ディレクトリ(直下のみ。既定は image-*.png)",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        dest="globs",
        help="ファイルの glob",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        dest="manifests",
        help="images.json(images[].file を images_dir 相対で読む)",
    )
    parser.add_argument("--out", default=None, help="出力ディレクトリ")
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument("--cell", type=int, default=320)
    parser.add_argument("--per-sheet", type=int, default=12, dest="per_sheet")
    parser.add_argument(
        "--all",
        action="store_true",
        help="page-*.png と kind: page-render も含める",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="シートパスだけを 1 行ずつ出す",
    )
    parser.add_argument(
        "--index-txt",
        action="store_true",
        dest="index_txt",
        help="同じ out に index.txt を書く",
    )
    return parser.parse_args(argv)


def load_font():
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=14)
    except TypeError:
        return ImageFont.load_default()


def resample_filter():
    from PIL import Image

    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return Image.LANCZOS


def text_width(draw, text, font):
    if hasattr(draw, "textlength"):
        return draw.textlength(text, font=font)
    if hasattr(draw, "textbbox"):
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]
    width, _height = draw.textsize(text, font=font)
    return width


def fit_label(text, font, max_width):
    from PIL import Image, ImageDraw

    scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    if text_width(scratch, text, font) <= max_width:
        return text
    ellipsis = "…"
    while text:
        candidate = text + ellipsis
        if text_width(scratch, candidate, font) <= max_width:
            return candidate
        text = text[:-1]
    return ellipsis


def to_rgb(image):
    from PIL import Image

    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def open_fitted(path, cell):
    from PIL import Image

    try:
        with Image.open(path) as image:
            image.load()
            if getattr(image, "n_frames", 1) > 1:
                image.seek(0)
            rgb = to_rgb(image)
            rgb.load()
            fitted = rgb.copy()
    except Exception:
        return None
    width, height = fitted.size
    if width < 1 or height < 1:
        return None
    scale = float(cell) / float(max(width, height))
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    if (new_w, new_h) != (width, height):
        fitted = fitted.resize((new_w, new_h), resample_filter())
    return fitted


def cell_origin(col_index, row_index, cell):
    x = MARGIN + col_index * (cell + GAP)
    y = MARGIN + row_index * (cell + LABEL_GAP + LABEL_H + GAP)
    return x, y


def draw_unreadable(draw, box, font):
    x, y, cell = box
    draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=UNREADABLE_BG)
    label = "UNREADABLE"
    tw = text_width(draw, label, font)
    tx = x + max(0, (cell - int(tw)) // 2)
    ty = y + max(0, (cell - LABEL_H) // 2)
    draw.text((tx, ty), label, fill=UNREADABLE_FG, font=font)


def render_sheet(chunk, cols, cell, font, page_map):
    from PIL import Image, ImageDraw

    rows = max(1, (len(chunk) + cols - 1) // cols)
    width = MARGIN * 2 + cols * cell + (cols - 1) * GAP
    height = (
        MARGIN * 2
        + rows * (cell + LABEL_GAP + LABEL_H)
        + (rows - 1) * GAP
    )
    sheet = Image.new("RGB", (width, height), SHEET_BG)
    draw = ImageDraw.Draw(sheet)
    index_rows = []
    unreadable = []
    for i, path in enumerate(chunk):
        col = i % cols
        row = i // cols
        x, y = cell_origin(col, row, cell)
        draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=CELL_BG)
        fitted = open_fitted(path, cell)
        if fitted is None:
            draw_unreadable(draw, (x, y, cell), font)
            unreadable.append(str(path.resolve()))
        else:
            px = x + (cell - fitted.size[0]) // 2
            py = y + (cell - fitted.size[1]) // 2
            sheet.paste(fitted, (px, py))
        draw.rectangle(
            [x, y, x + cell - 1, y + cell - 1], outline=CELL_BORDER
        )
        stem = path.stem
        page = page_map.get(str(path.resolve()))
        label = "%s p%s" % (stem, page) if page is not None else stem
        label = fit_label(label, font, cell - 4)
        tw = text_width(draw, label, font)
        lx = x + max(0, (cell - int(tw)) // 2)
        ly = y + cell + LABEL_GAP
        draw.text((lx, ly), label, fill=LABEL_FG, font=font)
        index_rows.append((row + 1, col + 1, path.name, page))
    return sheet, index_rows, unreadable


def write_sheets(images, out_dir, cols, cell, per_sheet, page_map, write_index):
    from io import BytesIO

    font = load_font()
    sheets = []
    index_lines = []
    unreadable = []
    warnings = []
    total = len(images)
    sheet_no = 0
    for start in range(0, total, per_sheet):
        sheet_no += 1
        chunk = images[start : start + per_sheet]
        sheet, rows, unread = render_sheet(chunk, cols, cell, font, page_map)
        dest = out_dir / ("sheet-%03d.png" % sheet_no)
        buf = BytesIO()
        sheet.save(buf, format="PNG")
        warning = atomic_replace(dest, buf.getvalue(), out_dir)
        if warning:
            warnings.append(warning)
        else:
            sheets.append(str(dest.resolve()))
        unreadable.extend(unread)
        stem = "sheet-%03d" % sheet_no
        for row, col, filename, page in rows:
            line = "%s r%dc%d\t%s" % (stem, row, col, filename)
            if page is not None:
                line += "\t%s" % page
            index_lines.append(line)
    if write_index:
        text = "\n".join(index_lines) + ("\n" if index_lines else "")
        warning = atomic_replace(
            out_dir / "index.txt", text.encode("utf-8"), out_dir
        )
        if warning:
            warnings.append(warning)
    return sheets, unreadable, warnings


def main(argv=None):
    args = parse_args(argv)
    if not (args.dirs or args.globs or args.manifests):
        die("--dir / --glob / --manifest のいずれかを指定すること。")
    if args.cols < 1 or args.cell < 1 or args.per_sheet < 1:
        die("--cols / --cell / --per-sheet は 1 以上であること。")
    if args.cell > MAX_CELL or args.cols > MAX_COLS or args.per_sheet > MAX_PER_SHEET:
        die(
            "--cell は %d 以下、--cols は %d 以下、--per-sheet は %d 以下であること。"
            % (MAX_CELL, MAX_COLS, MAX_PER_SHEET)
        )

    page_map = {}
    collected = []
    for raw in args.dirs:
        collected.extend(collect_dir(raw, args.all))
    for pattern in args.globs:
        collected.extend(collect_glob(pattern))
    for raw in args.manifests:
        collected.extend(collect_manifest(raw, args.all, page_map))
    images = dedupe_sort(collected)

    slug = infer_slug(args.dirs, args.manifests, images)
    if args.out:
        out_dir = resolve_user_path(args.out)
    else:
        tmpdir = os.environ.get("TMPDIR") or tempfile.gettempdir()
        out_dir = Path(tmpdir) / ("contact-sheet-%s" % slug)
    if is_under_raw(out_dir):
        die("--out が .raw/ 配下である。")
    if not images:
        die("画像が 0 枚である。")
    if out_dir.exists() and not out_dir.is_dir():
        die("--out がディレクトリではない: %s" % out_dir)

    n_sheets = (len(images) + args.per_sheet - 1) // args.per_sheet
    if len(images) > COUNT_WARN:
        sys.stderr.write(
            "contact-sheet: %d images -> %d sheets; Read only what you need\n"
            % (len(images), n_sheets)
        )

    ensure_pillow()
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets, unreadable, warnings = write_sheets(
        images,
        out_dir,
        args.cols,
        args.cell,
        args.per_sheet,
        page_map,
        args.index_txt,
    )
    if args.quiet:
        for path in sheets:
            sys.stdout.write(path + "\n")
        return EXIT_USAGE if warnings else EXIT_OK
    payload = {
        "sheets": sheets,
        "count": len(images),
        "unreadable": unreadable,
        "cell": args.cell,
        "cols": args.cols,
        "warnings": warnings,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return EXIT_USAGE if warnings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
