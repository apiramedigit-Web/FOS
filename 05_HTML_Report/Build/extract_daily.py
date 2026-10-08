"""Day-level figures for the Final Output Sales date range (calendar Date From / Date To) (read-only).

Same sources and rules as extract.py, split by day, from 1 Sep up to the latest eBay order date in the source (TY, 2026;
e.g. 7 Oct) and the same calendar days of 2025 (LY). Day index 1-30 = 1-30 Sep, 31.. = 1 Oct onwards:
  sales  = SUM(order_total) of Completed eBay orders, orders = COUNT(DISTINCT order_id)   (public.order_transaction)
  views  = SUM(click), impressions = SUM(impression)                                      (public.traffic_data)
Run right after extract.py: the Sep day totals (days 1-30) must equal dataset.json's monthly figures for every eBay ID, else
it stops. Writes 04_HTML_Report/data/daily_detail.json: "id|mp" -> [LY views per day 1..last_day, TY views per day (0 instead
of the array = the listing has no traffic row in Sep), LY [[day, sales, orders]...], TY [[day, sales, orders]...]], plus
end_date (last TY day = latest order date) and traffic_end (latest traffic date) - days after traffic_end have no views yet -
and end_open: eBay orders dated end_date that are not Completed yet (e.g. New / Hold), so the page can say the last day is still
incomplete in the source (they are never counted; the Completed-only rule is unchanged).
"""
import datetime, json, os
from collections import defaultdict
import psycopg2
from extract import SS

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "04_HTML_Report", "data", "dataset.json")
OUT = os.path.join(BASE, "04_HTML_Report", "data", "daily_detail.json")

# day index = days since 1 Sep + 1 in the row's own year (LY 2025, TY 2026); window = 1 Sep + n days in both years
PER = """CASE WHEN {c} < '2026-01-01' THEN 'LY' ELSE 'TY' END"""
DAY = """({c}::date - (CASE WHEN {c} < '2026-01-01' THEN DATE '2025-09-01' ELSE DATE '2026-09-01' END) + 1)"""
WIN = """(({c} >= '2025-09-01' AND {c} < DATE '2025-09-01' + %(n)s) OR ({c} >= '2026-09-01' AND {c} < DATE '2026-09-01' + %(n)s))"""
SQL_ORD = f"""SELECT ss_name, market_place, item_id, {PER.format(c='order_date')} AS per, {DAY.format(c='order_date')},
       SUM(COALESCE(order_total,0))::float, COUNT(DISTINCT order_id)
FROM public.order_transaction
WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s AND {WIN.format(c='order_date')}
GROUP BY 1,2,3,4,5"""
SQL_TRF = f"""SELECT sub_source_name, market_place, ref_id, {PER.format(c='date')} AS per, {DAY.format(c='date')},
       SUM(COALESCE(click,0))::float, SUM(COALESCE(impression,0))::float
FROM public.traffic_data
WHERE which_channel=2 AND sub_source_name IN %(ss)s AND {WIN.format(c='date')}
GROUP BY 1,2,3,4,5"""
# eBay ID x SKU per day (Price & SKU tab date range): price x qty, units, distinct orders - same fields as extract.py SQL_ORDERS
SQL_SKU = f"""SELECT ss_name, market_place, item_id, sku, {PER.format(c='order_date')} AS per, {DAY.format(c='order_date')},
       SUM(COALESCE(item_price,0)*COALESCE(quantity,0))::numeric, SUM(COALESCE(quantity,0))::numeric, COUNT(DISTINCT order_id)
FROM public.order_transaction
WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s AND {WIN.format(c='order_date')}
GROUP BY 1,2,3,4,5,6"""
# latest Completed eBay order date and latest traffic date in the source (future-dated rows ignored)
SQL_END = """SELECT (SELECT MAX(order_date)::date FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed'
                     AND ss_name IN %(ss)s AND order_date < CURRENT_DATE + 1),
                    (SELECT MAX(date) FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN %(ss)s AND date < CURRENT_DATE + 1)"""
# orders on the latest order date that are still open (not Completed / Cancelled / Refunded): the last day is partial
SQL_OPEN = """SELECT order_status, COUNT(DISTINCT order_id) FROM public.order_transaction
WHERE source_name='EBAY' AND ss_name IN %(ss)s AND order_date::date = %(d)s AND order_status NOT IN ('Completed','Cancelled','Refunded')
GROUP BY 1 ORDER BY 1"""


