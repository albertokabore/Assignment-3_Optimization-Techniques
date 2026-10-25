"""Exact models: capacitated facility location MILP, its LP relaxation, and the
transportation LP used to evaluate any fixed set of open DCs (solver: HiGHS via SciPy).

Notation (weekly, pallets):
    y_i   in {0,1}  open DC i                      f_i  fixed cost
    x_ij  in [0,1]  share of customer j's demand   c_ij port->i->j $/pallet
                    served from DC i               d_j  demand,  K_i capacity
    min   sum_i f_i y_i + sum_ij c_ij d_j x_ij
    s.t.  sum_i x_ij = 1                              (demand)
          sum_j d_j x_ij <= u_max K_i y_i             (capacity)
          sum_j d_j x_ij >= u_min K_i y_i             (minimum throughput)
          x_ij <= y_i                                 (linking, strong formulation)
          x_ij = 0 if t_ij > SLA                      (delivery time)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from .instance import Instance


@dataclass
class Solution:
    method: str
    open: np.ndarray  # (n,) bool
    flows: np.ndarray  # (n, m) pallets / week
    objective: float  # fixed + transport + handling (+ penalties if any)
    runtime_s: float
    status: str = "optimal"
    unmet: np.ndarray | None = None  # (m,) pallets
    balance_shortfall: np.ndarray | None = None  # (n,) pallets below u_min
    lower_bound: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def n_open(self) -> int:
        return int(self.open.sum())


# --- MILP --------------------------------------------------------------------
def solve_cflp(inst: Instance, *, sla: bool = True, balance: bool = True,
               capacitated: bool = True, single_source: bool = False, relax: bool = False,
               n_open: int | None = None, fixed_open: np.ndarray | None = None,
               time_limit: float = 120.0, method: str | None = None) -> Solution:
    """Solve the capacitated facility location problem (or its LP relaxation)."""
    n, m, p = inst.n, inst.m, inst.params
    d, K = inst.demand, inst.capacity
    nx = n * m
    c = np.concatenate([inst.fixed, (inst.unit_cost * d[None, :]).ravel()])

    rows = []
    # demand: sum_i x_ij = 1
    A_dem = sparse.hstack([sparse.csr_matrix((m, n)),
                           sparse.kron(np.ones((1, n)), sparse.eye(m))])
    rows.append(LinearConstraint(A_dem, 1, 1))
    # capacity / balance: sum_j d_j x_ij - u K_i y_i
    A_load = sparse.kron(sparse.eye(n), d[None, :])
    if capacitated:
        rows.append(LinearConstraint(
            sparse.hstack([-sparse.diags(p.u_max * K), A_load]), -np.inf, 0))
    if balance:
        rows.append(LinearConstraint(
            sparse.hstack([-sparse.diags(p.u_min * K), A_load]), 0, np.inf))
    # linking: x_ij - y_i <= 0
    A_link = sparse.hstack([-sparse.kron(sparse.eye(n), np.ones((m, 1))), sparse.eye(nx)])
    rows.append(LinearConstraint(A_link, -np.inf, 0))
    if n_open is not None:
        rows.append(LinearConstraint(np.r_[np.ones(n), np.zeros(nx)][None, :], n_open, n_open))

    lb, ub = np.zeros(n + nx), np.ones(n + nx)
    if sla:
        ub[n:] = (inst.allowed if sla else np.ones((n, m))).ravel().astype(float)
    if fixed_open is not None:
        lb[:n] = ub[:n] = np.asarray(fixed_open, dtype=float)

    integrality = np.zeros(n + nx)
    if not relax:
        integrality[:n] = 1
        if single_source:
            integrality[n:] = 1

    t0 = time.perf_counter()
    res = milp(c, constraints=rows, bounds=Bounds(lb, ub), integrality=integrality,
               options={"time_limit": time_limit, "mip_rel_gap": 1e-6, "disp": False})
    rt = time.perf_counter() - t0
    name = method or ("LP relaxation" if relax else "MILP (exact)")
    if res.x is None:
        return Solution(name, np.zeros(n, bool), np.zeros((n, m)), np.inf, rt,
                        status=res.message)
    y = res.x[:n]
    x = res.x[n:].reshape(n, m)
    bound = getattr(res, "mip_dual_bound", None)
    if relax and res.status == 0:
        bound = float(res.fun)
    sol = Solution(name, y > 0.5 if not relax else y > 1e-6, x * d[None, :], float(res.fun),
                   rt, status="optimal" if res.status == 0 else res.message,
                   lower_bound=None if bound is None else float(bound))
    sol.extra["y"] = y
    sol.extra["mip_gap"] = getattr(res, "mip_gap", 0.0)
    return sol


# --- Transportation LP for a fixed network -------------------------------------
def evaluate(inst: Instance, open_mask, *, demand: np.ndarray | None = None,
             sla: bool = True, balance: bool = True, penalized: bool = True,
             method: str = "evaluation") -> Solution:
    """Optimal flows for a given set of open DCs.

    Unmet demand and min-utilization shortfall are allowed at a penalty so that *every*
    open set gets a finite score (needed by heuristics and Monte-Carlo simulation).
    Duals of the capacity and demand constraints are returned in ``extra``.
    """
    p = inst.params
    open_mask = np.asarray(open_mask, dtype=bool)
    d = inst.demand if demand is None else np.asarray(demand, float)
    if open_mask.shape != (inst.n,) or d.shape != (inst.m,):
        raise ValueError("Network and demand shapes must match the instance")
    if not np.isfinite(d).all() or (d < 0).any():
        raise ValueError("Demand must be finite and nonnegative")
    oi = np.flatnonzero(open_mask)
    k, m = len(oi), inst.m
    fixed = float(inst.fixed[oi].sum())
    t0 = time.perf_counter()
    if k == 0:
        unmet = d.copy()
        return Solution(method, open_mask, np.zeros((inst.n, m)),
                        p.unmet_penalty * d.sum() if penalized else np.inf,
                        time.perf_counter() - t0, status="penalized" if penalized else "infeasible",
                        unmet=unmet, balance_shortfall=np.zeros(inst.n))

    allowed = inst.allowed[oi] if sla else np.ones((k, m), bool)
    cost = inst.unit_cost[oi]
    nx = k * m
    # Variables are pallets, so demand is a RHS and its dual is dollars per pallet.
    # A share-based formulation with demand-dependent coefficients cannot use the
    # equality dual alone as a demand marginal cost.
    c = np.concatenate([cost.ravel(), np.full(m, p.unmet_penalty),
                        np.full(k, p.unmet_penalty if balance else 0.0)])
    A_eq = sparse.hstack([sparse.kron(np.ones((1, k)), sparse.eye(m)), sparse.eye(m),
                          sparse.csr_matrix((m, k))]).tocsr()
    A_load = sparse.kron(sparse.eye(k), np.ones((1, m)))
    A_cap = sparse.hstack([A_load, sparse.csr_matrix((k, m)), sparse.csr_matrix((k, k))])
    A_bal = sparse.hstack([-A_load, sparse.csr_matrix((k, m)), -sparse.eye(k)])
    A_ub = sparse.vstack([A_cap, A_bal]).tocsr()
    K = inst.capacity[oi]
    b_ub = np.concatenate([p.u_max * K, -p.u_min * K if balance else np.zeros(k)])
    bounds = ([(0, None if a else 0) for a in allowed.ravel()]
              + [(0, None if penalized else 0)] * (m + k))

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=d, bounds=bounds,
                  method="highs")
    rt = time.perf_counter() - t0
    if res.status == 2 and not penalized:
        return Solution(method, open_mask, np.zeros((inst.n, m)), np.inf, rt,
                        status="infeasible", unmet=d.copy(), balance_shortfall=np.zeros(inst.n))
    if res.status != 0:
        raise RuntimeError(f"Evaluation LP failed: {res.message}")
    x = res.x[:nx].reshape(k, m)
    flows = np.zeros((inst.n, m))
    flows[oi] = x
    shortfall = np.zeros(inst.n)
    shortfall[oi] = res.x[nx + m:]
    sol = Solution(method, open_mask, flows, fixed + float(res.fun), rt,
                   unmet=res.x[nx:nx + m], balance_shortfall=shortfall)
    sol.status = "feasible" if sol.unmet.sum() < 1e-6 and shortfall.sum() < 1e-6 else "penalized"
    cap_dual = np.zeros(inst.n)
    cap_dual[oi] = res.ineqlin.marginals[:k]  # $ per extra pallet of capacity (<= 0)
    sol.extra["capacity_dual"] = cap_dual
    balance_dual = np.zeros(inst.n)
    balance_dual[oi] = res.ineqlin.marginals[k:]
    sol.extra["balance_dual"] = balance_dual
    sol.extra["demand_dual"] = res.eqlin.marginals  # $ per extra pallet required
    return sol


# --- Metrics -------------------------------------------------------------------
def metrics(inst: Instance, sol: Solution) -> dict:
    """Cost breakdown, service level, utilization balance and inventory for a solution."""
    p, F = inst.params, sol.flows
    thr = F.sum(axis=1)
    served = F.sum()
    oi = sol.open
    util = np.where(oi, thr / inst.capacity, np.nan)
    u_open = util[oi]
    variance = ((1 + p.common_demand_cv ** 2) * ((p.demand_cv * F) ** 2).sum(axis=1)
                + p.common_demand_cv ** 2 * thr ** 2)
    safety = p.service_z * np.sqrt(p.replenishment_lead_time_weeks * variance)
    cycle = thr * p.cycle_stock_weeks / 2
    unmet = 0.0 if sol.unmet is None else float(sol.unmet.sum())
    shortfall = 0.0 if sol.balance_shortfall is None else float(sol.balance_shortfall.sum())
    fixed = float(inst.fixed[oi].sum())
    inbound = float((inst.inbound_cost[:, None] * F).sum())
    outbound = float((inst.outbound_cost * F).sum())
    handling = float(p.handling_cost * served)
    w = F / max(served, 1e-9)
    return {
        "method": sol.method,
        "n_dcs": sol.n_open,
        "dcs": "; ".join(inst.labels(oi)),
        "fixed": fixed,
        "inbound": inbound,
        "outbound": outbound,
        "handling": handling,
        "network_cost": fixed + inbound + outbound + handling,
        "transport_cost": inbound + outbound,
        "status": sol.status,
        "cost_per_pallet": (fixed + inbound + outbound + handling) / max(served, 1e-9),
        "unmet_pallets": unmet,
        "balance_shortfall": shortfall,
        "avg_miles": float((w * inst.road_miles).sum()),
        "avg_time_p50_min": float((w * inst.time_p50).sum()),
        "pct_within_sla_p50": float(np.clip((w * (inst.time_p50 <= (p.sla_minutes if p.sla_minutes is not None else np.inf))).sum(), 0, 1)),
        "pct_within_sla_p75": float(np.clip((w * (inst.time_p75 <= (p.sla_minutes if p.sla_minutes is not None else np.inf))).sum(), 0, 1)),
        "util_mean": float(np.mean(u_open)) if u_open.size else np.nan,
        "util_min": float(np.min(u_open)) if u_open.size else np.nan,
        "util_max": float(np.max(u_open)) if u_open.size else np.nan,
        "util_cv": float(np.std(u_open) / np.mean(u_open)) if u_open.size else np.nan,
        "cycle_stock": float(cycle.sum()),
        "safety_stock": float(safety.sum()),
        "inventory_holding": float((cycle.sum() + safety.sum()) * p.holding_cost_per_pallet_week),
        "runtime_s": sol.runtime_s,
    }


def check_feasibility(inst: Instance, sol: Solution, tol: float = 1e-6) -> dict[str, bool]:
    """Independent constraint audit (used in validation and tests)."""
    p, F = inst.params, sol.flows
    thr = F.sum(axis=1)
    K = inst.capacity
    return {
        "demand_met": bool(np.allclose(F.sum(axis=0), inst.demand, rtol=1e-6, atol=1e-4)),
        "capacity": bool(np.all(thr <= p.u_max * K * sol.open + tol * K)),
        "balance": bool(np.all(thr[sol.open] >= p.u_min * K[sol.open] - 1e-3)),
        "sla": bool(np.all(F[~inst.allowed] <= 1e-6)),
        "only_open_dcs_ship": bool(np.all(F[~sol.open] <= 1e-6)),
        "non_negative": bool(np.all(F >= -1e-9)),
    }
