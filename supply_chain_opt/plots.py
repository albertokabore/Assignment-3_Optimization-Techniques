"""Static report figures (matplotlib). Colors follow a fixed categorical order, thin marks,
recessive grid, one y-axis per chart."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import config as C  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 9.5,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlecolor": INK,
    "axes.titlelocation": "left", "legend.frameon": False, "lines.linewidth": 2,
    "figure.dpi": 110, "savefig.dpi": 200, "savefig.bbox": "tight",
})


def _save(fig, name):
    C.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = C.FIGURES_DIR / name
    fig.savefig(path, metadata={"Software": None})  # no tool tag in the PNG header
    plt.close(fig)
    return path


def _states(ax, outlines):
    for rings in outlines.values():
        for r in rings:
            ax.fill(r[:, 0], r[:, 1], color="#f1f0ec", ec="#c9c8c2", lw=0.6, zorder=0)
    ax.set_xlim(-91.5, -75.0)
    ax.set_ylim(24.3, 37.2)
    ax.set_aspect(1 / np.cos(np.radians(31)))
    ax.grid(False)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def network_map(inst, sol, outlines, name="fig_network_map.png", title=None):
    fig, ax = plt.subplots(figsize=(8, 7.2))
    _states(ax, outlines)
    cand, cust = inst.candidates, inst.customers
    F = sol.flows
    for i, j in zip(*np.nonzero(F > 1)):
        ax.plot([cand.lon[i], cust.lon[j]], [cand.lat[i], cust.lat[j]], color=SERIES[0],
                lw=0.4 + 2.2 * F[i, j] / F.max(), alpha=0.55, zorder=1)
    ax.scatter(cust.lon, cust.lat, s=inst.demand / 12, color=SERIES[1], alpha=0.8,
               ec=SURFACE, lw=0.8, zorder=2, label="Customer county (size = demand)")
    closed = ~sol.open
    ax.scatter(cand.lon[closed], cand.lat[closed], marker="s", s=34, fc="none",
               ec=MUTED, lw=1.2, zorder=3, label="Candidate DC (closed)")
    ax.scatter(cand.lon[sol.open], cand.lat[sol.open], marker="s", s=90, color=SERIES[0],
               ec=SURFACE, lw=1.5, zorder=4, label="Open DC")
    port = cand[cand.fips == C.SUPPLY_FIPS].iloc[0]
    ax.scatter(port.lon, port.lat, marker="*", s=260, color=SERIES[3], ec=INK, lw=0.6,
               zorder=5, label="Supply proxy: Chatham County")
    for i in np.flatnonzero(sol.open):
        ax.annotate(cand.label[i], (cand.lon[i], cand.lat[i]), xytext=(5, 5),
                    textcoords="offset points", fontsize=8, color=INK, zorder=6)
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title(title or f"{sol.method}: {sol.n_open} DCs, ${sol.objective / 1e6:.3f}M / week")
    return _save(fig, name)


def model_comparison(df: pd.DataFrame, opt_cost: float):
    df = df.sort_values("network_cost", ascending=True)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    gap = 100 * (df.network_cost / opt_cost - 1)
    bars = ax.barh(df.method, gap, color=SERIES[0], height=0.6)
    for b, g, t in zip(bars, gap, df.network_cost):
        ax.text(max(b.get_width(), 0) + 0.15, b.get_y() + b.get_height() / 2,
                f"{g:+.2f}%  (${t / 1e6:.3f}M)", va="center", fontsize=8, color=INK2)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_xlabel("Cost gap vs. MILP optimum (%)")
    ax.set_title("Solution quality")
    ax.invert_yaxis()
    ax.set_xlim(right=max(gap.max(), 1) * 1.45)
    ax = axes[1]
    bars = ax.barh(df.method, df.runtime_s, color=SERIES[2], height=0.6)
    for b, r in zip(bars, df.runtime_s):
        ax.text(b.get_width() * 1.15, b.get_y() + b.get_height() / 2, f"{r:.2f}s",
                va="center", fontsize=8, color=INK2)
    ax.set_xscale("log")
    ax.set_xlabel("Wall-clock runtime (s, log scale)")
    ax.set_title("Computational cost")
    ax.invert_yaxis()
    ax.set_yticklabels([])
    fig.tight_layout()
    return _save(fig, "fig_model_comparison.png")


def cost_breakdown(df: pd.DataFrame):
    parts = ["fixed", "inbound", "outbound", "handling"]
    names = ["DC fixed", "Inbound linehaul", "Outbound delivery", "Handling"]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    left = np.zeros(len(df))
    for k, (p, nm) in enumerate(zip(parts, names)):
        v = df[p].to_numpy() / 1e6
        ax.barh(df.method, v, left=left, color=SERIES[k], height=0.6, label=nm,
                ec=SURFACE, lw=2)
        left += v
    for y, t in enumerate(left):
        ax.text(t + 0.02, y, f"${t:.2f}M", va="center", fontsize=8, color=INK2)
    ax.invert_yaxis()
    ax.set_xlabel("Weekly network cost ($M)")
    ax.set_title("Cost composition by method")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.45, -0.15), fontsize=8)
    return _save(fig, "fig_cost_breakdown.png")


def sa_convergence(history: np.ndarray, opt_cost: float):
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.plot(history[:, 0] / 1e6, color=SERIES[0], lw=1, alpha=0.6, label="Current state")
    ax.plot(history[:, 1] / 1e6, color=SERIES[1], lw=2, label="Best found")
    ax.axhline(opt_cost / 1e6, color=INK2, lw=1, ls="--", label="MILP optimum")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Weekly cost incl. penalties ($M)")
    ax.set_title("Simulated annealing convergence")
    ax.legend()
    return _save(fig, "fig_sa_convergence.png")


def gradient_figures(inst, grad_sol, outlines):
    run = grad_sol.extra["best_run"]
    loc = grad_sol.extra["locator"]
    lat0 = 31.0
    to_ll = lambda Z: (Z[:, 0] / (69.17 * np.cos(np.radians(lat0))), Z[:, 1] / 69.05)  # noqa: E731
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2), gridspec_kw={"width_ratios": [1.1, 1]})
    ax = axes[0]
    _states(ax, outlines)
    ax.scatter(inst.customers.lon, inst.customers.lat, s=inst.demand / 15, color=SERIES[1],
               alpha=0.5, ec="none", label="Customer demand")
    traj = np.stack(run["trace"]["Z"])  # (T, K, 2)
    for k in range(traj.shape[1]):
        lon, lat = to_ll(traj[:, k])
        ax.plot(lon, lat, color=SERIES[0], lw=1, alpha=0.7)
        ax.scatter(lon[0], lat[0], s=14, color=MUTED, zorder=3)
    lon, lat = to_ll(run["Z"])
    ax.scatter(lon, lat, s=70, color=SERIES[0], ec=SURFACE, lw=1.2, zorder=4,
               label="Fitted continuous sites")
    m = grad_sol.open
    ax.scatter(inst.candidates.lon[m], inst.candidates.lat[m], marker="s", s=60, fc="none",
               ec=SERIES[7], lw=1.6, zorder=5, label="Snapped + repaired DCs")
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title(f"Gradient descent site trajectories (K = {run['K']})")
    ax = axes[1]
    for k, K in enumerate(sorted({r["K"] for r in grad_sol.extra["runs"]})):
        r0 = next(r for r in grad_sol.extra["runs"] if r["K"] == K and r["restart"] == 0)
        if K in (6, 8, 10, 12):
            ax.plot(np.array(r0["trace"]["loss"]) / 1e6, lw=1.5, label=f"K = {K}",
                    color=SERIES[[6, 8, 10, 12].index(K)])
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Smoothed Lagrangian ($M)")
    ax.set_title("Changing temperature and capacity prices")
    ax.legend()
    fig.tight_layout()
    return _save(fig, "fig_gradient.png")


def gradient_k_curve(runs):
    df = pd.DataFrame([{k: r[k] for k in ("K", "restart", "lp_cost", "repaired_cost")}
                       for r in runs])
    g = df.groupby("K").min()
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.plot(g.index, g.lp_cost / 1e6, marker="o", ms=6, color=SERIES[1], label="Snap only")
    ax.plot(g.index, g.repaired_cost / 1e6, marker="o", ms=6, color=SERIES[0],
            label="Snap + repair")
    ax.set_yscale("log")
    ax.set_xlabel("Number of continuous sites K")
    ax.set_ylabel("Exact LP cost incl. penalties ($M, log)")
    ax.set_title("Gradient method: best of restarts by K")
    ax.legend()
    return _save(fig, "fig_gradient_k.png")


def sensitivity_grid(sens: pd.DataFrame, base_cost: float):
    params = list(dict.fromkeys(sens.parameter))
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.6))
    for ax, p in zip(axes.ravel(), params):
        d = sens[sens.parameter == p].copy()
        if p == "SLA (hours)":
            d["x"] = d.level.fillna(24).astype(float)
            xlabel = "Modeled p50 threshold (h; 24 = no limit)"
        else:
            d["x"] = d.level.astype(float)
            xlabel = "Multiplier on base value" if p != "Min utilization" else "u_min"
        ok = d.network_cost.notna()
        ax.plot(d.x[ok], d.network_cost[ok] / 1e6, marker="o", ms=6, color=SERIES[0])
        for x, y, n in zip(d.x[ok], d.network_cost[ok] / 1e6, d.n_dcs[ok]):
            ax.annotate(f"{int(n)}", (x, y), xytext=(0, 7), textcoords="offset points",
                        ha="center", fontsize=7.5, color=INK2)
        for x in d.x[~ok]:
            ax.axvline(x, color=SERIES[7], lw=1, ls=":")
        ax.axhline(base_cost / 1e6, color=MUTED, lw=0.8, ls="--")
        ax.set_title(p)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Optimal cost ($M / wk)")
    fig.suptitle("Sensitivity of the optimal network (labels = # open DCs; dotted = infeasible)",
                 x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout()
    return _save(fig, "fig_sensitivity.png")


def tornado_chart(tor: pd.DataFrame, delta: float = 0.2):
    fig, ax = plt.subplots(figsize=(8, 3.8))
    y = np.arange(len(tor))
    ax.barh(y, 100 * tor.low, color=SERIES[0], height=0.55, label=f"-{delta:.0%}")
    ax.barh(y, 100 * tor.high, color=SERIES[1], height=0.55, label=f"+{delta:.0%}")
    ax.set_yticks(y, tor.parameter)
    ax.axvline(0, color=INK2, lw=0.8)
    ax.set_xlabel("Change in re-optimised weekly cost (%)")
    ax.set_title(f"Tornado: one-at-a-time ±{delta:.0%} parameter shocks")
    ax.legend(loc="lower right")
    return _save(fig, "fig_tornado.png")


def monte_carlo_chart(mc: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for k, (name, d) in enumerate(mc.groupby("network", sort=False)):
        axes[0].hist(d.cost_with_penalty / 1e6, bins=40, alpha=0.6, color=SERIES[k], label=name)
        axes[1].hist(100 * d.service_level, bins=40, alpha=0.6, color=SERIES[k], label=name)
    axes[0].set_xlabel("Weekly cost incl. $500/pallet unmet penalty ($M)")
    axes[0].set_ylabel("Scenarios")
    axes[0].set_title("Cost under demand uncertainty")
    axes[1].set_xlabel("Demand served on allowed arcs (%)")
    axes[1].set_title("Fill rate under assumed demand shocks")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    return _save(fig, "fig_monte_carlo.png")


def utilization_chart(inst, sols):
    fig, ax = plt.subplots(figsize=(10, 4))
    w = 0.8 / len(sols)
    x = np.arange(inst.n)
    for k, s in enumerate(sols):
        u = s.flows.sum(1) / inst.capacity
        ax.bar(x + k * w - 0.4 + w / 2, 100 * u, width=w, color=SERIES[k], label=s.method,
               ec=SURFACE, lw=1)
    ax.axhline(100 * inst.params.u_max, color=SERIES[7], lw=1, ls="--")
    ax.axhline(100 * inst.params.u_min, color=MUTED, lw=1, ls="--")
    ax.text(inst.n - 0.5, 100 * inst.params.u_max + 2, "u_max", ha="right", fontsize=8, color=INK2)
    ax.text(inst.n - 0.5, 100 * inst.params.u_min + 2, "u_min", ha="right", fontsize=8, color=INK2)
    ax.set_xticks(x, inst.candidates.label, rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("Utilization of capacity (%)")
    ax.set_title("Weekly throughput / assumed DC capacity")
    ax.legend(fontsize=8, ncol=len(sols), loc="upper left")
    return _save(fig, "fig_utilization.png")


def iteration_chart(it: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    labels = it.version.str.replace(" (base)", "", regex=False)
    axes[0].bar(labels, it.network_cost / 1e6, color=SERIES[0], width=0.6)
    for k, (c, n) in enumerate(zip(it.network_cost / 1e6, it.n_dcs)):
        axes[0].text(k, c + 0.02, f"${c:.3f}M\n{n} DCs", ha="center", fontsize=7.5, color=INK2)
    axes[0].set_ylim(it.network_cost.min() / 1e6 * 0.9, it.network_cost.max() / 1e6 * 1.04)
    axes[0].set_ylabel("Optimal weekly cost ($M)")
    axes[0].set_title("Model iterations: cost of each constraint family")
    axes[1].bar(labels, 100 * it.pct_within_sla_p50, color=SERIES[2], width=0.6)
    axes[1].set_ylabel("Volume on arcs with modeled p50 ≤ 8 h (%)")
    axes[1].set_title("Travel-time proxy compliance")
    for ax in axes:
        ax.tick_params(axis="x", rotation=25, labelsize=8)
        plt.setp(ax.get_xticklabels(), ha="right")
    fig.tight_layout()
    return _save(fig, "fig_iterations.png")


def travel_time_chart(routes: pd.DataFrame, sla_minutes: float, circuity: float = 1.2):
    r = routes[routes.orig != routes.dest]
    fig, ax = plt.subplots(figsize=(7.5, 4))
    obs = r.bts_observed.astype(bool)
    ax.scatter(r.gc_miles[obs] * circuity, r.tt_p50[obs] / 60, s=9, color=SERIES[0], alpha=0.45,
               ec="none", label="BTS-derived (includes assumed floor)")
    if (~obs).any():
        ax.scatter(r.gc_miles[~obs] * circuity, r.tt_p50[~obs] / 60, s=16, color=SERIES[1], marker="x",
                   lw=1, label="Imputed (regression)")
    ax.axhline(sla_minutes / 60, color=SERIES[7], lw=1, ls="--")
    ax.text(5, sla_minutes / 60 + 0.8, f"{sla_minutes / 60:g}-h model threshold", fontsize=8, color=INK2)
    ax.set_xlabel("Estimated road distance (mi)")
    ax.set_ylabel("Modeled county-pair p50 elapsed time (h)")
    ax.set_title("County-pair time proxy vs. estimated distance")
    ax.legend()
    return _save(fig, "fig_travel_time.png")


def shadow_price_map(dem: pd.DataFrame, cap: pd.DataFrame, inst, outlines):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    _states(ax, outlines)
    sc = ax.scatter(dem.lon, dem.lat, c=dem.marginal_cost_per_pallet, s=dem.demand / 10,
                    cmap="Blues", vmin=dem.marginal_cost_per_pallet.min() * 0.6,
                    ec=INK2, lw=0.5)
    cb = fig.colorbar(sc, ax=ax, shrink=0.7)
    cb.set_label("Marginal cost to serve ($ / pallet)")
    ax.set_title("Demand duals: marginal cost per extra pallet")
    ax = axes[1]
    c = cap.sort_values("capacity_shadow_price")
    ax.barh(c.dc, c.capacity_shadow_price, color=SERIES[0], height=0.6)
    for y, (v, u) in enumerate(zip(c.capacity_shadow_price, c.utilization)):
        ax.text(v + 0.1, y, f"${v:.2f}  (util {u:.0%})", va="center", fontsize=8, color=INK2)
    ax.set_xlabel("Local savings per extra pallet/week of rated capacity ($)")
    ax.set_title("Capacity shadow prices (open DCs)")
    ax.set_xlim(right=max(c.capacity_shadow_price.max() * 1.6, 1))
    fig.tight_layout()
    return _save(fig, "fig_shadow_prices.png")
