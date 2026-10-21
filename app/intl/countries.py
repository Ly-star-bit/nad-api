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

from ..address import STATE_NAMES, _STATES, state_abbr


@dataclass(frozen=True)
class Country:
    code: str
    zh: str
    names: tuple[str, ...]      # how people write the country (English, native, other languages, Chinese)
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
_UK_PC = r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}"
_OSM = ("osm",)

_LIST = [
    Country("AD", "安道尔", ("andorra", "安道爾"), r"AD\s?\d{3}", _OSM),
    Country("AL", "阿尔巴尼亚", ("albania", "shqiperia", "shqiperi", "albanien", "albanie", "阿爾巴尼亞"), _D4, _OSM),
    Country("AT", "奥地利", ("austria", "osterreich", "oesterreich", "autriche", "奧地利"), _D4, ("at_bev", "overture", "osm")),
    Country("AX", "奥兰群岛", ("aland", "aland islands", "ahvenanmaa", "奧蘭群島"), r"22\d{3}", _OSM),
    Country("BA", "波黑", ("bosnia and herzegovina", "bosnia herzegovina", "bosnia", "bosna i hercegovina",
                         "波斯尼亚和黑塞哥维那", "波赫", "波士尼亞與赫塞哥維納"), _D5, _OSM),
    Country("BE", "比利时", ("belgium", "belgique", "belgie", "belgien", "belgio", "belgica", "比利時"), _D4,
            ("be_register", "overture", "osm")),
    Country("BG", "保加利亚", ("bulgaria", "bulgariya", "bulgarien", "bulgarie", "българия", "保加利亞"), _D4, _OSM),
    Country("CH", "瑞士", ("switzerland", "schweiz", "suisse", "svizzera", "swiss", "suiza", "svizra"), _D4,
            ("ch_geoadmin", "overture", "osm")),
    Country("CY", "塞浦路斯", ("cyprus", "kypros", "κυπρος", "zypern", "chypre", "chipre", "賽普勒斯", "塞浦路斯共和国"),
            _D4, _OSM),
    Country("CZ", "捷克", ("czech republic", "czechia", "czech", "czech rep", "cesko", "ceska republika", "tschechien",
                         "republique tcheque", "chequia", "republica checa", "捷克共和国", "捷克共和國"), r"\d{3}\s?\d{2}",
            ("cz_ruian", "overture", "osm")),
    Country("DE", "德国", ("germany", "deutschland", "bundesrepublik deutschland", "federal republic of germany", "brd",
                         "allemagne", "alemania", "germania", "德國", "德意志联邦共和国", "德意志聯邦共和國"), _D5,
            ("overture", "osm")),
    Country("DK", "丹麦", ("denmark", "danmark", "danemark", "dinamarca", "danimarca", "丹麥"), _D4, ("overture", "osm")),
    Country("EE", "爱沙尼亚", ("estonia", "eesti", "estland", "estonie", "愛沙尼亞"), _D5, ("ee_inads", "overture", "osm")),
    Country("ES", "西班牙", ("spain", "espana", "spanien", "espagne", "spagna", "reino de espana"), _D5,
            ("es_cartociudad", "overture", "osm")),
    Country("FI", "芬兰", ("finland", "suomi", "finnland", "finlande", "finlandia", "芬蘭"), _D5, ("fi_ryhti", "overture", "osm")),
    Country("FO", "法罗群岛", ("faroe islands", "faroe is", "faeroe islands", "faroes", "foroyar", "法羅群島"), r"\d{3}",
            ("overture", "osm")),
    Country("FR", "法国", ("france", "frankreich", "francia", "republique francaise", "法國"), _D5,
            ("fr_ban", "overture", "osm"), number_first=True),
    Country("GB", "英国", ("united kingdom", "uk", "u k", "great britain", "britain", "england", "scotland", "wales",
                         "northern ireland", "n ireland", "great britain and northern ireland",
                         "united kingdom of great britain and northern ireland", "grossbritannien", "royaume uni",
                         "reino unido", "regno unito", "vereinigtes konigreich", "英國", "英格兰", "英格蘭", "苏格兰",
                         "蘇格蘭", "威尔士", "威爾斯", "北爱尔兰", "北愛爾蘭", "大不列颠", "大不列顛", "联合王国",
                         "聯合王國"), _UK_PC, _OSM, number_first=True),
    Country("GG", "根西岛", ("guernsey", "bailiwick of guernsey", "根西島"), r"GY\d{1,2}\s*\d[A-Z]{2}", _OSM, number_first=True),
    Country("GI", "直布罗陀", ("gibraltar", "直布羅陀"), r"GX11\s*1AA", _OSM, number_first=True),
    Country("GR", "希腊", ("greece", "hellas", "ellada", "ελλαδα", "hellenic republic", "griechenland", "grece", "grecia",
                         "希臘"), r"\d{3}\s?\d{2}", _OSM),
    Country("HR", "克罗地亚", ("croatia", "hrvatska", "kroatien", "croatie", "croacia", "克羅地亞", "克羅埃西亞"), _D5,
            ("overture", "osm")),
    Country("HU", "匈牙利", ("hungary", "magyarorszag", "ungarn", "hongrie", "hungria", "ungheria"), _D4, _OSM),
    Country("IE", "爱尔兰", ("ireland", "eire", "republic of ireland", "irland", "irlande", "irlanda", "愛爾蘭"),
            r"[AC-FHKNPRTV-Y]\d{2}\s?[0-9AC-FHKNPRTV-Y]{4}|D6W\s?[0-9AC-FHKNPRTV-Y]{4}", _OSM, number_first=True),
    Country("IM", "马恩岛", ("isle of man", "马恩岛", "曼岛", "馬恩島"), r"IM\d{1,2}\s*\d[A-Z]{2}", _OSM, number_first=True),
    Country("IS", "冰岛", ("iceland", "island", "islande", "islandia", "冰島"), r"\d{3}", ("is_hms", "overture", "osm")),
    Country("IT", "意大利", ("italy", "italia", "italien", "italie", "義大利"), _D5, ("overture", "osm")),
    Country("JE", "泽西岛", ("jersey", "bailiwick of jersey", "澤西島"), r"JE\d\s*\d[A-Z]{2}", _OSM, number_first=True),
    Country("LI", "列支敦士登", ("liechtenstein", "列支敦斯登"), _D4, ("overture", "osm")),
    Country("LT", "立陶宛", ("lithuania", "lietuva", "litauen", "lituanie", "lituania"), r"(?:LT-?)?\d{5}", ("overture", "osm")),
    Country("LU", "卢森堡", ("luxembourg", "letzebuerg", "luxemburg", "lussemburgo", "luxemburgo", "盧森堡"), r"(?:L-?)?\d{4}",
            ("lu_geoportail", "overture", "osm"), number_first=True),
    Country("LV", "拉脱维亚", ("latvia", "latvija", "lettland", "lettonie", "letonia", "拉脫維亞"), r"(?:LV-?)?\d{4}",
            ("lv_vzd", "overture", "osm")),
    Country("MC", "摩纳哥", ("monaco", "摩納哥"), r"980\d{2}", _OSM, number_first=True),
    Country("MD", "摩尔多瓦", ("moldova", "republic of moldova", "moldawien", "摩爾多瓦"), r"(?:MD-?)?\d{4}", _OSM),
    Country("ME", "黑山", ("montenegro", "crna gora", "црна гора", "蒙特內哥羅"), _D5, _OSM),
    Country("MK", "北马其顿", ("north macedonia", "macedonia", "северна македонија", "македонија", "nordmazedonien",
                           "马其顿", "北馬其頓", "馬其頓"), _D4, _OSM),
    Country("MT", "马耳他", ("malta", "馬爾他", "馬耳他"), r"[A-Z]{3}\s?\d{2,4}", _OSM, number_first=True),
    Country("NL", "荷兰", ("netherlands", "the netherlands", "nederland", "holland", "kingdom of the netherlands",
                         "niederlande", "pays bas", "paises bajos", "paesi bassi", "荷蘭"), r"\d{4}\s?[A-Z]{2}",
            ("nl_pdok", "overture", "osm")),
    Country("NO", "挪威", ("norway", "norge", "noreg", "norwegen", "norvege", "noruega", "norvegia"), _D4,
            ("no_kartverket", "overture", "osm")),
    Country("PL", "波兰", ("poland", "polska", "polen", "pologne", "polonia", "波蘭"), r"\d{2}-\d{3}", ("pl_gugik", "overture", "osm")),
    Country("PT", "葡萄牙", ("portugal", "portogallo"), r"\d{4}-\d{3}", ("overture", "osm")),
    Country("RO", "罗马尼亚", ("romania", "rumanien", "roumanie", "rumania", "羅馬尼亞"), r"\d{6}", _OSM),
    Country("RS", "塞尔维亚", ("serbia", "srbija", "србија", "serbien", "serbie", "塞爾維亞"), _D5, ("overture", "osm")),
    Country("SE", "瑞典", ("sweden", "sverige", "schweden", "suede", "suecia", "svezia"), r"\d{3}\s?\d{2}", _OSM),
    Country("SI", "斯洛文尼亚", ("slovenia", "slovenija", "slowenien", "slovenie", "eslovenia", "斯洛維尼亞", "斯洛文尼亞"),
            r"(?:SI-?)?\d{4}", ("si_gurs", "overture", "osm")),
    Country("SK", "斯洛伐克", ("slovakia", "slovensko", "slovak republic", "slowakei", "slovaquie", "eslovaquia"),
            r"\d{3}\s?\d{2}", ("overture", "osm")),
    Country("SM", "圣马力诺", ("san marino", "聖馬利諾", "聖馬力諾"), r"4789\d", _OSM),
    Country("UA", "乌克兰", ("ukraine", "ukraina", "україна", "украина", "ucrania", "ucraina", "烏克蘭"), _D5, _OSM),
    Country("VA", "梵蒂冈", ("vatican", "vatican city", "holy see", "citta del vaticano", "梵蒂岡"), r"00120", _OSM),
    Country("XK", "科索沃", ("kosovo", "kosova"), _D5, _OSM),
    Country("JP", "日本", ("japan", "nippon", "nihon", "japon", "giappone", "日本国", "日本國"), r"\d{3}-?\d{4}", ("jp_gsi",)),
]

