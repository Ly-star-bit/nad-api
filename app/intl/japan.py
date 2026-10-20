"""Japan: 国土地理院 (GSI) address search + Japan Post postcode tables.

Japanese addresses are blocks, not streets: 町 + 丁目 + 番 (block) + 号 (building), or in rural areas a parcel
number (地番, "978-6"). GSI's search answers with the deepest level it knows - "西新宿二丁目８番" - and silently drops
what it doesn't, so the verdict compares that level with what was asked:

- 号 matched: verified.
- only 番 known (much of central Tokyo has no 号 data): verified to the block; the 号 cannot be checked. Where the
  block does have 号 data, a 号 GSI doesn't know is street_only.
- the 丁目 exists but not this 番: street_only; the town exists but not this 丁目: not_found.
- rural parcel areas where GSI stops at the town: not_covered.

GSI searches by name only, so a town name that exists in several places (本町, 中央) needs the prefecture or city;
Japan Post's tables fill those in from the postcode, a ward, or a prefecture written anywhere in the address, and
read addresses written in romaji ("2-8-1 Nishi-Shinjuku, Shinjuku-ku, Tokyo 163-8001"), which GSI can't search.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import re
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import config
from ..upstream import UpstreamRejected
from .base import Ctx, ProviderInfo
from .countries import JP_PREFECTURES, zh_to_ja

log = logging.getLogger(__name__)

INFO = {"jp_gsi": ProviderInfo("jp_gsi", "国土地理院 住所検索", "official", "https://maps.gsi.go.jp",
                               "国土地理院コンテンツ利用規約（PDL1.0 互換）", "出典：国土地理院ウェブサイト; 日本郵便 郵便番号データ")}

_KANJI_DIGITS = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_NUM = "〇一二三四五六七八九十"
_DASHES = "‐‑‒–—―−ー－ｰ─━"
_PREF_RE = re.compile("|".join(JP_PREFECTURES))


def kanji_int(s: str) -> int | None:
    """'二十三' -> 23, '十' -> 10, '四' -> 4."""
    if not s or any(ch not in _KANJI_NUM for ch in s):
        return None
    if "十" not in s:
        n = 0
        for ch in s:
            n = n * 10 + _KANJI_DIGITS[ch]
        return n
    tens, _, ones = s.partition("十")
    return (_KANJI_DIGITS.get(tens, 1) if tens else 1) * 10 + (_KANJI_DIGITS.get(ones, 0) if ones else 0)


# Japan Post's town names with a numeral + 番 in them (other than 番町 / 番丁 / 番通); a bare 一番 with its ward
_BAN_TOWNS = ("麻布十番", "熱田区一番", "熱田区二番", "熱田区四番", "熱田区六番", "熱田区八番", "一番堀通町", "一番堰", "二番堰",
              "一番沢", "二番沢", "三番沢", "四番沢", "六番沢", "五番領", "市中一番甲", "市中二番甲", "市中三番甲", "市中四番甲",
              "春日一番割", "春日二番割", "春日三番割", "春日四番割", "一番江", "二番江", "三番江", "四番江", "五番江", "六番江",
              "七番江", "八番江", "奥二番川", "奥三番川", "奥四番川", "一番耕地", "二番耕地", "三番耕地")


def _kanji_before(text: str, markers: str) -> str:
    """Kanji numerals in front of 丁目 / 番 / 号 / 条 / 線 -> digits: '二丁目八番' -> '2丁目8番', '千代田一番一号' ->
    '千代田1番1号', '曽我八八八番地' -> '曽我888番地'. Not inside town names: 一番町, 二番丁, 麻布十番, 熱田区一番."""
    def sub(m: re.Match) -> str:
        marker = text[m.end():]
        if re.match(r"番[町丁通]", marker) or (re.match(r"番(?!地)", marker) and any(
                text.startswith(name, m.end() - name.index("番")) for name in _BAN_TOWNS if m.end() >= name.index("番"))):
            return m.group()
        return str(kanji_int(m.group(1)))
    return re.sub(rf"([{_KANJI_NUM}]+)(?=(?:{markers}))", sub, text)


def jp_text(text: str) -> str:
    """NFKC (full-width digits and hyphens), dash-like characters between digits -> '-', 'の' between numbers
    ('978番地の6' too)."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(rf"(?<=\d)\s*[{_DASHES}-]\s*(?=\d)", "-", t)
    t = re.sub(r"(?<=\d)の(?=\d)", "-", t)
    t = re.sub(r"(?<=\d)(番地|番)の(?=\d)", r"\1-", t)
    return re.sub(r"\s+", " ", t).strip()


_CMP = str.maketrans({"ヶ": "ケ", "ヵ": "カ", "が": "ケ", "ノ": "の", "之": "の", "﨑": "崎", "髙": "高", "邊": "辺",
                      "邉": "辺", "濵": "浜", "德": "徳", "瀨": "瀬", "舘": "館", "冨": "富"})
# Variant kanji GSI doesn't know as typed (尼﨑 -> 尼崎): only for a second query
_VARIANTS = str.maketrans({"﨑": "崎", "髙": "高", "邊": "辺", "邉": "辺", "濵": "浜", "德": "徳", "瀨": "瀬", "舘": "館",
                           "冨": "富", "槇": "槙", "嶋": "島", "籠": "篭"})


def town_key(text: str) -> str:
    """For comparing place names: kanji numerals -> digits, 大字 / 字 dropped, variant kana and kanji unified."""
    t = jp_text(text).replace(" ", "").replace("大字", "").replace("字", "")
    t = re.sub(r"(?<=[都道府県])[^都道府県市区町村郡]{1,6}郡", "", t)  # GSI writes ニセコ町, not 虻田郡ニセコ町
    t = re.sub(r"（.*?）|\(.*?\)", "", t)
    t = re.sub(rf"[{_KANJI_NUM}]+", lambda m: str(kanji_int(m.group())) if kanji_int(m.group()) is not None else m.group(), t)
    return t.translate(_CMP)


_BOUNDARY = "都道府県市区郡町村"


def town_matches(want: str, have: str) -> bool:
    """The typed place (any prefix of 都道府県 + 市区町村 + 町, possibly with parts left out) is the GSI town. Names
    are compared whole: '上野' is not '東上野', '新宿' is not '西新宿'."""
    w, h = town_key(want), town_key(have)
    if not w or not h:
        return False
    if h.endswith(w) and (len(h) == len(w) or h[-len(w) - 1] in _BOUNDARY):
        return True
    tail = re.split(r"[都道府県市区郡]", w)[-1] if re.search(r"[都道府県市区郡].", w) else ""
    return (len(tail) >= 2 and h.endswith(tail) and h[-len(tail) - 1] in _BOUNDARY
            and w[: -len(tail)].rstrip(_BOUNDARY) in h)


# ------------------------------------------------------------------------------------------- input

@dataclass
class JpAddress:
    raw: str
    prefix: str = ""                 # everything before the numbers: 東京都新宿区西新宿
    nums: list[int] = field(default_factory=list)
    chome: bool = False              # the first number was written as 丁目
    rest: str = ""                   # building / room after the numbers
    postcode: str = ""
    romaji: bool = False
    hints: list[str] = field(default_factory=list)  # 区 / 市 / 都道府県 written elsewhere ("本町1-1-1, 中央区, 大阪府")
    prefer: list[str] = field(default_factory=list)  # the request's state / city fields, read as places: preferred
    warnings: list[str] = field(default_factory=list)
    read_as: str = ""                # what was cut out to read it ("寺町通御池上る", a label, a name)
    table_missing: bool = False      # a romaji address needed Japan Post's table, which isn't loaded

    @property
    def number(self) -> str:
        return "-".join(map(str, self.nums))


_LABEL_RE = re.compile(r"(?:住所|ご住所|郵便番号|〒番号|邮编|邮政编码|郵遞區號|地址|收件人|收货人|受取人|宛先|宛名|お届け先|送付先|"
                       r"氏名|名前|姓名|电话|電話|手机|手機|携帯|TEL|Tel|tel|FAX|Fax)\s*[:：]\s*")
# A phone number: after a TEL / 電話 label or +81 (spaces may separate the groups), otherwise only with the trunk 0
# every domestic number starts with and dashes, so "721-1 1203" (block + room) and "2381-1-1203" stay the address
_PHONE_RE = re.compile(r"(?:TEL|Tel|tel|電話|电话|携帯|手机|手機)\s*[:：.]?\s*(?:\+81[\s-]?)?0?\d{1,4}[-\s(]\d{1,4}[-\s)]\d{4}(?![\d-])"
                       r"|(?:電話|电话|手机|手機|携帯)\s*[:：]?\s*(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)"  # a Chinese mobile
                       r"|\+81[\s-]?0?\d{1,4}[-\s(]\d{1,4}[-\s)]\d{4}(?![\d-])|(?<![\d-])0\d{1,4}[-(]\d{1,4}[-)]\d{4}(?![\d-])")
