"""Split a European address line into street, house number, unit, postcode and city.

Each country writes addresses in one of two orders - "Unter den Linden 77, 10117 Berlin" or
"18 bis rue de la République, 69002 Lyon" - with the postcode next to the city. The providers do their own
searching; this only has to find the parts that the answer is compared against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..address import prep
from .countries import Country, fold, strip_country

# Unit / floor / staircase markers that follow the house number (German, French, Spanish, Italian, Dutch, Nordic,
# English). Everything from the marker on is the unit.
_UNIT_WORDS = (
    r"apt|apartment|appt|app|appart|appartement|flat|unit|suite|ste|room|rm|floor|fl"
    r"|wohnung|whg|top|stiege|stg|tur|tuer|og|eg|dg|stock|etage|"
    r"bus|boite|bte|bat|batiment|esc|escalier|porte|"
    r"piso|puerta|planta|escalera|bajo|atico|"
    r"int|interno|scala|piano|"
    r"lgh|lagenhet|leil|"
    r"lok|lokal|m|kv|"
    r"h\d{4}"
)
_UNIT_RE = re.compile(rf"(?:^|[\s,])(?:(?:{_UNIT_WORDS})\b\.?|#|\d+\s?[º°ª]|\d+o(?=\s|$)|\d+\.\s*(?:og|stock|etage|floor)\b)", re.I)
# A house number: digits, an optional letter ("12a", "11 A"), French "bis/ter/quater", and "/" or "-" parts
# ("4/6/8" in Poland, "12/3/4" in Austria, "8-H" in the Netherlands, "7-1" in Estonia).
_NUM = r"\d{1,5}(?:\s?[A-Za-z](?![A-Za-z]))?(?:\s?(?:bis|ter|quater)\b)?(?:\s?[-/]\s?[0-9A-Za-z]{1,4}(?![A-Za-z]{2}))*"
_NUM_LAST_RE = re.compile(rf"^(?P<street>.*?\D)\s*,?\s+(?P<num>{_NUM})\s*$")
_NUM_FIRST_RE = re.compile(rf"^(?P<num>{_NUM})\s*,?\s+(?P<street>\D.*)$")
_NUM_INSIDE_RE = re.compile(rf"(?:^|\s)(?P<num>{_NUM})(?:\s|$)")


@dataclass
class IntlAddress:
    raw: str
    country: str
    street: str = ""
    number: str = ""        # house number as written, spaces removed: "77", "12a", "18bis", "4/6/8", "8-H"
    unit: str = ""          # flat / floor / door text after the number
    postcode: str = ""
    city: str = ""
    extra: list[str] = field(default_factory=list)  # other segments (building or company names)
    warnings: list[str] = field(default_factory=list)

    @property
    def number_base(self) -> str:
        """'8-H' -> '8', '12/3/4' -> '12', '18bis' -> '18bis'."""
        return re.split(r"[-/]", self.number, maxsplit=1)[0]

    @property
    def number_ext(self) -> str:
        """The part after the first '-' or '/': '8-H' -> 'H', '12/3/4' -> '3/4'."""
        parts = re.split(r"[-/]", self.number, maxsplit=1)
        return parts[1] if len(parts) > 1 else ""


def _pc_regex(c: Country) -> re.Pattern:
    # optional "D-" / "F-" / "CH-" style prefix in front of numeric postcodes
    return re.compile(rf"(?<![\w-])(?:[A-Z]{{1,3}}-)?(?P<pc>{c.postcode})(?![\w-])", re.I)


def norm_number(text: str) -> str:
    """'11 A' -> '11A', '18 bis' -> '18bis', '4 / 6' -> '4/6'."""
    return re.sub(r"\s+", "", text or "")


def split_street(seg: str, c: Country) -> tuple[str, str, str]:
    """Street segment -> (street, number, unit)."""
    unit = ""
    m = _UNIT_RE.search(seg)
    # a unit marker after some text that already contains a digit (so "Top 5" in "Hauptstraße 12 Top 5", not the
    # "M" of a street called "M 4")
    if m and m.start() > 0 and re.search(r"\d", seg[: m.start()]):
        unit, seg = seg[m.start():].strip(" ,"), seg[: m.start()].strip(" ,")
    orders = (_NUM_FIRST_RE, _NUM_LAST_RE) if c.number_first else (_NUM_LAST_RE, _NUM_FIRST_RE)
    for rx in orders:
        if m := rx.match(seg):
            return m.group("street").strip(" ,"), norm_number(m.group("num")), unit
    if m := _NUM_INSIDE_RE.search(seg):  # "Calle Mayor 5 3ºB" style leftovers: number in the middle
        street = (seg[: m.start()] + " " + seg[m.end():]).strip(" ,")
        return street, norm_number(m.group("num")), unit
    return seg, "", unit


def parse_line(text: str, c: Country) -> IntlAddress:
    t = prep(text)
    a = IntlAddress(raw=t, country=c.code)
    segs = strip_country([s.strip() for s in t.split(",") if s.strip()])
    if not segs:
        return a
    rx = _pc_regex(c)

    # postcode + city, searched from the end ("..., 10117 Berlin", "London SW1A 2AA", "Rīga, LV-1050")
    for i in range(len(segs) - 1, -1, -1):
        seg = segs[i]
        found = list(rx.finditer(seg))
        if not found:
            continue
        m = found[-1]
        before, after = seg[: m.start()].strip(" ,"), seg[m.end():].strip(" ,")
        if i == 0 and (len(segs) > 1 or not re.search(r"\d", before)):
            continue  # the street segment's own house number
        a.postcode = m.group("pc").upper()
        if i == 0:  # one segment: "Unter den Linden 77 10117 Berlin"
            segs[0], a.city = before, after
        else:
            a.city = after or before
            del segs[i]
            if not a.city and i - 1 >= 1:  # "London, SW1A 2AA": the city is the segment before
                a.city = segs.pop(i - 1)
        break

    # the street segment: the first one with a house number that is not a unit line ("Flat 3, 10 Downing St")
    street_i = None
    for i, seg in enumerate(segs):
        if re.search(r"\d", seg) and not (_UNIT_RE.match(seg) and not _NUM_FIRST_RE.match(seg)):
            street_i = i
            break
    if street_i is None:
        street_i = 0
    a.street, a.number, a.unit = split_street(segs[street_i], c)
    for i, seg in enumerate(segs):
        if i == street_i:
            continue
        if _UNIT_RE.match(seg):
            a.unit = ", ".join(p for p in (a.unit, seg) if p)
        elif i > street_i and not a.city and not re.search(r"\d", seg):
            a.city = seg  # "Via Roma 10, Milano" (no postcode)
        else:
            a.extra.append(seg)
    if not a.number:
        a.warnings.append("no house number found in the address")
    return a


def parse_structured(c: Country, street: str, city: str = "", postcode: str = "", unit: str = "") -> IntlAddress:
    a = IntlAddress(raw=", ".join(p for p in (prep(street), prep(unit), prep(postcode), prep(city)) if p), country=c.code)
    a.street, a.number, a.unit = split_street(prep(street), c)
    if unit:
        a.unit = prep(unit)
    a.city = prep(city)
    pc = prep(postcode)
    if pc:
        m = _pc_regex(c).search(pc)
        a.postcode = (m.group("pc") if m else pc).upper()
    if not a.number:
        a.warnings.append("no house number found in the street")
    return a


def is_unit_only(text: str) -> bool:
    return bool(_UNIT_RE.match(text or ""))


def city_matches(want: str, have: str | None) -> bool | None:
    if not want or not have:
        return None
    w, h = fold(want), fold(have)
    return w == h or w in h or h in w
