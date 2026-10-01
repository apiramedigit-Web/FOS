# Discovery findings: eBay Final Output Sales, September 2026

Recorded 2026-10-01. Every statement below comes from a check run on that date. The queries are in `04_SQL_Data/Discovery_Queries/discovery_queries_run_2026-10-01.sql`.

## 1. Requirement files
| File | Finding |
|---|---|
| `Ebay - Final OutPut Sales.csv` | 30 lines × 21 columns. It is a layout template: placeholder `xxxx` rows under 6 account labels plus filters Account and Segment. It contains no data values. It is a CSV export of the last sheet of Ebay.xlsx. |
| `Ebay.xlsx` | 4 sheets: Workflow (9 steps), Segmentation (A–D), Root Cause Matrix (7 causes), Final OutPut Sales. Workflow note: "Data on september". This workbook is the requirement source of truth. |

## 2. Existing-asset search
- No earlier "Final Output Sales" asset existed in the home folder or Downloads.
- Related assets reused for method only (no code copied): the ELD multi-account model (account keys, no FX mixing) and the eBay PPC ACOS Monitor (Advanced campaign spend partly unattributed to listings).
- One working project exists: `C:\Users\LED 222\eBay_Final_Output_Sales`. It is where the pipeline runs. This folder holds copies; see `08_Documentation/START-HERE.md`.
- Previous dashboard: only the v3 build (`05_HTML_Report/Previous/`) was archived. Earlier v1 and v2 builds were overwritten in place by `build.py`.

## 3. Data sources (read-only; order_management_copy unless stated)
| Data | Table | Finding |
|---|---|---|
| Sales, orders, SKU, price | `public.order_transaction` | Present for both Septembers. TY non-Completed lines: New 124, Hold 2. They are excluded by the skill rule "Completed only". |
| Views, impressions | `public.traffic_data` (which_channel=2) | TY covers 1–29 Sep (30 Sep not loaded). LY gaps: so_926407 has 16/30 days; huettenlampen and ledsonede have 26/30. |
| Advertising | **ledsone** `ebay_campaigns.listing_performance` ⋈ `campaigns` ⋈ `order_management.sub_source` | Used since 2026-10-01. `order_management_copy.public.ppc_performance` was found **incomplete for TY** at listing grain: e.g. eBay ID 315379102143 had no TY rows there, but 26 days of Advanced ads in ledsone; Sunsone UK TY Advanced spend was 203.55 there vs 958.60 in ledsone. LY is almost identical in both. ledsone key has 0 duplicates and all 30 days in both years. |
| Segment (ph_segment) | `analytics.ph_segment` | Present, but not used: Ebay.xlsx defines Segment as rules A–D. |
| SKU for no-order IDs | `public.listing_data` | Covers 8,461 of the 12,470 IDs that have no Completed order. Where an ID has both order and listing SKUs, they disagree for 528 IDs, so listing SKUs are shown tagged "from listing". |

## 4. Integrity checks
- **Join:** 1,482 of 1,483 TY order listings match traffic on (account, marketplace, item_id).
- **Duplicates:** 0 duplicate campaign metadata rows, 0 duplicate ad rows, 0 duplicate traffic rows.
- **Ad feed (ppc_performance, original check):** it writes all-zero rows for idle advertised listings (TY 27,227 rows; all 30 days in both years), so "no ad row" means not advertised.
- **Scope:** 14,902 eBay IDs (14,890 before the ad-source fix; 12 more listings have ads only in ledsone) (account × marketplace × item). 2,420 have a Completed sale in either September.

## 5. Duplicate-truth risks
- This folder holds **copies**. The pipeline (extract → validate → build → validate) runs in `C:\Users\LED 222\eBay_Final_Output_Sales`. Rebuilding there does not update these copies. Checksums are in `08_Documentation/asset_manifest.txt`.
- Listing SKUs vs order SKUs: two SKU sources. Order SKU always takes priority; listing SKUs are tagged and used only when there is no order.

## 6. Ad-source correction (2026-10-01)
The user compared eBay ID 315379102143 against the DB and saw TY Advanced ads that the dashboard showed as "Not advertised". Root cause: the dashboard read ads from `ppc_performance`, which is incomplete for TY. Ads now come from ledsone `ebay_campaigns.listing_performance` (listing currency). Effect: 79 IDs changed segment (53 false "C – Lost Ad Sales (Advanced)" and 12 false "C – Lost Ad Sales (Standard)" removed; 2 genuine Standard added), and 12 ad-only IDs were added. The pre-fix HTML is in `05_HTML_Report/Previous/*__v7_ads_from_ppc_performance.html`.
