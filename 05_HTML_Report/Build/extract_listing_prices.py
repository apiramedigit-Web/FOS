"""Current eBay listing price per eBay ID x SKU for the Price & SKU tab (read-only).

Source: ledsone listings.ebay_listings (synced daily; it holds the CURRENT price only - there is no eBay price history in any
source, so this is never a September price). DSN: WLP_SOURCE_DB_URL (ledsone, read-only), never printed.
Match: exact item_id + exact SKU + site = the dashboard marketplace, wrong_sku = 0 only (rows flagged wrong_sku are never
used); the variation row (is_child = 1) first, else the listing's own row (single-SKU listing, is_parent = 1).
Blank (never a guess): ended listing (is_ended = 1, price frozen at the end date), or two rows for the same key with
different prices. Writes 04_HTML_Report/data/listing_prices.json: {"id|mp|sku": [price, currency]} + counts.
"""
import datetime, json, os
import psycopg2

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "04_HTML_Report", "data", "dataset.json")   # extract.py
OUT = os.path.join(BASE, "04_HTML_Report", "data", "listing_prices.json")
DAILY_DETAIL = os.path.join(BASE, "04_HTML_Report", "data", "daily_detail.json")   # extract_daily.py (run before this)


def main():
    # every eBay ID x SKU the dashboard can show: order SKUs and listing SKUs of each listing (a superset is harmless)
    want = set()
    for r in json.load(open(DATA, encoding="utf-8"))["rows"]:
        for s in ([r["sku"]] if r["sku"] else []) + list(r.get("lsku") or []):
            want.add((r["id"], r["mp"], s))
    # + SKUs sold on the October days (03_SQL/extract_daily.py), shown on the Price tab as "Oct order" rows
    for k, v in json.load(open(DAILY_DETAIL, encoding="utf-8"))["skus"].items():
        i, mp = k.split("|")
        want.update((i, mp, s) for s in v if s)
    items = sorted({k[0] for k in want})
    con = psycopg2.connect(os.environ["WLP_SOURCE_DB_URL"]); con.set_session(readonly=True, autocommit=True); cur = con.cursor()
    cur.execute("""SELECT item_id, site, sku, price::text, currency, is_child, is_ended, updated_at
                   FROM listings.ebay_listings WHERE wrong_sku = 0 AND item_id = ANY(%s)""", (items,))
    rows = cur.fetchall(); con.close()
    cand = {}
    for item, site, sku, price, cur_, child, ended, upd in rows:
        k = (item, site, sku)
        if k in want and price is not None:
            cand.setdefault(k, []).append((child, ended, price, cur_, upd))
    out, n_ended, n_amb, synced = {}, 0, 0, None
    for k, c in cand.items():
        best = [x for x in c if x[0] == 1] or c                       # variation row first, else the listing row
        if any(x[1] for x in best):
            n_ended += 1; continue
        if len({(x[2], x[3]) for x in best}) > 1:
            n_amb += 1; continue
        out["|".join(k)] = [best[0][2], best[0][3]]
        synced = max(synced or best[0][4], best[0][4])
    json.dump({"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "source": "ledsone listings.ebay_listings (current price, wrong_sku=0)", "synced_at": str(synced),
               "dashboard_keys": len(want), "matched": len(out), "ended": n_ended, "ambiguous": n_amb, "prices": out},
              open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"listing prices: {len(out)} of {len(want)} eBay ID x SKU rows priced; {n_ended} ended listing, {n_amb} conflicting duplicates (blank); synced {synced}")


if __name__ == "__main__":
    main()
