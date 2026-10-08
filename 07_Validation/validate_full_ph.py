"""Full PH -> eBay ID -> dashboard field validation against the LIVE sources (read-only).

Nothing here writes to a database, the Sheet, the mapping or the dashboard. It only reads and writes reports.
  1. PH ownership: live Google Sheet ("Master Sheet Ebay 4th Cycle", fresh xlsx export) + live ledsone staff PH tables,
     parsed by independent code with the approved resolution rule, vs the extracted 04_HTML_Report/data/ph_map.json.
  2. Field values: fresh item-level SQL (order_management_copy: orders, traffic, listing SKUs; ledsone: ads,
     listing created date), written independently of 03_SQL/extract.py, vs every rendered dashboard cell.
     Every cell is compared as the exact display string. The only transformation applied to the source value is the
     dashboard's documented representation: values are stored to 4 dp in the page payload (build.py r4), then shown
     with en-GB Intl formatting (2 dp money/percent, 0 dp counts, half-away-from-zero). No numeric tolerance is used.
     Each mismatch is classified against the build snapshot (dataset.json, extracted 2026-10-01T12:43):
       SOURCE_DRIFT  = dashboard == snapshot, live source changed since the extract
       BUILD_ERROR   = dashboard != snapshot
  3. Coverage, multi-PH, Unassigned, per-PH x account x marketplace totals, global totals, 30-PH contamination
     test in the browser, data-quality checks, screenshots.
Writes 06_Validation/reports/ph_id_completeness.json, ph_dashboard_coverage.json, full_ph_data_validation.{json,md}
and full_ph_row_mismatches.csv.
"""
import csv, io, json, os, pathlib, re, sys, urllib.request, winreg, datetime
from collections import defaultdict, Counter
from decimal import Decimal, ROUND_HALF_UP
import openpyxl, psycopg2
from playwright.sync_api import sync_playwright
from validate_html import GET_ROWS, all_pages, shown, expected_ids, acct_override

BASE = pathlib.Path(__file__).resolve().parent.parent
REP = BASE / "06_Validation" / "reports"
HTML = BASE / "04_HTML_Report" / "eBay_Final_Output_Sales_September_2026.html"
PH_MAP = BASE / "04_HTML_Report" / "data" / "ph_map.json"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1X2_ruF6uelZ0hvRwmok7mheNECt3tOJj1XOJl9dUWjk/export?format=xlsx"
SHEET_TAB = "Master Sheet Ebay 4th Cycle"
ALIAS = {"Paulroshan": "paulr", "Tharsiga Jaf": "Tharsika(jaffna)", "Tharshika Nell": "Tharsiga(nelli)",
         "Illakiya": "Illakkiya", "Ilakkiya": "Illakkiya"}     # approved resolution (user, 2026-10-02)
SS = ("so_926407", "led_sone", "electricalsone", "huettenlampen", "ledsonede")
NOPH = "__none__"
PER = """CASE WHEN {c} >= '2025-09-01' AND {c} < '2025-10-01' THEN 'LY' WHEN {c} >= '2026-09-01' AND {c} < '2026-10-01' THEN 'TY' END"""
WHR = """(({c} >= '2025-09-01' AND {c} < '2025-10-01') OR ({c} >= '2026-09-01' AND {c} < '2026-10-01'))"""


def acct(ss, mp):
    return {"so_926407": "Sunsone", "electricalsone": "Electricalsone", "huettenlampen": "Huttenlampen",
            "ledsonede": "ledsone de"}.get(ss) or ("ledsone uk de" if mp == "Germany" else "Ledsone")


def env(k):
    v = os.environ.get(k)
    if v: return v
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
        return winreg.QueryValueEx(h, k)[0]


# ------------------------------------------------------------------ display formatting (en-GB Intl, halfExpand)
# Intl (ICU) rounds the shortest decimal form of the double: 20.86/4 = 5.215 -> 5.22 (Decimal(5.215) would be 5.2149999…)
def f2(x): return format(Decimal(repr(float(x))).quantize(Decimal("0.01"), ROUND_HALF_UP), ",.2f")
def f0(x): return format(Decimal(repr(float(x))).quantize(Decimal("1"), ROUND_HALF_UP), ",.0f")
def r4(x): return None if x is None else round(x, 4)


def expected_cells(d):
    """Display strings the page must show for one eBay ID (d in validate_html.expected_ids structure)."""
    L, T = d["LY"], d["TY"]
    ls, ts = r4(L["s"]), r4(T["s"])
    e = {"lys": f2(ls), "tys": f2(ts), "lyo": f0(L["o"]), "tyo": f0(T["o"])}
    if ls == 0: e["yoy"] = "N/A"
    else:
        v = (ts - ls) / ls * 100; e["yoy"] = ("+" if v > 0 else "") + f2(v) + "%"
    for p, k in (("LY", "ly"), ("TY", "ty")):
        x = d[p]
        if p == "LY" and d["nl"]:
            e[k + "v"] = e[k + "c"] = "Not live in Sep 2025"
        elif x["v"] is None:
            e[k + "v"] = e[k + "c"] = "No data"
        else:
            e[k + "v"] = f0(x["v"])
            e[k + "c"] = "N/A" if x["v"] == 0 else f2(x["o"] / x["v"] * 100) + "%"
            e[k + "v_im"] = "no data" if x["im"] is None else f0(x["im"])
        # LY/TY Price left the main table on 2026-10-07 (Price & SKU Sales Analysis tab, validate_price_tab.py)
    ads = {S: {p: [r4(v) for v in vals] for p, vals in pv.items()} for S, pv in d["ads"].items()}
    spec = {"LYAdSales": ("LY", lambda a: f2(a[3])), "TYAdSales": ("TY", lambda a: f2(a[3])),
            "AdImpressions": ("TY", lambda a: f0(a[0])), "AdClicks": ("TY", lambda a: f0(a[1])),
            "AdSpend": ("TY", lambda a: f2(a[2])), "AdSales": ("TY", lambda a: f2(a[3])),
            "ra": ("TY", lambda a: ("N/A" if a[2] == 0 else f2(a[3] / a[2])) + " / " + ("N/A" if a[3] == 0 else f2(a[2] / a[3] * 100) + "%"))}
    for col, (p, fn) in spec.items():
        if col == "LYAdSales" and d["nl"]:
            e[col + "S"] = e[col + "A"] = "Not live in Sep 2025"; continue
        s, a = ads.get("S", {}).get(p), ads.get("A", {}).get(p)
        for S, x in (("S", s), ("A", a)):
            e[col + S] = "Not advertised" if (s is None and a is None) else ("–" if x is None else fn(x))
    e["segs"] = sorted(d["seg"])
    if d["skus"]: e["sku_lines"], e["sku_src"] = sorted(d["skus"]), False
    elif d["lsku"]: e["sku_lines"], e["sku_src"] = list(d["lsku"]), True
    else: e["sku_lines"], e["sku_src"], e["sku"] = [], False, "Not available in source data"
    return e


