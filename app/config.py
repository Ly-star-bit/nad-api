"""Runtime configuration (all values overridable via environment variables)."""

import os


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# --- Upstream services (all free / public domain, no API key required) -------------

# USDOT National Address Database, hosted by USDOT on ArcGIS Online (~98M address points).
# Only OBJECTID and Shape are indexed upstream, so every query MUST carry a spatial filter.
NAD_LAYER_URL = os.getenv(
    "NAD_LAYER_URL",
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "Address_Points_from_National_Address_Database_view/FeatureServer/0",
)

# USDOT "NAD County Participation Status" polygon layer (which counties are in the NAD).
COVERAGE_LAYER_URL = os.getenv(
    "NAD_COVERAGE_LAYER_URL",
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NAD_County_Participation_Status/FeatureServer/2",
)

# US Census Bureau geocoder (TIGER/Line based, free, no key).
CENSUS_GEOCODER_URL = os.getenv("CENSUS_GEOCODER_URL", "https://geocoding.geo.census.gov/geocoder")
CENSUS_BENCHMARK = os.getenv("CENSUS_BENCHMARK", "Public_AR_Current")
CENSUS_VINTAGE = os.getenv("CENSUS_VINTAGE", "Current_Current")

# Census TIGERweb ZIP Code Tabulation Areas (used only for the optional ZIP fallback).
ZCTA_LAYER_URL = os.getenv(
    "ZCTA_LAYER_URL",
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/1",
)

# Overture Maps addresses (GeoParquet on public S3, read with DuckDB; no account or key).
# Used as a fallback when NAD cannot confirm an address - it also carries county/city data
# (via OpenAddresses) for many areas that are missing from the NAD.
OVERTURE_ENABLED = os.getenv("OVERTURE_ENABLED", "1") == "1"
OVERTURE_RELEASE = os.getenv("OVERTURE_RELEASE", "")  # empty = newest release, re-checked daily
OVERTURE_BUCKET = os.getenv("OVERTURE_BUCKET", "overturemaps-us-west-2")
OVERTURE_REGION = os.getenv("OVERTURE_REGION", "us-west-2")
OVERTURE_SEARCH_RADIUS_M = _env_int("OVERTURE_SEARCH_RADIUS_M", 1500)
OVERTURE_TIMEOUT_S = _env_float("OVERTURE_TIMEOUT_S", 30.0)
OVERTURE_MAX_CONCURRENCY = _env_int("OVERTURE_MAX_CONCURRENCY", 4)
OVERTURE_MEMORY_LIMIT = os.getenv("OVERTURE_MEMORY_LIMIT", "1GB")
# DuckDB threads. Each one makes one HTTP request at a time and mostly waits on the network, so more threads than
# cores pays off: the first query reads ~43 MB of Parquet metadata from 64 files (cold: 2.3 s at 32 threads,
# 9-26 s at 1-2 threads, measured from the US; slower again from Asia).
OVERTURE_THREADS = _env_int("OVERTURE_THREADS", 16)
# The start-up warm-up runs in the background, so it can wait much longer than a user's query.
OVERTURE_WARMUP_TIMEOUT_S = _env_float("OVERTURE_WARMUP_TIMEOUT_S", 180.0)

# --- Outside the US (Europe, Japan): see app/intl/ ---------------------------------

# Official registers with a free public API (no key).
FR_BAN_URL = os.getenv("FR_BAN_URL", "https://data.geopf.fr/geocodage/search")
NL_PDOK_URL = os.getenv("NL_PDOK_URL", "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free")
BE_VL_GEO_URL = os.getenv("BE_VL_GEO_URL", "https://geo.api.vlaanderen.be/geolocation/v4/Location")
BE_VL_REGISTER_URL = os.getenv("BE_VL_REGISTER_URL", "https://api.basisregisters.vlaanderen.be/v2/adressen")
BE_WA_URL = os.getenv("BE_WA_URL", "https://geoservices.wallonie.be/geocodeWS/geocode")
LU_URL = os.getenv("LU_URL", "https://apiv4.geoportail.lu/geocode/search")
CH_URL = os.getenv("CH_URL", "https://api3.geo.admin.ch/rest/services/api/SearchServer")
AT_URL = os.getenv("AT_URL", "https://kataster.bev.gv.at/api/all/")
ES_URL = os.getenv("ES_URL", "https://www.cartociudad.es/geocoder/api/geocoder/candidates")
NO_URL = os.getenv("NO_URL", "https://ws.geonorge.no/adresser/v1/sok")
FI_URL = os.getenv("FI_URL", "https://paikkatiedot.ymparisto.fi/geoserver/ryhti_building/ogc/features/v1/collections/"
                             "open_address/items")
