"""Japan: 国土地理院 (GSI) address search + Japan Post postcode tables.

Japanese addresses are blocks, not streets: 町 + 丁目 + 番 (block) + 号 (building), or in rural areas a parcel
number (地番, "978-6"). GSI's search answers with the deepest level it knows - "西新宿二丁目８番" - and silently drops
what it doesn't, so the verdict compares that level with what was asked:

- 号 matched: verified.
- only 番 known (most of central Tokyo has no 号 data): verified to the block; the 号 cannot be checked.
- the 丁目 exists but not this 番: street_only; the town exists but not this 丁目: not_found.
- rural parcel areas where GSI stops at the town: not_covered.

Addresses written in romaji ("2-8-1 Nishishinjuku, Shinjuku-ku, Tokyo 163-8001") cannot be searched at all, so the
postcode is looked up in Japan Post's tables (free, monthly) and the address rebuilt in kanji. The same tables
check that a postcode belongs to the town.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import re
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import config
from ..upstream import UpstreamError, UpstreamRejected
from .base import Ctx, ProviderInfo

log = logging.getLogger(__name__)

INFO = {"jp_gsi": ProviderInfo("jp_gsi", "国土地理院 住所検索", "official", "https://maps.gsi.go.jp",
                               "国土地理院コンテンツ利用規約（PDL1.0 互換）", "出典：国土地理院ウェブサイト; 日本郵便 郵便番号データ")}

_KANJI_DIGITS = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_NUM = "〇一二三四五六七八九十"
_DASHES = "‐‑‒–—―−ー－ｰ─━"


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


def _kanji_before(text: str, markers: str) -> str:
    """Kanji numerals in front of 丁目 / 番 / 号 / 条 / 線 -> digits: '二丁目八番' -> '2丁目8番'."""
    return re.sub(rf"([{_KANJI_NUM}]+)(?=(?:{markers}))", lambda m: str(kanji_int(m.group(1))), text)


def jp_text(text: str) -> str:
    """NFKC (full-width digits and hyphens), dash-like characters between digits -> '-', 'の' between numbers."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(rf"(?<=\d)\s*[{_DASHES}-]\s*(?=\d)", "-", t)
    t = re.sub(r"(?<=\d)の(?=\d)", "-", t)
    return re.sub(r"\s+", " ", t).strip()


_CMP = str.maketrans({"ヶ": "ケ", "ヵ": "カ", "が": "ケ", "ノ": "の", "之": "の", "﨑": "崎", "髙": "高", "邊": "辺",
                      "邉": "辺", "濵": "浜", "德": "徳", "瀨": "瀬", "舘": "館", "冨": "富"})


def town_key(text: str) -> str:
    """For comparing place names: kanji numerals -> digits, 大字 / 字 dropped, variant kana and kanji unified."""
    t = jp_text(text).replace(" ", "").replace("大字", "").replace("字", "")
    t = re.sub(r"(?<=[都道府県])[^都道府県市区町村郡]{1,6}郡", "", t)  # GSI writes ニセコ町, not 虻田郡ニセコ町
    t = re.sub(r"（.*?）|\(.*?\)", "", t)
    t = re.sub(rf"[{_KANJI_NUM}]+", lambda m: str(kanji_int(m.group())) if kanji_int(m.group()) is not None else m.group(), t)
    return t.translate(_CMP)


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
    warnings: list[str] = field(default_factory=list)

    @property
    def number(self) -> str:
        return "-".join(map(str, self.nums))


_PC_RE = re.compile(r"〒?\s*(?<!\d)(\d{3})-?(\d{4})(?!\d)")
_COUNTRY_RE = re.compile(r"^\s*(?:日本(?:国)?|japan)\s*[,、]?\s*|\s*[,、]?\s*(?:日本(?:国)?|japan)\s*$", re.I)


