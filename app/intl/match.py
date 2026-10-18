"""Compare what a provider returned with what was asked, and turn that into a verdict.

Most free geocoders answer *something* for a nonexistent house number - the street, or the nearest real number
on it (Swiss, Spanish and Brussels services, PDOK free text, Photon) - and several search the whole country, so the
same street and number in another town comes back too. So nothing here trusts "a result came back": every
candidate's street, house number, postcode and town are compared with the input again, and a record is only taken
as the address when it lies in the town (or postcode) the input names.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .countries import fold
from .parse import IntlAddress
from .places import district_differs, namesake, same_city

STREET_OK = 0.85  # strict: "Wiejska" vs "Nowowiejska" is 0.78 and must not pass

# Street-type words -> one canonical token. Collisions across languages don't matter: inputs are only ever compared
# with records from the same country.
_TYPE_WORDS = {
    "str": "str strasse straat strada street st",
    "pl": "pl platz place plac plein plaza piazza pza pzza praca placa pc plass plads plats namesti nam namestie torg "
          "torv tori ter trg laukums valjak aukio",
    "av": "av ave avenue avenida avda avinguda avd",
    "al": "al aleja aleje allee alle allees all alameda",
    "bd": "bd bld blvd boulevard bv bulvar bulvaris bulevar bulevardi",
    "rue": "rue r rua",
    "rd": "rd road",
    "ln": "ln lane laan",
    "dr": "drive",  # "dr" itself: a Drive at the end of the name, else a doctor (_TITLES)
    "ct": "ct court",
    "sq": "sq square",
    "cres": "cres crescent", "terr": "terr terrace", "gdns": "gdns gardens", "way": "way wy", "pk": "pk park",
    "gr": "gr grove",
    "cl": "cl close calle c carrer cesta",
    "ch": "ch chem chemin", "rte": "rte route", "imp": "imp impasse", "quai": "quai qu", "crs": "crs cours",
    "pass": "pass passage pje pasaje passatge passagem", "sente": "sente", "voie": "voie",
    "paseo": "paseo po passeig pg passeio",
    "ctra": "ctra carretera estrada estr",
    "ronda": "ronda rda", "trav": "travesia trav travessa tv travessera", "camino": "camino cmno cami",
    "via": "via v", "viale": "viale vle", "corso": "corso cso", "largo": "largo lgo lg", "vicolo": "vicolo vic vlo",
    "piazzale": "piazzale ple", "contrada": "contrada", "loc": "localita loc frazione fraz",
    "ul": "ul ulica ulice ulitsa iela", "os": "os osiedle", "trida": "trida tr",
    "weg": "weg wg", "gasse": "gasse", "steig": "steig", "ring": "ring", "damm": "damm", "ufer": "ufer",
    "gate": "gate gt gata gatan gaten gade", "vei": "vei veien vn vej vagen vag veg vegen",
    "katu": "katu", "tie": "tie", "kuja": "kuja",
    "utca": "utca u", "ut": "ut", "krt": "krt korut",
    "mnt": "mnt maantee", "tn": "tn tanav", "pst": "pst puiestee", "tee": "tee", "gatve": "gatve g",
    "pr": "pr prospekt prospektas prospekts prosp", "rambla": "rambla", "urb": "urb urbanizacion", "glorieta": "gta glorieta",
    "beco": "bc beco", "calcada": "cc calcada", "chaussee": "chaussee chee steenweg",  # 'Rue' / 'Chaussée de Louvain'
}
_TYPES = {w: canon for canon, words in _TYPE_WORDS.items() for w in words.split()}
# Where a word is another type than elsewhere: Latvian 'gatve' (avenue) is not Lithuanian 'gatvė' (street)
_TYPES_BY_CC = {"LV": {"gatve": "lvgatve", "g": "lvgatve"}}
# Titles, written or left out: "Rue du Dr Roux" is "Rue du Docteur Roux", "Dr.-Otto-Nuschke-Straße" "Otto-Nuschke-Straße"
_TITLES = {"dr", "docteur", "doktor", "doutor", "doctor", "dottore"}
# Type words glued to the name ("Hauptstraße", "Museumstraat", "Drottninggatan", "Mannerheimintie"), with the
# common misspellings of the German and Dutch ones
_TYPE_SUFFIXES = {"strasse": "str", "straat": "str", "str": "str", "strase": "str", "strabe": "str", "strat": "str",
                  "gasse": "gasse", "gase": "gasse", "allee": "al", "platz": "pl", "plein": "pl", "laan": "ln", "weg": "weg",
                  "gatan": "gate", "gata": "gate", "gaten": "gate", "gate": "gate", "gade": "gate", "gt": "gate",
                  "vej": "vei", "vagen": "vei", "veien": "vei", "vegen": "vei", "veg": "vei", "vei": "vei", "katu": "katu",
                  "tie": "tie", "kuja": "kuja", "utca": "utca", "ring": "ring", "damm": "damm", "ufer": "ufer"}
# People leave these out: "Wiejska" is "ul. Wiejska", "Mayor" is "Calle Mayor"
_OMISSIBLE = {"ul", "cl", "via", "rue", "tn", "gatve"}
# ...but in Czech, Slovak and Polish naming a square or avenue is not the plain name's street: "Palackého" is not
# "Palackého náměstí" (nor "Palackého třída"), "Wolności" not "plac Wolności"
_DISTINCT = {"CZ": {"pl", "trida"}, "SK": {"pl", "trida"}, "PL": {"pl"}}
# Dotted abbreviations that fold() would split into two words ("C.so" -> "c so")
_DOTTED = [(re.compile(p, re.I), r) for p, r in (
    (r"\bc\.\s?so\b", "corso"), (r"\bp\.\s?zz?a\b", "piazza"), (r"\bp\.\s?le\b", "piazzale"), (r"\bv\.\s?le\b", "viale"),
    (r"\bl\.\s?go\b", "largo"), (r"\bv\.\s?lo\b", "vicolo"), (r"\bl\.\s?re\b", "lungotevere"), (r"\bgl\.", "gammel"),
)]
# Abbreviated parts of names
_ALIASES = {"fbg": "faubourg", "gal": "general", "mal": "marechal", "pdt": "president", "pres": "president",
            "cdt": "commandant", "lt": "lieutenant", "col": "colonel", "mgr": "monseigneur", "prof": "professor",
            "professeur": "professor", "professore": "professor", "eng": "engenheiro", "sta": "santa", "sto": "santo",
            "gamle": "gammel", "gl": "gammel"}
_SAINT = {"saint": "st", "sankt": "st", "sint": "st", "san": "st", "santo": "st", "sao": "st", "sainte": "ste",
          "ste": "ste", "santa": "ste", "sv": "st", "svaty": "st", "svata": "ste", "sveti": "st", "sveta": "ste", "swietego": "st"}
_STOP = {"de", "la", "le", "les", "du", "des", "d", "l", "der", "die", "das", "den", "dem", "van", "het", "t", "of",
         "the", "di", "del", "della", "delle", "dei", "degli", "dello", "dell", "y", "e", "da", "do", "dos", "das", "el",
         "los", "las", "et", "en", "und", "an", "am", "im", "auf", "zum", "zur", "a", "i", "o"}
# Words that make another street: "Neue Hauptstraße" is not "Hauptstraße", "Calle Mayor Baja" not "Calle Mayor",
# "Norra Hamngatan" not "Hamngatan", "Rue Saint-Jean" not "Rue Jean"
_MODIFIERS = set(
    "nord sud sued ost west north south east neue neuer neues alte alter altes obere oberer untere unterer mittlere "
    "kleine kleiner grosse grosser hintere vordere innere aussere lange kurze upper lower old new great little petite "
    "petit grande grand vieille vieux nouvelle nouveau haute haut basse bas prolongee nueva nuevo vieja viejo alta alto "
    "baja bajo menor vecchia vecchio nuova nuovo antica antico superiore inferiore piccola nova novo velha velho stary "
    "stara nowy nowa gammel gamla nya ny store lilla stora vanha uusi iso pikku nedre ovre ytre indre faubourg "
    "norra sodra ostra vastra norre sondre oster vester nordre ostre vestre pohjois etela ita lansi "
    "nieuwe oude korte st ste ancienne ancien maggiore "
    "maza mazais liela lielais veca jauna mazoji didzioji senoji naujoji vana uus vaike suur".split())
# Danish / Norwegian 'lille' (small): in France Lille is a town, and BAN's "Rue Faidherbe (Lille)" is Lille's street
_MODIFIERS_BY_CC = {"DK": {"lille"}, "NO": {"lille"}}
# Where streets are named after people and the given name or title is usually left out ("ul. Kościuszki" for
# "Tadeusza Kościuszki", "Via Garibaldi", "Avenue Foch"). Elsewhere a longer name is another street: "Kensington High
# Street", "King William Street", "Calle Real de Arriba".
_GIVEN_NAMES_LEFT_OUT = {"PL", "IT", "FR", "LV", "LT", "EE", "BE"}  # Riga's "Barona iela", "Rue Dansaert"
# Spain and Portugal leave out only a title ("Calle Larios" is "Calle Marqués de Larios"): "Calle Real" is not "Calle
# Ciudad Real" nor "Calle Real de Arriba"
_TITLES_LEFT_OUT = {"ES": {"marques", "conde", "duque", "general", "infanta", "infante", "reyes", "alcalde", "capitan",
                           "coronel", "padre", "maestro"},
                    "PT": {"marques", "conde", "duque", "general", "infanta", "infante", "capitao", "coronel", "padre",
                           "almirante"}}

_NUMBER_WORDS = {w: i for i, ws in enumerate([
    (), ("uno", "un", "une", "eins", "premier", "premiere", "primo", "prima", "primero", "primera", "erste", "eerste"),
    ("due", "deux", "dos", "zwei", "deuxieme", "secondo", "seconda", "segundo", "segunda", "zweite", "tweede"),
    ("tre", "trois", "tres", "drei", "troisieme", "terzo", "terza", "tercero", "tercera", "dritte", "derde"),
    ("quattro", "quatre", "cuatro", "vier", "quatrieme", "quarto", "quarta", "cuarto", "vierde"),
    ("cinque", "cinq", "cinco", "funf", "cinquieme", "quinto", "quinta", "vijfde"),
    ("sei", "six", "seis", "sechs", "sixieme", "sesto", "sexto"), ("sette", "sept", "siete", "sieben", "settimo", "septimo"),
    ("otto", "huit", "ocho", "acht", "ottavo", "octavo"), ("nove", "neuf", "nueve", "neun", "nono", "noveno"),
    ("dieci", "dix", "diez", "zehn", "decimo"), ("undici", "onze", "once", "undicesimo"), ("dodici", "douze", "doce",
                                                                                          "dodicesimo"),
    ("tredici", "treize", "trece", "tredicesimo"), ("quattordici", "quatorze", "catorce"), ("quindici", "quinze", "quince"),
    ("sedici", "seize", "dieciseis"), ("diciassette", "diecisiete"), ("diciotto", "dieciocho"), ("diciannove", "diecinueve"),
    ("venti", "vingt", "veinte", "ventesimo"), ("ventuno", "veintiuno", "ventunesimo"), ("ventidue", "veintidos"),
    ("ventitre", "veintitres", "ventitreesimo"), ("ventiquattro", "veinticuatro"), ("venticinque", "veinticinco"),
    ("ventisei", "veintiseis"), ("ventisette", "veintisiete"), ("ventotto", "veintiocho"), ("ventinove", "veintinueve"),
    ("trenta", "trente", "treinta"), ("trentuno", "treinta y uno"),
]) for w in ws}
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _number_token(tok: str) -> str:
    """Dates and ordinals in street names are written three ways: 'Via XX Settembre' (the register: 'Via Venti
    Settembre'), 'Rue du 4 Septembre' / 'Rue du Quatre-Septembre', 'Corso Vittorio Emanuele II' / '... Secondo',
    '1e Jan Steenstraat' / 'Eerste ...'. All become digits."""
    if tok in _NUMBER_WORDS:
        return str(_NUMBER_WORDS[tok])
    if m := re.fullmatch(r"(\d+)(?:er|re|e|eme|ieme|o|a|st|nd|rd|th)", tok):
        return m.group(1)
    if len(tok) >= 2 and re.fullmatch(r"[ivxlc]+", tok):
        vals = [_ROMAN[ch] for ch in tok]
        n = sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))
        if 0 < n < 200:
            return str(n)
    return tok


def street_parts(text: str | None, cc: str | None = None) -> tuple[str, set[str], list[str], set[str]]:
    """'Rue de la République' -> ('republique', {'rue'}, ['republique'], set()); 'Hauptstr.' -> ('haupt', {'str'},
    ['haupt'], {'str'}). The last set holds the types that were glued to the name."""
    t = text or ""
    for rx, full in _DOTTED:
        t = rx.sub(full, t)
    if cc in ("SE", "NO", "DK"):  # "Drottningg." is Drottninggatan
        t = re.sub(r"(?<=\w{4})g\.(?=\s|,|$)", "gatan", t)
    toks = [_ALIASES.get(x, x) for x in fold(t).split()]
    local = _TYPES_BY_CC.get(cc or "", {})
    name: list[str] = []
    types: set[str] = set()
    glued: set[str] = set()
    for i, tok in enumerate(toks):
        if tok in _SAINT or (tok == "st" and i < len(toks) - 1):  # "St Mary Axe" is Saint, "Downing St" Street
            name.append(_SAINT.get(tok, "st"))
            continue
        if tok == "i" and i == len(toks) - 1 and len(toks) > 1:  # "Corso Umberto I"
            name.append("1")
            continue
        if tok == "dr" and i == len(toks) - 1 and len(toks) > 1:  # "Mulholland Dr" is a Drive, "Rue du Dr Roux" a doctor
            types.add("dr")
            continue
        if tok in _TITLES:
            continue
        if tok in local or tok in _TYPES:
            types.add(local.get(tok) or _TYPES[tok])
            continue
        if tok in _STOP:
            continue
        tok = _number_token(tok)
        for suf, canon in sorted(_TYPE_SUFFIXES.items(), key=lambda kv: -len(kv[0])):
            if tok.endswith(suf) and len(tok) - len(suf) >= 3:
                types.add(canon)
                glued.add(canon)
                tok = tok[: -len(suf)]
                break
        name.append(tok)
    if not name:  # the name is only type words ("Ring", "Le Cours"): compare those
        name = sorted(types)
    return "".join(name), types, name, glued


def _types_differ(wt: set[str], wg: set[str], ht: set[str], hg: set[str], cc: str | None = None) -> bool:
    """'Rue de la Paix' / 'Avenue de la Paix', 'Markt' / 'Marktstraße', 'Via Augusta' / 'Calle Augusta' are other
    streets; 'Castellana' / 'Paseo de la Castellana' and 'Wiejska' / 'ul. Wiejska' are one street written with and
    without its type word."""
    if not wt or not ht:  # a separate type word left out is fine, a type glued to the name or a square's is not
        return bool(wg or hg or (wt | ht) & _DISTINCT.get(cc or "", set()))
    diff = wt ^ ht
    if not diff:
        return False
    if diff - _OMISSIBLE:
        return True
    return not (wt <= ht or ht <= wt)  # an omissible type left out, not another one in its place ('ulica' / 'cesta')


def _subset(a: list[str], b: list[str], cc: str | None = None) -> bool:
    """'Kościuszki' in 'Tadeusza Kościuszki', 'Foch' in 'Maréchal Foch': one name is the other with a first name or
    title left out (not with 'Neue' / 'Nord' left out, which makes another street). Only where streets are written
    that way (any country when it is not known: Photon's search)."""
    titles = _TITLES_LEFT_OUT.get(cc or "")
    if cc and cc not in _GIVEN_NAMES_LEFT_OUT and not titles:
        return False
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if not small or len(small) == len(big) or not set(small) <= set(big):
        return False
    if titles and not all(t in titles for t in big if t not in small):
        return False
    local = _MODIFIERS_BY_CC.get(cc, set()) if cc else set().union(*_MODIFIERS_BY_CC.values())
    extra = [t for t in big if t not in small]
    # the given name goes before the surname that is kept ('Tadeusza Kościuszki'); only Italian registers put it after
    # ('VIA VERDI GIUSEPPE'): elsewhere a record's 'Rue Notre-Dame des Champs' or 'ul. Polna Boczna' is another street
    # (an input's trailing word is more often its town: 'rue de Rivoli Paris')
    return (all(t not in _MODIFIERS and t not in local and not t.isdigit() for t in extra)
            and any(len(t) >= 4 for t in small) and (small[-1] == big[-1] or (small[0] == big[0]
                                                                               and (cc in (None, "IT") or big is a))))


def street_sim(want: str | None, have: str | None, cc: str | None = None) -> float:
    """0..1; `cc` (the country) lets local naming decide: Latvian 'gatve', names written without the given name."""
    if not want or not have:
        return 0.0
    wn, wt, wtoks, wg = street_parts(want, cc)
    hn, ht, htoks, hg = street_parts(have, cc)
    if not wn or not hn:
        return 0.0
    if wn == hn or sorted(wtoks) == sorted(htoks):  # 'VIA VERDI GIUSEPPE' is 'Via Giuseppe Verdi'
        s = 1.0
    else:
        s = SequenceMatcher(None, wn, hn).ratio()
        if _subset(wtoks, htoks, cc):
            s = max(s, 0.9)
        elif wn[0] != hn[0]:  # "Wiejska" / "Miejska", "Reinsgraben" / "Steinsgraben": another street, not a typo
            s -= 0.1
    if re.findall(r"\d+", wn) != re.findall(r"\d+", hn):  # "Via XX Settembre" is not "Via XXI Settembre"
        s = min(s, 0.5)
    if wt ^ ht == {"ul", "al"}:  # Polish "ul." is written for an "al." (aleja) too, but it is not the exact name
        s = min(s, 0.95)
    elif _types_differ(wt, wg, ht, hg, cc):
        s -= 0.25  # "Rue de la Paix" / "Avenue de la Paix", "Markt" / "Marktstraße"
    return round(max(s, 0.0), 3)


def best_street_sim(want: str | None, *names: str | None, cc: str | None = None) -> float:
    """Best match against a record's names, each bilingual one also in parts: Brussels' "Grote Markt - Grand-Place",
    South Tyrol's "Lauben / Portici"."""
    parts = [n for name in names if name for n in (name, *re.split(r"\s+[-/]\s+", name))]
    return max((street_sim(want, n, cc) for n in parts), default=0.0)


def norm_num(text: str | None) -> str:
    """'18 bis' -> '18bis', '8-H' -> '8h', '011' -> '11', '26 A' -> '26a'; '4/6/8' and '7-1' keep their separators
    ('12/3' is not '123')."""
    t = re.sub(r"[\s.]", "", unicodedata.normalize("NFKC", text or "").casefold())
    t = re.sub(r"^(\d+)-([a-z])$", r"\1\2", t)
    return re.sub(r"^0+(?=\d)", "", t)


def _range(text: str) -> tuple[int, int] | None:
    """'48/50', '48-50', '15-19', Ryhti's '11-35' -> the numbers one building carries; '99/5' (a flat), '853/12',
    '5/27' and '11/12' (Czech building/orientation pairs: '/' joins a double number a few numbers apart on one side
    of the street) are not."""
    m = re.fullmatch(r"(\d+)([-/])(\d+)", text)
    lo, hi = (int(m.group(1)), int(m.group(3))) if m else (0, 0)
    return (lo, hi) if lo < hi and (m.group(2) == "-" or (hi - lo <= 20 and (hi - lo) % 2 == 0)) else None


def num_matches(a: IntlAddress, have: str | None, block: bool = False) -> str | None:
    """'full' when the record has the whole number ('4/6/8', '8-H'), 'base' when only the part before '/' or '-'
    ('12' for '12/3/4' - the rest is a flat), 'range' when the record is a range or double number that contains
    it ('48/50' for 48, '15-19' for 17), else None. `block`: the record is a register's block of numbers (Ryhti's
    number / number2), whose numbers over a span of 20 aren't all on one side of the street."""
    if not have or not a.number:
        return None
    h, w = norm_num(have), norm_num(a.number)
    if h == w:
        return "full"
    hr, wr = _range(h), _range(w)
    if hr and wr and hr == wr:  # "48-50" / "48/50"
        return "full"
    if a.number_ext and h == norm_num(a.number_base):
        return "base"
    if hr and w.isdigit() and a.country not in ("CZ", "SK"):  # there "5/25" is a building and its orientation number
        n, (lo, hi) = int(w), hr
        # its first and last number, and one between on the same side of the street ("11-13" has no 12; Harrods'
        # "87-135" has no 88); a register's block over 20 numbers has both sides
        if n in (lo, hi) or (lo < n < hi and ((block and hi - lo > 20) or (n - lo) % 2 == 0)):
            return "range"
    return None


def norm_postcode(text: str | None) -> str:
    """'LV-1050' -> '1050', '1012 lg' -> '1012LG', 'SW1A 2AA' -> 'SW1A2AA', '00-110' -> '00110'."""
    t = re.sub(r"[\s-]", "", (text or "").upper())
    return re.sub(r"^[A-Z]{1,3}(?=\d{3,})", "", t) if re.fullmatch(r"[A-Z]{1,3}\d{3,}", t) else t


def norm_unit(text: str | None) -> str:
    """'Whg 5' / 'Apt. 5' / 'bus 0106' -> '5' / '5' / '106'; 'H0201' and 'leil. H0201' -> 'h0201'."""
    t = re.sub(r"\b(apt|apartment|app|appartement|flat|unit|suite|wohnung|whg|top|bus|boite|bte|box|piso|puerta|int|"
               r"interno|lgh|lagenhet|leil|leilighet|bolig|kv|door|tur|korter|krt|dz|byt|m|lok)\b", " ", fold(text))
    t = re.sub(r"\s+", "", t)
    if re.fullmatch(r"h\d{4}", t):  # Norwegian H0201 (floor 02, flat 01): the zeros are part of it
        return t
    return re.sub(r"(?<![0-9])0+(?=\d)", "", t)


def pc_near(want: str, have: str | None, size: int = 2) -> bool:
    """A postcode typo rather than another town: the same first two (or `size`) characters (the same district in
    most European postcode systems)."""
    w, h = norm_postcode(want), norm_postcode(have)
    return len(w) >= 3 and len(h) >= 3 and w[:size] == h[:size]


# ---------------------------------------------------------------------------------------------- verdict

_BAN_DISTRICT_RE = re.compile(r"(?P<street>.+?)\s*\((?P<district>[^()]*[^\W\d_][^()]*)\)")


def score(a: IntlAddress, c: dict[str, Any]) -> dict[str, Any]:
    street, district = c.get("street"), None
    if a.country == "FR" and (m := _BAN_DISTRICT_RE.fullmatch(street or "")):
        # BAN names the streets of a merged commune after the former commune: 'Rue Jean Jaurès (Lomme)', 59160 Lille
        # is in Lomme, and is not the 'Rue Jean Jaurès (Lille)' of a Lille 59000 address
        street, district = m.group("street"), m.group("district")
    ss = best_street_sim(a.street, street, *(c.get("street_alt") or []), cc=a.country)
    num = num_matches(a, c.get("number"), block=c.get("source") == "Ryhti") if c.get("kind") == "address" else None
    unit_value = c.get("unit")
    if num != "full" and c.get("kind") == "address" and unit_value and re.fullmatch(r"[A-Za-z]|bis|ter|quater", unit_value.strip(), re.I):
        # Overture's Italian / Danish / Portuguese rows keep the letter apart: number '10', unit 'A' is "10A" (and
        # "10/A")
        if norm_num(f"{c.get('number')}{unit_value.strip()}") in (norm_num(a.number), norm_num(a.number_base + a.number_ext)):
            num, unit_value = "full", None
    have_pc = c.get("postcode") or c.get("area_postcode")
    pc = (norm_postcode(a.postcode) == norm_postcode(have_pc)) if a.postcode and have_pc and not a.cedex else None
    cities = [x for x in (c.get("city"), *(c.get("city_alt") or [])) if x]
    towns = [same_city(a.city, x, a.country) for x in cities]
    if (q := _qualifier(a)) and cities:  # 'Bergen' with the '(NH)' the parser set aside is Bergen (NH), not Bergen L
        towns = [True if same_city(f"{a.city} {q}", x, a.country) else t for t, x in zip(towns, cities)]
    # the same town; the start of the record's longer name (None: the postcode or the located street decides); another
    city = True if True in towns else None if None in towns or not towns else False
    if city and district_differs(a.city, have_pc, a.country):
        city = False  # 'Dublin 2', a record in D04
    if city is None and pc and a.city and None in towns:
        city = True  # 'Buchs' for 'Buchs ZH', 'Frankfurt' for 'Frankfurt am Main': the postcode says which
    if district and not pc:
        city = same_city(a.city, district, a.country)
    # the row's own postcode is another town's (Weinsberg 74189 for Erlenbach 74235; Abstatt 74232 down the road), not
    # one of the input town's next to it: Berlin 10115 / 10117, whose rows on the located street name the Ortsteil
    own_pc_differs = bool(a.postcode and c.get("postcode") and not a.cedex
                          and norm_postcode(a.postcode) != norm_postcode(c["postcode"])
                          and not (pc_near(a.postcode, c["postcode"], 3) and (
                              c.get("on_street") or (c.get("distance_m") is not None and c["distance_m"] <= 100))))
    if city is False and c.get("city_loose") and not own_pc_differs:  # a loose town name, unless its postcode differs too
        city = None
    if city is None and c.get("area_city"):
        # the town Photon located the street in: it stands for an Overture row without a town, and still shows a wrong
        # input town - but it doesn't make a row with a town of its own the input's
        area = same_city(a.city, c["area_city"], a.country)
        if not cities or area is False:
            city = area
    # a postcode typo rather than another town; with no town to confirm it, only one in the last digits
    near = pc is False and pc_near(a.postcode, have_pc, 2 if a.city else 3)
    # ...or a row with the input's town right by the street Photon located in the input's postcode, or in the same
    # numbered district ('Praha 2' / 'Praha 2'): that town with a mistyped postcode (München 80331 / 81241, Praha
    # 110 00 / 120 00) - not Wien 1 for a record in Wien 1140, nor Newport NP20 for one in Newport PO30
    nd = re.search(r"\s(\d{1,2}|[IVX]{1,5})\.?$", a.city or "")
    same_area = city is True and ((c.get("distance_m") is not None and c["distance_m"] <= 2000 and bool(c.get("area_pc_ok")))
                                  or bool(nd and any((m := re.search(r"\s(\d{1,2}|[IVX]{1,5})\.?$", x))
                                                     and m.group(1).upper() == nd.group(1).upper() for x in cities)))
    if pc:
        place = True
    elif pc is False and not near and not same_area:
        place = False  # a postcode far from the input's is another town, even one with the same name (Beaumont 54470/89250)
    else:              # no postcode to compare, or one that differs only in its last digits: the town decides
        place = city
    unit = None
    if a.unit and c.get("kind") == "address" and unit_value:
        unit = norm_unit(unit_value) == norm_unit(a.unit)
    total = 0.55 * ss + 0.3 * (num is not None) + 0.1 * (pc is not False) + 0.05 * (city is not False)
    # BAN's former commune, else the row's own district code (Köln's 'Wv')
    return {**c, "district": district or c.get("district"),
            "match": {"score": round(total, 3), "street_score": ss, "number_exact": num == "full" if num else False,
                      "zip_match": pc, "city_match": city, "unit_match": unit, "distance_m": c.get("distance_m"),
                      "number": num, "place": place}}


def _qualifier(a: IntlAddress) -> str | None:
    """A bracket after the town that the parser set aside ('Bergen (NH)'): it says which namesake."""
    if a.city and not re.search(r"[()]", a.city):
        return next((x.strip() for x in a.extra if re.fullmatch(r"\(\s*[^\W\d_][^()]{0,20}\)", x.strip())), None)
    return None


AUTHORITY_CONFIDENCE = {"official": 0.97, "overture": 0.9, "osm": 0.75}


def _town(c: dict) -> str:
    town = " ".join(p for p in (c.get("postcode") or c.get("area_postcode"), c.get("city") or c.get("area_city")) if p)
    return f"{town} ({c['district']})" if c.get("district") else town


def decide(a: IntlAddress, cands: list[dict], *, authority: str, dataset: str, unit_support: bool = False,
           street_found: bool = False, area_empty: bool = False, max_candidates: int = 5,
           incomplete: str | None = None) -> dict[str, Any]:
    """Candidates (kind 'address' or 'street') from one provider -> status, best match, suggestions, notes.
    `incomplete`: why this source's missing number proves nothing (it lacks some kinds of buildings)."""
    notes: list[str] = []
    scored = sorted((score(a, c) for c in cands), key=lambda c: -c["match"]["score"])
    addrs = [c for c in scored if c.get("kind") == "address"]
    # The street is the input's: exactly that name when the source has it in the input's town (then "Kirschstraße"
    # can't stand in for "Kirchstraße"), else a close spelling. Another town's street of that name says nothing about
    # this town's, and the streets Photon adds to Overture's rows carry OSM's spelling, not the register's ("Rua de
    # São Bento" for "R S BENTO"): those only show that the street is there. A register row of exactly that name on the
    # street Photon located is that street whatever its town says (Overture's 'Maienstraße 1-4' in 'Freiburg im
    # Breisgau' for a 'Freiburg' input): then 'Marienstraße 9' nearby can't stand in for 'Maienstraße 9'.
    exact = any(c["match"]["street_score"] >= 0.999 and (c["match"]["place"] is not False or c.get("on_street"))
                and (c.get("kind") == "address" or authority != "overture") for c in scored)
    same = (lambda c: c["match"]["street_score"] >= 0.999) if exact else (lambda c: c["match"]["street_score"] >= STREET_OK)
    same_street = [c for c in addrs if same(c)]
    here = [c for c in same_street if c["match"]["place"] is not False]  # in the town / postcode the input names
    elsewhere = [c for c in same_street if c["match"]["place"] is False]
    street_found = street_found or bool(here) or any(
        c.get("kind") == "street" and same(c) and c["match"]["place"] is not False for c in scored)
    hits = [c for c in here if c["match"]["number"]]
    other = [c for c in elsewhere if c["match"]["number"]]
    out: dict[str, Any] = {"status": "not_found", "verified": False, "match_level": None, "confidence": 0.0,
                           "best_match": None,
                           # the same number on other streets (a typo in the street name), not every nearby address
                           "candidates": _dedupe([c for c in addrs if not same(c) and c["match"]["number"]
                                                  and c["match"]["place"] is not False])[:max_candidates],
                           "suggestions": [], "notes": notes}
    base = AUTHORITY_CONFIDENCE[authority]

    if hits and not (a.postcode or a.city) and len({_town(c) for c in hits}) > 1:
        towns = list(dict.fromkeys(_town(c) for c in hits))
        notes.append(f"no town or postcode in the address, and '{a.street} {a.number}' exists in {len(towns)} places "
                     f"({'; '.join(towns[:5])}); add the town or postcode")
        out.update(status="not_covered", candidates=_dedupe(hits)[:max_candidates])
        return out
    if hits and not (a.postcode or a.city) and authority != "official":  # not a nationwide search: one is no proof
        notes.append(f"no town or postcode in the address: only the place OpenStreetMap search picked "
                     f"({_town(hits[0]) or 'one place'}) was searched, and other places may have this street too; "
                     "add the town or postcode")
        out.update(status="not_covered", candidates=_dedupe(hits)[:max_candidates])
        return out
    if hits:
        # the whole number before a base-only one, the input's town and postcode before others, then the spelling
        hits.sort(key=lambda c: (c["match"]["number"] == "base", c["match"]["number"] == "range",
                                 c["match"]["place"] is not True, c["match"]["zip_match"] is False,
                                 -c["match"]["street_score"]))
        top = hits[0]["match"]["number"]
        full = [c for c in hits if c["match"]["number"] == top]
        sure = [c for c in full if c["match"]["place"] is True] or full  # those surely in the town, else all
        if not a.postcode and len({norm_postcode(c.get("postcode"))[:3] for c in sure if c.get("postcode")}) > 1:
            # one town, several streets of this name in different postcode areas ('Hauptstraße 5, Berlin', 'Zielonki 12,
            # Zielonki'), not one building with two of its postcodes: only the postcode can say which
            towns = list(dict.fromkeys(_town(c) for c in sure))
            notes.append(f"no postcode in the address, and '{a.street} {a.number}' exists in {len(towns)} places "
                         f"({'; '.join(towns[:5])}); add the postcode")
            out.update(status="not_covered", candidates=_dedupe(sure)[:max_candidates])
            return out
        if authority != "official" and all(_namesake_only(a, c) for c in full):
            # 'Frankfurt' for a house in Frankfurt (Oder), 'Buchs' in Buchs ZH: not a nationwide search, so the other
            # places of that name were never looked at
            towns = list(dict.fromkeys(_town(c) for c in full))
            notes.append(("" if a.postcode else "no postcode in the address, and ")
                         + f"'{a.city}' may be any of the places of that name; {dataset} search found it in "
                         f"{'; '.join(towns[:5])}; add the postcode")
            out.update(status="not_covered", candidates=_dedupe(full)[:max_candidates])
            return out
        units = sorted({c["unit"] for c in full if c.get("unit")}, key=_natural)
        bare = [c for c in full if not c.get("unit")]
        best = full[0]
        conf = base if best["match"]["street_score"] >= 0.95 else base * 0.95
        level = "address"
        if a.unit or (top == "base" and unit_support):
            want = a.unit or a.number_ext
            with_unit = [c for c in full if c.get("unit") and norm_unit(c["unit"]) == norm_unit(want)]
            if with_unit:
                best, level = with_unit[0], "unit"
                best["match"]["unit_match"] = True
            elif unit_support:
                best = bare[0] if bare else full[0]
                best["match"]["unit_match"] = False
                notes.append(f"the building exists in {dataset} but unit '{want}' is not recorded"
                             + (f"; units there: {', '.join(units[:20])}" if units else ""))
                out.update(status="unit_not_found", verified=True, match_level="address", confidence=round(conf * 0.9, 3),
                           best_match=best, candidates=_dedupe([c for c in full if c is not best], units=True)[:max_candidates])
                return _with_zip_note(out, a, dataset)
            else:  # the building, not one of its flats picked at random; and no 'unit differs' for an unchecked unit
                best = bare[0] if bare else best
                best["match"]["unit_match"] = None
                # (Overture lists some flats of a Danish building - '2 tv', not '9. tv' - but not all of them)
                notes.append(f"{dataset} has no flat / unit data, so unit '{want}' was not checked" if not units else
                             f"the flat / unit data in {dataset} is incomplete, so unit '{want}' was not checked "
                             f"(units it lists here: {', '.join(units[:20])})")
        elif units and unit_support:
            if bare:
                best = bare[0]
            else:  # "Damrak 8" where the register has only 8-A ... 8-E
                notes.append(f"this number exists only with an addition in {dataset}: {', '.join(units[:20])}; "
                             "without one the parcel may not be deliverable")
                conf *= 0.9
        elif bare:
            best = bare[0]
        if top == "base" and level != "unit":
            notes.append(f"{dataset} has house number {best.get('number')}; '{a.number_ext}' after it was not checked")
        if top == "range":
            notes.append(f"{dataset} records the building as number {best.get('number')}, which includes {a.number}")
            conf *= 0.95
        if best["match"]["street_score"] < 0.999:
            notes.append(f"street name differs: input '{a.street}', {dataset} '{best.get('street')}'")
        if not (a.postcode or a.city):
            notes.append(f"no town or postcode in the address; the only match is in {_town(best) or 'one place'}")
            conf *= 0.9
        elif best["match"]["city_match"] is False and best["match"]["zip_match"]:
            notes.append(f"town differs: input '{a.city}', {dataset} '{best.get('city')}' (the postcode matches)")
        elif (authority == "official" and not a.postcode and best.get("city") and best["match"]["city_match"] is None
              and same_city(a.city, best["city"], a.country) is None):
            # 'Buchs': the town of that name where the register's search found the address (a search with a limit:
            # other places of that name are not ruled out)
            notes.append(f"no postcode in the address; '{a.city}' taken as {best['city']}, where {dataset} has this "
                         "address; add the postcode to be sure")
        out.update(status="verified", verified=True, match_level=level, best_match=best, confidence=round(conf, 3),
                   candidates=_dedupe([c for c in full if c is not best], units=True)[:max_candidates])
        return _with_zip_note(out, a, dataset)

    sugg, seen = [], set()
    for c in sorted(here, key=lambda c: (_num_distance(a.number, c.get("number")), bool(c.get("unit")))):
        # one entry per house number, not one per flat or per way of writing it (Czech "190" and "190/5")
        if norm_num(c.get("number")) not in seen and c["formatted"] not in seen:
            seen.update((norm_num(c.get("number")), c["formatted"]))
            sugg.append(c)
    out["suggestions"] = sugg[:max_candidates]
    if other:
        towns = list(dict.fromkeys(_town(c) for c in other if _town(c)))
        place = ", ".join(p for p in (a.postcode, a.city) if p)
        notes.append(f"'{a.street} {a.number}' exists only in other places ({'; '.join(towns[:5]) or 'elsewhere'}), "
                     f"not in {place}")
        out["candidates"] = _dedupe(other + out["candidates"])[:max_candidates]
    if street_found:
        if authority == "osm" or incomplete:  # a missing number says nothing about the address
            why = incomplete or "OpenStreetMap is missing many house numbers"
            notes.append(f"the street is in {dataset} but this house number is not; {why}, so this does not mean "
                         "the address is wrong")
            out.update(status="not_covered", confidence=0.0)
        else:
            notes.append(f"the street exists in {dataset}, but house number {a.number} was not found on it")
            out.update(status="street_only", match_level="street", confidence=0.3)
        return out
    if area_empty:
        notes.append(f"{dataset} has no addresses around this location - the area is not covered")
        out["status"] = "not_covered"
        return out
    notes.append(f"no address with this street and number in {dataset}"
                 + (f" in {', '.join(p for p in (a.postcode, a.city) if p)}" if other else ""))
    out["confidence"] = 0.05
    return out


def _namesake_only(a: IntlAddress, c: dict) -> bool:
    """The row is in one of the namesakes a qualifier tells apart ('Frankfurt (Oder)', 'Buchs ZH'), and neither the
    postcode (the row's, a typo of it, or the one Photon located the street in) nor the town Photon located it in says
    it is the input's."""
    cities = [x for x in (c.get("city"), *(c.get("city_alt") or [])) if x]
    return (bool(a.city) and c["match"]["city_match"] is None and c["match"]["zip_match"] is None and not c.get("area_pc_ok")
            and any(namesake(a.city, x, a.country) for x in cities)
            and same_city(a.city, c.get("area_city"), a.country) is not True)


def _dedupe(cands: list[dict], units: bool = False) -> list[dict]:
    """One per address: a building's flats ('10, 1', '10, 2', ...) are one entry, the bare record first - unless the
    flats are what is being listed (the other units of the building found)."""
    out, seen = [], set()
    for c in sorted(cands, key=lambda c: bool(c.get("unit"))):
        key = (fold(c.get("street")), norm_num(c.get("number")), norm_postcode(c.get("postcode")), fold(c.get("city")),
               fold(c.get("unit")) if units else "")
        if key not in seen:
            seen.add(key)
            out.append(c)
    order = {id(c): i for i, c in enumerate(cands)}
    return sorted(out, key=lambda c: order[id(c)])


def _with_zip_note(out: dict, a: IntlAddress, dataset: str) -> dict:
    bm = out["best_match"]
    have = bm.get("postcode") or bm.get("area_postcode") if bm else None
    if bm and a.cedex and have:
        out["notes"].append(f"{a.postcode} is a CEDEX (business mail) code; the address's postcode is {have}")
    elif bm and bm["match"]["zip_match"] is False:
        out["notes"].append(f"postcode differs: input {a.postcode} vs {dataset} {have}")
        out["confidence"] = round(out["confidence"] * 0.9, 3)
    return out


def _natural(text: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", str(text))]


def _num_distance(want: str, have: str | None) -> float:
    w, h = re.match(r"\d+", want or ""), re.match(r"\d+", have or "")
    return abs(int(w.group()) - int(h.group())) if w and h else 1e9
