"""Check assignment artifacts against source hashes, tables, and executed notebook cells."""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import nbformat
import numpy as np
import pandas as pd
from docx import Document

from supply_chain_opt import config as C
from supply_chain_opt.cleaning import cleaning_audit
from supply_chain_opt.data import build_dataset
from supply_chain_opt.instance import build_instance


def main():
    summary = json.loads((C.RESULTS_DIR / "summary.json").read_text(encoding="utf-8"))
    verification = json.loads((C.RESULTS_DIR / "source_verification.json").read_text(encoding="utf-8"))
    assert verification["all_checks_pass"], "Source comparisons failed"
    assert all(hashlib.sha256((C.ROOT / row["file"]).read_bytes()).hexdigest() == row["sha256"]
               for row in verification["raw_file_hashes"]), "Raw files changed since verification"
    data = build_dataset()
    pd.testing.assert_frame_equal(pd.read_csv(C.RESULTS_DIR / "cleaning_audit.csv"), cleaning_audit(data))
    inst = build_instance(data["candidates"], data["customers"], data["routes"])

    comparison = pd.read_csv(C.RESULTS_DIR / "model_comparison.csv")
    components = comparison[["fixed", "inbound", "outbound", "handling"]].sum(axis=1)
    np.testing.assert_allclose(comparison.network_cost, components, atol=1e-6)
    np.testing.assert_allclose(comparison.transport_cost, comparison.inbound + comparison.outbound, atol=1e-6)
    audit = pd.read_csv(C.RESULTS_DIR / "feasibility_audit.csv")
    assert audit.drop(columns="method").all().all(), "An optimized network failed its constraint audit"
    assert comparison.loc[comparison.feasible_base_model, "unmet_pallets"].max() < 1e-6
    assert comparison.loc[comparison.feasible_base_model, "balance_shortfall"].max() < 1e-6
    opt = summary["milp"]
    np.testing.assert_allclose(comparison.iloc[0].network_cost, opt["network_cost"], atol=1e-6)
    np.testing.assert_allclose(summary["brute_force"]["enumeration_cost"],
                               summary["brute_force"]["milp_cost"], rtol=1e-8)

    flows = pd.read_csv(C.RESULTS_DIR / "optimal_flows.csv", dtype={"dc_fips": str, "customer_fips": str})
    demand = flows.groupby("customer_fips").pallets_week.sum().reindex(inst.customers.fips, fill_value=0)
    np.testing.assert_allclose(demand, inst.demand, atol=1e-6)
    throughput = flows.groupby("dc_fips").pallets_week.sum().reindex(inst.candidates.fips, fill_value=0).to_numpy()
    opened = inst.candidates.label.isin(opt["dcs"].split("; ")).to_numpy()
    assert opened.sum() == opt["n_dcs"]
    assert np.all(throughput <= inst.params.u_max * inst.capacity * opened + 1e-6)
    assert np.all(throughput[opened] >= inst.params.u_min * inst.capacity[opened] - 1e-6)
    assert flows.modeled_p50_minutes.max() <= inst.params.sla_minutes
    np.testing.assert_allclose((flows.pallets_week * flows.modeled_unit_cost).sum() + inst.fixed[opened].sum(),
                               opt["network_cost"], atol=1e-6)
    inventory = pd.read_csv(C.RESULTS_DIR / "inventory_policy.csv")
    np.testing.assert_allclose(inventory.cycle_stock.sum(), opt["cycle_stock"])
    np.testing.assert_allclose(inventory.safety_stock_approx.sum(), opt["safety_stock"])
    simulation = pd.read_csv(C.RESULTS_DIR / "monte_carlo.csv")
    scenarios = int(summary["run"]["simulation_scenarios"])
    assert simulation.groupby("network").scenario.nunique().eq(scenarios).all()

    notebook = nbformat.read(C.ROOT / "notebooks" / "supply_chain_optimization.ipynb", as_version=4)
    nbformat.validate(notebook)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    markdown = [cell.source for cell in notebook.cells if cell.cell_type == "markdown"]
    assert all(cell.execution_count is not None for cell in code)
    assert not any(output.output_type == "error" for cell in code for output in cell.outputs)
    assert not any("display(Markdown(" in cell.source for cell in code), "Narrative is in code cells"
    for heading in ["Data cleaning and validation", "Data-cleaning observations",
                    "Final insights and recommendations", "Conclusion"] + [f"Observations: Step {i}" for i in range(1, 7)]:
        assert any(heading in text for text in markdown), f"Missing Markdown section: {heading}"
    assert "Albert Kabore, PhD student in AI" in markdown[0]
    assert any(f"{opt['network_cost']:,.0f}" in text for text in markdown)
    document = Document(C.ROOT / "report" / "Assignment3_Optimization_Report.docx")
    report_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Data cleaning and validation" in report_text
    assert f"${opt['network_cost']:,.0f}" in report_text

    suites = ET.parse(C.RESULTS_DIR / "test_results.xml").getroot().findall(".//testsuite")
    tests = {key: sum(int(suite.get(key, 0)) for suite in suites)
             for key in ("tests", "failures", "errors", "skipped")}
    assert tests["tests"] > 0 and tests["failures"] == tests["errors"] == 0, "Test result file has failures"
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "all_checks_pass": True,
              "tests": tests, "source_comparison_checked_at_utc": verification["checked_at_utc"],
              "raw_hashes_match": True, "cleaning_audit_matches": True,
              "flow_cost_and_capacity_checks_pass": True, "executed_notebook_code_cells": len(code),
              "notebook_errors": 0, "narrative_cell_type": "markdown",
              "simulation_scenarios_per_network": scenarios,
              "scope": "Checks apply to the saved exercise run; synthetic assumptions remain explicitly labeled."}
    (C.RESULTS_DIR / "validation_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
