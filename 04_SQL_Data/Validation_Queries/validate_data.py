"""Independent reconciliation: direct source SQL totals vs dataset.json aggregates.

Writes 06_Validation/reports/reconciliation.json (embedded in the HTML by build.py)
and exits 1 if any check fails.
"""
import json, os, sys
from collections import defaultdict
import psycopg2

BASE = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(BASE, "04_HTML_Report", "data", "dataset.json")
OUT = os.path.join(BASE, "06_Validation", "reports", "reconciliation.json")
SS = ("so_926407", "led_sone", "electricalsone", "huettenlampen", "ledsonede")
PER = "CASE WHEN {c} < '2026-01-01' THEN 'LY' ELSE 'TY' END"
WIN = "(({c} >= '2025-09-01' AND {c} < '2025-10-01') OR ({c} >= '2026-09-01' AND {c} < '2026-10-01'))"

SQL = {
    "sales": f"""SELECT ss_name, market_place, {PER.format(c='order_date')}, SUM(COALESCE(order_total,0))::float,
                 COUNT(DISTINCT order_id), COUNT(DISTINCT (item_id, order_id)), COUNT(DISTINCT item_id), COUNT(DISTINCT sku),
                 COUNT(*) FILTER (WHERE sku IS NULL OR btrim(sku)=''), COUNT(*)
               FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed'
                 AND ss_name IN %(ss)s AND {WIN.format(c='order_date')} GROUP BY 1,2,3""",
    "views": f"""SELECT sub_source_name, market_place, {PER.format(c='date')}, SUM(COALESCE(click,0))::float, COUNT(DISTINCT ref_id), SUM(COALESCE(impression,0))::float
               FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN %(ss)s
                 AND {WIN.format(c='date')} GROUP BY 1,2,3""",
}
# ads live in the ledsone warehouse (ebay_campaigns); marketplace mapped in Python, independently of extract.py
SQL_ADS_LEDSONE = f"""SELECT s.name, c.marketplace_id, {PER.format(c='lp.date')}, c.campaign_type,
                 SUM(lp.impressions)::float, SUM(lp.clicks)::float, SUM(lp.ad_fees_listing_currency)::float, SUM(lp.sale_amount_listing_currency)::float
               FROM ebay_campaigns.listing_performance lp
               JOIN ebay_campaigns.campaigns c ON c.campaign_id=lp.campaign_id
               JOIN order_management.sub_source s ON s.id=c.sub_source
               WHERE s.name IN %(ss)s AND c.campaign_type IN ('ON_SITE','COST_PER_SALE') AND {WIN.format(c='lp.date')}
               GROUP BY 1,2,3,4"""
MP = {"EBAY_GB": "UK", "EBAY_DE": "Germany", "EBAY_FR": "France", "EBAY_US": "US", "EBAY_IT": "Italy", "EBAY_ES": "Spain", "EBAY_CA": "Canada"}


def ledsone_dsn():
    dsn = os.environ.get("WLP_SOURCE_DB_URL")
    if not dsn:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            dsn = winreg.QueryValueEx(k, "WLP_SOURCE_DB_URL")[0]
    return dsn


