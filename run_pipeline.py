"""End-to-end pipeline: data -> models -> validation -> sensitivity -> figures/tables.

Usage:  python run_pipeline.py [--refresh-data] [--quick]
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import hashlib
import time
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import scipy

from supply_chain_opt import analysis as A
from supply_chain_opt import config as C
from supply_chain_opt import plots as P
from supply_chain_opt.data import build_dataset, load_state_outlines
from supply_chain_opt.gradient import gradient_location
from supply_chain_opt.heuristics import greedy_add, greedy_drop, simulated_annealing
from supply_chain_opt.instance import build_instance
from supply_chain_opt.models import check_feasibility, evaluate, metrics, solve_cflp

log = logging.getLogger("pipeline")


def md_table(df: pd.DataFrame, floatfmt: dict | None = None) -> str:
    """Minimal Markdown table writer (avoids an optional 'tabulate' dependency)."""
    floatfmt = floatfmt or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if isinstance(v, (float, np.floating)):
                cells.append(format(v, floatfmt.get(c, ",.2f")) if np.isfinite(v) else "—")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def data_description(inst, routes: pd.DataFrame) -> pd.DataFrame:
    """Report table: source, variables, units and preprocessing of every input data set."""
    p, n_obs = inst.params, int(routes.bts_observed.astype(bool).sum())
    rows = [
        ("Population / assumed demand", f"U.S. Census Bureau, data.census.gov table {C.TABLE_POPULATION} "
         "(ACS 5-year 2019-2023, Total Population)", "B01003_001E total population",
         "persons; derived demand in pallets/week",
         f"Counties of {', '.join(C.STATE_FIPS.values())}; {inst.m} most populous kept as "
         f"customer zones; demand = population x {p.pallets_per_1000_pop} pallets / 1,000 "
         "residents / week"),
        ("Warehousing activity / candidate counties", "U.S. Census Bureau, data.census.gov table "
         f"{C.TABLE_BUSINESS_PATTERNS} (County Business Patterns 2022), NAICS "
         f"{C.NAICS_WAREHOUSING} Warehousing and Storage", "ESTAB, EMP",
         "establishments; employees",
         "All-employment-size/all-legal-form row; missing source rows remain unknown; "
         f"{inst.n} candidate DC counties, tier = large if EMP >= "
         f"{C.LARGE_TIER_MIN_WAREHOUSE_EMP:,} (assumed rule) -> assumed capacity "
         f"{p.capacity['large']:,}/{p.capacity['small']:,} pallets/week"),
        ("County locations", "U.S. Census Bureau, 2020 Centers of Population by County",
         "LATITUDE, LONGITUDE", "decimal degrees",
         "Population mean centers used as county proxies; not warehouse addresses or port coordinates"),
        ("Land area / density", "U.S. Census Bureau, 2023 Gazetteer (counties)",
         "ALAND_SQMI", "square miles; persons / sq mi",
         f"density = population / land area; assumed rule scales DC fixed cost from "
         f"-{p.density_premium / 2:.0%} to +{p.density_premium / 2:.0%}"),
        ("County-pair elapsed times / delivery proxy", "Bureau of Transportation Statistics / ATRI "
         "county-to-county truck travel times 2024 (data.bts.gov uta5-4eu5)",
         "tt_percentile_25/50/75, pair_moves", "minutes",
         f"{len(routes):,} county pairs; {n_obs:,} BTS rows; "
         f"{int((routes.time_source == 'assumed_local').sum())} local pairs assumed 30 min; "
         f"{int((routes.time_source == 'imputed').sum())} nonlocal pairs imputed; "
         f"{int(routes.time_adjusted.sum())} BTS rows adjusted using an assumed 55-mph centroid floor; "
         f"modeled p50 threshold {p.sla_minutes:.0f} min; no delivery guarantee"),
        ("Transportation costs", "Great-circle distance x circuity; ATRI 2024 operational cost",
         "road miles, $/truck-mile", "miles; $ per pallet",
         f"estimated road miles = {p.circuity} x haversine (assumed); historical "
         f"${p.truck_cost_per_mile}/truck-mile operating-cost benchmark, not a freight quote; "
         f"outbound ${p.outbound_rate:.4f} and inbound ${p.inbound_rate:.4f} per pallet-mile"),
    ]
    return pd.DataFrame(rows, columns=["data", "source", "variables", "units", "preprocessing"])


def main(refresh: bool = False, quick: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()

    # 1. Data collection -------------------------------------------------------------
    data = build_dataset(force=True, refresh_sources=refresh)
    cleaning = pd.read_csv(C.PROCESSED_DIR / "cleaning_audit.csv")
    cleaning.to_csv(C.RESULTS_DIR / "cleaning_audit.csv", index=False)
    inst = build_instance(data["candidates"], data["customers"], data["routes"])
    outlines = load_state_outlines(refresh=refresh)
    routes = data["routes"]
    obs = routes[routes.bts_observed.astype(bool) & (routes.orig != routes.dest)]
    r_corr = float(np.corrcoef(obs.gc_miles, obs.tt_p50)[0, 1])
    log.info("Instance: %d candidates, %d customers, %.0f pallets/week",
             inst.n, inst.m, inst.demand.sum())

    # 2-3. Models ---------------------------------------------------------------------
    log.info("Solving MILP / LP ...")
    milp = solve_cflp(inst)
    relax = solve_cflp(inst, relax=True)
    if milp.status != "optimal" or relax.status != "optimal":
        raise RuntimeError("The reference MILP and LP must finish successfully before reporting results")
    lp_round = evaluate(inst, relax.extra["y"] > 1e-6, method="LP relaxation + rounding")
    lp_round.runtime_s = relax.runtime_s + lp_round.runtime_s
    log.info("Heuristics ...")
    g_add, g_drop = greedy_add(inst), greedy_drop(inst)
    sa = simulated_annealing(inst, iters=1500 if quick else 4000)
    log.info("Gradient-based ...")
    grad_snap, grad = gradient_location(inst, restarts=2 if quick else 4)
    base = A.baseline(inst)

    sols = [milp, lp_round, g_add, g_drop, sa, grad_snap, grad, base]
    comp = pd.DataFrame([metrics(inst, s) for s in sols])
    comp["objective_incl_penalties"] = [s.objective for s in sols]
    comp["feasible_base_model"] = [all(check_feasibility(inst, s).values()) for s in sols]
    comp["gap_vs_milp_pct"] = np.where(
        comp.feasible_base_model, 100 * (comp.network_cost / milp.objective - 1), np.nan)
    comp["lp_evaluations"] = [s.extra.get("lp_evaluations", np.nan) for s in sols]
    comp.to_csv(C.RESULTS_DIR / "model_comparison.csv", index=False)

    # 4. Validation --------------------------------------------------------------------
    log.info("Validation ...")
    audit = A.audit(inst, sols[:-1])
    audit.to_csv(C.RESULTS_DIR / "feasibility_audit.csv", index=False)
    brute = A.brute_force_check(inst, n_cand=10 if quick else 12)

    # 5. Iteration ---------------------------------------------------------------------
    log.info("Iteration study ...")
    iters = A.iteration_study(inst)
    iters.to_csv(C.RESULTS_DIR / "iterations.csv", index=False)

    # Sensitivity + simulation -------------------------------------------------------------
    log.info("Sensitivity analysis ...")
    sens = A.sensitivity(inst, milp.open)
    sens.to_csv(C.RESULTS_DIR / "sensitivity.csv", index=False)
    tor = A.tornado(inst)
    tor.to_csv(C.RESULTS_DIR / "tornado.csv", index=False)
    cap_sp, dem_sp = A.shadow_prices(inst, milp.open)
    cap_sp.to_csv(C.RESULTS_DIR / "capacity_shadow_prices.csv", index=False)
    dem_sp.to_csv(C.RESULTS_DIR / "demand_marginal_costs.csv", index=False)

    log.info("Monte-Carlo simulation ...")
    hedged = solve_cflp(inst.with_params(pallets_per_1000_pop=inst.params.pallets_per_1000_pop
                                         * 1.15))
    networks = {"MILP optimum (nominal demand)": milp.open,
                "Hedged MILP (+15 % demand)": hedged.open}
    mc = A.monte_carlo(inst, networks, n=150 if quick else 500)
    mc.to_csv(C.RESULTS_DIR / "monte_carlo.csv", index=False)
    mc_sum = mc.groupby("network", sort=False).agg(
        mean_cost=("cost_with_penalty", "mean"), p95_cost=("cost_with_penalty",
                                                           lambda s: s.quantile(0.95)),
        mean_network_cost=("network_cost", "mean"), mean_fill_rate=("service_level", "mean"),
        p05_fill_rate=("service_level", lambda s: s.quantile(0.05)),
        prob_shortage=("unmet_pallets", lambda s: (s > 1).mean())).reset_index()
    mc_sum["n_dcs"] = [int(m.sum()) for m in networks.values()]
    mc_sum["fixed_cost"] = [float(inst.fixed[m].sum()) for m in networks.values()]
    mc_sum.to_csv(C.RESULTS_DIR / "monte_carlo_summary.csv", index=False)

    # 6. Figures -------------------------------------------------------------------------
    log.info("Figures ...")
    P.network_map(inst, milp, outlines, "fig_network_map.png",
                  f"Optimal network (MILP): {milp.n_open} DCs, ${milp.objective / 1e6:.3f}M / week")
    P.network_map(inst, base, outlines, "fig_network_baseline.png",
                  f"Baseline: 6 metro hubs, ${metrics(inst, base)['network_cost'] / 1e6:.3f}M / "
                  f"week, {100 * metrics(inst, base)['pct_within_sla_p50']:.0f}% on allowed arcs")
    feasible = comp[comp.feasible_base_model]
    P.model_comparison(feasible.assign(network_cost=feasible.objective_incl_penalties),
                       milp.objective)
    P.cost_breakdown(comp)
    P.sa_convergence(sa.extra["history"], milp.objective)
    P.gradient_figures(inst, grad, outlines)
    P.gradient_k_curve(grad.extra["runs"])
    P.sensitivity_grid(sens, milp.objective)
    P.tornado_chart(tor)
    P.monte_carlo_chart(mc)
    P.utilization_chart(inst, [milp, sa, grad, base])
    P.iteration_chart(iters)
    P.travel_time_chart(routes, inst.params.sla_minutes, inst.params.circuity)
    P.shadow_price_map(dem_sp, cap_sp, inst, outlines)

    # Data description + summary ------------------------------------------------------------
    pd.DataFrame({
        "customer": inst.customers.label, "fips": inst.customers.fips,
        "population_acs_2019_2023": inst.customers.population,
        "demand_pallets_wk": inst.demand.round(1),
    }).to_csv(C.RESULTS_DIR / "customers_demand.csv", index=False)
    pd.DataFrame({
        "dc": inst.candidates.label, "fips": inst.candidates.fips,
        "warehouse_estab_cbp2022": inst.candidates.warehouse_estab,
        "warehouse_emp_cbp2022": inst.candidates.warehouse_emp, "tier": inst.candidates.tier,
        "capacity_pallets_wk": inst.capacity, "fixed_cost_wk": inst.fixed.round(0),
        "density_per_sqmi": inst.candidates.density.round(0),
        "inbound_miles_from_port": inst.inbound_miles.round(0),
        "open_in_milp": milp.open,
    }).to_csv(C.RESULTS_DIR / "candidates.csv", index=False)
    data_desc = data_description(inst, routes)
    data_desc.to_csv(C.RESULTS_DIR / "data_description.csv", index=False)
    oi, cj = np.nonzero(milp.flows > 1e-6)
    pd.DataFrame({
        "dc_fips": inst.candidates.fips.to_numpy()[oi],
        "customer_fips": inst.customers.fips.to_numpy()[cj],
        "dc": inst.candidates.label.to_numpy()[oi],
        "customer": inst.customers.label.to_numpy()[cj],
        "pallets_week": milp.flows[oi, cj],
        "modeled_unit_cost": inst.unit_cost[oi, cj],
        "modeled_p50_minutes": inst.time_p50[oi, cj],
        "modeled_p75_minutes": inst.time_p75[oi, cj],
    }).to_csv(C.RESULTS_DIR / "optimal_flows.csv", index=False)
    throughput = milp.flows.sum(1)
    p = inst.params
    variance = ((1 + p.common_demand_cv ** 2) * ((p.demand_cv * milp.flows) ** 2).sum(1)
                + p.common_demand_cv ** 2 * throughput ** 2)
    inventory = pd.DataFrame({"dc": inst.candidates.label, "open": milp.open,
                              "throughput": throughput, "utilization": throughput / inst.capacity,
                              "cycle_stock": throughput * p.cycle_stock_weeks / 2,
                              "safety_stock_approx": p.service_z * np.sqrt(p.replenishment_lead_time_weeks * variance)})
    inventory[inventory.open].to_csv(C.RESULTS_DIR / "inventory_policy.csv", index=False)
    verification_path = C.RESULTS_DIR / "source_verification.json"
    verified = json.loads(verification_path.read_text(encoding="utf-8")) if verification_path.exists() else None
    hashes_current = verified is not None and all(
        (C.ROOT / item["file"]).exists()
        and hashlib.sha256((C.ROOT / item["file"]).read_bytes()).hexdigest() == item["sha256"]
        for item in verified["raw_file_hashes"])

    summary = {
        "run": {"completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "quick": quick, "python": platform.python_version(),
                "platform": platform.platform(), "numpy": np.__version__, "pandas": pd.__version__,
                "scipy": scipy.__version__,
                "parameters": asdict(inst.params), "simulation_scenarios": 150 if quick else 500,
                "sa_iterations": 1500 if quick else 4000, "gradient_restarts": 2 if quick else 4,
                "source_values_verified": bool(verified and verified["all_checks_pass"] and hashes_current),
                "source_checked_at_utc": verified["checked_at_utc"] if verified else None},
        "instance": {"candidates": inst.n, "customers": inst.m,
                     "population": int(inst.customers.population.sum()),
                     "demand_pallets_week": float(inst.demand.sum()),
                     "capacity_pallets_week": float(inst.capacity.sum()),
                     "routes": len(routes),
                     "bts_observed_share": float(routes.bts_observed.astype(bool).mean()),
                     "bts_rows_adjusted": int(routes.time_adjusted.sum()),
                     "assumed_local_pairs": int((routes.time_source == "assumed_local").sum()),
                     "imputed_nonlocal_pairs": int((routes.time_source == "imputed").sum()),
                     "bts_corr_distance_time": r_corr,
                     "allowed_arc_share": float(inst.allowed.mean()),
                     "outbound_rate": inst.params.outbound_rate,
                     "inbound_rate": inst.params.inbound_rate},
        "milp": metrics(inst, milp) | {"lower_bound": milp.lower_bound,
                                        "mip_gap": milp.extra["mip_gap"]},
        "lp_relaxation": {"objective": relax.objective, "runtime_s": relax.runtime_s,
                          "fractional_y": int(((relax.extra["y"] > 1e-6)
                                               & (relax.extra["y"] < 1 - 1e-6)).sum()),
                          "integrality_gap_pct": 100 * (milp.objective / relax.objective - 1)},
        "hedged_dcs": "; ".join(inst.labels(hedged.open)),
        "gradient_sla_radius_miles": grad.extra["sla_radius_miles"],
        "gradient_best_K": grad.extra["best_run"]["K"],
        "brute_force": brute,
        "audit_all_pass": bool(audit.drop(columns="method").all().all()),
        "runtime_total_s": time.perf_counter() - t_all,
    }
    (C.RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    cols = ["method", "n_dcs", "network_cost", "gap_vs_milp_pct", "cost_per_pallet",
            "unmet_pallets", "avg_time_p50_min", "pct_within_sla_p50", "util_cv", "runtime_s"]
    md = ["# Results tables\n", "All demand, capacities, facility costs, and routing assumptions describe an exercise scenario.\n",
          "## Data description\n", md_table(data_desc),
          "\n## Data cleaning and validation\n", md_table(cleaning),
          "\n## Model comparison\n",
          md_table(comp[cols], {"n_dcs": "d", "gap_vs_milp_pct": "+.2f", "runtime_s": ".3f",
                                "pct_within_sla_p50": ".1%", "util_cv": ".3f",
                                "avg_time_p50_min": ".0f", "network_cost": ",.0f",
                                "unmet_pallets": ",.0f"}),
          "\n## Iterations\n",
          md_table(iters[["version", "n_dcs", "network_cost", "avg_time_p50_min",
                          "pct_within_sla_p50", "util_cv", "runtime_s"]],
                   {"network_cost": ",.0f", "pct_within_sla_p50": ".1%", "runtime_s": ".3f",
                    "avg_time_p50_min": ".0f", "util_cv": ".3f"}),
          "\n## Sensitivity\n",
          md_table(sens[["parameter", "level", "network_cost", "n_dcs", "jaccard_vs_base",
                         "dcs"]].astype({"level": object}),
                   {"network_cost": ",.0f", "jaccard_vs_base": ".2f"}),
          "\n## Tornado (±20 %)\n", md_table(tor, {"low": "+.2%", "high": "+.2%", "span": ".2%"}),
          "\n## Capacity shadow prices\n", md_table(cap_sp, {"utilization": ".1%"}),
          "\n## Monte Carlo\n", md_table(mc_sum, {"mean_fill_rate": ".2%",
                                                 "p05_fill_rate": ".2%", "prob_shortage": ".1%"}),
          "\n## Feasibility audit\n", md_table(audit)]
    (C.RESULTS_DIR / "tables.md").write_text("\n".join(md), encoding="utf-8")
    log.info("Done in %.1fs. Results in %s", summary["runtime_total_s"], C.RESULTS_DIR)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh-data", action="store_true", help="re-download source data")
    ap.add_argument("--quick", action="store_true", help="fewer iterations (for CI / smoke runs)")
    a = ap.parse_args()
    main(refresh=a.refresh_data, quick=a.quick)