COUNTRIES: dict[str, Country] = {c.code: c for c in _LIST}

# Recognised, but not covered: an address naming one of these is refused with a clear message instead of going
# through the US pipeline (where a Canadian street could match an American one).
OTHER_COUNTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "CA": ("加拿大", ("canada",)), "MX": ("墨西哥", ("mexico",)), "AU": ("澳大利亚", ("australia", "澳洲", "澳大利亞")),
    "NZ": ("新西兰", ("new zealand", "紐西蘭", "新西蘭")), "CN": ("中国", ("china", "people's republic of china", "中國",
                                                                   "中华人民共和国", "中華人民共和國")),
    "HK": ("香港", ("hong kong",)), "MO": ("澳门", ("macau", "macao", "澳門")), "TW": ("台湾", ("taiwan", "臺灣", "台灣")),
    "KR": ("韩国", ("south korea", "korea", "republic of korea", "韓國", "大韩民国", "大韓民國")),
    "SG": ("新加坡", ("singapore",)), "MY": ("马来西亚", ("malaysia", "馬來西亞")), "TH": ("泰国", ("thailand", "泰國")),
    "VN": ("越南", ("vietnam", "viet nam")), "PH": ("菲律宾", ("philippines", "菲律賓")),
    "ID": ("印度尼西亚", ("indonesia", "印尼", "印度尼西亞")), "IN": ("印度", ("india",)), "BR": ("巴西", ("brazil", "brasil")),
    "AR": ("阿根廷", ("argentina",)), "CL": ("智利", ("chile",)), "CO": ("哥伦比亚", ("colombia", "哥倫比亞")),
    "ZA": ("南非", ("south africa",)), "AE": ("阿联酋", ("united arab emirates", "uae", "阿拉伯联合酋长国", "阿聯酋")),
    "SA": ("沙特阿拉伯", ("saudi arabia", "沙特", "沙烏地阿拉伯")), "IL": ("以色列", ("israel",)), "EG": ("埃及", ("egypt",)),
    "RU": ("俄罗斯", ("russia", "russian federation", "россия", "俄羅斯")), "BY": ("白俄罗斯", ("belarus", "беларусь", "白俄羅斯")),
    "TR": ("土耳其", ("turkey", "turkiye")), "KZ": ("哈萨克斯坦", ("kazakhstan", "哈薩克")),
    "AM": ("亚美尼亚", ("armenia", "亞美尼亞")), "AZ": ("阿塞拜疆", ("azerbaijan", "亞塞拜然")),
}

