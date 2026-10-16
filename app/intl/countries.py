"""Countries outside the US that the service verifies, and which data source answers for each.

Providers, best first (the first one that answers decides; the next is only tried when it fails):
- an official national/regional address register with a free public API (France BAN, Netherlands BAG, ...);
- `overture`: Overture Maps addresses, which carry the official open register for the countries in
  OVERTURE_COUNTRIES, located with Photon (OpenStreetMap search);
- `osm`: OpenStreetMap through Photon only - community data, so it can confirm an address but a miss proves nothing;
- `jp_gsi`: Japan, 国土地理院 address search (block-level data, see japan.py).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    code: str
    zh: str
    names: tuple[str, ...]      # how people write the country at the end of an address (English, native, Chinese)
    postcode: str               # regex of the postcode as written, without any 'D-' / 'F-' style prefix
    providers: tuple[str, ...]
    number_first: bool = False  # "12 rue de Rivoli" rather than "Unter den Linden 77"


# Countries whose official open address register is in Overture Maps (release 2026-09-23.1, measured).
# DE lacks Bavaria; JP is left out on purpose: its Overture numbers are block placeholders ("8-9"), not 号.
OVERTURE_COUNTRIES = {
    "AT", "BE", "CH", "CZ", "DE", "DK", "EE", "ES", "FI", "FO", "FR", "HR", "IS", "IT", "LI", "LT", "LU", "LV",
    "NL", "NO", "PL", "PT", "RS", "SI", "SK",
}

_D4, _D5 = r"\d{4}", r"\d{5}"

_LIST = [
    Country("AD", "安道尔", ("andorra", "安道尔"), r"AD\s?\d{3}", ("osm",)),
    Country("AL", "阿尔巴尼亚", ("albania", "shqiperia", "阿尔巴尼亚"), _D4, ("osm",)),
    Country("AT", "奥地利", ("austria", "osterreich", "oesterreich", "奥地利"), _D4, ("at_bev", "overture", "osm")),
    Country("BA", "波黑", ("bosnia and herzegovina", "bosnia", "bosna i hercegovina", "波黑", "波斯尼亚和黑塞哥维那"), _D5, ("osm",)),
    Country("BE", "比利时", ("belgium", "belgique", "belgie", "belgien", "比利时"), _D4, ("be_register", "overture", "osm")),
    Country("BG", "保加利亚", ("bulgaria", "bulgariya", "保加利亚"), _D4, ("osm",)),
    Country("CH", "瑞士", ("switzerland", "schweiz", "suisse", "svizzera", "瑞士"), _D4, ("ch_geoadmin", "overture", "osm")),
    Country("CY", "塞浦路斯", ("cyprus", "kypros", "塞浦路斯"), _D4, ("osm",)),
    Country("CZ", "捷克", ("czech republic", "czechia", "cesko", "ceska republika", "捷克"), r"\d{3}\s?\d{2}", ("cz_ruian", "overture", "osm")),
    Country("DE", "德国", ("germany", "deutschland", "德国", "德國"), _D5, ("overture", "osm")),
    Country("DK", "丹麦", ("denmark", "danmark", "丹麦"), _D4, ("overture", "osm")),
    Country("EE", "爱沙尼亚", ("estonia", "eesti", "爱沙尼亚"), _D5, ("ee_inads", "overture", "osm")),
    Country("ES", "西班牙", ("spain", "espana", "西班牙"), _D5, ("es_cartociudad", "overture", "osm")),
    Country("FI", "芬兰", ("finland", "suomi", "芬兰"), _D5, ("fi_ryhti", "overture", "osm")),
    Country("FO", "法罗群岛", ("faroe islands", "foroyar", "法罗群岛"), r"\d{3}", ("overture", "osm")),
    Country("FR", "法国", ("france", "法国", "法國"), _D5, ("fr_ban", "overture", "osm"), number_first=True),
    Country("GB", "英国", ("united kingdom", "uk", "great britain", "britain", "england", "scotland", "wales",
                          "northern ireland", "英国", "英國"),
            r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", ("osm",), number_first=True),
    Country("GR", "希腊", ("greece", "hellas", "ellada", "希腊"), r"\d{3}\s?\d{2}", ("osm",)),
    Country("HR", "克罗地亚", ("croatia", "hrvatska", "克罗地亚"), _D5, ("overture", "osm")),
    Country("HU", "匈牙利", ("hungary", "magyarorszag", "匈牙利"), _D4, ("osm",)),
    Country("IE", "爱尔兰", ("ireland", "eire", "爱尔兰"), r"[AC-FHKNPRTV-Y]\d{2}\s?[0-9AC-FHKNPRTV-Y]{4}|D6W\s?[0-9AC-FHKNPRTV-Y]{4}",
            ("osm",), number_first=True),
    Country("IS", "冰岛", ("iceland", "island", "冰岛"), r"\d{3}", ("is_hms", "overture", "osm")),
    Country("IT", "意大利", ("italy", "italia", "意大利"), _D5, ("overture", "osm")),
    Country("LI", "列支敦士登", ("liechtenstein", "列支敦士登"), _D4, ("overture", "osm")),
    Country("LT", "立陶宛", ("lithuania", "lietuva", "立陶宛"), r"(?:LT-?)?\d{5}", ("overture", "osm")),
    Country("LU", "卢森堡", ("luxembourg", "letzebuerg", "luxemburg", "卢森堡"), r"(?:L-?)?\d{4}", ("lu_geoportail", "overture", "osm"),
            number_first=True),
    Country("LV", "拉脱维亚", ("latvia", "latvija", "拉脱维亚"), r"(?:LV-?)?\d{4}", ("lv_vzd", "overture", "osm")),
    Country("MC", "摩纳哥", ("monaco", "摩纳哥"), r"980\d{2}", ("osm",), number_first=True),
    Country("MD", "摩尔多瓦", ("moldova", "摩尔多瓦"), r"(?:MD-?)?\d{4}", ("osm",)),
    Country("ME", "黑山", ("montenegro", "crna gora", "黑山"), _D5, ("osm",)),
    Country("MK", "北马其顿", ("north macedonia", "macedonia", "北马其顿"), _D4, ("osm",)),
    Country("MT", "马耳他", ("malta", "马耳他"), r"[A-Z]{3}\s?\d{2,4}", ("osm",), number_first=True),
    Country("NL", "荷兰", ("netherlands", "the netherlands", "nederland", "holland", "荷兰", "荷蘭"), r"\d{4}\s?[A-Z]{2}",
            ("nl_pdok", "overture", "osm")),
    Country("NO", "挪威", ("norway", "norge", "noreg", "挪威"), _D4, ("no_kartverket", "overture", "osm")),
    Country("PL", "波兰", ("poland", "polska", "波兰"), r"\d{2}-\d{3}", ("pl_gugik", "overture", "osm")),
    Country("PT", "葡萄牙", ("portugal", "葡萄牙"), r"\d{4}-\d{3}", ("overture", "osm")),
    Country("RO", "罗马尼亚", ("romania", "罗马尼亚"), r"\d{6}", ("osm",)),
    Country("RS", "塞尔维亚", ("serbia", "srbija", "塞尔维亚"), _D5, ("overture", "osm")),
    Country("SE", "瑞典", ("sweden", "sverige", "瑞典"), r"\d{3}\s?\d{2}", ("osm",)),
    Country("SI", "斯洛文尼亚", ("slovenia", "slovenija", "斯洛文尼亚"), r"(?:SI-?)?\d{4}", ("si_gurs", "overture", "osm")),
    Country("SK", "斯洛伐克", ("slovakia", "slovensko", "斯洛伐克"), r"\d{3}\s?\d{2}", ("overture", "osm")),
    Country("SM", "圣马力诺", ("san marino", "圣马力诺"), r"4789\d", ("osm",)),
    Country("UA", "乌克兰", ("ukraine", "ukraina", "乌克兰"), _D5, ("osm",)),
    Country("XK", "科索沃", ("kosovo", "科索沃"), _D5, ("osm",)),
    Country("JP", "日本", ("japan", "nippon", "日本"), r"\d{3}-?\d{4}", ("jp_gsi",)),
]

COUNTRIES: dict[str, Country] = {c.code: c for c in _LIST}


def fold(text: str | None) -> str:
    """Lower-case, accents removed, punctuation to spaces: 'Österreich' -> 'osterreich', 'Straße' -> 'strasse'."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = text.translate(_SPECIAL)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


