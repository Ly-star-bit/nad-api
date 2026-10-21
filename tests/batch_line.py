"""A spreadsheet row's columns joined into one line the way the batch page (app/static/batch.html, oneLine) sends
them: "street unit, city, state zip"; columns written in kanji the Japanese way, largest place first."""

import re

_CJK = re.compile(r"[぀-ヿ一-鿿]")


def one_line(street="", unit="", city="", state="", zip="", **_) -> str:
    if _CJK.search(street) and all(not v or _CJK.search(v) for v in (city, state)):
        return " ".join(p for p in (zip, state + city + street, unit) if p)
    return ", ".join(p for p in (" ".join(p for p in (street, unit) if p), city, f"{state} {zip}".strip()) if p)


def as_request(req: dict) -> dict:
    """A structured request -> {address, country}."""
    if "street" not in req:
        return req
    return {"address": one_line(**req), **({"country": req["country"]} if req.get("country") else {})}
