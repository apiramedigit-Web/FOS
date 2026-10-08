"""Build the standalone HTML from dataset.json + reconciliation.json + Ebay.xlsx rules (rules.py).

Run order: 03_SQL/extract.py -> 06_Validation/validate_data.py -> 03_SQL/extract_prices.py -> this
           -> 06_Validation/validate_html.py, validate_ph.py, validate_price_tab.py   (all of it: run_refresh.py)
"""
import csv, glob, json, os, re
from decimal import Decimal, ROUND_HALF_UP
import rules

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data", "dataset.json")
IDS_OUT = os.path.join(HERE, "data", "id_rows.json")
RECON = os.path.join(BASE, "06_Validation", "reports", "reconciliation.json")
PH_MAP = os.path.join(HERE, "data", "ph_map.json")          # 03_SQL/extract_ph.py (Sheet + ledsone staff tables)
ACCT_OVR = os.path.join(HERE, "data", "account_overrides.json")   # display-only account group for listed IDs
TEMPLATE = os.path.join(HERE, "template", "report_template.html")
OUT = os.path.join(HERE, "eBay_Final_Output_Sales_September_2026.html")
SMAP = {"COST_PER_SALE": "S", "ON_SITE": "A"}
# Price & SKU Sales Analysis tab
SNAPS = os.path.join(HERE, "data", "price_snapshots")                    # 03_SQL/extract_prices.py, one file per day
SHEET_MAP = os.path.join(BASE, "05_Evidence", "ph_mapping", "01_sheet_mapping.csv")   # 03_SQL/extract_ph.py
LISTING_PRICES = os.path.join(HERE, "data", "listing_prices.json")   # 03_SQL/extract_listing_prices.py (current price only)
# Business rule (2026-10-07): a combo SKU ("+") on an eBay ID whose PH Sheet category is Lampshade or Wall plug shows the
# BOS Healthy price of its FIRST component on the combo row; other combos stay blank; single SKUs use their own SKU.
FIRST_COMPONENT_CATS = {"lampshade", "wall plug"}
# BLOS eBay channel by the listing's real seller account x marketplace (not the display account group); the channel's
# site/currency must match the marketplace. led_sone on the Germany site and other pairs have no channel -> blank.
CHANNEL = {("led_sone", "UK"): "ledsone", ("electricalsone", "UK"): "electricalsone", ("so_926407", "UK"): "sunsone",
           ("huettenlampen", "Germany"): "huettenlampen", ("ledsonede", "Germany"): "ledsonede",
           ("electricalsone", "Germany"): "electricalsone-de"}


def r4(x):
    return None if x is None else round(x, 4)


# one file per extract date (history of the extracted Sep figures; kept as data, no longer shown on the page)
MAIN_SNAPS = os.path.join(HERE, "data", "main_snapshots")
DAILY = os.path.join(HERE, "data", "daily_detail.json")      # 03_SQL/extract_daily.py (Sep day range filter)


def days_payload(extract_generated_at):
    """Day-level LY/TY figures for the Sep day range filter; only when built from this same extract."""
    if not os.path.exists(DAILY):
        raise SystemExit("daily_detail.json missing - run 03_SQL/extract_daily.py after extract.py")
    d = json.load(open(DAILY, encoding="utf-8"))
    if d["dataset_generated_at"] != extract_generated_at:
        raise SystemExit(f"daily_detail.json was built from extract {d['dataset_generated_at']}, dataset is {extract_generated_at} - rerun extract_daily.py")
    return {"generated_at": d["generated_at"], "last_day": d["last_day"], "end_date": d["end_date"], "traffic_end": d["traffic_end"],
            "end_open": d.get("end_open") or {}, "rows": d["rows"]}


def main_snapshot(path):
    """Daily snapshot of the September figures as extracted on one date: "id|mp" -> [LY sales, TY sales, LY orders,
    TY orders, LY views, TY views] (orders = listing-level, views None = no traffic row). Written once per extract date."""
    d = json.load(open(path, encoding="utf-8"))
    day = d["meta"]["generated_at"][:10]
    snap = {}
    for r in d["rows"]:
        v = snap.setdefault(f'{r["id"]}|{r["mp"]}', [0.0, 0.0, 0, 0, None, None])
        v[0] += r["LY"]["sales"]; v[1] += r["TY"]["sales"]
        v[2], v[3], v[4], v[5] = r["LY"]["lorders"], r["TY"]["lorders"], r["LY"]["views"], r["TY"]["views"]
    snap = {k: [r4(v[0]), r4(v[1])] + v[2:] for k, v in snap.items()}
    os.makedirs(MAIN_SNAPS, exist_ok=True)
    json.dump({"date": day, "extracted_at": d["meta"]["generated_at"], "rows": snap},
              open(os.path.join(MAIN_SNAPS, day + ".json"), "w", encoding="utf-8"), separators=(",", ":"))
    return day