def parse_jp(text: str) -> JpAddress:
    t = _COUNTRY_RE.sub("", jp_text(text))
    a = JpAddress(raw=jp_text(text))
    if m := _PC_RE.search(t):
        a.postcode = m.group(1) + m.group(2)
        t = (t[: m.start()] + " " + t[m.end():]).strip(" ,、")
    a.romaji = not re.search(r"[぀-ヿ㐀-鿿]", t)
    return _parse_romaji(a, t) if a.romaji else _parse_kanji(a, t)


def _parse_kanji(a: JpAddress, t: str) -> JpAddress:
    t = _kanji_before(t, "丁目|番地|番|号|条|線|地割")
    t = re.sub(r"\s*-\s*", "-", t)
    # the numbers start at the first digit that is not part of a town name (北1条西, 東2線, 第3地割)
    for m in re.finditer(r"\d+", t):
        if not re.match(r"条|線|地割", t[m.end():]):
            start = m.start()
            break
    else:
        a.prefix = re.sub(r"[\s,、]", "", t)
        a.warnings.append("no block / house number (番・号 or 番地) found")
        return a
    a.prefix = re.sub(r"[\s,、]", "", t[:start])
    pos = start
    while m := re.compile(r"(\d+)\s*(丁目|番地|番|号|-)?\s*").match(t, pos):
        if m.group(2) == "丁目":
            if a.nums:
                break
            a.chome = True
        a.nums.append(int(m.group(1)))
        pos = m.end()
        if not m.group(2) or m.group(2) == "号":
            break
    a.rest = t[pos:].strip(" ,、")
    _cap(a)
    return a


def _parse_romaji(a: JpAddress, t: str) -> JpAddress:
    t = re.sub(r"(\d+)\s*-?\s*chome\s*[-,]?\s*", r"\1-", t, flags=re.I)
    if room := re.search(r"(?:\b(?:apt|apartment|room|rm|unit|suite|flat)\.?|#)\s*([\w-]+)", t, re.I):
        a.rest = room.group(0).strip()
        t = (t[: room.start()] + t[room.end():]).strip(" ,")
    if m := re.search(r"(?<![\d-])(\d+)-(\d+)(?:-(\d+))?(?:-(\d+))?", t):
        a.nums = [int(g) for g in m.groups() if g]
    elif m := re.search(r"(?<![\d-])(\d+)(?![\d-])", t):
        a.nums = [int(m.group(1))]
    else:
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


# ------------------------------------------------------------------------------------- Japan Post

@dataclass
class PostRow:
    postcode: str
    pref: str
    city: str
    town: str
    town_r: str = ""
    city_r: str = ""
    business: str = ""