def page_cells(c):
    g = {k: c.get(k) for k in ("lys", "tys", "lyo", "tyo", "yoy", "lyv", "tyv", "lyc", "tyc")}
    for col in ("LYAdSales", "TYAdSales", "AdImpressions", "AdClicks", "AdSpend", "AdSales", "ra"):
        for S in "SA": g[col + S] = c.get(col + S)
    for k in ("ly", "ty"):
        m = re.search(r"Impressions: ([\d,]+|no data)", c.get(k + "v_t") or "")
        g[k + "v_im"] = m.group(1) if m else None
    g["segs"], g["sku_lines"], g["sku_src"], g["sku"] = sorted(c["segs"]), c["sku_lines"], c["sku_src"], c.get("sku")
    return g


FIELD = {"lys": "LY Sales", "tys": "TY Sales", "yoy": "YoY %", "lyo": "LY Orders", "tyo": "TY Orders", "lyv": "LY Views",
         "tyv": "TY Views", "lyc": "LY Conversion %", "tyc": "TY Conversion %", "lyp": "LY Price", "typ": "TY Price",
         "lyv_im": "LY Impressions (Views tooltip)", "tyv_im": "TY Impressions (Views tooltip)", "segs": "Segment",
         "sku_lines": "SKU", "sku_src": "SKU source tag", "sku": "SKU"}
for _c, _n in (("LYAdSales", "LY Ad Sales"), ("TYAdSales", "TY Ad Sales"), ("AdImpressions", "Ad Impressions"), ("AdClicks", "Ad Clicks"),
               ("AdSpend", "Ad Spend"), ("AdSales", "Ad Sales"), ("ra", "ROAS/ACOS")):
    FIELD[_c + "S"], FIELD[_c + "A"] = _n + " (Std)", _n + " (Adv)"
GROUP = {"Sales": {"lys", "tys", "yoy", "lyo", "tyo"}, "Traffic": {"lyv", "tyv", "lyc", "tyc", "lyv_im", "tyv_im"},
         "Segment": {"segs"}, "SKU": {"sku_lines", "sku_src", "sku"}}


def field_group(k):
    for g, ks in GROUP.items():
        if k in ks: return g
    return "Advertising"


