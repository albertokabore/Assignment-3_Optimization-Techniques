# Results tables

All demand, capacities, facility costs, and routing assumptions describe an exercise scenario.

## Data description

| data | source | variables | units | preprocessing |
|---|---|---|---|---|
| Population / assumed demand | U.S. Census Bureau, data.census.gov table ACSDT5Y2023.B01003 (ACS 5-year 2019-2023, Total Population) | B01003_001E total population | persons; derived demand in pallets/week | Counties of AL, FL, GA, NC, SC, TN; 60 most populous kept as customer zones; demand = population x 1.5 pallets / 1,000 residents / week |
| Warehousing activity / candidate counties | U.S. Census Bureau, data.census.gov table CBP2022.CB2200CBP (County Business Patterns 2022), NAICS 493 Warehousing and Storage | ESTAB, EMP | establishments; employees | All-employment-size/all-legal-form row; missing source rows remain unknown; 22 candidate DC counties, tier = large if EMP >= 3,000 (assumed rule) -> assumed capacity 12,000/7,000 pallets/week |
| County locations | U.S. Census Bureau, 2020 Centers of Population by County | LATITUDE, LONGITUDE | decimal degrees | Population mean centers used as county proxies; not warehouse addresses or port coordinates |
| Land area / density | U.S. Census Bureau, 2023 Gazetteer (counties) | ALAND_SQMI | square miles; persons / sq mi | density = population / land area; assumed rule scales DC fixed cost from -15% to +15% |
| County-pair elapsed times / delivery proxy | Bureau of Transportation Statistics / ATRI county-to-county truck travel times 2024 (data.bts.gov uta5-4eu5) | tt_percentile_25/50/75, pair_moves | minutes | 1,320 county pairs; 1,300 BTS rows; 20 local pairs assumed 30 min; 0 nonlocal pairs imputed; 180 BTS rows adjusted using an assumed 55-mph centroid floor; modeled p50 threshold 480 min; no delivery guarantee |
| Transportation costs | Great-circle distance x circuity; ATRI 2024 operational cost | road miles, $/truck-mile | miles; $ per pallet | estimated road miles = 1.2 x haversine (assumed); historical $2.27/truck-mile operating-cost benchmark, not a freight quote; outbound $0.1247 and inbound $0.1004 per pallet-mile |

## Data cleaning and validation

| stage | check | input_rows | output_rows | affected_rows | type | action |
|---|---|---|---|---|---|---|
| Population | County keys and required numeric fields | 534 | 534 | 0 | validation | Five-digit unique FIPS; finite nonnegative integer population; no invalid rows found. |
| CBP | Keep all-size / all-legal-form NAICS 493 totals | 522 | 193 | 329 | selection | Exclude employment-size breakdowns; do not sum overlapping totals. |
| CBP | Duplicate or flagged county totals | 193 | 193 | 0 | validation | Unique county totals and no EMP_F/ESTAB_F flags; no duplicate records removed. |
| County join | Counties without a CBP total | 534 | 534 | 341 | validation | Keep missing warehousing activity unknown; never replace it with zero. |
| Geography | Missing county joins / invalid coordinates or land area | 534 | 534 | 0 | validation | Validate one-to-one joins; stop on missing geography; no counties dropped. |
| Customers | Most populous study counties | 534 | 60 | 474 | selection | Select demand zones by population rank; exclusions are scope choices, not bad data. |
| Candidates | Manually specified counties | 534 | 22 | 512 | selection | Select the stated candidate list and require reported establishments and employment. |
| BTS | FIPS formatting and unique 2024 pairs | 1300 | 1300 | 0 | validation | Validate normalized five-digit codes, year and pair uniqueness; retain source quartiles. |
| BTS | Negative, missing, or unordered source quartiles | 1300 | 1300 | 0 | validation | Reject invalid source times; retain long nonnegative elapsed times without outlier trimming. |
| Routes | Candidate-by-customer grid | 1300 | 1320 | 20 | join | Create every candidate/customer pair and identify absent source pairs. |
| Routes | Assumed local delivery time | 1320 | 1320 | 20 | modeling assumption | Assign 30 minutes to same-county modeled times; preserve any original source values. |
| Routes | Nonlocal missing-time regression | 1320 | 1320 | 0 | modeling assumption | Fit OLS only if a nonlocal source time is missing; not used when the count is zero. |
| Routes | BTS rows changed by the modeled time floor | 1320 | 1320 | 180 | modeling assumption | Apply the assumed centroid-distance / 55-mph floor; raw p50/p75 remain unchanged. |
| Processed data | Missing pairs, duplicate keys, invalid model inputs | 1320 | 1320 | 0 | validation | All required processed values pass validation; source population and candidate CBP values match. |