_SPECIAL = str.maketrans({"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "đ": "d", "ł": "l", "þ": "th", "ð": "d", "ı": "i"})

_BY_NAME = {fold(n): c.code for c in _LIST for n in (*c.names, c.zh)}
_BY_NAME.update({"usa": "US", "us": "US", "united states": "US", "united states of america": "US", "美国": "US"})


def resolve_country(value: str | None) -> str | None:
    """'de' / 'DE' / 'Germany' / '德国' -> 'DE'; 'US' for the US; None when unknown. Two-letter input is read as an
    ISO code, so 'DE' means Germany here (in an address line it would be Delaware, see country_in_text)."""
    v = (value or "").strip()
    if not v:
        return None
    if len(v) == 2 and v.isascii() and v.isalpha():
        code = v.upper()
        return code if code in COUNTRIES or code == "US" else None
    return _BY_NAME.get(fold(v))


# Kana, or a Japanese prefecture followed by a municipality: a line like this is Japanese whatever the country field.
_JP_TEXT_RE = re.compile(r"[぀-ヿ]|〒|(?:都|道|府|県).{1,8}?(?:市|区|郡|町|村)")


def country_in_text(text: str | None) -> str | None:
    """Country named at the end of an address line ('..., 10117 Berlin, Germany' -> 'DE'), or a Japanese address
    written in Japanese. Only full names count: a trailing 'DE' / 'MD' / 'ME' is a US state, not Germany / Moldova /
    Montenegro, and so is 'Georgia', which is not in the list at all."""
    t = unicodedata.normalize("NFKC", text or "").strip()
    if not t:
        return None
    last = re.split(r"[,\n]", t)[-1]
    code = _BY_NAME.get(fold(last))
    if code is None:  # "10117 Berlin Germany" - the country after the city without a comma
        words = fold(last).split()
        for n in (3, 2, 1):
            if len(words) > n and (hit := _BY_NAME.get(" ".join(words[-n:]))) and len(" ".join(words[-n:])) > 2:
                code = hit
                break
    if code is None and _JP_TEXT_RE.search(t):
        return "JP"
    return code


def strip_country(segments: list[str]) -> list[str]:
    """Drop a trailing country name (its own segment, or the last words of the last segment)."""
    segs = list(segments)
    while segs and fold(segs[-1]) in _BY_NAME:
        segs.pop()
    if segs:
        words = segs[-1].split()
        for n in (3, 2, 1):
            # "10117 Berlin Germany" loses Germany, but in "L-1648 Luxembourg" / "98000 Monaco" the name right after
            # the postcode is the city
            if (len(words) > n and fold(" ".join(words[-n:])) in _BY_NAME and len(fold(" ".join(words[-n:]))) > 2
                    and not re.search(r"\d", words[-n - 1])):
                segs[-1] = " ".join(words[:-n])
                break
    return segs
