"""Compare cached study inputs with the original Census and BTS services."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import requests

from supply_chain_opt import config as C


def census_check(table, columns, codeset=None):
    suffix = "_naics493" if codeset else ""
    path = C.RAW_DIR / f"{table}{suffix}.csv"
    cached = pd.read_csv(path, dtype=str).fillna("")
    frames = []
    for state in C.STATE_FIPS:
        params = {"id": table, "g": f"040XX00US{state}$0500000"}
        if codeset:
            params["codeset"] = codeset
        response = requests.get(C.URL_CENSUS_TABLES, params=params, timeout=90)
        response.raise_for_status()
        rows = response.json()["response"]["data"]
        frames.append(pd.DataFrame(rows[1:], columns=rows[0]).fillna("").astype(str))
    current = pd.concat(frames, ignore_index=True)
    def records(df):
        return sorted(map(tuple, df[columns].fillna("").astype(str).to_numpy()))
    return {"file": str(path.relative_to(C.ROOT)), "source_url": C.URL_CENSUS_TABLES,
            "table": table, "columns_compared": columns, "cached_rows": len(cached),
            "source_rows": len(current), "all_compared_values_match": records(cached) == records(current)}


def bts_check():
    path = next(C.RAW_DIR.glob("bts_truck_travel_times_2024_*.json"))
    cached = pd.read_json(path, dtype=False)
    candidates = pd.read_csv(C.PROCESSED_DIR / "candidates.csv", dtype={"fips": str})
    customers = pd.read_csv(C.PROCESSED_DIR / "customers.csv", dtype={"fips": str})
    origin = ",".join(f"'{int(v)}'" for v in candidates.fips)
    dest = ",".join(f"'{int(v)}'" for v in customers.fips)
    params = {"$where": f"origin_county_fips in({origin}) AND dest_county_fips in({dest})",
              "$limit": 50000}
    response = requests.get(C.URL_BTS_TRAVEL_TIMES, params=params, timeout=90)
    response.raise_for_status()
    current = pd.DataFrame(response.json())
    cols = ["origin_county_fips", "dest_county_fips", "year", "pair_moves",
            "tt_percentile_25", "tt_percentile_50", "tt_percentile_75"]
    def records(df):
        return sorted(map(tuple, df[cols].astype(str).to_numpy()))
    metadata = requests.get("https://data.bts.gov/api/views/uta5-4eu5.json", timeout=90)
    metadata.raise_for_status()
    info = metadata.json()
    return {"file": str(path.relative_to(C.ROOT)), "source_url": response.url,
            "title": info.get("name"), "description": info.get("description"),
            "columns_compared": cols, "cached_rows": len(cached), "source_rows": len(current),
            "all_compared_values_match": records(cached) == records(current)}


def geography_check(url, filename):
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    path = C.RAW_DIR / filename
    return {"file": str(path.relative_to(C.ROOT)), "source_url": url,
            "all_compared_values_match": response.content == path.read_bytes()}


def main():
    tasks = [
        lambda: census_check(C.TABLE_POPULATION, ["GEO_ID", "NAME", "B01003_001E"]),
        lambda: census_check(C.TABLE_BUSINESS_PATTERNS,
                             ["GEO_ID", "NAICS2017", "EMPSZES", "LFO", "ESTAB", "EMP", "EMP_F"],
                             "naics~493"),
        bts_check,
        lambda: geography_check(C.URL_CENTROIDS, "CenPop2020_Mean_CO.txt"),
        lambda: geography_check(C.URL_GAZETTEER, "2023_Gaz_counties_national.zip"),
    ]
    checks = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for future in [pool.submit(task) for task in tasks]:
            checks.append(future.result())
    files = [{"file": str(p.relative_to(C.ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(C.RAW_DIR.iterdir()) if p.is_file()]
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "all_checks_pass": all(c["all_compared_values_match"] for c in checks),
              "raw_file_hashes": files}
    path = C.RESULTS_DIR / "source_verification.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "raw_file_hashes"}, indent=2), flush=True)
    if not result["all_checks_pass"]:
        raise SystemExit("Cached data differ from the source. Inspect the verification results.")


if __name__ == "__main__":
    main()
