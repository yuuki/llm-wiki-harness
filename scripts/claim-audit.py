#!/usr/bin/env python3
"""claim-audit.py — 命題の再検証(adversary)のためのパケット生成と結果の台帳。

concept ページの主題節にある太字命題は、再編纂で観察を畳んだ結果であり、畳む過程で
出典に無い一般化や数値のずれが入り得る。本スクリプトは命題を標本抽出し、命題が引く
source ページ(と、あれば `.raw/` の抽出テキスト)から語彙重なりの高い段落を切り出して
「命題 + 根拠候補」のパケットにする。判定(支持 / 不支持 / 出典に無い)は LLM か人間が行い、
`record` で台帳と報告書に残す。本スクリプトは wiki の本文ページを書かない。

使い方:
  python3 scripts/claim-audit.py sample --pages wiki/concepts/X.md [...] [--per-page 5]
  python3 scripts/claim-audit.py sample --recompiled [--limit 5] [--per-page 3]
  python3 scripts/claim-audit.py sample --random 10 [--seed 7] [--per-page 2]
      命題を選び、根拠候補の抜粋を添えた JSON を stdout に出す。台帳に既に判定のある命題は
      --reaudit を付けない限り除く。--include-inbox で受信箱の観察も対象にする。
  python3 scripts/claim-audit.py record --verdicts /tmp/verdicts.json [--by human|agent]
      判定 JSON([{"id","verdict","note"}])を台帳 `.vault-meta/claim-audit.json` に追記し、
      `wiki/meta/claim-audit-YYYY-MM-DD.md` を当日分の台帳から再描画する。
  python3 scripts/claim-audit.py resolve <id> [--note TEXT]
      不支持・出典に無いと判定した命題に対処した(留保を付けた / 出典を直した)ことを台帳に記す。
  python3 scripts/claim-audit.py report
      台帳の集計を markdown で stdout に出す(lint 用)。
  python3 scripts/claim-audit.py numbers --pages wiki/concepts/X.md [...] [--include-inbox]
  python3 scripts/claim-audit.py numbers --spec spec.json
  python3 scripts/claim-audit.py numbers --source-page "wiki/sources/@....md"
      数値が出典にあるかを機械的に照合する。--pages は命題行と根拠・反証行(根拠行はその行が引く出典)、
      --spec は wiki-append の batch spec の観察と問い(追記の前に使う)、--source-page は source ページ本文を
      自分の raw 抽出テキストに照らす。missing はページにも raw にも無い数値、raw_only はページに無く
      raw にだけある数値(出典に無いと誤判定しないための印)。どちらも判定ではなく要確認の一覧である。
      missing があれば終了 1。sample のパケットにも同じ照合結果が `numbers` として付く。

判定語:
  supported       出典の記述が命題を支持する
  unsupported     出典の記述が命題と食い違う(数値・条件・向き)
  not_in_source   出典にその内容が無い(命題が正しくても引用が誤り。wiki 側の解釈・対応づけも含む)
  source_missing  引かれた source ページが存在しない
  unclear         抜粋では判定できない(source ページを excerpt して再判定する)

evidence の status:
  ok / stub(lint が作った source stub か本文 120 字未満。wiki からは検証できないので raw か原典を見る)
  / source_missing / unreadable

環境変数 WIKI_VAULT_ROOT で vault ルートを差し替えられる(試験用)。

終了コード:
  0 — 成功
  2 — 使い方の誤り
  3 — 入力ファイルが無い
"""

import argparse
import importlib.util
import json
import math
import os
import random
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from wiki_lock import page_lock
from wiki_claims import evidence_names, extract_claims, source_names

VAULT_ROOT = Path(os.environ.get("WIKI_VAULT_ROOT") or Path(__file__).resolve().parent.parent).resolve()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 3

LEDGER_REL = ".vault-meta/claim-audit.json"
REPORT_DIR_REL = "wiki/meta"
VERDICTS = ("supported", "unsupported", "not_in_source", "source_missing", "unclear")

RAW_TEXT_SUFFIXES = (".txt", ".md")
EXCERPT_CHARS = 420
TOP_PARAGRAPHS = 3


def log(msg):
    print(msg, file=sys.stderr)