_ISO3 = {
    "AND": "AD", "ALB": "AL", "AUT": "AT", "ALA": "AX", "BIH": "BA", "BEL": "BE", "BGR": "BG", "CHE": "CH", "CYP": "CY",
    "CZE": "CZ", "DEU": "DE", "DNK": "DK", "EST": "EE", "ESP": "ES", "FIN": "FI", "FRO": "FO", "FRA": "FR", "GBR": "GB",
    "GGY": "GG", "GIB": "GI", "GRC": "GR", "HRV": "HR", "HUN": "HU", "IRL": "IE", "IMN": "IM", "ISL": "IS", "ITA": "IT",
    "JEY": "JE", "LIE": "LI", "LTU": "LT", "LUX": "LU", "LVA": "LV", "MCO": "MC", "MDA": "MD", "MNE": "ME", "MKD": "MK",
    "MLT": "MT", "NLD": "NL", "NOR": "NO", "POL": "PL", "PRT": "PT", "ROU": "RO", "SRB": "RS", "SWE": "SE", "SVN": "SI",
    "SVK": "SK", "SMR": "SM", "UKR": "UA", "VAT": "VA", "XKX": "XK", "JPN": "JP", "USA": "US",
}
# Codes people use that are not the ISO code: only read from the country field, never from address text
_FIELD_CODES = {"UK": "GB", "EL": "GR", "PR": "US", "GU": "US", "VI": "US", "AS": "US", "MP": "US", "UM": "US"}
# City-states: the name after the postcode is the city ("L-1648 Luxembourg", "98000 Monaco")
_CITY_STATE_NAMES = {"luxembourg", "luxemburg", "letzebuerg", "monaco", "san marino", "vatican", "vatican city",
                     "citta del vaticano", "gibraltar", "andorra"}
# Country names that are also surnames or US town names (Holland MI, Greece NY, Mexico MO): after a US state + ZIP
# they are the recipient or the town, and on their own after a street they are taken for a US town
_AMBIGUOUS = {"holland", "england", "wales", "scotland", "britain", "ireland", "jersey", "guernsey", "poland",
              "norway", "denmark", "sweden", "finland", "belgium", "luxemburg", "malta", "france", "island", "china",
              "mexico", "brazil", "chile", "india", "israel", "jordan", "greece", "italy", "switzerland", "macedonia",
              "san marino"}
# ... of which these are only US towns, not surnames: after a state + ZIP they are the country ("Trento TN 38122, Italy")
_TOWN_NAMES = {"greece", "italy", "switzerland", "macedonia", "san marino"}
# A country name after one of these is part of a place name: "New Holland", "Little Italy", "North Wales"
_QUALIFIERS = {"new", "little", "north", "south", "east", "west", "upper", "lower", "great", "old", "rhode", "staten",
               "long", "mercer", "grand", "rock", "marco"}