def price2(p):
    """Σ(item price × qty) ÷ Σ qty, exact decimal, half-up to 2 dp; None when no units (blank, never 0)."""
    if not p["units"]:
        return None
    v = Decimal(repr(round(p["pxq"], 4))) / Decimal(repr(round(p["units"], 4)))
    return float(v.quantize(Decimal("0.01"), ROUND_HALF_UP))


def sheet_categories():
    """eBay ID -> PH Sheet category (block header "PH - Category")."""
    cat = {}
    for c in csv.DictReader(open(SHEET_MAP, encoding="utf-8-sig")):
        h = c["block"]
        k = (h.rsplit("-", 1)[1] if h.startswith("Rectangular Flush mount-") else re.split(r"\s*-\s*", h, maxsplit=1)[-1]).strip()
        assert cat.setdefault(c["ebay_id"], k) == k, (c["ebay_id"], cat[c["ebay_id"]], k)
    return cat


def price_tab(ids, d_rows):
    """Per eBay ID row: [[sku, rule, sales, channel, listing, oct]]; rule 0 = single SKU, 1 = combo using first component, 2 = other combo.
    listing = [current eBay listing price, currency] of this exact eBay ID x SKU (ledsone listings.ebay_listings), else None.
    sales = [LY price, LY units, TY price, TY units, LY orders, TY orders, LY sales, TY sales] of this eBay ID x SKU's own
    Completed orders (business decision 2026-10-07: eBay ID x SKU grain), else None. Price = Σ(item price × qty) ÷ Σ qty in
    exact decimals, rounded half-up to 2 dp here (28.335 -> 28.34; the browser's float division would show 28.33)."""
    cat = sheet_categories()
    if not os.path.exists(LISTING_PRICES):
        raise SystemExit("listing_prices.json missing - run 03_SQL/extract_listing_prices.py")
    lp = json.load(open(LISTING_PRICES, encoding="utf-8"))
    LP = lp["prices"]
    # eBay ID x SKU per day (1 Sep .. latest order date, LY = same calendar days of 2025) for the Price tab date range
    dsk = json.load(open(DAILY, encoding="utf-8"))["skus"]
    sk = {}
    for r in d_rows:
        if r["sku"] and (r["LY"]["units"] or r["TY"]["units"] or r["LY"]["orders"] or r["TY"]["orders"]):
            sk[(r["ss"], r["mp"], r["id"], r["sku"])] = [price2(r["LY"]), r4(r["LY"]["units"]), price2(r["TY"]), r4(r["TY"]["units"]),
                                                         r["LY"]["orders"], r["TY"]["orders"], r4(r["LY"]["sales"]), r4(r["TY"]["sales"])]
    entries, need_bos, need_eb = [], set(), {}
    for x in ids:
        fc = cat.get(x["id"], "").lower() in FIRST_COMPONENT_CATS
        ch = CHANNEL.get((x["ss"], x["mp"]))
        e = []
        base = x["skus"] or x["lsku"]
        # SKUs whose only Completed orders fall on the October days (1 Oct .. latest order date, 2025 or 2026): added to
        # their eBay ID so October sales are visible (flag 1 = "Oct order"); no September figures, so sales stay None
        extra = sorted(s for s in dsk.get(f'{x["id"]}|{x["mp"]}', {}) if s and s not in base)
        for s in base + extra:
            rule = 0 if "+" not in s else 1 if fc else 2
            sales = sk.get((x["ss"], x["mp"], x["id"], s))
            assert sales or not x["skus"] or s in extra, (x["id"], s)      # an order SKU always has its own orders
            assert not (s in extra and sales), (x["id"], s)
            e.append([s, rule, sales, ch, LP.get(f'{x["id"]}|{x["mp"]}|{s}'), 1 if s in extra else 0])
            if rule < 2: need_bos.add(s if rule == 0 else s.split("+", 1)[0])
            if ch: need_eb.setdefault(ch, set()).add(s)
        entries.append(e)
    snaps = {}
    for f in sorted(glob.glob(os.path.join(SNAPS, "*.json"))):
        sp = json.load(open(f, encoding="utf-8"))
        snaps[sp["date"]] = {
            "fetched_at": sp["fetched_at"], "bos_published_at": sp["bos"]["published_at"],
            "bos": {k: v["v"] for k, v in sp["bos"]["healthy"].items() if k in need_bos},
            "eb": {c: {k: v for k, v in sp["blos"]["channels"][c]["prices"].items() if k in need_eb.get(c, ())}
                   for c in sp["blos"]["channels"]},
            # BLOS postage status per priced SKU: "I" = Recommended Price includes postage, "N" = not included
            # (absent in snapshots taken before 2026-10-07 15:30 -> "Postage status not available in source data")
            "ebp": {c: {k: v for k, v in (sp["blos"]["channels"][c].get("postage") or {}).items() if k in need_eb.get(c, ()) and v}
                    for c in sp["blos"]["channels"]},
            "cur": {c: [cu for _, cu in sp["blos"]["channels"][c]["site_currency"]] for c in sp["blos"]["channels"]}}
    if not snaps:
        raise SystemExit("no price snapshot - run 03_SQL/extract_prices.py first")
    return entries, {"dates": sorted(snaps), "snaps": snaps, "listing_synced_at": lp["synced_at"], "days": dsk}


