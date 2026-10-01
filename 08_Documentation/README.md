# eBay Final Output Sales: September Comparison (evidence)

Built 2026-10-01. Deliverable: `04_HTML_Report/eBay_Final_Output_Sales_September_2026.html` (standalone, 4.05 MB, all data embedded).

## 1. Requirement (source of truth = `01_Requirements/Ebay.xlsx`)
- Sheets: **Workflow** (9 steps), **Segmentation** (A–D), **Root Cause Matrix** (7 causes), **Final OutPut Sales** (21-column template, filters Account and Segment). Workflow note: "Data on september".
- `Ebay - Final OutPut Sales.csv` is a CSV export of the last sheet only (same 21 columns, `xxxx` placeholder rows, no values).
- Period: LY = 1–30 Sep 2025, TY = 1–30 Sep 2026. Grain = **eBay ID** (Workflow: "Identify relevant eBay IDs", "Segment IDs").
- Undefined workbook expressions are flagged, never thresholded (user instruction 2026-10-01). The full rule register is in `04_HTML_Report/rules.py` (`RULES`) and in the report.

## 2. Existing-asset check
No existing "Final Output Sales" asset. Related assets were reused for method, not code: ELD multi-account model (account keys, no FX mixing), eBay PPC ACOS Monitor (Advanced campaign spend partly unattributed to listings), skill pack `TABLE_order_transaction / traffic_data / ppc / ph_segment` (revenue = Σ order_total on Completed; never sum ON_SITE + COST_PER_SALE). `analytics.ph_segment` was not used: the workbook defines Segment as A–D.

## 3. Sources (order_management_copy, read-only via DATABASE_URL)
| Field | Source |
|---|---|
| Sales, Orders, SKU, Price | `public.order_transaction` (source_name='EBAY', order_status='Completed') |
| Views, Impressions, CTR | `public.traffic_data` (which_channel=2; click = views) |
| Ad Impr/Clicks/Spend/Sales | `public.ppc_performance` record_type='ad' ⋈ `public.ppc` (campaign subtype) |
Account keys: Sunsone=so_926407, Ledsone=led_sone non-Germany, Electricalsone=electricalsone, Huttenlampen=huettenlampen, ledsone uk de=led_sone Germany marketplace, ledsone de=ledsonede.

## 4. Data quality (see `03_data_quality_profile.txt`)
- Joins: 1,482/1,483 TY order listings match traffic on (account, marketplace, item_id). 0 duplicate traffic, ad or campaign rows. 0 blank SKUs or eBay IDs.
- Traffic gaps: TY covers 1–29 Sep. LY: so_926407 16/30 days; huettenlampen and ledsonede 26/30; others 28/30.
- TY non-Completed lines (New 124, Hold 2) are excluded by the Completed rule.
- The PPC feed writes all-zero rows for idle ads (TY 27,227), so no ad row = not advertised = 0 ad sales (used for rule C).
- No currency column: values are per marketplace and never summed across marketplaces.
- Not in the source data: competitor price, listing-quality data, market demand.

## 5. Validation
- `06_Validation/validate_data.py`: **350/350** independent SQL-vs-report checks (Sales, listing Orders, Views, Organic Impressions, Ad Impr/Clicks/Spend/Sales per account × marketplace × period × strategy). Report: `06_Validation/reports/reconciliation.json`.
- `06_Validation/validate_html.py` (Playwright): **21/21**. Covers: exactly the 21 columns in workbook order; only the Account and Segment filters; every one of the 2,448 rows recomputed independently (all 21 columns, both ad strategies); page Sales totals = source SQL per account × marketplace × period; page Orders = source; every Segment and Account option; no JS errors/NaN; no horizontal scroll at 390 px. Report + screenshots in `06_Validation/reports/`.
- **Page scope (user, 2026-10-01): only the requirement output is shown.** That is the Final OutPut Sales table with the Account and Segment filters. Rows are the 2,448 eBay IDs with non-zero Step 1 Key Data. Account header rows are split by marketplace (currency). Ad columns show Standard and Advanced as separate lines, never summed. KPIs, summaries, workflow analysis, the rule register and data-quality notes are kept here and in `04_HTML_Report/rules.py` only, not on the page.

## 6. Rebuild
```
python 03_SQL/extract.py
python 06_Validation/validate_data.py      # must pass; build refuses on failures
python 04_HTML_Report/build.py
python 06_Validation/validate_html.py
```

## 7. Repair v4 (2026-10-01): data restoration + table UI
- **Root cause (v3 regression):** `build.py` embedded only the 2,448 IDs with non-zero Sales/Ad Sales. It dropped 12,442 IDs that carry real ad and traffic data: TY Ad Impressions Std 7,676,572 / Adv 2,485,418; TY Ad Clicks Std 6,362 / Adv 3,318; Ad Spend Adv TY 579.45, LY 1,324.12, Std LY 5.21; TY Views 47,452; advertised IDs Std TY 5,317. Impressions were never embedded. Sales, Orders and Ad Sales were unaffected (every dropped ID has 0 in all three). Evidence: `06_Validation/reports/field_audit_v3_before_repair.json`.
- **Fix:** all 14,890 IDs and LY/TY Impressions are embedded. `field_audit_v4_repaired.json` shows source = dataset = HTML on all 30 audited fields.
- **UI:** the 7 ad columns are split into Std | Adv sub-columns under a two-row sticky header. Account · Marketplace subtotal rows. eBay ID/SKU pinned. One-line SKU (+n, full list in tooltip). Impressions/CTR in the Views tooltip. Segment codes (A, C·Std, C·Adv, D, B/D?, None) with full workbook names in tooltip, legend and filter; every matching segment is kept. 100 rows/page. Default = all accounts, all segments. Fits 1920 px with no horizontal scroll; below that, the table scrolls inside its box with ID/SKU pinned.
- **Validation:** `validate_html.py` 21/21. Covers every row on every page (14,890 × 28 cells), all 21 subtotal rows reconciled to source SQL, all filter options and combinations, no broken values, and no JS errors.
- **Not filled:** SKU for 12,470 IDs with no Completed order. `public.listing_data` covers 8,461 of them, but it disagrees with order-data SKUs on IDs that have both (528 with no overlap), so it was not mixed in. Business decision pending.
- Baseline copies: `07_Archive/*__v3_before_repair.*`.

## 8. v5 (2026-10-01, user feedback): earlier layout restored + data for all IDs
- The user preferred the earlier (v3) look. The table is back to 21 columns with stacked Std/Adv lines per ad cell. A single "Not advertised" replaces paired dashes when neither ad type has a row. Account · Marketplace header rows show the ID count and LY/TY Sales.
- SKU for no-order IDs now comes from `public.listing_data` (wrong_sku=0, `sku` column) and is tagged "from listing": 8,461 of the 12,470. 4,009 IDs have no SKU in any source.
- One truncated line per SKU (max 2, "+N more", full list in tooltip). IDs sorted by sales, then views. Fits 1920 px.
- Why most rows show 0.00 / N/A: only 2,420 of the 14,890 IDs had a Completed order in either September. The rest are real views/ads-only listings.
- Validation: `validate_html.py` 19/19 (every row on every page; Σ rows = source SQL for every account × marketplace and metric). `field_audit_v5_final.json` all OK.