def main():
    con = psycopg2.connect(os.environ["DATABASE_URL"]); con.set_session(readonly=True); cur = con.cursor()
    cur.execute("select current_database()"); assert cur.fetchone()[0] == "order_management_copy"
    cur.execute(SQL_END, {"ss": SS}); end, trf_end = cur.fetchone()
    n = max(30, (end - datetime.date(2026, 9, 1)).days + 1)       # last TY day index (at least the full September)
    cur.execute(SQL_ORD, {"ss": SS, "n": n}); orders = cur.fetchall()
    cur.execute(SQL_TRF, {"ss": SS, "n": n}); traffic = cur.fetchall()
    cur.execute(SQL_SKU, {"ss": SS, "n": n}); skurows = cur.fetchall()
    cur.execute(SQL_OPEN, {"ss": SS, "d": end}); end_open = dict(cur.fetchall())
    con.close()

    D = defaultdict(lambda: defaultdict(lambda: [0.0, 0, None, None, 0.0, 0, None, None]))
    for ss, mp, item, per, day, s, o in orders:
        v = D[(ss, mp, item)][day]; i = 0 if per == "LY" else 4
        v[i] += s; v[i + 1] += o
    for ss, mp, item, per, day, cl, im in traffic:
        v = D[(ss, mp, item)][day]; i = 0 if per == "LY" else 4
        v[i + 2] = (v[i + 2] or 0) + cl; v[i + 3] = (v[i + 3] or 0) + im

    # self-check against the monthly extract (same source, same moment): every listing's days must add up exactly
    ds = json.load(open(DATA, encoding="utf-8"))
    month = {}
    for r in ds["rows"]:
        k = (r["ss"], r["mp"], r["id"])
        m = month.setdefault(k, {"LY": [0.0, 0, None, None], "TY": [0.0, 0, None, None]})
        for p in ("LY", "TY"):
            m[p][0] += r[p]["sales"]; m[p][1] = r[p]["lorders"]; m[p][2] = r[p]["views"]; m[p][3] = r[p]["impr"]
    bad = []
    for k in set(month) | set(D):
        days = {d: v for d, v in D.get(k, {}).items() if d <= 30}     # September only
        for p, i in (("LY", 0), ("TY", 4)):
            s = sum(v[i] for v in days.values()); o = sum(v[i + 1] for v in days.values())
            vv = [v[i + 2] for v in days.values() if v[i + 2] is not None]; ii = [v[i + 3] for v in days.values() if v[i + 3] is not None]
            m = month.get(k, {"LY": [0.0, 0, None, None], "TY": [0.0, 0, None, None]})[p]
            if abs(s - m[0]) > 0.005 or o != m[1] or (sum(vv) if vv else None) != m[2] or (sum(ii) if ii else None) != m[3]:
                bad.append((k, p, (round(s, 2), o, sum(vv) if vv else None), (round(m[0], 2), m[1], m[2])))
    # SKU level: Sep days of every eBay ID x SKU must equal the monthly per-SKU price x qty / units / orders
    S = defaultdict(list)
    for ss, mp, item, sku, per, day, pxq, u, o in skurows:
        S[(ss, mp, item, sku or "", per)].append((day, pxq, u, o))
    msku = {}
    for r in ds["rows"]:
        for p in ("LY", "TY"):
            if r[p]["units"] or r[p]["orders"] or r[p]["pxq"]:
                msku[(r["ss"], r["mp"], r["id"], r["sku"] or "", p)] = (r[p]["pxq"], r[p]["units"], r[p]["orders"])
    for k in set(msku) | {k for k, v in S.items() if any(d <= 30 for d, *_ in v)}:
        sp = [x for x in S.get(k, []) if x[0] <= 30]
        got = (float(sum(x[1] for x in sp)), float(sum(x[2] for x in sp)), sum(x[3] for x in sp))
        m = msku.get(k, (0.0, 0.0, 0))
        if abs(got[0] - m[0]) > 0.005 or abs(got[1] - m[1]) > 1e-9 or got[2] != m[2]:
            bad.append((k, got, m))
    if bad:
        raise SystemExit(f"day totals differ from dataset.json for {len(bad)} listing-periods, e.g. {bad[:3]} - run extract.py first")

    # compact payload per "id|mp": [LY views by day 1..n (0 = no traffic row in the whole window), TY views by day,
    # LY [[day, sales, orders], ...], TY [[day, sales, orders], ...]]. Impressions are not split by day.
    def views(days, i):
        return [int(days[d][i + 2]) if d in days and days[d][i + 2] is not None else 0 for d in range(1, n + 1)] \
            if any(v[i + 2] is not None for v in days.values()) else 0
    def sales(days, i):
        return [[d, round(v[i], 4), v[i + 1]] for d, v in sorted(days.items()) if v[i] or v[i + 1]]
    out = {f"{item}|{mp}": [views(days, 0), views(days, 4), sales(days, 0), sales(days, 4)] for (ss, mp, item), days in D.items()}
    # "id|mp" -> {sku: [LY [[day, price x qty, units, orders]...], TY [...]]}  (price x qty exact to 4 dp; units whole numbers)
    skus = {}
    for (ss, mp, item, sku, per), v in S.items():
        e = skus.setdefault(f"{item}|{mp}", {}).setdefault(sku, [[], []])
        e[0 if per == "LY" else 1] += [[d, float(round(pxq, 4)), int(u) if u == int(u) else float(u), o] for d, pxq, u, o in sorted(v)]
    json.dump({"generated_at": datetime.datetime.now().isoformat(timespec="seconds"), "dataset_generated_at": ds["meta"]["generated_at"],
               "skus": skus,
               "last_day": n, "end_date": str(datetime.date(2026, 9, 1) + datetime.timedelta(days=n - 1)), "traffic_end": str(trf_end),
               "end_open": end_open, "rows": out}, open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"daily_detail: {len(out)} listings, 1 Sep -> {datetime.date(2026, 9, 1) + datetime.timedelta(days=n - 1)} ({n} days), "
          f"traffic to {trf_end}; Sep day totals == monthly extract; not yet Completed on {end}: {end_open or 'none'}")


if __name__ == "__main__":
    main()