def load_stats_module():
    spec = importlib.util.spec_from_file_location("wiki_concept_stats", _SCRIPTS / "wiki-concept-stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STATS = load_stats_module()


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


def locked_ledger(fn):
    """読み込みから保存までを台帳のロックで囲む。並行 record の更新喪失を防ぐ。"""
    def wrapper(*args, **kwargs):
        with page_lock(LEDGER_REL):
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def rel_posix(path):
    return Path(path).resolve().relative_to(VAULT_ROOT.resolve()).as_posix()


# --------------------------------------------------------------------------- 根拠の抜粋

def tokens(text):
    """和文は文字 2-gram、英数字は小文字の語。記号・空白は捨てる。"""
    out = set()
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-+%]*", text):
        out.add(word.lower())
    cjk = re.sub(r"[^\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]", " ", text)
    for run in cjk.split():
        for k in range(len(run) - 1):
            out.add(run[k:k + 2])
    return out


def looks_like_table_dump(s):
    """PDF 抽出テキストの表・図キャプション断片(数字と短い断片ばかり)を捨てる。"""
    toks = s.split()
    if len(toks) < 6:
        return True
    numeric = sum(1 for t in toks if re.fullmatch(r"[\d.,%±()\-–]+", t))
    return numeric / len(toks) > 0.35


def paragraphs(text):
    paras = []
    for chunk in re.split(r"\n\s*\n", text):
        s = re.sub(r"\s+", " ", " ".join(line.strip() for line in chunk.splitlines() if line.strip())).strip()
        if len(s) >= 20 and not looks_like_table_dump(s):
            paras.append(s)
    return paras