# "…西新宿2-8-1 03 5321 1111": a domestic number in space-separated groups, after the address numbers (parse_jp)
_PHONE_SPACED_RE = re.compile(r"(?<=\s)0\d{1,4} \d{1,4} \d{4}(?![\d-])")
_PC_MARKED_RE = re.compile(r"〒\s*(\d{3})\s*-?\s*(\d{4})(?!\d)")
# without 〒: "163-8001" (_postcode decides by what is around it), or 7 digits at the start or the end of the line
# ("…西新宿2-8-1, 1638001")
_PC_RE = re.compile(r"(?<![\d-])(\d{3})-(\d{4})(?![\d-])|^\s*(\d{3})(\d{4})(?!\d)|(?<![\d-])(\d{3})(\d{4})\s*$")
_PC_LABEL_RE = re.compile(r"(?:郵便番号|〒番号|邮编|邮政编码|郵遞區號|郵政編碼|日本国|日本國|日本|zip|postal\s*code|post\s*code)\s*[:：]?\s*$",
                          re.I)
_COUNTRY_RE = re.compile(r"^\s*日本(?:国|國)?(?=\s|,|、|$|" + "|".join(JP_PREFECTURES) + r")\s*[,、]?\s*|\s*[,、]?\s*(?:日本(?:国|國)?|japan)\s*$"
                         r"|^\s*japan\s*[,、]?\s*", re.I)
# Kyoto writes the way to the town from the nearest crossing: "寺町通御池上る上本能寺前町488" (from 寺町通 at 御池, go
# north, to 上本能寺前町). GSI only knows the town. Only that part goes, from the line's start or the last 区 / 市:
# the prefecture, city and ward stay (真町 is in 上京区 and in 下京区).
_KYOTO_RE = re.compile(r"(?:(?<=[区市])|(?<![^\s、,]))[^\s、,\d区市]{1,12}?通[^\s、,\d区市]{0,12}?"
                       r"(?:上る|下る|上ル|下ル|上がる|下がる|東入る|西入る|東入ル|西入ル|東入|西入)")
_HONORIFIC_RE = re.compile(r"(?:様|さま|御中|殿|宛)$")


def parse_jp(text: str) -> JpAddress:
    t = jp_text(text)
    a = JpAddress(raw=t)
    cut: list[str] = []
    for rx in (_PHONE_RE, _LABEL_RE):
        t = rx.sub(lambda m: (cut.append(m.group().strip()), " ")[1], t)
    t = _PHONE_SPACED_RE.sub(lambda m: (cut.append(m.group()), " ")[1] if re.search(r"\d", m.string[: m.start()])
                             and len(re.sub(r"\D", "", m.group())) in (10, 11) else m.group(), t)
    # "日本东京都…" (a Chinese IME's 东京都), "地址：日本大阪市北区…": the country glued to a prefecture or a city
    t = re.sub(r"(?:^|(?<=[\s,、]))日本(?:国|國)?\s*[,、]?\s*", lambda m: " " if _PREF_RE.match(zh_to_ja(m.string[m.end():]))
               or re.match(r"[^\s\d、,]{1,5}?[市区]", m.string[m.end():]) else m.group(), t).strip()
    t = _COUNTRY_RE.sub(" ", t).strip(" ,、")
    if pc := _postcode(t):
        a.postcode, start, end = pc
        t = re.sub(r"[(\[【]\s*[)\]】]", " ", t[:start] + " " + t[end:]).strip(" ,、/;")
    a.romaji = not re.search(r"[぀-ヿ㐀-鿿]", t)
    if cut:
        a.read_as = "; ".join(x for x in cut if re.search(r"\d", x))
    return _parse_romaji(a, t) if a.romaji else _parse_kanji(a, t)


def _postcode(t: str) -> tuple[str, int, int] | None:
    """The postcode in a line -> (its 7 digits, where it starts with its label, where it ends). Without 〒, NNN-NNNN
    is one at the start, after a space, comma, bracket, separator or letter, after a label ('郵便番号163-8001',
    'ZIP:163-8001', '日本163-8001…') or before a prefecture ('163-8001東京都…'); glued to a town name it is the
    parcel number ('笠原町978-1234')."""
    if m := _PC_MARKED_RE.search(t):
        return m.group(1) + m.group(2), m.start(), m.end()
    for m in _PC_RE.finditer(t):
        g = [x for x in m.groups() if x]
        if not m.group(1):
            return g[0] + g[1], m.start(), m.end()
        head = t[: m.start()]
        if label := _PC_LABEL_RE.search(head):
            return g[0] + g[1], label.start(), m.end()
        if re.search(r"(?:^|[\s,、(\[【/;:A-Za-z])$", head) or _PREF_RE.match(zh_to_ja(t[m.end():]).lstrip(" ,、")):
            return g[0] + g[1], m.start(), m.end()
    return None


# A building or room written before the address ("サンハイツ101 東京都…", "5F 大阪市北区…", "101号室 …"): a building
# word, a floor, or a name with a room number - not a bare number or "注文123456", which are order or customer numbers
_BUILDING_RE = re.compile(r"ビル|マンション|ハイツ|ハイム|コーポ|レジデンス|タワー|アパート|メゾン|ヒルズ|パレス|プラザ|"
                          r"コート|ハウス|荘|寮|棟|号室|階|^\d{1,3}\s*[FＦ]$")
_ROOM_NAME_RE = re.compile(r"(?P<name>[ァ-ヶーA-Za-z]{2,})[\s・]*\d{1,4}[A-Za-z]?")


def _building(head: str) -> bool:
    if _HONORIFIC_RE.search(head):
        return False
    m = _ROOM_NAME_RE.fullmatch(head)
    return bool(_BUILDING_RE.search(head) or m and m.group("name").upper() not in ("NO", "TEL", "FAX"))


def _parse_kanji(a: JpAddress, t: str) -> JpAddress:
    if k := _KYOTO_RE.search(t):
        a.read_as = "; ".join(p for p in (a.read_as, k.group()) if p)
        t = (t[: k.start()] + t[k.end():]).strip()
    # "山田太郎様 東京都…", "株式会社ABC 東京都…": the address starts at the prefecture (unless it is written last,
    # "本町1-1-1, 中央区, 大阪府", or is part of a building after the address: "新宿区西新宿2-8-1 東京都庁第一本庁舎")
    building = ""  # written before the address: still the address's building, kept with the room after the numbers
    if (p := _PREF_RE.search(t)) and p.start() > 0 and re.search(r"\d", t[p.end():]):
        head = t[: p.start()].strip(" ,、")
        if head and (not re.search(r"[\d市区町村郡]", head) or re.match(r"[\s、,]*[^\s\d、,]{1,8}?[市区郡町村]", t[p.end():])):
            if _building(head):
                building = head
            else:
                a.read_as = "; ".join(x for x in (a.read_as, head) if x)
            t = t[p.start():]
    # no prefecture, a numbered building first ("サンハイツ101 世田谷区世田谷4-21-27"): its number is not the 番地
    if not building and not _PREF_RE.search(t) and (w := re.match(r"\s*([^\s,、]+)[\s,、]+(?=[^\s\d,、]{1,8}?[市区郡][^\s,、]*\d)", t)) \
            and re.search(r"\d", w.group(1)) and _building(w.group(1)):
        building, t = w.group(1), t[w.end():]
    t = _kanji_before(t, "丁目|番地|番|号|条|線|地割")
    t = re.sub(rf"(?<=\d)(番地|番)の([{_KANJI_NUM}]+)", lambda m: f"{m.group(1)}-{kanji_int(m.group(2))}", t)  # 九七八番地の六
    t = re.sub(r"\s*-\s*", "-", t)
    # the numbers start at the first digit that is not part of a town name (北1条西, 東2線, 第3地割, 1番町)
    start = None
    for m in re.finditer(r"\d+", t):
        if not re.match(r"条|線|地割|番[町丁通]", t[m.end():]) and not (t[m.end():].startswith("号")
                                                                  and re.search(r"線[東西南北]?$", t[: m.start()])):
            start = m.start()
            break
    if start is None:
        a.prefix = re.sub(r"[\s,、]", "", t)
        a.warnings.append("no block / house number (番・号 or 番地) found")
        return a
    head = t[:start]
    words = [w for w in re.split(r"[\s,、]+", head) if w]
    if len(words) > 1 and not _PREF_RE.search(head):  # "山田太郎様 新宿区西新宿": drop what is not a place
        # the first word with a 市 / 区 / 郡 inside ('中村太郎 新宿区西新宿': not the 村 of a name) unless a place is before
        # it ('大阪市 北区梅田'); else the first word with any of them
        keep = next((i for i, w in enumerate(words) if re.search(r".[市区郡].", w) and not _HONORIFIC_RE.search(w)), None)
        if keep is None or any(re.search(r"[都道府県市区郡]$", w) for w in words[:keep]):
            keep = next((i for i, w in enumerate(words) if re.search(r"[市区町村郡]", w) and not _HONORIFIC_RE.search(w)), None)
        if keep:
            head = " ".join(words[:keep])
            if _building(head):
                building = head
            else:
                a.read_as = "; ".join(x for x in (a.read_as, head) if x)
            words = words[keep:]
    a.prefix = "".join(words)
    pos, marks = start, []
    while m := re.compile(r"-?\s*(\d+)\s*(丁目|番地|番|号|-)?\s*").match(t, pos):
        if m.group(2) == "丁目":
            if a.nums:
                break
            a.chome = True
        a.nums.append(int(m.group(1)))
        marks.append(m.group(2))
        pos = m.end()
        if not m.group(2) or m.group(2) == "号":
            break
    rest = t[pos:].strip(" ,、")
    # "豊洲2-2-1001", "長倉2381-1-1203": a third number of 3+ digits after a dash is the room, not a 号
    if len(a.nums) == 3 and a.nums[2] >= 100 and marks[1] == "-" and marks[2] != "号":
        rest = f"{a.nums.pop()} {rest}".strip()
    # a place written after the numbers ("本町1-1-1, 中央区, 大阪府", Western order): a hint, not the building
    parts = [x for x in re.split(r"[\s,、]+", rest) if x]
    hints = [x for x in parts if not re.search(r"\d", x) and (len(x) <= 8 or _PREF_RE.match(x) and len(x) <= 14)
             and (_PREF_RE.fullmatch(x) or re.search(r"[市区郡]$", x))]
    a.hints = hints
    a.rest = " ".join(x for x in parts if x not in hints)
    if not a.prefix and parts:  # "2-8-1 西新宿, 新宿区, 東京都": the town comes after the numbers
        town = next((x for x in parts if x not in hints and not re.search(r"\d", x)), "")
        if town:
            a.prefix = town
            a.rest = " ".join(x for x in parts if x not in hints and x != town)
    a.rest = " ".join(x for x in (building, a.rest) if x)
    _cap(a)
    return a


