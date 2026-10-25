"""Checks for errors that would silently corrupt geographic joins or model inputs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from supply_chain_opt.cleaning import normalize_fips, numeric_values, require_unique, validate_processed_dataset
from supply_chain_opt.data import impute_travel_times, load_warehouses


def test_fips_preserves_leading_zeros_and_trims_whitespace():
    assert normalize_fips(pd.Series(["1073", " 12031 ", "00123"])).tolist() == ["01073", "12031", "00123"]


@pytest.mark.parametrize("value", [None, "bad", "123456", "1073.0"])
def test_invalid_fips_is_rejected(value):
    with pytest.raises(ValueError, match="county codes"):
        normalize_fips(pd.Series([value]))


@pytest.mark.parametrize("value", [-555555555, np.inf, np.nan, 1.5])
def test_invalid_population_is_rejected(value):
    with pytest.raises(ValueError):
        numeric_values(pd.Series([value]), "population", integer=True)


def test_duplicate_county_keys_are_rejected():
    with pytest.raises(ValueError, match="duplicate keys"):
        require_unique(pd.DataFrame({"fips": ["01073", "01073"]}), ["fips"], "counties")


def test_flagged_cbp_values_are_not_silently_used(monkeypatch):
    source = pd.DataFrame({"NAICS2017": ["493"], "EMPSZES": ["001"], "LFO": ["001"],
                           "EMP_F": ["D"], "ESTAB_F": [""], "GEO_ID": ["0500000US01073"],
                           "EMP": ["0"], "ESTAB": ["1"]})
    monkeypatch.setattr("supply_chain_opt.data.fetch_census_table", lambda *args, **kwargs: source)
    with pytest.raises(ValueError, match="Flagged CBP"):
        load_warehouses()


@pytest.fixture
def data():
    candidates = pd.DataFrame({"fips": ["13051", "01073"], "population": [100, 200],
                               "land_sqmi": [100., 200.], "lat": [32., 33.], "lon": [-81., -87.],
                               "warehouse_estab": [1, 2], "warehouse_emp": [20, 40],
                               "tier": ["small", "small"]})
    customers = candidates.copy()
    routes = pd.DataFrame({"orig": ["13051", "13051", "01073", "01073"],
                           "dest": ["13051", "01073", "13051", "01073"],
                           "gc_miles": [0., 350., 350., 0.], "tt_p50": [30., 500., 500., 30.],
                           "tt_p75": [30., 600., 600., 30.], "time_source": ["assumed_local", "bts", "bts", "assumed_local"]})
    return {"candidates": candidates, "customers": customers, "routes": routes}


@pytest.mark.parametrize("problem", ["missing_pair", "duplicate_pair", "negative_distance", "quartile_order"])
def test_corrupt_processed_routes_are_rejected(data, problem):
    if problem == "missing_pair":
        data["routes"] = data["routes"].iloc[:-1]
    elif problem == "duplicate_pair":
        data["routes"] = pd.concat([data["routes"], data["routes"].iloc[:1]], ignore_index=True)
    elif problem == "negative_distance":
        data["routes"].loc[0, "gc_miles"] = -1
    else:
        data["routes"].loc[0, "tt_p75"] = 20
    with pytest.raises(ValueError):
        validate_processed_dataset(data)


def test_missing_geography_is_rejected(data):
    data["customers"].loc[0, "lat"] = np.nan
    with pytest.raises(ValueError, match="coordinates"):
        validate_processed_dataset(data)


def test_partial_quartile_imputation_is_labeled():
    routes = pd.DataFrame({"orig": ["a"] * 3, "dest": ["b", "c", "d"],
                           "gc_miles": [50., 100., 150.], "tt_p50": [100., 200., 300.],
                           "tt_p75": [150., 300., np.nan]})
    cleaned = impute_travel_times(routes)
    assert cleaned.time_source.iloc[2] == "imputed"
    assert cleaned.tt_p75_imputed.iloc[2]
    assert not cleaned.tt_p50_imputed.iloc[2]
    assert np.isnan(cleaned.tt_p75_raw.iloc[2])


def test_long_elapsed_time_is_retained():
    routes = pd.DataFrame({"orig": ["a"], "dest": ["b"], "gc_miles": [50.],
                           "tt_p50": [100000.], "tt_p75": [200000.]})
    cleaned = impute_travel_times(routes)
    assert cleaned.tt_p50.iloc[0] == cleaned.tt_p50_raw.iloc[0] == 100000.
    assert not cleaned.time_adjusted.iloc[0]
