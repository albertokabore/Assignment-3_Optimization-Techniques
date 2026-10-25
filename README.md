# Assignment 3: Optimization Techniques

County-level distribution-network planning for Alabama, Florida, Georgia, North
Carolina, South Carolina, and Tennessee. The exercise compares a facility-location
MILP and LP relaxation with greedy search, simulated annealing, and a continuous
gradient method followed by discrete assignment and repair.

The scenario uses real Census population, county warehousing activity, geography,
and BTS truck-time summaries. Pallet demand, facility capacity, facility costs,
load factors, and inventory policy are stated assumptions. County centers represent
locations; the data do not identify available warehouse properties or customer orders.
The eight-hour restriction screens a modeled median-time proxy and does not establish
an actual on-time delivery rate.

| Deliverable | Contents |
|---|---|
| [Word report](report/Assignment3_Optimization_Report.docx) | Complete report with references, figures, and assumptions |
| [Readable report](report/Assignment3_Optimization_Report.md) | Matching Markdown report |
| [Notebook](notebooks/supply_chain_optimization.ipynb) | Six assignment steps and saved results; can rerun the analysis |
| [Results tables](results/tables.md) | Method comparison, sensitivity, simulation, and feasibility checks |
| [Source verification](results/source_verification.json) | Live-source comparisons and raw-file SHA-256 hashes |
| [Cleaning audit](results/cleaning_audit.csv) | Executed validation, selection, joins, and modeling assumptions with row counts |
| [Validation record](results/validation_summary.json) | Artifact consistency checks and recorded test results |
| [Shipment decisions](results/optimal_flows.csv) | Selected counties and weekly pallet flows |
| [Inventory indicators](results/inventory_policy.csv) | Cycle stock and approximate safety stock under the stated policy |
| [Review notes](report/Review_Notes.md) | Substantive corrections and remaining modeling limits |

| Input | Original source | Use |
|---|---|---|
| Population, 2019–2023 | [ACS B01003](https://data.census.gov/table/ACSDT5Y2023.B01003) | County population; assumed demand scales with population |
| Warehousing activity, 2022 | [CBP NAICS 493](https://data.census.gov/table/CBP2022.CB2200CBP?n=493) | Employment and establishments; assumed capacity-tier rule |
| Coordinates, 2020 | [Census population centers](https://www.census.gov/geographies/reference-files/2020/geo/2020-centers-population.html) | County location proxies |
| Land area, 2023 | [Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2023.html) | Density and assumed fixed-cost adjustment |
| Truck times, 2024 | [BTS county-pair data](https://data.bts.gov/d/uta5-4eu5) | County-pair elapsed-time proxy |

```powershell
python -m pip install -r requirements.txt
python -m pytest -q --junitxml=results/test_results.xml
python run_pipeline.py
python report/build_docx.py
python notebooks/build_notebook.py
python -m nbconvert --to notebook --execute --inplace notebooks/supply_chain_optimization.ipynb
python validate_deliverables.py
```

The pipeline rebuilds processed data from cached raw inputs and writes tables and
figures. The complete run includes 4,000 annealing proposals, four gradient restarts
for each K from 5 to 12, enumeration of 4,095 reduced networks, and 500 demand
scenarios. Elapsed time depends on the computer; runtimes are recorded in summary.json.
The 38 tests cover solver bounds, feasibility, demand/capacity marginal costs,
gradient calculations, repair, malformed or duplicate identifiers, source disclosure
flags, missing data, and travel-time preprocessing. The notebook includes executable
cleaning checks and actual before/after row counts; all observations and conclusions
are Markdown cells. After changing pipeline results, rebuild and re-execute the
notebook so its tables, plots, and Markdown describe the same run.

```powershell
python verify_sources.py                  # network: compare cached values with original records
python run_pipeline.py --refresh-data      # network: replace downloads and rebuild results
python run_pipeline.py --quick             # smaller validation/simulation run
python notebooks/build_notebook.py         # rebuild notebook source
```

The Census table-viewer service retrieves the study-state tables; its interface
can change. Cached files support offline analysis. No source values are silently
replaced with invented values. Local travel times and every other scenario assumption
are identified in the report and processed data.