## Model comparison

| method | n_dcs | network_cost | gap_vs_milp_pct | cost_per_pallet | unmet_pallets | avg_time_p50_min | pct_within_sla_p50 | util_cv | runtime_s |
|---|---|---|---|---|---|---|---|---|---|
| MILP (exact) | 8 | 2,889,006 | -0.00 | 53.32 | 0 | 153 | 100.0% | 0.297 | 0.228 |
| LP relaxation + rounding | 11 | 2,988,062 | +3.43 | 55.15 | 0 | 132 | 100.0% | 0.320 | 0.043 |
| Greedy ADD | 7 | 2,918,129 | +1.01 | 53.86 | 0 | 193 | 100.0% | 0.295 | 1.627 |
| Greedy DROP | 10 | 2,907,404 | +0.64 | 53.66 | 0 | 113 | 100.0% | 0.300 | 4.703 |
| Simulated annealing | 8 | 2,889,006 | +0.00 | 53.32 | 0 | 153 | 100.0% | 0.297 | 17.441 |
| Gradient (snap only) | 10 | 3,143,046 | +8.79 | 58.01 | 0 | 136 | 100.0% | 0.308 | 53.363 |
| Gradient (snap + repair) | 8 | 2,889,006 | +0.00 | 53.32 | 0 | 153 | 100.0% | 0.297 | 53.363 |
| Baseline (6 metro hubs) | 6 | 3,378,578 | — | 62.35 | 0 | 338 | 82.6% | 0.233 | 0.016 |

## Iterations

| version | n_dcs | network_cost | avg_time_p50_min | pct_within_sla_p50 | util_cv | runtime_s |
|---|---|---|---|---|---|---|
| v1 Uncapacitated | 3 | 2,713,045 | 412 | 73.5% | 0.241 | 0.051 |
| v2 + Capacity | 6 | 2,806,453 | 388 | 71.6% | 0.140 | 1.336 |
| v3 + Delivery-time SLA | 9 | 2,883,343 | 116 | 100.0% | 0.353 | 0.067 |
| v4 + Minimum throughput (base) | 8 | 2,889,006 | 153 | 100.0% | 0.297 | 0.336 |
| v5 + Single sourcing | 9 | 2,895,986 | 117 | 100.0% | 0.320 | 1.358 |

## Sensitivity

| parameter | level | network_cost | n_dcs | jaccard_vs_base | dcs |
|---|---|---|---|---|---|
| Truck cost per mile | 0.70 | 2,231,712 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Truck cost per mile | 0.85 | 2,560,359 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Truck cost per mile | 1.00 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Truck cost per mile | 1.15 | 3,215,719 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| Truck cost per mile | 1.30 | 3,539,325 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| Truck cost per mile | 1.50 | 3,970,799 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| DC fixed cost | 0.50 | 2,687,292 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| DC fixed cost | 0.75 | 2,789,703 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| DC fixed cost | 1.00 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC fixed cost | 1.50 | 3,075,469 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC fixed cost | 2.00 | 3,261,932 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC fixed cost | 3.00 | 3,629,439 | 7 | 0.15 | Montgomery, AL; Duval, FL; Polk, FL; Bibb, GA; Chatham, GA; Mecklenburg, NC; Knox, TN |
| Demand volume | 0.80 | 2,367,502 | 7 | 0.67 | Jefferson, AL; Duval, FL; Leon, FL; Polk, FL; Fulton, GA; Wake, NC; Richland, SC |
| Demand volume | 0.90 | 2,631,590 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Demand volume | 1.00 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Demand volume | 1.10 | 3,143,781 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| Demand volume | 1.20 | 3,405,904 | 9 | 0.70 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Demand volume | 1.30 | 3,662,773 | 9 | 0.89 | Jefferson, AL; Leon, FL; Miami-Dade, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC capacity | 0.80 | 2,913,379 | 9 | 0.89 | Jefferson, AL; Leon, FL; Miami-Dade, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC capacity | 0.90 | 2,897,474 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| DC capacity | 1.00 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC capacity | 1.10 | 2,882,444 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| DC capacity | 1.25 | 2,880,341 | 7 | 0.67 | Jefferson, AL; Duval, FL; Leon, FL; Polk, FL; Fulton, GA; Wake, NC; Richland, SC |
| DC capacity | 1.50 | 2,892,647 | 7 | 0.67 | Jefferson, AL; Duval, FL; Leon, FL; Polk, FL; Fulton, GA; Wake, NC; Richland, SC |
| SLA (hours) | 5.00 | 3,013,251 | 9 | 0.42 | Jefferson, AL; Montgomery, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Mecklenburg, NC; Richland, SC |
| SLA (hours) | 6.00 | 2,919,270 | 7 | 0.50 | Jefferson, AL; Leon, FL; Miami-Dade, FL; Polk, FL; Chatham, GA; Fulton, GA; Mecklenburg, NC |
| SLA (hours) | 7.00 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| SLA (hours) | 8.00 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| SLA (hours) | 10.00 | 2,878,848 | 8 | 0.45 | Jefferson, AL; Duval, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| SLA (hours) | 12.00 | 2,878,848 | 8 | 0.45 | Jefferson, AL; Duval, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| SLA (hours) | 18.00 | 2,852,779 | 8 | 0.33 | Jefferson, AL; Duval, FL; Miami-Dade, FL; Orange, FL; Henry, GA; Wake, NC; Charleston, SC; Richland, SC |
| SLA (hours) | — | 2,806,453 | 6 | 0.27 | Duval, FL; Miami-Dade, FL; Orange, FL; Chatham, GA; Henry, GA; Richland, SC |
| Min utilization | 0.00 | 2,883,343 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| Min utilization | 0.20 | 2,883,343 | 9 | 0.55 | Jefferson, AL; Duval, FL; Leon, FL; Miami-Dade, FL; Polk, FL; Fulton, GA; Wake, NC; Charleston, SC; Richland, SC |
| Min utilization | 0.35 | 2,889,006 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Min utilization | 0.50 | 2,898,553 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |
| Min utilization | 0.60 | 2,922,256 | 8 | 1.00 | Jefferson, AL; Leon, FL; Orange, FL; Polk, FL; Chatham, GA; Fulton, GA; Wake, NC; Richland, SC |

