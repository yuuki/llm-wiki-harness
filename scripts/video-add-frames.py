#!/usr/bin/env python3
"""video-add-frames.py — 選んだ候補フレームを .raw/videos/<slug>/frames/ に追加する。

prepare-video-source.sh が vault 外に残す候補(cand-HHMMSS.jpg)から、エージェントが
コンタクトシートで選んだものを、続き番号の frame-NNN.jpg としてコピーする。
metadata.json の frames と frame_times(frame 名 → 時刻)も同時に更新する。
既存の frame は書き換えない(.raw の原本不変)。

usage: python3 scripts/video-add-frames.py <slug> <cand-HHMMSS.jpg> [...]
出力: 追加した frame 名と時刻を 1 行ずつ(例: frame-031	0:18:20)
"""
import json
import re
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    dest = Path(".raw/videos") / sys.argv[1]
    frames_dir = dest / "frames"
    meta_path = dest / "metadata.json"
    if not frames_dir.is_dir() or not meta_path.is_file():
        print(f"ERROR: {dest} に frames/ と metadata.json が無い", file=sys.stderr)
        return 1
    meta = json.loads(meta_path.read_text())
    frame_times = meta.setdefault("frame_times", {})
    existing = sorted(int(m.group(1)) for p in frames_dir.glob("frame-*.jpg")
                      if (m := re.fullmatch(r"frame-(\d+)\.jpg", p.name)))
    n = existing[-1] if existing else 0
    for arg in sys.argv[2:]:
        src = Path(arg)
        m = re.fullmatch(r"cand-(\d{2})(\d{2})(\d{2})\.jpg", src.name)
        if not src.is_file() or not m:
            print(f"ERROR: 候補フレーム(cand-HHMMSS.jpg)ではない: {arg}", file=sys.stderr)
            return 1
        n += 1
        name = f"frame-{n:03d}"
        shutil.copyfile(src, frames_dir / f"{name}.jpg")
        frame_times[name] = f"{int(m.group(1))}:{m.group(2)}:{m.group(3)}"
        print(f"{name}\t{frame_times[name]}")
    meta["frames"] = len(list(frames_dir.glob("frame-*.jpg")))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