def fold(text: str | None) -> str:
    """Lower-case, accents removed, punctuation to spaces: 'Österreich' -> 'osterreich', 'Straße' -> 'strasse'."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = text.translate(_SPECIAL)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


_SPECIAL = str.maketrans({"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "đ": "d", "ł": "l", "þ": "th", "ð": "d", "ı": "i"})

_US_NAMES = ("usa", "us", "u s", "u s a", "united states", "united states of america", "america", "美国", "美國",
             "美利坚合众国", "美利堅合眾國", "puerto rico", "guam", "virgin islands", "us virgin islands",
             "u s virgin islands", "american samoa", "northern mariana islands", "波多黎各", "关岛", "關島")
_BY_NAME = {fold(n): c.code for c in _LIST for n in (*c.names, c.zh)}
_BY_NAME.update({fold(n): code for code, (zh, names) in OTHER_COUNTRIES.items() for n in (*names, zh)})
_BY_NAME.update({fold(n): "US" for n in _US_NAMES})
# Chinese names, which may follow the city without a space or comma: "Berlin 德国", "柏林德国"
_CJK_NAMES = sorted((n for n in _BY_NAME if not n.isascii() and re.fullmatch(r"[一-鿿]+", n)), key=len, reverse=True)
_CJK_TAIL_RE = re.compile(rf"(?:{'|'.join(_CJK_NAMES)})$")
_CJK_HEAD_RE = re.compile(rf"^(?:{'|'.join(_CJK_NAMES)})")


def supported(code: str | None) -> bool:
    return code == "US" or code in COUNTRIES


def country_name(code: str) -> str:
    """'DE' -> '德国', 'CA' -> '加拿大'."""
    if code in COUNTRIES:
        return COUNTRIES[code].zh
    return OTHER_COUNTRIES[code][0] if code in OTHER_COUNTRIES else "美国" if code == "US" else code


def _code(text: str, *, field: bool) -> str | None:
    """'DE' / 'U.K.' / 'DEU' -> the country. In address text only upper-case codes count, and never one that is
    also a US state: 'DE' after 'Wilmington' is Delaware, 'AL' Alabama, 'MD' Maryland, 'ME' Maine, 'MT' Montana."""
    letters = re.sub(r"[\s.]", "", text)
    if not (letters.isascii() and letters.isalpha() and len(letters) in (2, 3)):
        return None
    up = letters.upper()
    if not field and letters != up:
        return None
    if len(up) == 3:
        return _ISO3.get(up)
    if not field and up in _STATES:
        return None
    if up in COUNTRIES or up == "US":
        return up
    return _FIELD_CODES.get(up) if field else {"UK": "GB"}.get(up)


def known_country(value: str | None) -> str | None:
    """Any country this service recognises, covered or not: 'de' / 'DEU' / 'Germany' / '德國' -> 'DE',
    'UK' -> 'GB', 'U.S.A.' / 'PR' -> 'US', 'Canada' -> 'CA'; None when unknown."""
    v = unicodedata.normalize("NFKC", value or "").strip()
    if not v:
        return None
    code = _code(v, field=True) or _BY_NAME.get(fold(v)) or _BY_NAME.get(fold(v.replace(".", "")))
    if code is None:  # ERP labels: '美国(US)', 'US(美国)', 'Germany (DE)', 'DE - Germany' - the parts must agree
        parts = {_code(p, field=True) or _BY_NAME.get(fold(p)) for p in re.split(r"[()\[\]【】/|,;:]|\s[-–—]\s", v)
                 if fold(p)} - {None}
        if len(parts) == 1:
            code = parts.pop()
    return code


def resolve_country(value: str | None) -> str | None:
    """The country field -> a covered country's code ('US' for the US), None when unknown or not covered. Two
    letters are read as an ISO code, so 'DE' means Germany here (in an address line it is Delaware, see
    country_in_text)."""
    code = known_country(value)
    return code if supported(code) else None


# --------------------------------------------------------------------------------------- address text

_PHONE_LABEL = r"(?:tel|telephone|phone|ph|mobile|mob|cell|fax|电话|手机|電話|手機|联系电话|聯絡電話)\.?\s*[:：]?\s*"
# what can follow the country at the end of a line: "(Max Mustermann)", "+49 30 1234567", "Tel: 030 123456"
_TRAIL_RE = re.compile(rf"\s*(?:[(\[【][^()\[\]【】]*[)\]】]|(?:{_PHONE_LABEL})?\+\d[\d\s().\-/]{{6,}}"
                       rf"|{_PHONE_LABEL}\d[\d\s().\-/]{{6,}})\s*$", re.I)
_US_STATE_ZIP_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_STATES | {"AA", "AE", "AP"})) + r")\s+\d{5}(?:-\d{4})?(?![\d-])"  # "AZ 85007", "APO AE 09012"
    r"|(?:^|[,(])\s*(?i:" + "|".join(sorted(_STATES)) + r")\s+\d{5}(?:-\d{4})?(?![\d-])"  # ", az 85007"
    r"|\s(?i:" + "|".join(sorted(_STATES)) + r")\s+\d{5}(?:-\d{4})?\s*$"                 # "... new york ny 10118"
    r"|(?i:\b(?:" + "|".join(n.replace(" ", r"\s+") for n in sorted(STATE_NAMES, key=len, reverse=True))
    + r"))\s+\d{5}(?:-\d{4})?(?![\d-])")
# ZIP3 ranges of the US states whose code is also a European country or province: "Milano MI 20121" is Milan, as
# the ZIP can't be Michigan's (480-499); "80331 München, DE" is Germany, as Delaware's ZIPs are 197-199
_ZIP3 = {"AL": ((350, 369),), "AR": ((716, 729),), "CA": ((900, 961),), "CO": ((800, 816),), "CT": ((60, 69),),
         "DE": ((197, 199),), "FL": ((320, 339), (341, 349)), "GU": ((969, 969),), "MA": ((10, 27), (55, 55)),
         "MD": ((206, 219),), "ME": ((39, 49),), "MI": ((480, 499),), "MN": ((550, 567),), "MO": ((630, 658),),
         "MS": ((386, 397),), "MT": ((590, 599),), "NE": ((680, 693),), "NH": ((30, 38),), "OR": ((970, 979),),
         "PA": ((150, 196),), "PR": ((6, 7), (9, 9)), "RI": ((28, 29),), "TN": ((370, 385),), "UT": ((840, 847),),
         "VA": ((201, 201), (220, 246)), "VI": ((8, 8),), "VT": ((50, 59),), "AP": ((962, 966),)}
# ... and the leading digits of the postcodes of those whose code is also a country with postcodes of the same shape:
# "50122 Firenze FI" is Florence, "00100 Helsinki FI" Finland; "3011 Bern BE" is Bern, "1000 Bruxelles BE" Belgium
_PROVINCE_PC = {("IT", "BA"): "70", ("IT", "CZ"): "88", ("IT", "FI"): "50", ("IT", "FR"): "03", ("IT", "GR"): "58",
                ("IT", "LT"): "04", ("IT", "MC"): "62", ("IT", "ME"): "98", ("IT", "VA"): "21", ("ES", "BA"): "06",
                ("ES", "GR"): "18", ("ES", "SE"): "41", ("CH", "BE"): "2[5-7]|3[0-8]|49", ("CH", "LU"): "6[0-2]",
                ("NL", "LI"): "59|6[0-4]"}
# street words of those countries, which tell such a province code from the country with no batch default
_STREET_WORDS = {
    "IT": re.compile(r"\b(?:via|viale|piazza|piazzale|corso|largo|vicolo|strada|contrada|lungomare|borgo)\b", re.I),
    "ES": re.compile(r"\b(?:calle|c/|avenida|avda|plaza|paseo|carrer|camino|carretera|ronda|travesia|rambla)\b", re.I),
    "CH": re.compile(r"\w(?:strasse|straße|gasse|weg|platz)\b", re.I),  # not "rue": "Rue Royale 1, 4900 Spa BE"
    "NL": re.compile(r"\w(?:straat|weg|plein|laan|gracht|kade|dijk)\b", re.I),
}


def _province_postcode(country: str, prov: str, text: str) -> bool:
    """A postcode of `country` in the text that fits province `prov` (True when the table doesn't know it)."""
    if (rng := _PROVINCE_PC.get((country, prov))) is None:
        return True
    return any(re.match(rf"(?:{rng})", m.group()) for m in _postcode_re(country).finditer(text))


def us_zip_fits(state: str | None, zip_: str | None) -> bool:
    """False only for a 5-digit code that can't be that state's ZIP ('MI' + '20121')."""
    st, z = state_abbr(state) or (state or "").strip().upper(), re.sub(r"\D", "", zip_ or "")[:5]
    return not (st in _ZIP3 and len(z) == 5 and not any(a <= int(z[:3]) <= b for a, b in _ZIP3[st]))


_US_STATE_END_RE = re.compile(r"\s(?:" + "|".join(sorted(_STATES)) + r")$")  # "Holland MI"
_US_TYPE = (r"(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|ln|lane|way|ct|court|pl|place|hwy|highway|pkwy"
            r"|parkway|cir|circle|ter|terrace|trl|trail|sq|square)")
# a part that is a US street line: "270 S River Ave", "100 Main St Apt 4" (not "Damrak 1 Amsterdam", "Rua Augusta 1")
_US_STREET_LINE_RE = re.compile(rf"\b{_US_TYPE}\.?(?:\s+(?:[NSEW]|NE|NW|SE|SW)\.?)?"
                                r"(?:\s+(?:apt|ste|suite|unit|#)\.?\s*[\w-]+)?\s*$", re.I)
# a US-shaped street anywhere: house number first, then the name and a street type ("1301 E 6th Ave"); "PO Box 12"
_US_SHAPED_RE = re.compile(rf"(?:^|[,;\n])\s*(?:\d+[A-Za-z]?(?:[-/]\d+[A-Za-z]?)?\s+[^,;\n]*?[^\s,;]\s+{_US_TYPE}"
                           r"\b\.?(?![\w'-])|p\.?\s*o\.?\s*box\s+\d)", re.I)
# ... after a unit, or with the type right after the number: "Ste 5 100 Main St", "#5 100 Main St", "100 Avenue of the
# Americas" (a Japanese "9-1 Akashicho" has a hyphen)
_US_UNIT_STREET_RE = re.compile(rf"(?:^|[,;\n])\s*(?:(?:ste|suite|apt|unit|#)\s*[\w-]+\s+)?\d+[A-Za-z]?\s+(?:[^,;\n]*?\s)?"
                                rf"{_US_TYPE}\b\.?(?![\w'-])", re.I)
_UNIT_PART_RE = re.compile(r"\s*(?:ste|suite|apt|unit|box|bldg|rm|room|fl|floor|lot|#)\b", re.I)  # "Ste 100", "Box 123"


def noise_segment(seg: str) -> bool:
    """A phone number, e-mail address or bracketed note written as its own part of the address."""
    s = seg.strip()
    if re.fullmatch(r"[(\[【].*[)\]】]", s) or re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", s):
        return True
    m = re.fullmatch(rf"(?:{_PHONE_LABEL})?(\+?[\d\s().\-/]+)", s, re.I)
    return bool(m) and len(re.sub(r"\D", "", m.group(1))) >= 8 and not re.fullmatch(r"\d{5}-\d{4}", s)


def _segments(text: str) -> list[str]:
    segs = [s.strip() for s in re.split(r"[,;\n、]", text)]
    segs = [s for s in segs if fold(s)]
    while len(segs) > 1 and noise_segment(segs[-1]):
        segs.pop()
    return segs


def _untail(seg: str) -> str:
    while (m := _TRAIL_RE.search(seg)) and m.start() > 0:
        seg = seg[: m.start()]
    return seg.strip()


def _postcode_re(code: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-])(?:{COUNTRIES[code].postcode})(?![\w-])", re.I)


