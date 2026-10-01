"""Field-level 3-way audit: source SQL vs dataset.json vs an HTML file's embedded payload.

Usage: python audit_fields.py <html> [label]   -> writes reports/field_audit_<label>.json
Money is summed per marketplace only inside checks; the printed grand totals mix currencies
and are used ONLY as equality fingerprints, never as business figures.
"""
import json, os, re, sys
from collections import defaultdict
import psycopg2

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(BASE, "04_HTML_Report", "data", "dataset.json")
SS = ("so_926407", "led_sone", "electricalsone", "huettenlampen", "ledsonede")
PER = "CASE WHEN {c} < '2026-01-01' THEN 'LY' ELSE 'TY' END"
WIN = "(({c} >= '2025-09-01' AND {c} < '2025-10-01') OR ({c} >= '2026-09-01' AND {c} < '2026-10-01'))"
STRAT = {"COST_PER_SALE": "S", "ON_SITE": "A"}


MP = {"EBAY_GB": "UK", "EBAY_DE": "Germany", "EBAY_FR": "France", "EBAY_US": "US", "EBAY_IT": "Italy", "EBAY_ES": "Spain", "EBAY_CA": "Canada"}


def ledsone_dsn():
    dsn = os.environ.get("WLP_SOURCE_DB_URL")
    if not dsn:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            dsn = winreg.QueryValueEx(k, "WLP_SOURCE_DB_URL")[0]
    return dsn


def source():
    con = psycopg2.connect(os.environ["DATABASE_URL"]); con.set_session(readonly=True); cur = con.cursor()
    out = {}
    cur.execute(f"""SELECT {PER.format(c='order_date')}, SUM(COALESCE(order_total,0)), COUNT(DISTINCT (item_id, order_id)),
                    COUNT(DISTINCT sku), COUNT(*) FILTER (WHERE sku IS NULL OR btrim(sku)='')
                    FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed'
                    AND ss_name IN %(ss)s AND {WIN.format(c='order_date')} GROUP BY 1""", {"ss": SS})
    for p, s, o, nsku, blank in cur.fetchall():
        out[f"Sales {p}"] = float(s); out[f"Orders {p}"] = o; out[f"blank SKU lines {p}"] = blank
    cur.execute(f"""SELECT {PER.format(c='date')}, SUM(COALESCE(click,0)), SUM(COALESCE(impression,0))
                    FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN %(ss)s AND {WIN.format(c='date')} GROUP BY 1""", {"ss": SS})
    for p, v, im in cur.fetchall():
        out[f"Views {p}"] = float(v); out[f"Impressions {p}"] = float(im)
    # eBay ID universe = orders ∪ traffic (order_management_copy) ∪ ads (ledsone); union taken in Python
    cur.execute(f"""SELECT ss_name, market_place, item_id FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s AND {WIN.format(c='order_date')}
        UNION SELECT sub_source_name, market_place, ref_id FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN %(ss)s AND {WIN.format(c='date')}""", {"ss": SS})
    universe = set(cur.fetchall())
    # ads: ledsone ebay_campaigns.listing_performance (eBay PPC source of truth)
    lcon = psycopg2.connect(ledsone_dsn()); lcon.set_session(readonly=True); lcur = lcon.cursor()
    ad_from = f"""FROM ebay_campaigns.listing_performance lp JOIN ebay_campaigns.campaigns c ON c.campaign_id=lp.campaign_id
                  JOIN order_management.sub_source s ON s.id=c.sub_source
                  WHERE s.name IN %(ss)s AND c.campaign_type IN ('ON_SITE','COST_PER_SALE') AND {WIN.format(c='lp.date')}"""
    lcur.execute(f"""SELECT {PER.format(c='lp.date')}, c.campaign_type, SUM(lp.impressions), SUM(lp.clicks), SUM(lp.ad_fees_listing_currency),
                     SUM(lp.sale_amount_listing_currency), COUNT(DISTINCT (s.name, c.marketplace_id, lp.ebay_listing_id)) {ad_from} GROUP BY 1,2""", {"ss": SS})
    for p, st, im, cl, sp, sa, n in lcur.fetchall():
        k = STRAT[st]
        out[f"Ad Impressions {k} {p}"] = float(im); out[f"Ad Clicks {k} {p}"] = float(cl)
        out[f"Ad Spend {k} {p}"] = float(sp); out[f"Ad Sales {k} {p}"] = float(sa); out[f"Advertised IDs {k} {p}"] = n
    lcur.execute(f"SELECT DISTINCT s.name, c.marketplace_id, lp.ebay_listing_id::text {ad_from}", {"ss": SS})
    universe |= {(ss, MP[mp], ref) for ss, mp, ref in lcur.fetchall()}
    lcon.close()
    out["eBay IDs (account x marketplace x item)"] = len(universe)
    cur.execute(f"""SELECT COUNT(DISTINCT sku) FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed'
                    AND ss_name IN %(ss)s AND {WIN.format(c='order_date')}""", {"ss": SS})
    out["Unique SKUs (order data)"] = cur.fetchone()[0]
    con.close()
    return out