class JapanPost:
    """Japan Post's postcode files (KEN_ALL_ROME: every town with its romaji; JIGYOSYO: large-business codes),
    downloaded into DATA_DIR and refreshed monthly. Until they are loaded, romaji addresses cannot be verified and
    postcodes are not checked."""

    FILES = {"KEN_ALL_ROME.zip": "roman/KEN_ALL_ROME.zip", "jigyosyo.zip": "office/zip/jigyosyo.zip"}

    def __init__(self, http: httpx.AsyncClient, data_dir: Path) -> None:
        self.http, self.dir = http, data_dir / "japanpost"
        self.by_pc: dict[str, list[PostRow]] = {}
        self.by_town: dict[str, set[str]] = {}
        self.loaded_at = 0.0
        self.error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.by_pc)

    async def ensure(self) -> bool:
        if self.ready and time.time() - self.loaded_at < 30 * 86400:
            return True
        async with self._lock:
            if self.ready and time.time() - self.loaded_at < 30 * 86400:
                return True
            try:
                await self._download()
                by_pc, by_town = await asyncio.to_thread(self._read)
            except Exception as exc:  # keep any older tables
                self.error = f"Japan Post postcode files unavailable: {exc}"
                log.warning(self.error)
                if self.ready:
                    self.loaded_at = time.time() - 29 * 86400  # retry in a day
                return self.ready
            self.by_pc, self.by_town, self.loaded_at, self.error = by_pc, by_town, time.time(), None
            return True

    async def _download(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, path in self.FILES.items():
            f = self.dir / name
            if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
                continue
            try:
                resp = await self.http.get(f"{config.JAPANPOST_URL}/{path}", timeout=60)
                resp.raise_for_status()
                zipfile.ZipFile(io.BytesIO(resp.content)).testzip()
            except Exception:
                if f.exists():  # stale copy beats none
                    continue
                raise
            tmp = f.with_suffix(f".{os.getpid()}.part")  # several workers may download at once
            tmp.write_bytes(resp.content)
            tmp.replace(f)

    def _read(self) -> tuple[dict[str, list[PostRow]], dict[str, set[str]]]:
        by_pc: dict[str, list[PostRow]] = {}
        by_town: dict[str, set[str]] = {}
        for row in _csv_rows(self.dir / "KEN_ALL_ROME.zip"):
            pc, pref, city, town, _, city_r, town_r = row[:7]
            town, town_r = _clean_town(town), _clean_town(town_r)
            if town is None:
                continue  # the middle of a long town name split over several rows
            r = PostRow(pc, pref, city.replace("　", "").replace(" ", ""), town, town_r or "", city_r)
            by_pc.setdefault(pc, []).append(r)
            by_town.setdefault(town_key(r.pref + r.city + r.town), set()).add(pc)
        for row in _csv_rows(self.dir / "jigyosyo.zip"):
            name, pref, city, town, detail, pc = row[2], row[3], row[4], row[5], row[6], row[7]
            by_pc.setdefault(pc, []).append(PostRow(pc, pref, city.replace("　", ""), town + detail, business=name))
        return by_pc, by_town

    def rows(self, pc: str) -> list[PostRow]:
        return self.by_pc.get(pc, [])

    def by_romaji(self, letters: str) -> list[PostRow]:
        """Towns whose romaji name and city ('SETAGAYA KU' -> SETAGAYA) both appear in the address letters; the
        longest town name wins ('NISHISHINJUKU' over 'SHINJUKU')."""
        hits = []
        for rows in self.by_pc.values():
            for r in rows:
                town = re.sub(r"[^A-Z]", "", r.town_r.upper())
                if len(town) < 3 or town not in letters:
                    continue
                # 'SAPPORO SHI CHUO KU' -> SAPPORO, CHUO: every part must be in the address (in any order)
                parts = [w for w in re.sub(r"[^A-Z ]", "", r.city_r.upper()).split() if w not in _CITY_WORDS]
                rest = letters.replace(town, "", 1)
                if parts and all(p in rest for p in parts):
                    hits.append((len(town), r))
        if not hits:
            return []
        top = max(n for n, _ in hits)
        return [r for n, r in hits if n == top]

    def postcodes_for(self, place: str) -> list[str]:
        """Postcodes of a town (prefecture + city + town, any 丁目 removed); [] when unknown."""
        k = town_key(re.sub(r"\d+丁目$", "", _kanji_before(jp_text(place), "丁目")))
        return sorted(self.by_town.get(k, ()))


_CITY_WORDS = {"SHI", "KU", "MACHI", "CHO", "MURA", "SON", "GUN", "TO", "KEN", "FU", "DO"}


def _csv_rows(path: Path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            with z.open(name) as f:
                yield from csv.reader(io.TextIOWrapper(f, encoding="cp932", errors="replace"))


def _clean_town(t: str) -> str | None:
    """'西新宿（次のビルを除く）' -> '西新宿'; '以下に掲載がない場合' -> ''; the continuation rows of a long
    parenthesised list -> None."""
    t = t.strip()
    if t in ("以下に掲載がない場合", "IKANIKEISAIGANAIBAAI") or t.endswith(("の次に番地がくる場合", "一円")):
        return ""
    if ("）" in t and "（" not in t) or (")" in t and "(" not in t):
        return None
    return re.sub(r"\s*[（(].*$", "", t)


# ---------------------------------------------------------------------------------------- GSI

_TITLE_RE = re.compile(r"^(?P<town>.*?)(?:(?P<chome>\d+)丁目)?(?:(?P<ban>\d+)(?P<bs>番地|番))?(?:(?P<go>\d+)号)?$")


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
    title = (feature.get("properties") or {}).get("title") or ""
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    t = _kanji_before(jp_text(title), "丁目").replace(" ", "")
    m = _TITLE_RE.match(t)
    if not m or len(coords) < 2:
        return None
    n = lambda k: int(m.group(k)) if m.group(k) else None  # noqa: E731
    return Title(title, m.group("town"), n("chome"), n("ban"), m.group("bs") == "番地", n("go"), coords[1], coords[0])


def town_matches(want: str, have: str) -> bool:
    """The typed place (any prefix of 都道府県 + 市区町村 + 町, possibly with parts left out) is the GSI town."""
    w, h = town_key(want), town_key(have)
    if not w or not h:
        return False
    if h.endswith(w):
        return True
    tail = re.split(r"[都道府県市区郡]", w)[-1] if re.search(r"[都道府県市区郡].", w) else ""
    return len(tail) >= 2 and h.endswith(tail) and w[: -len(tail)] in h


async def verify_jp(ctx: Ctx, jpost: JapanPost | None, text: str) -> dict[str, Any]:
    """-> the fields of a verdict (status, best_match, ...); core.py wraps them into the response."""
    a = parse_jp(text)
    notes = list(a.warnings)
    timing: dict[str, float] = {}
    res: dict[str, Any] = {"jp": a, "notes": notes, "timing": timing, "status": None, "geocode": None,
                           "best": None, "level": None, "zip_match": None, "zip_should_be": None}
    if a.romaji:
        await _from_romaji(jpost, a, notes)
        if not a.prefix:
            res["status"] = "geocode_failed"
            return res
    if not a.nums:
        res["status"] = "invalid_input"
        return res
    if not a.prefix:
        res["status"] = "invalid_input"
        notes.append("no prefecture / city / town before the numbers")
        return res

    q = a.prefix + (f"{a.nums[0]}丁目" + "-".join(map(str, a.nums[1:])) if a.chome else "-".join(map(str, a.nums)))
    t = time.perf_counter()
    try:
        data = await ctx.get("jp_gsi", config.GSI_URL, {"q": q})
    except UpstreamRejected:
        data = []
    timing["geocode"] = round((time.perf_counter() - t) * 1000, 1)
    titles = [x for x in (parse_title(f) for f in (data or [])[:100]) if x]
    mine = [x for x in titles if town_matches(a.prefix, x.town)]
    if not mine:
        res["status"] = "geocode_failed"
        notes.append(f"国土地理院 has no place '{a.prefix}'" + (f"; nearest: {titles[0].raw}" if titles else ""))
        return res
    order = {"go": 0, "ban": 1, "chome": 2, "town": 3}
    best = min(mine, key=lambda x: order[x.level])
    res["best"] = best
    res["geocode"] = {"source": "gsi", "lat": best.lat, "lon": best.lon, "matched_address": best.raw}
    chome_town = None
    if best.level == "town" and not a.chome:
        # "西新宿9-1-1" came back as just 西新宿: either the town has no 9丁目, or it is a parcel (地番) area GSI has
        # no numbers for. Asking for its 1丁目 tells which.
        probe = await ctx.get("jp_gsi", config.GSI_URL, {"q": f"{best.town}1丁目"})
        chome_town = any((t := parse_title(f)) and t.chome is not None and town_key(t.town) == town_key(best.town)
                         for f in (probe or [])[:5])
    _decide(a, best, res, chome_town)
    if jpost is not None:
        await _postcode_check(jpost, a, best, res)
    return res


def _decide(a: JpAddress, b: Title, res: dict, chome_town: bool | None = None) -> None:
    notes = res["notes"]
    chome_area = b.chome is not None or a.chome or bool(chome_town)
    nums = list(a.nums)
    if chome_area:
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
        res["status"], res["level"] = "verified", "block"
        notes.append("国土地理院 data here only goes down to the block (番); the 号 could not be checked"
                     if len(rest) > 1 else "the address has no 号 (building number); only the block was checked")
        return
    # parcel-number (地番) areas: 978-6 = 番地 978, branch 6
    if b.ban is None:
        res["status"] = "not_covered"
        notes.append(f"国土地理院 has no parcel numbers for {b.town}, so {a.number} cannot be checked "
                     "(common in rural areas that use 地番 addresses)")
        return
    if b.ban != nums[0]:
        res["status"] = "not_covered"
        notes.append(f"国土地理院 matched {b.raw}, not parcel {nums[0]}")
        return
    if len(nums) > 1 and b.go != nums[1]:
        res["status"], res["level"] = "verified", "block"
        notes.append(f"parcel {b.ban}番地 exists; the branch number -{nums[1]} could not be checked")
        return
    res["status"], res["level"] = "verified", "address"


async def _from_romaji(jpost: JapanPost | None, a: JpAddress, notes: list[str]) -> None:
    """Romaji address + postcode -> kanji prefix from Japan Post's table (GSI only reads Japanese)."""
    if jpost is None or not await jpost.ensure():
        notes.append("the Japan Post postcode table is not available, so a romaji address cannot be read"
                     + (f" ({jpost.error})" if jpost and jpost.error else ""))
        a.prefix = ""
        return
    letters = re.sub(r"[^A-Z]", "", a.raw.upper())
    if not a.postcode:
        rows = jpost.by_romaji(letters)
        towns = {(r.pref, r.city, r.town) for r in rows}
        if len(towns) != 1:
            notes.append("add the postcode (〒): 国土地理院 only reads Japanese, and "
                         + (f"the town and city names match several places ({', '.join(r.town + ' ' + r.city for r in rows[:4])})"
                            if towns else "the town and city names were not found in Japan Post's list"))
            a.prefix = ""
            return
        r = rows[0]
        a.prefix = r.pref + r.city + r.town
        notes.append(f"romaji address read via Japan Post's town list as {a.prefix}{a.number}")
        return
    rows = jpost.rows(a.postcode)
    if not rows:
        notes.append(f"postcode {a.postcode[:3]}-{a.postcode[3:]} does not exist (Japan Post)")
        a.prefix = ""
        return
    named = [r for r in rows if r.town_r and re.sub(r"[^A-Z]", "", r.town_r.upper()) in letters]
    if named:
        r = max(named, key=lambda r: len(r.town_r))
    elif len({(r.pref, r.city, r.town) for r in rows}) == 1:
        r = rows[0]
    else:
        notes.append(f"postcode {a.postcode} covers several towns ({', '.join(r.town_r or r.town for r in rows[:6])}) "
                     "and none of them is named in the address")
        a.prefix = ""
        return
    town = re.sub(r"\d.*$", "", _kanji_before(jp_text(r.town), "丁目|番地|番|号")) if r.business else r.town
    a.prefix = r.pref + r.city + town
    notes.append(f"romaji address read via postcode {a.postcode[:3]}-{a.postcode[3:]} as {a.prefix}{a.number}")


async def _postcode_check(jpost: JapanPost, a: JpAddress, b: Title, res: dict) -> None:
    if not await jpost.ensure():
        return
    place = b.town + (f"{b.chome}丁目" if b.chome is not None else "")
    should = jpost.postcodes_for(place)
    if not a.postcode:
        res["zip_should_be"] = should[0] if len(should) == 1 else None
        return
    rows = jpost.rows(a.postcode)
    key = town_key(b.town)
    ok = any(key == town_key(r.pref + r.city + re.sub(r"\d.*$", "", _kanji_before(jp_text(r.town), "丁目")))
             or (not r.town and key.startswith(town_key(r.pref + r.city))) for r in rows)
    if ok or a.postcode in should:
        res["zip_match"] = True
        return
    res["zip_match"] = False
    res["zip_should_be"] = should[0] if should else None
    res["notes"].append(f"postcode {a.postcode[:3]}-{a.postcode[3:]} is "
                        + (f"for {rows[0].pref}{rows[0].city}{rows[0].town or ''}" if rows else "not a Japan Post postcode")
                        + (f"; {b.town} is {', '.join(p[:3] + '-' + p[3:] for p in should[:3])}" if should else ""))
