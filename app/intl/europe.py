"""Official national address registers with a free public API (no key)."""

from __future__ import annotations

import re

from .. import config
from ..upstream import UpstreamRejected
from .base import Ctx, Lookup, ProviderInfo, Skip, cand
from .parse import IntlAddress

INFO = {
    "fr_ban": ProviderInfo("fr_ban", "BAN (Base Adresse Nationale)", "official", "https://adresse.data.gouv.fr",
                           "Licence Ouverte / Etalab 2.0", "Base Adresse Nationale, IGN Géoplateforme"),
    "nl_pdok": ProviderInfo("nl_pdok", "BAG (PDOK Locatieserver)", "official", "https://www.pdok.nl",
                            "CC0 1.0", "Basisregistratie Adressen en Gebouwen, Kadaster / PDOK"),
}


# ----------------------------------------------------------------------------------------- France

async def fr_ban(ctx: Ctx, a: IntlAddress) -> Lookup:
    """IGN Géoplateforme geocoder over the BAN. A missing number comes back as type 'street' (never a neighbour);
    50 requests/s per IP."""
    q = " ".join(p for p in (a.number_base, a.street, a.postcode, a.city) if p)
    try:
        data = await ctx.get("fr_ban", config.FR_BAN_URL, {"q": q[:200], "limit": 10, "index": "address"})
    except UpstreamRejected:  # BAN refuses queries shorter than 3 characters or without letters
        return Lookup(status="geocode_failed", notes=["BAN refused the query (too short or no street name)"])
    return Lookup(cands=fr_ban_cands(data))


def fr_ban_cands(data: dict) -> list[dict]:
    out = []
    for f in data.get("features") or []:
        p, (lon, lat) = f.get("properties") or {}, _point(f.get("geometry"))
        kind = {"housenumber": "address", "street": "street", "locality": "street"}.get(p.get("type"))
        if not kind:
            continue
        out.append(cand(kind, number=p.get("housenumber") if kind == "address" else None,
                        street=p.get("street") or p.get("name"), postcode=p.get("postcode"), city=p.get("city"),
                        lat=lat, lon=lon, id=p.get("id"), formatted=p.get("label"), region=p.get("context"),
                        source="BAN", license="Etalab 2.0"))
    return out


# ------------------------------------------------------------------------------------ Netherlands

_NL_NUM_RE = re.compile(r"^(\d+)\s*-?\s*([A-Za-z](?![A-Za-z0-9]))?\s*-?\s*([0-9A-Za-z]{1,4})?$")


def nl_split(a: IntlAddress) -> tuple[int | None, str]:
    """'8-H' -> (8, 'H'); '1A' -> (1, 'A'); '123-2' -> (123, '2'); '12 hs' (unit 'hs') -> (12, 'HS'). The letter
    (huisletter) and the addition (toevoeging) are both treated as the unit: together they name one dwelling."""
    m = _NL_NUM_RE.match(a.number or "")
    if not m:
        return None, a.unit
    extra = "".join(p for p in (m.group(2), m.group(3)) if p) + re.sub(r"\W", "", a.unit or "")
    return int(m.group(1)), extra.upper()


async def nl_pdok(ctx: Ctx, a: IntlAddress) -> Lookup:
    """BAG through PDOK's Locatieserver. With a postcode the exact Solr filter is used (a nonexistent number gives
    0 hits); free text then finds the street and its numbers (it snaps to a neighbour, so it is only compared)."""
    huisnummer, extra = nl_split(a)
    fields = ("id weergavenaam straatnaam huisnummer huisletter huisnummertoevoeging huis_nlt postcode "
              "woonplaatsnaam centroide_ll type")
    cands: list[dict] = []
    pc = re.sub(r"\s", "", a.postcode).upper()
    if huisnummer is not None and re.fullmatch(r"\d{4}[A-Z]{2}", pc):
        data = await ctx.get("nl_pdok", config.NL_PDOK_URL, {
            "q": "*:*", "fq": ["type:adres", f"postcode:{pc}", f"huisnummer:{huisnummer}"], "fl": fields, "rows": 50})
        cands += nl_cands(data)
    if not any(c["street"] and _same(c["street"], a.street) for c in cands):
        q = " ".join(p for p in (a.street, a.number, a.postcode, a.city) if p)
        data = await ctx.get("nl_pdok", config.NL_PDOK_URL, {"q": q, "fq": "type:adres", "fl": fields, "rows": 20})
        seen = {c["uuid"] for c in cands}
        cands += [c for c in nl_cands(data) if c["uuid"] not in seen]
    view = IntlAddress(**{**a.__dict__, "number": str(huisnummer) if huisnummer is not None else a.number,
                          "unit": extra, "warnings": a.warnings})
    return Lookup(cands=cands, unit_support=True, view=view)


def _same(x: str, y: str) -> bool:
    from .match import STREET_OK, best_street_sim
    return best_street_sim(y, x) >= STREET_OK


