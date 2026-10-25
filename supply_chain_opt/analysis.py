"""Iteration study, sensitivity analysis, Monte-Carlo simulation and validation."""
from __future__ import annotations

import itertools
import time

import numpy as np
import pandas as pd

from .instance import Instance
from .models import Solution, check_feasibility, evaluate, metrics, solve_cflp

# Assumed comparison network: one DC in each of six metro hubs.
# (Atlanta, Charlotte, Orlando, Nashville, Birmingham, Jacksonville)
BASELINE_FIPS = ["13121", "37119", "12095", "47037", "01073", "12031"]


def baseline(inst: Instance) -> Solution:
    """Illustrative comparison network, routed without the travel-time restriction."""
    mask = inst.candidates.fips.isin(BASELINE_FIPS).to_numpy()
    return evaluate(inst, mask, sla=False, method="Baseline (6 metro hubs)")


# --- Iteration: build the model up one constraint family at a time -------------
ITERATIONS = [
    ("v1 Uncapacitated", dict(capacitated=False, sla=False, balance=False)),
    ("v2 + Capacity", dict(capacitated=True, sla=False, balance=False)),
    ("v3 + Delivery-time SLA", dict(capacitated=True, sla=True, balance=False)),
    ("v4 + Minimum throughput (base)", dict(capacitated=True, sla=True, balance=True)),
    ("v5 + Single sourcing", dict(capacitated=True, sla=True, balance=True, single_source=True)),
]


def iteration_study(inst: Instance) -> pd.DataFrame:
    rows = []
    for name, kw in ITERATIONS:
        sol = solve_cflp(inst, method=name, **kw)
        rows.append(metrics(inst, sol) | {"version": name})
    return pd.DataFrame(rows)


# --- Sensitivity ------------------------------------------------------------------
def _scaled(d: dict, k: float) -> dict:
    return {key: v * k for key, v in d.items()}


def sensitivity(inst: Instance, base_open: np.ndarray) -> pd.DataFrame:
    """Re-optimise the MILP while sweeping one parameter at a time."""
    p = inst.params
    sweeps = {
        "Truck cost per mile": [(k, dict(truck_cost_per_mile=p.truck_cost_per_mile * k))
                                for k in (0.7, 0.85, 1.0, 1.15, 1.3, 1.5)],
        "DC fixed cost": [(k, dict(fixed_cost=_scaled(p.fixed_cost, k)))
                          for k in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)],
        "Demand volume": [(k, dict(pallets_per_1000_pop=p.pallets_per_1000_pop * k))
                          for k in (0.8, 0.9, 1.0, 1.1, 1.2, 1.3)],
        "DC capacity": [(k, dict(capacity=_scaled(p.capacity, k)))
                        for k in (0.8, 0.9, 1.0, 1.1, 1.25, 1.5)],
        "SLA (hours)": [(None if v is None else v / 60, dict(sla_minutes=v))
                        for v in (300, 360, 420, 480, 600, 720, 1080, None)],
        "Min utilization": [(v, dict(u_min=v)) for v in (0.0, 0.2, 0.35, 0.5, 0.6)],
    }
    rows = []
    for param, values in sweeps.items():
        for level, kw in values:
            sub = inst.with_params(**kw)
            sol = solve_cflp(sub, time_limit=60)
            row = {"parameter": param, "level": level, "status": sol.status}
            if np.isfinite(sol.objective):
                m = metrics(sub, sol)
                inter = (sol.open & base_open).sum()
                row |= {k: m[k] for k in ("network_cost", "cost_per_pallet", "n_dcs", "dcs",
                                          "avg_time_p50_min", "util_cv", "runtime_s")}
                row["jaccard_vs_base"] = inter / (sol.open | base_open).sum()
            rows.append(row)
    return pd.DataFrame(rows)


def tornado(inst: Instance, delta: float = 0.2) -> pd.DataFrame:
    """Re-optimised cost change for +/- ``delta`` on each economic parameter."""
    p = inst.params
    base = solve_cflp(inst).objective
    specs = {
        "Truck cost per mile": lambda k: dict(truck_cost_per_mile=p.truck_cost_per_mile * k),
        "Demand volume": lambda k: dict(pallets_per_1000_pop=p.pallets_per_1000_pop * k),
        "DC fixed cost": lambda k: dict(fixed_cost=_scaled(p.fixed_cost, k)),
        "DC capacity": lambda k: dict(capacity=_scaled(p.capacity, k)),
        "Handling cost": lambda k: dict(handling_cost=p.handling_cost * k),
        "Outbound load factor": lambda k: dict(outbound_load_factor=p.outbound_load_factor * k),
        "Circuity factor": lambda k: dict(circuity=p.circuity * k),
    }
    rows = []
    for name, f in specs.items():
        lo = solve_cflp(inst.with_params(**f(1 - delta))).objective
        hi = solve_cflp(inst.with_params(**f(1 + delta))).objective
        rows.append({"parameter": name, "low": (lo - base) / base, "high": (hi - base) / base})
    return pd.DataFrame(rows).assign(span=lambda d: (d.high - d.low).abs()).sort_values("span")


