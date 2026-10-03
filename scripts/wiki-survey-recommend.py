#!/usr/bin/env python3
"""wiki-survey-recommend.py — wiki-survey テーマ推薦の機械信号。

判定(ready / 姉妹 / 要 ingest / 別 skill)はしない。frontmatter の sources を
文書単位に畳み、既存 surveys との同一主題 / 近傍、テーマ塊所属、compile debt、
研究関心プロファイルを JSON で出す。エージェントは stdout だけを見る。
index.md / clusters.json / profile.md を Read するな。

Usage:
  python3 scripts/wiki-survey-recommend.py
  python3 scripts/wiki-survey-recommend.py --min-sources 13 --near-min 8 --top-dense 40

Exit codes:
  0 — 成功
  2 — 使い方の誤り
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_USAGE = 2

H2_RE = re.compile(r"^## ", flags=re.M)
TOP_BULLET_RE = re.compile(r"^[-*] ", flags=re.M)
INBOX_HEADING = "未編纂の観察"
LEGACY_INBOX_HEADING = "横断的知見"
UNRESOLVED_HEADING = "未解決の問い"
FIXED_HEADINGS = frozenset({
    "定義", "子概念", UNRESOLVED_HEADING, INBOX_HEADING, LEGACY_INBOX_HEADING, "関連", "出典",
})
# paper-ids / concept-candidates と同じ。章分割を文書に畳む。
CHAPTER_SUFFIX_RE = re.compile(r"\s+-\s+(Chapter|Ch\.|Part|Section|Appendix|第|付録)\s*.*$", re.I)
IDENTITY_LEFTOVER_RE = re.compile(r"^(の)?(教科書|サーベイ|文献横断調査).*$")
CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z0-9]")
DEFAULT_MIN_SOURCES = 13
DEFAULT_NEAR_MIN = 8
DEFAULT_TOP_DENSE = 40
DEFAULT_TOP_NEAR = 20
DEFAULT_TOP_DEBT = 15
DEFAULT_MIN_INBOX = 5


def vault_root():
    return Path(os.environ.get("WIKI_VAULT_ROOT") or _SCRIPTS.parent).resolve()


def log(msg):
    print(msg, file=sys.stderr)


def _stats_mod():
    spec = importlib.util.spec_from_file_location("wiki_concept_stats", _SCRIPTS / "wiki-concept-stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_STATS = _stats_mod()
split_frontmatter = _STATS.split_frontmatter
as_list = _STATS.as_list
wikilink_target = _STATS.wikilink_target


def unique_targets(value):
    seen = []
    for item in as_list(value):
        name = wikilink_target(item)
        if name and name not in seen:
            seen.append(name)
    return seen


def doc_key(source):
    """章分割 source を文書単位に畳む。concept-candidates.doc_key と同じ規則。"""
    stem = Path(str(source)).stem if str(source).endswith(".md") else str(source)
    return CHAPTER_SUFFIX_RE.sub("", stem).strip()


def unique_documents(value):
    return sorted({doc_key(s) for s in unique_targets(value) if doc_key(s)})


def h2_sections(body):
    matches = list(H2_RE.finditer(body))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[match.start():end]
        heading = block.split("\n", 1)[0][3:].strip()
        out.append((heading, block))
    return out


def compile_metrics(body):
    inbox = 0
    topics = 0
    unresolved = 0
    legacy = False
    for heading, block in h2_sections(body):
        bullets = len(TOP_BULLET_RE.findall(block))
        if heading in (INBOX_HEADING, LEGACY_INBOX_HEADING):
            inbox += bullets
            if heading == LEGACY_INBOX_HEADING:
                legacy = True
        elif heading == UNRESOLVED_HEADING:
            unresolved = bullets
        elif heading not in FIXED_HEADINGS:
            topics += 1
    return {
        "inbox_bullets": inbox,
        "topic_sections": topics,
        "legacy_heading": legacy,
        "n_unresolved": unresolved,
    }


def list_md(dirpath):
    if not dirpath.is_dir():
        return []
    out = []
    for name in os.listdir(dirpath):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        path = dirpath / name
        if path.is_file():
            out.append(path)
    return out


def rel_posix(root, path):
    return path.resolve().relative_to(root.resolve()).as_posix()


def aliases_of(fm, fallback):
    names = []
    title = fm.get("title")
    if not title or isinstance(title, list):
        title = fallback
    else:
        title = str(title).strip() or fallback
    names.append(title)
    for item in as_list(fm.get("aliases")):
        raw = wikilink_target(item) or str(item).strip().strip('"').strip("'")
        if raw and raw not in names:
            names.append(raw)
    return title, names


def fold_title(s):
    return re.sub(r"[\s—―\-–]+", "", str(s))


def normalize_topic(s):
    n = fold_title(s).replace("/", "").replace("・", "")
    return n.replace("モニタリング", "監視")


def topic_variants(s):
    n = normalize_topic(s)
    out = {n}
    if n.endswith("管理"):
        out.add(n[:-2] + "対応")
    if n.endswith("対応"):
        out.add(n[:-2] + "管理")
    return {v for v in out if v}


def token_ok(s, *, for_question=False):
    cjk = len(CJK_RE.findall(s))
    latin = len(LATIN_RE.findall(s))
    if for_question:
        return cjk >= 3 or latin >= 6
    return cjk >= 2 or latin >= 3


def leftover_identity(survey_norm, concept_norm):
    if not concept_norm or concept_norm not in survey_norm:
        return False
    leftover = survey_norm.replace(concept_norm, "", 1)
    return leftover == "" or bool(IDENTITY_LEFTOVER_RE.match(leftover))


def page_mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def scan_concepts(root):
    rows = []
    newest = 0
    for path in list_md(root / "wiki" / "concepts"):
        newest = max(newest, page_mtime(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = split_frontmatter(text)
        title, aliases = aliases_of(fm, path.stem)
        srcs = unique_targets(fm.get("sources"))
        row = {
            "path": rel_posix(root, path),
            "title": title,
            "aliases": aliases,
            "n_sources": len(srcs),
            "n_documents": len(unique_documents(fm.get("sources"))),
            "lines": len(text.splitlines()),
        }
        row.update(compile_metrics(body))
        rows.append(row)
    rows.sort(key=lambda r: (-r["n_documents"], -r["n_sources"], -r["n_unresolved"], r["path"]))
    return rows, newest


def scan_surveys(root):
    rows = []
    for path in list_md(root / "wiki" / "surveys"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _body = split_frontmatter(text)
        title, aliases = aliases_of(fm, path.stem)
        question = fm.get("question")
        if isinstance(question, list):
            question = " ".join(str(x) for x in question)
        related = []
        for item in unique_targets(fm.get("related")):
            related.append(Path(item).stem)
        rows.append({
            "path": rel_posix(root, path),
            "title": title,
            "aliases": aliases,
            "question": str(question or "").strip(),
            "related": related,
            "n_sources": len(unique_targets(fm.get("sources"))),
            "n_documents": len(unique_documents(fm.get("sources"))),
        })
    rows.sort(key=lambda r: r["title"])
    return rows


def latin_topic_in(needle, hay):
    """SLO ⊂ 'SLI/SLO 教科書' は可。RDMA ⊂ 'RDMAネットワーク…' は不可。"""
    if not token_ok(needle):
        return False
    n = re.escape(str(needle))
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){n}(?![A-Za-z0-9])(?:[/・]|(?:[\s/・]*)の?(?:教科書|サーベイ|文献横断調査)|$)",
        str(hay),
        re.I,
    ))


def identity_hit(concept_names, survey):
    survey_titles = [survey["title"], *(survey.get("aliases") or [])]
    survey_norms = []
    for name in survey_titles:
        survey_norms.extend(topic_variants(name))
    for name in concept_names:
        for variant in topic_variants(name):
            if any(leftover_identity(sn, variant) for sn in survey_norms):
                return True
        if any(latin_topic_in(name, title) for title in survey_titles):
            return True
    return False


def question_title_hit(concept_title, question):
    """question が『Xの教科書を…』なら同一主題。『Xは除外する』は同一主題にしない。"""
    if not question or not token_ok(concept_title, for_question=True):
        return False
    qn = normalize_topic(question)
    for variant in topic_variants(concept_title):
        if leftover_identity(qn, variant):
            return True
    return False


def related_hit(concept_names, survey):
    related = {normalize_topic(x) for x in survey.get("related") or []}
    for name in concept_names:
        if normalize_topic(name) in related:
            return True
    return False


def classify_survey(concept_names, survey):
    if identity_hit(concept_names, survey) or question_title_hit(concept_names[0], survey.get("question")):
        return "hit"
    if related_hit(concept_names, survey):
        return "near"
    return None


def attach_survey_hits(concepts, surveys):
    for row in concepts:
        names = row.get("aliases") or [row["title"]]
        hits = []
        near = []
        for survey in surveys:
            kind = classify_survey(names, survey)
            if kind == "hit":
                hits.append(survey["path"])
            elif kind == "near":
                near.append(survey["path"])
        row["survey_hits"] = hits
        row["survey_near"] = near
        row.pop("aliases", None)


def load_profile(root):
    spec = importlib.util.spec_from_file_location("wiki_profile", _SCRIPTS / "wiki-profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path, _rel = mod.resolve_profile(root)
    if not path.is_file():
        return None
    return mod.parse_profile(path.read_text(encoding="utf-8"))


def profile_fit(title, profile):
    if not profile:
        return "unknown"
    folded = normalize_topic(title)
    if not token_ok(title):
        return "unknown"
    for bucket in ("out_of_scope", "core", "adjacent"):
        for item in profile.get(bucket) or []:
            other = normalize_topic(item)
            if not other:
                continue
            if folded == other or (token_ok(title, for_question=True) and (folded in other or other in folded)):
                return bucket
            if token_ok(title) and token_ok(item) and (folded in other or other in folded):
                return bucket
    return "unknown"


def attach_profile_fit(concepts, profile):
    for row in concepts:
        row["profile_fit"] = profile_fit(row["title"], profile)


def load_clusters(root):
    path = root / ".vault-meta" / "clusters.json"
    if not path.is_file():
        return None, None, 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, 0
    labels = {}
    compact = []
    for cluster in payload.get("clusters") or []:
        cid = cluster.get("id")
        label = cluster.get("label") or ""
        labels[cid] = label
        tops = []
        for member in cluster.get("top_members") or []:
            title = member.get("title")
            if title:
                tops.append(title)
            if len(tops) >= 4:
                break
        compact.append({
            "id": cid,
            "label": label,
            "size": cluster.get("size") or 0,
            "top": tops,
        })
    compact.sort(key=lambda c: (-c["size"], c["id"] if isinstance(c["id"], int) else 0))
    meta = {
        "built_at": payload.get("built_at"),
        "topology_sha256": payload.get("topology_sha256"),
        "membership": payload.get("membership") or {},
        "labels": labels,
    }
    return compact, meta, page_mtime(path)


def attach_clusters(concepts, cluster_meta):
    membership = (cluster_meta or {}).get("membership") or {}
    labels = (cluster_meta or {}).get("labels") or {}
    for row in concepts:
        rec = membership.get(row["path"]) or {}
        cid = rec.get("id")
        row["cluster_id"] = cid
        row["cluster_label"] = labels.get(cid, "") if cid is not None else ""
        row["on_backbone"] = cid is not None


def collect(root=None, min_sources=DEFAULT_MIN_SOURCES, near_min=DEFAULT_NEAR_MIN,
            top_dense=DEFAULT_TOP_DENSE, top_near=DEFAULT_TOP_NEAR,
            top_debt=DEFAULT_TOP_DEBT, min_inbox=DEFAULT_MIN_INBOX):
    root = Path(root or vault_root())
    warnings = []
    concepts, newest_concept = scan_concepts(root)
    surveys = scan_surveys(root)
    attach_survey_hits(concepts, surveys)
    profile = load_profile(root)
    if profile is None:
        warnings.append("profile_missing")
    attach_profile_fit(concepts, profile)
    compact, cluster_meta, clusters_mtime = load_clusters(root)
    if compact is None:
        warnings.append("clusters_missing")
        compact = []
        for row in concepts:
            row["cluster_id"] = None
            row["cluster_label"] = ""
            row["on_backbone"] = False
        cluster_info = {"built_at": None, "topology_sha256": None}
    else:
        attach_clusters(concepts, cluster_meta)
        cluster_info = {
            "built_at": cluster_meta.get("built_at"),
            "topology_sha256": cluster_meta.get("topology_sha256"),
        }
        if newest_concept > clusters_mtime + 1:
            warnings.append("clusters_stale")
    dense = [c for c in concepts if c["n_documents"] >= min_sources]
    near = [c for c in concepts if near_min <= c["n_documents"] < min_sources]
    debt = [c for c in concepts if c["inbox_bullets"] >= min_inbox]
    debt.sort(key=lambda r: (-r["inbox_bullets"], r["path"]))
    return {
        "gates": {"min_sources": min_sources, "near_min": near_min, "min_inbox": min_inbox},
        "counts": {
            "concepts": len(concepts),
            "ge_min": len(dense),
            "near": len(near),
            "compile_debt": len(debt),
            "surveys": len(surveys),
            "clusters": len(compact),
        },
        "profile": profile,
        "surveys": [{k: s[k] for k in ("path", "title", "question", "n_sources", "n_documents")} for s in surveys],
        "dense": dense[:top_dense],
        "near": near[:top_near],
        "compile_debt": debt[:top_debt],
        "clusters": compact,
        "cluster_info": cluster_info,
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="wiki-survey テーマ推薦の機械信号。")
    parser.add_argument("--min-sources", type=int, default=DEFAULT_MIN_SOURCES)
    parser.add_argument("--near-min", type=int, default=DEFAULT_NEAR_MIN)
    parser.add_argument("--top-dense", type=int, default=DEFAULT_TOP_DENSE)
    parser.add_argument("--top-near", type=int, default=DEFAULT_TOP_NEAR)
    parser.add_argument("--top-debt", type=int, default=DEFAULT_TOP_DEBT)
    parser.add_argument("--min-inbox", type=int, default=DEFAULT_MIN_INBOX)
    args = parser.parse_args(argv)
    if min(args.min_sources, args.near_min, args.top_dense, args.top_near,
           args.top_debt, args.min_inbox) < 1:
        log("ERR: numeric flags must be >= 1")
        return EXIT_USAGE
    if args.near_min >= args.min_sources:
        log("ERR: --near-min must be < --min-sources")
        return EXIT_USAGE
    payload = collect(
        min_sources=args.min_sources,
        near_min=args.near_min,
        top_dense=args.top_dense,
        top_near=args.top_near,
        top_debt=args.top_debt,
        min_inbox=args.min_inbox,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