def nl_cands(data: dict) -> list[dict]:
    out = []
    for d in (data.get("response") or {}).get("docs") or []:
        if d.get("type") != "adres":
            continue
        m = re.match(r"POINT\(([-\d.]+) ([-\d.]+)\)", d.get("centroide_ll") or "")
        unit = "".join(p for p in (d.get("huisletter"), d.get("huisnummertoevoeging")) if p) or None
        out.append(cand("address", number=d.get("huisnummer"), unit=unit.upper() if unit else None,
                        street=d.get("straatnaam"), postcode=d.get("postcode"), city=d.get("woonplaatsnaam"),
                        lat=m.group(2) if m else None, lon=m.group(1) if m else None, id=d.get("id"),
                        formatted=d.get("weergavenaam"), source="BAG", license="CC0"))
    return out


# ------------------------------------------------------------------------------------------ helpers

def _point(geom: dict | None) -> tuple[float | None, float | None]:
    """(lon, lat) of a GeoJSON Point; (None, None) for anything else (a street comes back as a polygon)."""
    g = geom or {}
    c = g.get("coordinates") or []
    return (c[0], c[1]) if g.get("type", "Point") == "Point" and len(c) >= 2 and not isinstance(c[0], list) else (None, None)


def _digits(text: str) -> str:
    m = re.match(r"\d+", text or "")
    return m.group() if m else ""


def _cql(text: str) -> str:
    """A string literal for a CQL filter."""
    return "'" + (text or "").replace("'", "''") + "'"


def _pc_digits(a: IntlAddress) -> str:
    return re.sub(r"\D", "", a.postcode or "")


def _strip_prefix(street: str, prefixes: str) -> str:
    return re.sub(rf"^(?:{prefixes})\.?\s+", "", street or "", flags=re.I).strip()


# ---------------------------------------------------------------------------------------- Belgium

INFO.update({
    "be_register": ProviderInfo("be_register", "CRAB / ICAR (Vlaanderen, Wallonie)", "official",
                                "https://www.vlaanderen.be/basisregisters", "Modellicentie gratis hergebruik / CC BY 4.0",
                                "Digitaal Vlaanderen; Service public de Wallonie (ICAR)"),
    "lu_geoportail": ProviderInfo("lu_geoportail", "geoportail.lu (BD-Adresses)", "official", "https://www.geoportail.lu",
                                  "CC0", "Administration du cadastre et de la topographie"),
    "ch_geoadmin": ProviderInfo("ch_geoadmin", "Amtliches Gebäudeadressverzeichnis (geo.admin.ch)", "official",
                                "https://www.geo.admin.ch", "Open data (OGD)", "© swisstopo"),
    "at_bev": ProviderInfo("at_bev", "BEV Adressregister", "official", "https://kataster.bev.gv.at", "CC BY 4.0",
                           "© Österreichisches Adressregister (BEV)"),
    "es_cartociudad": ProviderInfo("es_cartociudad", "CartoCiudad (IGN)", "official", "https://www.cartociudad.es",
                                   "CC BY 4.0", "CartoCiudad, IGN / CNIG"),
    "no_kartverket": ProviderInfo("no_kartverket", "Matrikkelen (Kartverket)", "official", "https://ws.geonorge.no",
                                  "CC BY 4.0", "© Kartverket"),
    "fi_ryhti": ProviderInfo("fi_ryhti", "Ryhti rakennusrekisteri (Syke)", "official", "https://paikkatiedot.ymparisto.fi",
                             "CC BY 4.0", "Suomen ympäristökeskus Syke, Ryhti"),
    "is_hms": ProviderInfo("is_hms", "Staðfangaskrá (HMS)", "official", "https://gis.fasteignaskra.is",
                           "Open public-sector licence", "Byggir á upplýsingum frá HMS"),
    "ee_inads": ProviderInfo("ee_inads", "In-ADS (Maa- ja Ruumiamet)", "official", "https://inaadress.maaamet.ee",
                             "Open data", "Maa- ja Ruumiamet"),
    "lv_vzd": ProviderInfo("lv_vzd", "Valsts adrešu reģistrs (VZD)", "official", "https://data.gov.lv", "CC BY 4.0",
                           "Valsts zemes dienests"),
    "pl_gugik": ProviderInfo("pl_gugik", "PRG (GUGiK)", "official", "https://services.gugik.gov.pl/uug/",
                             "Open data", "Główny Urząd Geodezji i Kartografii"),
    "cz_ruian": ProviderInfo("cz_ruian", "RÚIAN (ČÚZK)", "official", "https://ags.cuzk.gov.cz", "CC BY 4.0", "© ČÚZK"),
    "si_gurs": ProviderInfo("si_gurs", "Register prostorskih enot (GURS)", "official", "https://ipi.eprostor.gov.si",
                            "CC BY 4.0", "Geodetska uprava Republike Slovenije"),
})