def _postcode_after_words(code: str, seg: str) -> bool:
    """The country's postcode after other words, so not a leading house number: 'Damrak 1 1012 LG Amsterdam'."""
    return code in COUNTRIES and any(m.start() > 0 for m in _postcode_re(code).finditer(seg))


def postcode_in(code: str, text: str | None) -> bool:
    """The country's postcode in a part after the first one: 'Via Roma 10, 20121 Milano MI'."""
    if code not in COUNTRIES:
        return False
    return any(_postcode_re(code).search(s) for s in _segments(unicodedata.normalize("NFKC", text or ""))[1:])


def _tail(seg: str, text: str, alone: bool) -> str | None:
    """A country after the town in the same part: "10117 Berlin Germany", "Berlin 德国", "London SW1A 2AA UK",
    "Oslo Norway". A name that is also a US town or surname (Holland, Poland, Ireland) counts only after a street
    part or with its postcode, and none after "New" / "Little" / "North" / "Rhode": "New Holland", "Little Italy",
    "North Wales" and "Rhode Island" stay American. `alone`: the part is the whole line."""
    if m := _CJK_TAIL_RE.search(seg):
        if m.start() > 0:
            return _BY_NAME[m.group()]
    words = seg.split()
    for n in (3, 2, 1):
        if len(words) <= n:
            continue
        name = fold(" ".join(words[-n:]))
        code = _BY_NAME.get(name)
        if code is None and n == 1 and (code := _code(words[-1], field=False)) in COUNTRIES:
            # "75001 Paris FR", "10117 Berlin DEU": a code (never a US state) after that country's postcode
            body = " ".join(words[:-1])
            if not (_postcode_after_words(code, body) if alone else _postcode_re(code).search(body)):
                return None
            # ... unless it is a province after its own postcode and a street of that country: "Via Roma 1, 50122
            # Firenze FI" is Florence, "Calle Sierpes 1, 41004 Sevilla SE" Seville, "Pilatusstrasse 1, 6003 Luzern LU"
            return next((c for c, words_re in _STREET_WORDS.items() if (c, code) in _PROVINCE_PC
                         and words_re.search(text) and _province_postcode(c, code, body)
                         and _postcode_re(c).search(body)), code)
        if code not in COUNTRIES:
            continue
        if fold(words[-n - 1]) in _QUALIFIERS or (name == "island" and "ísland" not in seg.casefold()):
            return None
        if _postcode_re(code).search(text) or (name not in _AMBIGUOUS and (len(name) > 3 or name == "uk")) or (
                name in _AMBIGUOUS and not alone):
            return code
        return None
    return None


def _named_before_postcode(segs: list[str]) -> str | None:
    """"..., Berlin, Germany 10117", "..., Oxford, England, OX1 4AA": the country, then its postcode."""
    last = segs[-1].split()
    tries = [(" ".join(last[:-k]), " ".join(last[-k:])) for k in (1, 2) if len(last) > k]
    if len(segs) > 2:
        tries.append((segs[-2], segs[-1]))
    for name, pc in tries:
        code = _BY_NAME.get(fold(name))
        if fold(name) in _AMBIGUOUS and (len(segs) < 3 or re.fullmatch(r"\d{5}", pc.strip())):
            continue  # "1 Main St, Greece, 14612": Greece NY and its ZIP
        if code in COUNTRIES and re.fullmatch(
                rf"(?:{COUNTRIES[code].postcode})", pc.strip(), re.I):
            return code
    return None


def _misfit_state_zip(text: str | None) -> bool:
    """Every "state + ZIP" in the line is a province + postcode that no ZIP of that state fits: "Milano MI 20121"."""
    hits = [re.search(r"([A-Za-z][A-Za-z\s]*?)\s+(\d{5})", m.group())
            for m in _US_STATE_ZIP_RE.finditer(unicodedata.normalize("NFKC", text or ""))]
    return bool(hits) and all(h and not us_zip_fits(h.group(1), h.group(2)) for h in hits)


# a US unit or mail-drop part: "Dept 4500", "Gate 12345", "Bay 1234" (also a Maltese postcode's shape), "PMB 123"
_US_UNIT_RE = re.compile(r"\s*(?:ste|suite|apt|unit|box|bldg|rm|room|fl|floor|lot|dept|dock|gate|door|space|spc|pier|bay"
                         r"|office|ofc|mailstop|ms|pmb|trlr|hngr|hangar|slip|rte|hwy|recv|rcv)\b|\s*#", re.I)
# Montenegro's real postcodes (81000 Podgorica ... 85000 Bar), not any 5 digits: "98122 Messina ME" is Messina
_STATE_COUNTRY_PC = {"ME": r"8[1-5]\d{3}"}


def _malta_postcode(st: str, segs: list[str]) -> bool:
    """'MT' after a real Maltese postcode, upper-case, after the town: "12 Tower Road, Sliema SLM 1549, MT"."""
    body = segs[-1][: -len(segs[-1].split()[-1])].strip()
    return st == "MT" and any((m := re.search(r"\S\s+([A-Z]{3})\s?\d{4}$", s.strip())) and not _US_UNIT_RE.fullmatch(
        m.group(1)) for s in (segs[-2] if len(segs) > 2 and not body else "", body))