def bullet_aware_paragraphs(body):
    """wiki ページは箇条書き 1 行が 1 主張なので、箇条書きは行単位、散文は段落単位。"""
    out = []
    for chunk in re.split(r"\n\s*\n", body):
        lines = [l for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue
        if all(re.match(r"^\s*[-*] ", l) or re.match(r"^\s*>", l) for l in lines):
            for l in lines:
                s = re.sub(r"^\s*[-*>]\s*", "", l).strip()
                if len(s) >= 20:
                    out.append(s)
        else:
            s = " ".join(l.strip() for l in lines)
            if len(s) >= 20 and not s.startswith("#"):
                out.append(s)
    return out


def rank_paragraphs(claim, paras, top=TOP_PARAGRAPHS):
    ct = tokens(claim)
    if not ct:
        return []
    scored = []
    for p in paras:
        pt = tokens(p)
        if not pt:
            continue
        overlap = len(ct & pt)
        if overlap == 0:
            continue
        score = overlap / math.sqrt(len(pt))
        scored.append((score, p))
    scored.sort(key=lambda sp: -sp[0])
    out = []
    for score, p in scored[:top]:
        out.append({"score": round(score, 3), "text": p if len(p) <= EXCERPT_CHARS else p[: EXCERPT_CHARS - 1] + "…"})
    return out


def resolve_source_page(name):
    p = VAULT_ROOT / "wiki" / "sources" / f"{name}.md"
    return p if p.is_file() else None


def raw_text_paths(fm):
    """source ページの `sources:` にある `.raw/...` 参照から、読める抽出テキストを探す。"""
    out = []
    for item in STATS.as_list(fm.get("sources")):
        target = STATS.wikilink_target(item) or str(item)
        target = target.strip().strip('"')
        if not target.startswith(".raw/"):
            continue
        cand = VAULT_ROOT / target
        candidates = []
        if cand.is_dir():
            # ハブ entity は `.raw/theses/<slug>/` を引く。全文テキストは <slug>/<slug>.txt。
            candidates.append(cand / f"{cand.name}.txt")
        elif cand.suffix.lower() in RAW_TEXT_SUFFIXES:
            candidates.append(cand)
        else:
            for suf in RAW_TEXT_SUFFIXES:
                candidates.append(cand.with_suffix(suf))
        for c in candidates:
            if c.is_file() and c not in out:
                out.append(c)
    return out


def number_raw_paths(fm):
    """数値照合に使う raw テキスト。章別の原本(chapters/ch-NN.*)を引く source には、同じ書籍・論文の
    目次(toc.txt)と全文テキスト(<slug>.txt)も足す。章番号の相互参照と、章の外にある文献表のため。
    章の原本が PDF だけで抽出テキストが無くても、目次と全文テキストがあればそれに照らす。"""
    out = raw_text_paths(fm)
    chapter_dirs = [c.parent for c in out if c.parent.name == "chapters"]
    for item in STATS.as_list(fm.get("sources")):
        target = (STATS.wikilink_target(item) or str(item)).strip().strip('"')
        if target.startswith(".raw/") and Path(target).parent.name == "chapters":
            chapter_dirs.append(VAULT_ROOT / Path(target).parent)
    for d in chapter_dirs:
        root = d.parent
        for extra in (root / "toc.txt", root / f"{root.name}.txt"):
            if extra.is_file() and extra not in out:
                out.append(extra)
    return out


def hub_packet(name, claim):
    """博士論文・サーベイ・書籍のハブ entity。章別 source が根拠に無いときの代替で、抜粋は要約水準。"""
    page = VAULT_ROOT / "wiki" / "entities" / f"{name}.md"
    try:
        text = page.read_text(encoding="utf-8")
    except OSError:
        return {"source": name, "page": None, "status": "source_missing", "page_excerpts": [],
                "raw_text": None, "raw_excerpts": []}
    _, body = STATS.split_frontmatter(text)
    return {"source": name, "page": rel_posix(page), "status": "hub",
            "page_excerpts": rank_paragraphs(claim["claim"], bullet_aware_paragraphs(body)),
            "raw_text": None, "raw_excerpts": []}


# --------------------------------------------------------------------------- 数値の照合

# 数値として読まない部分。wikilink(出典名の年)、コード、URL、ISO 日付、頁番号。
NUM_BLANK_RE = re.compile(
    r"!?\[\[[^\]]*\]\]|`[^`]*`|\]\([^)]*\)|https?://\S+|\b\d{4}-\d{2}-\d{2}\b"
    r"|\bpp?\.\s*\d+(?:\s*[-–]\s*\d+)?|\b10\.\d{4,}/\S+|\b\d{4}\.\d{4,5}(?:v\d+)?\b"
)
# 著者年の引用(Salfner+ 2010、Zhang et al. 2024、Cook (1998))。数値の主張ではなく文献の名指しなので、
# 照合する側(命題・根拠・観察)でだけ読まない。出典側で消すと文献表の刊行年が照合に使えなくなる。
CITATION_RE = re.compile(
    r"[A-Za-z]\+\s?\(?(?:19|20)\d{2}\)?|\bet al\.?,?\s*\(?(?:19|20)\d{2}\)?"
    r"|\b[A-Z][A-Za-z\-]+\s\((?:19|20)\d{2}\)"
)
# 図表キャプション。数値は画像から読んだもので抽出テキストに無いのが普通なので、missing と分けて返す。
CAPTION_RE = re.compile(r"^\(?(?:Figure|Fig\.|Table|図|表)\s*\d")
# 3.3.2 のような節番号は字面で、それ以外は値で照合する。英字直後の数字(H100, GPT4, v2)は識別子なので拾わない。
# 古い論文の抽出テキストは小数の先頭の 0 を省く(.5)。英字・数字・点の直後でない「.」から始まるものは小数として読む
# (Fig.5、v1.2、...3 は従来どおり拾わない)。
NUM_RE = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"(\d+(?:\.\d+){2,}|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?![0-9])\s*(%|[kKMB](?![A-Za-z])|万|億|(?i:thousand|million|billion)\b)?"
)
NUM_SCALE = {"k": 10**3, "K": 10**3, "M": 10**6, "B": 10**9, "万": 10**4, "億": 10**8,
             "thousand": 10**3, "million": 10**6, "billion": 10**9}
CHECKED_EVIDENCE = ("根拠", "反証")


def _canon(d):
    s = format(d.normalize(), "f")
    return s[:-2] if s.endswith(".0") else s


# 年の範囲表記で後ろの年を下 2 桁に省略した形(1984〜86 年、2019-21)。NFKC の後なので「〜」は残り、「～」は「~」になる。
YEAR_RANGE_RE = re.compile(r"(?<![0-9.])((?:1[5-9]|20)\d{2})(\s*[〜~\-–—]\s*)(\d{1,2})(?![0-9.])")


def expand_year_ranges(s):
    """省略した年を展開する(1984〜86 → 1984〜1986)。展開して前の年より後になるときだけ展開する。

    2024-05 のような年月は展開すると 2005 で前の年より前になるので、そのまま残す。
    """
    def repl(m):
        first, sep, short = m.group(1), m.group(2), m.group(3)
        full = first[: len(first) - len(short)] + short
        if int(full) <= int(first):
            return m.group(0)
        return first + sep + full
    return YEAR_RANGE_RE.sub(repl, s)


