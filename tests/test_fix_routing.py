"""Which pipeline a request goes to: the US cases a review found sent abroad (or refused), and the European / Japanese
lines that stopped being recognised. Offline."""

import pytest
from fastapi import HTTPException

from app.intl import countries as C
from app.main import _country
from app.models import VerifyRequest
from batch_line import as_request


def route(**req) -> str:
    try:
        return _country(VerifyRequest(**as_request(req)))
    except HTTPException as e:
        assert e.status_code == 422
        return "422"


@pytest.mark.parametrize("req", [
    # a structured US row without a state: the town is spelled like a country
    {"street": "270 S River Ave", "city": "Holland", "zip": "49423"},
    {"street": "300 N Coal St", "city": "Mexico", "zip": "65265"},
    {"street": "1 Main St", "city": "Greece", "zip": "14616"},
    {"street": "1 Main St", "city": "Poland", "state": "", "zip": "44514"},
    {"street": "100 Main St", "city": "Sweden", "state": "N/A", "zip": "40148"},
    {"street": "1 Main St", "city": "Canada", "zip": "41519"},
    # a bare town named like a country right after the street, no state or ZIP
    {"address": "300 N Coal St, Mexico"}, {"address": "270 S River Ave, Holland"}, {"address": "1 Main St, Norway"},
    {"address": "1 Main St, Wales"}, {"address": "100 Main St, China"}, {"address": "1234 Main St, Norway"},
    # qualifiers and US-state look-alikes
    {"address": "100 Main St, New Holland"}, {"address": "1 Main St, Mercer Island"}, {"address": "100 Main St, North Wales"},
    {"address": "123 Mulberry St, Little Italy"}, {"address": "1 Main St, Boston, MA, IT"},
    {"address": "123 Main St, Springfield, IL, 62701"}, {"address": "350 5th ave new york ny 10118"},
    {"address": "PSC 1234 Box 5678, APO AE 09012"},
    # US streets and phone numbers that look Japanese
    {"address": "200 Ala-Ku St, Honolulu"}, {"address": "Sushi Nara, 123 Main St, Springfield, Tel 555-1234"},
    {"address": "7 Tokyo Ave 123-4567"}, {"address": "150 Nara Rd 555-1234"},
    # Chinese addresses are not Japanese (they go to the US pipeline, which says it can't find them)
    {"address": "深圳市南山区科技园1-2-3"}, {"address": "天津市和平区南京路1-1"}, {"address": "北京市朝阳区建国路88号"},
])
def test_us_requests_stay_us(req):
    assert route(**req) == "US"


@pytest.mark.parametrize("text, code", [
    ("Unter den Linden 77, Berlin Germany", "DE"), ("10 Downing Street, London England", "GB"),
    ("1 Grafton Street, Dublin 2 Ireland", "IE"), ("Via Roma 1, Roma Italy", "IT"), ("Damrak 1, Amsterdam Holland", "NL"),
    ("Karl Johans gate 22, Oslo Norway", "NO"), ("damrak 1, 1012 lg amsterdam netherlands", "NL"),
    ("Laugavegur 1, 101 Reykjavík Ísland", "IS"), ("10 Downing Street London United Kingdom", "GB"),
    ("Via Roma 10, 20121, Milano Italy", "IT"), ("Unter den Linden 77, Berlin, Germany 10117", "DE"),
    ("11 High Street, Oxford, England, OX1 4AA", "GB"), ("10 Downing Street, London UK", "GB"),
    ("Keizersgracht 123, Amsterdam, Holland", "NL"), ("Damrak 1, 1012 LG Amsterdam, Holland", "NL"),
    ("France, 75001 Paris, 10 Rue de Rivoli", "FR"), ("Poland, 00-693 Warszawa, ul. Marszałkowska 99A", "PL"),
    ("Italia, Via Roma 10, 20121 Milano MI", "IT"), ("Unter den Linden 77, 10117 Berlin, DE", "DE"),
    ("1 Main St, Toronto, ON M5V 2T6, Canada", "CA"), ("Tverskaya 7, Moscow 125009, Russia", "RU"),
    # Japanese lines without a prefecture
    ("京都市北区金閣寺町1", "JP"), ("京都市左京区吉田本町36", "JP"), ("京都市上京区今出川通烏丸東入玄武町601", "JP"),
    ("京都市下京区東塩小路町721", "JP"), ("603-8361 京都市北区金閣寺町1", "JP"), ("府中市宮西町2", "JP"),
    ("宇都宮市本町1", "JP"), ("福岡市博多区博多駅中央街1-1", "JP"), ("横滨市中区本町6-50-10", "JP"),
])
def test_country_named_in_the_line(text, code):
    assert C.country_in_text(text) == code