def main():
    d = json.load(open(DATA, encoding="utf-8"))
    recon = json.load(open(RECON, encoding="utf-8"))
    if recon["n_failed"]:
        raise SystemExit(f"reconciliation has {recon['n_failed']} failures - not building")
    m = d["meta"]
    ids = rules.build_ids(d["rows"], m["coverage"])
    json.dump(ids, open(IDS_OUT, "w", encoding="utf-8"), separators=(",", ":"))
    ph = json.load(open(PH_MAP, encoding="utf-8"))
    assert len(ph["phs"]) == 30 and all(p in ph["phs"] for v in ph["map"].values() for p in v)
    # display-only account group for the listed IDs (business decision 2026-10-07); values untouched
    ovr = json.load(open(ACCT_OVR, encoding="utf-8"))["ids"]
    for i, o in ovr.items():
        hit = [x for x in ids if x["id"] == i]
        assert len(hit) == 1 and (hit[0]["acct"], hit[0]["mp"]) == (o["from_acct"], o["mp"]), (i, [(x["acct"], x["mp"]) for x in hit])
        hit[0]["acct"] = o["acct"]

    # Every eBay ID in scope is embedded (14,890). v3 kept only IDs with Sales/Ad Sales and so
    # dropped real ad impressions/clicks/spend and views (see 06_Validation/reports/field_audit_*).
    pe, price = price_tab(ids, d["rows"])
    cat = sheet_categories()                     # PH Sheet category of the eBay ID ("" = no PH category)
    main_snapshot(DATA)
    rows = []
    for x, e in zip(ids, pe):
        per = lambda p: [r4(x[p]["s"]), x[p]["o"], r4(x[p]["u"]), r4(x[p]["pxq"]), x[p]["v"], x[p]["im"]]
        ads = {SMAP[s]: {p: [r4(v) for v in vals] for p, vals in pv.items()} for s, pv in x["ads"].items()}
        # a[8] = listing_data SKUs, only for IDs with no Completed order (no order SKU)
        # a[10] = PHs owning the eBay ID (item_id); an ID with two PHs lists both, [] = Unassigned
        # a[11] = Price & SKU Sales Analysis entries per SKU: [sku, rule, sales, BLOS channel] (see price_tab)
        # a[12] = Category (PH Sheet block category of the eBay ID; "" when the ID has no PH category)
        rows.append([x["acct"], x["mp"], x["id"], x["skus"], per("LY"), per("TY"), ads, x["seg"],
                     [] if x["skus"] else x["lsku"], x["created"], ph["map"].get(x["id"], []), e, cat.get(x["id"], "")])
    payload = json.dumps({"generated_at": m["generated_at"], "phs": ph["phs"], "rows": rows, "price": price,
                          "days": days_payload(m["generated_at"])},
                         separators=(",", ":"), ensure_ascii=False).replace("</", r"<\/")
    html = open(TEMPLATE, encoding="utf-8").read()
    assert html.count("/*__DATA__*/null") == 1
    open(OUT, "w", encoding="utf-8").write(html.replace("/*__DATA__*/null", payload))
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1e6:.2f} MB, {len(rows)} eBay IDs)")


if __name__ == "__main__":
    main()