def number_mentions(text, source_side=False):
    """text 中の数値を [(字面, 照合キー集合)] で返す。キーは 1.50 と 1.5、10K と 10000、35% と 0.35 を同一視する。"""
    s = expand_year_ranges(NUM_BLANK_RE.sub(" ", unicodedata.normalize("NFKC", text)))
    if not source_side:
        s = CITATION_RE.sub(" ", s)
    out = []
    for m in NUM_RE.finditer(s):
        num, suffix = m.group(1), m.group(2)
        if num.count(".") >= 2:
            out.append((num, {"s:" + num}))
            continue
        try:
            base = Decimal(num.replace(",", ""))
        except InvalidOperation:
            continue
        keys = {_canon(base)}
        scale = NUM_SCALE.get(suffix) or NUM_SCALE.get((suffix or "").lower())
        if scale:
            keys.add(_canon(base * scale))
        elif suffix == "%":
            keys.add(_canon(base / 100))
        out.append((m.group(0).strip(), keys))
    return out


WORD_NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
WORD_NUMBERS.update({w: i for i, w in enumerate(
    "_ first second third fourth fifth sixth seventh eighth ninth tenth".split()) if i})
WORD_NUMBERS.update({"twice": 2, "double": 2, "triple": 3, "half": 0.5, "dozen": 12})
WORD_RE = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.IGNORECASE)
# source ページで照合しない節。書誌(出版年・版・URL)は抽出テキストに無いことが多い。
BIBLIO_HEADINGS = ("出典", "論文情報", "書誌情報", "関連")


def number_keys(text):
    """出典側の数値キー。命題は丸めて書くので、小数は桁を落とした値(0.4837 → 0.484, 0.48, 0.5, 0)も持たせる。
    英語の数詞(two, second, twice)も数値として持たせる。"""
    keys = {_canon(Decimal(str(WORD_NUMBERS[w.lower()]))) for w in WORD_RE.findall(text)}
    for _, ks in number_mentions(text, source_side=True):
        keys |= ks
        for k in list(ks):
            if k.startswith("s:") or "." not in k:
                continue
            d = Decimal(k)
            for places in range(len(k.split(".")[1]) - 1, -1, -1):
                keys.add(_canon(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)))
    return keys


class NumberHaystack:
    """source ページと raw 抽出テキストの数値キー集合。出典ごとに 1 回だけ読む。"""

    def __init__(self):
        self.cache = {}

    def get(self, name):
        if name in self.cache:
            return self.cache[name]
        entry = {"page": set(), "raw": set(), "page_found": False, "raw_available": False}
        page = resolve_source_page(name) or (VAULT_ROOT / "wiki" / "entities" / f"{name}.md")
        if page.is_file():
            try:
                text = page.read_text(encoding="utf-8")
                entry["page"] = number_keys(text)
                entry["page_found"] = True
                fm, _ = STATS.split_frontmatter(text)
                for raw in number_raw_paths(fm):
                    try:
                        entry["raw"] |= number_keys(raw.read_text(encoding="utf-8", errors="replace"))
                        entry["raw_available"] = True
                    except OSError:
                        pass
            except OSError:
                pass
        self.cache[name] = entry
        return entry


def check_line_numbers(text, sources, haystack, where):
    """1 行の数値を出典の source ページと raw テキストに照らす。

    返り値の missing は「どの出典のページにも raw にも無い」数値、raw_only は「ページには無く raw にだけある」数値。
    どちらも判定ではなく要確認の印である(単位換算 200µs / 0.2ms や言い換えは拾えない)。
    """
    result = {"checked": 0, "missing": [], "raw_only": []}
    stacks = [haystack.get(s) for s in sources]
    stacks = [h for h in stacks if h["page_found"]]
    if not stacks:
        return result
    # 引いた出典名の年(@2016__...)は、その出典の刊行年として照合済みとみなす
    cited_years = {m.group(1) for m in (re.match(r"@((?:19|20)\d{2})__", s) for s in sources) if m}
    for literal, keys in number_mentions(text):
        result["checked"] += 1
        if literal in cited_years:
            continue
        in_page = any(keys & h["page"] for h in stacks)
        in_raw = any(keys & h["raw"] for h in stacks)
        item = {"where": where, "number": literal, "sources": list(sources)}
        if not in_page and not in_raw:
            item["raw_available"] = any(h["raw_available"] for h in stacks)
            result["missing"].append(item)
        elif not in_page:
            result["raw_only"].append(item)
    return result


def _merge(acc, part):
    acc["checked"] += part["checked"]
    acc["missing"].extend(part["missing"])
    acc["raw_only"].extend(part["raw_only"])
    return acc


