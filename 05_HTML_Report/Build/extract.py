"""eBay Final Output Sales - September LY vs TY extract.

Sources (read-only SELECTs):
  * order_management_copy (DATABASE_URL): orders, traffic, listing SKUs, ph_segment
  * ledsone (WLP_SOURCE_DB_URL): eBay ads, ebay_campaigns.listing_performance
Writes 04_HTML_Report/data/dataset.json.

Business rules (documented in 05_Evidence/02_data_mapping.md):
  * LY = 2025-09-01..2025-09-30, TY = 2026-09-01..2026-09-30
  * Sales = SUM(order_total), Completed orders only (skill TABLE_order_transaction)
  * Orders = COUNT(DISTINCT order_id); Price = SUM(item_price*qty)/SUM(qty)
  * Views = traffic_data.click (eBay listing views), listing grain
  * Ads = ledsone ebay_campaigns.listing_performance (listing grain), split by campaign_type;
    ON_SITE (Advanced) and COST_PER_SALE (Standard) are never summed
  * Segment = analytics.ph_segment, latest TY period inside September
"""
import json, os, datetime
from collections import defaultdict
import psycopg2

PERIODS = {"LY": ("2025-09-01", "2025-10-01"), "TY": ("2026-09-01", "2026-10-01")}
SS = ("so_926407", "led_sone", "electricalsone", "huettenlampen", "ledsonede")
SEG_PERIOD = ("2026-09-15", "2026-09-28")
OUT = os.path.join(os.path.dirname(__file__), "..", "04_HTML_Report", "data", "dataset.json")


def account(ss, mp):
    # Requirement account labels -> data keys (ebay-account-name-to-subsource)
    if ss == "so_926407": return "Sunsone"
    if ss == "led_sone": return "ledsone uk de" if mp == "Germany" else "Ledsone"
    if ss == "electricalsone": return "Electricalsone"
    if ss == "huettenlampen": return "Huttenlampen"
    if ss == "ledsonede": return "ledsone de"
    raise ValueError(ss)


PERIOD_CASE = """CASE WHEN {c} >= '2025-09-01' AND {c} < '2025-10-01' THEN 'LY'
                      WHEN {c} >= '2026-09-01' AND {c} < '2026-10-01' THEN 'TY' END"""
PERIOD_WHERE = """(({c} >= '2025-09-01' AND {c} < '2025-10-01') OR ({c} >= '2026-09-01' AND {c} < '2026-10-01'))"""

SQL_ORDERS = f"""
SELECT ss_name, market_place, item_id, sku, {PERIOD_CASE.format(c='order_date')} AS per,
       SUM(COALESCE(order_total,0))::float AS sales,
       COUNT(DISTINCT order_id) AS orders,
       SUM(COALESCE(quantity,0))::float AS units,
       SUM(COALESCE(item_price,0)*COALESCE(quantity,0))::float AS price_x_qty
FROM public.order_transaction
WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s
  AND {PERIOD_WHERE.format(c='order_date')}
GROUP BY 1,2,3,4,5"""

# listing-level distinct orders (an order holding two SKUs of one listing counts once)
SQL_LISTING_ORDERS = f"""
SELECT ss_name, market_place, item_id, {PERIOD_CASE.format(c='order_date')} AS per,
       COUNT(DISTINCT order_id) AS orders
FROM public.order_transaction
WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s
  AND {PERIOD_WHERE.format(c='order_date')}
GROUP BY 1,2,3,4"""

SQL_TRAFFIC = f"""
SELECT sub_source_name, market_place, ref_id, {PERIOD_CASE.format(c='date')} AS per,
       SUM(COALESCE(click,0))::float AS views, SUM(COALESCE(impression,0))::float AS impr, COUNT(DISTINCT date) AS days
FROM public.traffic_data
WHERE which_channel=2 AND sub_source_name IN %(ss)s AND {PERIOD_WHERE.format(c='date')}
GROUP BY 1,2,3,4"""

# Ads come from the LEDSONE warehouse (eBay PPC source of truth), not order_management_copy:
# public.ppc_performance was found incomplete for TY at listing grain (2026-10-01, e.g. eBay ID
# 315379102143 had no TY rows there but 26 days of Advanced ads in ledsone). Listing currency
# amounts match the marketplace currency used for Sales. Key (campaign, listing, ad_group, date)
# has no duplicates. Marketplace codes mapped to the order/traffic vocabulary.
MP_CASE = """CASE c.marketplace_id WHEN 'EBAY_GB' THEN 'UK' WHEN 'EBAY_DE' THEN 'Germany' WHEN 'EBAY_FR' THEN 'France'
              WHEN 'EBAY_US' THEN 'US' WHEN 'EBAY_IT' THEN 'Italy' WHEN 'EBAY_ES' THEN 'Spain' WHEN 'EBAY_CA' THEN 'Canada'
              ELSE c.marketplace_id END"""
