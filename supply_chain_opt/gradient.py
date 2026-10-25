"""Gradient-based continuous location-allocation (multi-facility Weber problem).

DCs are treated as continuous points z_k in a planar projection (miles). For a fixed
number of DCs K we minimise a smooth surrogate of total weekly cost

    L(z, mu) = sum_j d_j * softmin_tau_k [ g_jk ] - cap * sum_k mu_k
    g_jk     = r_out * dist(c_j, z_k) + r_in * dist(z_k, port)          (transport)
             + lam * s * softplus((dist(c_j, z_k) - R) / s)              (SLA proxy)
             + mu_k                                                       (capacity price)

* softmin with temperature tau gives soft assignments p_jk = softmax(-g_jk / tau);
  tau is annealed so assignments become hard (deterministic-annealing style).
* z is updated with Adam on the analytic gradient dL/dz.
* mu_k is a Lagrange multiplier for DC capacity, updated by projected dual ascent:
  mu_k <- max(0, mu_k + eta * (load_k - cap) / cap)   (primal-dual gradient method).
* R is the first distance bin where fewer than half of modeled arcs pass the time
  screen. It is a coarse empirical distance proxy.

The fitted continuous sites are then "snapped" to distinct candidate counties (Hungarian
assignment) and scored with the exact transportation LP, so the result is directly
comparable with the MILP and heuristics.
"""
from __future__ import annotations

import time

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.special import logsumexp, softmax, expit

from . import config as C
from .instance import Instance
from .models import Solution, evaluate

EPS = 1.0  # miles; smooths the distance at zero


def project(lat, lon, lat0: float = 31.0) -> np.ndarray:
    """Approximate planar coordinates in miles; distortion varies across the region."""
    return np.column_stack([np.asarray(lon) * 69.17 * np.cos(np.radians(lat0)),
                            np.asarray(lat) * 69.05])


def sla_radius(inst: Instance) -> float:
    """Road distance at which ~50 % of arcs satisfy the SLA (from BTS times)."""
    if inst.params.sla_minutes is None:
        return np.inf
    dist, ok = inst.road_miles.ravel(), inst.allowed.ravel()
    edges = np.arange(0, dist.max() + 25, 25.0)
    for lo, hi in zip(edges[:-1], edges[1:]):  # P(ok | dist in 25-mile bin)
        in_bin = (dist >= lo) & (dist < hi)
        if in_bin.sum() >= 5 and ok[in_bin].mean() < 0.5:
            return float(lo)
    return float(dist.max())


class ContinuousLocator:
    def __init__(self, inst: Instance, sla_weight: float = 0.25, sla_scale: float = 15.0):
        p = inst.params
        self.inst = inst
        self.C = project(inst.customers.lat, inst.customers.lon)
        port = inst.candidates.loc[inst.candidates.fips == C.SUPPLY_FIPS].iloc[0]
        self.port = project([port.lat], [port.lon])[0]
        self.cand_xy = project(inst.candidates.lat, inst.candidates.lon)
        self.d = inst.demand
        self.r_out = p.outbound_rate * p.circuity
        self.r_in = p.inbound_rate * p.circuity
        self.R = sla_radius(inst) / p.circuity  # in straight-line miles
        self.lam, self.s = sla_weight, sla_scale
        self.cap = p.u_max * float(np.mean(inst.capacity))

    def _terms(self, Z, mu):
        diff = Z[None, :, :] - self.C[:, None, :]  # (m, K, 2)
        r = np.sqrt((diff ** 2).sum(-1) + EPS ** 2)  # (m, K)
        dp = Z - self.port
        rp = np.sqrt((dp ** 2).sum(-1) + EPS ** 2)  # (K,)
        u = (r - self.R) / self.s
        sla_pen = self.lam * self.s * np.logaddexp(0, u) if np.isfinite(self.R) else 0 * r
        g = self.r_out * r + self.r_in * rp[None, :] + sla_pen + mu[None, :]
        return diff, r, dp, rp, u, g

    def loss_and_grad(self, Z, mu, tau):
        diff, r, dp, rp, u, g = self._terms(Z, mu)
        P = softmax(-g / tau, axis=1)  # (m, K) soft assignment
        loss = float((self.d * -tau * logsumexp(-g / tau, axis=1)).sum() - self.cap * mu.sum())
        dg_dr = self.r_out + (self.lam * expit(u) if np.isfinite(self.R) else 0)
        W = self.d[:, None] * P  # (m, K)
        grad = ((W * dg_dr)[:, :, None] * diff / r[:, :, None]).sum(0)
        grad += (W.sum(0) * self.r_in / rp)[:, None] * dp
        return loss, grad, P

    def _init(self, K, rng):
        """Demand-weighted k-means++ seeding on customer locations."""
        idx = [rng.choice(len(self.d), p=self.d / self.d.sum())]
        for _ in range(1, K):
            D = ((self.C[:, None] - self.C[idx][None]) ** 2).sum(-1).min(1) * self.d
            idx.append(rng.choice(len(self.d), p=D / D.sum()))
        return self.C[idx].astype(float) + rng.normal(0, 5, (K, 2))

    def fit(self, K, *, iters=1500, lr=8.0, tau0=20.0, tau1=0.2, eta=2.0, seed=0):
        if not 1 <= K <= min(len(self.d), self.inst.n) or iters < 2:
            raise ValueError("K must fit the customer/candidate counts; iters must be at least 2")
        rng = np.random.default_rng(seed)
        Z = self._init(K, rng)
        mu = np.zeros(K)
        m1, m2 = np.zeros_like(Z), np.zeros_like(Z)
        b1, b2 = 0.9, 0.999
        trace = {"loss": [], "Z": []}
        for t in range(1, iters + 1):
            frac = (t - 1) / (iters - 1)
            tau = tau0 * (tau1 / tau0) ** frac
            loss, g, P = self.loss_and_grad(Z, mu, tau)
            m1 = b1 * m1 + (1 - b1) * g
            m2 = b2 * m2 + (1 - b2) * g ** 2
            step = lr * (1 - 0.9 * frac)
            Z -= step * (m1 / (1 - b1 ** t)) / (np.sqrt(m2 / (1 - b2 ** t)) + 1e-8)
            load = (self.d[:, None] * P).sum(0)
            mu = np.maximum(0.0, mu + eta * (load - self.cap) / self.cap)
            trace["loss"].append(loss)
            if t % 25 == 0 or t == 1:
                trace["Z"].append(Z.copy())
        return Z, mu, trace

    def snap(self, Z) -> np.ndarray:
        """Map continuous sites to distinct candidate counties (min total displacement)."""
        D = np.sqrt(((Z[:, None] - self.cand_xy[None]) ** 2).sum(-1))
        _, cols = linear_sum_assignment(D)
        mask = np.zeros(self.inst.n, bool)
        mask[cols] = True
        return mask