def shadow_prices(inst: Instance, open_mask: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    """LP duals at the optimal network: value of capacity and marginal cost to serve."""
    sol = evaluate(inst, open_mask, penalized=False)
    if not np.isfinite(sol.objective):
        raise ValueError("Shadow prices require a feasible fixed network")
    cap = pd.DataFrame({
        "dc": inst.candidates.label, "open": open_mask,
        "throughput": sol.flows.sum(1), "capacity": inst.capacity,
        "utilization": sol.flows.sum(1) / inst.capacity,
        # Upper-bound relaxation alone, with the minimum-throughput bound fixed.
        "usable_capacity_shadow_price": -sol.extra["capacity_dual"],
        # A change to K changes BOTH throughput bounds. Units: $ saved per +1 K.
        "capacity_shadow_price": (-inst.params.u_max * sol.extra["capacity_dual"]
                                  + inst.params.u_min * sol.extra["balance_dual"]),
    })[open_mask]
    dem = pd.DataFrame({
        "customer": inst.customers.label, "demand": inst.demand,
        "marginal_cost_per_pallet": sol.extra["demand_dual"],
        "lat": inst.customers.lat, "lon": inst.customers.lon,
    })
    return cap.reset_index(drop=True), dem


# --- Monte-Carlo demand simulation ---------------------------------------------------
def monte_carlo(inst: Instance, networks: dict[str, np.ndarray], n: int = 500,
                seed: int = 7) -> pd.DataFrame:
    """Score fixed networks under random demand (county-level + region-wide lognormal shocks)."""
    rng = np.random.default_rng(seed)
    sig = np.sqrt(np.log(1 + inst.params.demand_cv ** 2))
    common_sigma = np.sqrt(np.log(1 + inst.params.common_demand_cv ** 2))
    rows = []
    for s in range(n):
        shock = rng.lognormal(-common_sigma ** 2 / 2, common_sigma)
        d = inst.demand * shock * rng.lognormal(-sig ** 2 / 2, sig, inst.m)
        for name, mask in networks.items():
            sol = evaluate(inst, mask, demand=d)
            m = metrics(inst, sol)
            rows.append({"scenario": s, "network": name, "total_demand": d.sum(),
                         "unmet_pallets": m["unmet_pallets"],
                         "service_level": 1 - m["unmet_pallets"] / d.sum(),
                         "network_cost": m["network_cost"],
                         "cost_with_penalty": sol.objective})
    return pd.DataFrame(rows)


# --- Validation ----------------------------------------------------------------------
def brute_force_check(inst: Instance, n_cand: int = 12, seed: int = 3) -> dict:
    """Enumerate every open set on a reduced instance and compare with the MILP optimum."""
    rng = np.random.default_rng(seed)
    cand = np.sort(rng.choice(inst.n, n_cand, replace=False))
    sub0 = inst.subset(cand)
    cust = np.flatnonzero(sub0.allowed.any(0))  # customers reachable within the SLA
    sub = inst.subset(cand, cust)
    t0 = time.perf_counter()
    best, best_mask, n_feasible = np.inf, None, 0
    for bits in itertools.product([False, True], repeat=n_cand):
        mask = np.array(bits)
        if not mask.any():
            continue
        sol = evaluate(sub, mask, penalized=False)
        if sol.unmet.sum() < 1e-6 and sol.balance_shortfall.sum() < 1e-6:
            n_feasible += 1
            if sol.objective < best:
                best, best_mask = sol.objective, mask
    t_enum = time.perf_counter() - t0
    milp = solve_cflp(sub)
    if best_mask is None:
        raise ValueError("Reduced instance has no feasible open set")
    return {"candidates": n_cand, "customers": len(cust), "subsets": 2 ** n_cand - 1,
            "feasible_subsets": n_feasible, "enumeration_cost": best,
            "milp_cost": milp.objective, "same_network": bool((milp.open == best_mask).all()),
            "rel_diff": abs(milp.objective - best) / best,
            "enumeration_s": t_enum, "milp_s": milp.runtime_s}


def audit(inst: Instance, sols: list[Solution]) -> pd.DataFrame:
    rows = []
    for s in sols:
        rows.append({"method": s.method} | check_feasibility(inst, s))
    return pd.DataFrame(rows)
