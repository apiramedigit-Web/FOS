# Source-to-target mapping: Final OutPut Sales (21 columns)

Source databases (read-only): `order_management_copy` for orders, traffic and listing SKUs; **ledsone** `ebay_campaigns` for all advertising columns (since 2026-10-01). This mapping is taken from the code in `05_HTML_Report/Build/extract.py`, `rules.py` and `report_template.html`.
Period: LY = 1–30 Sep 2025, TY = 1–30 Sep 2026. Grain: one row per eBay ID (account × marketplace × item_id).

| # | Report column | Source table.column | Transformation | Missing / N/A rule |
|---|---|---|---|---|
| – | Account | order_transaction.ss_name / traffic_data.sub_source_name / ledsone order_management.sub_source.name (ads) | Sunsone=so_926407; Ledsone=led_sone (non-Germany); Electricalsone=electricalsone; Huttenlampen=huettenlampen; ledsone uk de=led_sone on the Germany marketplace (**not confirmed by the business**); ledsone de=ledsonede | – |
| 1 | eBay ID | order_transaction.item_id = traffic_data.ref_id = ledsone listing_performance.ebay_listing_id::text | Text, unchanged | – |
| 2 | SKU | order_transaction.sku (Completed, either September) | All SKUs listed | If no order: listing_data.sku (wrong_sku=0), tagged "from listing"; else "Not available in source data" |
| 3–4 | LY / TY Sales | Σ order_transaction.order_total | order_status='Completed', source_name='EBAY' | 0.00 when there are no Completed orders |
| 5 | YoY % | (TY − LY) ÷ LY × 100 | Full precision, shown to 2 dp | N/A when LY = 0 |
| 6–7 | LY / TY Ad Sales | Σ ledsone ebay_campaigns.listing_performance.sale_amount_listing_currency | strategy = campaigns.campaign_type (ON_SITE = Advanced, COST_PER_SALE = Standard); marketplace from campaigns.marketplace_id (EBAY_GB → UK, EBAY_DE → Germany, …) | Standard and Advanced shown separately, never summed; "Not advertised" when there is no ad row |
| 8–9 | LY / TY Views | Σ traffic_data.click (which_channel=2) | Listing views; Impressions (Σ traffic_data.impression) and CTR in the tooltip | "No data" when there is no traffic row |
| 10–11 | LY / TY Orders | COUNT(DISTINCT order_id) | Completed | 0 |
| 12–13 | LY / TY Conversion % | Orders ÷ Views × 100 | – | N/A when Views = 0; "No data" when there is no traffic row |
| 14–15 | LY / TY Price | Σ(item_price × quantity) ÷ Σ quantity | Quantity-weighted average selling price | N/A when units = 0 |
| 16–19 | Ad Impressions / Clicks / Spend / Sales | Σ ledsone listing_performance impressions / clicks / ad_fees_listing_currency / sale_amount_listing_currency | TY, per strategy | As for Ad Sales |
| 20 | ROAS/ACOS | Ad Sales ÷ Ad Spend / Ad Spend ÷ Ad Sales × 100 | TY, per strategy | ROAS N/A when Spend = 0; ACOS N/A when Ad Sales = 0 |
| 21 | Segment | Ebay.xlsx "Segmentation" sheet | A: TY > LY. C: LY Ad Sales > 0 and TY Ad Sales = 0 (per strategy). D: LY > 0 and TY = 0. TY < LY with TY > 0 → "Undetermined: B or D" | All matching labels shown; "No segment rule met" otherwise |

**"Not live in Sep 2025" (display label, not a business rule):** shown in LY Ad Sales, LY Views and LY Conversion % when the listing's earliest `listings.ebay_listings.created_at` (ledsone) is after 2025-09-30 **and** the ID has no LY order, traffic or ad row. Reliability check (2026-10-01): of 2,564 IDs created after Sep 2025, 0 have any LY data. 2,228 IDs are not in the listings table and keep the normal labels.

**Not in any source:** Competitor Price (Workflow 6), listing-quality data (Workflow 7), market demand (Workflow 8).

**Undefined in Ebay.xlsx (flagged, no threshold set):** "recent sales improving" (B), "TY Sales ≈ 0" for TY > 0 (D), "Flat" / "stable", Stable/New Ad Sales, Increase/Maintain/Reduce/Restart.
