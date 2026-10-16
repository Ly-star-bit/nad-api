"""Address parsing and normalization.

The Census geocoder returns USPS-style abbreviations ("W WASHINGTON ST", "5TH AVE") while the
NAD stores FGDC-style full words ("West Washington Street", "5 Avenue"). Everything is reduced
to one canonical token form before comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DIRECTIONS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "N": "N", "S": "S", "E": "E", "W": "W", "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW",
}

# USPS Publication 28, Appendix C1 (common subset): variant -> standard abbreviation.
_SUFFIX_GROUPS = {
    "ALY": ["ALLEY", "ALLEE", "ALLY"],
    "ANX": ["ANNEX", "ANEX", "ANNX"],
    "ARC": ["ARCADE"],
    "AVE": ["AVENUE", "AV", "AVEN", "AVENU", "AVN", "AVNUE"],
    "BYU": ["BAYOU", "BAYOO"],
    "BCH": ["BEACH"],
    "BND": ["BEND"],
    "BLF": ["BLUFF", "BLUF"],
    "BLVD": ["BOULEVARD", "BOUL", "BOULV"],
    "BR": ["BRANCH", "BRNCH"],
    "BRG": ["BRIDGE", "BRDGE"],
    "BRK": ["BROOK"],
    "BYP": ["BYPASS", "BYPA", "BYPAS", "BYPS"],
    "CSWY": ["CAUSEWAY", "CAUSWA"],
    "CTR": ["CENTER", "CEN", "CENT", "CENTR", "CENTRE", "CNTER", "CNTR"],
    "CIR": ["CIRCLE", "CIRC", "CIRCL", "CRCL", "CRCLE"],
    "CLF": ["CLIFF"],
    "CLFS": ["CLIFFS"],
    "CLB": ["CLUB"],
    "CMN": ["COMMON"],
    "COR": ["CORNER"],
    "CORS": ["CORNERS"],
    "CRSE": ["COURSE"],
    "CT": ["COURT"],
    "CTS": ["COURTS"],
    "CV": ["COVE"],
    "CRK": ["CREEK"],
    "CRES": ["CRESCENT", "CRSENT", "CRSNT"],
    "XING": ["CROSSING", "CRSSNG"],
    "DL": ["DALE"],
    "DM": ["DAM"],
    "DR": ["DRIVE", "DRIV", "DRV"],
    "EST": ["ESTATE"],
    "ESTS": ["ESTATES"],
    "EXPY": ["EXPRESSWAY", "EXP", "EXPR", "EXPRESS", "EXPW"],
    "EXT": ["EXTENSION", "EXTN", "EXTNSN"],
    "FLS": ["FALLS"],
    "FRY": ["FERRY", "FRRY"],
    "FLD": ["FIELD"],
    "FLDS": ["FIELDS"],
    "FLT": ["FLAT"],
    "FRD": ["FORD"],
    "FRST": ["FOREST", "FORESTS"],
    "FRG": ["FORGE", "FORG"],
    "FRK": ["FORK"],
    "FRKS": ["FORKS"],
    "FT": ["FORT", "FRT"],
    "FWY": ["FREEWAY", "FREEWY", "FRWAY", "FRWY"],
    "GDN": ["GARDEN", "GARDN", "GRDEN", "GRDN"],
    "GDNS": ["GARDENS", "GRDNS"],
    "GTWY": ["GATEWAY", "GATEWY", "GATWAY", "GTWAY"],
    "GLN": ["GLEN"],
    "GRN": ["GREEN"],
    "GRV": ["GROVE", "GROV"],
    "HBR": ["HARBOR", "HARB", "HARBR", "HRBOR"],
    "HVN": ["HAVEN"],
    "HTS": ["HEIGHTS", "HT"],
    "HWY": ["HIGHWAY", "HIGHWY", "HIWAY", "HIWY", "HWAY"],
    "HL": ["HILL"],
    "HLS": ["HILLS"],
    "HOLW": ["HOLLOW", "HLLW", "HOLLOWS", "HOLWS"],
    "INLT": ["INLET"],
    "IS": ["ISLAND", "ISLND"],
    "JCT": ["JUNCTION", "JCTION", "JCTN", "JUNCTN", "JUNCTON"],
    "KY": ["KEY"],
    "KNL": ["KNOLL", "KNOL"],
    "LK": ["LAKE"],
    "LKS": ["LAKES"],
    "LNDG": ["LANDING", "LNDNG"],
    "LN": ["LANE"],
    "LGT": ["LIGHT"],
    "LOOP": ["LOOPS"],
    "MALL": [],
    "MNR": ["MANOR"],
    "MDW": ["MEADOW"],
    "MDWS": ["MEADOWS", "MEDOWS"],
    "ML": ["MILL"],
    "MSN": ["MISSION", "MISSN", "MSSN"],
    "MTWY": ["MOTORWAY"],
    "MT": ["MOUNT", "MNT"],
    "MTN": ["MOUNTAIN", "MNTAIN", "MNTN", "MOUNTIN", "MTIN"],
    "OVAL": ["OVL"],
    "OPAS": ["OVERPASS"],
    "PARK": ["PRK", "PARKS"],
    "PKWY": ["PARKWAY", "PARKWY", "PKWAY", "PKY", "PARKWAYS", "PKWYS"],
    "PASS": [],
    "PSGE": ["PASSAGE"],
    "PATH": ["PATHS"],
    "PIKE": ["PIKES"],
    "PNE": ["PINE"],
    "PNES": ["PINES"],
    "PL": ["PLACE"],
    "PLN": ["PLAIN"],
    "PLNS": ["PLAINS"],
    "PLZ": ["PLAZA", "PLZA"],
    "PT": ["POINT"],
    "PTS": ["POINTS"],
    "PRT": ["PORT"],
    "PR": ["PRAIRIE", "PRR"],
    "RADL": ["RADIAL", "RAD", "RADIEL"],
    "RNCH": ["RANCH", "RANCHES", "RNCHS"],
    "RPD": ["RAPID"],
    "RST": ["REST"],
    "RDG": ["RIDGE", "RDGE"],
    "RIV": ["RIVER", "RVR", "RIVR"],
    "RD": ["ROAD"],
    "RDS": ["ROADS"],
    "RTE": ["ROUTE"],
    "ROW": [],
    "RUN": [],
    "SHR": ["SHORE", "SHOAR"],
    "SHRS": ["SHORES", "SHOARS"],
    "SKWY": ["SKYWAY"],
    "SPG": ["SPRING", "SPNG", "SPRNG"],
    "SPGS": ["SPRINGS", "SPNGS", "SPRNGS"],
    "SQ": ["SQUARE", "SQR", "SQRE", "SQU"],
    "STA": ["STATION", "STATN", "STN"],
    "STRA": ["STRAVENUE", "STRAV", "STRAVEN", "STRAVN", "STRVN", "STRVNUE"],
    "STRM": ["STREAM", "STREME"],
    "ST": ["STREET", "STRT", "STR"],
    "STS": ["STREETS"],
    "SMT": ["SUMMIT", "SUMIT", "SUMITT"],
    "TER": ["TERRACE", "TERR"],
    "TRWY": ["THROUGHWAY"],
    "TRCE": ["TRACE", "TRACES"],
    "TRAK": ["TRACK", "TRACKS", "TRK", "TRKS"],
    "TRFY": ["TRAFFICWAY"],
    "TRL": ["TRAIL", "TRAILS", "TRLS"],
    "TRLR": ["TRAILER", "TRLRS"],
    "TUNL": ["TUNNEL", "TUNEL", "TUNLS", "TUNNELS", "TUNNL"],
    "TPKE": ["TURNPIKE", "TRNPK", "TURNPK"],
    "UPAS": ["UNDERPASS"],
    "UN": ["UNION"],
    "VLY": ["VALLEY", "VALLY", "VLLY"],
    "VIA": ["VIADUCT", "VDCT", "VIADCT"],
    "VW": ["VIEW"],
    "VLG": ["VILLAGE", "VILL", "VILLAG", "VILLG", "VILLIAGE"],
    "VL": ["VILLE"],
    "VIS": ["VISTA", "VIST", "VST", "VSTA"],
    "WALK": ["WALKS"],
    "WAY": ["WY"],
    "WL": ["WELL"],
    "WLS": ["WELLS"],
}
SUFFIXES: dict[str, str] = {}
for _abbr, _variants in _SUFFIX_GROUPS.items():
    SUFFIXES[_abbr] = _abbr
    for _v in _variants:
        SUFFIXES[_v] = _abbr

# Name-word normalizations applied to every token (both sides get the same treatment).
_WORDS = {"SAINT": "ST", "MOUNT": "MT", "FORT": "FT", "COUNTY": "CO", "STATE": "STATE",
          "ROUTE": "RTE", "HIGHWAY": "HWY", "US": "US", "INTERSTATE": "I"}

_ORDINAL_WORDS = {
    "FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4", "FIFTH": "5", "SIXTH": "6",
    "SEVENTH": "7", "EIGHTH": "8", "NINTH": "9", "TENTH": "10", "ELEVENTH": "11",
    "TWELFTH": "12", "THIRTEENTH": "13", "FOURTEENTH": "14", "FIFTEENTH": "15",
    "SIXTEENTH": "16", "SEVENTEENTH": "17", "EIGHTEENTH": "18", "NINETEENTH": "19",
    "TWENTIETH": "20",
}
_ORDINAL_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)$")

UNIT_DESIGNATORS = {
    "APT", "APARTMENT", "UNIT", "STE", "SUITE", "RM", "ROOM", "FL", "FLR", "FLOOR", "BLDG",
    "BUILDING", "LOT", "SPC", "SPACE", "TRLR", "TRAILER", "DEPT", "OFC", "OFFICE", "PH",
    "PENTHOUSE", "HNGR", "HANGAR", "PIER", "SLIP", "BSMT", "BASEMENT", "LOWR", "UPPR", "REAR",
    "FRNT", "SIDE", "NO", "NUM", "NUMBER",
}
# Designators that may appear without an identifier ("REAR", "BSMT").
_UNIT_STANDALONE = {"BSMT", "BASEMENT", "LOWR", "UPPR", "REAR", "FRNT", "SIDE", "PH", "PENTHOUSE"}

_UNIT_RE = re.compile(
    r"(?:\b(APT|APARTMENT|UNIT|STE|SUITE|RM|ROOM|FL|FLR|FLOOR|BLDG|BUILDING|LOT|SPC|SPACE|TRLR|"
    r"TRAILER|DEPT|OFC|OFFICE|PH|PENTHOUSE|HNGR|HANGAR|PIER|SLIP)\b\.?\s*#?|#)\s*"
    r"([A-Z0-9][A-Z0-9\-/]*)",
    re.I,
)
_STATE_ZIP_TAIL_RE = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-?(\d{4}))?\s*$", re.I)
_ZIP_RE = re.compile(r"\b(\d{5})(?:-?(\d{4}))?\s*$")
# Letter suffixes must be attached ("12A"); "1700 W Washington" is a directional, not a suffix.
_HOUSE_NUMBER_RE = re.compile(
    r"^\s*(?P<pre>[A-Z]{1,2}(?=\d))?(?P<num>\d+)(?:-(?P<num2>\d+))?"
    r"(?:(?P<suf>[A-Z])|\s+(?P<half>1/2))?(?=\s|,|$)",
    re.I,
)
_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "PR", "VI", "GU", "AS", "MP",
}


def clean(text: str | None) -> str:
    """Uppercase, strip punctuation (keeping '/' for '1/2'), collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"[^A-Za-z0-9/ ]+", " ", text.upper())
    return re.sub(r"\s+", " ", text).strip()