# ------------------------------------------------------------------ live sources
def live_sources():
    oc = psycopg2.connect(env("DATABASE_URL")); oc.set_session(readonly=True); cur = oc.cursor()
    cur.execute("select current_database()"); assert cur.fetchone()[0] == "order_management_copy"
    cur.execute(f"""SELECT ss_name, market_place, item_id::text, {PER.format(c='order_date')},
        SUM(COALESCE(order_total,0))::float, COUNT(DISTINCT order_id), SUM(COALESCE(quantity,0))::float,
        SUM(COALESCE(item_price,0)*COALESCE(quantity,0))::float, array_agg(DISTINCT sku) FILTER (WHERE sku IS NOT NULL),
        COUNT(*) FILTER (WHERE order_total < 0 OR quantity < 0 OR item_price < 0)
      FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %(ss)s
       AND {WHR.format(c='order_date')} GROUP BY 1,2,3,4""", {"ss": SS})
    orders = cur.fetchall()
    cur.execute(f"""SELECT sub_source_name, market_place, ref_id::text, {PER.format(c='date')},
        SUM(COALESCE(click,0))::float, SUM(COALESCE(impression,0))::float, MAX(date)::text
      FROM public.traffic_data WHERE which_channel=2 AND sub_source_name IN %(ss)s AND {WHR.format(c='date')} GROUP BY 1,2,3,4""", {"ss": SS})
    traffic = cur.fetchall()
    cur.execute("""SELECT sub_source_name, market_place, ref_id::text, array_agg(DISTINCT btrim(sku) ORDER BY btrim(sku))
      FROM public.listing_data WHERE which_channel=2 AND sub_source_name IN %(ss)s AND wrong_sku=0 AND sku IS NOT NULL AND btrim(sku)<>''
      GROUP BY 1,2,3""", {"ss": SS})
    lsku = {(a, b, c): d for a, b, c, d in cur.fetchall()}
    oc.close()
    lc = psycopg2.connect(env("WLP_SOURCE_DB_URL")); lc.set_session(readonly=True); cur = lc.cursor()
    cur.execute("select current_database()"); assert cur.fetchone()[0] == "ledsone"
    cur.execute(f"""SELECT s.name, CASE c.marketplace_id WHEN 'EBAY_GB' THEN 'UK' WHEN 'EBAY_DE' THEN 'Germany' WHEN 'EBAY_FR' THEN 'France'
          WHEN 'EBAY_US' THEN 'US' WHEN 'EBAY_IT' THEN 'Italy' WHEN 'EBAY_ES' THEN 'Spain' WHEN 'EBAY_CA' THEN 'Canada' ELSE c.marketplace_id END,
        lp.ebay_listing_id::text, {PER.format(c='lp.date')}, c.campaign_type,
        SUM(lp.impressions)::float, SUM(lp.clicks)::float, SUM(lp.ad_fees_listing_currency)::float, SUM(lp.sale_amount_listing_currency)::float,
        COUNT(*), COUNT(DISTINCT (lp.campaign_id, lp.ad_group_id, lp.date))
      FROM ebay_campaigns.listing_performance lp JOIN ebay_campaigns.campaigns c ON c.campaign_id = lp.campaign_id
      JOIN order_management.sub_source s ON s.id = c.sub_source
      WHERE s.name IN %(ss)s AND c.campaign_type IN ('ON_SITE','COST_PER_SALE') AND {WHR.format(c='lp.date')} GROUP BY 1,2,3,4,5""", {"ss": SS})
    ads = cur.fetchall()
    keys = {(r[0], r[1], r[2]) for r in orders} | {(r[0], r[1], r[2]) for r in traffic} | {(r[0], r[1], r[2]) for r in ads}
    cur.execute("SELECT item_id::text, MIN(created_at)::date::text FROM listings.ebay_listings WHERE item_id::text = ANY(%s) GROUP BY 1",
                (sorted({k[2] for k in keys}),))
    created = dict(cur.fetchall())
    lc.close()

    blank = lambda: {"s": 0.0, "u": 0.0, "pxq": 0.0, "o": 0, "v": None, "im": None}
    src, dq = {}, Counter()
    def get(ss, mp, i):
        k = (acct_override(acct(ss, mp), mp, i), mp, i)
        if k not in src:
            src[k] = {"acct": k[0], "mp": mp, "id": i, "ss": ss, "skus": set(), "lsku": [], "created": created.get(i),
                      "ads": {}, "LY": blank(), "TY": blank(), "has_order": False}
        return src[k]
    tmax = Counter()
    for ss, mp, i, p, s, o, u, pxq, skus, neg in orders:
        d = get(ss, mp, i); d["has_order"] = True
        d[p].update(s=s, o=o, u=u, pxq=pxq); d["skus"] |= set(skus or [])
        dq["negative order values (rows)"] += neg
    for ss, mp, i, p, v, im, mx in traffic:
        d = get(ss, mp, i); d[p]["v"], d[p]["im"] = v, im; tmax[(p, mx)] += 1
    for ss, mp, i, p, ct, im, cl, sp, sa, n, nd in ads:
        d = get(ss, mp, i)
        d["ads"].setdefault("S" if ct == "COST_PER_SALE" else "A", {})[p] = [im, cl, sp, sa]
        if n != nd: dq["duplicate ad rows (campaign, ad group, date)"] += n - nd
        if min(im, cl, sp, sa) < 0: dq["negative ad values"] += 1
    for d in src.values():
        if not d["has_order"]:
            d["lsku"] = lsku.get((d["ss"], d["mp"], d["id"]), [])
        L, T = d["LY"]["s"], d["TY"]["s"]
        seg = set()
        if T > L: seg.add("A – YoY Winner")
        if L > 0 and T == 0: seg.add("D – Lost Performer")
        if L > 0 and 0 < T < L: seg.add("Undetermined: B or D")
        for S, nm in (("S", "Standard"), ("A", "Advanced")):
            ly = (d["ads"].get(S, {}).get("LY") or [0, 0, 0, 0])[3]; ty = (d["ads"].get(S, {}).get("TY") or [0, 0, 0, 0])[3]
            if ly > 0 and ty == 0: seg.add(f"C – Lost Ad Sales ({nm})")
        d["seg"] = seg or {"No segment rule met"}
        lyad = any(d["ads"].get(S, {}).get("LY") for S in "SA")
        d["nl"] = bool(d["created"] and d["created"] > "2025-09-30" and L == 0 and d["LY"]["o"] == 0 and d["LY"]["v"] is None and not lyad)
    ty_dates = sorted({mx for (p, mx) in tmax if p == "TY"})
    return src, dq, {"traffic_TY_max_dates": ty_dates[-3:], "orders_rows": len(orders), "traffic_rows": len(traffic), "ad_rows": len(ads)}


