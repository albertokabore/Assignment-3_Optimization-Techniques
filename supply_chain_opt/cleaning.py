"""Input checks and an audit of the actual raw-to-processed data transformations."""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd

from . import config as C


def normalize_fips(values: pd.Series, label: str = "County FIPS") -> pd.Series:
    """Trim numeric county codes and restore leading zeros; reject missing/bad codes."""
    codes = values.astype("string").str.strip()
    if not codes.str.fullmatch(r"[0-9]{1,5}", na=False).all():
        raise ValueError(f"{label}: missing or malformed county codes")
    return codes.str.zfill(5).astype(str)


def require_unique(frame: pd.DataFrame, keys: list[str], label: str) -> None:
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise ValueError(f"{label}: missing or duplicate keys {keys}")


def numeric_values(values: pd.Series, label: str, *, positive: bool = False,
                   integer: bool = False, allow_missing: bool = False) -> pd.Series:
    """Parse numeric data without accepting sentinels, infinities, or negative counts."""
    numeric = pd.to_numeric(values, errors="raise")
    present = numeric.dropna()
    invalid = not np.isfinite(present.to_numpy(dtype=float)).all()
    invalid |= bool((present <= 0).any()) if positive else bool((present < 0).any())
    invalid |= not allow_missing and bool(numeric.isna().any())
    invalid |= integer and bool((present % 1 != 0).any())
    if invalid:
        raise ValueError(f"{label}: missing, nonfinite, negative, or otherwise invalid values")
    return numeric


def validate_processed_dataset(data: dict[str, pd.DataFrame]) -> None:
    """Reject incomplete or corrupted cached inputs before they reach an optimizer."""
    candidates, customers, routes = (data[k] for k in ("candidates", "customers", "routes"))
    for label, frame in (("Candidates", candidates), ("Customers", customers)):
        require_unique(frame, ["fips"], label)
        normalized = normalize_fips(frame.fips, label)
        if not frame.fips.astype(str).eq(normalized).all():
            raise ValueError(f"{label}: county codes must retain five digits")
        numeric_values(frame.population, f"{label} population", integer=True)
        numeric_values(frame.land_sqmi, f"{label} land area", positive=True)
        if not (np.isfinite(frame[["lat", "lon"]].to_numpy(dtype=float)).all()
                and frame.lat.between(-90, 90).all() and frame.lon.between(-180, 180).all()):
            raise ValueError(f"{label}: missing or out-of-range coordinates")
    numeric_values(candidates.warehouse_estab, "Candidate establishments", positive=True, integer=True)
    numeric_values(candidates.warehouse_emp, "Candidate employment", integer=True)
    if not candidates.tier.isin(["large", "small"]).all():
        raise ValueError("Candidates: unknown capacity tier")
    require_unique(routes, ["orig", "dest"], "Routes")
    expected = pd.MultiIndex.from_product([candidates.fips, customers.fips])
    actual = pd.MultiIndex.from_frame(routes[["orig", "dest"]])
    if len(actual) != len(expected) or len(expected.difference(actual)) or len(actual.difference(expected)):
        raise ValueError("Routes: missing or unexpected candidate/customer pairs")
    for column in ("gc_miles", "tt_p50", "tt_p75"):
        numeric_values(routes[column], f"Routes {column}")
    if (routes.tt_p75 < routes.tt_p50).any():
        raise ValueError("Routes: modeled p75 is below p50")
    if not routes.time_source.isin(["bts", "assumed_local", "imputed"]).all():
        raise ValueError("Routes: unknown travel-time source")