def _code_after_postcode(st: str, segs: list[str]) -> bool:
    """A trailing 'DE' / 'ME' / 'MT' (also US states) is the country after that country's postcode written as a
    number of its own that no ZIP of the state fits, in a line with no US street: "Hauptstraße 5, 80331 München, DE",
    "Bulevar Svetog Petra Cetinjskog 1, 81000 Podgorica, ME" - not "1 Main St, Dover, 19901, DE" or
    "1301 E 6th Ave, Ste 100, Helena, MT"."""
    if _US_SHAPED_RE.search(", ".join(segs)) and not _malta_postcode(st, segs):
        return False
    pc = _STATE_COUNTRY_PC.get(st, COUNTRIES[st].postcode)
    body = segs[-1][: -len(segs[-1].split()[-1])].strip()  # "98122 Messina" of "98122 Messina ME"
    # "80331", or "80331 München" / "Valletta VLT 1117" after the street line (the first part with a number) or right
    # before a code of its own - never the street line, a unit ("Dept 4500", "Gate 12345") or a street after a name
    # ("John Smith, 1234 Oak Loop, Birmingham, AL", "Acme Inc\n1234 Oak Loop\nBirmingham AL")
    street = next((i for i, s in enumerate(segs) if re.search(r"\d", s) and not _US_UNIT_RE.match(s)), 0)
    hits = [m.group(1) for i, s in enumerate(segs[1:-1], 1) if not _US_UNIT_RE.match(s) and (
        (m := re.fullmatch(rf"\s*({pc})\s*", s, re.I)) or ((i > street or (i == len(segs) - 2 and not body)) and (
            m := re.match(rf"\s*({pc})\s+\D", s, re.I) or re.search(rf"(?<=\s)({pc})\s*$", s, re.I))))]
    lead = r"(?:^|\s)" if len(segs) > 1 else r"\s"  # one part: after the street, "Hauptstraße 5 10115 Berlin DE"
    # ... and in one part only after the house number: "Hauptstraße 5 80331 München DE", not the house number itself
    # ("John Smith 1234 Oak Loop Birmingham AL")
    if body and (m := re.search(rf"{lead}({pc})(?:\s+\D[^\d]*)?$", body, re.I)) and (
            len(segs) > 1 or re.search(r"\d", body[: m.start(1)])) and not (
            (prev := body[: m.start(1)].split()) and _US_UNIT_RE.fullmatch(prev[-1])):  # "... Dept 4500 Birmingham AL"
        hits.append(m.group(1))
    return any(not _UNIT_PART_RE.match(h) and not (len(z := re.sub(r"\D", "", h)) == 5 and us_zip_fits(st, z))
               for h in hits)


def country_in_text(text: str | None) -> str | None:
    """The country an address line names: "..., 10117 Berlin, Germany" -> 'DE', ", GB" -> 'GB', a Japanese address
    -> 'JP', a US state + ZIP -> 'US'; None when it names none (the caller's default, else the US pipeline, then
    answers). A country that is not covered comes back too ('CA'), for the caller to refuse.

    US addresses must never leave the US pipeline: a trailing 'DE' / 'MD' / 'ME' is a US state, 'Rhode Island' is
    not Iceland, and once a US state + ZIP appears ("AZ 85007") only a whole, unambiguous country name after it
    counts - a recipient called Holland, a Chinese note (小区, 知道) or a katakana name does not. A town named like a
    country right after the street ("270 S River Ave, Holland", "300 N Coal St, Mexico") is a US town too."""
    t = unicodedata.normalize("NFKC", text or "").strip()
    segs = _segments(t)
    if not segs:
        return None
    last = _untail(segs[-1])
    us_zip = bool(_US_STATE_ZIP_RE.search(t)) or (  # "..., Springfield, IL, 62701"
        len(segs) > 1 and bool(re.fullmatch(r"\d{5}(?:-\d{4})?", last)) and bool(state_abbr(segs[-2])))
    us = us_zip or bool(state_abbr(last) or _US_STATE_END_RE.search(last))
    name = fold(last)
    code = _code(last, field=False) or _BY_NAME.get(name)
    if code:
        after_state = len(segs) > 1 and bool(state_abbr(_untail(segs[-2])))  # "..., Boston, MA, IT"
        if (after_state and not us and code in COUNTRIES and postcode_in(code, t) and not _US_SHAPED_RE.search(t)
                and not any(us_zip_fits(_untail(segs[-2]), z) for z in re.findall(r"(?<![\w-])\d{5}(?![\w-])", t))):
            return code  # a province, then the country, after its postcode: "Via Roma 10, 20121, Milano, MI, IT"
        if code == "US" or ((us or after_state) and len(name) <= 3):
            return "US"
        # a recipient or town called Holland after a US state, unless the line is foreign: "Milano, MI 20121, Italy"
        if (us or after_state) and name in _AMBIGUOUS and (_US_SHAPED_RE.search(t) or (
                us_zip and name not in _TOWN_NAMES and not _misfit_state_zip(t))):
            return "US"
        if (not us and (name in _AMBIGUOUS or code in OTHER_COUNTRIES)
                and (len(segs) == 1 or (len(segs) == 2 and _US_STREET_LINE_RE.search(segs[0])))):
            return None  # a US street, then a town named like a country: "270 S River Ave, Holland"
        return code
    if us_zip:
        return "US"
    if code := _named_before_postcode([*segs[:-1], last]):
        return code
    first = segs[0]
    if (len(segs) > 1 and (code := _BY_NAME.get(fold(first))) and code != "US" and not (us and _US_SHAPED_RE.search(t))
            and (fold(first) not in _AMBIGUOUS
                 or (code in COUNTRIES and any(_postcode_re(code).search(x) for x in segs[1:])))):
        return code  # "Germany, 10117 Berlin, Unter den Linden 77", "France, 75001 Paris, ..." (not "Italia, 1 Main St, IL")
    if us:
        st = re.sub(r"[\s.]", "", last.split()[-1]).upper()
        if st in COUNTRIES and _code_after_postcode(st, [*segs[:-1], last]):
            return st  # "Hauptstraße 5, 80331 München, DE"
        if (m := _CJK_HEAD_RE.match(first)) and postcode_in(_BY_NAME[m.group()], t):
            return _BY_NAME[m.group()]  # "意大利 Via Roma 10, 20121 Milano MI"
        return None
    if code := _tail(last, t, alone=len(segs) == 1):
        return code
    if m := _CJK_HEAD_RE.match(first):
        return _BY_NAME[m.group()]  # "德国 柏林 10117 ...", "日本大阪市..."
    return "JP" if is_japanese(t) else None


_CROWN_RE = re.compile(r"(?<![A-Z0-9])(JE|GY|IM)\d{1,2}\s*\d[A-Z]{2}(?![A-Z0-9])", re.I)