SQL_ADS = f"""
SELECT s.name AS ss_name, {MP_CASE} AS marketplace, lp.ebay_listing_id::text, {PERIOD_CASE.format(c='lp.date')} AS per,
       c.campaign_type AS strat,
       SUM(lp.impressions)::float, SUM(lp.clicks)::float, SUM(lp.ad_fees_listing_currency)::float,
       SUM(lp.sale_amount_listing_currency)::float, SUM(lp.attributed_sales)::float
FROM ebay_campaigns.listing_performance lp
JOIN ebay_campaigns.campaigns c ON c.campaign_id = lp.campaign_id
JOIN order_management.sub_source s ON s.id = c.sub_source
WHERE s.name IN %(ss)s AND c.campaign_type IN ('ON_SITE','COST_PER_SALE')
  AND {PERIOD_WHERE.format(c='lp.date')}
GROUP BY 1,2,3,4,5"""

# [ledsone] earliest created_at of each eBay listing (all rows: parent, variations, sites)
SQL_CREATED = """
SELECT item_id::text, MIN(created_at)::date::text
FROM listings.ebay_listings
WHERE item_id::text = ANY(%(ids)s)
GROUP BY 1"""

SQL_SEGMENT = """
SELECT sub_source_name, market_place, ref_id,
       string_agg(DISTINCT performance_segment_name, ' / ' ORDER BY performance_segment_name)
FROM analytics.ph_segment
WHERE which_channel=2 AND sub_source_name IN %(ss)s AND period_start=%(s)s AND period_end=%(e)s
GROUP BY 1,2,3"""

# Listing SKUs: used ONLY for eBay IDs with no Completed order in either September
# (order data has no SKU for them). wrong_sku=0 only; sku column, not mapped_sku (bundle).
SQL_LISTING_SKU = """
SELECT sub_source_name, market_place, ref_id, array_agg(DISTINCT btrim(sku) ORDER BY btrim(sku))
FROM public.listing_data
WHERE which_channel=2 AND sub_source_name IN %(ss)s AND wrong_sku=0 AND sku IS NOT NULL AND btrim(sku)<>''
GROUP BY 1,2,3"""

SQL_DAYS = f"""
SELECT 'traffic', sub_source_name, market_place, {PERIOD_CASE.format(c='date')}, COUNT(DISTINCT date), MIN(date)::text, MAX(date)::text
FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN %(ss)s AND {PERIOD_WHERE.format(c='date')} GROUP BY 1,2,3,4
UNION ALL
SELECT 'orders', ss_name, market_place, {PERIOD_CASE.format(c='order_date')}, COUNT(DISTINCT order_date::date), MIN(order_date)::date::text, MAX(order_date)::date::text
FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s AND {PERIOD_WHERE.format(c='order_date')} GROUP BY 1,2,3,4"""

SQL_STATUS = f"""
SELECT ss_name, market_place, {PERIOD_CASE.format(c='order_date')}, order_status,
       COUNT(*), SUM(COALESCE(order_total,0))::float
FROM public.order_transaction WHERE source_name='EBAY' AND ss_name IN %(ss)s AND {PERIOD_WHERE.format(c='order_date')}
GROUP BY 1,2,3,4"""


def ledsone_dsn():
    """Ledsone warehouse DSN: WLP_SOURCE_DB_URL env var, else the HKCU user env (never printed)."""
    dsn = os.environ.get("WLP_SOURCE_DB_URL")
    if not dsn:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            dsn = winreg.QueryValueEx(k, "WLP_SOURCE_DB_URL")[0]
    return dsn