def _parse_romaji(a: JpAddress, t: str) -> JpAddress:
    t = re.sub(r"(\d+)\s*-?\s*chome\s*[-,]?\s*(?=\d)", r"\1-", t, flags=re.I)  # "2-chome 8-1" -> "2-8-1"
    chome = None
    # "8-1 Nishishinjuku 2-chome", "1-1 Kita 1 Jonishi 2 Chome": the 丁目 written apart from the 番-号
    if m := re.search(r"(?<![\d-])(\d+)\s*-?\s*chome\b\s*,?", t, flags=re.I):
        chome, t = int(m.group(1)), (t[: m.start()] + " " + t[m.end():]).strip(" ,")
    t = re.sub(r"\b(\d+)\s*-?\s*j[oō]\b", r"\1jo", t, flags=re.I)  # Sapporo "Kita 1-jo": part of the town name
    if room := re.search(r"(?:\b(?:apt|apartment|room|rm|unit|suite|flat)\.?|#)\s*([\w-]+)", t, re.I):
        a.rest = room.group(0).strip()
        t = (t[: room.start()] + t[room.end():]).strip(" ,")
    if m := re.search(r"(?<![\d-])(\d+)-(\d+)(?:-(\d+))?(?:-(\d+))?(?![\djJ])", t):
        a.nums = [int(g) for g in m.groups() if g]
    elif m := re.search(r"(?<![\d-])(\d+)(?![\d-]|jo)", t):
        a.nums = [int(m.group(1))]
    if len(a.nums) == 3 and a.nums[2] >= 100:  # "8-1-1203 Nishishinjuku 2-chome", "2-2-1001 Toyosu": the room (R39)
        a.rest = f"{a.nums.pop()} {a.rest}".strip()
    if chome is not None and len(a.nums) <= 2:
        a.nums, a.chome = [chome, *a.nums], True
    elif chome is not None and a.nums[0] != chome:  # "4-21-27 Setagaya 3-chome": which 丁目?
        a.nums = []
        a.warnings.append(f"the 丁目 is written twice and differs ({chome}-chome and {m.group()})")
    if not a.nums:
        a.warnings.append("no block / house number found")
    a.prefix = t
    _cap(a)
    return a


def _cap(a: JpAddress) -> None:
    """At most 丁目-番-号: a fourth number is a room ("2-8-1-101")."""
    limit = 3
    if len(a.nums) > limit:
        a.rest = (f"{'-'.join(map(str, a.nums[limit:]))} " + a.rest).strip()
        a.nums = a.nums[:limit]


def romaji_norm(text: str) -> str:
    """Romaji spellings made comparable: macrons, long vowels (also the passport 'oh') and m/n before b/p dropped
    ('Tōkyō' / 'Toukyou' / 'TOKYO' -> 'TOKYO', 'Ohta' -> 'OTA', 'Nihombashi' -> 'NIHONBASHI')."""
    t = "".join(ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)).upper()
    t = re.sub(r"[^A-Z]", "", t)
    t = re.sub(r"OH(?![AEIOUY])", "O", t)  # an h before a consonant is a long vowel, not ha / hi / hu / he / ho
    t = t.replace("OU", "O").replace("OO", "O").replace("UU", "U")
    return re.sub(r"M(?=[BPM])", "N", t)


def _romaji_loose(name: str) -> str:
    """A romaji_norm'd town name with Kunrei / Nihon-shiki spellings as Hepburn and without a trailing -cho / -machi,
    to compare spellings of one name: TUKIJI -> TSUKIJI, NISHISHINJYUKU -> NISHISHINJUKU, HONMACHI / HOMCHO -> HON."""
    t = re.sub(r"SY", "SH", re.sub(r"TY", "CH", re.sub(r"[ZJ]Y", "J", re.sub(r"M(?![AEIOUY])", "N", name))))
    t = re.sub(r"(?<![SC])HU", "FU", re.sub(r"TU", "TSU", re.sub(r"TI", "CHI", re.sub(r"SI", "SHI", t))))
    t = re.sub(r"[ZD]I", "JI", re.sub(r"DU", "ZU", t))
    return re.sub(r"(?<=...)(?:CHO|MACHI)$", "", t)


# romaji_norm of each prefecture's name (JIS order, as JP_PREFECTURES: 'JP-13' is the 13th)
_PREF_ROMAJI = dict(zip(JP_PREFECTURES, (
    "HOKKAIDO", "AOMORI", "IWATE", "MIYAGI", "AKITA", "YAMAGATA", "FUKUSHIMA", "IBARAKI", "TOCHIGI", "GUNMA", "SAITAMA",
    "CHIBA", "TOKYO", "KANAGAWA", "NIIGATA", "TOYAMA", "ISHIKAWA", "FUKUI", "YAMANASHI", "NAGANO", "GIFU", "SHIZUOKA",
    "AICHI", "MIE", "SHIGA", "KYOTO", "OSAKA", "HYOGO", "NARA", "WAKAYAMA", "TOTTORI", "SHIMANE", "OKAYAMA", "HIROSHIMA",
    "YAMAGUCHI", "TOKUSHIMA", "KAGAWA", "EHIME", "KOCHI", "FUKUOKA", "SAGA", "NAGASAKI", "KUMAMOTO", "OITA", "MIYAZAKI",
    "KAGOSHIMA", "OKINAWA")))
_ROMAJI_PREF = {v: k for k, v in _PREF_ROMAJI.items()}
_PREF_WORDS = {"TO", "FU", "KEN", "PREFECTURE", "PREF", "METROPOLIS", "METROPOLITAN", "JAPAN"}


def prefecture(text: str | None) -> str:
    """A prefecture written any way -> its name: 'Tokyo', 'TOKYO-TO', 'Tōkyō', 'JP-13', '13', '東京', '东京都' ->
    '東京都'; '' when it isn't one."""
    t = zh_to_ja(jp_text(text)).strip()
    if m := re.fullmatch(r"(?:JP)?[-\s]?(\d{1,2})", t, re.I):
        return JP_PREFECTURES[int(m.group(1)) - 1] if 1 <= int(m.group(1)) <= 47 else ""
    if p := next((p for p in JP_PREFECTURES if t in (p, p[:-1] if p[-1] in "都府県" else p)), ""):
        return p
    words = [romaji_norm(w) for w in re.findall(r"[A-Za-zÀ-ɏ]+", t)]
    if not words or re.search(r"[぀-ヿ㐀-鿿\d]", t):
        return ""
    return _ROMAJI_PREF.get("".join(words)) or _ROMAJI_PREF.get("".join(w for w in words if w not in _PREF_WORDS), "")


# ------------------------------------------------------------------------------------- Japan Post

@dataclass(slots=True)
class PostRow:
    postcode: str
    pref: str
    city: str
    town: str                              # "" for a city's catch-all code (以下に掲載がない場合)
    chome: tuple[int, int] | None = None   # the 丁目 this code covers: 大通西（２０〜２８丁目）
    town_r: str = ""                       # romaji, normalised (romaji_norm)
    city_r: str = ""                       # romaji city words, normalised: "SAPPORO CHUO", "KARUIZAWA"
    business: str = ""                     # a large business's (or a building's) own code
    gun_r: str = ""                        # its 郡 (district), apart: "KITASAKU" - addresses often leave it out

    @property
    def place(self) -> str:
        return self.pref + self.city + self.town


_CITY_WORDS = {"SHI", "KU", "MACHI", "CHO", "MURA", "SON", "GUN", "TO", "KEN", "FU", "DO", "CITY", "WARD", "TOWN",
               "VILLAGE", "PREFECTURE"}


