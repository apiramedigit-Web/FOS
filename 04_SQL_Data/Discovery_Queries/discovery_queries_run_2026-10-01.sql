-- Read-only discovery queries executed 2026-10-01 against order_management_copy
-- (MCP postgres_2 = DATABASE_URL role temp_user). SELECT only; nothing was written.
-- Results are summarised in 02_Discovery/discovery_findings.md.

-- Q1. Completed/other order lines, items and sales per account x marketplace x year x status (Sep LY/TY)
SELECT current_database() db, ss_name, market_place,
  date_trunc('year', order_date)::date yr, order_status, count(*) lines, count(distinct item_id) items, sum(coalesce(order_total,0))::numeric(14,2) sales
FROM public.order_transaction
WHERE source_name='EBAY' AND ss_name IN ('so_926407','led_sone','electricalsone','huettenlampen','ledsonede')
  AND ((order_date>='2025-09-01' AND order_date<'2025-10-01') OR (order_date>='2026-09-01' AND order_date<'2026-10-01'))
GROUP BY 1,2,3,4,5 ORDER BY 2,3,4,5;

-- Q2. Traffic coverage (days, items, impressions, clicks=views, conversions) per account x marketplace x year
SELECT 'traffic' src, sub_source_name ss, market_place mp, extract(year from date)::int yr, count(distinct date) days, max(date) maxd,
       count(distinct ref_id) items, sum(impression) impr, sum(click) clk, sum(conversion) conv
FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN ('so_926407','led_sone','electricalsone','huettenlampen','ledsonede')
 AND ((date>='2025-09-01' AND date<'2025-10-01') OR (date>='2026-09-01' AND date<'2026-10-01'))
GROUP BY 1,2,3,4 ORDER BY 2,3,4;

-- Q3. eBay PPC coverage by account x marketplace x year x strategy x grain
SELECT pp.ss_name, pp.marketplace, extract(year from pp.date)::int yr, p.record_subtype, pp.record_type, count(*) n, count(distinct pp.date) days,
       max(pp.date) maxd, count(distinct pp.ref_id) refs, sum(pp.impressions) impr, sum(pp.clicks) clk, sum(pp.spend) spend, sum(pp.sales) sales
FROM public.ppc_performance pp JOIN public.ppc p ON p.parent_id=pp.parent_id AND p.record_main_type='campaign'
WHERE pp.source=2 AND ((pp.date>='2025-09-01' AND pp.date<'2025-10-01') OR (pp.date>='2026-09-01' AND pp.date<'2026-10-01'))
GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5;

-- Q4. analytics.ph_segment periods around September (eBay)
SELECT period_start, period_end, sub_source_name, market_place, count(*) n, count(distinct ref_id) refs
FROM analytics.ph_segment WHERE which_channel=2 AND period_start>='2025-08-15' AND (period_end<'2025-10-15' OR period_start>='2026-08-15')
GROUP BY 1,2,3,4 ORDER BY 1,3,4;

-- Q5. Column inventory of the source tables
SELECT table_schema, table_name, string_agg(column_name, ',' ORDER BY ordinal_position) cols
FROM information_schema.columns
WHERE (table_schema,table_name) IN (('public','order_transaction'),('public','traffic_data'),('analytics','ph_segment'))
GROUP BY 1,2;

-- Q6. Join integrity: TY order listings vs traffic on (account, marketplace, item_id)
WITH o AS (SELECT DISTINCT ss_name, market_place, item_id FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed'
           AND ss_name IN ('so_926407','led_sone','electricalsone','huettenlampen','ledsonede') AND order_date>='2026-09-01' AND order_date<'2026-10-01'),