def norm_token(tok: str, types: bool = True) -> str:
    """Canonical token. `types=False` keeps street-type words intact, for use inside a street
    name ("LAKE VIEW" must not become "LK VW", which would no longer match "LAKEVIEW")."""
    m = _ORDINAL_RE.match(tok)
    if m:
        return m.group(1)
    if tok in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[tok]
    if tok in DIRECTIONS:
        return DIRECTIONS[tok]
    if types and tok in SUFFIXES:
        return SUFFIXES[tok]
    return _WORDS.get(tok, tok)


def norm_text(text: str | None, types: bool = True) -> str:
    return " ".join(norm_token(t, types) for t in clean(text).split())


def norm_unit(text: str | None) -> str:
    """'Apt. #4-B' -> '4B'; 'Unit 12' -> '12'."""
    toks = [t for t in clean(text).replace("/", " ").split() if t not in UNIT_DESIGNATORS]
    return re.sub(r"[^A-Z0-9]", "", "".join(toks))


@dataclass
class StreetKey:
    predir: str = ""
    pretype: str = ""
    core: str = ""
    posttype: str = ""
    postdir: str = ""

    @property
    def full(self) -> str:
        return " ".join(p for p in (self.predir, self.pretype, self.core, self.posttype, self.postdir) if p)

    @classmethod
    def from_parts(cls, predir="", pretype="", name="", posttype="", postdir="") -> "StreetKey":
        return cls(
            predir=norm_text(predir), pretype=norm_text(pretype), core=norm_text(name, types=False),
            posttype=norm_text(posttype), postdir=norm_text(postdir),
        )

    @classmethod
    def from_text(cls, text: str | None) -> "StreetKey":
        """Heuristic split of a free-text street ('W Washington St', '5th Avenue')."""
        toks = clean(text).split()
        key = cls()
        if len(toks) > 1 and toks[0] in DIRECTIONS:
            key.predir = DIRECTIONS[toks.pop(0)]
        if len(toks) > 1 and toks[-1] in DIRECTIONS:
            key.postdir = DIRECTIONS[toks.pop()]
        if len(toks) > 1 and toks[-1] in SUFFIXES:
            key.posttype = SUFFIXES[toks.pop()]
        key.core = norm_text(" ".join(toks), types=False)
        return key


