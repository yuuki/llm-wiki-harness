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
新設時に ingest が置いた「現時点では単一ソースのため、今後の ingest で追記する」の類の 1 項目。
観察ではないので受信箱の件数に数えない。判定は is_placeholder_bullet。
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
    r"今後|次の(?:関連)?ソース|され次第|(?:2|二)\s*ソース目以降|追加時|ソースが(?:加わ|入|揃)"
    r"|未蓄積|蓄積されていない|蓄積待ち|まだ(?:無い|ない|蓄積|行えていない|形成されていない)|限定的|困難"
)
# この節へ積む、を言う語
_ACCUM_RE = re.compile(r"追記|蓄積|積み増|育て|追加する")
_EVIDENCE_RE = re.compile(r"[(（]\s*(?:Source|ソース|出典)\s*[:：]")


def _strip_bullet(text):
    t = text.strip()
    if t[:2] in ("- ", "* "):
        t = t[2:].strip()
    elif t in ("-", "*"):
        t = ""
    return t


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
    1. 本文が空(`- ` だけ)。
    2. 項目全体が 1 組の括弧に収まる(`- (1 ソース目。今後の ingest で追記する)`)。
    3. 項目全体が「まだ無い・今後」の語と、「出典が 1 つだけ」か「この節へ積む」の語を含み、
       すべての文がそのどれかを含む(`- 現時点では単一ソースのため、横断的知見は未蓄積。`
       `- 今後の取り込みで、複数ソース間の関係を追記する。`)。観察を 1 文でも含む項目と、
       `(Source: …)` で出典を示す項目は外れる。
    """
    t = _strip_bullet(text)
    if not t:
        return True
    if t.startswith("**"):
        return False
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
