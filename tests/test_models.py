"""Unit / integration tests on a small synthetic instance (no network access needed)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from dataclasses import replace

from supply_chain_opt import config as C
from supply_chain_opt.analysis import brute_force_check
from supply_chain_opt.data import haversine_miles, impute_travel_times
from supply_chain_opt.gradient import ContinuousLocator, gradient_location, repair
from supply_chain_opt.heuristics import greedy_add, greedy_drop, simulated_annealing
from supply_chain_opt.instance import build_instance
from supply_chain_opt.models import check_feasibility, evaluate, metrics, solve_cflp


@pytest.fixture(scope="module")
def inst():
    rng = np.random.default_rng(0)
    n, m = 8, 25
    cand_fips = [C.SUPPLY_FIPS] + [f"99{k:03d}" for k in range(1, n)]
    candidates = pd.DataFrame({
        "fips": cand_fips, "lat": rng.uniform(30, 36, n), "lon": rng.uniform(-88, -79, n),
        "tier": ["large", "small"] * (n // 2), "density": rng.uniform(100, 3000, n),
    })
    candidates["label"] = [f"DC{k}" for k in range(n)]
    customers = pd.DataFrame({
        "fips": [f"88{k:03d}" for k in range(m)], "lat": rng.uniform(30, 36, m),
        "lon": rng.uniform(-88, -79, m), "population": rng.integers(2e5, 1.2e6, m),
    })
    customers["label"] = [f"C{k}" for k in range(m)]
    routes = candidates[["fips", "lat", "lon"]].merge(customers[["fips", "lat", "lon"]],
                                                     how="cross", suffixes=("_o", "_d"))
    routes = routes.rename(columns={"fips_o": "orig", "fips_d": "dest"})
    routes["gc_miles"] = haversine_miles(routes.lat_o, routes.lon_o, routes.lat_d, routes.lon_d)
    routes["tt_p50"] = 30 + routes.gc_miles * 1.2 / 50 * 60
    routes["tt_p75"] = routes.tt_p50 * 1.5
    params = C.Params(sla_minutes=420.0, u_min=0.2)
    return build_instance(candidates, customers, routes, params)


def test_haversine_known_distance():
    # Atlanta -> Charlotte is ~226 great-circle miles
    d = haversine_miles(33.749, -84.388, 35.227, -80.843)
    assert 220 < d < 232


def test_impute_fills_missing_and_floors():
    r = pd.DataFrame({"orig": ["a"] * 6, "dest": list("abcdef"),
                      "gc_miles": [0, 50, 100, 200, 300, 400],
                      "tt_p50": [1.0, 90, np.nan, 300, 480, 2.0],
                      "tt_p75": [1.0, 120, np.nan, 400, 600, 3.0]})
    out = impute_travel_times(r)
    assert out.tt_p50.notna().all()
    assert out.loc[0, "tt_p50"] == 30.0  # same county
    assert out.loc[5, "tt_p50"] >= 400 * 1.2 / 55 * 60 - 1e-9  # floored at driving time


def test_milp_feasible_and_bounded_by_relaxation(inst):
    sol = solve_cflp(inst)
    relax = solve_cflp(inst, relax=True)
    assert sol.status == "optimal"
    assert all(check_feasibility(inst, sol).values())
    assert relax.objective <= sol.objective + 1e-6


def test_milp_matches_brute_force(inst):
    res = brute_force_check(inst, n_cand=inst.n, seed=0)
    assert res["rel_diff"] < 1e-7
    assert res["same_network"]


def test_evaluate_matches_milp_for_fixed_network(inst):
    sol = solve_cflp(inst)
    ev = evaluate(inst, sol.open)
    assert ev.objective == pytest.approx(sol.objective, rel=1e-7)
    assert ev.unmet.sum() < 1e-6


def test_single_sourcing_costs_at_least_split(inst):
    split = solve_cflp(inst).objective
    single = solve_cflp(inst, single_source=True)
    assert single.objective >= split - 1e-6
    shares = single.flows / inst.demand[None, :]
    assert np.all(np.isclose(shares, 0, atol=1e-6) | np.isclose(shares, 1, atol=1e-6))


def test_constraints_monotonically_raise_cost(inst):
    c1 = solve_cflp(inst, capacitated=False, sla=False, balance=False).objective
    c2 = solve_cflp(inst, sla=False, balance=False).objective
    c3 = solve_cflp(inst, balance=False).objective
    c4 = solve_cflp(inst).objective
    assert c1 <= c2 + 1e-6 <= c3 + 2e-6 <= c4 + 3e-6


def test_evaluate_penalises_empty_network(inst):
    ev = evaluate(inst, np.zeros(inst.n, bool))
    assert ev.objective == pytest.approx(inst.params.unmet_penalty * inst.demand.sum())


def test_capacity_duals_are_nonpositive(inst):
    ev = evaluate(inst, solve_cflp(inst).open)
    assert np.all(ev.extra["capacity_dual"] <= 1e-9)


@pytest.mark.parametrize("heuristic", [greedy_add, greedy_drop])
def test_greedy_heuristics_near_optimal(inst, heuristic):
    opt = solve_cflp(inst).objective
    sol = heuristic(inst)
    assert sol.objective >= opt - 1e-6
    assert sol.objective <= opt * 1.10


def test_simulated_annealing_finds_optimum_small(inst):
    opt = solve_cflp(inst).objective
    sol = simulated_annealing(inst, iters=1500, seed=1)
    assert sol.objective == pytest.approx(opt, rel=1e-6)


def test_gradient_matches_finite_differences(inst):
    loc = ContinuousLocator(inst)
    rng = np.random.default_rng(1)
    Z = loc.C[:3] + rng.normal(0, 20, (3, 2))
    mu = np.array([0.0, 1.0, 2.0])
    _, g, _ = loc.loss_and_grad(Z, mu, tau=5.0)
    h = 1e-4
    num = np.zeros_like(Z)
    for k in range(3):
        for a in range(2):
            Zp, Zm = Z.copy(), Z.copy()
            Zp[k, a] += h; Zm[k, a] -= h
            num[k, a] = (loc.loss_and_grad(Zp, mu, 5.0)[0] - loc.loss_and_grad(Zm, mu, 5.0)[0]) / (2 * h)
    np.testing.assert_allclose(g, num, rtol=1e-4, atol=1e-3)


def test_gradient_location_returns_feasible_network(inst):
    _, rep = gradient_location(inst, k_range=range(3, 6), restarts=1, iters=300)
    assert rep.unmet.sum() < 1e-6
    assert rep.objective >= solve_cflp(inst).objective - 1e-6


def test_metrics_cost_components_add_up(inst):
    sol = solve_cflp(inst)
    m = metrics(inst, sol)
    assert m["network_cost"] == pytest.approx(sol.objective, rel=1e-7)
    assert 0 <= m["pct_within_sla_p50"] <= 1 + 1e-9


def test_demand_duals_match_cost_changes(inst):
    mask = solve_cflp(inst).open
    ev = evaluate(inst, mask, penalized=False)
    for j in range(inst.m):
        demand = inst.demand.copy()
        demand[j] += 0.01
        changed = evaluate(inst, mask, demand=demand, penalized=False)
        assert (changed.objective - ev.objective) / 0.01 == pytest.approx(
            ev.extra["demand_dual"][j], rel=1e-5, abs=1e-4)


def test_rated_capacity_marginal_includes_both_bounds(inst):
    mask = solve_cflp(inst).open
    ev = evaluate(inst, mask, penalized=False)
    for i in np.flatnonzero(mask):
        cap = inst.capacity.copy()
        cap[i] += 0.01
        changed = evaluate(replace(inst, capacity=cap), mask, penalized=False)
        predicted = (-inst.params.u_max * ev.extra["capacity_dual"][i]
                     + inst.params.u_min * ev.extra["balance_dual"][i])
        assert (ev.objective - changed.objective) / 0.01 == pytest.approx(
            predicted, rel=1e-5, abs=1e-4)


def test_repair_handles_minimum_throughput_deficits(inst):
    sub = replace(inst.subset([0, 1], [0]), demand=np.array([10.]),
                  capacity=np.array([20., 20.]), road_miles=np.zeros((2, 1)),
                  inbound_miles=np.zeros(2), time_p50=np.zeros((2, 1)),
                  params=inst.params.with_(u_min=0.35))
    mask = np.ones(2, dtype=bool)
    assert evaluate(sub, mask).balance_shortfall.sum() > 0
    repaired = evaluate(sub, repair(sub, mask))
    assert all(check_feasibility(sub, repaired).values())


def test_local_time_assumption_does_not_require_regression():
    routes = pd.DataFrame({"orig": ["a"], "dest": ["a"], "gc_miles": [0.],
                           "tt_p50": [np.nan], "tt_p75": [np.nan]})
    cleaned = impute_travel_times(routes)
    assert cleaned.time_source.iloc[0] == "assumed_local"
    assert cleaned.tt_p50.iloc[0] == 30
    assert np.isnan(cleaned.tt_p50_raw.iloc[0])


def test_strict_evaluation_rejects_infeasible_network(inst):
    sol = evaluate(inst, np.zeros(inst.n, bool), penalized=False)
    assert sol.status == "infeasible"
    assert np.isinf(sol.objective)