def street_score(query: StreetKey, cand: StreetKey) -> float:
    """0..1 similarity between two street keys, penalizing conflicting directionals/types."""
    if not query.core and not query.full:
        return 0.0
    core = SequenceMatcher(None, query.core, cand.core).ratio() if query.core and cand.core else 0.0
    full = SequenceMatcher(None, query.full, cand.full).ratio()
    # Pre-type streets ("STATE HWY 1", "AVENUE A") often split differently between sources.
    q_flat, c_flat = query.full.replace(" ", ""), cand.full.replace(" ", "")
    flat = SequenceMatcher(None, q_flat, c_flat).ratio()
    score = max(full, flat, core * 0.95)
    if query.predir and cand.predir and query.predir != cand.predir:
        score -= 0.25
    if query.postdir and cand.postdir and query.postdir != cand.postdir:
        score -= 0.25
    if query.posttype and cand.posttype and query.posttype != cand.posttype:
        score -= 0.1
    return round(max(0.0, min(1.0, score)), 3)


@dataclass
class HouseNumber:
    number: int
    raw: str
    prefix: str = ""
    suffix: str = ""


@dataclass
class ParsedAddress:
    raw: str
    house: HouseNumber | None = None
    street_text: str = ""  # street line without house number and unit
    unit: str = ""  # identifier only, e.g. "4B"
    unit_raw: str = ""  # as written, e.g. "Apt 4B"
    city: str = ""
    state: str = ""
    zip5: str = ""
    zip4: str = ""
    geocode_text: str = ""  # one-line address with the unit removed (Census rejects some units)
    warnings: list[str] = field(default_factory=list)