def live_mapping():
    raw = urllib.request.urlopen(SHEET_URL, timeout=120).read()
    rows = list(openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)[SHEET_TAB].iter_rows(values_only=True))
    hdr, accrow = rows[0], rows[2]
    heads, cur = [], None
    for c in range(max(len(r) for r in rows)):
        h = hdr[c] if c < len(hdr) else None
        if h is not None and str(h).strip(): cur = re.sub(r"\s+", " ", str(h)).strip()
        heads.append(cur)
    cells, invalid = [], []
    for i, r in enumerate(rows[3:], start=4):
        for c, v in enumerate(r):
            if v is None or str(v).strip() == "": continue
            s = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
            h = heads[c]
            ph = h.rsplit("-", 1)[1].strip() if h.startswith("Rectangular Flush mount-") else re.split(r"\s*-\s*", h, maxsplit=1)[0].strip()
            (cells if re.fullmatch(r"\d{12}", s) else invalid).append((ph, s, accrow[c] if c < len(accrow) else None, i, c + 1))
    lc = psycopg2.connect(env("WLP_SOURCE_DB_URL")); lc.set_session(readonly=True); cur = lc.cursor()
    cur.execute("""SELECT u.username, p.ref_id, p.source_id FROM staff.ph_categories pc JOIN staff.users u ON u.id = pc.user_id
                   LEFT JOIN staff.ph_category_products p ON p.ph_category_id = pc.id""")
    db = cur.fetchall(); lc.close()
    users = sorted({r[0] for r in db}, key=str.lower)
    low = {u.lower(): u for u in users}
    res = lambda n: ALIAS.get(n) or low.get(n.lower())
    sheet_pairs = Counter((res(ph), s) for ph, s, a, i, c in cells)
    db_pairs = Counter((u, ref) for u, ref, src in db if src == 2 and ref)
    return {"users": users, "sheet_cells": cells, "invalid": invalid, "unresolved": sorted({ph for ph, *_ in cells if not res(ph)}),
            "sheet_pairs": sheet_pairs, "db_pairs": db_pairs, "sheet_acct": {(res(ph), s): a for ph, s, a, i, c in cells}}