class JapanPost:
    """Japan Post's postcode files, downloaded into DATA_DIR and refreshed monthly in the background:
    utf_ken_all (every town, current), KEN_ALL_ROME (the romaji names; Japan Post stopped updating it in 2025, so
    it only supplies names) and JIGYOSYO (large businesses' own codes). Requests never wait for a download: until the
    tables are loaded, romaji addresses can't be read and postcodes are not checked."""

    FILES = {"utf_ken_all.zip": "utf/zip/utf_ken_all.zip", "KEN_ALL_ROME.zip": "roman/KEN_ALL_ROME.zip",
             "jigyosyo.zip": "office/zip/jigyosyo.zip"}
    MAX_AGE_S = 30 * 86400

    def __init__(self, http: httpx.AsyncClient, data_dir: Path) -> None:
        self.http, self.dir = http, data_dir / "japanpost"
        self.by_pc: dict[str, list[PostRow]] = {}
        self.by_place: dict[str, list[PostRow]] = {}   # town_key(pref + city + town)
        self.by_town: dict[str, list[PostRow]] = {}    # town_key(town)
        self.by_romaji: dict[str, list[PostRow]] = {}  # town_r
        self._cities: tuple[dict, dict[str, set[tuple[str, str]]]] = ({}, {})  # (its by_pc, romaji_city's index)
        self._muni: tuple[dict, dict[str, list[str]]] = ({}, {})  # (its by_pc, prefecture -> cities, longest first)
        self.loaded_at = 0.0
        self.error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.by_pc)

    async def run(self) -> None:
        """Background task for the app's lifetime: load, then refresh monthly; on failure retry after 1 min, doubling
        up to 6 h (an unreachable japanpost.jp costs requests nothing)."""
        delay = 60.0
        while True:
            if await self.ensure():
                delay = 60.0
                await asyncio.sleep(max(3600.0, self.loaded_at + self.MAX_AGE_S - time.time()))
            else:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 6 * 3600)

    async def ensure(self) -> bool:
        if self.ready and time.time() - self.loaded_at < self.MAX_AGE_S:
            return True
        async with self._lock:
            if self.ready and time.time() - self.loaded_at < self.MAX_AGE_S:
                return True
            try:
                await self._download()
                tables = await asyncio.to_thread(self._read)
            except Exception as exc:  # keep any older tables
                self.error = f"Japan Post postcode files unavailable: {type(exc).__name__}: {exc}"
                log.warning(self.error)
                return self.ready
            self.by_pc, self.by_place, self.by_town, self.by_romaji = tables
            self.loaded_at, self.error = time.time(), None
            return True

    async def _download(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, path in self.FILES.items():
            f = self.dir / name
            if f.exists() and time.time() - f.stat().st_mtime < self.MAX_AGE_S:
                continue
            try:
                resp = await self.http.get(f"{config.JAPANPOST_URL}/{path}", timeout=httpx.Timeout(60, connect=10))
                resp.raise_for_status()
                zipfile.ZipFile(io.BytesIO(resp.content)).testzip()
            except Exception:
                if f.exists():  # stale copy beats none
                    continue
                raise
            tmp = f.with_suffix(f".{os.getpid()}.part")  # several workers may download at once
            tmp.write_bytes(resp.content)
            tmp.replace(f)

    def _read(self):
        rows: list[PostRow] = []
        towns: dict[tuple[str, str], set[str]] = {}
        floors: list[PostRow] = []
        for r in _csv_rows(self.dir / "utf_ken_all.zip", "utf-8"):
            # the same prefecture and city names are on thousands of rows: one copy of each
            pc, pref, city, town = r[2], sys.intern(r[6]), sys.intern(r[7]), unicodedata.normalize("NFKC", r[8])
            row = PostRow(pc, pref, city, *_clean_town(town))
            if re.search(r"\((?:地階・階層不明|\d+階)\)", town):
                floors.append(row)  # "六本木六本木ヒルズ森タワー(8階)": the building's own code, in town 六本木
            else:
                towns.setdefault((pref, city), set()).add(row.town)
            rows.append(row)
        for row in floors:
            base = max((t for t in towns.get((row.pref, row.city), ()) if t and row.town.startswith(t)), key=len, default="")
            row.business, row.town = row.town, base or row.town
        # KEN_ALL_ROME splits names with an ideographic space: "宮の森　一条" is the town 宮の森一条, while
        # "六本木　六本木ヒルズ森タワー" is a building in 六本木 (its floor rows were moved to that town above)
        romaji: dict[tuple[str, str], tuple[str, str, str]] = {}
        before: dict[tuple[str, str], tuple[str, str, str]] = {}  # the name before the space
        for r in _csv_rows(self.dir / "KEN_ALL_ROME.zip", "cp932"):
            town = re.sub(r"\(.*$", "", unicodedata.normalize("NFKC", r[3])).strip()
            town_r = re.sub(r"\s*\(.*$", "", r[6])
            if not town_r or town_r.startswith("IKANIKEISAI"):
                continue
            words = r[5].upper().split()  # "KITASAKU GUN KARUIZAWA MACHI": the 郡 apart from the town
            gun = words.index("GUN") + 1 if "GUN" in words else 0
            city_r, gun_r = (sys.intern(" ".join(romaji_norm(w) for w in part if w not in _CITY_WORDS))
                             for part in (words[gun:], words[:gun]))
            if name := _clean_town(town.replace(" ", ""))[0]:
                romaji.setdefault((r[0], name), (sys.intern(romaji_norm(town_r)), city_r, gun_r))
            if " " in town and (name := _clean_town(town.split(" ")[0])[0]):
                before.setdefault((r[0], name), (sys.intern(romaji_norm(town_r.split(" ")[0])), city_r, gun_r))
        for row in rows:
            key = (row.postcode, row.town)
            row.town_r, row.city_r, row.gun_r = romaji.get(key) or before.get(key, ("", "", ""))
        # codes KEN_ALL_ROME doesn't have (it is no longer updated): the city's romaji from its other towns
        cities = {(row.pref, row.city): (row.city_r, row.gun_r) for row in rows if row.city_r}
        for row in rows:
            if not row.city_r:
                row.city_r, row.gun_r = cities.get((row.pref, row.city), ("", ""))
        for r in _csv_rows(self.dir / "jigyosyo.zip", "cp932"):
            name, pref, city, pc = r[2], r[3], r[4], r[7]
            town, chome = _business_town(r[5] + r[6])
            rows.append(PostRow(pc, sys.intern(pref), sys.intern(city.replace("\u3000", "")), town,
                                (chome, chome) if chome else None, business=name))
        by_pc: dict[str, list[PostRow]] = {}
        by_place: dict[str, list[PostRow]] = {}
        by_town: dict[str, list[PostRow]] = {}
        by_romaji: dict[str, list[PostRow]] = {}
        for row in rows:
            by_pc.setdefault(row.postcode, []).append(row)
            by_place.setdefault(town_key(row.place), []).append(row)
            if row.town:
                by_town.setdefault(town_key(row.town), []).append(row)
            if row.town_r:
                by_romaji.setdefault(row.town_r, []).append(row)
        return by_pc, by_place, by_town, by_romaji

    def rows(self, pc: str) -> list[PostRow]:
        return self.by_pc.get(pc, [])

    def postcodes_for(self, place: str, chome: int | None = None) -> list[str]:
        """Postcodes of a town (prefecture + city + town), for its 丁目 when the code depends on it; [] when unknown."""
        rows = [r for r in self.by_place.get(town_key(place), []) if not r.business]
        if chome is not None and any(r.chome for r in rows):
            rows = [r for r in rows if r.chome is None or r.chome[0] <= chome <= r.chome[1]]
        return sorted({r.postcode for r in rows})

    def places_named(self, town: str, hints: list[str] = ()) -> list[str]:
        """Prefecture + city + town of every town with this name, narrowed by the 区 / 市 / 都道府県 written with it."""
        rows = [r for r in self.by_town.get(town_key(town), []) if not r.business]
        keys = [town_key(h) for h in hints]
        return sorted({r.place for r in rows if all(k in town_key(r.pref + r.city) for k in keys)})

    def romaji_city(self, text: str) -> str:
        """A city written in romaji with its kind ('Shinjuku-ku', 'Karuizawa-machi') -> prefecture + city, when only
        one city has that name; '' otherwise."""
        words = romaji_tokens(text)
        kinds = [_MUNI_WORDS[w] for w in words if w in _MUNI_WORDS]
        name = " ".join(w for w in words if w not in _CITY_WORDS)
        if not name or not kinds:
            return ""
        if self._cities[0] is not self.by_pc:  # built once per table load (a scan is ~13 ms)
            index: dict[str, set[tuple[str, str]]] = {}
            for rows in self.by_pc.values():
                for r in rows:
                    if not r.business:  # a ward: 北区 of 6 cities
                        for key in {r.city_r, f"{r.gun_r} {r.city_r}", r.city_r.split(" ")[-1]}:
                            index.setdefault(key, set()).add((r.pref, r.city))
            self._cities = (self.by_pc, index)
        cities = {pref + c for pref, c in self._cities[1].get(name, ()) if c.endswith(kinds[-1])}
        return cities.pop() if len(cities) == 1 else ""

    def city_at(self, pref: str, text: str) -> str:
        """The city (市区町村) `text` starts with, as written there: '大阪市中央区本町' -> '大阪市中央区'. A ward city
        written without its ward ('大阪市本町') and a town without its 郡 ('軽井沢町') count. '' when none fits;
        without the prefecture, only a city name no other prefecture has."""
        if self._muni[0] is not self.by_pc:
            index: dict[str, set[str]] = {}
            for rows in self.by_pc.values():
                for r in rows:
                    names = index.setdefault(r.pref, set())
                    names.add(r.city)
                    if m := re.match(r"(.+?市).+区$", r.city):  # 大阪市中央区 -> 大阪市
                        names.add(m.group(1))
                    if "郡" in r.city[:-1]:  # 北佐久郡軽井沢町 -> 軽井沢町
                        names.add(r.city.split("郡", 1)[1])
            self._muni = (self.by_pc, {p: sorted(n, key=len, reverse=True) for p, n in index.items()})
        prefs = [pref] if pref else list(self._muni[1])
        found = {c for p in prefs for c in self._muni[1].get(p, ()) if text.startswith(c) and len(text) > len(c)}
        best = max(found, key=len, default="")
        return best if pref or sum(best in self._muni[1].get(p, ()) for p in prefs) == 1 else ""

    def romaji_places(self, text: str, near: tuple[int | None, int, int] = (None, 0, 0),
                      postcode: set[str] = frozenset(), skip: range = range(0)) -> list[PostRow]:
        """Towns named in a romaji address: the town as whole words (so NAGO is not found in NAGOYA), its city's
        words elsewhere in the address (its 郡 may be left out), nothing written that contradicts it (_fits). One whose
        prefecture is written wins, then the one that leaves fewest words unexplained, then the one written next to the
        house number ('Shinjuku Park Tower, 3-7-1 Nishishinjuku': `near` = the number's word index and how close the
        words before / after it are, 2 without a comma between), the postcode's (`postcode`: town_key of its places),
        the longest ('NISHISHINJUKU' over 'SHINJUKU'), one whose 郡 is written. `skip`: see _pref_written."""
        tokens = romaji_tokens(text)
        at, before, after = near
        hits: list[tuple[tuple[int, ...], PostRow]] = []
        for i in range(len(tokens)):
            for j in range(i + 1, min(i + 4, len(tokens)) + 1):
                name = romaji_norm("".join(tokens[i:j]))  # "Wadayamacho Okada": OO across the space too
                if j < len(tokens) and tokens[j] in _MUNI_WORDS:
                    continue  # "Sakuragawa-shi", "Kanzaki-gun": a city or 郡 written, not the town
                for row in self.by_romaji.get(name, ()):
                    others = set(tokens[:i] + tokens[j:])
                    city = row.city_r.split()
                    if city and all(w in others for w in city) and (pref := _fits(tokens, row, i, j, skip=skip)) is not None:
                        gun = bool(row.gun_r) and set(row.gun_r.split()) <= others
                        adj = max(before if j == at else 0, after if i == at else 0)
                        hits.append(((pref, -_unexplained(tokens, row, i, j), adj, int(town_key(row.place) in postcode),
                                      len(name), int(gun)), row))
        if not hits:
            return []
        top = max(k for k, _ in hits)
        return [r for k, r in hits if k == top]


def romaji_tokens(text: str) -> list[str]:
    return [w for w in (romaji_norm(x) for x in re.findall(r"[A-Za-zÀ-ɏ]+", text)) if w]


_MUNI_WORDS = {"SHI": "市", "KU": "区", "WARD": "区", "CHO": "町", "MACHI": "町", "MURA": "村", "SON": "村", "GUN": "郡"}
_SUFFIXES = _MUNI_WORDS.keys() | _PREF_WORDS


def _unexplained(tokens: list[str], row: PostRow, i: int, j: int) -> int:
    """Words of a romaji address this town doesn't account for (its name, city, 郡 and prefecture with their
    suffixes): 'Wagotaira, Asahi-machi, Yamagata' is 朝日町和合平, not 山形市あさひ町 with Wagotaira left over."""
    used = set(range(i, j))
    for w in row.city_r.split() + row.gun_r.split() + [_PREF_ROMAJI.get(row.pref, "")]:
        # the occurrence written with its suffix first: in 'Shinjuku Park Tower, Nishishinjuku, Shinjuku-ku' the
        # city is the second SHINJUKU, the first is the building's
        ks = [k for k, t in enumerate(tokens) if t == w and k not in used]
        k = next((k for k in ks if k + 1 < len(tokens) and tokens[k + 1] in _SUFFIXES), ks[0] if ks else None)
        if k is not None:
            used |= {k, k + 1} if k + 1 < len(tokens) and tokens[k + 1] in _SUFFIXES else {k}
    return len(tokens) - len(used)


_PREF_SUFFIX = (_PREF_WORDS - {"JAPAN"}) | {"DO"}


def _pref_written(tokens: list[str], skip: range = range(0)) -> set[int]:
    """The words written as the prefecture: a prefecture's name followed by -ken / -fu / -to / -do / Prefecture, or
    the last place word (before the postcode / country) unless -shi / -ku follows it. A prefecture's name anywhere
    else is often a person's, a company's or a building's ('Taro Yamaguchi', 'Chiba Building'). `skip`: the words of
    a name written after the postcode ('…, Tokyo 163-8001 Taro Yamaguchi'), never the last place word."""
    last = max((k for k, t in enumerate(tokens) if t not in _SUFFIXES and k not in skip), default=None)
    return {k for k, t in enumerate(tokens) if t in _ROMAJI_PREF and (
        k + 1 < len(tokens) and tokens[k + 1] in _PREF_SUFFIX
        or k == last and (k + 1 == len(tokens) or tokens[k + 1] not in _MUNI_WORDS))}


def _fits(tokens: list[str], row: PostRow, i: int = 0, j: int = 0, city: bool = True,
          skip: range = range(0)) -> int | None:
    """Whether the rest of a romaji address agrees with this town (tokens[i:j] is its name): None when it names
    another prefecture in the prefecture's place ('Honmachi, Fuchu-cho, Hiroshima' is not 東京都府中市本町; a
    prefecture's name can also be the city's: 'Fukushima-shi'; elsewhere it is only a preference: 'Taro Yamaguchi')
    or, with `city`, a city that is none of the town's city / 郡 names ('Omachi, Kitakata-shi' is not 福島市大町) or
    is another kind ('Fuchu-cho' is not 府中市, 'Kawasaki-ku' not 田川郡川崎町); 1 when the town's prefecture is
    written ('Osaka' also for 大阪市), else 0."""
    names = row.city_r.split() + row.gun_r.split()
    rest = tokens[:i] + tokens[j:]
    prefs = {tokens[k] for k in _pref_written(tokens, skip) if not i <= k < j} - set(names)
    if prefs and _PREF_ROMAJI.get(row.pref) not in prefs:
        return None
    written = [(tokens[k], tokens[k + 1]) for k in range(len(tokens) - 1) if tokens[k + 1] in _MUNI_WORDS and not i <= k < j]
    if city and any(w not in names and w not in _ROMAJI_PREF or w in names and _MUNI_WORDS[x] not in row.city
                    for w, x in written):
        return None
    return int(_PREF_ROMAJI.get(row.pref) in rest)


def _csv_rows(path: Path, encoding: str):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            with z.open(name) as f:
                yield from csv.reader(io.TextIOWrapper(f, encoding=encoding, errors="replace"))


def _clean_town(t: str) -> tuple[str, tuple[int, int] | None]:
    """'西新宿（次のビルを除く）' -> ('西新宿', None); '大通西（２０〜２８丁目）' -> ('大通西', (20, 28));
    '以下に掲載がない場合' -> ('', None)."""
    t = unicodedata.normalize("NFKC", t).strip()
    if t == "以下に掲載がない場合" or t.endswith(("の次に番地がくる場合", "一円")):
        return "", None
    chome = None
    if m := re.search(r"\((\d+)[〜~](\d+)丁目\)", t):
        chome = (int(m.group(1)), int(m.group(2)))
    elif m := re.search(r"\((\d+)丁目\)", t):
        chome = (int(m.group(1)), int(m.group(1)))
    return re.sub(r"\s*\(.*$", "", t).strip(), chome


def _business_town(text: str) -> tuple[str, int | None]:
    """'北１条西' + '１丁目６番地さっぽろ創生スクエア９階' -> ('北1条西', 1); '寺町通御池上る' + '上本能寺前町４８８番地'
    -> ('上本能寺前町', None): the town of a business's own code, without its street, number or building."""
    t = _KYOTO_RE.sub("", unicodedata.normalize("NFKC", text))
    t = _kanji_before(t, "丁目|番地|番|号|条|線")
    m = re.search(r"\d+(?!\d|条|線|地割)", t)
    if not m:
        return t.strip(), None
    chome = int(m.group()) if t[m.end():].startswith("丁目") else None
    return t[: m.start()].strip(), chome


# ---------------------------------------------------------------------------------------- GSI

_TITLE_RE = re.compile(r"^(?P<town>.*?)(?:(?P<chome>\d+)丁目)?(?:(?P<ban>\d+)(?P<bs>番地|番))?(?:(?P<go>\d+)号?)?$")


@dataclass
class Title:
    raw: str
    town: str
    chome: int | None
    ban: int | None
    banchi: bool
    go: int | None
    lat: float
    lon: float

    @property
    def level(self) -> str:
        return "go" if self.go is not None else "ban" if self.ban is not None else "chome" if self.chome is not None else "town"


def parse_title(feature: dict) -> Title | None:
    if not isinstance(feature, dict):
        return None
    title = (feature.get("properties") or {}).get("title") or ""
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2:
        return None
    t = _kanji_before(jp_text(title), "丁目").replace(" ", "")
    m = _TITLE_RE.match(t)
    if not m or (m.group("go") and not m.group("ban")):  # a trailing number without 番 is part of the name
        m = re.match(r"^(?P<town>.*?)(?:(?P<chome>\d+)丁目)?(?:(?P<ban>\d+)(?P<bs>番地|番))?(?P<go>)$", t)
        if not m:
            return None
    n = lambda k: int(m.group(k)) if m.group(k) else None  # noqa: E731
    return Title(title, m.group("town"), n("chome"), n("ban"), m.group("bs") == "番地", n("go"), coords[1], coords[0])


async def _gsi(ctx: Ctx, q: str) -> list[Title]:
    try:
        data = await ctx.get("jp_gsi", config.GSI_URL, {"q": q}, expect=list)
    except UpstreamRejected:
        data = []
    return [x for x in (parse_title(f) for f in data[:100]) if x]


def _query(a: JpAddress, prefix: str) -> str:
    return prefix + (f"{a.nums[0]}丁目" + "-".join(map(str, a.nums[1:])) if a.chome else "-".join(map(str, a.nums)))


async def verify_jp(ctx: Ctx, jpost: JapanPost | None, text: str, *, state: str | None = None,
                    postcode: str | None = None, city: str | None = None) -> dict[str, Any]:
    """-> the fields of a verdict (status, best_match, ...); core.py wraps them into the response. `state`, `city`
    and `postcode` are the request's separate fields: hints, never searched as written (GSI answers
    '大阪府中央区本町3-1-1' with only 大阪府, because the city is 大阪市)."""
    a = parse_jp(text)
    if postcode and not a.postcode and (m := re.fullmatch(r"〒?\s*(\d{3})-?(\d{4})", jp_text(postcode))):
        a.postcode = m.group(1) + m.group(2)
    notes = list(a.warnings)
    for field_, h in (("state", state), ("city", city)):
        h = jp_text(h or "").strip()
        if not h or a.romaji:
            continue
        # a prefecture written any way ('Tokyo', 'JP-13', '东京都'), or a name in Japanese script; never a filter
        ja = zh_to_ja(h).replace(" ", "") if re.search(r"[぀-ヿ㐀-鿿]", h) else ""
        place = (prefecture(h) or ja) if field_ == "state" else (ja or prefecture(h))
        if not place and field_ == "city" and jpost is not None and jpost.ready:
            place = jpost.romaji_city(h)  # 'Shinjuku-ku' -> 東京都新宿区
        if not place:
            notes.append(f"{field_} '{h}' is not a Japanese place name; not used")
        elif place not in a.prefix and place not in a.hints and place not in a.prefer:
            a.prefer.append(place)
    if a.read_as:
        notes.append(f"read without: {a.read_as}")
    timing: dict[str, float] = {}
    res: dict[str, Any] = {"jp": a, "notes": notes, "timing": timing, "status": None, "geocode": None,
                           "best": None, "level": None, "zip_match": None, "zip_should_be": None}
    jp = jpost if jpost is not None and jpost.ready else None
    if jp and not a.romaji and re.fullmatch(r"[A-Za-z][A-Za-z\s,.-]*", a.rest) and (place := jp.romaji_city(a.rest)):
        a.prefer.append(place)  # a romaji city cell after the address: '西新宿2-8-1, Shinjuku-ku'
        a.rest = ""
    if a.romaji:
        _from_romaji(jpost, a, notes, (city, _PREF_ROMAJI.get(prefecture(state), state)))  # state last: _pref_written
        if not a.prefix:  # a service fault when the table is missing, not a bad address
            res["status"] = "upstream_error" if a.table_missing else "geocode_failed"
            return res
    if not a.nums:
        res["status"] = "invalid_input"
        return res
    if not a.prefix:
        res["status"] = "invalid_input"
        notes.append("no prefecture / city / town before the numbers")
        return res
    if jp and not _PREF_RE.match(a.prefix):  # no prefecture: Japan Post knows which towns have this name
        if len(places := _fill_place(jp, a)) > 1:  # "真町52": 4 towns are called 真町, and GSI answers for one
            res["status"] = "geocode_failed"
            notes.append(f"'{a.prefix}' is the name of {len(places)} places ({'、'.join(places[:5])}); add the city "
                         "or ward")
            return res

    t = time.perf_counter()
    titles = await _gsi(ctx, _query(a, a.prefix))
    mine = _mine(a, titles)
    for retry in _retries(a, titles, jp):  # simplified Chinese, variant kanji, a designated city without its ward
        if mine:
            break
        more = await _gsi(ctx, _query(a, retry))
        if found := _mine(a, more, retry):
            a.prefix, titles, mine = retry, more, found
            notes.append(f"searched as {retry}")
    timing["geocode"] = round((time.perf_counter() - t) * 1000, 1)
    if not mine:
        res["status"] = "geocode_failed"
        notes.append(f"国土地理院 has no place '{a.prefix}'" + (f" in {', '.join(a.hints)}" if a.hints else "")
                     + (f"; nearest: {titles[0].raw}" if titles else ""))
        return res
    towns = {town_key(x.town) for x in mine}
    if len(towns) > 1 and a.postcode and jp:
        own = {town_key(r.place) for r in jp.rows(a.postcode)}
        mine = [x for x in mine if town_key(x.town) in own] or mine
        towns = {town_key(x.town) for x in mine}
    if len(towns) > 1:
        res["status"] = "geocode_failed"
        names = list(dict.fromkeys(x.town for x in mine))
        notes.append(f"'{a.prefix}' matches {len(towns)} places ({'、'.join(names[:5])}); add the prefecture and city")
        return res
    order = {"go": 0, "ban": 1, "chome": 2, "town": 3}
    best = min(mine, key=lambda x: order[x.level])
    res["best"] = best
    if off := [h for h in a.prefer if town_key(h) not in town_key(best.town)]:
        notes.append(f"{'、'.join(off)} (state / city field) is not where the address is ({best.town}); not used")
    res["geocode"] = {"source": "gsi", "lat": best.lat, "lon": best.lon, "matched_address": best.raw}
    chome_town = None
    if best.level == "town" and not a.chome:
        # "西新宿9-1-1" came back as just 西新宿: either the town has no 9丁目, or it is a parcel (地番) area GSI has
        # no numbers for. Asking for its 1丁目 tells which.
        probe = await _gsi(ctx, f"{best.town}1丁目")
        chome_town = any(x.chome is not None and town_key(x.town) == town_key(best.town) for x in probe[:5])
    block_has_go = None
    if best.level == "ban" and not best.banchi and _wants_go(a, best):
        # GSI stopped at the block: does it have 号 numbers for this block at all? (then this 号 isn't one of them)
        go = a.nums[-1]
        q = best.town + (f"{best.chome}丁目" if best.chome is not None else "") + f"{best.ban}-{2 if go == 1 else 1}"
        probe = await _gsi(ctx, q)
        block_has_go = any(x.go is not None and x.ban == best.ban and x.chome == best.chome
                           and town_key(x.town) == town_key(best.town) for x in probe[:5])
    banchi_chome = None
    if best.level == "chome" and len(a.nums) > 1 and best.chome == a.nums[0]:
        # GSI stopped at the 丁目: is it one that numbers parcels (番地, Sapporo's 北1条西1丁目)? A parcel GSI doesn't
        # list there can't be checked, while a 住居表示 丁目 without this 番 has no such block
        probe = await _gsi(ctx, f"{best.town}{best.chome}丁目1")
        banchi_chome = any(x.banchi and x.chome == best.chome and town_key(x.town) == town_key(best.town)
                           for x in probe[:5])
    _decide(a, best, res, chome_town, block_has_go, banchi_chome)
    if jp:
        _postcode_check(jp, a, best, res)
    return res


def _mine(a: JpAddress, titles: list[Title], prefix: str | None = None) -> list[Title]:
    """GSI's titles that are the typed place (and lie in the 区 / 市 / 都道府県 written with it; in the request's
    state / city fields when some do)."""
    keys = [town_key(h) for h in a.hints]
    mine = [x for x in titles if town_matches(prefix or a.prefix, x.town) and all(k in town_key(x.town) for k in keys)]
    prefer = [town_key(h) for h in a.prefer]
    return [x for x in mine if all(k in town_key(x.town) for k in prefer)] or mine


def _retries(a: JpAddress, titles: list[Title], jp: JapanPost | None):
    tried = {a.prefix}
    for p in (zh_to_ja(a.prefix), a.prefix.translate(_VARIANTS)):
        if p not in tried:
            tried.add(p)
            yield p
    if jp:  # "大阪府大阪市梅田": GSI fell back to the city; its ward comes from Japan Post
        for x in titles[:3]:
            k, typed = town_key(x.town), town_key(a.prefix)
            if x.level == "town" and typed.startswith(k) and len(k) < len(typed):
                places = [p for p in jp.places_named(typed[len(k):]) if town_key(p).startswith(k)]
                if len(places) == 1 and places[0] not in tried:
                    tried.add(places[0])
                    yield places[0]
                break


def split_place(jp: JapanPost | None, a: JpAddress) -> tuple[str, str, str]:
    """-> (prefecture, city, the town after them) of the place read before the numbers. The prefecture or city can
    also be written apart ('本町1-1-1, 中央区, 大阪府', a state / city field); a part not written (or, for a city in
    the line, not in Japan Post's table) is ''."""
    pref, city, place = "", "", a.prefix
    if m := _PREF_RE.match(place):
        pref, place = m.group(), place[m.end():]
    if jp is not None and jp.ready and (city := jp.city_at(pref, place)):
        place = place[len(city):]
    for h in a.hints + a.prefer:  # '東京都新宿区' from a romaji city field, '大阪府', '中央区'
        m = _PREF_RE.match(h)
        pref = pref or (m.group() if m else "")
        city = city or (h[m.end():] if m else h)
    return pref, city, place


def _fill_place(jp: JapanPost, a: JpAddress) -> list[str]:
    """A town written without its prefecture or city ('本町1-1-1, 中央区, 大阪府', '新宿区西新宿2-8-1'): the full name
    from Japan Post when only one town fits - the one with the postcode given, or the only one with that name in the
    区 / 市 / 都道府県 written (or in the state / city fields). -> the towns that fit when there are several."""
    town = re.split(r"[都道府県市区郡]", a.prefix)[-1] if re.search(r"[都道府県市区郡].", a.prefix) else a.prefix
    hints = [h for h in a.hints + ([a.prefix[: -len(town)]] if town != a.prefix else []) if h]
    places = (a.prefer and jp.places_named(town, hints + a.prefer)) or jp.places_named(town, hints)
    if a.postcode:
        own = {r.place for r in jp.rows(a.postcode)}
        places = [p for p in places if p in own] or places
    places = list({town_key(p): p for p in places}.values())  # the same town written two ways (ケ / ヶ)
    if len(places) == 1 and town_matches(a.prefix, places[0]):
        a.prefix = places[0]
    return places if len(places) > 1 else []


def _wants_go(a: JpAddress, b: Title) -> bool:
    """The input has a 号 after the block GSI matched (so a 番-level answer leaves the 号 unchecked)."""
    nums = a.nums[1:] if (b.chome is not None or a.chome) else a.nums
    return len(nums) > 1 and nums[0] == b.ban


def _decide(a: JpAddress, b: Title, res: dict, chome_town: bool | None = None, block_has_go: bool | None = None,
            banchi_chome: bool | None = None) -> None:
    notes = res["notes"]
    chome_area = b.chome is not None or a.chome or bool(chome_town)
    nums = list(a.nums)
    res["parcel"] = bool(b.banchi or not chome_area)  # a 番地 (parcel-number) address, not 丁目-番-号
    res["go_given"] = len(nums) - (1 if chome_area else 0) > 1  # the input has a 号 / branch number after the 番
    if chome_area and not b.banchi:
        if b.chome is None:  # GSI fell back to the town: there is no such 丁目
            res["status"] = "not_found"
            notes.append(f"{b.town} has no {nums[0]}丁目")
            return
        if b.chome != nums[0]:
            res["status"] = "not_found"
            return
        rest = nums[1:]
        if not rest:
            res["status"] = "invalid_input"
            notes.append("the address stops at the 丁目; a 番 (block) and 号 are needed")
            return
        if b.ban is None and banchi_chome:
            res["status"] = "not_covered"
            notes.append(f"国土地理院 has no parcel {rest[0]} in {b.town}{b.chome}丁目, which numbers parcels (番地), so "
                         f"{'-'.join(map(str, rest))} cannot be checked")
            return
        if b.ban is None or b.ban != rest[0]:
            res["status"] = "street_only"
            res["level"] = "street"
            notes.append(f"{b.town}{b.chome}丁目 exists but has no block {rest[0]}番")
            return
        if len(rest) > 1 and b.go is not None:
            if b.go == rest[1]:
                res["status"], res["level"] = "verified", "address"
            else:
                res["status"], res["level"] = "street_only", "street"
                notes.append(f"block {b.ban}番 exists but has no {rest[1]}号")
            return
        if len(rest) > 1 and block_has_go:
            res["status"], res["level"] = "street_only", "street"
            notes.append(f"国土地理院 has 号 numbers for block {b.ban}番, but not {rest[1]}号 (a building newer than its "
                         "data is possible)")
            return
        res["status"], res["level"] = "verified", "block"
        notes.append("国土地理院 data here only goes down to the block (番); the 号 could not be checked"
                     if len(rest) > 1 else "the address has no 号 (building number); only the block was checked")
        return
    # parcel-number (地番) areas, also inside 丁目 (Sapporo's 北4条西4丁目1 is 番地 1): 978-6 = 番地 978, branch 6
    if chome_area:
        if b.chome != nums[0]:
            res["status"] = "not_found"
            return
        nums = nums[1:]
        if not nums:
            res["status"] = "invalid_input"
            return
    if b.ban is None:
        res["status"] = "not_covered"
        notes.append(f"国土地理院 has no parcel numbers for {b.town}, so {'-'.join(map(str, nums))} cannot be checked "
                     "(common in rural areas that use 地番 addresses)")
        return
    if b.ban != nums[0]:
        res["status"] = "not_covered"
        notes.append(f"国土地理院 matched {b.raw}, not parcel {nums[0]}")
        return
    if len(nums) > 1 and b.go is not None and b.go != nums[1]:
        res["status"], res["level"] = "street_only", "street"
        notes.append(f"parcel {b.ban}番地 exists, but not with the branch number -{nums[1]}")
        return
    if len(nums) > 1 and block_has_go and not b.banchi:  # 大阪城1-99: a 番-号 town without 丁目
        res["status"], res["level"] = "street_only", "street"
        notes.append(f"国土地理院 has 号 numbers for block {b.ban}番, but not {nums[1]}号 (a building newer than its data is "
                     "possible)")
        return
    if len(nums) > 1 and b.go != nums[1]:
        res["status"], res["level"] = "verified", "block"
        notes.append(f"parcel {b.ban}番地 exists; the branch number -{nums[1]} could not be checked")
        return
    res["status"], res["level"] = "verified", "address"


def _from_romaji(jpost: JapanPost | None, a: JpAddress, notes: list[str], hints: tuple = ()) -> None:
    """Romaji address -> the kanji place from Japan Post's tables (GSI only reads Japanese). The town written in
    the address decides; the postcode only picks among same-named towns or fills in a town not written - it never
    moves the address to its own town (a wrong postcode is then reported by the postcode check). When the two are in
    different prefectures and no prefecture is written to tell which is right, neither is used."""
    if jpost is None or not jpost.ready:
        notes.append("the Japan Post postcode table is not loaded yet, so a romaji address cannot be read"
                     + (f" ({jpost.error})" if jpost and jpost.error else "") + "; write it in Japanese to check it now")
        a.prefix, a.table_missing = "", True
        return
    text = " ".join([a.raw, *[h for h in hints if h]])
    rows = jpost.rows(a.postcode) if a.postcode else []
    skip = _name_after_postcode(a)
    # a numbered town is the postcode's only with its number written ('Kita48-johigashi' is not the postcode's
    # 北四十一条東: romaji_places compares the letters only); JIGYOSYO writes 北1条西
    own_pc = {town_key(r.place) for r in rows if _numbers_agree(a, r.town)}
    named = jpost.romaji_places(re.sub(r"\d", " ", text), _number_at(a), own_pc, skip)
    places = list(dict.fromkeys(r.place for r in named))
    if a.postcode:
        if not rows:
            notes.append(f"postcode {a.postcode[:3]}-{a.postcode[3:]} does not exist (Japan Post)")
        own = [p for p in places if town_key(p) in own_pc]
        tokens, letters = romaji_tokens(text), romaji_norm(text)
        if own:
            places = own
        elif places and rows and not {r.pref for r in rows} & {r.pref for r in named} \
                and not any(_fits(tokens, r, city=False, skip=skip) == 1 for r in named):
            notes.append(f"the town written ({'、'.join(places[:3])}) and postcode {a.postcode[:3]}-{a.postcode[3:]} "
                         f"({rows[0].pref}{rows[0].city}{rows[0].town}) are in different prefectures; add the prefecture")
            a.prefix = ""
            return
        elif not places and rows:
            # no city written ('3-1-1 Umeda, Kita-ku 530-0001'): the postcode's town when the address names it too.
            # Otherwise only a business's own code whose town is written, or, with its city written, a town too new for
            # KEN_ALL_ROME (no romaji to compare). A town Japan Post spells differently is not replaced by the
            # postcode's: a typo with a wrong postcode would be verified at another town
            spans = {romaji_norm("".join(tokens[i:j])): (i, j) for i in range(len(tokens))
                     for j in range(i + 1, min(i + 4, len(tokens)) + 1) if j == len(tokens) or tokens[j] not in _MUNI_WORDS}
            towns = list(dict.fromkeys(r.place for r in rows if r.town and r.town_r in spans
                                       and _fits(tokens, r, *spans[r.town_r], skip=skip) is not None
                                       and _numbers_agree(a, r.town)))
            city_ok = any(r.town and r.city_r and all(w in letters for w in r.city_r.split()) for r in rows)
            if not towns and city_ok:
                # the postcode's town spelt another way ('Honmachi' / HONCHO, 'Kinshi-cho' / KINSHI, 'Tukiji',
                # 'Nishishinjyuku'), with its city written. Not from a place word (the prefecture 'Nara' for 楢町,
                # 'Hakui-shi', 'Karuizawa-machi' for 軽井沢町軽井沢); a number written in the town's name must be the
                # town's ('Nishi7-jyokita' is not 西二条北)
                loose: dict[str, list[tuple[int, int]]] = {}
                for name, (i, j) in spans.items():
                    loose.setdefault(_romaji_loose(name), []).append((i, j))
                prefs = _pref_written(tokens, skip)

                def town_word(r: PostRow, i: int, j: int) -> bool:
                    k = j - 1 if j - i > 1 and tokens[j - 1] in ("CHO", "MACHI") else j
                    name = "".join(tokens[i:k])  # the city once ('Ogi, Saga 845-0001'), not 'Ogi, Ogi-shi' for 小城町
                    city = name in r.city_r.split() + r.gun_r.split() and (r.city.endswith("町") if k < j else tokens.count(name) < 2)
                    return not any(t in _SUFFIXES for t in tokens[i:k]) and not city and not (k - i == 1 and i in prefs)

                towns = list(dict.fromkeys(r.place for r in rows if r.town and r.town_r and any(
                    town_word(r, i, j) and _fits(tokens, r, i, j, skip=skip) is not None
                    for i, j in loose.get(_romaji_loose(r.town_r), ())) and _numbers_agree(a, r.town)))
                if len(towns) == 1:
                    notes.append(f"the town is spelt differently in Japan Post's list: read as the postcode's town {towns[0]}")
            business_ok = any(r.business and romaji_norm(_romaji_of(jpost, r)) in letters for r in rows)
            if len(towns) != 1 and (business_ok or city_ok and not any(r.town_r for r in rows if r.town)):
                towns = list(dict.fromkeys(r.place for r in rows
                                           if r.town and _fits(tokens, r, city=False, skip=skip) is not None))
                if len(towns) == 1 and not business_ok:
                    notes.append(f"the town's romaji is not in Japan Post's list: read as the postcode's town {towns[0]}")
            if len(towns) == 1:
                places = towns
            elif city_ok:
                town = f"{rows[0].pref}{rows[0].city}{rows[0].town}"
                notes.append(f"the town written is not in Japan Post's list, and postcode {a.postcode[:3]}-{a.postcode[3:]} "
                             f"is for {town}: check the town, or write the address in Japanese")
                a.prefix = ""
                return
    if len(places) > 1 and (jo := re.findall(r"(\d+)\s*-?\s*j[oō]\b", a.raw, re.I)):
        # Sapporo's 北一条西 / 北二条西 are the same words without the number: 'Kita 1-jo Nishi' says which
        places = [p for p in places if all(re.search(rf"(?<!\d){n}条", town_key(p)) for n in jo)] or places
    if len(places) != 1:
        notes.append("add the postcode (〒) or write the address in Japanese: 国土地理院 only reads Japanese, and "
                     + (f"the town and city names match several places ({'、'.join(places[:4])})" if places
                        else "the town and city names were not found in Japan Post's list"))
        a.prefix = ""
        return
    a.prefix = places[0]
    notes.append(f"romaji address read as {a.prefix}{a.number}")


def _numbers_agree(a: JpAddress, town: str) -> bool:
    """For a numbered town spelt another way: the numbers written in the town's name ('3-jyominami', 'Hama4-ban') are
    its own (romaji_norm drops digits: 'Nishi7-jyokita' is not 西二条北); true when none is written or it has none."""
    have = {int(x) if x.isdigit() else kanji_int(x) for x in re.findall(r"\d+|[一二三四五六七八九十]+", town)}
    pc = _postcode(a.raw)
    raw = f"{a.raw[: pc[1]]} {a.raw[pc[2]:]}" if pc else a.raw
    glued = {int(m.group()) for m in re.finditer(r"\d+", raw) if not re.match(r"-?\s*(?:chome|fl?)\b", raw[m.end():], re.I)
             and (re.search(r"[A-Za-z]$", raw[: m.start()]) or re.match(r"-?[A-Za-z]", raw[m.end():])
                  and not re.search(r"[\d-]$", raw[: m.start()]))}
    return not have or glued <= have


def _name_after_postcode(a: JpAddress) -> range:
    """The words after the postcode when they look like a person's or company's name ('…, Tokyo 163-8001 Taro
    Yamaguchi'): places written before it, after it two or more words, only letters, no -shi / -ku / -ken / Japan
    ('…, Fuchu-shi 726-0005 Hiroshima' is the prefecture); else none."""
    pc = _postcode(a.raw)
    if not pc or pc[0] != a.postcode:
        return range(0)
    head, tail = romaji_tokens(a.raw[: pc[2]]), romaji_tokens(a.raw[pc[2]:])  # head: with a 'ZIP' label
    if not romaji_tokens(a.raw[: pc[1]]) or len(tail) < 2 or re.search(r"\d", a.raw[pc[2]:]) \
            or any(t in _SUFFIXES for t in tail):
        return range(0)
    return range(len(head), len(head) + len(tail))


def _number_at(a: JpAddress) -> tuple[int | None, int, int]:
    """Where the house number is in a romaji address, for romaji_places: its word index, and how close the words
    before / after it are (2: only a space or hyphen between, 1: a comma, 0: none)."""
    nums = [m for m in re.finditer(r"(?<![\d-])(?:\d+\s*-?\s*chome\s*[-,]?\s*)?\d+(?:-\d+)*(?![\d-])", a.raw, re.I)
            if re.sub(r"\D", "", m.group()) != a.postcode]
    m = next((m for m in nums if re.search(r"-|chome", m.group(), re.I)), None) or next(
        (m for m in nums if not re.match(r"\s*(?:f|fl|jo|jō)\b", a.raw[m.end():], re.I)), None)
    if not m:
        return None, 0, 0
    head, tail = a.raw[: m.start()], a.raw[m.end():]
    before = 2 if re.search(r"[A-Za-zÀ-ɏ][\s-]*$", head) else int(bool(re.search(r"[A-Za-zÀ-ɏ][\s,、-]*$", head)))
    after = 2 if re.match(r"[\s-]*[A-Za-zÀ-ɏ]", tail) else int(bool(re.match(r"[\s,、-]*[A-Za-zÀ-ɏ]", tail)))
    return len(romaji_tokens(head)), before, after


def _romaji_of(jp: JapanPost, row: PostRow) -> str:
    """A business row's town in romaji (JIGYOSYO has none): the same town's ordinary row."""
    return next((r.town_r for r in jp.by_place.get(town_key(row.place), []) if r.town_r), "")


def _postcode_check(jp: JapanPost, a: JpAddress, b: Title, res: dict) -> None:
    should = jp.postcodes_for(b.town, b.chome)
    if not a.postcode:
        res["zip_should_be"] = should[0] if len(should) == 1 else None
        return
    rows = jp.rows(a.postcode)
    key = town_key(b.town)
    ok = any(town_key(r.place) == key and (r.chome is None or b.chome is None or r.chome[0] <= b.chome <= r.chome[1])
             for r in rows)
    # a city's catch-all code (以下に掲載がない場合) is right only for towns without a code of their own
    ok = ok or (not should and any(not r.town and key.startswith(town_key(r.pref + r.city)) for r in rows))
    if ok or a.postcode in should:
        res["zip_match"] = True
        return
    res["zip_match"] = False
    res["zip_should_be"] = should[0] if should else None
    where = (f"{rows[0].pref}{rows[0].city}{rows[0].town}" + (f" ({rows[0].business})" if rows[0].business else "")) if rows else ""
    place = b.town + (f"{b.chome}丁目" if b.chome is not None else "")
    res["notes"].append(f"postcode {a.postcode[:3]}-{a.postcode[3:]} is " + (f"for {where}" if rows else "not a Japan Post postcode")
                        + (f"; {place} is {', '.join(p[:3] + '-' + p[3:] for p in should[:3])}" if should else ""))