def parse_house_number(street_line: str) -> tuple[HouseNumber | None, str]:
    m = _HOUSE_NUMBER_RE.match(street_line)
    if not m:
        return None, street_line.strip()
    num = m.group("num")
    house = HouseNumber(
        number=int(num),
        raw=m.group(0).strip(),
        prefix=(m.group("pre") or "").upper(),
        suffix=(m.group("suf") or m.group("half") or "").upper(),
    )
    if m.group("num2"):  # Queens-style "123-45": NAD stores the combined number in AddNo_Full
        house.suffix = f"-{m.group('num2')}"
    return house, street_line[m.end():].strip(" ,")


def extract_unit(street_line: str) -> tuple[str, str, str]:
    """Returns (street_line_without_unit, unit_identifier, unit_as_written)."""
    matches = list(_UNIT_RE.finditer(street_line))
    if matches:
        m = matches[-1]  # last occurrence wins ("...St Bldg 2 Apt 5")
        unit_raw = m.group(0).strip()
        rest = (street_line[: m.start()] + " " + street_line[m.end():]).strip(" ,")
        return re.sub(r"\s+", " ", rest), norm_unit(m.group(2)), unit_raw
    toks = street_line.split()
    if toks and toks[-1].upper() in _UNIT_STANDALONE:
        return " ".join(toks[:-1]), toks[-1].upper(), toks[-1]
    return street_line, "", ""