def repair(inst: Instance, mask: np.ndarray, max_steps: int | None = None) -> np.ndarray:
    """Reduce unmet demand and minimum-throughput deficits by opening or closing sites.

    This local search can stop before reaching feasibility. Callers retain and report
    the remaining deficits rather than treating a repaired network as feasible by name.
    """
    mask = mask.copy()
    max_steps = 2 * inst.n if max_steps is None else max_steps
    def rank(sol):
        deficit = float(sol.unmet.sum() + sol.balance_shortfall.sum())
        return (round(deficit, 6), sol.objective)
    for _ in range(max_steps):
        sol = evaluate(inst, mask)
        if rank(sol)[0] < 1e-6:
            break
        best_rank, best_mask = rank(sol), None
        for i in range(inst.n):
            trial = mask.copy()
            trial[i] ^= True
            trial_rank = rank(evaluate(inst, trial))
            if trial_rank < best_rank:
                best_rank, best_mask = trial_rank, trial
        if best_mask is None:
            break
        mask = best_mask
    return mask


def gradient_location(inst: Instance, k_range=range(5, 13), restarts: int = 4,
                      seed: int = 0, **fit_kw) -> tuple[Solution, Solution]:
    """Run the gradient method for each K, snap, score exactly, and keep the best network.

    Returns ``(snap_only, snap_plus_repair)`` solutions.
    """
    t0 = time.perf_counter()
    loc = ContinuousLocator(inst)
    runs = []
    for K in k_range:
        for r in range(restarts):
            Z, mu, trace = loc.fit(K, seed=seed + 100 * K + r, **fit_kw)
            mask = loc.snap(Z)
            fixed_mask = repair(inst, mask)
            runs.append({"K": K, "restart": r, "Z": Z, "mu": mu, "trace": trace,
                         "mask": mask, "lp_cost": evaluate(inst, mask).objective,
                         "repaired_mask": fixed_mask,
                         "repaired_cost": evaluate(inst, fixed_mask).objective,
                         "surrogate": trace["loss"][-1] + float(np.sort(inst.fixed)[:K].sum())})
    runtime = time.perf_counter() - t0
    def selection_key(run, key):
        sol = evaluate(inst, run[key])
        deficit = float(sol.unmet.sum() + sol.balance_shortfall.sum())
        return (deficit > 1e-6, sol.objective)
    best_snap = min(runs, key=lambda d: selection_key(d, "mask"))
    best_rep = min(runs, key=lambda d: selection_key(d, "repaired_mask"))
    snap = evaluate(inst, best_snap["mask"], method="Gradient (snap only)")
    rep = evaluate(inst, best_rep["repaired_mask"], method="Gradient (snap + repair)")
    for s, b in ((snap, best_snap), (rep, best_rep)):
        s.runtime_s = runtime
        s.extra.update(best_run=b, runs=runs, sla_radius_miles=loc.R * inst.params.circuity,
                       locator=loc)
    return snap, rep