def main():
    t0 = datetime.datetime.now()
    out = {"generated_at": t0.isoformat(timespec="seconds"), "dashboard": str(HTML), "snapshot_extracted_at": None}
    blockers, dq_issues = [], []
    # ---------------- 1. ownership completeness ----------------
    M = live_mapping()
    pm = json.load(open(PH_MAP, encoding="utf-8"))
    phs = M["users"]
    src_map = defaultdict(set)
    for (u, s) in M["sheet_pairs"]: src_map[s].add(u)     # Sheet = source of truth (user, 2026-10-02); DB not ownership
    comp = []
    for p in phs:
        sids = {s for s, v in src_map.items() if p in v}
        eids = {s for s, v in pm["map"].items() if p in v}
        dups = [f"{s} (Sheet x{n})" for (u, s), n in M["sheet_pairs"].items() if u == p and n > 1] + \
               [f"{s} (DB x{n})" for (u, s), n in M["db_pairs"].items() if u == p and n > 1]
        comp.append({"ph": p, "source_id_count": len(sids), "sheet_ids": sum(1 for (u, s) in M["sheet_pairs"] if u == p),
                     "db_ids": sum(1 for (u, s) in M["db_pairs"] if u == p), "extracted_id_count": len(eids),
                     "missing_ids": sorted(sids - eids), "extra_ids": sorted(eids - sids),
                     "duplicate_pairs": dups, "multi_ph_ids": sorted(s for s in sids if len(src_map[s]) > 1),
                     "note": "0 official eBay IDs" if not sids else "",
                     "status": "PASS" if sids == eids else "FAIL"})
    multi = {s: sorted(v, key=str.lower) for s, v in src_map.items() if len(v) > 1}
    json.dump({"source": {"sheet_tab": SHEET_TAB, "sheet_id_cells": len(M["sheet_cells"]), "sheet_invalid_cells": [list(x) for x in M["invalid"]],
                          "sheet_unresolved_names": M["unresolved"], "db": "ledsone staff.ph_categories/ph_category_products(source_id=2)/users",
                          "phs": len(phs), "distinct_ids": len(src_map), "multi_ph_ids": len(multi)},
               "extracted_generated_at": pm["meta"]["generated_at"], "per_ph": comp, "multi_ph": multi},
              open(REP / "ph_id_completeness.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    if M["unresolved"]: blockers.append(f"Sheet PH names with no DB user: {M['unresolved']}")

    # ---------------- 2. live field sources + snapshot ----------------
    src, src_dq, src_meta = live_sources()
    snap = expected_ids()
    out["snapshot_extracted_at"] = json.load(open(BASE / "04_HTML_Report" / "data" / "dataset.json", encoding="utf-8"))["meta"]["generated_at"]
    out["live_source_meta"] = src_meta
    txt = HTML.read_text(encoding="utf-8")
    payload = json.loads(txt.split("const DATA = ", 1)[1].split(";\n(function()", 1)[0])
    P = {(a[0], a[1], a[2]): a for a in payload["rows"]}
    dash_keys = set(P)
    ph_of = lambda i: src_map.get(i, set())

    # ---------------- 3. coverage ----------------
    in_dash_ids = {k[2] for k in dash_keys}
    src_ids_all = defaultdict(list)
    for k in src: src_ids_all[k[2]].append(k)
    cov, missing_all = [], {}
    for p in phs + [""]:
        off = {s for s, v in src_map.items() if (p in v if p else v)}
        exp_keys = {k for k in dash_keys if k[2] in off}
        miss = sorted(off - in_dash_ids)
        for m in miss: missing_all[m] = None
        cov.append({"ph": p or "All PHs", "official_ids": len(off), "ids_in_dashboard": len({k[2] for k in exp_keys}),
                    "dashboard_rows": len(exp_keys), "missing_from_dashboard": miss})
    # reason for each missing ID: does it have ANY Sept order/traffic/ad row, in any eBay account?
    oc = psycopg2.connect(env("DATABASE_URL")); oc.set_session(readonly=True); cur = oc.cursor()
    ids_m = sorted(missing_all)
    cur.execute(f"""SELECT 'order', ss_name, item_id::text FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed'
          AND item_id::text = ANY(%(i)s) AND {WHR.format(c='order_date')}
        UNION SELECT 'traffic', sub_source_name, ref_id::text FROM public.traffic_data WHERE which_channel=2 AND ref_id::text = ANY(%(i)s) AND {WHR.format(c='date')}
        UNION SELECT 'listing_data', sub_source_name, ref_id::text FROM public.listing_data WHERE which_channel=2 AND ref_id::text = ANY(%(i)s)""", {"i": ids_m})
    ev = defaultdict(set)
    for kind, ss, i in cur.fetchall(): ev[i].add(f"{kind}:{ss}")
    oc.close()
    lc = psycopg2.connect(env("WLP_SOURCE_DB_URL")); lc.set_session(readonly=True); cur = lc.cursor()
    cur.execute(f"""SELECT DISTINCT 'ads', s.name, lp.ebay_listing_id::text FROM ebay_campaigns.listing_performance lp
        JOIN ebay_campaigns.campaigns c ON c.campaign_id=lp.campaign_id JOIN order_management.sub_source s ON s.id=c.sub_source
        WHERE lp.ebay_listing_id::text = ANY(%(i)s) AND {WHR.format(c='lp.date')}""", {"i": ids_m})
    for kind, ss, i in cur.fetchall(): ev[i].add(f"{kind}:{ss}")
    cur.execute("""SELECT e.item_id::text, s.name FROM listings.ebay_listings e LEFT JOIN order_management.sub_source s ON s.id=e.sub_source
                   WHERE e.item_id::text = ANY(%(i)s)""", {"i": ids_m})
    listed = defaultdict(set)
    for i, ss in cur.fetchall(): listed[i].add(ss)
    lc.close()
    miss_rows = []
    for i in ids_m:
        sept = sorted(x for x in ev[i] if not x.startswith("listing_data"))
        in_scope = [x for x in sept if x.split(":")[1] in SS]
        if in_scope: reason, ok = "HAS Sept data in a report account -> should be in dashboard", False
        elif sept: reason, ok = "Sept data only in a non-report account (" + ", ".join(sept) + ")", True
        elif not listed[i]: reason, ok = "not in listings.ebay_listings and no Sept order/traffic/ad row in any account", True
        else: reason, ok = "no Sept 2025/2026 Completed order, traffic or ad row in any account (listing: " + ", ".join(sorted(map(str, listed[i]))) + ")", True
        missing_all[i] = {"ebay_id": i, "phs": sorted(ph_of(i), key=str.lower), "reason": reason, "documented_rule_applies": ok}
        miss_rows.append(missing_all[i])
    unexplained_missing = [m for m in miss_rows if not m["documented_rule_applies"]]
    # source universe vs dashboard universe (both directions, all IDs)
    src_only = sorted(set(src) - dash_keys); dash_only = sorted(dash_keys - set(src))
    json.dump({"rule": "Dashboard grain = every eBay ID (account x marketplace x item_id) with a Sep 2025/2026 Completed order, traffic row or "
                       "ad row in the 5 report accounts (03_SQL/extract.py). An official PH ID with none of these has no row by design.",
               "per_ph": cov, "missing_detail": miss_rows, "unexplained_missing": unexplained_missing,
               "live_source_keys_not_in_dashboard": [list(k) for k in src_only], "dashboard_keys_not_in_live_source": [list(k) for k in dash_only]},
              open(REP / "ph_dashboard_coverage.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    # ---------------- 4. browser: every row, every PH ----------------
    errors, rendered, ph_tests, shots = [], {}, [], {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(HTML.as_uri()); pg.wait_for_selector("#t tbody tr[data-id]")
        # these checks are for the September monthly table: select 1-30 Sep (the page default is 1 Sep -> latest data date)
        pg.fill("#sdTo", "2026-09-30"); pg.wait_for_timeout(600)
        opts = pg.eval_on_selector_all("#fPh option", "e=>e.map(x=>x.value)")
        pg.screenshot(path=str(REP / "fullval_1_all_phs.png")); shots["All PHs"] = "fullval_1_all_phs.png"
        pg.select_option("#pgSize", "200"); pg.wait_for_timeout(150)
        dup_rows = []
        for key in [""] + phs + [NOPH]:
            pg.select_option("#fPh", key); pg.wait_for_timeout(150)
            rows = all_pages(pg)[0] if shown(pg) else []
            got = [(c["acct"], c["mp"], c["id"]) for c in rows]
            for c in rows: rendered.setdefault((c["acct"], c["mp"], c["id"]), c)
            if key == "": exp = {k for k in dash_keys if ph_of(k[2])}
            elif key == NOPH: exp = {k for k in dash_keys if not ph_of(k[2])}
            else: exp = {k for k in dash_keys if key in ph_of(k[2])}
            cross = [c["id"] for c in rows if (key not in ("", NOPH) and key not in ph_of(c["id"])) or (key == NOPH and ph_of(c["id"])) or (key == "" and not ph_of(c["id"]))]
            tagbad = [c["id"] for c in rows if set(filter(None, (c["ph"] or "").split("|"))) != ph_of(c["id"])]
            d = [k for k, n in Counter(got).items() if n > 1]; dup_rows += d
            ph_tests.append({"ph": {"": "All PHs", NOPH: "Unassigned"}.get(key, key), "expected": len(exp), "displayed": len(got),
                             "count_label": shown(pg), "missing": [list(x) for x in sorted(exp - set(got))][:50],
                             "unexpected": [list(x) for x in sorted(set(got) - exp)][:50], "duplicates": [list(x) for x in d][:50],
                             "cross_ph": cross[:50], "ph_tag_mismatch": tagbad[:50],
                             "status": "PASS" if (set(got) == exp and not d and not cross and not tagbad and shown(pg) == len(exp)) else "FAIL"})
            if key == "Abinayaa": pg.select_option("#pgSize", "50"); pg.wait_for_timeout(150); pg.screenshot(path=str(REP / "fullval_2_ph_abinayaa.png")); shots["One PH with IDs (Abinayaa)"] = "fullval_2_ph_abinayaa.png"; pg.select_option("#pgSize", "200")
            if key == "Akalika": pg.screenshot(path=str(REP / "fullval_3_ph_zero_akalika.png")); shots["PH with 0 IDs (Akalika)"] = "fullval_3_ph_zero_akalika.png"
            if key == NOPH: pg.select_option("#pgSize", "50"); pg.wait_for_timeout(150); pg.screenshot(path=str(REP / "fullval_4_unassigned.png")); shots["Unassigned"] = "fullval_4_unassigned.png"; pg.select_option("#pgSize", "200")
        # SKU / detail view: a PH-owned multi-SKU listing found by search
        multi_sku = max((k for k in dash_keys if ph_of(k[2])), key=lambda k: len(P[k][3]))
        pg.select_option("#fPh", ""); pg.fill("#fQ", multi_sku[2]); pg.wait_for_timeout(400)
        pg.screenshot(path=str(REP / "fullval_5_sku_detail.png")); shots[f"SKU detail ({multi_sku[2]}, {len(P[multi_sku][3])} SKUs)"] = "fullval_5_sku_detail.png"
        pg.fill("#fQ", ""); pg.wait_for_timeout(300)
        b.close()

    # ---------------- 5. row-level field comparison ----------------
    mism, n_cells, rows_checked = [], 0, 0
    per_ph_err = Counter(); per_ph_seg = Counter(); per_ph_err_ids = defaultdict(set)
    broken = []
    for k, c in rendered.items():
        rows_checked += 1
        g = page_cells(c)
        for kk, v in c.items():
            if isinstance(v, str) and re.search(r"undefined|NaN|Infinity|\[object|null", v): broken.append((k[2], kk, v))
        s = src.get(k)
        if s is None:
            mism.append({"key": list(k), "field": "(row)", "source": "no live source row", "dashboard": "row present", "snapshot_equal": None,
                         "class": "SOURCE_DRIFT" if k in snap else "BUILD_ERROR", "phs": sorted(ph_of(k[2]))}); continue
        e, es = expected_cells(s), expected_cells(snap[k]) if k in snap else None
        for f, ev_ in e.items():
            n_cells += 1
            if f == "sku" and g.get("sku") == ev_: continue
            gv = g.get(f)
            if gv != ev_:
                if f in GROUP["SKU"]:      # classify on the whole SKU cell, not one part of it
                    same_snap = es is not None and all(es.get(x) == g.get(x) for x in ("sku_lines", "sku_src")) and ("sku" not in es or es["sku"] == g.get("sku"))
                else:
                    same_snap = es is not None and es.get(f) == gv
                mism.append({"key": list(k), "field": FIELD.get(f, f), "group": field_group(f), "source": ev_, "dashboard": gv,
                             "snapshot": es.get(f) if es else None, "class": "SOURCE_DRIFT" if same_snap else "BUILD_ERROR",
                             "phs": sorted(ph_of(k[2]), key=str.lower)})
                for p in ph_of(k[2]) or {"Unassigned"}:
                    (per_ph_seg if f == "segs" else per_ph_err)[p] += 1; per_ph_err_ids[p].add(k[2])
    with open(REP / "full_ph_row_mismatches.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["account", "marketplace", "ebay_id", "phs", "field", "group", "live_source_value", "dashboard_value", "snapshot_value", "class"])
        for m in mism: w.writerow(m["key"] + ["|".join(m["phs"]), m["field"], m.get("group", ""), m["source"], m["dashboard"], m.get("snapshot"), m["class"]])

    # ---------------- 6. per-PH and global totals: live source vs dashboard payload ----------------
    def tot(keys, from_src):
        A = defaultdict(lambda: defaultdict(float))
        for k in keys:
            a = A[(k[0], k[1])]; a["ids"] += 1
            if from_src:
                d = src.get(k)
                if d is None: continue
                vals = {"ly_sales": d["LY"]["s"], "ty_sales": d["TY"]["s"], "ly_orders": d["LY"]["o"], "ty_orders": d["TY"]["o"],
                        "ly_views": d["LY"]["v"] or 0, "ty_views": d["TY"]["v"] or 0}
                ads = d["ads"]
            else:
                r = P[k]; vals = {"ly_sales": r[4][0], "ty_sales": r[5][0], "ly_orders": r[4][1], "ty_orders": r[5][1],
                                  "ly_views": r[4][4] or 0, "ty_views": r[5][4] or 0}
                ads = r[6]
            for f, v in vals.items(): a[f] += v
            for S in "SA":
                ty = (ads.get(S) or {}).get("TY"); ly = (ads.get(S) or {}).get("LY")
                if ty: a["ad_impr_" + S] += ty[0]; a["ad_clicks_" + S] += ty[1]; a["ad_spend_" + S] += ty[2]; a["ty_ad_sales_" + S] += ty[3]
                if ly: a["ly_ad_sales_" + S] += ly[3]
        return A
    def compare(keys):
        S_, D_ = tot(keys, True), tot(keys, False)
        bad = []
        for am in sorted(set(S_) | set(D_)):
            n = D_[am]["ids"]
            for f in sorted(set(S_[am]) | set(D_[am])):
                tol = n * 0.00005 * (4 if f.startswith(("ad_spend", "ty_ad", "ly_ad")) else 1)   # payload stores 4 dp per ID
                if abs(S_[am].get(f, 0) - D_[am].get(f, 0)) > tol + 1e-9:
                    bad.append({"account": am[0], "marketplace": am[1], "field": f, "live_source": round(S_[am].get(f, 0), 4), "dashboard": round(D_[am].get(f, 0), 4)})
        return bad, {f"{a} · {m}": {f: round(v, 2) for f, v in d.items()} for (a, m), d in D_.items()}
    ph_tot = {}
    for p in phs + ["", NOPH]:
        keys = {k for k in dash_keys if (p in ph_of(k[2]) if p not in ("", NOPH) else bool(ph_of(k[2])) == (p == ""))}
        bad, totals = compare(keys)
        ph_tot[{"": "All PHs", NOPH: "Unassigned"}.get(p, p)] = {"mismatches": bad, "dashboard_totals": totals}
    glob_bad, glob_tot = compare(dash_keys)
    src_tot = {f"{a} · {m}": {f: round(v, 2) for f, v in d.items()} for (a, m), d in tot(set(src), True).items()}

    # ---------------- 7. data quality ----------------
    dq = dict(src_dq)
    dq["duplicate dashboard rows (account, marketplace, eBay ID)"] = len(payload["rows"]) - len(P)
    dq["eBay IDs on >1 dashboard row (different account/marketplace)"] = sum(1 for i, n in Counter(k[2] for k in dash_keys).items() if n > 1)
    dq["invalid eBay ID format in dashboard"] = sum(1 for k in dash_keys if not re.fullmatch(r"\d{12}", k[2]))
    dq["blank eBay ID in dashboard"] = sum(1 for k in dash_keys if not k[2])
    dq["duplicate PH+eBay ID pairs in Sheet"] = sum(n - 1 for n in M["sheet_pairs"].values() if n > 1)
    dq["duplicate PH+eBay ID rows in DB"] = sum(n - 1 for n in M["db_pairs"].values() if n > 1)
    dq["invalid Sheet cells (not a 12-digit ID)"] = len(M["invalid"])
    dq["undefined/NaN/Infinity/null in rendered cells"] = len(broken)
    dq["PH-owned dashboard IDs with no SKU in any source"] = sum(1 for k in dash_keys if ph_of(k[2]) and not P[k][3] and not P[k][8])
    sheet_ss = {"LEDSone UK": ("led_sone", "UK"), "ElectricalSone UK": ("electricalsone", "UK"), "Electricalsone UK": ("electricalsone", "UK"),
                "SunSone UK": ("so_926407", "UK"), "Sunsone UK": ("so_926407", "UK"), "HuttenLampen Germany": ("huettenlampen", "Germany"),
                "Huttenlamp DE": ("huettenlampen", "Germany"), "LEDSone Germany": ("ledsonede", "Germany"), "LEDSone DE": ("ledsonede", "Germany"),
                "LEDsone de (DE reg)": ("ledsonede", "Germany"), "LEDSone UK Reg Germany": ("led_sone", "Germany"),
                "LEDSone UK Reg DE": ("led_sone", "Germany"), "LED Sone DE (uk reg)": ("led_sone", "Germany"),
                "LEDSone US": ("led_sone", "US"), "Neighbour Market US": ("neighbourmarket", "US")}
    acct_mm = []
    by_id = defaultdict(list)
    for k in dash_keys: by_id[k[2]].append(k)
    for (u, s), a in M["sheet_acct"].items():
        if s in by_id and str(a).strip() in sheet_ss:
            ss, mp = sheet_ss[str(a).strip()]
            if not any(k[0] == acct(ss, mp) for k in by_id[s]):
                acct_mm.append({"ebay_id": s, "ph": u, "sheet_account": a, "dashboard": [f"{k[0]} · {k[1]}" for k in by_id[s]]})
    dq["Sheet account column differs from dashboard account (ownership is by eBay ID; informational)"] = len(acct_mm)

    # ---------------- 8. verdicts ----------------
    build_err = [m for m in mism if m["class"] == "BUILD_ERROR"]
    drift = [m for m in mism if m["class"] == "SOURCE_DRIFT"]
    comp_fail = [c for c in comp if c["status"] == "FAIL"]
    ph_fail = sorted({t["ph"] for t in ph_tests if t["status"] == "FAIL"} | {c["ph"] for c in comp_fail} |
                     {p for p, v in ph_tot.items() if v["mismatches"]} | {p for p in per_ph_err if per_ph_err[p] or per_ph_seg[p]})
    crit = {
        "ownership: live Sheet (source of truth) == extracted mapping for every PH": not comp_fail,
        "all Sheet PH names resolve to a DB PH": not M["unresolved"],
        "every official ID in dashboard or excluded by the documented grain rule (verified by SQL)": not unexplained_missing,
        "live source universe == dashboard universe": not src_only and not dash_only,
        "30-PH browser test: displayed == expected, 0 cross-PH, 0 duplicates (All PHs, 30 PHs, Unassigned)": all(t["status"] == "PASS" for t in ph_tests),
        "PH dropdown = All PHs + 30 PHs + Unassigned": opts == [""] + phs + [NOPH],
        "every rendered field == live source (row level, exact display string)": not mism,
        "no build/render errors (dashboard == build snapshot)": not build_err,
        "per-PH x account x marketplace totals == live source": not any(v["mismatches"] for v in ph_tot.values()),
        "global totals == live source": not glob_bad,
        "All PHs + Unassigned = every dashboard row, disjoint": len({k for k in dash_keys if ph_of(k[2])}) + len({k for k in dash_keys if not ph_of(k[2])}) == len(dash_keys),
        "no undefined/NaN/Infinity/null rendered": not broken,
        "no JS/console errors": not errors,
        "no duplicate dashboard rows": not dup_rows and dq["duplicate dashboard rows (account, marketplace, eBay ID)"] == 0,
    }
    overall = "PASS" if all(crit.values()) else "FAIL"
    by_field = Counter(m["field"] for m in mism)
    by_grp = Counter(m.get("group", "(row)") for m in mism)
    by_cls = Counter(m["class"] for m in mism)
    ph_rows = []
    for p in phs:
        c_ = next(x for x in comp if x["ph"] == p); cv = next(x for x in cov if x["ph"] == p); t = next(x for x in ph_tests if x["ph"] == p)
        ph_rows.append({"ph": p, "source_ids": c_["source_id_count"], "dashboard_ids": cv["ids_in_dashboard"], "dashboard_rows": t["displayed"],
                        "missing_from_dashboard": len(cv["missing_from_dashboard"]), "extra": len(t["unexpected"]) + len(c_["extra_ids"]),
                        "data_errors": per_ph_err[p], "segment_errors": per_ph_seg[p], "ids_with_errors": len(per_ph_err_ids[p]),
                        "status": "PASS" if p not in ph_fail else "FAIL"})
    res = {**out, "overall": overall, "criteria": crit,
           "counts": {"phs_tested": len(phs), "phs_passed": len(phs) - len([p for p in phs if p in ph_fail]), "phs_failed": [p for p in phs if p in ph_fail],
                      "source_official_ids": len(src_map), "official_ids_in_dashboard": len(set(src_map) & in_dash_ids),
                      "dashboard_rows": len(dash_keys), "dashboard_rows_all_phs": len({k for k in dash_keys if ph_of(k[2])}),
                      "unassigned_rows": len({k for k in dash_keys if not ph_of(k[2])}),
                      "missing_ids": len(miss_rows), "missing_ids_unexplained": len(unexplained_missing),
                      "extra_ids": sum(len(c["extra_ids"]) for c in comp), "duplicate_rows": len(set(dup_rows)), "multi_ph_ids": len(multi),
                      "multi_ph_ids_in_dashboard": sum(1 for s in multi if s in in_dash_ids),
                      "rows_checked": rows_checked, "cells_checked": n_cells, "field_mismatches": len(mism),
                      "field_mismatches_by_class": dict(by_cls), "field_mismatches_by_group": dict(by_grp), "field_mismatches_by_field": dict(by_field.most_common()),
                      "segment_mismatches": by_field.get("Segment", 0), "cross_ph_contamination": sum(len(t["cross_ph"]) for t in ph_tests)},
           "ph_summary": ph_rows, "ph_filter_tests": ph_tests, "ph_totals": ph_tot, "global_mismatches": glob_bad,
           "global_dashboard_totals": glob_tot, "global_live_source_totals": src_tot, "data_quality": dq,
           "sheet_vs_dashboard_account_differences": acct_mm, "html": {"js_errors": errors[:20], "broken_cells": broken[:20], "screenshots": shots,
                                                                         "kpi_cards": "none on page (removed per user 2026-10-01); summary = count line + account header totals",
                                                                         "sorting": "none on page (removed per user)"},
           "business_rule_blockers": [
               "B – YoY Recovery: 'recent sales improving' has no period/measure in Ebay.xlsx -> IDs with LY>0, 0<TY<LY shown as 'Undetermined: B or D'",
               "D – Lost Performer: tolerance of 'TY Sales ≈ 0' undefined -> applied only where TY Sales = 0",
               "SKU for 4,010 dashboard IDs with no Completed order and no listing_data SKU: not available in source data"],
           "blockers": blockers, "first_mismatches": mism[:200]}
    json.dump(res, open(REP / "full_ph_data_validation.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False, default=str)
    write_md(res)
    print(json.dumps({"overall": overall, "criteria": crit, "counts": res["counts"]}, indent=1, ensure_ascii=False))


def write_md(r):
    c = r["counts"]
    L = [f"# Full PH → eBay ID → Dashboard Validation", "", f"Run: {r['generated_at']} (read-only). Dashboard data snapshot: {r['snapshot_extracted_at']}.", "",
         f"## Overall: **{r['overall']}**", "", "| Criterion | Result |", "|---|---|"]
    L += [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in r["criteria"].items()]
    L += ["", "## Counts", "", "| Measure | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in c.items() if not isinstance(v, (dict, list))]
    L += ["", "Field mismatches by class: " + json.dumps(c["field_mismatches_by_class"]), "",
          "Field mismatches by group: " + json.dumps(c["field_mismatches_by_group"], ensure_ascii=False), "",
          "Field mismatches by field: " + json.dumps(c["field_mismatches_by_field"], ensure_ascii=False), "",
          "## PH summary", "", "| PH | Source IDs | Dashboard IDs | Rows | Missing | Extra | Data Errors | Segment Errors | Status |", "|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {p['ph']} | {p['source_ids']} | {p['dashboard_ids']} | {p['dashboard_rows']} | {p['missing_from_dashboard']} | {p['extra']} | {p['data_errors']} | {p['segment_errors']} | {p['status']} |" for p in r["ph_summary"]]
    L += ["", "## Global totals: mismatches (live source vs dashboard)", ""] + [f"- {m}" for m in r["global_mismatches"][:40]] or ["- none"]
    L += ["", "## Data quality", ""] + [f"- {k}: {v}" for k, v in r["data_quality"].items()]
    L += ["", "## HTML", "", f"- JS errors: {len(r['html']['js_errors'])}", f"- broken cells: {len(r['html']['broken_cells'])}",
          f"- KPI cards: {r['html']['kpi_cards']}", f"- sorting: {r['html']['sorting']}"] + [f"- screenshot {k}: reports/{v}" for k, v in r["html"]["screenshots"].items()]
    L += ["", "## Business-rule blockers (not validation failures)", ""] + [f"- {b}" for b in r["business_rule_blockers"]]
    L += ["", "Row-level mismatch list: reports/full_ph_row_mismatches.csv"]
    (REP / "full_ph_data_validation.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