def claim_numbers(claim, haystack):
    """命題行と根拠・反証行の数値を照合する。根拠行はその行が引く出典、引かなければ命題の出典全体に照らす。"""
    acc = {"checked": 0, "missing": [], "raw_only": []}
    everything = list(claim["sources"]) + list(claim.get("hubs", []))
    _merge(acc, check_line_numbers(claim["claim"], everything, haystack, "命題"))
    for i, line in enumerate(claim.get("evidence_lines", [])):
        label = line[2:].split(":", 1)[0].strip() if line.startswith("- ") else ""
        if label not in CHECKED_EVIDENCE:
            continue
        s_names, h_names, _ = evidence_names(line)
        cited = s_names + h_names or everything
        _merge(acc, check_line_numbers(line, cited, haystack, f"{label}[{i}]"))
    return acc


def evidence_packet(claim):
    packet = [hub_packet(name, claim) for name in claim.get("hubs", [])]
    for name in claim["sources"]:
        page = resolve_source_page(name)
        if page is None:
            packet.append({"source": name, "page": None, "status": "source_missing", "page_excerpts": [],
                           "raw_text": None, "raw_excerpts": []})
            continue
        try:
            text = page.read_text(encoding="utf-8")
        except OSError:
            packet.append({"source": name, "page": rel_posix(page), "status": "unreadable", "page_excerpts": [],
                           "raw_text": None, "raw_excerpts": []})
            continue
        fm, body = STATS.split_frontmatter(text)
        paras = bullet_aware_paragraphs(body)
        for kc in STATS.as_list(fm.get("key_claims")):
            if len(str(kc)) >= 20:
                paras.append(str(kc))
        is_stub = "source stub" in body or len(body.strip()) < 120
        entry = {
            "source": name,
            "page": rel_posix(page),
            "status": "stub" if is_stub else "ok",
            "page_excerpts": rank_paragraphs(claim["claim"], paras),
            "raw_text": None,
            "raw_excerpts": [],
        }
        raws = raw_text_paths(fm)
        if raws:
            raw = raws[0]
            try:
                raw_text = raw.read_text(encoding="utf-8", errors="replace")
                entry["raw_text"] = rel_posix(raw)
                entry["raw_excerpts"] = rank_paragraphs(claim["claim"], paragraphs(raw_text))
            except OSError:
                pass
        packet.append(entry)
    return packet


# --------------------------------------------------------------------------- 台帳