def crown_dependency(code: str, text: str | None) -> str:
    """'GB' with a Jersey / Guernsey / Isle of Man postcode, or that name as a part of its own ("St Helier, Jersey")
    -> 'JE' / 'GG' / 'IM': they are not in the UK's data. "1 Jersey Road, London" stays GB."""
    if code != "GB":
        return code
    if m := _CROWN_RE.search(text or ""):
        return {"JE": "JE", "GY": "GG", "IM": "IM"}[m.group(1).upper()]
    names = {fold(s) for s in _segments(unicodedata.normalize("NFKC", text or ""))}
    return next((c for c in ("JE", "GG", "IM") if names & {fold(n) for n in COUNTRIES[c].names}), code)


def strip_country(segments: list[str], code: str = "") -> tuple[list[str], list[str]]:
    """Drop the country (a part of its own, or the last words of the last part, or the first part) and anything
    after it that is not address (a phone number, a note in brackets). `code` is the address's own country, whose
    code is dropped even where it is also a US state ("..., 10117, DE"). -> (segments, what was removed)."""
    segs, removed = list(segments), []
    while len(segs) > 1 and noise_segment(segs[-1]):
        removed.insert(0, segs.pop())
    if segs:
        tail = _untail(segs[-1])
        if tail != segs[-1]:
            removed.insert(0, segs[-1][len(tail):].strip())
            segs[-1] = tail
    own = {code, next((k for k, v in _ISO3.items() if v == code), "")} - {""}
    while segs and (_code(segs[-1], field=False) or fold(segs[-1]) in _BY_NAME
                    or re.sub(r"[\s.]", "", segs[-1]).upper() in own):
        removed.insert(0, segs.pop())
    if segs:
        last = segs[-1]
        if (m := _CJK_TAIL_RE.search(last)) and m.start() > 0:
            removed.insert(0, m.group())
            segs[-1] = last[: m.start()].strip()
        else:
            words = last.split()
            for n in (3, 2, 1):
                name = fold(" ".join(words[-n:]))
                # "10117 Berlin Germany" loses Germany, but in "L-1648 Luxembourg" / "98000 Monaco" the name right
                # after the postcode is the city
                if (len(words) > n and name in _BY_NAME and name != "island" and (len(name) > 2 or name == "uk")
                        and not (name in _CITY_STATE_NAMES and re.search(r"\d", words[-n - 1]))):
                    removed.insert(0, " ".join(words[-n:]))
                    segs[-1] = " ".join(words[:-n])
                    break
    if len(segs) > 1 and fold(segs[0]) in _BY_NAME:
        removed.insert(0, segs.pop(0))
    elif segs and (m := _CJK_HEAD_RE.match(segs[0])) and segs[0][m.end():].strip():
        removed.insert(0, m.group())
        segs[0] = segs[0][m.end():].strip()
    return [s for s in segs if s], removed


def is_country_word(text: str) -> bool:
    """'Germany' / '德国' / 'GB' / 'DEU': a country, not part of the address."""
    return bool(_code(text, field=False)) or fold(text) in _BY_NAME or bool(_CJK_TAIL_RE.fullmatch(text.strip()))


def city_state(names: list[str]) -> str | None:
    """A removed 'Luxembourg' / 'Monaco' that is also the city (no other city was written)."""
    return next((n for n in names if fold(n) in _CITY_STATE_NAMES), None)


# ------------------------------------------------------------------------------------------------ Japan

# Chinese forms people type for Japanese place names (simplified, and the traditional ones Japan simplified too)
# -> the Japanese kanji. Only for recognising and, when GSI finds nothing, retrying: 叶 / 澤 / 龍 are left alone
# because Japanese names use them as well.
ZH_TO_JA = str.maketrans({
    "东": "東", "县": "県", "广": "広", "冈": "岡", "长": "長", "岛": "島", "鸟": "鳥", "儿": "児", "爱": "愛", "宫": "宮",
    "枥": "栃", "冲": "沖", "绳": "縄", "滨": "浜", "马": "馬", "贺": "賀", "库": "庫", "德": "徳", "桥": "橋", "涩": "渋",
    "银": "銀", "荣": "栄", "丰": "豊", "黑": "黒", "并": "並", "练": "練", "饰": "飾", "户": "戸", "惠": "恵", "霸": "覇",
    "泽": "沢", "泻": "潟", "须": "須", "镰": "鎌", "仓": "倉", "轻": "軽", "难": "難", "斋": "斎", "顿": "頓", "关": "関",
    "乡": "郷", "门": "門", "驿": "駅", "发": "発", "乐": "楽", "边": "辺", "汤": "湯", "鹤": "鶴", "阳": "陽", "实": "実",
    "园": "園", "团": "団", "际": "際", "间": "間", "见": "見", "云": "雲", "庆": "慶", "樱": "桜", "泷": "滝", "纪": "紀",
    "绿": "緑", "总": "総", "冢": "塚", "沟": "溝", "滩": "灘", "濑": "瀬", "鸭": "鴨", "鹰": "鷹",
    "縣": "県", "廣": "広", "區": "区", "號": "号", "濱": "浜", "櫻": "桜", "澀": "渋", "豐": "豊", "榮": "栄", "戶": "戸",
    "鄉": "郷", "關": "関", "齋": "斎", "驛": "駅", "發": "発", "樂": "楽", "團": "団", "國": "国", "條": "条", "廳": "庁",
    "瀧": "滝", "淺": "浅", "壽": "寿", "萬": "万", "藏": "蔵", "邊": "辺", "實": "実", "會": "会", "觀": "観", "鐵": "鉄",
})


def zh_to_ja(text: str) -> str:
    """'神奈川县横滨市' -> '神奈川県横浜市', '千叶县' -> '千葉県'."""
    return text.replace("千叶", "千葉").translate(ZH_TO_JA)


JP_PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県",
    "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県",
    "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県",
    "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
_JP_TEXT_RE = re.compile(
    r"[ぁ-ゖァ-ヺ]|〒|^\s*日本"                     # kana letters (not the ・ / ー a Chinese name may carry)
    rf"|{'|'.join(JP_PREFECTURES)}"
    r"|\d\s*(?:丁目|番地)|\d\s*番\s*\d")
