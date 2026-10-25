"""Turn processed data into a numeric optimization instance (arrays, no pandas in hot loops)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as C
from .data import haversine_miles


@dataclass
class Instance:
    candidates: pd.DataFrame  # n rows
    customers: pd.DataFrame  # m rows
    params: C.Params
    demand: np.ndarray  # (m,) pallets / week
    capacity: np.ndarray  # (n,) pallets / week
    fixed: np.ndarray  # (n,) $ / week
    road_miles: np.ndarray  # (n, m) DC -> customer
    inbound_miles: np.ndarray  # (n,) port -> DC
    time_p50: np.ndarray  # (n, m) minutes
    time_p75: np.ndarray  # (n, m) minutes

    @property
    def n(self) -> int:
        return len(self.candidates)

    @property
    def m(self) -> int:
        return len(self.customers)

    @property
    def outbound_cost(self) -> np.ndarray:
        """$ per pallet DC i -> customer j."""
        return self.params.outbound_rate * self.road_miles

    @property
    def inbound_cost(self) -> np.ndarray:
        """$ per pallet port -> DC i."""
        return self.params.inbound_rate * self.inbound_miles

    @property
    def unit_cost(self) -> np.ndarray:
        """Total variable $ per pallet routed port -> i -> j (inbound + handling + outbound)."""
        return (self.outbound_cost + self.inbound_cost[:, None] + self.params.handling_cost)

    @property
    def allowed(self) -> np.ndarray:
        """Boolean (n, m) mask of arcs that satisfy the delivery-time SLA."""
        if self.params.sla_minutes is None:
            return np.ones((self.n, self.m), dtype=bool)
        return self.time_p50 <= self.params.sla_minutes

    def labels(self, mask=None) -> list[str]:
        lab = self.candidates.label.tolist()
        return lab if mask is None else [l for l, o in zip(lab, mask) if o]

    def with_params(self, **kwargs) -> "Instance":
        """Rebuild the instance with modified parameters (used by sensitivity analysis)."""
        return build_instance(self.candidates, self.customers, self._routes,
                              self.params.with_(**kwargs))

    def subset(self, cand_idx, cust_idx=None) -> "Instance":
        """Restrict to some candidates/customers (used for brute-force validation and tests)."""
        cand_idx = np.asarray(cand_idx)
        cust_idx = np.arange(self.m) if cust_idx is None else np.asarray(cust_idx)
        sub = Instance(
            self.candidates.iloc[cand_idx].reset_index(drop=True),
            self.customers.iloc[cust_idx].reset_index(drop=True),
            self.params, self.demand[cust_idx], self.capacity[cand_idx], self.fixed[cand_idx],
            self.road_miles[np.ix_(cand_idx, cust_idx)], self.inbound_miles[cand_idx],
            self.time_p50[np.ix_(cand_idx, cust_idx)], self.time_p75[np.ix_(cand_idx, cust_idx)])
        sub._routes = self._routes
        return sub


def build_instance(candidates: pd.DataFrame, customers: pd.DataFrame, routes: pd.DataFrame,
                   params: C.Params = C.DEFAULT_PARAMS) -> Instance:
    p = params
    demand = customers.population.to_numpy() * p.pallets_per_1000_pop / 1000.0
    capacity = candidates.tier.map(p.capacity).to_numpy(dtype=float)
    density_factor = np.clip(candidates.density.to_numpy() / 3000.0, 0, 1)
    fixed = candidates.tier.map(p.fixed_cost).to_numpy(dtype=float) * (
        1 - p.density_premium / 2 + p.density_premium * density_factor)

    piv = routes.pivot(index="orig", columns="dest")
    order_o, order_d = candidates.fips.tolist(), customers.fips.tolist()
    road = piv["gc_miles"].loc[order_o, order_d].to_numpy() * p.circuity
    t50 = piv["tt_p50"].loc[order_o, order_d].to_numpy()
    t75 = piv["tt_p75"].loc[order_o, order_d].to_numpy()

    port = candidates.loc[candidates.fips == C.SUPPLY_FIPS].iloc[0]
    inbound = haversine_miles(port.lat, port.lon, candidates.lat.to_numpy(),
                              candidates.lon.to_numpy()) * p.circuity

    inst = Instance(candidates, customers, p, demand, capacity, fixed, road, inbound, t50, t75)
    inst._routes = routes
    return inst


def load_instance(params: C.Params = C.DEFAULT_PARAMS) -> Instance:
    from .data import build_dataset
    d = build_dataset()
    return build_instance(d["candidates"], d["customers"], d["routes"], params)