t AS (SELECT DISTINCT sub_source_name ss, market_place, ref_id FROM public.traffic_data WHERE which_channel=2 AND date>='2026-09-01' AND date<'2026-10-01')
SELECT count(*) order_items,
 count(*) FILTER (WHERE item_id IS NULL OR item_id='') null_item,
 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM t WHERE t.ss=o.ss_name AND t.market_place=o.market_place AND t.ref_id=o.item_id)) exact_match,
 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM t WHERE t.ss=o.ss_name AND t.ref_id=o.item_id)) item_ss_match,
 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM t WHERE t.ss=o.ss_name AND t.ref_id=o.item_id AND t.market_place<>o.market_place)) other_mp_match,
 (SELECT count(*) FROM (SELECT ss_name,item_id FROM o GROUP BY 1,2 HAVING count(*)>1) x) items_multi_mp,
 (SELECT count(*) FROM (SELECT ss,ref_id FROM t GROUP BY 1,2 HAVING count(*)>1) x) traffic_items_multi_mp
FROM o;

-- Q7. Duplicate checks: campaign metadata, ad rows, traffic rows
SELECT (SELECT count(*) FROM (SELECT parent_id FROM public.ppc WHERE record_main_type='campaign' GROUP BY 1 HAVING count(*)>1 AND count(distinct record_subtype)>1) a) campaign_parent_conflicting_subtype,
(SELECT count(*) FROM (SELECT parent_id FROM public.ppc WHERE record_main_type='campaign' GROUP BY 1 HAVING count(*)>1) a) campaign_parent_dupes,
(SELECT count(*) FROM (SELECT date,ref_id,ss_name,marketplace,record_id,parent_id,child_id FROM public.ppc_performance WHERE source=2 AND record_type='ad' AND date>='2026-09-01' GROUP BY 1,2,3,4,5,6,7 HAVING count(*)>1) b) ad_row_dupes_ty,
(SELECT count(*) FROM (SELECT date,ref_id,ss_name,marketplace,record_id,parent_id,child_id FROM public.ppc_performance WHERE source=2 AND record_type='ad' AND date>='2025-09-01' AND date<'2025-10-01' GROUP BY 1,2,3,4,5,6,7 HAVING count(*)>1) b) ad_row_dupes_ly,
(SELECT count(*) FROM (SELECT date,ref_id,sub_source_name,market_place FROM public.traffic_data WHERE which_channel=2 AND ((date>='2026-09-01' AND date<'2026-10-01') OR (date>='2025-09-01' AND date<'2025-10-01')) GROUP BY 1,2,3,4 HAVING count(*)>1) c) traffic_dupes;

-- Q8. Evidence that the ad feed writes all-zero rows for idle advertised listings (basis for "no ad row = not advertised")
SELECT extract(year from date)::int yr, count(*) ad_rows, count(*) FILTER (WHERE impressions=0 AND clicks=0 AND spend=0 AND sales=0) all_zero_rows, count(distinct date) days
FROM public.ppc_performance WHERE source=2 AND record_type='ad' AND ss_name IN ('so_926407','led_sone','electricalsone','huettenlampen','ledsonede')
AND ((date>='2025-09-01' AND date<'2025-10-01') OR (date>='2026-09-01' AND date<'2026-10-01')) GROUP BY 1;

-- Q9. listing_data column inventory (SKU source for IDs with no Completed order)
SELECT table_schema, table_name, string_agg(column_name||':'||data_type, ', ' ORDER BY ordinal_position) cols
FROM information_schema.columns WHERE table_name IN ('listing_data') GROUP BY 1,2;

-- Q10. listing_data SKU coverage for IDs with no Completed order (run from a Python check via DATABASE_URL).
-- Result: 8,461 of 12,470 no-order IDs have a clean (wrong_sku=0) SKU; 156 only have wrong_sku<>0 SKUs.
-- Cross-check on IDs that also have order SKUs: 1,701 full agree, 102 partial, 528 no overlap, 89 not in listing_data.
SELECT sub_source_name, market_place, ref_id, sku, wrong_sku, is_parent, is_child, is_ended, is_deleted
FROM public.listing_data
WHERE which_channel=2 AND sub_source_name IN ('so_926407','led_sone','electricalsone','huettenlampen','ledsonede');