def q(cur, sql, **kw):
    cur.execute(sql, {"ss": SS, **kw})
    return cur.fetchall()


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    con = psycopg2.connect(dsn)
    con.set_session(readonly=True)
    cur = con.cursor()
    cur.execute("select current_database()")
    assert cur.fetchone()[0] == "order_management_copy"

    orders = q(cur, SQL_ORDERS)
    lorders = q(cur, SQL_LISTING_ORDERS)
    traffic = q(cur, SQL_TRAFFIC)
    seg = q(cur, SQL_SEGMENT, s=SEG_PERIOD[0], e=SEG_PERIOD[1])
    days = q(cur, SQL_DAYS)
    status = q(cur, SQL_STATUS)
    lsku = {f"{ss}|{mp}|{ref}": skus for ss, mp, ref, skus in q(cur, SQL_LISTING_SKU)}
    con.close()

    # ads: ledsone warehouse (WLP_SOURCE_DB_URL, read-only)
    lcon = psycopg2.connect(ledsone_dsn())
    lcon.set_session(readonly=True)
    lcur = lcon.cursor()
    lcur.execute("select current_database()")
    assert lcur.fetchone()[0] == "ledsone"
    ads = q(lcur, SQL_ADS)
    # first time each listing appears in the listing table (used only to label LY cells of
    # listings that were not live in Sep 2025; see "created" below)
    all_ids = sorted({r[2] for r in lorders} | {r[2] for r in traffic} | {r[2] for r in ads} | {r[2] for r in orders})
    lcur.execute(SQL_CREATED, {"ids": all_ids})
    created = dict(lcur.fetchall())
    lcon.close()

    # ---- listing-level facts -------------------------------------------------
    L = defaultdict(lambda: {"views": {}, "impr": {}, "orders": {}, "ads": {}, "segment": None})
    for ss, mp, item, per, o in lorders:
        L[(ss, mp, item)]["orders"][per] = o
    for ss, mp, item, per, v, im, _d in traffic:
        L[(ss, mp, item)]["views"][per] = v
        L[(ss, mp, item)]["impr"][per] = im
    for ss, mp, item, per, strat, im, cl, sp, sa, od in ads:
        L[(ss, mp, item)]["ads"].setdefault(strat, {})[per] = [im, cl, sp, sa, od]
    for ss, mp, item, name in seg:
        if (ss, mp, item) in L:
            L[(ss, mp, item)]["segment"] = name
    seg_lookup = {(ss, mp, item): name for ss, mp, item, name in seg}

    # ---- SKU rows --------------------------------------------------------------
    R = {}
    for ss, mp, item, sku, per, sales, o, units, pxq in orders:
        r = R.setdefault((ss, mp, item, sku), {})
        r[per] = [sales, o, units, pxq]
    sku_count = defaultdict(int)
    for (ss, mp, item, sku) in R:
        sku_count[(ss, mp, item)] += 1
    # listings with traffic/ads but no Completed order in either September
    for key in L:
        if sku_count.get(key, 0) == 0:
            R[key + (None,)] = {}

    rows = []
    for (ss, mp, item, sku), r in R.items():
        lk = (ss, mp, item)
        lf = L.get(lk) or {"views": {}, "impr": {}, "orders": {}, "ads": {}, "segment": None}
        row = {
            "acct": account(ss, mp), "ss": ss, "mp": mp, "id": item, "sku": sku,
            "shared": sku_count.get(lk, 0) > 1, "nsku": sku_count.get(lk, 0),
            "seg": lf["segment"] or seg_lookup.get(lk),
            "lsku": lsku.get(f"{ss}|{mp}|{item}") if sku_count.get(lk, 0) == 0 else None,
            "created": created.get(item),                  # YYYY-MM-DD or None (not in listings table)
        }
        for per in ("LY", "TY"):
            s, o, u, pxq = r.get(per, [0.0, 0, 0.0, 0.0])
            row[per] = {"sales": s, "orders": o, "units": u, "pxq": pxq,
                        "views": lf["views"].get(per),       # None = no traffic row
                        "impr": lf["impr"].get(per),
                        "lorders": lf["orders"].get(per, 0)}
        row["ads"] = {strat: {per: v for per, v in d.items()} for strat, d in lf["ads"].items()}
        rows.append(row)

    rows.sort(key=lambda x: (x["acct"], x["mp"], -x["TY"]["sales"], x["id"], x["sku"] or ""))

    meta = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_db": "order_management_copy + ledsone (ads, listing created date)",
        "periods": PERIODS, "segment_period": SEG_PERIOD,
        "coverage": [list(x) for x in days],
        "status_mix": [list(x) for x in status],
        "src_counts": {"order_rows": len(orders), "listing_order_rows": len(lorders),
                       "traffic_rows": len(traffic), "ad_rows": len(ads), "segment_rows": len(seg)},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": rows}, f, separators=(",", ":"), default=str)
    print(f"rows={len(rows)} listings={len(L)} skus_rows={sum(1 for k in R if k[3] is not None)} -> {os.path.abspath(OUT)}")
    print(meta["src_counts"])


if __name__ == "__main__":
    main()
