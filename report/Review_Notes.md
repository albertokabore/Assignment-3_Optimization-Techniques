# Review notes

The report, notebook, calculations, and figures were reviewed against the assignment
and the original Census and BTS sources on October 25, 2026.

| Issue | Correction |
|---|---|
| Population presented as customer demand | Retained original population; identified the pallet-demand conversion as an assumption. |
| Warehousing employment presented as warehouse capacity | Identified CBP as county economic activity; labeled the tier threshold, capacities, and costs as assumptions. |
| County centers described as warehouse or port coordinates | Labeled county proxies; identified zero Chatham inbound mileage as a co-location assumption. |
| Eight-hour delivery guarantees and inferred late-delivery rates | Used compliance with a modeled county-pair time screen. |
| Missing routes described as regression-imputed | All 20 missing pairs are local and use assumed times; no nonlocal regression was used. |
| Modified BTS times called directly observed | Preserved raw times and identified 180 adjusted source rows. |
| Missing CBP records treated as zero activity | Retained unknown values and required reported values for candidate counties. |
| Incorrect interpretation of demand-share duals | Used a pallet-flow LP with demand on the right-hand side; checked marginal cost numerically. |
| Usable capacity confused with rated capacity | Reported both; rated capacity changes upper and lower throughput bounds. |
| Missing gradient Lagrangian term | Added the negative capacity-price term to the loss. |
| Repair checked only unmet demand | Added opening/closing moves for minimum-throughput deficits and explicit feasibility checks. |
| Enumeration used a penalized problem | Used hard demand and throughput constraints. |
| Safety stock omitted the regional shock used in simulation | Included county and shared regional variance in approximate stock indicators. |
| Inventory reporting called inventory optimization | Explained cycle-stock coverage and the missing multi-period stock model. |
| Assumed six-hub network called an actual status quo | Identified an illustrative comparison; excluded it from feasible base-model rankings. |
| Simulated zero shortages described as eliminating risk | Limited conclusions to sampled scenarios and the assumed distribution. |
| Refresh option reused cached inputs | Made --refresh-data replace downloads and rebuild processed data. |
| Formulaic wording and document tags | Rewrote the prose and cleared document comments and tags. The notebook uses the author name and affiliation supplied by Albert Kabore. |
| Cleaning was not explicitly demonstrated | Added executable notebook checks, input validation, and a counted cleaning_audit.csv; kept data selection and modeling assumptions distinct. |

The compared 534 population rows, 522 CBP rows, 1,300 BTS rows, and two geography
downloads match their original sources. The checks and raw-file hashes are in
[source_verification.json](../results/source_verification.json).
The original Word package contained no watermark object or watermark text. The
rebuilt report has no watermark, template comment, or promotional footer. The notebook
retains Albert Kabore, PhD student in AI. Its narrative sections are actual Markdown
cells. Required mathematical and source terminology is retained.

The full analysis completed with 4,000 annealing proposals, four gradient restarts
for each K from 5 to 12, enumeration of 4,095 reduced networks, and 500 demand
scenarios. Thirty-eight tests passed. All seven optimized solutions passed the independent
constraint audit. The repaired gradient result now matches the MILP on this seeded
instance; it includes discrete repair and does not establish global convergence
for the continuous method.

The assignment permits synthetic inputs. Scenario assumptions remain visible because
public sources do not provide customer orders, available warehouse capacities,
property costs, or contracted freight prices. Removing those assumptions would leave
the problem unspecified. They are not described as measurements or evidence for an
investment decision.
