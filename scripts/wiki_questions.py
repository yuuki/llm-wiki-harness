"""wiki_questions.py — `## 未解決の問い` の項目が問いか、紛れ込んだ命題かを判定する。

wiki-append(ingest の追記)と wiki-concept-stats(compile 負債の走査)が共有する。
問い節は ingest の追記先だが、受け付けるのは問いか未解決点の記述だけである。
出典付きの断定(「X は Y を実用化した」)は観察であり、受信箱に書く(conventions §8)。

判定は語彙に頼る近似である。項目全体(wikilink を除く)に疑問の印か未解決を示す語が
あれば問いとみなす。太字の頭が名詞句のラベルで問いは本文にある項目や、背景の文のあとに
問いを置く項目が多いので、頭や最初の文だけで判定しない。
"""

import re

BOLD_HEAD_RE = re.compile(r"^\*\*(.+?)\*\*")
# `[[@X]]` とパス付きの `[[wiki/sources/@X]]` を同じ出典として扱う(claim-audit と同じ規則)
CITATION_RE = re.compile(r"\[\[(?:[^\]|#]*/)?@|[(（]\s*(?:Source|ソース|出典)\s*[:：]")
OPEN_WORDS = (
    "未実施", "未確立", "未解明", "未検証", "未確認", "未解決", "未比較", "未評価", "未整理", "未報告",
    "未知", "未定", "未着手", "課題", "欠如", "欠けて", "ギャップ", "不明", "残る", "残って", "残された",
    "分からない", "わからない", "明らかでない", "明らかになっていない", "定まっていない", "検証されていない",
    "確かめられていない", "知られていない", "要検証", "要確認", "検討", "余地",
    "ていない", "未探索", "将来", "必要", "要る", "どこまで", "どう", "なぜ", "何が", "限界", "境界",
    "限られ", "乏しい", "薄い", "未整備", "未回答", "解明", "示さない", "扱わない", "まだ", "はない", "は無い",
    "がない", "が無い", "未分析", "未完了", "明示されない", "自明で",
)

# 太字の頭が文として言い切っている(述語で終わる)なら命題の形。名詞句のラベルは問いの見出しとして通す。
ASSERTIVE_END_RE = re.compile(r"(?:である|であった|だった|だ|した|した。|する|される|された|示す|示した|持つ|持った|"
                              r"なる|なった|できる|できた|ている|ていた|られる|られた|ない)[。]?$")


QUESTION_ANYWHERE_RE = re.compile(r"[?？]|(?:か|のか|だろうか)(?:[」』)）]|[。、,，:：(（]|\s|$)")
WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")


def item_scope(text):
    """判定に使う範囲。項目全体から wikilink を除いたもの。"""
    t = text.strip()
    if t.startswith("- "):
        t = t[2:].strip()
    return WIKILINK_RE.sub("", t).replace("**", "").strip()


def is_question_like(text):
    scope = item_scope(text)
    if QUESTION_ANYWHERE_RE.search(scope):
        return True
    return any(w in scope for w in OPEN_WORDS)


def has_citation(text):
    return bool(CITATION_RE.search(text))


SENTENCE_SPLIT_RE = re.compile(r"(?<=[。．])")

# 出典を主語にして語る型: 「Gandalf([[@...]])は … と報告する」「[[@...]] が … を示した」。
# 出典の直後に主語の助詞が来て、同じ文の中で報告の動詞が言い切り(文末・「が、」・「。」の前)で終わる。
# 「〜が指摘するように」「〜が報告する 45% は」のように動詞が連体・従属で続く形は断定に数えない。
SOURCE_SUBJECT_RE = re.compile(
    r"\[\[(?:[^\]|#]*/)?@[^\]]*\]\][)）]?\s*(?:は|が|も)"
    r"[^。?？]*?"
    r"(?:報告|定量化|実測|測定|観測|確認|実証|指摘|提示|示|述べ|明らかにし|見出し|挙げ)"
    r"(?:する|した|している|していた|す|した|る|た|ている|ていた)"
    r"(?=\s*(?:[。．]|が[、,]|けれど|$))"
)


def _assertive(fragment):
    """述語で言い切った断定か。未解決点(「〜は未検証である」)と問いは断定に数えない。"""
    s = WIKILINK_RE.sub("", fragment).replace("**", "")
    s = re.sub(r"[(（][^()（）]*[)）]", "", s).strip().rstrip("。．:：").strip()
    if not s or is_question_like(s):
        return False
    return bool(ASSERTIVE_END_RE.search(s))


def _leading_assertion(text):
    """項目の頭が出典付きの断定か。

    後ろに未解決の語が続いても、頭の太字や最初の文が出典付きの事実を言い切っていれば、
    その事実は観察であり問い節に置かない(conventions §8 更新ルール 2)。対象は 3 つの型:
    (1) 太字の頭が述語で言い切っている。(2) 疑問の印が無い項目で、最後の文より前に出典付きの断定の文がある。
    (3) 出典を主語にして報告の動詞で言い切る文がある(「Gandalf([[@...]])は約 45% と報告する」)。
    (3) は疑問の印があっても断定とみなす。出典を文末の括弧に置いた背景の文(「背景を述べた(Source: …)。」)は
    (3) に当たらず、後ろに問いがあれば問いのままである。
    """
    t = text.strip()
    if t.startswith("- "):
        t = t[2:].strip()
    m = BOLD_HEAD_RE.match(t)
    if m and _assertive(m.group(1)):
        return True
    # (3) 出典を主語にして、その出典が報告・実証した事実を言い切る文がある。後ろに問いや未解決点が続いても断定である。
    if SOURCE_SUBJECT_RE.search(t.replace("**", "")):
        return True
    if QUESTION_ANYWHERE_RE.search(item_scope(text)):
        return False
    # 句点で閉じた文は述語で終わっている。最後の文(未解決点の置き場)より前にある、
    # 出典付きで問いでも未解決点でもない文を断定とみなす。
    sentences = [s for s in SENTENCE_SPLIT_RE.split(t) if s.strip()]
    return any(has_citation(s) and not is_question_like(s) for s in sentences[:-1])


def is_stray_assertion(text):
    """問い節に紛れた命題: 問いでも未解決点でもない、出典付きの断定。

    頭が出典付きの断定なら、後ろに未解決の語があっても紛れた命題とみなす(_leading_assertion)。
    """
    if not has_citation(text):
        return False
    return not is_question_like(text) or _leading_assertion(text)


def is_asserted_proposition(text):
    """wiki-append が拒む形: 太字の頭が述語で言い切った、出典付きの断定。"""
    t = text.strip()
    if t.startswith("- "):
        t = t[2:].strip()
    m = BOLD_HEAD_RE.match(t)
    if not m or not is_stray_assertion(text):
        return False
    return bool(ASSERTIVE_END_RE.search(WIKILINK_RE.sub("", m.group(1)).strip()))