def main():
    con = psycopg2.connect(os.environ["DATABASE_URL"]); con.set_session(readonly=True)
    cur = con.cursor()
    src = {}
    for k, s in SQL.items():
        cur.execute(s, {"ss": SS}); src[k] = cur.fetchall()
    con.close()
    lcon = psycopg2.connect(ledsone_dsn()); lcon.set_session(readonly=True)
    lcur = lcon.cursor(); lcur.execute(SQL_ADS_LEDSONE, {"ss": SS})
    src["ads"] = [(ss, MP[mp], p, st, *v) for ss, mp, p, st, *v in lcur.fetchall()]
    lcon.close()

    d = json.load(open(DATA, encoding="utf-8"))
    rows = d["rows"]

    # dataset aggregates
    ds_sales = defaultdict(float); ds_lorders = defaultdict(int); ds_rows = defaultdict(int)
    ds_views = defaultdict(float); ds_impr = defaultdict(float); ds_ads = defaultdict(lambda: [0.0] * 4)
    seen = set()
    for r in rows:
        for p in ("LY", "TY"):
            ds_sales[(r["ss"], r["mp"], p)] += r[p]["sales"]
            if r[p]["orders"]: ds_rows[(r["ss"], r["mp"], p)] += 1
        lk = (r["ss"], r["mp"], r["id"])
        if lk in seen: continue          # listing-level metrics counted once
        seen.add(lk)
        for p in ("LY", "TY"):
            ds_lorders[(r["ss"], r["mp"], p)] += r[p]["lorders"]
            if r[p]["views"] is not None: ds_views[(r["ss"], r["mp"], p)] += r[p]["views"]
            if r[p].get("impr") is not None: ds_impr[(r["ss"], r["mp"], p)] += r[p]["impr"]
        for strat, per in r["ads"].items():
            for p, v in per.items():
                a = ds_ads[(r["ss"], r["mp"], p, strat)]
                for i in range(4): a[i] += v[i]

    checks = []
    def chk(name, key, s, t, tol=0.01):
        ok = abs((s or 0) - (t or 0)) <= tol
        checks.append({"check": name, "key": " | ".join(map(str, key)), "source": round(s, 2), "report": round(t, 2), "ok": ok})

    info = []
    for ss, mp, p, sales, n_orders, n_item_orders, n_items, n_skus, blank_sku, n_lines in src["sales"]:
        k = (ss, mp, p)
        chk("Sales", k, sales, ds_sales.get(k, 0))
        chk("Orders (listing-level)", k, n_item_orders, ds_lorders.get(k, 0), 0)
        info.append({"key": " | ".join(k), "distinct_orders": n_orders, "listing_orders": n_item_orders,
                     "listings": n_items, "skus": n_skus, "blank_sku_lines": blank_sku, "lines": n_lines})
    for ss, mp, p, views, n, impr in src["views"]:
        chk("Views", (ss, mp, p), views, ds_views.get((ss, mp, p), 0))
        chk("Organic Impressions", (ss, mp, p), impr, ds_impr.get((ss, mp, p), 0))
    for ss, mp, p, strat, im, cl, sp, sa in src["ads"]:
        a = ds_ads.get((ss, mp, p, strat), [0] * 4)
        for i, (nm, v) in enumerate([("Ad Impressions", im), ("Ad Clicks", cl), ("Ad Spend", sp), ("Ad Sales", sa)]):
            chk(nm, (ss, mp, p, strat), v, a[i])

    # grand totals per period (count-type metrics only; money is never summed across marketplaces)
    totals = {}
    for p in ("LY", "TY"):
        totals[p] = {
            "source_listing_orders": sum(x[5] for x in src["sales"] if x[2] == p),
            "report_listing_orders": sum(v for k, v in ds_lorders.items() if k[2] == p),
            "source_distinct_orders": sum(x[4] for x in src["sales"] if x[2] == p),
            "source_views": sum(x[3] for x in src["views"] if x[2] == p),
            "report_views": sum(v for k, v in ds_views.items() if k[2] == p),
        }
    failed = [c for c in checks if not c["ok"]]
    res = {"n_checks": len(checks), "n_failed": len(failed), "checks": checks, "order_info": info,
           "totals": totals, "report_rows": len(rows),
           "report_sku_rows": sum(1 for r in rows if r["sku"] is not None),
           "unique_ebay_ids": len({r["id"] for r in rows}),
           "unique_skus": len({r["sku"] for r in rows if r["sku"]})}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), indent=1)
    print(f"checks={len(checks)} failed={len(failed)}")
    for c in failed[:20]: print("FAIL", c)
    print(json.dumps(totals, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
