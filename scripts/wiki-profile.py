#!/usr/bin/env python3
"""wiki-profile.py — 研究関心プロファイルの 40 行以内の要約を出す。

profile.md は人間が所有する。探索順は `WIKI_PROFILE_PATH`(絶対、または vault 相対)、
`research/curation/profile.md`、`wiki/meta/profile.md`。本スクリプトはそれを**読むだけ**で、
ingest(concept の新設・保留判断、conventions §12)と query / survey(設問の枠)が
全文を Read せずに関心の軸を知るための要約器である。profile.md は書き換えない。
プロファイルが無い vault では終了 3(呼び出し側は関心判定を飛ばす)。

出力(既定、markdown):
  - 研究の背骨(番号付きの柱)
  - コア関心 / 周辺関心 / 対象外 の各節から、箇条書きの太字見出しだけ(1 節 1 行に畳む)
  - 方法論・視点の好み の太字見出し

使い方:
  python3 scripts/wiki-profile.py                 # 要約(≤ 40 行)
  python3 scripts/wiki-profile.py --json          # 機械可読
  python3 scripts/wiki-profile.py --lines 25      # 行数上限を詰める(節ごとの見出しを切り詰める)

終了コード: 0 成功 / 3 profile.md が無い(呼び出し側は関心判定を飛ばす)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
DEFAULT_PROFILE_RELS = ("research/curation/profile.md", "wiki/meta/profile.md")

SECTION_KEYS = (("コア関心", "core"), ("周辺関心", "adjacent"), ("対象外", "out_of_scope"), ("方法論", "method"))
BOLD_RE = re.compile(r"^\s*-\s+\*\*(.+?)\*\*")
PILLAR_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


def resolve_profile(vault_root=None):
    root = Path(vault_root or VAULT_ROOT)
    env = os.environ.get("WIKI_PROFILE_PATH")
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        return p, rel
    for rel in DEFAULT_PROFILE_RELS:
        p = root / rel
        if p.is_file():
            return p, rel
    return root / DEFAULT_PROFILE_RELS[0], DEFAULT_PROFILE_RELS[0]


def split_frontmatter(text):
    if not text.startswith("---"):
        return text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


def parse_profile(text):
    body = split_frontmatter(text)
    out = {"pillars": [], "core": [], "adjacent": [], "out_of_scope": [], "method": [], "generated": None}
    m = re.search(r"^生成日:\s*(\S+)", text, re.M)
    if m:
        out["generated"] = m.group(1)
    current = None
    in_pillars = False
    for line in body.split("\n"):
        h = HEADING_RE.match(line)
        if h:
            current = None
            in_pillars = False
            for jp, key in SECTION_KEYS:
                if jp in h.group(1):
                    current = key
                    break
            continue
        if "背骨" in line or "本柱" in line:
            in_pillars = True
            continue
        if in_pillars:
            p = PILLAR_RE.match(line)
            if p:
                out["pillars"].append(re.sub(r"[((].*?[))]\s*$", "", p.group(2)).strip())
                continue
            if line.strip() and not out["pillars"]:
                continue
            if line.strip() == "" and out["pillars"]:
                in_pillars = False
        if current:
            b = BOLD_RE.match(line)
            if b:
                out[current].append(b.group(1).rstrip("。"))
    return out


def render(data, max_lines, profile_rel="research/curation/profile.md"):
    lines = [f"# 研究関心プロファイル(要約。全文は {profile_rel}、人間所有)"]
    if data.get("generated"):
        lines.append(f"- 蒸留日 {data['generated']}")
    if data["pillars"]:
        lines.append("")
        lines.append("## 研究の背骨")
        for i, p in enumerate(data["pillars"], 1):
            lines.append(f"{i}. {p}")
    budget_sections = [("コア関心(concept 新設・保留を後押しする軸)", "core"),
                       ("周辺関心(source に留めるか、既存 concept の観察に足す)", "adjacent"),
                       ("対象外(concept を新設しない。source ページで閉じる)", "out_of_scope"),
                       ("評価の癖(query / survey の枠付け)", "method")]
    remaining = max_lines - len(lines) - 2 * len(budget_sections)
    per = max(1, remaining // max(1, len(budget_sections)))
    for title, key in budget_sections:
        items = data.get(key) or []
        if not items:
            continue
        lines.append("")
        lines.append(f"## {title}")
        # 1 行に 3 項目ずつ畳み、節あたり per 行まで
        rows = []
        for i in range(0, len(items), 3):
            rows.append("- " + "、".join(items[i:i + 3]))
        if len(rows) > per:
            rows = rows[:per]
            rows[-1] += " ほか"
        lines.extend(rows)
    return "\n".join(lines[:max_lines]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="研究関心プロファイルの要約。")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--lines", type=int, default=40)
    args = parser.parse_args(argv)
    path, rel = resolve_profile()
    if not path.is_file():
        print(
            "WARN: 関心プロファイルが無い"
            "(`WIKI_PROFILE_PATH` / research/curation/profile.md / wiki/meta/profile.md)。"
            "呼び出し側は関心判定を飛ばす",
            file=sys.stderr,
        )
        return 3
    data = parse_profile(path.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(render(data, args.lines, profile_rel=rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