# No prefecture: a city or ward, the town, then its number - "大阪市北区梅田3-1-1", "京都市北区金閣寺町1",
# "府中市宮西町2", maybe a room, name or postcode after it ("京都市北区金閣寺町1 田中様"); a town alone
# ("道後湯之町5", "箱根町湯本256"). A bare "N号" is Chinese (Japanese writes 番地, N番N号 or N-N).
# Chinese addresses have the same shape, but a road, park or building word before the number (南京路1-1,
# 人民南路4段1号, 科技园1-2-3), a Chinese-only word (省, 小区, 单元, 號) or a Chinese city first (北京市, 台北市).
_JP_MUNI_RE = re.compile(
    r"^(?:\d{3}-?\d{4}\s*)?(?:[一-鿿々]{2,5}\s+)?"  # a postcode or the recipient first: "山田太郎 京都市北区金閣寺町1"
    r"[一-鿿々]{1,6}?[市区郡][一-鿿々ヶケノ]{1,16}?(?:\d+条[東西南北]?)?\d+(?:\s*-\s*\d+)*(?:\s*番地?)?"  # 北1条西2-1
    r"(?:\s+(?![号號])\S+){0,3}(?:\s*[,、，]\s*〒?\d{3}-\d{4})?\s*$"  # "…町2, 606-8402"
    r"|^[一-鿿々]{2,8}?町[一-鿿々ヶケノ]{0,8}?\d+(?:\s*-\s*\d+)*(?:\s*番地?)?"
    r"(?:\s+(?:\d+号室|\S+様|〒?\d{3}-\d{4}))?\s*$"  # "道後湯之町5 101号室", "箱根町湯本256 田中様"
    r"|[一-鿿々](?:市|区|町|村|郡)[一-鿿々]{0,12}?\d+\s*-\s*\d+(?!\s*[\d号])")
_ZH_ADDRESS_RE = re.compile(r"[路巷弄园苑]\s*\d|大道\s*\d|街\s*\d+\s*号|号楼|单元|栋|幢|小区|省|自治区|大厦|号院|胡同|[圳镇號]"
                            r"|^\s*(?:[一-鿿]{2,4}\s+)?(?:中国|中國)?\s*(?:(?:北京|上海|天津|重庆|重慶|广州|廣州|深圳|杭州|南京|成都|武汉"
                            r"|武漢|西安|厦门|廈門|苏州|蘇州|宁波|寧波|青岛|青島|长沙|長沙|郑州|鄭州|沈阳|瀋陽|大连|大連|昆明|东莞|東莞"
                            r"|佛山|义乌|義烏|无锡|無錫|珠海|温州|溫州|福州|合肥|济南|濟南|哈尔滨|长春|長春|台北|臺北|新北|台中|臺中"
                            r"|台南|臺南|高雄|桃园|桃園|新竹|基隆|嘉义|嘉義|香港|澳门|澳門)(?:市|\s|$)"
                            # more Chinese / Taiwanese cities, only with 市 (中山 is also a Japanese surname)
                            r"|(?:南宁|南寧|中山|海口|宜蘭|宜兰|屏東|屏东|贵阳|貴陽|太原|惠州|石家庄|石家莊|泉州|洛阳|洛陽|汕头|汕頭"
                            r"|南昌|花蓮|花莲|彰化|兰州|蘭州|西宁|西寧|银川|銀川|乌鲁木齐|烏魯木齊|呼和浩特|拉萨|拉薩|苗栗|南投"
                            r"|台東|臺東|台东|金門|金门|常州|徐州|南通|扬州|揚州|绍兴|紹興|嘉兴|嘉興|台州|金华|金華|漳州|保定|唐山"
                            r"|烟台|煙台|潍坊|濰坊|江门|江門|湛江|三亚|三亞|桂林|柳州|襄阳|宜昌|芜湖|蕪湖|绵阳|綿陽)市"
                            r"|(?:台北|臺北)?西門町)"  # Ximending, Taipei (静岡市葵区西門町 is Japanese)
                            r"|加州|洛杉矶|洛杉磯")  # California, Los Angeles
_JP_ROMAJI_PLACES = (
    "tokyo", "osaka", "kyoto", "hokkaido", "kanagawa", "aichi", "saitama", "chiba", "hyogo", "fukuoka", "yokohama",
    "nagoya", "sapporo", "kobe", "kawasaki", "sendai", "hiroshima", "kitakyushu", "okinawa", "naha", "shizuoka",
    "hamamatsu", "niigata", "kumamoto", "kagoshima", "okayama", "gifu", "nara", "shiga", "mie", "ibaraki", "tochigi",
    "gunma", "nagano", "yamanashi", "toyama", "ishikawa", "kanazawa", "fukui", "wakayama", "tottori", "shimane",
    "yamaguchi", "tokushima", "kagawa", "ehime", "kochi", "saga", "nagasaki", "oita", "miyazaki", "aomori", "iwate",
    "miyagi", "akita", "yamagata", "fukushima", "sakai", "chuo", "minato", "shinjuku", "shibuya",
)
_JP_ROMAJI_RE = re.compile(r"\b[a-z]+-(?:ku|shi|cho|machi|gun|mura)\b|\b\d+\s*-?\s*chome\b", re.I)
_JP_PLACE_RE = re.compile(rf"\b(?:{'|'.join(_JP_ROMAJI_PLACES)})(?:-(?:to|fu|ken|shi|ku))?\b", re.I)
_JP_POSTCODE_RE = re.compile(r"(?<![\d-])\d{3}-\d{4}(?![\d-])")


_US_STREET_AFTER_RE = re.compile(r"\s+(?:ave|avenue|rd|road|st|street|dr|drive|blvd|boulevard|way|ln|lane|ct|court|pl"
                                 r"|place|pkwy|parkway|hwy|highway|cir|circle|ter|terrace|trl|trail|sq|square)\b", re.I)


def is_japanese(text: str | None) -> bool:
    """A Japanese address: kana, 〒, a prefecture, 丁目 / 番地, a city or ward then the town and its number
    (simplified characters such as 县 / 东 count too); in romaji, a "-ku" / "-shi" / "chome", or a 〒 code with a
    Japanese place in a line without a US street ("7 Tokyo Ave 123-4567", "Sushi Nara, 123 Main St, ... Tel 555-1234"
    are a US street and phone number; "St. Luke's International Hospital, 9-1 Akashicho, ... Tokyo 104-8560" is not)."""
    raw = unicodedata.normalize("NFKC", text or "")
    t = zh_to_ja(raw)
    if _JP_TEXT_RE.search(t) or any(not _US_STREET_AFTER_RE.match(t, m.end()) for m in _JP_ROMAJI_RE.finditer(t)):
        return True
    if _JP_MUNI_RE.search(t.strip()) and not _ZH_ADDRESS_RE.search(raw):
        return True
    return (any(not re.search(rf"\b{_PHONE_LABEL}$", t[: m.start()], re.I) for m in _JP_POSTCODE_RE.finditer(t))
            and not _US_SHAPED_RE.search(t) and not _US_UNIT_STREET_RE.search(t)  # "Tel 555-1234" is a phone number
            and any(not _US_STREET_AFTER_RE.match(t, m.end()) for m in _JP_PLACE_RE.finditer(t)))
