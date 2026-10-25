"""Write notebook Markdown from saved results and preserve unchanged code outputs."""
import json
from pathlib import Path

import nbformat as nbf
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def build():
    results = ROOT / "results"
    S = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    study, optimum, parameters = S["instance"], S["milp"], S["run"]["parameters"]
    comparison = pd.read_csv(results / "model_comparison.csv")
    methods = comparison.set_index("method")
    benchmark = methods.loc["Baseline (6 metro hubs)"]
    add, drop = methods.loc["Greedy ADD"], methods.loc["Greedy DROP"]
    annealing = methods.loc["Simulated annealing"]
    rounded = methods.loc["LP relaxation + rounding"]
    snap, repaired = methods.loc["Gradient (snap only)"], methods.loc["Gradient (snap + repair)"]
    modeled_saving = 1 - optimum["network_cost"] / benchmark.network_cost
    audit = pd.read_csv(results / "feasibility_audit.csv")
    enumeration = S["brute_force"]
    iterations = pd.read_csv(results / "iterations.csv")
    v1, v2, v3, v4, v5 = [iterations.iloc[i] for i in range(5)]
    effects = pd.read_csv(results / "tornado.csv").set_index("parameter")
    capacity_values = pd.read_csv(results / "capacity_shadow_prices.csv")
    expansion_candidates = "; ".join(
        capacity_values.loc[capacity_values.capacity_shadow_price > 1e-6, "dc"])
    simulation = pd.read_csv(results / "monte_carlo_summary.csv")
    nominal, hedged = simulation.iloc[0], simulation.iloc[1]
    cleaning = pd.read_csv(results / "cleaning_audit.csv")
    cleaning_counts = cleaning.set_index("check")

    def usd(value, decimals=0):
        return "\\$" + format(value, f",.{decimals}f")

    md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
    def narrative(heading, body, level=3):
        """Store the heading and completed prose together in a real Markdown cell."""
        return [md(f"{'#' * level} {heading}\n\n{body.strip()}")]

    notebook = nbf.v4.new_notebook()
    notebook.cells = [
        md("""# Assignment 3: Optimization Techniques

**Author:** Albert Kabore, PhD student in AI

## Introduction

The placement of distribution centers affects facility operating costs, inbound
transportation, outbound delivery distances, and the ability to serve customer demand.
This assignment examines these decisions together for a hypothetical regional network
supplied through a Chatham County location representing the Savannah area.

This exercise compares county-level warehouse-location and transportation models for
six southeastern states using Census and BTS data. Pallet demand, capacities, most
costs, and service thresholds are scenario assumptions. County centers represent
locations; they are not warehouse addresses. The time screen uses a modeled
county-pair median and does not guarantee actual deliveries.

The objectives are to select distribution-center counties, minimize weekly network
cost, meet demand within capacity and time restrictions, and evaluate inventory
coverage through a stated cycle-stock policy. The analysis compares linear and
integer programming, greedy search, simulated annealing, and gradient-based location
fitting. Validation, sensitivity analysis, and demand simulation establish how the
results depend on the model and its assumptions.

The notebook follows data collection, prior knowledge, model development, validation,
iteration, and reporting. Set `RECOMPUTE = True` below to rerun the full analysis.
Otherwise it displays the completed pipeline outputs. The Markdown findings describe
the saved run. After producing new pipeline results, run
`python notebooks/build_notebook.py` to refresh those Markdown cells."""),
        md("### Notebook setup and saved analysis settings"),
        code("""import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from IPython.display import Image, Markdown, display

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))
from run_pipeline import main
from supply_chain_opt.data import build_dataset
from supply_chain_opt.instance import build_instance
from supply_chain_opt.models import check_feasibility, solve_cflp

RECOMPUTE = False
if RECOMPUTE:
    main()
RES = ROOT / "results"
S = json.loads((RES / "summary.json").read_text(encoding="utf-8"))
study, optimum, parameters = S["instance"], S["milp"], S["run"]["parameters"]
def table(name):
    return pd.read_csv(RES / name)
def show(name, width=900):
    display(Image(filename=str(RES / "figures" / name), width=width))
def usd(value, decimals=0):
    return "\\$" + format(value, f",.{decimals}f")
pd.set_option("display.max_colwidth", 100)
"""),
        md("""## Step 1: Data collection

Population comes from ACS B01003 (2019–2023), and county warehousing activity from
2022 County Business Patterns, NAICS 493. Coordinates are 2020 population mean centers;
land areas are from the 2023 Gazetteer. Demand is population × 0.0015 pallets/week.

The 22 candidate counties were selected for the exercise; CBP does not identify their
warehouse properties. A 3,000-employee threshold and capacity tiers are assumptions.
Missing source rows remain unknown rather than being assigned zero establishments.

Raw files are cached. `build_dataset(force=True)` rebuilds processed tables;
`refresh_sources=True` downloads them again. `verify_sources.py` checks original values."""),
        md("### Data sources and customer demand zones"),
        code("""data = build_dataset(force=True)
inst = build_instance(data["candidates"], data["customers"], data["routes"])
display(table("data_description.csv"))
display(data["customers"][["label", "fips", "population", "lat", "lon"]].head(10))
verification = json.loads((RES / "source_verification.json").read_text(encoding="utf-8"))
display(pd.DataFrame(verification["checks"])[["file", "all_compared_values_match"]])
"""),
        md("""### Data cleaning and validation

The preceding cell rebuilds the processed tables from the cached raw inputs. The
cleaning implementation is in [data.py](../supply_chain_opt/data.py) and
[cleaning.py](../supply_chain_opt/cleaning.py). It performs the following operations:

1. Trim county-code whitespace, retain five-digit FIPS codes and leading zeros, and
   reject missing or duplicate keys before joining records.
2. Parse population, employment, and establishment counts as numeric values. Reject
   missing required values, negative Census sentinels, infinities, and fractional counts.
3. Keep the NAICS 493 all-size/all-legal-form CBP row for each reported county.
   Check disclosure flags and preserve absent county records as unknown values.
4. Strip Gazetteer column-name whitespace, join geography by FIPS with one-to-one
   checks, and validate coordinates and positive land area. Missing geography causes
   an error rather than silently removing a county.
5. Validate BTS reference year, unique county pairs, finite nonnegative times, and
   source quartile order. Long elapsed times are retained; no outlier trimming occurs.
6. Construct the full candidate/customer route grid and validate all required model
   fields before passing data to the optimizers.

**Modeling assumptions are recorded separately:** the 30-minute local time and the
centroid-distance driving-time floor are imposed scenario rules, not corrections to
BTS measurements. Raw p50 and p75 times are retained beside the modeled times.
The audit shows the actual number of rows selected, missing, or adjusted; a zero
count means the check found no affected rows."""),
        code("""from supply_chain_opt.cleaning import cleaning_audit, validate_processed_dataset

validate_processed_dataset(data)
cleaning = cleaning_audit(data)
display(cleaning[["stage", "check", "input_rows", "output_rows", "affected_rows", "type"]])

required = {
    "customers": ["fips", "population", "lat", "lon", "land_sqmi"],
    "candidates": ["fips", "population", "lat", "lon", "land_sqmi", "warehouse_estab", "warehouse_emp", "tier"],
    "routes": ["orig", "dest", "gc_miles", "tt_p50", "tt_p75", "time_source"],
}
quality = pd.DataFrame([
    {"table": name, "rows": len(data[name]),
     "missing_required_values": int(data[name][columns].isna().sum().sum()),
     "duplicate_keys": int(data[name].duplicated(["orig", "dest"] if name == "routes" else ["fips"]).sum())}
    for name, columns in required.items()
])
display(quality)
assert quality.missing_required_values.sum() == 0
assert quality.duplicate_keys.sum() == 0

adjusted = data["routes"].loc[data["routes"].time_adjusted,
    ["orig", "dest", "gc_miles", "tt_p50_raw", "tt_p50", "tt_p75_raw", "tt_p75"]]
display(adjusted.head(6))
print("Validated processed inputs; raw source values remain available for comparison.")
"""),
        *narrative("Data-cleaning observations", f"""
- All **{cleaning_counts.loc['County keys and required numeric fields', 'input_rows']:,}**
  population rows pass the source-key and required-numeric checks. No invalid
  population rows or missing-geography counties were removed.
- The CBP extract has **{cleaning_counts.loc['Keep all-size / all-legal-form NAICS 493 totals', 'input_rows']:,}**
  rows. Selecting the reported county totals retains
  **{cleaning_counts.loc['Keep all-size / all-legal-form NAICS 493 totals', 'output_rows']:,}**
  and excludes **{cleaning_counts.loc['Keep all-size / all-legal-form NAICS 493 totals', 'affected_rows']:,}**
  employment-size rows. These are overlapping breakdowns, not erroneous observations.
- **{cleaning_counts.loc['Counties without a CBP total', 'affected_rows']:,}** of the
  study-region counties have no CBP total in this extract. Their warehousing activity
  remains unknown. The selected candidate counties have reported counts; no missing
  capacity evidence was replaced with an invented zero.
- The route grid contains **{study['routes']:,}** pairs. **{study['assumed_local_pairs']}**
  local times and **{study['bts_rows_adjusted']}** adjusted BTS rows reflect explicit
  modeling assumptions. No nonlocal time regression was needed for this saved run.
- Duplicate keys, invalid source quartiles, and incomplete processed route grids cause
  an error. The audit records successful checks rather than claiming that nonexistent
  duplicates or outliers were removed.
"""),
        md("""### County-pair times

BTS reports elapsed time between the last GPS observation in the origin county and
the first in the destination county, including stopped time. These are not
warehouse-to-customer measurements. The processed routes preserve raw p50/p75 times
and identify assumed local times and adjusted values. The 55-mph centroid-distance
floor is a modeling assumption. The actual counts are shown below."""),
        code("""routes = data["routes"]
display(routes.time_source.value_counts().rename("route_count").to_frame())
print("Source rows adjusted by the time floor:", int(routes.time_adjusted.sum()))
display(routes[["orig", "dest", "gc_miles", "tt_p50_raw", "tt_p50", "tt_p75", "time_source"]].head(8))
"""),
        md("""#### County-pair travel times and estimated distance

BTS-derived median-time proxies are plotted against estimated road distance. The
horizontal line marks the modeled eight-hour threshold."""),
        code('show("fig_travel_time.png", 750)'),
        *narrative("Observations: Step 1", f"""
- The study includes **{study['customers']} demand counties**, **{study['candidates']} candidate
  center counties**, and **{study['routes']:,} county-pair routes**. The population estimates
  sum to **{study['population']:,} residents**; the assumed demand rate converts this to
  **{study['demand_pallets_week']:,.1f} pallets per week**.
- BTS source records cover **{study['bts_observed_share']:.1%}** of the route pairs.
  **{study['bts_rows_adjusted']}** source rows were adjusted by the assumed time floor.
  The **{study['assumed_local_pairs']}** missing local pairs use an assumed 30 minutes;
  **{study['imputed_nonlocal_pairs']}** nonlocal pairs required regression imputation.
- The reported distance–time correlation is **{study['bts_corr_distance_time']:.2f}** for
  the nonlocal BTS-derived time proxies. Distance alone therefore does not describe
  all variation in the modeled time values.
- The source-verification record reports matching values for the compared Census and
  BTS inputs. This supports their provenance; it does not turn assumed pallet demand,
  facility capacities, or county location proxies into observed company measurements.
"""),
        md("""## Step 2: Prior knowledge and operational assumptions

The truck benchmark is $2.270/mile for 2023 from ATRI's 2024 update. It is a historical
operating-cost average, not a freight quote. Other economic inputs are assumptions.

The 35%–90% throughput band also bounds normalized cycle stock under a common
one-week ordering cycle: average cycle stock is half weekly throughput. It does not
optimize multi-period inventory. Approximate safety stock includes assumed county
and shared regional variation; holding cost is excluded from the network objective."""),
        md("### Cost, capacity, and demand assumptions"),
        code("""display(pd.DataFrame({"parameter": S["run"]["parameters"].keys(),
                      "value": [str(v) for v in S["run"]["parameters"].values()]}))
display(table("candidates.csv"))
"""),
        *narrative("Observations: Step 2", f"""
- The assumed loading and empty-mileage factors convert the historical truck-cost
  benchmark into **{usd(study['outbound_rate'], 4)} per outbound pallet-mile** and
  **{usd(study['inbound_rate'], 4)} per inbound pallet-mile**.
- The candidate centers have **{study['capacity_pallets_week']:,.0f} pallets/week** of
  assumed rated capacity, about **{study['capacity_pallets_week'] / study['demand_pallets_week']:.2f}
  times** nominal demand. Aggregate capacity does not establish whether suitable
  centers can serve each county within the time screen.
- Only **{study['allowed_arc_share']:.1%}** of candidate-to-customer arcs pass the
  **{parameters['sla_minutes'] / 60:g}-hour modeled median-time threshold**. Location
  choices must therefore account for the geographic pattern of allowable arcs.
- The **{parameters['u_min']:.0%}–{parameters['u_max']:.0%}** throughput band controls
  utilization. Under the common **{parameters['cycle_stock_weeks']:g}-week** ordering
  cycle, it also bounds normalized cycle stock. Stock conservation and replenishment
  over time are outside this model, and reported holding cost is outside its objective.
"""),
        md(r"""## Step 3: Model development

With binary openings $y_i$ and continuous demand shares $x_{ij}$, minimize
$$\sum_i f_i y_i + \sum_{ij} c_{ij} d_j x_{ij}.$$
The constraints are $\sum_i x_{ij}=1$,
$0.35K_i y_i\leq\sum_j d_jx_{ij}\leq0.90K_i y_i$,
$x_{ij}\leq y_i$, and $x_{ij}=0$ when the modeled median exceeds 480 minutes.
Here $c_{ij}$ includes inbound transport, handling, and outbound transport.
Split sourcing and fractional aggregate pallet flows are allowed.

The MILP provides the reference; the LP relaxation supplies a lower bound.
Rounding opens every positive LP location and reoptimizes flows. Greedy ADD/DROP and
simulated annealing search location sets using LP routing scores. Search penalties
allow infeasible intermediate networks; final feasibility is audited separately.

The gradient method fits continuous points with Adam and capacity-price updates,
then assigns them to distinct counties. Its Lagrangian includes
$-C\sum_k\mu_k$. A local repair search reduces unmet demand and minimum-throughput
deficits by opening or closing centers. The repaired result combines continuous
fitting with discrete local search and has no global optimality guarantee."""),
        md("### LP results and comparison of optimization methods"),
        code("""display(pd.DataFrame([S["lp_relaxation"]]))
comparison = table("model_comparison.csv")
methods = comparison.set_index("method")
benchmark = methods.loc["Baseline (6 metro hubs)"]
add, drop = methods.loc["Greedy ADD"], methods.loc["Greedy DROP"]
annealing = methods.loc["Simulated annealing"]
rounded = methods.loc["LP relaxation + rounding"]
snap = methods.loc["Gradient (snap only)"]
repaired = methods.loc["Gradient (snap + repair)"]
modeled_saving = 1 - optimum["network_cost"] / benchmark.network_cost
display(comparison[["method", "n_dcs", "network_cost", "transport_cost", "unmet_pallets",
                    "balance_shortfall", "feasible_base_model", "gap_vs_milp_pct", "runtime_s"]])
"""),
        md("""#### Selected distribution network

County location proxies and weekly pallet assignments show the selected network.
The connecting lines represent allocations rather than actual road routes."""),
        code('show("fig_network_map.png", 750)'),
        md("""#### Comparison of optimization costs and runtimes

Cost gaps relative to the MILP reference and recorded computation times are shown
for the solutions that pass the base-model feasibility checks."""),
        code('show("fig_model_comparison.png")'),
        md("""#### Continuous location trajectories and gradient objective

The map traces the fitted continuous sites and the snapped, repaired locations.
The objective trace uses changing temperatures and capacity prices."""),
        code('show("fig_gradient.png")'),
        *narrative("Observations: Step 3", f"""
- The MILP selects **{optimum['n_dcs']} centers** at **{usd(optimum['network_cost'])}/week**
  (**{usd(optimum['cost_per_pallet'], 2)}/pallet**). Its recorded solve time is
  **{optimum['runtime_s']:.3f} seconds**, with a reported gap of **{optimum['mip_gap']:.2g}**.
- The LP relaxation gives a lower bound of **{usd(S['lp_relaxation']['objective'])}/week**;
  the MILP exceeds that bound by **{S['lp_relaxation']['integrality_gap_pct']:.3f}%**.
  Opening every positive LP location yields **{rounded.n_dcs:.0f} centers** and a
  **{rounded.gap_vs_milp_pct:.2f}%** cost gap after routing.
- Greedy ADD selects **{add.n_dcs:.0f} centers** at **{add.gap_vs_milp_pct:.2f}%** above
  the MILP cost; greedy DROP selects **{drop.n_dcs:.0f}** at **{drop.gap_vs_milp_pct:.2f}%** above it.
  Simulated annealing finishes **{abs(annealing.gap_vs_milp_pct):.2f}%** from the reference
  in **{annealing.runtime_s:.2f} seconds** for this seeded run.
- Gradient fitting with snapping alone finishes **{snap.gap_vs_milp_pct:.2f}%** above
  the MILP. The result with discrete local repair finishes
  **{abs(repaired.gap_vs_milp_pct):.2f}%** from it. The shared search takes
  **{repaired.runtime_s:.2f} seconds**; the repaired result includes both continuous
  fitting and opening/closing moves, so it does not certify the continuous method.
- The selected design costs **{modeled_saving:.1%}** less than the assumed six-hub
  comparison. That comparison routes only **{benchmark.pct_within_sla_p50:.1%}** of
  volume on arcs that pass the time screen and is not a feasible base-model alternative.
"""),
        md("""## Step 4: Validation

Each network is checked for demand, capacity, minimum throughput, the time screen,
open-center shipments, and nonnegative flows. The assumed six-hub comparison ignores
the time screen and is excluded from feasible base-model rankings.

Reduced enumeration uses a hard-constrained transportation LP for every nonempty
open set. It validates the reduced instance only. Full-instance optimality relies on
the MILP termination status and solver bound. Tests also check marginal costs against
small cost changes, analytic gradients, and repair of throughput deficits."""),
        md("### Feasibility audit and reduced-instance enumeration"),
        code("""audit = table("feasibility_audit.csv")
display(audit)
display(pd.DataFrame([S["brute_force"]]))
enumeration = S["brute_force"]
validated = solve_cflp(inst)
assert all(check_feasibility(inst, validated).values())
assert np.isclose(validated.objective, S["milp"]["network_cost"], rtol=1e-8)
print("Saved optimal cost matches a fresh MILP solve.")
"""),
        *narrative("Observations: Step 4", f"""
- **{int(audit.drop(columns='method').all(axis=1).sum())} of {len(audit)}** optimized
  solutions pass every independent constraint check. Feasibility is assessed from
  the actual flows, separately from whether an algorithm produced a finite search score.
- Reduced enumeration examines **{enumeration['subsets']:,}** nonempty networks for
  **{enumeration['candidates']} candidate counties** and **{enumeration['customers']} demand counties**;
  **{enumeration['feasible_subsets']}** networks are feasible. Its minimum cost is
  **{usd(enumeration['enumeration_cost'], 2)}**, versus
  **{usd(enumeration['milp_cost'], 2)}** for the reduced MILP, a relative difference of
  **{enumeration['rel_diff']:.2g}**.
- Enumeration takes **{enumeration['enumeration_s']:.2f} seconds**, compared with
  **{enumeration['milp_s']:.3f} seconds** for the reduced MILP on this run. The full-instance
  result was also checked against a fresh solve in the preceding cell.
- Agreement on the reduced problem supports implementation correctness. Full-instance
  optimality still rests on the solver status and bound; it does not validate the
  assumed demand, costs, capacities, or delivery-time proxy against company operations.
"""),
        md("""## Step 5: Iteration

The first four models successively add capacity, the time screen, and minimum
throughput. The fifth requires a single center per county. Cost differences describe
this instance. Site counts need not change monotonically as constraints are added."""),
        md("### Results as operational constraints are added"),
        code("""iterations = table("iterations.csv")
display(iterations[["version", "n_dcs", "network_cost", "pct_within_sla_p50", "util_cv"]])
v1, v2, v3, v4, v5 = [iterations.iloc[i] for i in range(5)]
"""),
        md("""#### Effect of adding operational constraints

The panels compare optimal weekly cost, selected center counts, and the share of
assigned volume on arcs that pass the modeled time screen across model versions."""),
        code('show("fig_iterations.png")'),
        md("""### Inventory policy indicators

Cycle stock and approximate safety stock are calculated from the selected flows
under the stated inventory policy."""),
        code('display(table("inventory_policy.csv"))'),
        *narrative("Observations: Step 5", f"""
- The uncapacitated model opens **{v1.n_dcs:.0f} centers** at **{usd(v1.network_cost)}/week**,
  but only **{v1.pct_within_sla_p50:.1%}** of assigned volume uses arcs that pass the
  eight-hour time screen. Its capacity utilization is unconstrained.
- Adding capacity increases the selected count to **{v2.n_dcs:.0f}** and cost to
  **{usd(v2.network_cost)}/week**. The share on time-eligible arcs is
  **{v2.pct_within_sla_p50:.1%}**; capacity constraints alone do not enforce the time screen.
- Adding the time screen selects **{v3.n_dcs:.0f} centers** and raises cost by
  **{v3.network_cost / v2.network_cost - 1:.2%}**. The flow-weighted average of arc median
  proxies falls from **{v2.avg_time_p50_min / 60:.2f}** to **{v3.avg_time_p50_min / 60:.2f} hours**.
- The minimum-throughput rule changes the design to **{v4.n_dcs:.0f} centers** at
  **{v4.network_cost / v3.network_cost - 1:.2%}** additional cost. Utilization CV falls
  from **{v3.util_cv:.3f}** to **{v4.util_cv:.3f}**, although inventory dispersion is not
  directly optimized.
- Requiring one center per customer county selects **{v5.n_dcs:.0f} centers** and adds
  **{v5.network_cost / v4.network_cost - 1:.2%}** to base-model cost. This quantifies
  the scenario-specific price of simplifying sourcing assignments.
"""),
        md("""## Step 6: Sensitivity, simulation, and reporting

Sweeps change one parameter at a time. Capacity increases also raise minimum
throughput. Circuity changes cost distances while the main time matrix stays fixed.

LP marginal values hold the open centers fixed. Relaxing usable capacity differs
from increasing rated capacity, which changes both throughput bounds. Demand values
come from a pallet-flow LP with demand as a right-hand side. They are local LP
sensitivities, not dual prices of discrete warehouse decisions.

The paired simulation uses mean-one lognormal county and regional shocks, reallocates
flows, and prices unmet demand at $500/pallet. These are synthetic scenarios. A zero
shortage count does not prove zero shortage risk."""),
        md("### Sensitivity, capacity, and demand-simulation tables"),
        code("""tornado = table("tornado.csv")
capacity_values = table("capacity_shadow_prices.csv")
expansion_candidates = "; ".join(capacity_values.loc[capacity_values.capacity_shadow_price > 1e-6, "dc"])
simulation = table("monte_carlo_summary.csv")
effects = tornado.set_index("parameter")
nominal, hedged = simulation.iloc[0], simulation.iloc[1]
display(tornado)
display(capacity_values)
display(simulation)
"""),
        md("""#### Sensitivity of network cost to model inputs

Each panel reoptimizes the network while changing one input. Point labels give
the number of open centers."""),
        code('show("fig_sensitivity.png")'),
        md("""#### Demand marginal costs and capacity shadow prices

The map shows local demand marginal costs, and the bars show local rated-capacity
savings from the LP with the open-center set held fixed."""),
        code('show("fig_shadow_prices.png")'),
        md("""#### Demand-simulation costs and fill rates

The distributions compare the nominal design with the design for 15% higher demand
under the assumed demand shocks. Fill rates measure quantities served on allowed arcs."""),
        code('show("fig_monte_carlo.png")'),
        *narrative("Observations: Step 6", f"""
- A 20% demand reduction changes reoptimized cost by
  **{effects.loc['Demand volume', 'low']:+.2%}**; a 20% increase changes it by
  **{effects.loc['Demand volume', 'high']:+.2%}**. Corresponding truck-cost effects are
  **{effects.loc['Truck cost per mile', 'low']:+.2%}** and
  **{effects.loc['Truck cost per mile', 'high']:+.2%}**. Demand and transportation inputs
  therefore have larger tested cost effects than fixed costs or handling.
- A 20% improvement in assumed outbound load factor changes network cost by
  **{effects.loc['Outbound load factor', 'high']:+.2%}** after reoptimization. This is a
  scenario result; the model does not construct delivery routes to achieve that loading.
- The rated-capacity marginal savings are positive at
  **{'; '.join(f'{r.dc}: {usd(r.capacity_shadow_price, 2)}' for r in capacity_values.itertuples() if r.capacity_shadow_price > 1e-6)}**
  per additional pallet/week of rated capacity. These are local values for the fixed
  network and include changes to both throughput bounds.
- Across **{S['run']['simulation_scenarios']}** scenarios, the nominal design has mean
  fill **{nominal.mean_fill_rate:.3%}** and more than one unmet pallet in
  **{nominal.prob_shortage:.1%}** of scenarios. The design for 15% higher demand uses
  **{hedged.n_dcs:.0f} centers**, with mean fill **{hedged.mean_fill_rate:.3%}** and
  **{hedged.prob_shortage:.1%}** sampled shortage frequency, at
  **{hedged.mean_cost / nominal.mean_cost - 1:+.2%}** mean penalized cost.
- These fill rates concern quantities served on allowable arcs under assumed demand
  shocks. They are neither measured delivery punctuality nor a simulation of the
  inventory policy; zero sampled shortages would not establish zero future risk.
"""),
        *narrative("Final insights and recommendations", f"""
### Final insights

- **Network choice:** The selected **{optimum['n_dcs']}-center** design consists of
  **{optimum['dcs']}**. It costs **{usd(optimum['network_cost'])}/week**, including
  **{usd(optimum['transport_cost'])}** in transportation, under the defined scenario.
- **Modeled improvement:** Cost is **{modeled_saving:.1%}** below the assumed six-hub
  comparison. All selected flows use time-eligible arcs; the comparison's eligible
  share is **{benchmark.pct_within_sla_p50:.1%}**. The difference is not documented
  savings or an on-time delivery improvement for an operating company.
- **Method choice:** The MILP provides the certified reference for this candidate list.
  Simulated annealing and gradient fitting with local repair finish
  **{abs(annealing.gap_vs_milp_pct):.2f}%** and **{abs(repaired.gap_vs_milp_pct):.2f}%** from
  it in these runs. Greedy ADD and DROP finish within **{max(add.gap_vs_milp_pct, drop.gap_vs_milp_pct):.2f}%**.
- **Operating trade-offs:** Inbound transportation represents
  **{optimum['inbound'] / optimum['network_cost']:.1%}** of network cost. The time screen
  changes the design materially, while a minimum-throughput policy reduces
  utilization dispersion in this instance. Neither policy directly optimizes
  actual delivery reliability or multi-period inventory.

### Recommendations

1. **Investigate the selected counties as a shortlist.** Obtain actual property
   availability, addresses, lease costs, throughput and storage limits, and port
   drayage costs before choosing warehouse properties.
2. **Evaluate capacity where the marginal savings are positive.** The displayed table
   identifies **{expansion_candidates or 'no centers with positive marginal savings'}**. Reoptimize with realistic
   expansion increments and costs before using local shadow prices for a larger change.
3. **Use the higher-demand design as a stress case.** Its **{hedged.n_dcs:.0f}-center**
   configuration has **{hedged.prob_shortage:.1%}** sampled shortage frequency at
   **{hedged.mean_cost / nominal.mean_cost - 1:+.2%}** mean penalized cost in this simulation.
   It supports further evaluation rather than an automatic opening or closing rule.
4. **Test load consolidation and inbound transport options.** Loading assumptions and
   transportation rates materially affect modeled cost. Check operational feasibility
   and carrier quotes before treating the tested savings as achievable.
5. **Connect validated forecasts to the optimizer.** Replace population-based pallet
   demand with forecasts fitted to customer orders. Evaluate forecasts and resulting
   plans on later periods, and estimate service distributions from dispatch and arrival
   records before defining delivery promises.
6. **Extend inventory planning when required.** Add time-indexed stock conservation,
   replenishment, storage limits, shortages, and holding cost. The present stock
   indicators reflect a stated policy rather than optimized replenishment decisions.

The matching [report](../report/Assignment3_Optimization_Report.md) provides the full
methodology, detailed results, limitations, and APA references.
""", level=2),
        *narrative("Conclusion", f"""
The analysis combines public Census and BTS inputs with stated logistics assumptions
to compare exact and approximate approaches to network design. For this scenario,
the MILP selects **{optimum['n_dcs']} centers** at **{usd(optimum['network_cost'])} per week**,
or **{usd(optimum['cost_per_pallet'], 2)} per pallet**, while satisfying the modeled demand,
capacity, throughput, and median-time constraints. Its cost is **{modeled_saving:.1%}**
below the assumed six-hub comparison.

The LP lower bound, independent flow audits, reduced enumeration, and fresh solve
support the computational result. Heuristic and repaired gradient outcomes show that
alternative search methods can approach or match the reference in this instance,
without establishing general convergence or runtime superiority. Sensitivity analysis
identifies demand and transportation inputs as major tested cost drivers; paired
simulation illustrates the cost and shortage trade-off of a higher-demand design.

The business implication is to evaluate warehouse choices jointly with transport,
capacity, and service requirements. Operational use requires company orders,
property and carrier costs, trip-level delivery data, and a fuller inventory model.
The current result is a reproducible solution to the specified exercise, not a claim
of guaranteed real-world savings or delivery performance.
""", level=2),
    ]
    notebook.metadata = {"authors": [{"name": "Albert Kabore", "affiliation": "PhD student in AI"}],
                         "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                         "language_info": {"name": "python", "version": "3.11"}}
    path = ROOT / "notebooks" / "supply_chain_optimization.ipynb"
    if path.exists():
        existing = nbf.read(path, as_version=4)
        unchanged = {(cell.cell_type, cell.source): cell for cell in existing.cells}
        notebook.cells = [unchanged.get((cell.cell_type, cell.source), cell)
                          for cell in notebook.cells]
        # Keep execution counts, tables, plots, cell IDs, and kernel metadata for
        # unchanged analysis cells. Updating prose does not require rerunning them.
        notebook.metadata = existing.metadata
        notebook.metadata["authors"] = [{"name": "Albert Kabore", "affiliation": "PhD student in AI"}]
    nbf.write(notebook, path)
    return path


if __name__ == "__main__":
    print(build())
