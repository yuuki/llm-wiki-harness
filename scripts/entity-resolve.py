#!/usr/bin/env python3
"""entity-resolve.py — entity ページの表記ゆれ(同一人物・同一組織の別ページ)を検出し、統合の提案を出す。

読み取り専用。wiki ページは書き換えない。統合そのものは wiki-refactor の「統合」手順
(残す側に旧題名を aliases として足し、wikilink を張り替え、参照が無くなってから削除)で人間が
承認して行う。本スクリプトは (1) 候補対の検出、(2) 判断の台帳、(3) 統合前の影響範囲の列挙を担う。

使い方:
  python3 scripts/entity-resolve.py scan [--type person|organization|...] [--min-tier weak|medium|strong] [--limit N]
      候補対を JSON で出す。台帳で rejected / merged 済みの対は出さない(--all で出す)。
  python3 scripts/entity-resolve.py report [--limit N]
      lint 用 markdown(`## Entity Aliases`)。tier 別件数と、strong から順に上位 N 対。
  python3 scripts/entity-resolve.py plan --keep <題名> --drop <題名>
      統合の影響範囲: drop 側へリンクする wiki ページ、keep 側へ足す aliases、related の和。読み取り専用。
  python3 scripts/entity-resolve.py decide --keep <題名> --drop <題名> (--merged | --rejected) [--reason "..."]
      台帳 `.vault-meta/entity-merges.json`(git 追跡)に判断を記す。rejected は以後の scan / report に出ない。
      merged は drop 側のページがまだ在れば「統合が終わっていない」と report に出る。
  python3 scripts/entity-resolve.py ledger
      台帳の一覧。

検出の階層(tier):
  strong  正規化キー(NFKC、casefold、空白と記号の除去、法人格接尾辞と冠詞の除去)が一致する。
          または一方の alias が他方の題名か alias に(正規化して)一致する。
  medium  person: 姓が一致し、名が「イニシャル対フルネーム」か「ミドルネームの有無」か「姓名の順序」だけ違う。
          organization: 法人格を除いた語の集合が一致する(語順だけ違う)。「University of X」と「X University」。
          括弧内の略称が他方の題名に一致する(`Massachusetts Institute of Technology (MIT)` と `MIT`)。
  weak    正規化キーの類似度(difflib)が 0.92 以上で長さ 8 以上(綴りゆれ、全角半角、ハイフンの有無)。

entity_type が両方あって異なる対(person と organization など)は出さない。
候補は同じ tier でも「別人の同姓同名」でありうる。台帳に rejected を記して黙らせる。

終了コード: 0 成功 / 2 使い方の誤り / 3 ページが無い
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wiki_lock import page_lock  # noqa: E402

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()
ENTITIES_DIR = "wiki/entities"
LEDGER_REL = ".vault-meta/entity-merges.json"
GRAPH_REL = ".vault-meta/graph.json"

EXIT_OK, EXIT_USAGE, EXIT_MISSING = 0, 2, 3
TIER_RANK = {"strong": 3, "medium": 2, "weak": 1}

LEGAL_SUFFIXES = ("inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "gmbh", "ag",
                  "plc", "sa", "kk", "株式会社", "有限会社", "合同会社")
ARTICLES = ("the",)
FM_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
PAREN_ACRONYM_RE = re.compile(r"^(.*?)\s*[\(（]([A-Z0-9&.\-]{2,12})[\)）]\s*$")  # 大文字略称だけ(`(Amazon)` は所属の注記)
INITIAL_RE = re.compile(r"^[A-Za-z]\.?$")


def log(msg):
    print(msg, file=sys.stderr)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as fh:
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


# --------------------------------------------------------------------------- 正規化

def unquote(v):
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def fold(s):
    """NFKC + casefold + 空白と記号を除く。日本語の漢字かなはそのまま。"""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+", "", s)


def tokens(s, keep_hyphen=False):
    """語の列(NFKC + casefold、記号は区切り)。ピリオド付きイニシャルは `j.` のまま残す。
    人名では `I-Ting` のようなハイフン結合の名を 1 語に保つ(keep_hyphen)。"""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = re.sub(r"[,;/&()（）\[\]\"'`]+", " ", s)
    sep = r"[\s_]+" if keep_hyphen else r"[\s\-_]+"
    return [t for t in re.split(sep, s) if t]


def strip_org_noise(toks):
    toks = [t.rstrip(".") for t in toks]
    toks = [t for t in toks if t not in ARTICLES]
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks = toks[:-1]
    return toks


def norm_key(title, entity_type):
    toks = tokens(title)
    if entity_type != "person":
        toks = strip_org_noise(toks)
    return "".join(fold(t) for t in toks)


def person_shape(title):
    """(姓, 名の列) を返す。`Last, First` は並べ替える。1 語なら (語, [])。"""
    raw = unicodedata.normalize("NFKC", title or "").strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        toks = tokens(first, keep_hyphen=True) + tokens(last, keep_hyphen=True)
    else:
        toks = tokens(raw, keep_hyphen=True)
    toks = [t for t in toks if t not in ("jr", "jr.", "sr", "phd", "dr", "prof")]
    if not toks:
        return None, []
    return toks[-1].rstrip("."), [t.rstrip(".") for t in toks[:-1]]


def names_compatible(first_a, first_b):
    """名の列が「イニシャル対フル」「ミドルの有無」の範囲で一致するか。"""
    if not first_a or not first_b:
        return False  # 姓だけのページは判断材料が無い
    if first_a == first_b:
        return True
    a0, b0 = first_a[0], first_b[0]
    initial_match = a0[:1] == b0[:1] and (len(a0) == 1 or len(b0) == 1 or a0 == b0)
    if not initial_match:
        return False
    # 残り(ミドル)は片方が空か、先頭文字が合えばよい
    rest_a, rest_b = first_a[1:], first_b[1:]
    if not rest_a or not rest_b:
        return True
    return all(x[:1] == y[:1] for x, y in zip(rest_a, rest_b))


def identifying_aliases(e):
    """本人を特定できる alias だけ返す。題名の一部(姓だけ、名だけ、`IBM` ⊂ `IBM Research`)は除く。"""
    ft = fold(e["title"])
    out = []
    for a in e["aliases"]:
        fa = fold(a)
        if not fa or len(fa) < 3:
            continue
        if fa != ft and fa in ft:
            continue  # 部分名(姓だけなど)は同姓の別人を全部つないでしまう
        toks = tokens(a, keep_hyphen=True)
        if e["entity_type"] in (None, "person") and len(toks) >= 2 and INITIAL_RE.match(toks[0]):
            continue  # `Y. Li` 型の引用表記は同じ頭文字の別人と衝突する
        out.append(a)
    return out


def org_token_set(title):
    toks = strip_org_noise(tokens(title))
    toks = [t for t in toks if t not in ("of", "for", "and", "de", "der", "the")]
    return frozenset(fold(t) for t in toks if fold(t))


# --------------------------------------------------------------------------- ページ走査

def split_frontmatter(text):
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def parse_fm(fm_lines):
    fm, key = {}, None
    for line in fm_lines:
        if line.startswith("  - ") and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(unquote(line[4:]))
            continue
        m = FM_LINE_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                fm[key] = [unquote(x) for x in val[1:-1].split(",") if x.strip()]
            else:
                fm[key] = unquote(val) if val else []
    return fm


def load_entities():
    root = VAULT_ROOT / ENTITIES_DIR
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            head = path.read_text(encoding="utf-8")[:6000]
        except (OSError, UnicodeDecodeError):
            continue
        fm_lines, _ = split_frontmatter(head)
        fm = parse_fm(fm_lines) if fm_lines else {}
        title = fm.get("title") if isinstance(fm.get("title"), str) and fm.get("title") else path.stem
        aliases = [a for a in (fm.get("aliases") if isinstance(fm.get("aliases"), list) else []) if a]
        etype = fm.get("entity_type") if isinstance(fm.get("entity_type"), str) else None
        if etype is None:
            tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
            for t in ("person", "organization", "product", "repository", "dataset", "book", "thesis", "survey", "place"):
                if t in tags:
                    etype = t
                    break
        out.append({
            "path": f"{ENTITIES_DIR}/{path.name}", "stem": path.stem, "title": title,
            "aliases": aliases, "entity_type": etype,
            "address": fm.get("address") if isinstance(fm.get("address"), str) else None,
            "tier": fm.get("entity_tier") if isinstance(fm.get("entity_tier"), str) else None,
            "related": fm.get("related") if isinstance(fm.get("related"), list) else [],
        })
    return out


def load_degrees():
    path = VAULT_ROOT / GRAPH_REL
    if not path.is_file():
        return {}
    try:
        g = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {page: len(nbrs) for page, nbrs in (g.get("adj") or {}).items()}


# --------------------------------------------------------------------------- 候補検出

def pair_key(a, b):
    return " || ".join(sorted([a, b]))


def compatible_types(ea, eb):
    ta, tb = ea["entity_type"], eb["entity_type"]
    return ta is None or tb is None or ta == tb


def find_candidates(entities):
    """{pair_key: {"a", "b", "tier", "reasons": [...]}}"""
    found = {}

    def add(ea, eb, tier, reason):
        if ea["path"] == eb["path"] or not compatible_types(ea, eb):
            return
        k = pair_key(ea["stem"], eb["stem"])
        cur = found.get(k)
        if cur is None:
            found[k] = {"a": ea, "b": eb, "tier": tier, "reasons": [reason]}
        else:
            if TIER_RANK[tier] > TIER_RANK[cur["tier"]]:
                cur["tier"] = tier
            if reason not in cur["reasons"]:
                cur["reasons"].append(reason)

    # strong: 正規化キー一致、alias 一致
    by_key = defaultdict(list)
    for e in entities:
        k = norm_key(e["title"], e["entity_type"])
        if k:
            by_key[k].append(e)
    for k, group in by_key.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                add(group[i], group[j], "strong", "正規化した題名が一致")

    name_index = defaultdict(list)  # fold(name) -> entities that own it as title or alias
    for e in entities:
        ft = fold(e["title"])
        if ft:
            name_index[ft].append(e)
        for n in identifying_aliases(e):
            name_index[fold(n)].append(e)
    for f, group in name_index.items():
        uniq = list({e["path"]: e for e in group}.values())
        if len(uniq) < 2 or len(f) < 3:
            continue
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                ea, eb = uniq[i], uniq[j]
                if fold(ea["title"]) == f or fold(eb["title"]) == f:
                    add(ea, eb, "strong", f"一方の題名 `{f}` が他方の aliases に入っている(alias の衝突。統合するか alias を外す)")
                elif len(f) <= 4:
                    # 短い略称(KIT、CSU、UVA)は別組織が同じ略称を名乗ることが多い。統合ではなく alias の整理
                    add(ea, eb, "medium", f"短い略称 `{f}` を両方が aliases に持つ(別組織なら片方の alias を外す)")
                else:
                    add(ea, eb, "strong", f"同じ alias `{f}` を両方が持つ")

    # medium: person の姓ブロック
    by_last = defaultdict(list)
    for e in entities:
        if e["entity_type"] not in (None, "person"):
            continue
        last, first = person_shape(e["title"])
        if last and first:
            by_last[fold(last)].append((e, [fold(x) for x in first]))
    for last, group in by_last.items():
        if len(group) < 2 or len(group) > 60:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                (ea, fa), (eb, fb) = group[i], group[j]
                if fa == fb:
                    continue  # strong で拾っている
                if names_compatible(fa, fb):
                    add(ea, eb, "medium", "姓が一致し、名がイニシャルかミドルネームの有無だけ違う")
    # medium: 姓名の順序(2 語の名前で語集合が一致)
    by_set = defaultdict(list)
    for e in entities:
        if e["entity_type"] not in (None, "person"):
            continue
        toks = [fold(t) for t in tokens(e["title"])]
        toks = [t for t in toks if t]
        if len(toks) == 2:
            by_set[frozenset(toks)].append(e)
    for s, group in by_set.items():
        if len(group) >= 2:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    if group[i]["title"] != group[j]["title"]:
                        add(group[i], group[j], "medium", "同じ 2 語を姓名の順序だけ違えて持つ")

    # medium: organization の語集合一致、括弧略称
    by_orgset = defaultdict(list)
    acronym_owner = {}
    for e in entities:
        if e["entity_type"] in ("person",):
            continue
        s = org_token_set(e["title"])
        if len(s) >= 2:
            by_orgset[s].append(e)
        m = PAREN_ACRONYM_RE.match(e["title"])
        if m:
            acronym_owner.setdefault(fold(m.group(2)), []).append(e)
    for s, group in by_orgset.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                add(group[i], group[j], "medium", "法人格と前置詞を除いた語の集合が一致(語順だけ違う)")
    for acr, owners in acronym_owner.items():
        for other in name_index.get(acr, []):
            for owner in owners:
                add(owner, other, "medium", f"括弧内の略称 `{acr}` が他方の名前に一致")

    # weak: 近い正規化キー(ブロック: 先頭 2 文字)
    blocks = defaultdict(list)
    for e in entities:
        k = norm_key(e["title"], e["entity_type"])
        if len(k) >= 8:
            blocks[k[:2]].append((e, k))
    for prefix, group in blocks.items():
        if len(group) > 400:
            continue
        for i in range(len(group)):
            ea, ka = group[i]
            for j in range(i + 1, len(group)):
                eb, kb = group[j]
                if ka == kb or abs(len(ka) - len(kb)) > 3:
                    continue
                ratio = difflib.SequenceMatcher(None, ka, kb).ratio()
                if ratio >= 0.92:
                    add(ea, eb, "weak", f"正規化キーの類似度 {ratio:.2f}")
    return found


# --------------------------------------------------------------------------- 台帳

def ledger_path():
    return VAULT_ROOT / LEDGER_REL


def load_ledger():
    p = ledger_path()
    if not p.is_file():
        return {"version": 1, "decisions": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "decisions": {}}


def save_ledger(data):
    with page_lock(LEDGER_REL):
        atomic_write(ledger_path(), json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- 出力

def public_pair(k, c, degrees):
    def side(e):
        return {"title": e["title"], "path": e["path"], "entity_type": e["entity_type"], "address": e["address"],
                "aliases": e["aliases"], "degree": degrees.get(e["path"]), "entity_tier": e["tier"]}
    a, b = c["a"], c["b"]
    # 残す側の目安: 次数が多い方(同じなら aliases が多い方、次に題名が短い方)
    def rank(e):
        return (degrees.get(e["path"]) or 0, len(e["aliases"]), -len(e["title"]))
    keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
    return {"key": k, "tier": c["tier"], "reasons": c["reasons"], "a": side(a), "b": side(b),
            "suggest_keep": keep["title"], "suggest_drop": drop["title"]}


def sorted_pairs(found, degrees, ledger, include_all=False, min_tier="weak", etype=None):
    out = []
    decisions = ledger.get("decisions", {})
    for k, c in found.items():
        if TIER_RANK[c["tier"]] < TIER_RANK[min_tier]:
            continue
        if etype and etype not in (c["a"]["entity_type"], c["b"]["entity_type"]):
            continue
        d = decisions.get(k)
        if d and not include_all:
            continue
        p = public_pair(k, c, degrees)
        if d:
            p["decision"] = d
        out.append(p)
    out.sort(key=lambda p: (-TIER_RANK[p["tier"]], -((p["a"]["degree"] or 0) + (p["b"]["degree"] or 0)), p["key"]))
    return out


def cmd_scan(args):
    entities = load_entities()
    if not entities:
        log("entity ページが無い")
        return EXIT_MISSING
    found = find_candidates(entities)
    pairs = sorted_pairs(found, load_degrees(), load_ledger(), args.all, args.min_tier, args.type)
    if args.limit:
        pairs = pairs[: args.limit]
    print(json.dumps({"entities": len(entities), "pairs": len(pairs), "items": pairs}, ensure_ascii=False, indent=2))
    return EXIT_OK


def unfinished_merges(ledger, entities):
    stems = {e["stem"] for e in entities}
    out = []
    for k, d in ledger.get("decisions", {}).items():
        if d.get("state") == "merged" and d.get("drop") in stems:
            out.append((k, d))
    return out


def render_report(entities, found, degrees, ledger, limit):
    pairs = sorted_pairs(found, degrees, ledger)
    by_tier = defaultdict(int)
    for p in pairs:
        by_tier[p["tier"]] += 1
    decisions = ledger.get("decisions", {})
    n_rej = sum(1 for d in decisions.values() if d.get("state") == "rejected")
    n_mer = sum(1 for d in decisions.values() if d.get("state") == "merged")
    unfinished = unfinished_merges(ledger, entities)
    out = ["## Entity Aliases", ""]
    out.append(f"- entity {len(entities)} 頁。統合候補 {len(pairs)} 対(strong {by_tier['strong']}、medium {by_tier['medium']}、weak {by_tier['weak']})。"
               f"台帳: rejected {n_rej}、merged {n_mer}")
    out.append("- 統合は wiki-refactor の「統合」手順で人間承認のうえ行う(残す側に aliases、リンク張り替え、参照ゼロで削除)。"
               "別人の同姓同名は `entity-resolve.py decide --rejected` で黙らせる")
    out.append("")
    if unfinished:
        out.append("### 統合が終わっていない(台帳は merged だが drop 側のページが残っている)")
        out.append("")
        for k, d in unfinished:
            out.append(f"- [[{d.get('keep')}]] ← [[{d.get('drop')}]]({d.get('day')})")
        out.append("")
    if pairs:
        out.append(f"### 候補(上位 {min(limit, len(pairs))} 対、strong から)")
        out.append("")
        for p in pairs[:limit]:
            da, db = p["a"]["degree"], p["b"]["degree"]
            deg = "" if da is None and db is None else f"(次数 {da or 0} / {db or 0})"
            out.append(f"- **{p['tier']}** [[{p['a']['title']}]] と [[{p['b']['title']}]]{deg}: {'。'.join(p['reasons'])}。残す候補 [[{p['suggest_keep']}]]")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cmd_report(args):
    entities = load_entities()
    found = find_candidates(entities)
    sys.stdout.write(render_report(entities, found, load_degrees(), load_ledger(), args.limit))
    return EXIT_OK


def find_entity(entities, name):
    f = fold(name)
    for e in entities:
        if e["stem"] == name or e["title"] == name:
            return e
    for e in entities:
        if fold(e["title"]) == f or fold(e["stem"]) == f:
            return e
    return None


def rg_links_to(stem):
    """`[[stem` を含む wiki ページ(相対パス)。rg が無ければ空。"""
    try:
        out = subprocess.run(["rg", "-l", "-F", f"[[{stem}", "wiki"], cwd=str(VAULT_ROOT),
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(l for l in out.splitlines() if l and not l.endswith("/_index.md") and not l.startswith(f"wiki/entities/{stem}.md"))


def cmd_plan(args):
    entities = load_entities()
    keep, drop = find_entity(entities, args.keep), find_entity(entities, args.drop)
    if not keep or not drop:
        log("keep か drop のページが無い")
        return EXIT_MISSING
    linkers = rg_links_to(drop["stem"])
    new_aliases = [n for n in [drop["title"]] + drop["aliases"] if fold(n) not in {fold(x) for x in [keep["title"]] + keep["aliases"]}]
    new_related = [r for r in drop["related"] if r not in keep["related"] and fold(r.strip("[]")) != fold(keep["title"])]
    plan = {
        "keep": {"title": keep["title"], "path": keep["path"], "address": keep["address"]},
        "drop": {"title": drop["title"], "path": drop["path"], "address": drop["address"]},
        "aliases_to_add": new_aliases,
        "related_to_add": new_related,
        "pages_linking_to_drop": linkers,
        "steps": [
            f"1. `wiki-append.py` 相当で [[{keep['title']}]] の aliases に {new_aliases} を足す(lock 下)",
            f"2. 上の {len(linkers)} 頁で `[[{drop['stem']}` を `[[{keep['stem']}` に張り替える(表示名が要るなら `[[{keep['stem']}|{drop['title']}]]`)",
            f"3. drop 側本文の独自情報(role、first_mentioned、出典)を keep 側に移す",
            f"4. `rg -F '[[{drop['stem']}' wiki` が 0 になってから drop を `git rm`",
            f"5. `entity-resolve.py decide --keep \"{keep['title']}\" --drop \"{drop['title']}\" --merged` と log エントリ、commit `wiki: merge entity {drop['title']} into {keep['title']}`",
        ],
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_decide(args):
    entities = load_entities()
    keep, drop = find_entity(entities, args.keep), find_entity(entities, args.drop)
    keep_stem = keep["stem"] if keep else args.keep
    drop_stem = drop["stem"] if drop else args.drop
    if not keep and not drop:
        log("keep も drop も見つからない(統合後なら drop は無くてよいが keep は要る)")
        return EXIT_MISSING
    state = "merged" if args.merged else "rejected"
    data = load_ledger()
    data.setdefault("decisions", {})[pair_key(keep_stem, drop_stem)] = {
        "state": state, "keep": keep_stem, "drop": drop_stem,
        "reason": args.reason or "", "day": date.today().isoformat(),
    }
    save_ledger(data)
    print(json.dumps({"ok": True, "state": state, "keep": keep_stem, "drop": drop_stem}, ensure_ascii=False))
    return EXIT_OK


def cmd_ledger(args):
    data = load_ledger()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(description="entity の表記ゆれ検出と統合提案(読み取り専用)。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--type", default=None)
    p.add_argument("--min-tier", choices=("weak", "medium", "strong"), default="weak")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--all", action="store_true", help="台帳で判断済みの対も出す")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("report")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("plan")
    p.add_argument("--keep", required=True)
    p.add_argument("--drop", required=True)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("decide")
    p.add_argument("--keep", required=True)
    p.add_argument("--drop", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--merged", action="store_true")
    g.add_argument("--rejected", action="store_true")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("ledger")
    p.set_defaults(func=cmd_ledger)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
