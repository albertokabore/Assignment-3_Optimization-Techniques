"""Data collection and preprocessing (U.S. Census Bureau + Bureau of Transportation Statistics).

Census inputs are data.census.gov tables (ACS B01003 population, CBP NAICS 493
warehouses) plus the Census population-centroid and Gazetteer reference files.

Raw downloads are cached in ``data/raw``; cleaned tables are written to
``data/processed`` so the optimization models run offline and reproducibly.
"""
from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config as C
from .cleaning import (cleaning_audit, normalize_fips, numeric_values, require_unique,
                       validate_processed_dataset)

log = logging.getLogger(__name__)
TIMEOUT = 120


def _download(url: str, dest: Path, params: dict | None = None, *, refresh: bool = False) -> Path:
    """Download ``url`` to ``dest`` once; later calls reuse the cached file."""
    if not refresh and dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s", url)
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles; broadcasts over numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 3958.8 * np.arcsin(np.sqrt(a))


# --- Census -----------------------------------------------------------------
def fetch_census_table(table_id: str, codeset: str | None = None, *, refresh: bool = False) -> pd.DataFrame:
    """All counties of the study states from a data.census.gov table (cached as CSV).

    ``table_id`` is the id shown on data.census.gov (e.g. ``ACSDT5Y2023.B01003``);
    ``codeset`` applies a table filter such as ``naics~493``. The service answers one
    state per request, so the study states are fetched in turn and stacked.
    """
    suffix = f"_{codeset.replace('~', '')}" if codeset else ""
    cache = C.RAW_DIR / f"{table_id}{suffix}.csv"
    if not refresh and cache.exists() and cache.stat().st_size > 0:
        return pd.read_csv(cache, dtype=str)
    frames = []
    for st in C.STATE_FIPS:
        params = {"id": table_id, "g": f"040XX00US{st}$0500000"}
        if codeset:
            params["codeset"] = codeset
        log.info("Downloading data.census.gov table %s (state %s)", table_id, st)
        resp = requests.get(C.URL_CENSUS_TABLES, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()["response"]["data"]
        frames.append(pd.DataFrame(rows[1:], columns=rows[0]))
    df = pd.concat(frames, ignore_index=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df.fillna("").astype(str)


def load_population(*, refresh: bool = False) -> pd.DataFrame:
    """ACS 5-year (2019-2023) total population per county, table B01003."""
    acs = fetch_census_table(C.TABLE_POPULATION, refresh=refresh)
    population = pd.DataFrame({"fips": normalize_fips(acs.GEO_ID.str[-5:]),
                               "CTYNAME": acs.NAME.str.split(",").str[0],
                               "population": numeric_values(acs.B01003_001E, "ACS population", integer=True)})
    require_unique(population, ["fips"], "ACS population")
    if population.CTYNAME.isna().any():
        raise ValueError("ACS population: missing county names")
    return population


def load_warehouses(*, refresh: bool = False) -> pd.DataFrame:
    """CBP 2022 NAICS 493 (Warehousing and Storage) establishments and employees per county.

    Only the "All establishments" row (all employment sizes, all legal forms) is kept.
    """
    cbp = fetch_census_table(C.TABLE_BUSINESS_PATTERNS, f"naics~{C.NAICS_WAREHOUSING}", refresh=refresh)
    cbp = cbp[(cbp.NAICS2017 == C.NAICS_WAREHOUSING) & (cbp.EMPSZES == "001")
              & (cbp.LFO == "001")]
    if cbp.EMP_F.fillna("").ne("").any() or cbp.ESTAB_F.fillna("").ne("").any():
        raise ValueError("Flagged CBP employment/establishment values require explicit handling")
    warehouses = pd.DataFrame({"fips": normalize_fips(cbp.GEO_ID.str[-5:]),
                               "warehouse_estab": numeric_values(cbp.ESTAB, "CBP establishments", integer=True),
                               "warehouse_emp": numeric_values(cbp.EMP, "CBP employment", integer=True)})
    require_unique(warehouses, ["fips"], "CBP county totals")
    return warehouses


def load_counties(*, refresh: bool = False) -> pd.DataFrame:
    """County table for the study region: population, warehouses, centroid, land area, density."""
    pop = load_population(refresh=refresh)

    cen = pd.read_csv(_download(C.URL_CENTROIDS, C.RAW_DIR / "CenPop2020_Mean_CO.txt", refresh=refresh),
                      encoding="utf-8-sig", dtype={"STATEFP": str, "COUNTYFP": str})
    cen = cen.assign(fips=normalize_fips(cen.STATEFP + cen.COUNTYFP))[["fips", "LATITUDE", "LONGITUDE"]]
    require_unique(cen, ["fips"], "Population centers")

    zpath = _download(C.URL_GAZETTEER, C.RAW_DIR / "2023_Gaz_counties_national.zip", refresh=refresh)
    with zipfile.ZipFile(zpath) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        gaz = pd.read_csv(io.BytesIO(zf.read(name)), sep="\t", dtype={"GEOID": str})
    gaz.columns = gaz.columns.str.strip()
    gaz = gaz.rename(columns={"GEOID": "fips"})[["fips", "ALAND_SQMI"]]
    gaz["fips"] = normalize_fips(gaz.fips)
    require_unique(gaz, ["fips"], "Gazetteer")

    df = (pop.merge(cen, on="fips", how="left", validate="one_to_one")
          .merge(gaz, on="fips", how="left", validate="one_to_one")
          .merge(load_warehouses(refresh=refresh), on="fips", how="left", validate="one_to_one"))
    # An absent source row is unknown. It is not evidence of zero establishments.
    df = df.rename(columns={"CTYNAME": "county", "LATITUDE": "lat", "LONGITUDE": "lon",
                            "ALAND_SQMI": "land_sqmi"})
    df["state"] = df.fips.str[:2].map(C.STATE_FIPS)
    df["county"] = df.county.str.replace(r" (County|Parish)$", "", regex=True)
    numeric_values(df.land_sqmi, "County land area", positive=True)
    coordinates = df[["lat", "lon"]].to_numpy(dtype=float)
    if not (np.isfinite(coordinates).all() and df.lat.between(-90, 90).all()
            and df.lon.between(-180, 180).all()):
        raise ValueError("Missing or invalid county geography; counties were not silently dropped")
    df["density"] = df.population / df.land_sqmi
    df["label"] = df.county + ", " + df.state
    return df.reset_index(drop=True)


URL_STATE_KML = "https://www2.census.gov/geo/tiger/GENZ2023/kml/cb_2023_us_state_20m.zip"


def load_state_outlines(states=("AL", "FL", "GA", "NC", "SC", "TN", "MS", "KY", "VA"), *, refresh=False):
    """Census cartographic boundary polygons -> {STUSPS: [ (k,2) lon/lat arrays ]} (maps only)."""
    import xml.etree.ElementTree as ET
    zpath = _download(URL_STATE_KML, C.RAW_DIR / "cb_2023_us_state_20m.zip", refresh=refresh)
    with zipfile.ZipFile(zpath) as zf:
        root = ET.fromstring(zf.read(next(n for n in zf.namelist() if n.endswith(".kml"))))
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    out = {}
    for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark"):
        code = next((s.text for s in pm.iter("{http://www.opengis.net/kml/2.2}SimpleData")
                     if s.get("name") == "STUSPS"), None)
        if code not in states:
            continue
        rings = []
        for coords in pm.iterfind(".//k:outerBoundaryIs//k:coordinates", ns):
            pts = [tuple(map(float, p.split(",")[:2])) for p in coords.text.split()]
            rings.append(np.array(pts))
        out[code] = rings
    return out


# --- BTS --------------------------------------------------------------------
def _bts_fips(fips: str) -> str:
    # BTS stores county FIPS as text without the leading zero (e.g. "1073").
    return str(int(fips))


def load_bts_travel_times(origins: list[str], destinations: list[str], *, refresh: bool = False) -> pd.DataFrame:
    """Median / quartile truck travel times (minutes) for origin-destination county pairs."""
    # Key the cache on the O-D sets so a changed customer list triggers a fresh query.
    key = hashlib.sha1(",".join(sorted(origins) + ["|"] + sorted(destinations)).encode())
    cache = C.RAW_DIR / f"bts_truck_travel_times_2024_{key.hexdigest()[:10]}.json"
    if refresh or not cache.exists():
        o = ",".join(f"'{_bts_fips(f)}'" for f in origins)
        d = ",".join(f"'{_bts_fips(f)}'" for f in destinations)
        params = {
            "$where": f"origin_county_fips in({o}) AND dest_county_fips in({d})",
            "$limit": 50_000,
        }
        _download(C.URL_BTS_TRAVEL_TIMES, cache, params=params, refresh=refresh)
    bts = pd.read_json(cache, dtype=False)
    if bts.empty:
        return pd.DataFrame(columns=["orig", "dest", "tt_p25", "tt_p50", "tt_p75", "pair_moves"])
    if not bts.year.astype(str).eq("2024").all():
        raise ValueError("Expected BTS travel times for 2024 only")
    bts = bts.assign(orig=normalize_fips(bts.origin_county_fips, "BTS origin"),
                     dest=normalize_fips(bts.dest_county_fips, "BTS destination"))
    require_unique(bts, ["orig", "dest"], "BTS county pairs")
    for q in (25, 50, 75):
        bts[f"tt_p{q}"] = numeric_values(bts[f"tt_percentile_{q}"], f"BTS p{q}")
    if ((bts.tt_p25 > bts.tt_p50) | (bts.tt_p50 > bts.tt_p75)).any():
        raise ValueError("BTS source quartiles must satisfy p25 <= p50 <= p75")
    return bts[["orig", "dest", "tt_p25", "tt_p50", "tt_p75", "pair_moves"]]


# --- Assembled data set -------------------------------------------------------
def build_dataset(force: bool = False, *, refresh_sources: bool = False) -> dict[str, pd.DataFrame]:
    """Return processed customers, candidates and the DC x customer route table."""
    C.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    paths = {k: C.PROCESSED_DIR / f"{k}.csv" for k in ("customers", "candidates", "routes")}
    if not force and not refresh_sources and all(p.exists() for p in paths.values()):
        data = {k: pd.read_csv(p, dtype={"fips": str, "orig": str, "dest": str})
                for k, p in paths.items()}
        validate_processed_dataset(data)
        return data

    counties = load_counties(refresh=refresh_sources)
    customers = counties.nlargest(C.N_CUSTOMER_COUNTIES, "population").reset_index(drop=True)
    candidates = counties[counties.fips.isin(C.CANDIDATES)].copy()
    candidates["tier"] = np.where(
        candidates.warehouse_emp >= C.LARGE_TIER_MIN_WAREHOUSE_EMP, "large", "small")
    candidates = candidates.sort_values("fips").reset_index(drop=True)
    if len(candidates) != len(C.CANDIDATES):
        raise ValueError("Some candidate FIPS codes were not found in Census data")
    if candidates[["warehouse_estab", "warehouse_emp"]].isna().any().any():
        raise ValueError("Candidate counties require reported CBP employment and establishments")
    if (candidates.warehouse_estab <= 0).any():
        raise ValueError("Candidate DC counties must have CBP warehousing establishments")

    bts = load_bts_travel_times(candidates.fips.tolist(), customers.fips.tolist(), refresh=refresh_sources)
    routes = candidates[["fips", "lat", "lon"]].merge(
        customers[["fips", "lat", "lon"]], how="cross", suffixes=("_o", "_d"))
    routes = routes.rename(columns={"fips_o": "orig", "fips_d": "dest"})
    routes["gc_miles"] = haversine_miles(routes.lat_o, routes.lon_o, routes.lat_d, routes.lon_d)
    routes = routes.merge(bts, on=["orig", "dest"], how="left", validate="one_to_one")
    routes = impute_travel_times(routes)

    data = {"customers": customers, "candidates": candidates, "routes": routes}
    report = cleaning_audit(data)
    report.to_csv(C.PROCESSED_DIR / "cleaning_audit.csv", index=False)
    customers.to_csv(paths["customers"], index=False)
    candidates.to_csv(paths["candidates"], index=False)
    routes.to_csv(paths["routes"], index=False)
    return data


def impute_travel_times(routes: pd.DataFrame, max_speed_mph: float = 55.0) -> pd.DataFrame:
    """Clean BTS times and fill gaps with a distance regression fitted on observed pairs.

    * Same-county pairs get a 30-minute local delivery time.
    * BTS county-boundary elapsed times below the assumed centroid driving time are
      floored at estimated road miles / ``max_speed_mph`` (a modeling assumption).
    * Missing pairs are imputed with OLS ``tt_p50 = a + b * gc_miles`` (and likewise p75).
    """
    r = routes.copy()
    if not np.isfinite(max_speed_mph) or max_speed_mph <= 0:
        raise ValueError("Assumed driving speed must be finite and positive")
    numeric_values(r.gc_miles, "Route distance")
    for col in ("tt_p50", "tt_p75"):
        r[col] = numeric_values(r[col], col, allow_missing=True)
    road = r.gc_miles * C.DEFAULT_PARAMS.circuity
    drive_floor = road / max_speed_mph * 60
    for col in ("tt_p50", "tt_p75"):
        r[f"{col}_raw"] = r[col]
    r["bts_observed"] = r.tt_p50.notna()
    nonlocal_missing = (r.orig != r.dest) & (r.tt_p50.isna() | r.tt_p75.isna())
    fit_mask = r.bts_observed & (r.gc_miles > 40) & (r.tt_p50 >= drive_floor)
    coefs = {}
    for col in ("tt_p50", "tt_p75"):
        missing = r[col].isna() & (r.orig != r.dest)
        r[f"{col}_imputed"] = missing
        if missing.any():
            fit = fit_mask & r[col].notna()
            if fit.sum() < 2 or r.loc[fit, "gc_miles"].nunique() < 2:
                raise ValueError("Not enough observed routes to impute missing travel times")
            b, a = np.polyfit(r.loc[fit, "gc_miles"], r.loc[fit, col], 1)
            coefs[col] = (a, b)
            r.loc[missing, col] = a + b * r.loc[missing, "gc_miles"]
        r[f"{col}_floored"] = (r[col] < drive_floor) & (r.orig != r.dest)
        r[col] = np.maximum(r[col], drive_floor)
    r.loc[r.orig == r.dest, ["tt_p50", "tt_p75"]] = 30.0
    r["tt_p75"] = np.maximum(r.tt_p75, r.tt_p50)
    r["time_source"] = np.where(r.orig == r.dest, "assumed_local",
                                np.where(nonlocal_missing, "imputed", "bts"))
    r["time_adjusted"] = (r.bts_observed & ((r.tt_p50 != r.tt_p50_raw)
                                          | (r.tt_p75 != r.tt_p75_raw)))
    r.attrs["tt_regression"] = coefs
    return r
