"""wiki_questions.py — `## 未解決の問い` の項目が問いか、紛れ込んだ命題かを判定する。

wiki-append(ingest の追記)と wiki-concept-stats(compile 負債の走査)が共有する。
問い節は ingest の追記先だが、受け付けるのは問いか未解決点の記述だけである。
出典付きの断定(「X は Y を実用化した」)は観察であり、受信箱に書く(conventions §8)。

判定は語彙に頼る近似である。項目全体(wikilink を除く)に疑問の印か未解決を示す語が
あれば問いとみなす。太字の頭が名詞句のラベルで問いは本文にある項目や、背景の文のあとに
問いを置く項目が多いので、頭や最初の文だけで判定しない。

ただし出典付きの項目は、問いの形をしていても次の断定を含めば紛れた命題とみなす(_leading_assertion)。
- 太字の頭が述語で言い切っている。
- 出典を主語にして報告の動詞で言い切る(「[[@X]] は … と結論づける」「Gandalf([[@X]])は約 45% と報告する」)。
  報告の動詞は REPORT_VERB_STEMS(報告・示す・明らかにする・結論づける・主張する・発見する など)。
- 出典を主題の枠にして言い切る(「[[@X]] によれば、Y は 45% である」)。
- 前半で結果を報告し、後半を問いにする(「AgentX は有効性を示したが、別ドメインでも成り立つか」
  「予備実験は 0.32 → 0.06 の低下を示した。原因は何か」)。前半の述語は過去形の結果報告
  (RESULT_VERB_RE。示した・明らかにした・結論づけた・発見した 等)に限る。「述べた」のような
  背景の叙述は対象外で、後ろに問いがあれば問いのままである。
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
# 報告の動詞の語幹。活用語尾は REPORT_ENDINGS で受ける(結論づけ+る、明らかにし+た、見積も+った)。
REPORT_VERB_STEMS = (
    "報告", "定量化", "実測", "測定", "観測", "確認", "実証", "指摘", "提示", "示唆", "示", "述べ",
    "明らかにし", "明らかにす", "見出し", "挙げ", "結論づけ", "結論付け", "結論", "主張", "論じ", "論証",
    "発見", "証明", "突き止め", "特定", "推定", "算出", "見積も", "達成", "記録", "報じ",
)
REPORT_ENDINGS = r"(?:する|した|している|していた|す|る|た|った|ている|ていた|ってい(?:る|た))"
_REPORT_VERB = "(?:" + "|".join(REPORT_VERB_STEMS) + ")" + REPORT_ENDINGS
_SOURCE_LINK = r"\[\[(?:[^\]|#]*/)?@[^\]]*\]\][)）]?"
SOURCE_SUBJECT_RE = re.compile(
    _SOURCE_LINK + r"\s*(?:は|が|も)"
    r"[^。?？]*?"
    + _REPORT_VERB +
    r"(?=\s*(?:[(（][^()（）]*[)）])?\s*(?:[。．]|が[、,]|けれど|$))"
)
# 出典を主題の枠にした言い切り: 「[[@X]] によれば、Y は 45% である。」
SOURCE_FRAME_RE = re.compile(_SOURCE_LINK + r"\s*(?:によれば|によると|に従えば|の報告では|の実測では)[、,]?([^。?？]*)")
# 前半の結果報告: 過去形の結果報告の動詞で終わる節。「述べた」「挙げた」のような叙述は含めない。
RESULT_VERB_RE = re.compile(
    r"(?:報告し|定量化し|実測し|測定し|観測し|実証し|示し|明らかにし|結論づけ|結論付け|結論し|"
    r"発見し|証明し|突き止め|特定し|達成し|記録し|見出し)(?:た|ている|ていた)$"
)
CLAUSE_SPLIT_RE = re.compile(r"(?<=[。．])|(?<=が)[、,]|(?<=けれど)[、,]?")


def _assertive(fragment):
    """述語で言い切った断定か。未解決点(「〜は未検証である」)と問いは断定に数えない。"""
    s = WIKILINK_RE.sub("", fragment).replace("**", "")
    s = re.sub(r"[(（][^()（）]*[)）]", "", s).strip().rstrip("。．:：").strip()
    if not s or is_question_like(s):
        return False
    return bool(ASSERTIVE_END_RE.search(s))


def _reported_result_then_open(text):
    """最後でない節が、過去形の結果報告(示した・明らかにした・結論づけた 等)で言い切っているか。

    節は「。」と、逆接の「が、」「けれど」で切る。連体で続く「X が示した 45% は」は節の終わりではないので当たらない。
    """
    clauses = [c for c in CLAUSE_SPLIT_RE.split(text) if c and c.strip()]
    if len(clauses) < 2:
        return False
    for clause in clauses[:-1]:
        c = WIKILINK_RE.sub("", clause)
        c = re.sub(r"[(（][^()（）]*[)）]", "", c).strip()
        c = re.sub(r"(?:が|けれど)$", "", c.rstrip("。．、, ")).strip()
        if not c or is_question_like(c):
            continue
        if RESULT_VERB_RE.search(c):
            return True
    return False


def _leading_assertion(text):
    """項目の頭が出典付きの断定か。

    後ろに未解決の語が続いても、頭の太字や最初の文が出典付きの事実を言い切っていれば、
    その事実は観察であり問い節に置かない(conventions §8 更新ルール 2)。対象は 3 つの型:
    (1) 太字の頭が述語で言い切っている。(2) 疑問の印が無い項目で、最後の文より前に出典付きの断定の文がある。
    (3) 出典を主語にして報告の動詞で言い切る文がある(「Gandalf([[@...]])は約 45% と報告する」)。
    (4) 出典を主題の枠にして言い切る文がある(「[[@X]] によれば、Y は 45% である」)。
    (5) 最後でない節が過去形の結果報告で終わる(「X は有効性を示したが、別ドメインでも成り立つか」)。
    (3)〜(5) は疑問の印があっても断定とみなす。出典を文末の括弧に置いた背景の文(「背景を述べた(Source: …)。」)は
    (3) に当たらず、後ろに問いがあれば問いのままである。
    """
    t = text.strip()
    if t.startswith("- "):
        t = t[2:].strip()
    m = BOLD_HEAD_RE.match(t)
    if m and _assertive(m.group(1)):
        return True
    # (3) 出典を主語にして、その出典が報告・実証した事実を言い切る文がある。後ろに問いや未解決点が続いても断定である。
    plain = t.replace("**", "")
    if SOURCE_SUBJECT_RE.search(plain):
        return True
    # (4) 出典を主題の枠にして言い切る(「[[@X]] によれば、Y は 45% である」)。
    for m in SOURCE_FRAME_RE.finditer(plain):
        if _assertive(m.group(1)):
            return True
    # (5) 前半で結果を報告し、後半を問いか未解決点にする。最後の節より前の節が対象。
    if _reported_result_then_open(plain):
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

