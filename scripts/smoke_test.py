"""Live end-to-end check against a running server (hits the real upstream services).

usage: python scripts/smoke_test.py [base_url]   (default http://127.0.0.1:8000)
"""

import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")

# (request body, expected status, expected data_source)
CASES = [
    ({"address": "1700 W Washington St, Phoenix, AZ 85007"}, "verified", "nad"),
    ({"address": "350 5th Ave, New York, NY 10118"}, "verified", "nad"),
    ({"address": "980 9th St Ste 1750, Sacramento, CA 95814"}, "verified", "nad"),
    ({"address": "980 9th St Ste 9999, Sacramento, CA 95814"}, "unit_not_found", "nad"),
    ({"street": "11 W Washington St", "unit": "Suite 240", "city": "Phoenix", "state": "AZ", "zip": "85003"}, "verified", "nad"),
    ({"address": "77 Fair Dr, Costa Mesa, CA 92626"}, "verified", "nad"),
    ({"address": "13600 W Lake View Rd, Raymond, NE 68428"}, "verified", "nad"),
    ({"address": "1716 W Washington St, Phoenix, AZ 85007"}, "street_only", "nad"),
    # Counties missing from NAD -> answered by the Overture fallback (county data via OpenAddresses)
    ({"address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043"}, "verified", "overture"),
    ({"address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043", "overture_fallback": False}, "not_covered", "nad"),
    ({"address": "1 Civic Center Plaza, Irvine, CA 92606"}, "verified", "overture"),
    ({"address": "108 Bryant St Unit 32, Mountain View, CA 94041"}, "verified", "overture"),
    ({"address": "200 N Spring St, Los Angeles, CA 90012"}, "verified", "overture"),  # Census says S Spring
    ({"address": "1 Dr Carlton B Goodlett Pl, San Francisco, CA 94102"}, "verified", "overture"),
    ({"address": "123 Fakestreet Blvd, Phoenix AZ 85007"}, "geocode_failed", None),
    ({"address": "106 Quivira Rd, Marquette, KS 67464", "zip_fallback": True}, "verified", "nad"),
    ({"address": "asdkjh qwe zzz"}, "invalid_input", None),
]

failed = 0
with httpx.Client(timeout=90) as c:
    for body, expected, source in CASES:
        t = time.perf_counter()
        r = c.post(f"{BASE}/v1/verify", json=body)
        ms = (time.perf_counter() - t) * 1000
        d = r.json()
        ok = r.status_code == 200 and d.get("status") == expected and d.get("data_source") == source
        failed += not ok
        best = (d.get("best_match") or {}).get("formatted", "")
        label = (body.get("address") or body.get("street")) + ("" if body.get("overture_fallback", True) else " [no fallback]")
        got = f"{d.get('status', r.status_code)}/{d.get('data_source')}"
        print(f"{'PASS' if ok else 'FAIL'} {ms:7.0f} ms  {got:<24} {label}  ->  {best}")
print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
sys.exit(1 if failed else 0)