## Tornado (±20 %)

| parameter | low | high | span |
|---|---|---|---|
| DC capacity | +0.84% | -0.19% | 1.03% |
| Handling cost | -2.25% | +2.25% | 4.50% |
| DC fixed cost | -2.73% | +2.58% | 5.31% |
| Outbound load factor | +5.10% | -5.33% | 10.43% |
| Circuity factor | -15.17% | +15.04% | 30.21% |
| Truck cost per mile | -15.17% | +15.04% | 30.21% |
| Demand volume | -18.05% | +17.89% | 35.94% |

## Capacity shadow prices

| dc | open | throughput | capacity | utilization | usable_capacity_shadow_price | capacity_shadow_price |
|---|---|---|---|---|---|---|
| Jefferson, AL | True | 3,120.88 | 7,000.00 | 44.6% | 0.00 | 0.00 |
| Leon, FL | True | 2,450.00 | 7,000.00 | 35.0% | 0.00 | -1.36 |
| Orange, FL | True | 10,800.00 | 12,000.00 | 90.0% | 7.11 | 6.40 |
| Polk, FL | True | 10,800.00 | 12,000.00 | 90.0% | 5.81 | 5.23 |
| Chatham, GA | True | 8,025.24 | 12,000.00 | 66.9% | 0.00 | 0.00 |
| Fulton, GA | True | 10,075.49 | 12,000.00 | 84.0% | 0.00 | 0.00 |
| Wake, NC | True | 3,615.21 | 7,000.00 | 51.6% | 0.00 | 0.00 |
| Richland, SC | True | 5,296.49 | 7,000.00 | 75.7% | 0.00 | 0.00 |

## Monte Carlo

| network | mean_cost | p95_cost | mean_network_cost | mean_fill_rate | p05_fill_rate | prob_shortage | n_dcs | fixed_cost |
|---|---|---|---|---|---|---|---|---|
| MILP optimum (nominal demand) | 2,884,375.35 | 3,323,671.62 | 2,882,576.01 | 99.99% | 100.00% | 0.6% | 8 | 372,925.95 |
| Hedged MILP (+15 % demand) | 2,896,231.31 | 3,292,233.72 | 2,896,231.31 | 100.00% | 100.00% | 0.0% | 9 | 430,759.05 |

## Feasibility audit

| method | demand_met | capacity | balance | sla | only_open_dcs_ship | non_negative |
|---|---|---|---|---|---|---|
| MILP (exact) | True | True | True | True | True | True |
| LP relaxation + rounding | True | True | True | True | True | True |
| Greedy ADD | True | True | True | True | True | True |
| Greedy DROP | True | True | True | True | True | True |
| Simulated annealing | True | True | True | True | True | True |
| Gradient (snap only) | True | True | True | True | True | True |
| Gradient (snap + repair) | True | True | True | True | True | True |