@pytest.mark.parametrize("value, code", [
    ("美国(US)", "US"), ("US(美国)", "US"), ("United States (US)", "US"), ("US - United States", "US"),
    ("Germany (DE)", "DE"), ("德国（DE）", "DE"), ("DE - Germany", "DE"), ("[JP] Japan", "JP"),
])
def test_erp_country_labels(value, code):
    assert C.resolve_country(value) == code


def test_conflicting_labels_are_not_guessed():
    assert C.known_country("Germany (FR)") is None


@pytest.mark.parametrize("req, code", [
    ({"address": "1 Broad Street, St Helier JE2 3RR", "country": "GB"}, "JE"),
    ({"street": "1 Smith Street", "city": "St Peter Port", "zip": "GY1 2JG", "country": "UK"}, "GG"),
    ({"address": "1 Prospect Hill, Douglas IM1 1ET, United Kingdom"}, "IM"),
    ({"address": "10 Downing Street, London SW1A 2AA", "country": "GB"}, "GB"),
])
def test_crown_dependencies_written_as_the_uk(req, code):
    assert route(**req) == code


@pytest.mark.parametrize("req, code", [
    ({"street": "本町1-1-1", "city": "中央区", "state": "大阪府"}, "JP"),
    ({"street": "西新宿2-8-1", "city": "Shinjuku-ku", "state": "Tokyo", "zip": "160-0023"}, "JP"),
    ({"street": "Unter den Linden 77", "city": "Berlin, Germany", "zip": "10117"}, "DE"),
    ({"street": "11 High Street", "city": "Oxford", "state": "England", "zip": "OX1 4AA"}, "GB"),
    ({"street": "42 Place Guillaume II", "city": "Luxembourg", "zip": "L-1648"}, "LU"),
    ({"street": "Unter den Linden 77, 10117 Berlin, Germany"}, "DE"),
    ({"street": "西新宿2-8-1", "city": "新宿区"}, "JP"), ({"street": "金閣寺町1", "city": "京都市北区"}, "JP"),
])
def test_structured_rows_that_name_a_country(req, code):
    assert route(**req) == code


def test_unsupported_country_message_is_bilingual():
    with pytest.raises(HTTPException) as e:
        _country(VerifyRequest(address="Tverskaya 7, Moscow 125009, Russia"))
    assert "unsupported country" in e.value.detail and "本服务只核验" in e.value.detail


def test_deep_health_needs_the_key_and_runs_once_a_minute(monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app import main

    calls = []

    class FakeUp:
        cache, status = {}, {}

        async def nad_count(self):
            calls.append("nad")
            return 1

        async def ping(self):
            calls.append("ping")

        async def _get_json(self, service, url, params, cache=True):
            calls.append(service)

    monkeypatch.setattr(main.app.state, "upstream", FakeUp(), raising=False)
    monkeypatch.setattr(main.app.state, "overture", None, raising=False)
    monkeypatch.setattr(main, "INTL_PROBES", {"photon": ("u", {})})
    monkeypatch.setattr(main, "_deep", {"at": 0.0, "body": {}, "lock": None})
    monkeypatch.setattr(config, "API_KEYS", {"k"})
    client = TestClient(main.app)
    assert client.get("/health?deep=true").status_code == 401
    assert client.get("/health").status_code == 200 and not calls
    first = client.get("/health?deep=true", headers={"x-api-key": "k"}).json()
    assert first["nad_point_count"] == 1 and sorted(calls) == ["nad", "photon", "ping"]
    again = client.get("/health?deep=true", headers={"x-api-key": "k"}).json()
    assert again["nad_point_count"] == 1 and "deep_age_s" in again and len(calls) == 3