async def be_register(ctx: Ctx, a: IntlAddress) -> Lookup:
    """Belgium has three regional registers; the postcode says which. Flanders: Digitaal Vlaanderen's geolocation
    service (existing addresses only) + the address register for box numbers; Wallonia: ICAR geocodeWS, whose score
    says how the house matched. Brussels' UrbIS licence restricts commercial reuse, so Brussels goes to Overture,
    which carries the federal BeST data."""
    pc = _pc_digits(a)
    if len(pc) != 4:
        raise Skip("a Belgian postcode is needed to pick the regional register")
    n = int(pc)
    if 1000 <= n <= 1299:
        raise Skip("Brussels is answered from BeST data in Overture")
    if 1300 <= n <= 1499 or 4000 <= n <= 7999:
        return await _be_wallonia(ctx, a)
    return await _be_flanders(ctx, a)


async def _be_flanders(ctx: Ctx, a: IntlAddress) -> Lookup:
    q = f"{a.street} {a.number_base}, {a.postcode} {a.city}".strip(" ,")
    data = await ctx.get("be_vlaanderen", config.BE_VL_GEO_URL, {"q": q, "c": 10})
    cands = be_vl_cands(data)
    lk = Lookup(cands=cands)
    if not any(c["kind"] == "address" and c["number"] for c in cands):
        data = await ctx.get("be_vlaanderen", config.BE_VL_GEO_URL, {"q": f"{a.street}, {a.postcode} {a.city}".strip(" ,"), "c": 5})
        lk.cands += [c for c in be_vl_cands(data) if c["kind"] == "street"]
    hit = next((c for c in lk.cands if c["kind"] == "address" and c["number"] and c["number"].lower() == a.number_base.lower()), None)
    if hit and a.unit:  # box numbers (bus) are only in the register
        reg = await ctx.get("be_vlaanderen", config.BE_VL_REGISTER_URL, {
            "postcode": hit["postcode"], "straatnaam": hit["street"], "huisnummer": hit["number"], "status": "inGebruik",
            "limit": 100})
        for r in reg.get("adressen") or []:
            if r.get("busnummer"):
                lk.cands.append(cand("address", number=r.get("huisnummer"), unit=r["busnummer"], street=hit["street"],
                                     postcode=hit["postcode"], city=hit["city"], lat=hit["lat"], lon=hit["lon"],
                                     id=(r.get("identificator") or {}).get("objectId"),
                                     formatted=((r.get("volledigAdres") or {}).get("geografischeNaam") or {}).get("spelling"),
                                     source="CRAB (Vlaanderen)", license="CC BY 4.0"))
        lk.unit_support = True
    return lk


def be_vl_cands(data: dict) -> list[dict]:
    out = []
    for r in data.get("LocationResult") or []:
        typ = r.get("LocationType") or ""
        loc = r.get("Location") or {}
        kind = "address" if "huisnummer" in typ and r.get("Housenumber") else "street" if "straat" in typ else None
        if not kind:
            continue
        out.append(cand(kind, number=r.get("Housenumber") if kind == "address" else None, street=r.get("Thoroughfarename"),
                        postcode=r.get("Zipcode"), city=r.get("Municipality"), lat=loc.get("Lat_WGS84"),
                        lon=loc.get("Lon_WGS84"), id=r.get("ID"), formatted=r.get("FormattedAddress"),
                        source="CRAB (Vlaanderen)", license="CC BY 4.0"))
    return out


async def _be_wallonia(ctx: Ctx, a: IntlAddress) -> Lookup:
    q = f"{a.street} {a.number_base}, {a.postcode} {a.city}".strip(" ,")
    data = await ctx.get("be_wallonie", config.BE_WA_URL, {"address": q, "crs": "EPSG:4326"})
    return Lookup(cands=be_wa_cands(data))


