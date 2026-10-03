#!/usr/bin/env python3
"""retrieve / BM25 の分かち。

欧文は語、和文連続は文字 2-gram である。空白の無い和文を 1 トークンにしない。
数字は残す。欧文語の末尾数字も出す。数字と和文が隣接したら境界 2-gram も出す。
和文連続の端は 1-gram も出す。
Sudachi などの形態素解析器は使わない。索引とクエリは同じ関数を通す。
"""

from __future__ import annotations

import re
import unicodedata

TOKENIZE_ID = "cjk-bigram-v3"
INDEX_SCHEMA_VERSION = 4

STOPWORDS = frozenset("""
a an and are as at be by for from has have he her him his i if in is it its
of on or that the their them they this to was were will with you your
""".split())

# ひらがな・カタカナ・漢字・ハングル・々。
# U+3040–30FF を丸ごと取ると中黒・濁点記号が和文連続に混ざるので除外する。
# NFKC 後の全角英数は LATIN / 数字側へ落ちる。
_CJK = (
    r"\u3041-\u3096\u309d-\u309f"
    r"\u30a1-\u30fa\u30fc-\u30ff"
    r"\u31f0-\u31ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af"
    r"\u3005"
)
# \w は CJK を含む。語は非 CJK だけ続け、KVキャッシュ を 1 語にしない。
_WORD_CHAR = rf"(?:(?![{_CJK}])\w)"
RUN_RE = re.compile(
    rf"(?P<digit>[0-9]+(?:,[0-9]{{3}})*)|(?P<cjk>[{_CJK}]+)|"
    rf"(?P<word>{_WORD_CHAR}(?:{_WORD_CHAR}|['\-])*)"
)


def cjk_grams(run):
    if len(run) == 1:
        return [run]
    grams = [run[i:i + 2] for i in range(len(run) - 1)]
    grams.append(run[0])
    grams.append(run[-1])
    return grams


def _digit_token(raw):
    return raw.replace(",", "")


def _trailing_digits(word):
    i = len(word)
    while i > 0 and word[i - 1].isdigit():
        i -= 1
    if i == 0 or i == len(word):
        return None
    return word[i:]


def _bridge(kind1, text1, kind2, text2):
    if kind1 == "digit" and kind2 == "cjk":
        return text1 + text2[0]
    if kind1 == "cjk" and kind2 == "digit":
        return text1[-1] + text2
    return None


def _runs(text):
    """語末数字を独立した digit run として挿む。橋は digit↔cjk の隣接で架ける。"""
    out = []
    for m in RUN_RE.finditer(text):
        kind = m.lastgroup
        raw = m.group(0)
        start, end = m.span()
        if kind == "digit":
            out.append(("digit", _digit_token(raw), start, end))
            continue
        if kind == "word":
            out.append(("word", raw, start, end))
            trail = _trailing_digits(raw)
            if trail:
                out.append(("digit", trail, end - len(trail), end))
            continue
        out.append((kind, raw, start, end))
    return out


def tokenize(text, stopwords=STOPWORDS):
    """小文字化、NFKC、和文 2-gram、数字、欧文語。長さ 1 の欧文と stopword は落とす。"""
    if not text:
        return []
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    out = []
    prev = None
    for kind, raw, start, end in _runs(text):
        if prev is not None and prev[2] == start:
            bridge = _bridge(prev[0], prev[1], kind, raw)
            if bridge:
                out.append(bridge)
        if kind == "digit":
            out.append(raw)
        elif kind == "cjk":
            out.extend(cjk_grams(raw))
        elif raw not in stopwords and len(raw) > 1:
            out.append(raw)
        prev = (kind, raw, end)
    return out
