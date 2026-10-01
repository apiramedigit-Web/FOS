# Closure note: eBay Final Output Sales, September 2026

Date: 2026-10-01

## Deliverable
`05_HTML_Report/Current/eBay_Final_Output_Sales_September_2026.html`: a standalone page that opens in Chrome with all data embedded. It contains the Final OutPut Sales table (21 columns), the Account and Segment filters, eBay ID / SKU search, and pagination.

## Validation status: PASS (data and HTML)
| Check | Result | Evidence |
|---|---|---|
| Source SQL vs extracted dataset | 358/358 | `06_Evidence/Reconciliation_Evidence/reconciliation.json` |
| Source vs dataset vs HTML, 30 fields (ads vs ledsone) | All match | `06_Evidence/Advertising_Evidence/field_audit_v8_ads_ledsone.json` |
| Browser validation (all 14,902 rows, filters, search, no broken values) | 21/21 | `07_Validation/html_validation.json` |
| Advertising regression from v3 (dropped IDs) | Fixed | `field_audit_v3_before_repair.json` vs `field_audit_v5_final.json` |

## Segment counts (all 14,902 eBay IDs)
A – YoY Winner 1,146 · C – Lost Ad Sales (Standard) 1,029 · C – Lost Ad Sales (Advanced) 329 · D – Lost Performer 937 · Undetermined: B or D 330 · No segment rule met 12,464.

**Ad-source fix (2026-10-01):** ads now come from ledsone `ebay_campaigns.listing_performance`. `ppc_performance` was incomplete for TY, which caused 65 false "C – Lost Ad Sales" labels. Audit: `06_Evidence/Advertising_Evidence/field_audit_v8_ads_ledsone.json`. Per-ID detail: `06_Evidence/Segment_Evidence/`.

## Open items (need a business decision; nothing was assumed)
1. Ebay.xlsx terms with no definition: "recent sales improving" (B), "TY Sales ≈ 0" for TY > 0 (D), "Flat" / "stable", Stable/New Ad Sales, Increase/Maintain/Reduce/Restart.
2. Confirm that "ledsone uk de" = led_sone on the Germany marketplace.
3. Confirm that listing_data SKUs are acceptable for IDs with no orders (it disagrees with order SKUs where both exist).
4. Traffic gaps: TY covers to 29 Sep; LY so_926407 has 16/30 days; huettenlampen and ledsonede have 26/30.
5. Not available in any source: Competitor Price, listing-quality data, market demand (Workflow steps 6–8).

## Handover
The working pipeline is in `C:\Users\LED 222\eBay_Final_Output_Sales`. Rebuild order: `03_SQL/extract.py` → `06_Validation/validate_data.py` → `04_HTML_Report/build.py` → `06_Validation/validate_html.py`. Copy the refreshed outputs into this folder afterwards and update `08_Documentation/asset_manifest.txt`.

## Next step
The business answers the open items. Then rebuild and re-validate.
