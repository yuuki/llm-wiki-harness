#!/usr/bin/env python3
"""wiki-clusters.py — スキル世界のテーマ塊(無向 Blondel)。

アルゴリズム: `plugins/wiki-lens/docs/theme-chunks-algorithm.md`
（vault へ入れたあとは `.obsidian/plugins/wiki-lens/docs/theme-chunks-algorithm.md`）
契約: `plugins/wiki-lens/docs/clusters-for-skills.md`

wiki-lens の画面コミュニティを書き出さない。stem 解決・出現回数・surveys 除外の
バックボーンで独自に分割し、`.vault-meta/clusters.json` に書く。

in_count は無向次数重みである(向きのある被リンク数ではない)。

使い方:
  python3 scripts/wiki-clusters.py build
  python3 scripts/wiki-clusters.py list
  python3 scripts/wiki-clusters.py lookup "<page>"
  python3 scripts/wiki-clusters.py members <id> [--top 20] [--type concept]
  python3 scripts/wiki-clusters.py assign --pages FILE
  python3 scripts/wiki-clusters.py stats

assign は試験と将来フックである。wiki-survey は呼ぶな。

終了コード:
  0 — 成功
  1 — 構築失敗
  2 — 使い方の誤り
  3 — 未構築 / ページが無い / 不明 id
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_MISSING = 3

CLUSTERS_REL = ".vault-meta/clusters.json"
SCAN_DIRS = ("sources", "entities", "concepts", "questions")
TYPE_BY_DIR = {
    "sources": "source",
    "entities": "entity",
    "concepts": "concept",
    "questions": "question",
}
TYPE_RANK = {"concept": 0, "question": 1, "entity": 2, "source": 3}
LOUVAIN_SEED = 0x77696B69
RECIPE = "skill-backbone-undirected-blondel"
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
Q_EPS = 1e-9


def log(msg):
    print(msg, file=sys.stderr)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp).replace(path)
    except Exception:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise


def _i32(x):
    x = x & 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def _u32(x):
    return x & 0xFFFFFFFF


def _imul(a, b):
    return _i32((_u32(a) * _u32(b)) & 0xFFFFFFFF)


def mulberry32(seed):
    """`.obsidian/plugins/wiki-lens/src/core/rng.ts` と同じビット演算。"""
    a = [_i32(seed)]

    def rng():
        a[0] = _i32(a[0] + 0x6D2B79F5)
        t = _imul(a[0] ^ (_u32(a[0]) >> 15), a[0] | 1)
        t = _i32((t + _imul(t ^ (_u32(t) >> 7), t | 61)) ^ t)
        return _u32(t ^ (_u32(t) >> 14)) / 4294967296.0

    return rng


def split_frontmatter(text):
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return "", text


def fm_scalar(fm_text, key):
    m = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", fm_text, flags=re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v or None


def fm_list_links(fm_text, key):
    out = []
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", fm_text, flags=re.M)
    if not m:
        return out
    inline = m.group(1).strip()
    if inline and inline not in ("|", ">", "[]"):
        out.extend(WIKILINK_RE.findall(inline))
    rest = fm_text[m.end():]
    for line in rest.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            break
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item:
            out.extend(WIKILINK_RE.findall(item.group(1)))
    return [t.strip() for t in out]


def link_basename(target):
    t = target.strip()
    if not t or t.startswith((".raw/", "_attachments/", "papers/", "research/", "structures/", "notes/", "books/")):
        return None
    if t.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf", ".gif", ".webp", ".json")):
        return None
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if t.endswith(".md"):
        t = t[:-3]
    return t or None


def list_pages(vault=None):
    root = vault or VAULT_ROOT
    out = []
    for sub in SCAN_DIRS:
        folder = root / "wiki" / sub
        if not folder.is_dir():
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".md") and not name.startswith("_"):
                out.append((sub, folder / name))
    return out


def type_rank(node_type):
    return TYPE_RANK.get(node_type, 4)


# --------------------------------------------------------------------------- 走査


def scan_vault(vault=None):
    """4 ディレクトリを走査し、catalog / 有向出現回数 / seed 集合を返す。"""
    root = Path(vault or VAULT_ROOT).resolve()
    catalog = {}
    by_name = {}
    pages = list_pages(root)
    for sub, path in pages:
        rel = path.resolve().relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_text, _body = split_frontmatter(text)
        folder_type = TYPE_BY_DIR[sub]
        page_type = fm_scalar(fm_text, "type") or folder_type
        if page_type not in TYPE_BY_DIR.values():
            page_type = folder_type
        catalog[rel] = {
            "name": path.stem,
            "type": page_type,
            "title": fm_scalar(fm_text, "title") or path.stem,
            "status": fm_scalar(fm_text, "status") or "",
        }
        by_name.setdefault(path.stem, rel)
    directed = defaultdict(int)
    for sub, path in pages:
        rel = path.resolve().relative_to(root).as_posix()
        if rel not in catalog:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_text, body = split_frontmatter(text)
        occurrences = list(WIKILINK_RE.findall(body))
        for key in ("related", "sources"):
            occurrences.extend(fm_list_links(fm_text, key))
        for t in occurrences:
            base = link_basename(t)
            if not base:
                continue
            other = by_name.get(base)
            if other and other != rel:
                directed[(rel, other)] += 1
    return catalog, by_name, directed


def build_backbone(catalog, directed):
    """seed / 孤立を落とした無向整数重みグラフ。"""
    dropped = {}
    nodes = {}
    for rel, meta in catalog.items():
        if meta["type"] == "entity" and meta["status"] == "seed":
            dropped[rel] = "seed"
            continue
        nodes[rel] = dict(meta)
    undirected = defaultdict(int)
    for (src, dst), count in directed.items():
        if src not in nodes or dst not in nodes or src == dst:
            continue
        a, b = (src, dst) if src < dst else (dst, src)
        undirected[(a, b)] += count
    degree = {rel: 0 for rel in nodes}
    for (a, b), w in undirected.items():
        degree[a] += w
        degree[b] += w
    isolated = [rel for rel, deg in degree.items() if deg == 0]
    for rel in isolated:
        dropped[rel] = "isolated"
        del nodes[rel]
    edges = {pair: w for pair, w in undirected.items() if pair[0] in nodes and pair[1] in nodes}
    in_count = {rel: 0 for rel in nodes}
    for (a, b), w in edges.items():
        in_count[a] += w
        in_count[b] += w
    return nodes, edges, in_count, dropped


def topology_sha256(nodes, edges):
    lines = ["v1", str(len(nodes))]
    for p in sorted(nodes):
        lines.append(p)
    lines.append(str(len(edges)))
    for (a, b), w in sorted(edges.items()):
        lines.append(f"{a}\t{b}\t{int(w)}")
    payload = "\n".join(lines) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- Louvain


class LevelGraph:
    def __init__(self, node_ids, adj, loops, path_key, leaves):
        self.node_ids = list(node_ids)
        self.adj = adj
        self.loops = loops
        self.path_key = path_key
        self.leaves = leaves  # node -> list of leaf paths

    def degree(self, i):
        return sum(self.adj[i].values()) + self.loops[i]

    def two_m(self):
        return sum(self.degree(i) for i in self.node_ids)


def leaf_graph(nodes, edges):
    adj = {n: {} for n in nodes}
    loops = {n: 0.0 for n in nodes}
    path_key = {n: n for n in nodes}
    leaves = {n: [n] for n in nodes}
    for (a, b), w in edges.items():
        adj[a][b] = float(w)
        adj[b][a] = float(w)
    return LevelGraph(sorted(nodes), adj, loops, path_key, leaves)


def insert_delta_q(sigma_in, sigma_tot, k_i_in, k_i, two_m):
    if two_m <= 0:
        return 0.0
    sin_p = sigma_in / 2.0
    return (
        (sin_p + 2.0 * k_i_in) / two_m - ((sigma_tot + k_i) / two_m) ** 2
    ) - (
        sin_p / two_m - (sigma_tot / two_m) ** 2 - (k_i / two_m) ** 2
    )


def k_i_in(graph, i, members):
    total = 0.0
    adj_i = graph.adj[i]
    for j in members:
        if j == i:
            continue
        total += adj_i.get(j, 0.0)
    return total


def recompute_q(graph, comm, two_m):
    if two_m <= 0:
        return 0.0
    groups = defaultdict(list)
    for i in graph.node_ids:
        groups[comm[i]].append(i)
    q = 0.0
    for members in groups.values():
        member_set = set(members)
        sigma_tot = sum(graph.degree(u) for u in members)
        sigma_in = sum(graph.loops[u] for u in members)
        for u in members:
            for v, w in graph.adj[u].items():
                if v in member_set and v > u:
                    sigma_in += 2.0 * w
        q += sigma_in / two_m - (sigma_tot / two_m) ** 2
    return q


def phase1(graph, rng, trace=None):
    """局所移動。trace があれば net ΔQ と再計算 Q を記録する。"""
    nodes = sorted(graph.node_ids, key=lambda n: graph.path_key[n])
    comm = {i: i for i in graph.node_ids}
    members = {i: {i} for i in graph.node_ids}
    two_m = graph.two_m()
    k = {i: graph.degree(i) for i in graph.node_ids}
    sigma_tot = {i: k[i] for i in graph.node_ids}
    sigma_in = {i: graph.loops[i] for i in graph.node_ids}
    q = recompute_q(graph, comm, two_m)
    if trace is not None:
        trace.setdefault("q0", q)
        trace.setdefault("nets", [])
        trace.setdefault("adopted_moves", 0)
        trace.setdefault("stay_c0", 0)
        trace.setdefault("stay_nonpos", 0)

    def min_path(c):
        ms = members.get(c)
        if ms:
            return min(graph.path_key[n] for n in ms)
        return graph.path_key.get(c, str(c))

    moved_any_level = False
    while True:
        moves = 0
        for i in nodes:
            c0 = comm[i]
            k_in0 = k_i_in(graph, i, members[c0])
            st0 = sigma_tot[c0]
            si0 = sigma_in[c0]
            sigma_tot[c0] = st0 - k[i]
            sigma_in[c0] = si0 - 2.0 * k_in0 - graph.loops[i]
            members[c0].remove(i)

            neigh = {comm[j] for j in graph.adj[i]}
            neigh.add(c0)
            neigh = {c for c in neigh if c in members or c == c0}

            scored = []
            for c in neigh:
                d = insert_delta_q(sigma_in[c], sigma_tot[c], k_i_in(graph, i, members[c]), k[i], two_m)
                scored.append((c, d))
            insert0 = next(d for c, d in scored if c == c0)
            best = max(d for _, d in scored)
            tied = sorted((c for c, d in scored if d == best), key=min_path)
            if len(tied) == 1:
                c_star = tied[0]
            else:
                c_star = tied[int(rng() * len(tied)) % len(tied)]
            net = next(d for c, d in scored if c == c_star) - insert0
            raw_star = c_star
            stay = raw_star == c0 or net <= 0
            if stay:
                c_star = c0
                net = 0.0
                if trace is not None:
                    key = "stay_c0" if raw_star == c0 else "stay_nonpos"
                    trace[key] = trace.get(key, 0) + 1
            comm[i] = c_star
            members[c_star].add(i)
            sigma_tot[c_star] += k[i]
            sigma_in[c_star] += 2.0 * k_i_in(graph, i, members[c_star]) + graph.loops[i]
            if c_star != c0:
                moves += 1
                moved_any_level = True
                q += net
                if trace is not None:
                    trace["nets"].append(net)
                    trace["adopted_moves"] = trace.get("adopted_moves", 0) + 1
                    q_full = recompute_q(graph, comm, two_m)
                    trace.setdefault("q_pairs", []).append((q, q_full))
            if c0 != c_star and c0 in members and not members[c0]:
                del members[c0]
                sigma_tot.pop(c0, None)
                sigma_in.pop(c0, None)
        if moves == 0:
            break
    if trace is not None:
        trace["q_end"] = q
        trace["q_recomputed"] = recompute_q(graph, comm, two_m)
        trace["moved"] = bool(trace.get("moved") or moved_any_level)
    return comm, members, moved_any_level


def aggregate(graph, members):
    supers = []
    for c, ms in members.items():
        if not ms:
            continue
        sid = min(graph.path_key[n] for n in ms)
        supers.append((sid, c, ms))
    supers.sort(key=lambda x: x[0])
    ids = [s[0] for s in supers]
    adj = {sid: {} for sid in ids}
    loops = {sid: 0.0 for sid in ids}
    path_key = {sid: sid for sid in ids}
    leaves = {}
    node_to_super = {}
    for sid, _c, ms in supers:
        leaves[sid] = []
        for n in ms:
            leaves[sid].extend(graph.leaves[n])
            node_to_super[n] = sid
        leaves[sid].sort()
    for sid, _c, ms in supers:
        w_int = 0.0
        for n in ms:
            w_int += graph.loops[n] / 2.0
            for m, w in graph.adj[n].items():
                other = node_to_super[m]
                if other == sid:
                    if n < m:
                        w_int += w
                else:
                    # 片側のメンバーだけを辿るので、無向辺は 1 回だけ足る
                    adj[sid][other] = adj[sid].get(other, 0.0) + w
        loops[sid] = 2.0 * w_int
    return LevelGraph(ids, adj, loops, path_key, leaves)


def run_louvain(nodes, edges, seed=LOUVAIN_SEED, trace=None):
    if not nodes:
        return {}, trace
    rng = mulberry32(seed)
    graph = leaf_graph(nodes, edges)
    leaf_comm = {n: n for n in nodes}
    while True:
        comm, members, moved = phase1(graph, rng, trace=trace)
        n_before = len(graph.node_ids)
        n_after = sum(1 for ms in members.values() if ms)
        if n_after >= n_before or not moved:
            for n in graph.node_ids:
                for leaf in graph.leaves[n]:
                    leaf_comm[leaf] = comm[n]
            break
        for n in graph.node_ids:
            for leaf in graph.leaves[n]:
                leaf_comm[leaf] = comm[n]
        graph = aggregate(graph, members)
        if trace is not None:
            trace["aggregated"] = True
    return leaf_comm, trace


def relabel(leaf_comm):
    groups = defaultdict(list)
    for leaf, c in leaf_comm.items():
        groups[c].append(leaf)
    ordered = sorted(groups.values(), key=lambda ms: (-len(ms), min(ms)))
    mapping = {}
    for i, ms in enumerate(ordered):
        for leaf in ms:
            mapping[leaf] = i
    return mapping


def sort_members(paths, nodes, in_count):
    def key(p):
        meta = nodes[p]
        return (type_rank(meta["type"]), -in_count[p], p)
    return sorted(paths, key=key)


def cluster_label(paths, nodes, in_count):
    ranked = sort_members(paths, nodes, in_count)
    titles = [nodes[p]["title"] for p in ranked[:2]]
    return " / ".join(titles) if titles else paths[0]


def assemble(catalog, nodes, edges, in_count, dropped, leaf_ids):
    groups = defaultdict(list)
    for p, cid in leaf_ids.items():
        groups[cid].append(p)
    clusters = []
    membership = {}
    for cid in sorted(groups):
        members = groups[cid]
        ranked = sort_members(members, nodes, in_count)
        label = cluster_label(members, nodes, in_count)
        top = []
        for p in ranked[:5]:
            top.append({
                "path": p,
                "title": nodes[p]["title"],
                "in_count": in_count[p],
                "type": nodes[p]["type"],
            })
        clusters.append({
            "id": cid,
            "label": label,
            "size": len(members),
            "top_members": top,
        })
        for p in members:
            membership[p] = {
                "id": cid,
                "type": nodes[p]["type"],
                "title": nodes[p]["title"],
                "in_count": in_count[p],
            }
    clusters.sort(key=lambda c: (-c["size"], c["id"]))
    dropped_out = {}
    for rel, reason in dropped.items():
        meta = catalog[rel]
        dropped_out[rel] = {
            "reason": reason,
            "type": meta["type"],
            "title": meta["title"],
        }
    payload = {
        "version": 1,
        "built_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "recipe": RECIPE,
        "seed": LOUVAIN_SEED,
        "opts": {"include_seed_entities": False, "drop_isolated": True},
        "topology_sha256": topology_sha256(nodes, edges),
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "clusters": len(clusters),
            "dropped_seed_entities": sum(1 for r in dropped.values() if r == "seed"),
            "dropped_isolated": sum(1 for r in dropped.values() if r == "isolated"),
        },
        "clusters": clusters,
        "membership": membership,
        "catalog": {rel: {"name": m["name"], "type": m["type"], "title": m["title"], "status": m["status"]}
                    for rel, m in catalog.items()},
        "dropped": dropped_out,
    }
    return payload


def build_payload(vault=None, trace=None):
    catalog, _by_name, directed = scan_vault(vault)
    nodes, edges, in_count, dropped = build_backbone(catalog, directed)
    raw, _ = run_louvain(nodes, edges, seed=LOUVAIN_SEED, trace=trace)
    leaf_ids = relabel(raw) if raw else {}
    return assemble(catalog, nodes, edges, in_count, dropped, leaf_ids)


def load_clusters(path=None):
    p = path or (VAULT_ROOT / CLUSTERS_REL)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def existing_wiki_page(raw, vault=None):
    """4 ディレクトリに実在するファイルへ正規化する。無ければ None。"""
    root = Path(vault or VAULT_ROOT).resolve()
    s = raw.strip()
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2].split("|")[0].strip()
    if s.startswith("wiki/") and s.endswith(".md"):
        path = (root / s).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = None
        else:
            parts = rel.split("/")
            if len(parts) == 3 and parts[0] == "wiki" and parts[1] in SCAN_DIRS and path.is_file():
                return rel
    stem = s[:-3] if s.endswith(".md") else s
    stem = stem.rsplit("/", 1)[-1]
    for sub in SCAN_DIRS:
        path = root / "wiki" / sub / f"{stem}.md"
        if path.is_file():
            return path.resolve().relative_to(root).as_posix()
    return None


def normalize_page(raw, payload, vault=None):
    s = raw.strip()
    catalog = payload.get("catalog") or {}
    if s in catalog:
        return s
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2].split("|")[0].strip()
    if s in catalog:
        return s
    stem = s[:-3] if s.endswith(".md") else s
    stem = stem.rsplit("/", 1)[-1]
    for rel, meta in catalog.items():
        if meta.get("name") == stem:
            return rel
    return existing_wiki_page(raw, vault)


def resolve_live_page(raw, payload, vault=None):
    """4 ディレクトリに今あるファイルへ正規化する。キャッシュにだけ残る死んだパスは捨てる。"""
    root = Path(vault or VAULT_ROOT).resolve()
    live = existing_wiki_page(raw, vault)
    if live:
        return live
    cand = normalize_page(raw, payload, vault)
    if cand and (root / cand).is_file():
        return cand
    return None


def member_sort_key(rec, path):
    return (type_rank(rec.get("type")), -rec.get("in_count", 0), path)


# --------------------------------------------------------------------------- CLI


def cmd_build(_args):
    try:
        payload = build_payload()
        atomic_write(VAULT_ROOT / CLUSTERS_REL, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        log(f"ERR: build failed: {exc}")
        return EXIT_FAIL
    log(
        f"wiki-clusters: {payload['stats']['nodes']} nodes, "
        f"{payload['stats']['clusters']} clusters → {CLUSTERS_REL}"
    )
    print(json.dumps({"ok": True, "clusters": CLUSTERS_REL, **payload["stats"]}, ensure_ascii=False))
    return EXIT_OK


def require_payload():
    payload = load_clusters()
    if payload is None:
        log(f"ERR: no clusters at {CLUSTERS_REL}; run `python3 scripts/wiki-clusters.py build`")
        return None
    return payload


def cmd_list(_args):
    payload = require_payload()
    if payload is None:
        return EXIT_MISSING
    print(json.dumps(payload.get("clusters") or [], ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_stats(_args):
    payload = require_payload()
    if payload is None:
        return EXIT_MISSING
    out = dict(payload.get("stats") or {})
    out["topology_sha256"] = payload.get("topology_sha256")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_lookup(args):
    payload = require_payload()
    if payload is None:
        return EXIT_MISSING
    rel = resolve_live_page(args.page, payload)
    if rel is None:
        log(f"ERR: not a wiki page in sources/entities/concepts/questions: {args.page}")
        return EXIT_MISSING
    rec = (payload.get("membership") or {}).get(rel)
    if rec:
        cluster = next((c for c in payload.get("clusters") or [] if c["id"] == rec["id"]), None)
        print(json.dumps({
            "page": rel,
            "on_backbone": True,
            "id": rec["id"],
            "label": cluster["label"] if cluster else "",
            "type": rec["type"],
            "title": rec["title"],
            "in_count": rec["in_count"],
            "topology_sha256": payload.get("topology_sha256"),
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    dropped = (payload.get("dropped") or {}).get(rel) or {}
    reason = dropped.get("reason")
    if reason is None:
        meta = (payload.get("catalog") or {}).get(rel) or {}
        if meta.get("type") == "entity" and meta.get("status") == "seed":
            reason = "seed"
        else:
            reason = "isolated"
    print(json.dumps({
        "page": rel,
        "on_backbone": False,
        "reason": reason,
        "type": dropped.get("type") or (payload.get("catalog") or {}).get(rel, {}).get("type"),
        "title": dropped.get("title") or (payload.get("catalog") or {}).get(rel, {}).get("title"),
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_members(args):
    payload = require_payload()
    if payload is None:
        return EXIT_MISSING
    cid = args.id
    membership = payload.get("membership") or {}
    rows = [(p, rec) for p, rec in membership.items() if rec.get("id") == cid]
    if not rows and not any(c["id"] == cid for c in payload.get("clusters") or []):
        log(f"ERR: unknown cluster id: {cid}")
        return EXIT_MISSING
    if args.type:
        rows = [(p, rec) for p, rec in rows if rec.get("type") == args.type]
    rows.sort(key=lambda item: member_sort_key(item[1], item[0]))
    if args.top != 0:
        rows = rows[: args.top]
    out = [{"path": p, **rec} for p, rec in rows]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_assign(args):
    payload = require_payload()
    if payload is None:
        return EXIT_MISSING
    path = Path(args.pages)
    if not path.is_file():
        log(f"ERR: --pages file not found: {args.pages}")
        return EXIT_USAGE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log(f"ERR: cannot read --pages: {exc}")
        return EXIT_USAGE
    membership = payload.get("membership") or {}
    clusters_meta = {c["id"]: c for c in payload.get("clusters") or []}
    grouped = defaultdict(list)
    unassigned = []
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        rel = normalize_page(raw, payload)
        if rel is None or rel not in membership:
            unassigned.append(raw)
            continue
        grouped[membership[rel]["id"]].append(rel)
    assigned = []
    for cid in sorted(grouped, key=lambda i: (-len(grouped[i]), i)):
        meta = clusters_meta.get(cid) or {"label": "", "id": cid}
        assigned.append({
            "id": cid,
            "label": meta.get("label", ""),
            "size_in_population": len(grouped[cid]),
            "paths": grouped[cid],
        })
    print(json.dumps({"clusters": assigned, "unassigned": unassigned}, ensure_ascii=False, indent=2))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="スキル世界のテーマ塊。in_count は無向次数重み。JSON を Read せず CLI を使え。"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("list")
    sub.add_parser("stats")
    p = sub.add_parser("lookup")
    p.add_argument("page")
    p = sub.add_parser("members")
    p.add_argument("id", type=int)
    p.add_argument("--top", type=int, default=20, help="0 で全件(スキルは使うな)。既定 20")
    p.add_argument("--type", default=None, help="concept 等で絞る")
    p = sub.add_parser(
        "assign",
        help="母集団パスを塊へ写す。wiki-survey は呼ぶな。",
        description="母集団パスを塊へ写す。試験と将来フックである。wiki-survey は呼ぶな。",
    )
    p.add_argument("--pages", required=True, help="1 行 1 パス")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "stats":
        return cmd_stats(args)
    if args.cmd == "lookup":
        return cmd_lookup(args)
    if args.cmd == "members":
        if args.top < 0:
            log("ERR: --top は 0 以上")
            return EXIT_USAGE
        return cmd_members(args)
    if args.cmd == "assign":
        return cmd_assign(args)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