IS_URL = os.getenv("IS_URL", "https://gis.fasteignaskra.is/geoserver/wfs")
EE_URL = os.getenv("EE_URL", "https://inaadress.maaamet.ee/inaadress/gazetteer")
LV_URL = os.getenv("LV_URL", "https://data.gov.lv/dati/api/3/action/datastore_search")
LV_RESOURCE = os.getenv("LV_RESOURCE", "a510737a-18ce-400f-ad4b-04fce5228272")  # buildings and land parcels
PL_URL = os.getenv("PL_URL", "https://services.gugik.gov.pl/uug/")
CZ_URL = os.getenv("CZ_URL", "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/Vyhledavaci_sluzba_nad_daty_RUIAN/"
                             "MapServer/exts/GeocodeSOE/findAddressCandidates")
SI_URL = os.getenv("SI_URL", "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/ogc/features/collections/SI.GURS.KN:NASLOVI_HS/items")
GSI_URL = os.getenv("GSI_URL", "https://msearch.gsi.go.jp/address-search/AddressSearch")
POSTCODES_IO_URL = os.getenv("POSTCODES_IO_URL", "https://api.postcodes.io")
JAPANPOST_URL = os.getenv("JAPANPOST_URL", "https://www.post.japanpost.jp/service/search/zipcode/download")
# Photon (OpenStreetMap search by komoot). The public instance asks only for fair use; point this at a self-hosted
# Photon for heavy batch work. Public Nominatim is not an option: its policy forbids verification services.
PHOTON_URL = os.getenv("PHOTON_URL", "https://photon.komoot.io").rstrip("/")
# Concurrent requests per foreign service (a 100-row batch for one country never sends more than this at once).
INTL_CONCURRENCY = _env_int("INTL_CONCURRENCY", 4)
INTL_SERVICE_CONCURRENCY = {"photon": _env_int("PHOTON_CONCURRENCY", 2)}
# Downloaded reference data (Japan Post postcode tables, refreshed monthly).
DATA_DIR = os.getenv("NAD_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

# --- Behaviour ---------------------------------------------------------------------

HTTP_TIMEOUT = _env_float("NAD_HTTP_TIMEOUT", 25.0)
# Search radii (meters) around the geocoded point; the second is only used when the first finds nothing.
SEARCH_RADII_M = tuple(int(x) for x in os.getenv("NAD_SEARCH_RADII_M", "300,1500").split(","))
STREET_MATCH_THRESHOLD = _env_float("NAD_STREET_MATCH_THRESHOLD", 0.8)
MAX_NEAR_RADIUS_M = _env_int("NAD_MAX_NEAR_RADIUS_M", 1500)
MAX_BATCH_SIZE = _env_int("NAD_MAX_BATCH_SIZE", 100)
BATCH_CONCURRENCY = _env_int("NAD_BATCH_CONCURRENCY", 5)

CACHE_TTL_S = _env_int("NAD_CACHE_TTL_S", 24 * 3600)  # NAD is refreshed quarterly
CACHE_MAX_ENTRIES = _env_int("NAD_CACHE_MAX_ENTRIES", 5000)

# --- Exposure / protection ---------------------------------------------------------

# Comma-separated list; when set, requests must send header "X-API-Key: <key>".
API_KEYS = {k.strip() for k in os.getenv("NAD_API_KEYS", "").split(",") if k.strip()}
# Per-client-IP requests per minute (0 disables). Protects the free upstream services.
RATE_LIMIT_PER_MIN = _env_int("NAD_RATE_LIMIT_PER_MIN", 120)
# Set to 1 only behind a reverse proxy (nginx / Caddy / Cloudflare) that sets X-Forwarded-For;
# otherwise clients could spoof the header to dodge the rate limit.
TRUST_PROXY = os.getenv("NAD_TRUST_PROXY", "0") == "1"
CORS_ORIGINS = [o.strip() for o in os.getenv("NAD_CORS_ORIGINS", "*").split(",") if o.strip()]

USER_AGENT = os.getenv("NAD_USER_AGENT", "nad-address-api/1.0 (+https://www.transportation.gov/gis/national-address-database)")