def from_id_rows(rows):
    """rows: list of dicts {LY:{s,o,v,im}, TY:..., ads:{S|A:{LY|TY:[im,cl,sp,sa,...]}}, skus:[...]}"""
    t = defaultdict(float); skus = set(); adv = defaultdict(int)
    for r in rows:
        skus.update(r["skus"])
        for p in ("LY", "TY"):
            x = r[p]
            t[f"Sales {p}"] += x["s"]; t[f"Orders {p}"] += x["o"]
            if x.get("v") is not None: t[f"Views {p}"] += x["v"]
            if x.get("im") is not None: t[f"Impressions {p}"] += x["im"]
            for k in ("S", "A"):
                a = r["ads"].get(k, {}).get(p)
                if a:
                    adv[f"Advertised IDs {k} {p}"] += 1
                    for i, nm in enumerate(("Ad Impressions", "Ad Clicks", "Ad Spend", "Ad Sales")):
                        t[f"{nm} {k} {p}"] += a[i]
    t.update(adv)
    t["eBay IDs (account x marketplace x item)"] = len(rows)
    t["Unique SKUs (order data)"] = len(skus)
    return dict(t)


def dataset_rows():
    d = json.load(open(DATA, encoding="utf-8"))["rows"]
    ids = {}
    for r in d:
        k = (r["ss"], r["mp"], r["id"])
        x = ids.setdefault(k, {"skus": [], "ads": {STRAT[s]: v for s, v in r["ads"].items()},
                               "LY": {"s": 0, "o": r["LY"]["lorders"], "v": r["LY"]["views"], "im": r["LY"].get("impr")},
                               "TY": {"s": 0, "o": r["TY"]["lorders"], "v": r["TY"]["views"], "im": r["TY"].get("impr")}})
        if r["sku"]: x["skus"].append(r["sku"])
        for p in ("LY", "TY"): x[p]["s"] += r[p]["sales"]
    return list(ids.values())


def html_rows(path):
    html = open(path, encoding="utf-8").read()
    m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    data = json.loads(m.group(1).replace("<\\/", "</"))
    out = []
    for a in data["rows"]:
        P = lambda v: {"s": v[0], "o": v[1], "v": v[4], "im": v[5] if len(v) > 5 else None}
        out.append({"skus": a[3], "LY": P(a[4]), "TY": P(a[5]), "ads": a[6]})
    return out


def main():
    html, label = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "html")
    src, ds, ht = source(), from_id_rows(dataset_rows()), from_id_rows(html_rows(html))
    rows = []
    for k in sorted(src):
        s = src[k]; d = ds.get(k); h = ht.get(k)
        rows.append({"field": k, "source": s, "dataset": d, "html": h,
                     "dataset_ok": d is not None and abs(s - d) <= 0.05,
                     "html_ok": h is not None and abs(s - h) <= 0.05,
                     "html_missing": None if h is None else round(s - h, 2)})
    out = os.path.join(BASE, "06_Validation", "reports", f"field_audit_{label}.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), indent=1)
    print(f"{'field':40} {'source':>14} {'dataset':>14} {'html':>14}  ds  html  missing_in_html")
    for r in rows:
        f = lambda v: "None" if v is None else f"{v:,.2f}"
        print(f"{r['field']:40} {f(r['source']):>14} {f(r['dataset']):>14} {f(r['html']):>14}  {'OK' if r['dataset_ok'] else 'XX':3} {'OK' if r['html_ok'] else 'XX':4}  {f(r['html_missing'])}")


if __name__ == "__main__":
    main()