def _split_trailing_city(street_line: str) -> tuple[str, str]:
    """'123 Main St Miami' -> ('123 Main St', 'Miami'): cut after the last street-type word."""
    toks = street_line.split()
    for i in range(len(toks) - 2, 1, -1):
        if clean(toks[i]) in SUFFIXES:
            return " ".join(toks[: i + 1]), " ".join(toks[i + 1:])
    return street_line, ""


def parse_oneline(text: str) -> ParsedAddress:
    """Best-effort split of '1700 W Washington St Apt 2, Phoenix, AZ 85007'."""
    raw = re.sub(r"\s+", " ", text or "").strip()
    pa = ParsedAddress(raw=raw)
    if not raw:
        return pa
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    street_line, tail = parts[0], parts[1:]

    if not tail:
        # No commas: peel "ST 12345" off the end first so "FL 33101" is never read as a floor.
        m = _STATE_ZIP_TAIL_RE.search(street_line)
        if m and m.group(1).upper() in _STATES:
            pa.state, pa.zip5, pa.zip4 = m.group(1).upper(), m.group(2), m.group(3) or ""
            street_line = street_line[: m.start()].strip()
    else:
        last = tail[-1]
        m = _STATE_ZIP_TAIL_RE.search(last)
        if m and m.group(1).upper() in _STATES:
            pa.state, pa.zip5, pa.zip4 = m.group(1).upper(), m.group(2), m.group(3) or ""
            tail[-1] = last[: m.start()].strip()
        else:
            mz = _ZIP_RE.search(last)
            if mz:
                pa.zip5, pa.zip4 = mz.group(1), mz.group(2) or ""
                tail[-1] = last[: mz.start()].strip()
            if tail[-1].upper() in _STATES:
                pa.state = tail[-1].upper()
                tail[-1] = ""
        tail = [t for t in tail if t]
        # A unit written as its own segment: "123 Main St, Apt 4, Springfield, IL"
        if tail and _UNIT_RE.match(tail[0]):
            street_line = f"{street_line} {tail.pop(0)}"
        if tail:
            pa.city = tail[0]

    street_line, pa.unit, pa.unit_raw = extract_unit(street_line)
    if not tail and not pa.city:
        street_line, pa.city = _split_trailing_city(street_line)
    pa.house, pa.street_text = parse_house_number(street_line)
    if pa.house is None:
        pa.warnings.append("no house number found in input")

    pa.geocode_text = ", ".join(p for p in (street_line, pa.city, f"{pa.state} {pa.zip5}".strip()) if p)
    return pa


def parse_structured(street: str, city: str = "", state: str = "", zip_code: str = "",
                     unit: str = "") -> ParsedAddress:
    street_line, unit_id, unit_raw = extract_unit(re.sub(r"\s+", " ", street or "").strip())
    if unit:
        unit_id, unit_raw = norm_unit(unit), unit
    pa = ParsedAddress(
        raw=", ".join(p for p in (street, unit, city, f"{state or ''} {zip_code or ''}".strip()) if p),
        unit=unit_id, unit_raw=unit_raw, city=(city or "").strip(), state=(state or "").strip().upper(),
    )
    mz = re.match(r"^\s*(\d{5})(?:-?(\d{4}))?\s*$", zip_code or "")
    if mz:
        pa.zip5, pa.zip4 = mz.group(1), mz.group(2) or ""
    elif zip_code:
        pa.warnings.append(f"unrecognized ZIP code '{zip_code}'")
    pa.house, pa.street_text = parse_house_number(street_line)
    if pa.house is None:
        pa.warnings.append("no house number found in input")
    pa.geocode_text = ", ".join(p for p in (street_line, pa.city, f"{pa.state} {pa.zip5}".strip()) if p)
    return pa