def cleaning_audit(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Verify provenance and count transformations, keeping assumptions distinct.

    This function reads the cached sources; it does not download, remove outliers,
    deduplicate, or silently replace source values.
    """
    validate_processed_dataset(data)
    population = pd.read_csv(C.RAW_DIR / f"{C.TABLE_POPULATION}.csv", dtype=str)
    cbp = pd.read_csv(C.RAW_DIR / f"{C.TABLE_BUSINESS_PATTERNS}_naics493.csv", dtype=str)
    cbp_total = cbp[(cbp.NAICS2017 == C.NAICS_WAREHOUSING) & (cbp.EMPSZES == "001") & (cbp.LFO == "001")]
    population["fips"] = normalize_fips(population.GEO_ID.str[-5:])
    require_unique(population, ["fips"], "Population source")
    numeric_values(population.B01003_001E, "Population source", integer=True)
    require_unique(cbp, ["GEO_ID", "NAICS2017", "EMPSZES", "LFO"], "CBP source")
    cbp_total = cbp_total.assign(fips=normalize_fips(cbp_total.GEO_ID.str[-5:]))
    require_unique(cbp_total, ["fips"], "CBP county totals")
    flag_count = int(cbp_total[["EMP_F", "ESTAB_F"]].fillna("").ne("").any(axis=1).sum())
    if flag_count:
        raise ValueError("CBP flagged totals require explicit treatment")
    for column in ("EMP", "ESTAB"):
        numeric_values(cbp_total[column], f"CBP {column}", integer=True)
    centroids = pd.read_csv(C.RAW_DIR / "CenPop2020_Mean_CO.txt", encoding="utf-8-sig",
                            dtype={"STATEFP": str, "COUNTYFP": str})
    centroids["fips"] = normalize_fips(centroids.STATEFP + centroids.COUNTYFP)
    require_unique(centroids, ["fips"], "Population centers")
    with zipfile.ZipFile(C.RAW_DIR / "2023_Gaz_counties_national.zip") as archive:
        filename = next(name for name in archive.namelist() if name.endswith(".txt"))
        gazetteer = pd.read_csv(io.BytesIO(archive.read(filename)), sep="\t", dtype={"GEOID": str})
    gazetteer.columns = gazetteer.columns.str.strip()
    gazetteer["fips"] = normalize_fips(gazetteer.GEOID)
    require_unique(gazetteer, ["fips"], "Gazetteer")
    missing_geography = int((~population.fips.isin(centroids.fips)
                             | ~population.fips.isin(gazetteer.fips)).sum())
    if missing_geography:
        raise ValueError("Source counties lack required geography")
    geography = population[["fips"]].merge(
        centroids[["fips", "LATITUDE", "LONGITUDE"]], on="fips", validate="one_to_one").merge(
        gazetteer[["fips", "ALAND_SQMI"]], on="fips", validate="one_to_one")
    numeric_values(geography.ALAND_SQMI, "Study-county land area", positive=True)
    if not (np.isfinite(geography[["LATITUDE", "LONGITUDE"]].to_numpy(dtype=float)).all()
            and geography.LATITUDE.between(-90, 90).all()
            and geography.LONGITUDE.between(-180, 180).all()):
        raise ValueError("Study counties have missing or invalid coordinates")

    candidates, customers, routes = (data[k] for k in ("candidates", "customers", "routes"))
    # Check selected population and employment values against their source rows.
    for frame in (candidates, customers):
        expected_pop = pd.to_numeric(population.set_index("fips").loc[frame.fips, "B01003_001E"]).to_numpy()
        np.testing.assert_array_equal(frame.population.to_numpy(), expected_pop)
    expected_cbp = cbp_total.set_index("fips").loc[candidates.fips]
    for modeled, original in (("warehouse_emp", "EMP"), ("warehouse_estab", "ESTAB")):
        np.testing.assert_array_equal(candidates[modeled].to_numpy(),
                                      pd.to_numeric(expected_cbp[original]).to_numpy())
    from .data import load_bts_travel_times
    bts = load_bts_travel_times(candidates.fips.tolist(), customers.fips.tolist())
    source = routes[["orig", "dest", "tt_p50_raw", "tt_p75_raw"]].merge(
        bts, on=["orig", "dest"], how="left", validate="one_to_one")
    for quantile in (50, 75):
        np.testing.assert_allclose(source[f"tt_p{quantile}_raw"], source[f"tt_p{quantile}"], equal_nan=True)

    rows = []
    def add(stage, check, before, after, affected, action, kind="validation"):
        rows.append({"stage": stage, "check": check, "input_rows": int(before),
                     "output_rows": int(after), "affected_rows": int(affected),
                     "type": kind, "action": action})
    add("Population", "County keys and required numeric fields", len(population), len(population), 0,
        "Five-digit unique FIPS; finite nonnegative integer population; no invalid rows found.")
    add("CBP", "Keep all-size / all-legal-form NAICS 493 totals", len(cbp), len(cbp_total), len(cbp)-len(cbp_total),
        "Exclude employment-size breakdowns; do not sum overlapping totals.", "selection")
    add("CBP", "Duplicate or flagged county totals", len(cbp_total), len(cbp_total), flag_count,
        "Unique county totals and no EMP_F/ESTAB_F flags; no duplicate records removed.")
    missing_cbp = int((~population.fips.isin(cbp_total.fips)).sum())
    add("County join", "Counties without a CBP total", len(population), len(population), missing_cbp,
        "Keep missing warehousing activity unknown; never replace it with zero.")
    add("Geography", "Missing county joins / invalid coordinates or land area", len(population), len(population), missing_geography,
        "Validate one-to-one joins; stop on missing geography; no counties dropped.")
    add("Customers", "Most populous study counties", len(population), len(customers), len(population)-len(customers),
        "Select demand zones by population rank; exclusions are scope choices, not bad data.", "selection")
    add("Candidates", "Manually specified counties", len(population), len(candidates), len(population)-len(candidates),
        "Select the stated candidate list and require reported establishments and employment.", "selection")
    add("BTS", "FIPS formatting and unique 2024 pairs", len(bts), len(bts), 0,
        "Validate normalized five-digit codes, year and pair uniqueness; retain source quartiles.")
    add("BTS", "Negative, missing, or unordered source quartiles", len(bts), len(bts), 0,
        "Reject invalid source times; retain long nonnegative elapsed times without outlier trimming.")
    add("Routes", "Candidate-by-customer grid", len(bts), len(routes), len(routes)-len(bts),
        "Create every candidate/customer pair and identify absent source pairs.", "join")
    local = routes.orig.eq(routes.dest)
    add("Routes", "Assumed local delivery time", len(routes), len(routes), local.sum(),
        "Assign 30 minutes to same-county modeled times; preserve any original source values.", "modeling assumption")
    add("Routes", "Nonlocal missing-time regression", len(routes), len(routes), routes.time_source.eq("imputed").sum(),
        "Fit OLS only if a nonlocal source time is missing; not used when the count is zero.", "modeling assumption")
    add("Routes", "BTS rows changed by the modeled time floor", len(routes), len(routes), routes.time_adjusted.sum(),
        "Apply the assumed centroid-distance / 55-mph floor; raw p50/p75 remain unchanged.", "modeling assumption")
    add("Processed data", "Missing pairs, duplicate keys, invalid model inputs", len(routes), len(routes), 0,
        "All required processed values pass validation; source population and candidate CBP values match.")
    return pd.DataFrame(rows)