def be_wa_cands(data: dict) -> list[dict]:
    """ICAR score: hundreds = city, tens = street, units = house (1 exact, 2 suffix dropped, 3/4 a neighbour,
    5 somewhere on the street, 6 street centre, 0 none)."""
    out = []
    for c in data.get("candidates") or []:
        sc = int(c.get("score") or 0)
        house, street = c.get("house") or {}, c.get("street") or {}
        geo = (house.get("geometry") or street.get("geometry") or {}).get("coordinates") or (None, None)
        common = dict(street=street.get("name"), postcode=(c.get("zone") or {}).get("ident"),
                      city=(c.get("city") or {}).get("name"), lon=geo[0], lat=geo[1], source="ICAR (Wallonie)",
                      license="CC BY 4.0")
        if sc % 10 in (1, 2, 3, 4) and house.get("name"):
            out.append(cand("address", number=house["name"], id=house.get("ident"), **common))
        elif (sc // 10) % 10 == 1:
            out.append(cand("street", id=street.get("ident"), **common))
    return out


# -------------------------------------------------------------------------------------- Luxembourg

async def lu_geoportail(ctx: Ctx, a: IntlAddress) -> Lookup:
    q = f"{a.number_base} {a.street} {re.sub(r'^L-?', '', a.postcode)} {a.city}".strip()
    data = await ctx.get("lu_geoportail", config.LU_URL, {"queryString": q})
    return Lookup(cands=lu_cands(data))


def lu_cands(data: dict) -> list[dict]:
    """accuracy 8 = a building address, 6 = the street only, 5 = postcode / locality."""
    out = []
    for r in data.get("results") or []:
        d, (lon, lat) = r.get("AddressDetails") or {}, _point(r.get("geomlonlat"))
        acc = r.get("accuracy")
        kind = "address" if acc == 8 and d.get("postnumber") else "street" if acc in (6, 8) else None
        if kind:
            out.append(cand(kind, number=d.get("postnumber") if kind == "address" else None, street=d.get("street"),
                            postcode=d.get("zip"), city=d.get("locality"), lat=lat, lon=lon,
                            id=d.get("id_caclr_building"), formatted=r.get("address"), source="BD-Adresses", license="CC0"))
    return out


# ------------------------------------------------------------------------------------- Switzerland

_CH_LABEL_RE = re.compile(r"^(?P<street>.+?)\s+(?P<num>\d+(?:\s?[A-Za-z])?(?:\.\d+)?)\s+(?P<pc>\d{4})\s+(?P<city>.+)$")


async def ch_geoadmin(ctx: Ctx, a: IntlAddress) -> Lookup:
    """The federal building-address directory through geo.admin.ch search. It answers neighbouring numbers for a
    missing one, which become the suggestions."""
    q = f"{a.street} {a.number_base} {_pc_digits(a)} {a.city}".strip()
    data = await ctx.get("ch_geoadmin", config.CH_URL, {"type": "locations", "origins": "address", "sr": "4326",
                                                         "limit": 15, "searchText": q})
    return Lookup(cands=ch_cands(data))


def ch_cands(data: dict) -> list[dict]:
    out = []
    for r in data.get("results") or []:
        at = r.get("attrs") or {}
        label = re.sub(r"<[^>]+>", "", at.get("label") or "").strip()
        m = _CH_LABEL_RE.match(label)
        if not m:
            continue
        out.append(cand("address", number=m.group("num").replace(" ", ""), street=m.group("street"),
                        postcode=m.group("pc"), city=m.group("city"), lat=at.get("lat"), lon=at.get("lon"),
                        id=at.get("featureId"), formatted=label, source="GWR / swisstopo", license="OGD"))
    return out


# ----------------------------------------------------------------------------------------- Austria

async def at_bev(ctx: Ctx, a: IntlAddress) -> Lookup:
    """The search behind BEV's cadastre viewer (not a documented API: if it changes, Overture answers instead).
    The order "postcode town street number" matters: other orders ignore the postcode."""
    term = " ".join(p for p in (_pc_digits(a), a.city, a.street, a.number_base) if p)
    data = await ctx.get("at_bev", config.AT_URL, {"term": term})
    return Lookup(cands=at_cands(data))


def at_cands(data: dict) -> list[dict]:
    out = []
    for f in ((data.get("data") or {}).get("features")) or []:  # no 'features' key at all when nothing matched
        p, (lon, lat) = f.get("properties") or {}, _point(f.get("geometry"))
        kind = {"11": "address", "12": "street"}.get(str(p.get("objectType")))
        if kind:
            out.append(cand(kind, number=p.get("hnr") if kind == "address" else None, street=p.get("strasse"),
                            postcode=p.get("plz"), city=p.get("ort"), lat=lat if kind == "address" else None,
                            lon=lon if kind == "address" else None, formatted=p.get("name"), source="BEV",
                            license="CC BY 4.0"))
    return out


# ------------------------------------------------------------------------------------------- Spain

async def es_cartociudad(ctx: Ctx, a: IntlAddress) -> Lookup:
    """CartoCiudad candidates. The postcode must stay out of the query (with it nothing is found); a missing
    number comes back as its neighbours."""
    q = " ".join(p for p in (a.street, a.number_base, a.city) if p)
    data = await ctx.get("es_cartociudad", config.ES_URL, {"q": q, "limit": 15, "no_process": "callejero"})
    return Lookup(cands=es_cands(data if isinstance(data, list) else []))


def es_cands(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        typ = r.get("type")
        first = (r.get("address") or "").split(",")[0].strip()
        if typ == "portal" and r.get("portalNumber") is not None:
            street = re.sub(r"\s+\d+\s*\w?$", "", first)
            out.append(cand("address", number=f"{r['portalNumber']}{r.get('extension') or ''}", street=street,
                            postcode=r.get("postalCode"), city=r.get("muni"), lat=r.get("lat"), lon=r.get("lng"),
                            id=r.get("id"), source="CartoCiudad", license="CC BY 4.0"))
        elif typ == "callejero":
            out.append(cand("street", street=first, postcode=r.get("postalCode"), city=r.get("muni"),
                            lat=r.get("lat"), lon=r.get("lng"), id=r.get("id"), source="CartoCiudad", license="CC BY 4.0"))
    return out


# ------------------------------------------------------------------------------------------ Norway

async def no_kartverket(ctx: Ctx, a: IntlAddress) -> Lookup:
    """Kartverket's address search: structured filters are exact (a missing number gives 0 hits); the letter is
    part of the number ("11A"); dwelling units are H-numbers (H0201)."""
    n = _digits(a.number_base)
    base = {"adressenavn": a.street, "treffPerSide": 50}
    pc = _pc_digits(a)
    cands: list[dict] = []
    if n:
        for extra in ([{"postnummer": pc}] if pc else []) + [{}]:
            data = await ctx.get("no_kartverket", config.NO_URL, {**base, "nummer": n, **extra})
            cands = no_cands(data, a.unit)
            if cands:
                break
    lk = Lookup(cands=cands, unit_support=bool(re.fullmatch(r"[HhUuKkLl]\d{4}", (a.unit or "").replace(" ", ""))))
    if not any(norm_eq(c["number"], a.number_base) for c in cands):
        data = await ctx.get("no_kartverket", config.NO_URL, {**base, **({"postnummer": pc} if pc else {}), "treffPerSide": 100})
        street = no_cands(data, None)
        if not street:  # the exact name filter knows no such street: let the fuzzy free-text search try
            data = await ctx.get("no_kartverket", config.NO_URL, {"sok": f"{a.street} {a.number_base}", "fuzzy": "true",
                                                                 "treffPerSide": 20})
            street = no_cands(data, None)
        if not street and not cands:
            raise Skip(f"no street '{a.street}' in the register")
        lk.cands += street
    return lk


def norm_eq(x: str | None, y: str | None) -> bool:
    return (x or "").replace(" ", "").lower() == (y or "").replace(" ", "").lower()


def no_cands(data: dict, unit: str | None) -> list[dict]:
    out = []
    want = (unit or "").replace(" ", "").upper()
    for r in data.get("adresser") or []:
        p = r.get("representasjonspunkt") or {}
        common = dict(number=f"{r.get('nummer')}{r.get('bokstav') or ''}", street=r.get("adressenavn"),
                      postcode=r.get("postnummer"), city=r.get("poststed"), lat=p.get("lat"), lon=p.get("lon"),
                      formatted=f"{r.get('adressetekst')}, {r.get('postnummer')} {r.get('poststed')}",
                      source="Matrikkelen", license="CC BY 4.0")
        out.append(cand("address", id=r.get("adressekode"), **common))
        if want and want in (r.get("bruksenhetsnummer") or []):
            out.append(cand("address", unit=want, **common))
    return out


# ----------------------------------------------------------------------------------------- Finland

async def fi_ryhti(ctx: Ctx, a: IntlAddress) -> Lookup:
    """Syke's national building-address register (Ryhti) over OGC API Features. Exact names, Finnish or Swedish;
    a number can be a range ("11-13")."""
    name = _cql(a.street)
    where = f"(address_name_fin ILIKE {name} OR address_name_swe ILIKE {name})"
    places = []
    if _pc_digits(a):
        places.append(f"postal_code={_cql(_pc_digits(a))}")
    if a.city:
        places.append(f"(postal_office_fin ILIKE {_cql(a.city)} OR postal_office_swe ILIKE {_cql(a.city)})")
    places.append("")
    n = _digits(a.number_base)
    cands: list[dict] = []
    if n:
        num = (f"(number_part_of_address_number={n} OR (number_part_of_address_number<={n} "
               f"AND number_part_of_address_number2>={n}))")
        for place in places:  # with the postcode first; a wrong postcode shouldn't hide the address
            cands = fi_cands(await _fi(ctx, " AND ".join(p for p in (where, num, place) if p), 50), a)
            if cands:
                break
    lk = Lookup(cands=cands)
    if not any(norm_eq(c["number"], a.number_base) for c in cands):
        street = fi_cands(await _fi(ctx, " AND ".join(p for p in (where, places[0]) if p), 200), a)
        if not street and not cands:
            raise Skip(f"no street '{a.street}' in the register")
        lk.cands += street
    return lk


async def _fi(ctx: Ctx, cql: str, limit: int) -> dict:
    return await ctx.get("fi_ryhti", config.FI_URL, {"f": "json", "limit": limit, "filter-lang": "cql-text", "filter": cql})


def fi_cands(data: dict, a: IntlAddress) -> list[dict]:
    out, seen = [], set()
    want = _digits(a.number_base)
    for f in data.get("features") or []:
        p, (lon, lat) = f.get("properties") or {}, _point(f.get("geometry"))
        n, n2 = p.get("number_part_of_address_number"), p.get("number_part_of_address_number2")
        letter = p.get("subdivision_letter_of_address_number") or ""
        number = f"{n}{letter}"
        if n2 and want and int(n) <= int(want) <= int(n2) and not letter:
            number = want  # "Pohjoisesplanadi 11-13" contains 12
        key = (p.get("address_fin"), p.get("postal_code"))
        if key in seen:
            continue
        seen.add(key)
        out.append(cand("address", number=number, street=p.get("address_name_fin"),
                        street_alt=[p.get("address_name_swe")] if p.get("address_name_swe") else [],
                        postcode=p.get("postal_code"), city=(p.get("postal_office_fin") or "").title() or None,
                        lat=lat, lon=lon, id=p.get("address_key"),
                        formatted=f"{p.get('address_fin')}, {p.get('postal_code')} {(p.get('postal_office_fin') or '').title()}",
                        source="Ryhti", license="CC BY 4.0"))
    return out


# ----------------------------------------------------------------------------------------- Iceland

async def is_hms(ctx: Ctx, a: IntlAddress) -> Lookup:
    """HMS's address register (WFS). Street names in the nominative or dative; keep the Icelandic letters."""
    name = _cql(a.street)
    where = f"(HEITI_NF ILIKE {name} OR HEITI_TGF ILIKE {name})"
    n, pc = _digits(a.number_base), _pc_digits(a)
    cands: list[dict] = []
    if n:
        for place in ([f"POSTNR={pc}"] if pc else []) + [""]:
            cands = is_cands(await _is(ctx, " AND ".join(p for p in (where, f"HUSNR={n}", place) if p), 50))
            if cands:
                break
    lk = Lookup(cands=cands)
    if not any(norm_eq(c["number"], a.number_base) for c in cands):
        street = is_cands(await _is(ctx, " AND ".join(p for p in (where, f"POSTNR={pc}" if pc else "") if p), 200))
        if not street and not cands:
            raise Skip(f"no street '{a.street}' in the register")
        lk.cands += street
    return lk


async def _is(ctx: Ctx, cql: str, count: int) -> dict:
    return await ctx.get("is_hms", config.IS_URL, {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "public:Stadfangaskra",
        "outputFormat": "application/json", "srsName": "EPSG:4326", "count": count, "CQL_FILTER": cql})


def is_cands(data: dict) -> list[dict]:
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        number = f"{p.get('HUSNR')}{(p.get('BOKST') or '').lower()}" if p.get("HUSNR") is not None else None
        if not number:
            continue
        out.append(cand("address", number=number, street=p.get("HEITI_NF"), street_alt=[p.get("HEITI_TGF") or ""],
                        postcode=p.get("POSTNR"), lat=p.get("N_HNIT_WGS84"), lon=p.get("E_HNIT_WGS84"), id=p.get("HEINUM"),
                        formatted=f"{p.get('HEITI_NF')} {p.get('HUSMERKING') or number}, {p.get('POSTNR')}"
                                  + (f" ({p['SERHEITI']})" if p.get("SERHEITI") else ""),
                        source="Staðfangaskrá", license="HMS open licence"))
    return out


# ----------------------------------------------------------------------------------------- Estonia

async def ee_inads(ctx: Ctx, a: IntlAddress) -> Lookup:
    """Maa- ja Ruumiamet's In-ADS gazetteer: an exact search (a missing number or flat gives nothing), flats
    included ("Narva mnt 7-1")."""
    q = ", ".join(p for p in (f"{a.street} {a.number}".strip(), a.city) if p)
    data = await ctx.get("ee_inads", config.EE_URL, {"address": q, "results": 20, "appartment": 1, "ihist": 0})
    view = a
    if a.number_ext and not a.unit:  # "7-1": building 7, flat 1
        view = IntlAddress(**{**a.__dict__, "number": a.number_base, "unit": a.number_ext})
    lk = Lookup(cands=ee_cands(data), unit_support=True, view=view)
    if view.unit and not any(c["kind"] == "address" and norm_eq(c["number"], view.number) for c in lk.cands):
        # no such flat: is the building there?
        data = await ctx.get("ee_inads", config.EE_URL, {"address": ", ".join(p for p in (f"{a.street} {view.number}", a.city) if p),
                                                         "results": 20, "ihist": 0})
        lk.cands += ee_cands(data)
    if not any(c["kind"] == "address" and norm_eq(c["number"], view.number) for c in lk.cands):
        data = await ctx.get("ee_inads", config.EE_URL, {"address": ", ".join(p for p in (a.street, a.city) if p),
                                                         "results": 20, "ihist": 0})
        lk.cands += [c for c in ee_cands(data) if c["kind"] == "street" or c not in lk.cands]
    return lk


def ee_cands(data: dict) -> list[dict]:
    out = []
    for r in data.get("addresses") or []:
        liik = r.get("liikVal") or ""
        street = r.get("liikluspind") or r.get("nimi") or r.get("asustusyksus")
        line = r.get("aadresstekst") or ""
        if r.get("kort_nr") and not line.endswith(f"-{r['kort_nr']}"):
            line += f"-{r['kort_nr']}"
        formatted = ", ".join(p for p in (line, r.get("asustusyksus"), " ".join(p for p in (r.get("sihtnumber"), r.get("omavalitsus")) if p)) if p)
        common = dict(street=street, postcode=r.get("sihtnumber"), city=r.get("omavalitsus"), lat=r.get("viitepunkt_b"),
                      lon=r.get("viitepunkt_l"), id=r.get("adr_id"), formatted=formatted,
                      source="In-ADS", license="Open data")
        if liik == "TANAV":
            out.append(cand("street", **common))
        elif r.get("aadress_nr") and (r.get("kvaliteet") or "").startswith("tapne"):
            out.append(cand("address", number=r["aadress_nr"], unit=r.get("kort_nr") or None, **common))
    return out


# ------------------------------------------------------------------------------------------ Latvia

async def lv_vzd(ctx: Ctx, a: IntlAddress) -> Lookup:
    """VZD's state address register, queried live through data.gov.lv's datastore (all words must appear; the
    Latvian letters are needed). Only existing addresses (STATUSS EKS)."""
    q = " ".join(p for p in (a.street, a.number_base, a.city) if p)
    params = {"resource_id": config.LV_RESOURCE, "limit": 50, "filters": '{"STATUSS": "EKS"}'}
    data = await ctx.get("lv_vzd", config.LV_URL, {**params, "q": q})
    cands = lv_cands(data)
    lk = Lookup(cands=cands)
    if not any(norm_eq(c["number"], a.number_base) for c in cands):
        data = await ctx.get("lv_vzd", config.LV_URL, {**params, "q": " ".join(p for p in (a.street, a.city) if p), "limit": 200})
        street = lv_cands(data)
        if not street and not cands:
            raise Skip(f"no street '{a.street}' in the register (Latvian letters are needed)")
        lk.cands += street
    return lk


def lv_cands(data: dict) -> list[dict]:
    out = []
    for r in (data.get("result") or {}).get("records") or []:
        parts = [p.strip() for p in (r.get("STD") or "").split(",")]
        num = r.get("NOSAUKUMS") or ""
        if not parts or not re.match(r"\d", num) or not parts[0].endswith(num):
            continue  # a named rural house, or a row that is not "street number"
        out.append(cand("address", number=num, street=parts[0][: -len(num)].strip(), postcode=r.get("ATRIB"),
                        city=parts[1] if len(parts) > 2 else None, lat=r.get("DD_N"), lon=r.get("DD_E"),
                        id=r.get("KODS"), formatted=r.get("STD"), source="VZD", license="CC BY 4.0"))
    return out


# ------------------------------------------------------------------------------------------ Poland

async def pl_gugik(ctx: Ctx, a: IntlAddress) -> Lookup:
    """GUGiK's address service over the national register (PRG). It needs "City, Street Number" and matches
    street names loosely (Wiejska also finds Nowowiejska), so every hit is compared again."""
    if not a.city:
        raise Skip("the Polish register search needs the town")
    street = _strip_prefix(a.street, "ul|ulica|al|aleja|pl|plac|os|osiedle")
    cands: list[dict] = []
    for num in dict.fromkeys((a.number, a.number_base)):  # "4/6/8" as written, then "99" for "99/5" (flat 5)
        data = await ctx.get("pl_gugik", config.PL_URL, {"request": "GetAddress", "address": f"{a.city}, {street} {num}",
                                                         "srid": "4326"})
        cands = pl_cands(data)
        if cands:
            break
    lk = Lookup(cands=cands)
    if not any(c["kind"] == "address" and c["number"] for c in cands):
        data = await ctx.get("pl_gugik", config.PL_URL, {"request": "GetAddress", "address": f"{a.city}, {street}", "srid": "4326"})
        lk.cands += pl_cands(data)
    return lk


def pl_cands(data: dict) -> list[dict]:
    out = []
    kind = {"address": "address", "street": "street"}.get(data.get("type"))
    if not kind:
        return out
    for r in (data.get("results") or {}).values():
        if kind == "address" and not r.get("number"):
            continue
        out.append(cand(kind, number=r.get("number") if kind == "address" else None, street=r.get("street"),
                        postcode=r.get("code"), city=r.get("city"), lat=r.get("y"), lon=r.get("x"), id=r.get("id"),
                        source="PRG", license="Open data"))
    return out


# ----------------------------------------------------------------------------------------- Czechia

_CZ_ADDR_RE = re.compile(r"^(?P<street>.+?)\s+(?:č\.\s?(?:p|ev)\.\s*)?(?P<cp>\d+)(?:/(?P<co>\d+[a-zA-Z]?))?, "
                         r"(?:(?P<part>[^,]+), )?(?P<psc>\d{5}) (?P<city>.+)$")


async def cz_ruian(ctx: Ctx, a: IntlAddress) -> Lookup:
    """ČÚZK's RÚIAN geocoder. Czech numbers are "building/orientation" (151/1a): either one, or both, may be
    written. Candidates come back by number prefix ("1" finds 151/1a), so each is compared again."""
    pc = _pc_digits(a)
    q = ", ".join(p for p in (f"{a.street} {a.number}".strip(), f"{pc} {a.city}".strip()) if p)
    data = await ctx.get("cz_ruian", config.CZ_URL, {"SingleLine": q, "outSR": 4326, "maxLocations": 20, "f": "json"})
    lk = Lookup(cands=cz_cands(data))
    if not any(c["kind"] == "address" and norm_eq(c["number"], a.number) for c in lk.cands):
        data = await ctx.get("cz_ruian", config.CZ_URL, {"SingleLine": ", ".join(p for p in (a.street, f"{pc} {a.city}".strip()) if p),
                                                         "outSR": 4326, "maxLocations": 50, "f": "json"})
        lk.cands += cz_cands(data)
    return lk


def cz_cands(data: dict) -> list[dict]:
    out = []
    for c in data.get("candidates") or []:
        typ = (c.get("attributes") or {}).get("Type")
        loc = c.get("location") or {}
        addr = c.get("address") or ""
        if typ == "AdresniMisto" and (m := _CZ_ADDR_RE.match(addr)):
            common = dict(street=m.group("street"), postcode=m.group("psc"), city=m.group("city"), lat=loc.get("y"),
                          lon=loc.get("x"), formatted=addr, source="RÚIAN", license="CC BY 4.0")
            numbers = [m.group("co"), m.group("cp"), f"{m.group('cp')}/{m.group('co')}" if m.group("co") else None]
            for num in dict.fromkeys(n for n in numbers if n):  # the same place under each way of writing it
                out.append(cand("address", number=num, **common))
        elif typ == "Ulice":
            out.append(cand("street", street=addr.split(",")[0].strip(), lat=loc.get("y"), lon=loc.get("x"),
                            formatted=addr, source="RÚIAN", license="CC BY 4.0"))
    return out


# ---------------------------------------------------------------------------------------- Slovenia

async def si_gurs(ctx: Ctx, a: IntlAddress) -> Lookup:
    """GURS's register of house numbers (OGC API Features). Villages have no streets: their name is the street."""
    name = _cql(a.street)
    where = f"(ULICA_NAZIV ILIKE {name} OR NASELJE_NAZIV ILIKE {name})"
    n, pc = _digits(a.number_base), _pc_digits(a)
    cands: list[dict] = []
    if n:
        for place in ([f"POSTNI_OKOLIS_SIFRA={pc}"] if pc else []) + [""]:
            cands = si_cands(await _si(ctx, " AND ".join(p for p in (where, f"HS_STEVILKA={n}", place) if p), 50))
            if cands:
                break
    lk = Lookup(cands=cands)
    if not any(norm_eq(c["number"], a.number_base) for c in cands):
        street = si_cands(await _si(ctx, " AND ".join(p for p in (where, f"POSTNI_OKOLIS_SIFRA={pc}" if pc else "") if p), 200))
        if not street and not cands:
            raise Skip(f"no street '{a.street}' in the register")
        lk.cands += street
    return lk


async def _si(ctx: Ctx, cql: str, limit: int) -> dict:
    return await ctx.get("si_gurs", config.SI_URL, {"f": "application/geo+json", "filter-lang": "cql-text",
                                                     "filter": cql, "limit": limit})


def si_cands(data: dict) -> list[dict]:
    out = []
    for f in data.get("features") or []:
        p, (lon, lat) = f.get("properties") or {}, _point(f.get("geometry"))
        if p.get("HS_STEVILKA") is None:
            continue
        number = f"{p['HS_STEVILKA']}{(p.get('HS_DODATEK') or '').lower()}"
        street = p.get("ULICA_NAZIV") or p.get("NASELJE_NAZIV")
        out.append(cand("address", number=number, street=street, street_alt=[p.get("NASELJE_NAZIV") or ""],
                        postcode=p.get("POSTNI_OKOLIS_SIFRA"), city=p.get("POSTNI_OKOLIS_NAZIV"), lat=lat, lon=lon,
                        id=p.get("EID_HISNA_STEVILKA"),
                        formatted=f"{street} {number}, {p.get('POSTNI_OKOLIS_SIFRA')} {p.get('POSTNI_OKOLIS_NAZIV')}",
                        source="GURS", license="CC BY 4.0"))
    return out
