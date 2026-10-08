# Full PH → eBay ID → Dashboard Validation

Run: 2026-10-08T09:06:09 (read-only). Dashboard data snapshot: 2026-10-08T08:48:02.

## Overall: **PASS**

| Criterion | Result |
|---|---|
| ownership: live Sheet (source of truth) == extracted mapping for every PH | PASS |
| all Sheet PH names resolve to a DB PH | PASS |
| every official ID in dashboard or excluded by the documented grain rule (verified by SQL) | PASS |
| live source universe == dashboard universe | PASS |
| 30-PH browser test: displayed == expected, 0 cross-PH, 0 duplicates (All PHs, 30 PHs, Unassigned) | PASS |
| PH dropdown = All PHs + 30 PHs + Unassigned | PASS |
| every rendered field == live source (row level, exact display string) | PASS |
| no build/render errors (dashboard == build snapshot) | PASS |
| per-PH x account x marketplace totals == live source | PASS |
| global totals == live source | PASS |
| All PHs + Unassigned = every dashboard row, disjoint | PASS |
| no undefined/NaN/Infinity/null rendered | PASS |
| no JS/console errors | PASS |
| no duplicate dashboard rows | PASS |

## Counts

| Measure | Value |
|---|---|
| phs_tested | 30 |
| phs_passed | 30 |
| source_official_ids | 3219 |
| official_ids_in_dashboard | 3169 |
| dashboard_rows | 14912 |
| dashboard_rows_all_phs | 3170 |
| unassigned_rows | 11742 |
| missing_ids | 50 |
| missing_ids_unexplained | 0 |
| extra_ids | 0 |
| duplicate_rows | 0 |
| multi_ph_ids | 0 |
| multi_ph_ids_in_dashboard | 0 |
| rows_checked | 14912 |
| cells_checked | 414898 |
| field_mismatches | 0 |
| segment_mismatches | 0 |
| cross_ph_contamination | 0 |

Field mismatches by class: {}

Field mismatches by group: {}

Field mismatches by field: {}

## PH summary

| PH | Source IDs | Dashboard IDs | Rows | Missing | Extra | Data Errors | Segment Errors | Status |
|---|---|---|---|---|---|---|---|---|
| Abinayaa | 100 | 100 | 100 | 0 | 0 | 0 | 0 | PASS |
| Akalika | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| Akanila | 24 | 24 | 24 | 0 | 0 | 0 | 0 | PASS |
| Arudchelvi | 158 | 151 | 151 | 7 | 0 | 0 | 0 | PASS |
| Dilakshiga | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| Dilani | 250 | 250 | 250 | 0 | 0 | 0 | 0 | PASS |
| Illakkiya | 269 | 267 | 267 | 2 | 0 | 0 | 0 | PASS |
| Jasmini | 224 | 224 | 224 | 0 | 0 | 0 | 0 | PASS |
| Jathisha | 42 | 42 | 42 | 0 | 0 | 0 | 0 | PASS |
| Jubista | 60 | 60 | 60 | 0 | 0 | 0 | 0 | PASS |
| mothajini | 68 | 66 | 66 | 2 | 0 | 0 | 0 | PASS |
| Nithushana | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| paulr | 242 | 236 | 236 | 6 | 0 | 0 | 0 | PASS |
| prasath | 179 | 179 | 180 | 0 | 0 | 0 | 0 | PASS |
| preethi | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| Ramsika | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| Renuha | 92 | 92 | 92 | 0 | 0 | 0 | 0 | PASS |
| Saranya | 76 | 76 | 76 | 0 | 0 | 0 | 0 | PASS |
| Sarbavi | 42 | 42 | 42 | 0 | 0 | 0 | 0 | PASS |
| Shanthini | 56 | 56 | 56 | 0 | 0 | 0 | 0 | PASS |
| shimee | 390 | 367 | 367 | 23 | 0 | 0 | 0 | PASS |
| thanusha | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| Tharshana | 54 | 54 | 54 | 0 | 0 | 0 | 0 | PASS |
| Tharsiga(nelli) | 90 | 90 | 90 | 0 | 0 | 0 | 0 | PASS |
| Tharsika(jaffna) | 160 | 160 | 160 | 0 | 0 | 0 | 0 | PASS |
| Theepana | 76 | 76 | 76 | 0 | 0 | 0 | 0 | PASS |
| Thojika | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PASS |
| thuwaraga | 287 | 277 | 277 | 10 | 0 | 0 | 0 | PASS |
| utharsika | 184 | 184 | 184 | 0 | 0 | 0 | 0 | PASS |
| Vaishnavi | 96 | 96 | 96 | 0 | 0 | 0 | 0 | PASS |

## Global totals: mismatches (live source vs dashboard)


## Data quality

- negative order values (rows): 0
- duplicate dashboard rows (account, marketplace, eBay ID): 0
- eBay IDs on >1 dashboard row (different account/marketplace): 2
- invalid eBay ID format in dashboard: 0
- blank eBay ID in dashboard: 0
- duplicate PH+eBay ID pairs in Sheet: 0
- duplicate PH+eBay ID rows in DB: 149
- invalid Sheet cells (not a 12-digit ID): 2
- undefined/NaN/Infinity/null in rendered cells: 0
- PH-owned dashboard IDs with no SKU in any source: 210
- Sheet account column differs from dashboard account (ownership is by eBay ID; informational): 36

## HTML

- JS errors: 0
- broken cells: 0
- KPI cards: none on page (removed per user 2026-10-01); summary = count line + account header totals
- sorting: none on page (removed per user)
- screenshot All PHs: reports/fullval_1_all_phs.png
- screenshot One PH with IDs (Abinayaa): reports/fullval_2_ph_abinayaa.png
- screenshot PH with 0 IDs (Akalika): reports/fullval_3_ph_zero_akalika.png
- screenshot Unassigned: reports/fullval_4_unassigned.png
- screenshot SKU detail (164525233292, 52 SKUs): reports/fullval_5_sku_detail.png

## Business-rule blockers (not validation failures)

- B – YoY Recovery: 'recent sales improving' has no period/measure in Ebay.xlsx -> IDs with LY>0, 0<TY<LY shown as 'Undetermined: B or D'
- D – Lost Performer: tolerance of 'TY Sales ≈ 0' undefined -> applied only where TY Sales = 0
- SKU for 4,010 dashboard IDs with no Completed order and no listing_data SKU: not available in source data

Row-level mismatch list: reports/full_ph_row_mismatches.csv