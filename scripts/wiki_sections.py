"""wiki_sections.py — concept ページの `##` 節を機能名と主題節に分け、受信箱の案内文を見分ける。

wiki-concept-stats(compile 負債)、wiki-excerpt(--outline)、wiki_claims(claim-audit の命題抽出)が共有する。
3 者で節の数え方がずれると、負債の件数と outline の命題数と再検証の標本が食い違う。

機能名の節(主題節に数えない):
- 固定 6 節(定義 / 子概念 / 未解決の問い / 未編纂の観察 / 関連 / 出典)と旧名 `横断的知見`。
- 固定節に括弧の補足が付いた変種(`横断的知見(続き)` `横断的知見（追記）` `未解決の問い(追加)`)。
  括弧を外した名前で分類する。受信箱の変種は受信箱に、問い節の変種は問い節に数える。
- 参照先の一覧を置く節(`参照` `参照ソース` `関連ページ` `関連概念` `主要ソース` など REFERENCE_HEADINGS)。
  ingest や手書きが `## 関連` `## 出典` の別名として立てたもので、主題を持たない。
  「関連概念との違い」のように主題の名詞を含む見出しは完全一致しないので主題節のままである。

受信箱の案内文(placeholder):
新設時に ingest が置いた「現時点では単一ソースのため、今後の ingest で追記する」の類の 1 項目と、
「未実施」「まだない」「保留した観察: 0 件」のような状況報告だけの項目。観察ではないので受信箱の件数に
数えない。箇条書きでない案内の散文(`*(本 concept は現時点で単一ソースに基づく。…)*`)も同じ判定にかける。
判定は is_placeholder_bullet。

保留した観察(held):
再編纂が主題節へ畳まずに意図的に残した観察。項目の末尾に `— 保留理由: …` を持つ。観察ではあるが
compile 負債の閾値には数えない(保留が残るだけでページが負債へ再入し続けるのを防ぐ)。判定は is_held_bullet。
wiki-concept-stats(--compile-debt)と recompile-queue(refresh)は同じ compile_metrics で数え、
保留の件数は held_bullets として別に出す。
"""

import re

INBOX_HEADING = "未編纂の観察"
LEGACY_INBOX_HEADING = "横断的知見"
QUESTIONS_HEADING = "未解決の問い"
FIXED_HEADINGS = frozenset({
    "定義", "子概念", QUESTIONS_HEADING, INBOX_HEADING, LEGACY_INBOX_HEADING, "関連", "出典",
})
# `## 関連` `## 出典` の別名として立てられた参照一覧の節。完全一致で判定する。
REFERENCE_HEADINGS = frozenset({
    "参照", "参考", "参考文献", "文献", "ソース", "参照ソース", "主要ソース", "一次ソース", "関連ソース",
    "関連文献", "関連ページ", "関連リンク", "関連概念", "関連エンティティ", "関連概念・エンティティ",
    "関連項目", "外部リンク", "See also",
})

# 見出し末尾の括弧の補足(`(続き)` `（追記）` `(障害診断サーベイとの突き合わせ)`)
_HEADING_SUFFIX_RE = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")

KIND_DEFINITION = "definition"
KIND_CHILDREN = "children"
KIND_QUESTIONS = "questions"
KIND_INBOX = "inbox"
KIND_REFERENCE = "reference"
KIND_TOPIC = "topic"


def base_heading(heading):
    """括弧の補足を外した見出し。`横断的知見(続き)` → `横断的知見`。"""
    h = heading.strip()
    stripped = _HEADING_SUFFIX_RE.sub("", h).strip()
    return stripped or h


def section_kind(heading):
    """`##` 見出しの種類。topic だけが主題節である。"""
    h = heading.strip()
    base = base_heading(h)
    for name in (h, base):
        if name in (INBOX_HEADING, LEGACY_INBOX_HEADING):
            return KIND_INBOX
        if name == QUESTIONS_HEADING:
            return KIND_QUESTIONS
        if name == "定義":
            return KIND_DEFINITION
        if name == "子概念":
            return KIND_CHILDREN
        if name in ("関連", "出典") or name in REFERENCE_HEADINGS:
            return KIND_REFERENCE
    return KIND_TOPIC


def is_topic_heading(heading):
    return section_kind(heading) == KIND_TOPIC


def is_legacy_inbox_heading(heading):
    return base_heading(heading) == LEGACY_INBOX_HEADING


# --------------------------------------------------------------------------- 受信箱の案内文

_WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")
_SOURCE_LINK_RE = re.compile(r"\[\[(?:[^\]|#]*/)?(@[^\]|#]+)")
_SENTENCE_RE = re.compile(r"[^。．]+[。．]?")
# 出典が 1 つしかないことを言う語
_SINGLE_RE = re.compile(
    r"単一ソース|単一の(?:ソース|情報源)|単独(?:ソース|の(?:ソース|情報源))?|唯一の(?:ソース|典拠|情報源|出典)"
    r"|1\s*(?:ソース|件)(?:目|のみ)?|一次ソースのみ|のみ(?:が|を|に|から|で)?(?:典拠|出典|情報源|ソース|取り込み|基づ|構成)"
    r"|(?:ソース|典拠|出典|情報源)は.{0,60}のみ|のみを(?:ingest|取り込)"
)
# まだ無い・これから積む、を言う語
_PENDING_RE = re.compile(
    r"今後|次の(?:関連)?ソース|次の取り込み|され次第|揃い次第|可能になり次第|(?:2|二)\s*ソース目以降|追加時"
    r"|ソースが(?:加わ|入|揃)|これから|予定|蓄積中"
    r"|未蓄積|蓄積されていない|蓄積待ち|まだ(?:無い|ない|蓄積|行えていない|形成されていない)|限定的|困難"
)
# この節へ積む、を言う語
_ACCUM_RE = re.compile(r"追記|蓄積|積み増|育て|追加する|拡充|充実")
# 状況報告だけの項目(「未実施」「まだない」「保留した観察: 0 件」「該当なし」)。項目全体がこの形のときだけ当たる。
_STATUS_RE = re.compile(
    r"(?:未実施|未着手|まだ(?:無い|ない|なし)|(?:該当|特に|現時点で(?:は)?)?(?:なし|無し)|N/?A|none|TBD)"
    r"(?:\s*[(（][^()（）]*[)）])?[。．.]?"
    r"|[^。．:：\[\]]{1,24}[:：]\s*(?:[0０]\s*件|なし|無し|未実施|まだ(?:無い|ない))[。．]?",
    flags=re.IGNORECASE,
)
# 強調の囲み(`*(…)*` `_…_`)。案内の散文が斜体で置かれることがある
_EMPHASIS_RE = re.compile(r"^(\*{1,2}|_{1,2})(.+)\1$")
# 再編纂が意図的に残した観察の印。項目の末尾近くに `— 保留理由:` を置く
_HELD_RE = re.compile(r"(?:—|―|–|--)\s*保留理由\s*[:：]")
_EVIDENCE_RE = re.compile(r"[(（]\s*(?:Source|ソース|出典)\s*[:：]")


def _strip_bullet(text):
    t = text.strip()
    if t[:2] in ("- ", "* "):
        t = t[2:].strip()
    elif t in ("-", "*"):
        t = ""
    # 斜体の囲みを外す(太字の頭 `**X**: …` は囲みが末尾まで続かないので外れない)
    m = _EMPHASIS_RE.match(t)
    if m and m.group(1) in ("*", "_") and not m.group(2).startswith(("*", "_")):
        t = m.group(2).strip()
    return t


def is_held_bullet(text):
    """再編纂が保留理由を付けて受信箱に残した観察か(`- 観察。(Source: …) — 保留理由: 単一ソース`)。"""
    t = text.strip()
    if t[:2] in ("- ", "* "):
        t = t[2:]
    return bool(_HELD_RE.search(t))


def _fully_parenthesized(t):
    """先頭の括弧が末尾まで閉じない(= 項目全体が 1 組の括弧に収まる)か。

    内側の括弧を閉じ忘れた案内文(`(… ソース([[A]]・[[B]] 等が ingest された際に追記する。)`)もあるので、
    末尾で深さが 0 に戻ることは求めない。途中で先頭の括弧が閉じたら全体ではない。
    """
    if not t or t[0] not in "(（" or t[-1] not in ")）":
        return False
    depth = 0
    for ch in t[:-1]:
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth -= 1
            if depth <= 0:
                return False
    return True


def is_placeholder_bullet(text):
    """受信箱の案内文か。観察ではないので件数に数えない。

    次のいずれか。太字で始まる項目は命題か観察の頭なので案内文にしない。2 本以上の source を引く項目も
    突き合わせの観察なので案内文にしない(「当初は単一ソースだったが、2 ソース目として [[@B]] が…」)。
    1. 本文が空(`- ` だけ)か、状況報告だけ(`- 未実施` `- まだない` `- 保留した観察: 0 件` `- 該当なし`)。
    2. 項目全体が 1 組の括弧に収まる(`- (1 ソース目。今後の ingest で追記する)`)。
    3. 項目全体が「まだ無い・今後」の語と、「出典が 1 つだけ」か「この節へ積む」の語を含み、
       すべての文がそのどれかを含む(`- 現時点では単一ソースのため、横断的知見は未蓄積。`
       `- 今後の取り込みで、複数ソース間の関係を追記する。`)。観察を 1 文でも含む項目と、
       `(Source: …)` で出典を示す項目は外れる。
    箇条書きでない散文の行も同じ規則で判定する。斜体の囲み(`*(…)*`)は外してから判定する。
    """
    t = _strip_bullet(text)
    if not t:
        return True
    if t.startswith("**"):
        return False
    if _STATUS_RE.fullmatch(t):
        return True
    # 案内文が引くのは立ち上げに使った 1 本だけである。2 本以上の出典を引く項目は突き合わせの観察とみなす。
    if len(set(_SOURCE_LINK_RE.findall(t))) >= 2:
        return False
    if _fully_parenthesized(t):
        return True
    if _EVIDENCE_RE.search(t):
        return False
    plain = _WIKILINK_RE.sub("X", t)
    if not _PENDING_RE.search(plain) or not (_SINGLE_RE.search(plain) or _ACCUM_RE.search(plain)):
        return False
    sentences = [s.strip() for s in _SENTENCE_RE.findall(plain) if s.strip()]
    return bool(sentences) and all(
        _SINGLE_RE.search(s) or _PENDING_RE.search(s) or _ACCUM_RE.search(s) for s in sentences)
