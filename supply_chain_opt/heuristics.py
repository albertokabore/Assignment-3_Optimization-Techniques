"""Heuristic optimization over the set of open DCs.

All heuristics search the binary "which DCs are open" space; each candidate network is
scored with the transportation LP in :func:`models.evaluate` (a *matheuristic*: heuristic
master, exact sub-problem). Scores are memoised because SA revisits states often.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .instance import Instance
from .models import Solution, evaluate


class Evaluator:
    def __init__(self, inst: Instance):
        self.inst = inst
        self.cache: dict[bytes, float] = {}
        self.calls = 0

    def __call__(self, mask: np.ndarray) -> float:
        key = np.packbits(mask).tobytes()
        if key not in self.cache:
            self.calls += 1
            self.cache[key] = evaluate(self.inst, mask).objective
        return self.cache[key]


def _finish(inst: Instance, mask, name: str, t0: float, ev: Evaluator, **extra) -> Solution:
    sol = evaluate(inst, mask, method=name)
    sol.runtime_s = time.perf_counter() - t0
    sol.extra.update(lp_evaluations=ev.calls, **extra)
    return sol


def greedy_add(inst: Instance) -> Solution:
    """Start with no DCs; repeatedly open the DC that lowers total cost the most."""
    t0, ev = time.perf_counter(), Evaluator(inst)
    mask = np.zeros(inst.n, bool)
    best = ev(mask)
    while True:
        trials = []
        for i in np.flatnonzero(~mask):
            m2 = mask.copy(); m2[i] = True
            trials.append((ev(m2), i))
        if not trials:
            break
        cost, i = min(trials)
        if cost >= best - 1e-6:
            break
        mask[i], best = True, cost
    return _finish(inst, mask, "Greedy ADD", t0, ev)


def greedy_drop(inst: Instance) -> Solution:
    """Start with all DCs open; repeatedly close the DC whose removal saves the most."""
    t0, ev = time.perf_counter(), Evaluator(inst)
    mask = np.ones(inst.n, bool)
    best = ev(mask)
    while mask.sum() > 1:
        trials = []
        for i in np.flatnonzero(mask):
            m2 = mask.copy(); m2[i] = False
            trials.append((ev(m2), i))
        cost, i = min(trials)
        if cost >= best - 1e-6:
            break
        mask[i], best = False, cost
    return _finish(inst, mask, "Greedy DROP", t0, ev)


def simulated_annealing(inst: Instance, *, iters: int = 4000, t_start: float = 0.02,
                        t_end: float = 1e-5, seed: int = 42,
                        init: np.ndarray | None = None) -> Solution:
    """Simulated annealing with flip (open/close) and swap moves.

    Temperature is relative to the current cost, so the acceptance rule
    ``exp(-delta / (T * cost))`` is scale-free. Cooling is geometric.
    """
    rng = np.random.default_rng(seed)
    t0, ev = time.perf_counter(), Evaluator(inst)
    mask = rng.random(inst.n) < 0.4 if init is None else np.asarray(init, bool).copy()
    cost = ev(mask)
    best_mask, best = mask.copy(), cost
    alpha = (t_end / t_start) ** (1 / max(iters - 1, 1))
    T = t_start
    history = np.empty((iters, 2))
    for k in range(iters):
        cand = mask.copy()
        opened, closed = np.flatnonzero(mask), np.flatnonzero(~mask)
        if rng.random() < 0.5 and len(opened) and len(closed):
            cand[rng.choice(opened)] = False  # swap
            cand[rng.choice(closed)] = True
        else:
            cand[rng.integers(inst.n)] ^= True  # flip
        c2 = ev(cand)
        delta = c2 - cost
        if delta <= 0 or rng.random() < math.exp(-delta / (T * cost)):
            mask, cost = cand, c2
            if cost < best:
                best_mask, best = mask.copy(), cost
        history[k] = (cost, best)
        T *= alpha
    return _finish(inst, best_mask, "Simulated annealing", t0, ev, history=history)
