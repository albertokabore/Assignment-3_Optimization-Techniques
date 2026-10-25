"""Central configuration: data sources, study region and model parameters.

The main economic and operational assumptions are collected here for documentation
and sensitivity analysis. Algorithm settings and preprocessing defaults live with
their implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = RESULTS_DIR / "figures"

# --- Data sources -----------------------------------------------------------
# data.census.gov tables, fetched through the JSON service behind its table viewer
# (the same rows as "Download table" on https://data.census.gov/table/<id>).
URL_CENSUS_TABLES = "https://data.census.gov/api/access/data/table"
# ACS 5-year 2019-2023, B01003 "Total Population" (county) -> customer demand
TABLE_POPULATION = "ACSDT5Y2023.B01003"
# County Business Patterns 2022, CB2200CBP, NAICS 493 "Warehousing and Storage" -> warehouses
TABLE_BUSINESS_PATTERNS = "CBP2022.CB2200CBP"
NAICS_WAREHOUSING = "493"
# Census geography reference files (coordinates / land area are not published as tables)
URL_CENTROIDS = (
    "https://www2.census.gov/geo/docs/reference/cenpop2020/county/CenPop2020_Mean_CO.txt"
)
URL_GAZETTEER = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
    "2023_Gaz_counties_national.zip"
)
# BTS / ATRI Freight Mobility Initiative: county-to-county truck travel times, 2024
URL_BTS_TRAVEL_TIMES = "https://data.bts.gov/resource/uta5-4eu5.json"

# --- Study region: U.S. Southeast -------------------------------------------
STATE_FIPS = {"01": "AL", "12": "FL", "13": "GA", "37": "NC", "45": "SC", "47": "TN"}
N_CUSTOMER_COUNTIES = 60  # largest counties by ACS 2019-2023 population

# Single inbound supply node: Port of Savannah (Chatham County, GA)
SUPPLY_FIPS = "13051"

# Candidate DC counties (FIPS): metro logistics hubs that each have NAICS 493 warehouses
# in CBP 2022. The capacity tier is derived from CBP warehouse employment, see below.
CANDIDATES: tuple[str, ...] = (
    "13121",  # Fulton, GA (Atlanta)
    "13151",  # Henry, GA (south Atlanta logistics corridor)
    "13051",  # Chatham, GA (Savannah port)
    "13021",  # Bibb, GA (Macon)
    "37119",  # Mecklenburg, NC (Charlotte)
    "37183",  # Wake, NC (Raleigh)
    "37081",  # Guilford, NC (Greensboro)
    "45045",  # Greenville, SC
    "45079",  # Richland, SC (Columbia)
    "45019",  # Charleston, SC
    "47037",  # Davidson, TN (Nashville)
    "47157",  # Shelby, TN (Memphis)
    "47093",  # Knox, TN (Knoxville)
    "47065",  # Hamilton, TN (Chattanooga)
    "01073",  # Jefferson, AL (Birmingham)
    "01101",  # Montgomery, AL
    "01097",  # Mobile, AL
    "12031",  # Duval, FL (Jacksonville)
    "12095",  # Orange, FL (Orlando)
    "12105",  # Polk, FL (Lakeland)
    "12086",  # Miami-Dade, FL
    "12073",  # Leon, FL (Tallahassee)
)
# A county with at least this many CBP warehousing (NAICS 493) employees already supports
# big-box operations -> "large" DC tier; otherwise "small".
LARGE_TIER_MIN_WAREHOUSE_EMP = 3_000


@dataclass(frozen=True)
class Params:
    """Economic and operational parameters (weekly planning horizon, unit = pallet)."""

    # Transportation (ATRI 2024: average marginal truck operating cost, 2023 = $2.270/mile)
    truck_cost_per_mile: float = 2.270
    pallets_per_truck: int = 26
    outbound_load_factor: float = 0.70  # assumed average use of pallet positions
    inbound_deadhead: float = 0.15  # assumed empty miles per loaded mile (not fraction of total miles)
    circuity: float = 1.20  # road miles / great-circle miles (assumed)
    handling_cost: float = 6.0  # $ per pallet cross-docked through a DC (assumed)

    # Demand: pallets per 1,000 residents per week (assumed rate, scaled by ACS population)
    pallets_per_1000_pop: float = 1.5
    demand_cv: float = 0.25  # county-specific weekly shock CV (assumed)
    common_demand_cv: float = 0.08  # region-wide weekly shock CV (assumed)

    # Facilities (assumed values, documented in the report)
    capacity: dict = field(default_factory=lambda: {"large": 12_000, "small": 7_000})
    fixed_cost: dict = field(default_factory=lambda: {"large": 60_000, "small": 38_000})
    density_premium: float = 0.30  # fixed cost spans -15 % .. +15 % from sparse to dense counties

    # Operational constraints
    sla_minutes: float | None = 480.0  # median truck time DC -> customer <= 8 h
    u_max: float = 0.90  # max utilization of an open DC
    u_min: float = 0.35  # min throughput / capacity of an open DC

    # Inventory (reporting metric)
    cycle_stock_weeks: float = 1.0
    service_z: float = 1.645  # 95 % cycle service level
    replenishment_lead_time_weeks: float = 0.5
    holding_cost_per_pallet_week: float = 4.0

    # Penalty for unmet demand in heuristic / simulation evaluations ($ per pallet)
    unmet_penalty: float = 500.0

    @property
    def outbound_rate(self) -> float:
        """$ per pallet-mile on outbound (DC -> customer) movements."""
        return self.truck_cost_per_mile / (self.pallets_per_truck * self.outbound_load_factor)

    @property
    def inbound_rate(self) -> float:
        """$ per pallet-mile on inbound (port -> DC) full-truckload movements."""
        return self.truck_cost_per_mile * (1 + self.inbound_deadhead) / self.pallets_per_truck

    def with_(self, **kwargs) -> "Params":
        return replace(self, **kwargs)


DEFAULT_PARAMS = Params()
SEED = 42