def load_ledger():
    path = VAULT_ROOT / LEDGER_REL
    if not path.is_file():
        return {"version": 1, "updated_at": None, "verdicts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"ERR: ledger unreadable: {exc}")
        raise SystemExit(EXIT_USAGE)
    data.setdefault("verdicts", {})
    return data


def save_ledger(data):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    atomic_write(VAULT_ROOT / LEDGER_REL, json.dumps(data, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- sample

def concept_pages():
    folder = VAULT_ROOT / "wiki" / "concepts"
    if not folder.is_dir():
        return []
    return sorted(rel_posix(p) for p in folder.glob("*.md") if not p.name.startswith("_"))


def recompiled_pages(limit):
    rows = []
    for rel in concept_pages():
        try:
            text = (VAULT_ROOT / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = STATS.split_frontmatter(text)
        rec = fm.get("recompiled")
        if rec and not isinstance(rec, list):
            rows.append((str(rec), rel))
    rows.sort(reverse=True)
    return [rel for _, rel in rows[:limit]]


def sample(pages, per_page, include_inbox, reaudit, rng):
    ledger = load_ledger()
    haystack = NumberHaystack()
    audited = set(ledger["verdicts"])
    out = []
    skipped = 0
    for rel in pages:
        claims = extract_claims(rel, include_inbox=include_inbox)
        if not reaudit:
            fresh = [c for c in claims if c["id"] not in audited]
            skipped += len(claims) - len(fresh)
            claims = fresh
        if rng is not None:
            rng.shuffle(claims)
        for c in claims[:per_page]:
            c["evidence"] = evidence_packet(c)
            c["numbers"] = claim_numbers(c, haystack)
            out.append(c)
    return out, skipped


# --------------------------------------------------------------------------- numbers

def numbers_for_pages(pages, include_inbox):
    haystack = NumberHaystack()
    rows, checked = [], 0
    for rel in pages:
        for c in extract_claims(rel, include_inbox=include_inbox):
            res = claim_numbers(c, haystack)
            checked += res["checked"]
            if res["missing"] or res["raw_only"]:
                rows.append({"id": c["id"], "page": c["page"], "line": c["line"], "claim": c["claim"],
                             "missing": res["missing"], "raw_only": res["raw_only"]})
    return {"checked": checked, "claims": rows}


def numbers_for_spec(spec_path):
    """wiki-append の batch spec にある観察と問いを、その行が引く出典に照らす(追記の前に使う)。"""
    items = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = [items]
    haystack = NumberHaystack()
    rows, checked, unsourced = [], 0, 0
    for op in items:
        for key in ("observations", "questions_add"):
            for i, line in enumerate(op.get(key) or []):
                s_names, h_names, _ = evidence_names(line)
                cited = s_names + h_names
                if not cited:
                    unsourced += 1
                    continue
                res = check_line_numbers(line, cited, haystack, f"{key}[{i}]")
                checked += res["checked"]
                if res["missing"] or res["raw_only"]:
                    rows.append({"page": op.get("page", ""), "text": line,
                                 "missing": res["missing"], "raw_only": res["raw_only"]})
    return {"checked": checked, "unsourced_lines": unsourced, "lines": rows}


def numbers_for_source_page(rel):
    """source ページ本文(key_claims を含む)の数値のうち、raw 抽出テキストに無いものを行番号つきで返す。"""
    path = VAULT_ROOT / rel
    text = path.read_text(encoding="utf-8")
    fm, _ = STATS.split_frontmatter(text)
    raw_keys, raws = set(), []
    for raw in number_raw_paths(fm):
        try:
            raw_keys |= number_keys(raw.read_text(encoding="utf-8", errors="replace"))
            raws.append(rel_posix(raw))
        except OSError:
            pass
    out = {"page": rel_posix(path), "raw_text": raws, "checked": 0, "missing": [], "caption_missing": []}
    if not raws:
        return out
    _, body = STATS.split_frontmatter(text)
    offset = len(text.splitlines()) - len(body.splitlines())
    rows = [("key_claims", str(kc)) for kc in STATS.as_list(fm.get("key_claims"))]
    heading = ""
    for n, line in enumerate(body.splitlines(), 1):
        if line.startswith("## "):
            heading = line[3:].strip()
        elif heading not in BIBLIO_HEADINGS:
            rows.append((offset + n, line))
    for where, line in rows:
        for literal, keys in number_mentions(line):
            out["checked"] += 1
            if not keys & raw_keys:
                bucket = "caption_missing" if CAPTION_RE.match(line.strip()) else "missing"
                out[bucket].append({"line": where, "number": literal, "text": line.strip()[:160]})
    return out


# --------------------------------------------------------------------------- record / report

def render_report(day, entries):
    counts = {v: 0 for v in VERDICTS}
    for e in entries:
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    lines = [
        "---",
        "type: meta",
        f'title: "Claim Audit {day}"',
        f"date: {day} 00:00",
        f"created: {day}",
        f"updated: {day}",
        "tags:",
        f"  - {day.replace('-', '/')}",
        "  - meta",
        "  - claim-audit",
        "status: developing",
        "related:",
        '  - "[[index]]"',
        '  - "[[conventions]]"',
        "---",
        "",
        f"# Claim Audit: {day}",
        "",
        "`python3 scripts/claim-audit.py record` が台帳(`.vault-meta/claim-audit.json`)の当日分から再描画する。手で編集しない。"
        "判定は命題が引く source ページ(と `.raw/` の抽出テキスト)との照合であり、命題そのものの真偽ではない。",
        "",
        "## Summary",
        "",
        f"- Claims audited: {len(entries)}("
        + " / ".join(f"{v} {counts[v]}" for v in VERDICTS) + ")",
        f"- Pages: {len({e['page'] for e in entries})}",
        "",
        "## Findings",
        "",
    ]
    problems = [e for e in entries if e["verdict"] != "supported"]
    if not problems:
        lines.append("- 不支持・出典に無い・欠損の判定なし。")
    for e in sorted(problems, key=lambda e: (e["page"], e["line"])):
        name = Path(e["page"]).stem
        srcs = "、".join(f"[[{s}]]" for s in e.get("sources", [])[:3]) or "—"
        note = f" — {e['note']}" if e.get("note") else ""
        lines.append(f"- [[{name}]] L{e['line']} — **{e['claim'][:120]}** — `{e['verdict']}`{note}。出典: {srcs}")
    lines += ["", "## Supported", ""]
    ok = [e for e in entries if e["verdict"] == "supported"]
    if not ok:
        lines.append("- なし")
    for e in sorted(ok, key=lambda e: (e["page"], e["line"])):
        lines.append(f"- [[{Path(e['page']).stem}]] L{e['line']} — {e['claim'][:100]}")
    lines.append("")
    return "\n".join(lines)


@locked_ledger
def record(verdicts_path, by):
    path = Path(verdicts_path)
    if not path.is_file():
        log(f"ERR: verdicts file not found: {verdicts_path}")
        return None, EXIT_MISSING
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"ERR: verdicts JSON invalid: {exc}")
        return None, EXIT_USAGE
    if isinstance(items, dict):
        items = items.get("verdicts") or items.get("claims") or []
    ledger = load_ledger()
    day = date.today().isoformat()
    accepted, rejected = 0, []
    for item in items:
        cid = str(item.get("id", "")).strip()
        verdict = str(item.get("verdict", "")).strip()
        if not cid or verdict not in VERDICTS:
            rejected.append({"id": cid, "verdict": verdict})
            continue
        entry = {
            "id": cid,
            "page": item.get("page", ""),
            "line": int(item.get("line", 0) or 0),
            "claim": item.get("claim", ""),
            "sources": item.get("sources", []),
            "verdict": verdict,
            "note": str(item.get("note", "")).strip(),
            "date": day,
            "by": by,
        }
        prev = ledger["verdicts"].get(cid)
        if prev:
            entry["history"] = prev.get("history", []) + [{k: prev[k] for k in ("verdict", "note", "date", "by") if k in prev}]
        ledger["verdicts"][cid] = entry
        accepted += 1
    save_ledger(ledger)
    todays = [e for e in ledger["verdicts"].values() if e.get("date") == day]
    report_rel = f"{REPORT_DIR_REL}/claim-audit-{day}.md"
    with page_lock(report_rel):
        atomic_write(VAULT_ROOT / report_rel, render_report(day, todays))
    return {"ok": True, "accepted": accepted, "rejected": rejected, "report": report_rel,
            "ledger": LEDGER_REL, "today_total": len(todays)}, EXIT_OK


@locked_ledger
def resolve(cid, note):
    ledger = load_ledger()
    entry = ledger["verdicts"].get(cid)
    if entry is None:
        log(f"ERR: verdict not found: {cid}")
        return None, EXIT_MISSING
    entry["resolved"] = date.today().isoformat()
    entry["resolved_note"] = note
    save_ledger(ledger)
    return {"ok": True, "id": cid, "resolved": entry["resolved"], "note": note}, EXIT_OK


def render_summary(ledger):
    entries = list(ledger["verdicts"].values())
    counts = {v: 0 for v in VERDICTS}
    for e in entries:
        counts[e.get("verdict", "unclear")] = counts.get(e.get("verdict", "unclear"), 0) + 1
    pages = {}
    for e in entries:
        if e.get("verdict") in ("unsupported", "not_in_source", "source_missing"):
            pages[e.get("page", "")] = pages.get(e.get("page", ""), 0) + 1
    lines = [
        "## Claim Audit",
        f"- Claims audited (cumulative): {len(entries)}(" + " / ".join(f"{v} {counts[v]}" for v in VERDICTS) + ")",
    ]
    if entries:
        bad = counts["unsupported"] + counts["not_in_source"] + counts["source_missing"]
        lines.append(f"- Problem rate: {bad}/{len(entries)} = {bad / len(entries):.1%}")
    if pages:
        top = sorted(pages.items(), key=lambda kv: -kv[1])[:5]
        lines.append("- Pages with most problems: " + ", ".join(f"[[{Path(p).stem}]] ({n})" for p, n in top if p))
    open_items = [e for e in entries if e.get("verdict") in ("unsupported", "not_in_source") and not e.get("resolved")]
    if open_items:
        lines.append(f"- Unresolved findings: {len(open_items)}(命題側に `- 留保: 再検証` を付けるか、出典を直す)")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="命題の再検証パケットと台帳。")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sample")
    p.add_argument("--pages", nargs="*", default=[])
    p.add_argument("--recompiled", action="store_true", help="recompiled: を持つ concept を新しい順に")
    p.add_argument("--random", type=int, default=0, metavar="N", help="concept をランダムに N 頁")
    p.add_argument("--limit", type=int, default=5, help="--recompiled の頁数(既定 5)")
    p.add_argument("--per-page", type=int, default=3)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--include-inbox", action="store_true")
    p.add_argument("--reaudit", action="store_true", help="台帳に判定のある命題も含める")

    p = sub.add_parser("record")
    p.add_argument("--verdicts", required=True)
    p.add_argument("--by", default="agent", choices=("human", "agent"))

    p = sub.add_parser("resolve")
    p.add_argument("id")
    p.add_argument("--note", default="", help="何をしたか(留保を付けた / 出典を直した / 命題を弱めた)")

    sub.add_parser("report")

    p = sub.add_parser("numbers", help="数値が出典の source ページか raw 抽出テキストにあるかを照合する")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pages", nargs="+", help="concept ページの命題行と根拠・反証行")
    g.add_argument("--spec", help="wiki-append の batch spec(observations / questions_add)")
    g.add_argument("--source-page", help="source ページ本文を自分の raw 抽出テキストに照らす")
    p.add_argument("--include-inbox", action="store_true", help="--pages で受信箱の観察も対象にする")

    args = parser.parse_args(argv)

    if args.cmd == "sample":
        if args.per_page < 1 or args.limit < 1 or args.random < 0:
            log("ERR: --per-page / --limit は 1 以上、--random は 0 以上")
            return EXIT_USAGE
        modes = sum(bool(x) for x in (args.pages, args.recompiled, args.random))
        if modes != 1:
            log("ERR: --pages / --recompiled / --random のいずれか 1 つを指定する")
            return EXIT_USAGE
        rng = random.Random(args.seed) if (args.seed is not None or args.random) else None
        if args.pages:
            pages = []
            for raw in args.pages:
                rel = raw if raw.startswith("wiki/") else f"wiki/concepts/{raw.removesuffix('.md')}.md"
                if not (VAULT_ROOT / rel).is_file():
                    log(f"ERR: page not found: {rel}")
                    return EXIT_MISSING
                pages.append(rel)
        elif args.recompiled:
            pages = recompiled_pages(args.limit)
        else:
            pool = concept_pages()
            rng.shuffle(pool)
            pages = pool[: args.random]
        claims, skipped = sample(pages, args.per_page, args.include_inbox, args.reaudit, rng)
        log(f"claim-audit: {len(claims)} claim(s) from {len(pages)} page(s); {skipped} already audited")
        hub_only = sum(1 for c in claims if c.get("hubs") and not c["sources"])
        unverifiable = sum(1 for c in claims if c.get("unverifiable"))
        if hub_only or unverifiable:
            log(f"claim-audit: WARN {hub_only} claim(s) rest only on hub entities (status=hub), "
                f"{unverifiable} claim(s) have 根拠 lines but no verifiable source (judge source_missing)")
        print(json.dumps({"generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                          "pages": pages, "claims": claims}, ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.cmd == "record":
        out, code = record(args.verdicts, args.by)
        if out is not None:
            log(f"claim-audit: recorded {out['accepted']} verdict(s); report {out['report']}")
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return code

    if args.cmd == "resolve":
        out, code = resolve(args.id.strip(), args.note.strip())
        if out is not None:
            print(json.dumps(out, ensure_ascii=False))
        return code

    if args.cmd == "numbers":
        if args.pages:
            pages = []
            for raw in args.pages:
                rel = raw if raw.startswith("wiki/") else f"wiki/concepts/{raw.removesuffix('.md')}.md"
                if not (VAULT_ROOT / rel).is_file():
                    log(f"ERR: page not found: {rel}")
                    return EXIT_MISSING
                pages.append(rel)
            out = numbers_for_pages(pages, args.include_inbox)
            missing = sum(len(r["missing"]) for r in out["claims"])
        else:
            target = args.spec or args.source_page
            if not (Path(target).is_file() or (VAULT_ROOT / target).is_file()):
                log(f"ERR: not found: {target}")
                return EXIT_MISSING
            if args.spec:
                spec = target if Path(target).is_file() else VAULT_ROOT / target
                out = numbers_for_spec(spec)
                missing = sum(len(r["missing"]) for r in out["lines"])
            else:
                rel = rel_posix(target if Path(target).is_file() else VAULT_ROOT / target)
                out = numbers_for_source_page(rel)
                if not out["raw_text"]:
                    log("claim-audit: WARN raw 抽出テキストが無いので照合できない")
                missing = len(out["missing"])
        log(f"claim-audit: numbers checked {out['checked']}, missing {missing}")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if missing else EXIT_OK

    if args.cmd == "report":
        sys.stdout.write(render_summary(load_ledger()))
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
