"""Independent validation of the "Price & SKU Sales Analysis" tab.

Expected values are recomputed here, not taken from the page payload:
  * rows (eBay ID x SKU), sales and LY/TY price from 04_HTML_Report/data/dataset.json (+ account_overrides via validate_html)
  * BOS Healthy and BLOS recommended_price from the RAW source responses in 05_Evidence/price_sources/<date>/
  * Lampshade / Wall plug category from the PH Sheet evidence 05_Evidence/ph_mapping/01_sheet_mapping.csv
  * PH ownership from ph_map.json (itself validated against the Sheet by validate_ph.py)
Then the page is driven in Chromium: every row of All PHs + Unassigned, each PH's row set, searches, sorting, the date
comparison (on a throw-away copy with a synthetic second snapshot), Excel downloads, console errors, broken values.
Regression: main-table data identical to the pre-change build; former ID-level LY/TY Price reproduced from the SKU rows.
Writes reports/price_tab_validation.json; exit 1 on any failure.
"""
import csv, datetime, gzip, json, os, pathlib, re, sys, tempfile, urllib.request, http.cookiejar
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import openpyxl
from playwright.sync_api import sync_playwright
from validate_html import HTML, DATA, acct_override

BASE = pathlib.Path(__file__).resolve().parent.parent
REP = BASE / "06_Validation" / "reports"
SNAPS = BASE / "04_HTML_Report" / "data" / "price_snapshots"
EVP = BASE / "05_Evidence" / "price_sources"
PH_MAP = BASE / "04_HTML_Report" / "data" / "ph_map.json"
SHEET = BASE / "05_Evidence" / "ph_mapping" / "01_sheet_mapping.csv"
BASELINE = BASE / "07_Archive" / "pre_price_tab_2026-10-07" / "eBay_Final_Output_Sales_September_2026.html"
sys.path.insert(0, str(BASE / "03_SQL"))
import extract_prices  # noqa: E402  (login + fetch helpers; credentials never printed)

CH = {("led_sone", "UK"): "ledsone", ("electricalsone", "UK"): "electricalsone", ("so_926407", "UK"): "sunsone",
      ("huettenlampen", "Germany"): "huettenlampen", ("ledsonede", "Germany"): "ledsonede",
      ("electricalsone", "Germany"): "electricalsone-de"}
FC = {"lampshade", "wall plug"}
PCOLS = ["eBay ID", "SKU", "Category", "Last Year Price", "This Year Price", "Current eBay Listing Price", "Bos Balance Price (Healthy)",
         "Ebay Price(Calculate with bos price to our ebay price)", "sku with Sales"]   # sku with Sales = Sep 2026 order count
NOPH = "__none__"
POSTAGE, RAW = {}, {}   # expected BLOS postage status per channel x SKU; raw screen row (evidence)
MCUR = {"UK": "£", "Germany": "€", "France": "€", "Italy": "€", "Spain": "€", "Ireland": "€", "US": "$", "Canada": "C$"}
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:800]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def f2(v):
    """en-GB 2-decimal display as Intl.NumberFormat does it: round the shortest round-trip decimal of the double
    (JS ToString == Python repr) half away from zero, e.g. 27.09/2 = 13.545 -> 13.55."""
    return "" if v is None else f"{Decimal(repr(float(v))).quantize(Decimal('0.01'), ROUND_HALF_UP):,}"


def r4(x):
    return None if x is None else round(x, 4)


def payload(path):
    h = path.read_text(encoding="utf-8")
    m = re.search(r"const DATA\s*=\s*", h)
    return h, m, json.JSONDecoder().raw_decode(h, m.end())


def category():
    cat = {}
    for c in csv.DictReader(open(SHEET, encoding="utf-8-sig")):
        b = re.sub(r"\s+", " ", c["block"]).strip()
        k = b.rsplit("-", 1)[1] if b.startswith("Rectangular Flush mount-") else b.split("-", 1)[1] if "-" in b else ""
        cat.setdefault(c["ebay_id"], set()).add(k.strip().lower())
    return cat


def raw_sources(day):
    d = EVP / day
    bos = json.load(gzip.open(d / "bos_balanceprice_feed.json.gz"))
    healthy = {r["seller_sku"]: str(r["net_healthy_gbp"]) for r in bos["rows"]
               if r["sku_status"] == "ASSIGNED" and r["net_healthy_gbp"] not in (None, "")}
    blos, api, mism = {}, {}, []
    for ch in set(CH.values()):
        api[ch] = {r["sku"]: str(r["recommended_price"]) for r in json.load(gzip.open(d / f"blos_ebay_{ch}.json.gz"))
                   if r["recommended_price"] not in (None, "")}
        f = d / f"blos_screen_{ch}.json.gz"
        if not f.exists():                         # channel not on the BLOS screen (sunsone): API only
            blos[ch] = api[ch]; continue
        blos[ch] = {}; POSTAGE[ch] = {}
        for r in json.load(gzip.open(f))["rows"]:
            s, e, v = r[0], r[2].replace(",", "").lstrip("£€"), r[3].replace(",", "").lstrip("£€")
            if v not in ("—", "-", ""):
                blos[ch][s] = v
                # independent restatement: RECOMMENDED PRICE above REC PRICE (EXC POSTAGE) = postage included
                POSTAGE[ch][s] = None if e in ("—", "-", "") else ("I" if float(v) > float(e) else "N")
                RAW[(ch, s)] = r
        mism += [(ch, k) for k, v in api[ch].items() if k not in blos[ch] or float(blos[ch][k]) != float(v)]
    return bos, healthy, blos, api, mism


def expected():
    rows = json.load(open(DATA, encoding="utf-8"))["rows"]
    ph = json.load(open(PH_MAP, encoding="utf-8"))["map"]
    cat = category()
    # Category column = PH Sheet block category of the eBay ID, display case (independent restatement)
    catd = {}
    for c_ in csv.DictReader(open(SHEET, encoding="utf-8-sig")):
        b = c_["block"]
        k_ = b.rsplit("-", 1)[1] if b.startswith("Rectangular Flush mount-") else b.split("-", 1)[1] if "-" in b else ""
        catd.setdefault(c_["ebay_id"], re.sub(r"\s+", " ", k_).strip())
    # grain (business decision 2026-10-07): eBay ID x SKU, i.e. this listing's own Completed orders only
    g = {}
    for r in rows:
        k = (r["ss"], r["mp"], r["id"])
        d = g.setdefault(k, {"acct": acct_override(r["acct"], r["mp"], r["id"]), "ss": r["ss"], "mp": r["mp"], "id": r["id"],
                             "sk": {}, "lsku": r.get("lsku") or []})
        if r["sku"]:
            d["sk"][r["sku"]] = r
    DDS = json.load(open(BASE / "04_HTML_Report" / "data" / "daily_detail.json", encoding="utf-8"))["skus"]
    out = []
    for d in g.values():
        skus = sorted(d["sk"]) or list(d["lsku"])
        octx = sorted(s for s in DDS.get(f'{d["id"]}|{d["mp"]}', {}) if s and s not in skus)   # "Oct order" rows
        fc = bool(cat.get(d["id"], set()) & FC)
        base = {"id": d["id"], "acct": d["acct"], "mp": d["mp"], "ph": ph.get(d["id"], []), "ch": CH.get((d["ss"], d["mp"])),
                "cat": catd.get(d["id"], "")}
        if not skus and not octx:      # no SKU at all; with October-order SKUs those are listed instead (build.price_tab)
            out.append({**base, "sku": "", "rule": -1, "s": None, "fc": fc, "ss": d["ss"], "own": None})
        for s in skus:
            own = d["sk"].get(s)
            a = None if own is None else (own["LY"]["pxq"], own["LY"]["units"], own["TY"]["pxq"], own["TY"]["units"],
                                          own["LY"]["orders"], own["TY"]["orders"])
            out.append({**base, "sku": s, "rule": 0 if "+" not in s else 1 if fc else 2, "fc": fc, "ss": d["ss"],
                        "s": a if a and (a[1] or a[3] or a[4] or a[5]) else None, "own": a})
        for s in octx:
            out.append({**base, "sku": s, "rule": 0 if "+" not in s else 1 if fc else 2, "fc": fc, "ss": d["ss"], "s": None, "own": None, "oct": 1})
    return out


def exact_price(pxq, units):
    """Σ(item price × qty) ÷ Σ qty in exact decimals, half-up to 2 dp (28.335 -> 28.34); None when no units."""
    if not units:
        return None
    return float((Decimal(repr(round(pxq, 4))) / Decimal(repr(round(units, 4)))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def values(e, healthy, blos):
    key = e["sku"] if e["rule"] == 0 else e["sku"].split("+", 1)[0] if e["rule"] == 1 else None
    bos = healthy.get(key) if key else None
    eb = blos.get(e["ch"], {}).get(e["sku"]) if e["ch"] and e["sku"] else None
    s = e["s"]
    ly = exact_price(s[0], s[1]) if s else None
    ty = exact_price(s[2], s[3]) if s else None
    sal = "" if not e["sku"] else "Yes" if s and (s[4] > 0 or s[5] > 0) else "No"
    # Sep Orders = distinct Completed orders of this eBay ID x SKU (0 = none, "" = no SKU)
    lyo = "" if not e["sku"] else (int(s[4]) if s else 0)
    tyo = "" if not e["sku"] else (int(s[5]) if s else 0)
    return {"lyp": ly, "typ": ty, "bos": bos, "eb": eb, "sal": sal, "lyo": lyo, "tyo": tyo, "cat": e.get("cat", "")}


SCRAPE = """()=>[...document.querySelectorAll('#pt tbody tr[data-id]')].map(r=>{const o={id:'',acct:r.dataset.acct,mp:r.dataset.mp,
 sku:r.dataset.sku,rule:+r.dataset.rule};for(const c of r.cells){const k=c.dataset.k;const f=c.querySelector(':scope > span[title]');
 o[k==='sku'?'sku_txt':k]=k==='id'?(c.querySelector('.eid')?c.querySelector('.eid').textContent:''):(k==='bos'||k==='eb')?(f?f.textContent:''):c.innerText.trim().replace(/^(C\\$|[£€$])/,'');
 if(k==='eb'){const ps=c.querySelector('.pst');o.eb_post=ps?ps.dataset.post:null;o.eb_post_txt=ps?ps.textContent:'';}
 if(k==='lyp'||k==='typ'||k==='bos'||k==='eb'){const cu=c.querySelector(':scope > .cur');o[k+'_cur']=cu?cu.textContent:'';}
 if(k==='bos'||k==='eb'){const w=c.querySelector('.was');o[k+'_was']=w?w.textContent:null;}}return o;})"""


def scrape_all(pg):
    pg.select_option("#ppSize", "500"); pg.wait_for_timeout(150)
    out = []
    while True:
        out += pg.evaluate(SCRAPE)
        if pg.is_disabled("#ppNext"): break
        pg.click("#ppNext"); pg.wait_for_timeout(60)
    return out


def keyed(rows):
    """Carry the eBay ID down to continuation rows (the page shows it on the first SKU row of each ID only)."""
    cur = None
    for c in rows:
        c["shown_id"] = c["id"]
        cur = c["id"] or cur
        c["key"] = (cur, c["acct"], c["mp"], c["sku"])
    return rows


def counts(pg):
    t = pg.inner_html("#pCount")
    return int(re.search(r'data-n="(\d+)"', t).group(1)), int(re.search(r'data-ids="(\d+)"', t).group(1))


def main():
    snaps = sorted(p.stem for p in SNAPS.glob("*.json"))
    day = snaps[-1]
    snap = json.load(open(SNAPS / f"{day}.json", encoding="utf-8"))

    # ---------------- A. sources ----------------
    check("price snapshots exist; latest has raw source evidence", snaps and all((EVP / d / "bos_balanceprice_feed.json.gz").exists() for d in snaps), snaps)
    bos_raw, healthy, blos, blos_api, mism = raw_sources(day)
    check("BLOS screen RECOMMENDED PRICE == API recommended_price for every SKU the API prices", not mism, mism[:5])
    need = {"seller_sku", "sku_status", "net_healthy_gbp", "category"}
    check("BOS feed ok, required fields present", bos_raw.get("ok") and need <= set(bos_raw["rows"][0]), sorted(bos_raw["rows"][0])[:12])
    check("snapshot BOS values == raw feed (net_healthy_gbp, ASSIGNED)", {k: v["v"] for k, v in snap["bos"]["healthy"].items()} == healthy)
    check("snapshot BLOS values == raw BLOS screen / API evidence", all(snap["blos"]["channels"][c]["prices"] == blos[c] for c in blos))
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    u, p = extract_prices.blos_creds()
    try:
        extract_prices.get(op, extract_prices.BLOS + "/api/auth/login", json.dumps({"username": u, "password": p}).encode())
        live = json.loads(extract_prices.get(op, extract_prices.BLOS + "/api/listing-channel-price?platform=ebay&channel=ledsone"))
        check("BLOS API accessible now (login + ledsone channel)", len(live) > 0 and "recommended_price" in live[0], len(live))
    except SystemExit as e:
        check("BLOS API accessible now (login + ledsone channel)", False, e)
    html_text = HTML.read_text(encoding="utf-8")
    files = [HTML, *SNAPS.glob("*.json"), *(BASE / "04_HTML_Report" / "template").glob("*"), *(BASE / "03_SQL").glob("*.py"), *(REP.glob("*.json"))]
    leak = [f.name for f in files if p in f.read_text(encoding="utf-8", errors="ignore")]
    leak += [f.name for f in EVP.rglob("*.gz") if p.encode() in gzip.open(f).read()]
    check("BLOS password not present in HTML, template, scripts, snapshots, reports or evidence", not leak, leak)
    del u, p

    # ---------------- B. mapping ----------------
    E = expected()
    keys = [(e["id"], e["acct"], e["mp"], e["sku"]) for e in E]
    check("no duplicate eBay ID + SKU rows (expected)", len(keys) == len(set(keys)), len(keys) - len(set(keys)))
    _, _, (D, _) = payload(HTML)
    main_pairs = Counter((a[2], a[0], a[1], s) for a in D["rows"] for s in (a[3] or a[8] or [""]))
    sep_keys = Counter((e["id"], e["acct"], e["mp"], e["sku"]) for e in E if not e.get("oct"))
    has_sep = {k[:3] for k in sep_keys}
    sep_keys.update({(e["id"], e["acct"], e["mp"], ""): 1 for e in E if e.get("oct") and (e["id"], e["acct"], e["mp"]) not in has_sep})
    check("expected non-'Oct order' rows == the main dashboard's eBay ID x SKU pairs (nothing lost, nothing added)", sep_keys == main_pairs,
          (sum(sep_keys.values()), sum(main_pairs.values())))
    DDS_ = json.load(open(BASE / "04_HTML_Report" / "data" / "daily_detail.json", encoding="utf-8"))["skus"]
    oct_bad = [k for k in keys if k[3] and k not in main_pairs and not (
        (lambda v: v and any(d > 30 for per in v for d, *_ in per) and not any(d <= 30 for per in v for d, *_ in per))(DDS_.get(f"{k[0]}|{k[2]}", {}).get(k[3])))]
    check(f"'Oct order' rows ({sum(1 for e in E if e.get('oct')):,}) = exactly the eBay ID x SKUs with Completed orders only on the October days (daily_detail.json)",
          not oct_bad and all(e.get("oct") for e in E if (e["id"], e["acct"], e["mp"], e["sku"]) not in main_pairs), oct_bad[:5])
    V = [values(e, healthy, blos) for e in E]
    st = Counter()
    for e, v in zip(E, V):
        if not e["sku"]: st["IDs with no SKU"] += 1; continue
        st["SKU rows"] += 1
        st["single SKU rows"] += e["rule"] == 0; st["single with BOS"] += e["rule"] == 0 and v["bos"] is not None
        st["combo rows"] += e["rule"] > 0
        st["Lampshade/Wall plug combo rows"] += e["rule"] == 1; st["Lampshade/Wall plug combos with BOS"] += e["rule"] == 1 and v["bos"] is not None
        st["other-category combo rows"] += e["rule"] == 2; st["other combos with BOS (must be 0)"] += e["rule"] == 2 and v["bos"] is not None
        st["rows with eBay Price"] += v["eb"] is not None; st["rows with no BLOS channel"] += not e["ch"]
        st["rows with LY Price"] += v["lyp"] is not None; st["rows with TY Price"] += v["typ"] is not None
        st["SKU rows with sales = Yes"] += v["sal"] == "Yes"
    # why blanks are blank (every blank must have a source reason)
    blos_rows = {}
    for ch in blos:
        f = EVP / day / f"blos_screen_{ch}.json.gz"
        blos_rows[ch] = {r[0] for r in json.load(gzip.open(f))["rows"]} if f.exists() else set(blos_api[ch])
    def eb_reason(e, v):
        if v["eb"] is not None: return "populated"
        if not e["ch"]: return f"no BLOS channel ({e['acct']} {e['mp']})"
        if e["ch"] not in blos_rows or not blos_rows[e["ch"]]: return f"BLOS channel {e['ch']} has no rows"
        if e["sku"] not in blos_rows[e["ch"]]: return "SKU not listed in the BLOS channel"
        return "listed in BLOS but BLOS shows no Recommended Price (—)"
    def bos_reason(e, v):
        if v["bos"] is not None: return "populated"
        if e["rule"] == 2: return "N/A: combo outside Lampshade / Wall plug"
        return "lookup SKU not in BOS feed (feed has 334 SKUs)"
    for e, v in zip(E, V):
        if not e["sku"]: continue
        st["eBay Price: " + eb_reason(e, v)] += 1
        st["BOS: " + bos_reason(e, v)] += 1
        st["LY blank although LY units"] += bool(e["s"] and e["s"][1] and v["lyp"] is None)
        st["TY blank although TY units"] += bool(e["s"] and e["s"][3] and v["typ"] is None)
    check("no LY/TY Price blank where the SKU has Completed units", st["LY blank although LY units"] == 0 and st["TY blank although TY units"] == 0)
    ev = EVP / day / "utharsika_reconciliation.csv"
    with open(ev, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["eBay ID", "SKU", "PH", "Category", "Account", "Marketplace", "BLOS Channel", "LY Price", "TY Price", "BOS Healthy (source)",
                    "BOS lookup SKU", "BLOS Recommended Price", "Sep 2025 Orders", "Sep 2026 Orders", "LY units", "TY units", "BOS status", "BLOS status"])
        for e, v in zip(E, V):
            if "utharsika" not in e["ph"]: continue
            w.writerow([e["id"], e["sku"], "|".join(e["ph"]), v["cat"], e["acct"], e["mp"], e["ch"] or "", f2(v["lyp"]), f2(v["typ"]), v["bos"] or "",
                        (e["sku"] if e["rule"] == 0 else e["sku"].split("+")[0] if e["rule"] == 1 else ""), v["eb"] or "", v["lyo"], v["tyo"],
                        f"{e['s'][1]:g}" if e["s"] else "", f"{e['s'][3]:g}" if e["s"] else "", bos_reason(e, v), eb_reason(e, v)])
    uth = [(e, v) for e, v in zip(E, V) if "utharsika" in e["ph"] and e["sku"]]
    in_blos = [(e, v) for e, v in uth if e["ch"] in blos_rows and e["sku"] in blos_rows[e["ch"]]]
    check("Utharsika: every SKU BLOS prices in its channel shows that Recommended Price",
          all(v["eb"] == blos[e["ch"]].get(e["sku"]) for e, v in in_blos), len(in_blos))
    st["Utharsika SKU rows"] = len(uth); st["Utharsika rows listed in their BLOS channel"] = len(in_blos)
    st["Utharsika rows with BLOS Recommended Price"] = sum(1 for _, v in uth if v["eb"] is not None)
    st["Utharsika rows with BOS Healthy"] = sum(1 for _, v in uth if v["bos"] is not None)
    st["Utharsika rows with LY / TY Price"] = f"{sum(1 for _, v in uth if v['lyp'] is not None)} / {sum(1 for _, v in uth if v['typ'] is not None)}"
    st["unique eBay IDs"] = len({(e["id"], e["acct"], e["mp"]) for e in E})
    st["unique SKUs"] = len({e["sku"] for e in E if e["sku"]})
    check("no '+' combo outside Lampshade / Wall plug gets a BOS price", st["other combos with BOS (must be 0)"] == 0)
    lw = [e for e in E if e["rule"] == 1]
    check("Lampshade / Wall plug combos sit only on IDs in those Sheet categories", all(e["fc"] for e in lw) and all(not e["fc"] for e in E if e["rule"] == 2))

    # ---------------- D. regression vs the pre-change build ----------------
    _, _, (O, _) = payload(BASELINE)
    if O["generated_at"] == D["generated_at"]:
        check("main-table data identical to the pre-change build (all 11 fields, every row)", [a[:11] for a in O["rows"]] == [a[:11] for a in D["rows"]])
    else:   # a newer extract (daily refresh): identity with the old build no longer applies; source checks cover it
        print(f"INFO main-table identity check skipped: extract {D['generated_at']} is newer than baseline {O['generated_at']}")
    # eBay ID x SKU grain: every source pair with units has a price; exact-decimal rounding (28.335 -> 28.34)
    src_pairs = {p: {(e["id"], e["acct"], e["mp"], e["sku"]) for e in E if e["own"] and e["own"][1 if p == "LY" else 3]} for p in ("LY", "TY")}
    shown = {p: {(e["id"], e["acct"], e["mp"], e["sku"]) for e, v in zip(E, V) if v["lyp" if p == "LY" else "typ"] is not None} for p in ("LY", "TY")}
    check("LY/TY Price shown for exactly the eBay ID x SKU pairs with Completed units (grain B)",
          src_pairs == shown, {p: (len(src_pairs[p]), len(shown[p])) for p in src_pairs})
    st["eBay ID x SKU pairs with LY units / TY units"] = f"{len(src_pairs['LY'])} / {len(src_pairs['TY'])}"
    tie = [(e, v) for e, v in zip(E, V) if e["id"] == "164624648612" and e["sku"] == "ENC6040"]
    check("exact-decimal rounding: 164624648612 / ENC6040 LY = 340.02 / 12 = 28.335 -> 28.34", tie and f2(tie[0][1]["lyp"]) == "28.34", tie[:1])
    lost = [(e["id"], e["sku"]) for e in E if e["own"] and (e["own"][1] or e["own"][3]) and not e["s"]]
    check("every SKU row with its own Completed units has a sales record (no lost orders)", not lost, lost[:5])

    # ---------------- C/E. page ----------------
    exp = {k: (e, v) for k, e, v in zip(keys, E, V)}
    def disp(e, v):
        return {"lyp": f2(v["lyp"]), "typ": f2(v["typ"]), "bos": f2(v["bos"]), "eb": f2(v["eb"]),
                "tyo": str(v["tyo"]), "cat": v["cat"],
                "sku_txt": (e["sku"] + ("Oct order" if e.get("oct") else "")) or "Not available in source data",
                # currency: LY/TY in the marketplace currency, BOS in GBP, eBay Price in the BLOS channel's currency
                "lyp_cur": MCUR.get(e["mp"], "") if v["lyp"] is not None else "", "typ_cur": MCUR.get(e["mp"], "") if v["typ"] is not None else "",
                "bos_cur": "£" if v["bos"] is not None else "", "eb_cur": ("€" if e["mp"] == "Germany" else "£") if v["eb"] is not None else ""}
    phs = json.load(open(PH_MAP, encoding="utf-8"))["phs"]
    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        errors = []; pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(HTML.as_uri()); pg.wait_for_selector("#t tbody tr[data-id]")
        pg.fill("#sdTo", "2026-09-30"); pg.wait_for_timeout(500)
        main_hd = pg.eval_on_selector_all("#t thead th", "e=>e.map(x=>x.textContent.trim())")
        check("main table no longer shows LY Price / TY Price", "LY Price" not in main_hd and "TY Price" not in main_hd, main_hd)
        pg.click("#tabBtnPrice"); pg.wait_for_selector("#pt tbody tr")
        check("new tab opens; main tab hidden", pg.is_visible("#tabPrice") and not pg.is_visible("#tabMain"))
        END = json.load(open(BASE / "04_HTML_Report" / "data" / "daily_detail.json", encoding="utf-8"))["end_date"]
        cal = pg.evaluate("()=>{const f=document.querySelector('#psFrom'),t=document.querySelector('#psTo');return [f.type,t.type,f.min,t.max,f.value,t.value,!!f.closest('.card')]}")
        check(f"sales calendar (type=date) inside the Price & SKU card, 1 Sep 2026 -> {END}, default the whole period, October mentioned",
              cal == ["date", "date", "2026-09-01", END, "2026-09-01", END, True] and ("includes October data" in pg.inner_text("#psMsg")) == (END > "2026-09-30"), cal)
        # the September checks below compare with the monthly extract: select 1-30 Sep
        pg.fill("#psTo", "2026-09-30"); pg.wait_for_timeout(600)
        hd = pg.eval_on_selector_all("#pt thead th", "e=>e.map(x=>x.firstChild.textContent.trim())")
        check("tab columns are exactly the required columns (+ Current eBay Listing Price after This Year Price)", hd == PCOLS, hd)
        labels = pg.eval_on_selector_all("#tabPrice .filters label", "e=>e.map(x=>x.childNodes[0].textContent.trim())")
        check("controls: PH, Category, Price snapshot From/To, Search eBay ID, Search SKU + Download Excel + Clear Filters",
              labels == ["PH", "Category", "Price snapshot From", "Price snapshot To", "Search eBay ID", "Search SKU"] and pg.is_visible("#pDl") and pg.is_visible("#pClr"), labels)
        check("PH options identical to the main PH filter", pg.eval_on_selector_all("#pPh option", "e=>e.map(x=>x.value)") == pg.eval_on_selector_all("#fPh option", "e=>e.map(x=>x.value)"))
        check("date options = stored snapshots only", pg.eval_on_selector_all("#pTo option", "e=>e.map(x=>x.value)") == snaps, snaps)
        if len(snaps) == 1:
            check("single snapshot -> earlier dates shown as Not Available", "Not Available" in pg.inner_text("#pNote"), pg.inner_text("#pNote")[:200])

        def full(key):
            pg.select_option("#pPh", key); pg.wait_for_timeout(200)
            return keyed(scrape_all(pg))
        bad, seen, broken, idrule = [], Counter(), [], []
        for key in ("", NOPH):
            rows = full(key)
            prev = None
            for i, c in enumerate(rows):
                k = c["key"]; seen[k] += 1
                grp = k[:3]
                if (c["shown_id"] != "") != (i % 500 == 0 or grp != prev): idrule.append((i, k, c["shown_id"]))
                prev = grp
                if k not in exp: bad.append(("unexpected", k)); continue
                e, v = exp[k]; want = disp(e, v)
                for f, w in want.items():
                    if c[f] != w: bad.append((k, f, w, c[f]))
                if c["rule"] != e["rule"]: bad.append((k, "rule", e["rule"], c["rule"]))
                for f in ("lyp", "typ", "bos", "eb", "tyo", "cat", "sku_txt"):
                    if re.search(r"undefined|NaN|Infinity|null", c[f] or ""): broken.append((k, f, c[f]))
        allowed = [k for k, (e, _) in exp.items()]
        check(f"All PHs + Unassigned: every row shown once and matches the recompute ({len(seen):,} rows, 7 columns)",
              not bad and set(seen) == set(allowed) and max(seen.values()) == 1, bad[:6])
        check("eBay ID shown only on the first SKU row of each ID (and at the top of a page)", not idrule, idrule[:5])
        check("no undefined / NaN / Infinity / null rendered", not broken, broken[:5])
        rendered_bos_other = [k for k in seen if exp[k][0]["rule"] == 2 and exp[k][1]["bos"] is not None]
        check("page: 0 other-category combo rows with a BOS price", not rendered_bos_other)
        style = pg.evaluate("""()=>{const P=['font-size','font-weight','text-transform','letter-spacing','padding-top','padding-left','border-bottom-width','border-bottom-style','color','background-color'];
          const cs=(s)=>{const e=document.querySelector(s);if(!e)return null;const c=getComputedStyle(e);return Object.fromEntries(P.map(p=>[p,c.getPropertyValue(p)]));};
          return {th:[cs('#t thead th:nth-child(3)'),cs('#pt thead th:nth-child(4)')],td:[cs('#t tbody tr[data-id] td:nth-child(3)'),cs('#pt tbody tr[data-id] td:nth-child(4)')]};}""")
        box = pg.evaluate("""()=>[...document.querySelectorAll('#pt tbody tr.first')].slice(0,60).map(tr=>{const c=tr.querySelector('td.idbox');
            let n=1,x=tr.nextElementSibling;while(x&&!x.classList.contains('first')){n++;x=x.nextElementSibling;}
            const cs=getComputedStyle(c);return [+c.rowSpan,n,cs.textAlign,cs.verticalAlign,cs.position];})""")
        # ---- Utharsika: BLOS Recommended Price + postage status, every eBay ID x SKU ----
        pg.select_option("#pPh", "utharsika"); pg.wait_for_timeout(300)
        urows = keyed(scrape_all(pg))
        ev_rows, ubad = [], []
        for c in urows:
            e, v = exp[c["key"]]
            if not e["sku"]:
                continue
            exp_post = POSTAGE.get(e["ch"], {}).get(e["sku"]) if v["eb"] is not None else None
            shown_price = c["eb"]; shown_post = c.get("eb_post") or None
            ok_ = shown_price == f2(v["eb"]) and (shown_post == exp_post) and (v["eb"] is None or c.get("eb_post_txt"))
            if not ok_:
                ubad.append((c["key"], f2(v["eb"]), shown_price, exp_post, shown_post))
            raw = RAW.get((e["ch"], e["sku"]))
            ev_rows.append([e["id"], e["sku"], e["acct"], e["mp"], e["ch"] or "", v["eb"] or "", raw[2] if raw else "", raw[3] if raw else "", raw[5] if raw and len(raw) > 5 else "",
                            {"I": "Postage Included", "N": "Postage Not Included", None: ""}[exp_post] if v["eb"] is not None else "",
                            (("£" if e["mp"] != "Germany" else "€") + shown_price) if shown_price else "", c.get("eb_post_txt") or "",
                            eb_reason(e, v), "PASS" if ok_ else "FAIL"])
        with open(EVP / day / "utharsika_blos_postage_validation.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["eBay ID", "SKU", "Account", "Marketplace", "BLOS channel", "BLOS Recommended Price (source)", "BLOS REC PRICE (EXC POSTAGE)",
                        "BLOS RECOMMENDED PRICE (screen)", "BLOS CARRIER CHARGE", "Expected postage status", "Displayed price", "Displayed postage status",
                        "BLOS status / blank reason", "Validation"])
            w.writerows(ev_rows)
        up = [r for r in ev_rows if r[5] != ""]
        st["Utharsika eBay IDs"] = len({(c["key"][0], c["key"][1], c["key"][2]) for c in urows})
        st["Utharsika BLOS Recommended Price populated"] = len(up)
        st["Utharsika Postage Included"] = sum(1 for r in up if r[11] == "Postage Included")
        st["Utharsika Postage Not Included"] = sum(1 for r in up if r[11] == "Postage Not Included")
        st["Utharsika postage status unavailable"] = sum(1 for r in up if r[11] not in ("Postage Included", "Postage Not Included"))
        st["Utharsika source-found but dashboard-missing BLOS price"] = sum(1 for r in ev_rows if r[5] != "" and r[10] == "")
        check(f"Utharsika: every eBay ID x SKU shows the exact BLOS Recommended Price and the source postage status ({len(ev_rows)} rows)", not ubad, ubad[:5])
        pg.select_option("#pPh", ""); pg.wait_for_timeout(300)
        check("eBay ID drawn once per group in one merged box, centred (rowspan = SKU rows of the group)",
              box and all(r[0] == r[1] and r[2] == "center" and r[3] == "middle" and r[4] == "sticky" for r in box), box[:3])
        check("'sku with Sales' shows the Sep 2026 order count (digits), never Yes/No",
              all(re.fullmatch(r"\d+", c["tyo"] or "") or (not c["sku"] and c["tyo"] == "") for c in rows) and not any(c["tyo"] in ("Yes", "No") for c in rows),
              [c["tyo"] for c in rows[:5]])
        u_gt_o = [k for k, (e, v) in exp.items() if e["s"] and e["s"][3] > e["s"][5] > 0]
        check("Sep Orders count orders, not units (rows where TY units > TY orders show the order count)",
              u_gt_o and all(str(int(exp[k][0]["s"][5])) == str(exp[k][1]["tyo"]) for k in u_gt_o) and not bad, f"{len(u_gt_o)} rows with units > orders")
        ccur = [(c["key"], f, c.get(f + "_cur"), w) for c in rows for f in ("lyp", "typ", "bos", "eb")
                if c["key"] in exp for w in [disp(*exp[c["key"]])[f + "_cur"]] if c.get(f + "_cur") != w]
        check("currency symbol per value (marketplace / GBP for BOS / BLOS channel currency)", not ccur, ccur[:4])
        # sticky: eBay ID column frozen left and header frozen top inside the scroll box
        pg.select_option("#pPh", "utharsika"); pg.wait_for_timeout(200); pg.set_viewport_size({"width": 760, "height": 900}); pg.wait_for_timeout(200)
        # scroll down to the 2nd eBay ID group (a fixed 300px can fall inside one tall merged ID cell, e.g. 52 SKU rows)
        pg.eval_on_selector("#pWrap", "e=>{const f=e.querySelectorAll('#pt tbody tr.first');e.scrollLeft=600;e.scrollTop=f.length>1?f[1].offsetTop-100:300}"); pg.wait_for_timeout(200)
        stick = pg.evaluate("""()=>{const w=document.querySelector('#pWrap').getBoundingClientRect();
          const td=[...document.querySelectorAll('#pt tbody tr.first td:first-child')].find(c=>{const r=c.getBoundingClientRect();return r.top>w.top+40&&r.top<w.bottom-40;});
          const th=document.querySelector('#pt thead th:first-child').getBoundingClientRect(), th2=document.querySelector('#pt thead th:nth-child(5)').getBoundingClientRect();
          return {scrolledX:document.querySelector('#pWrap').scrollLeft, idLeft:td?td.getBoundingClientRect().left-w.left:null, idText:td?td.innerText.slice(0,12):null,
                  thTop:th.top-w.top, thLeft:th.left-w.left, th5Top:th2.top-w.top, topCell:document.elementFromPoint(th.left+5,th.top+5).closest('th')===document.querySelector('#pt thead th:first-child')};}""")
        check("eBay ID column frozen left and header frozen top while scrolled (no overlap)",
              stick["scrolledX"] > 0 and stick["idLeft"] is not None and abs(stick["idLeft"]) <= 2 and re.fullmatch(r"\d{12}", stick["idText"] or "")
              and abs(stick["thTop"]) <= 2 and abs(stick["th5Top"]) <= 2 and abs(stick["thLeft"]) <= 2 and stick["topCell"], stick)
        pg.set_viewport_size({"width": 1600, "height": 1000}); pg.select_option("#pPh", ""); pg.wait_for_timeout(200)

        badph = []
        for key in [""] + phs + [NOPH]:
            pg.select_option("#pPh", key); pg.wait_for_timeout(120)
            sel = (lambda e: bool(e["ph"])) if key == "" else (lambda e: not e["ph"]) if key == NOPH else (lambda e, key=key: key in e["ph"])
            want = {k for k, (e, _) in exp.items() if sel(e)}
            n, nid = counts(pg)
            if n != len(want) or nid != len({k[:3] for k in want}): badph.append((key or "All PHs", len(want), n)); continue
            if key not in ("", NOPH) and n and {c["key"] for c in keyed(scrape_all(pg))} != want: badph.append((key, "row set"))
        check(f"PH filter: All PHs, each of {len(phs)} PHs and Unassigned show exactly their rows and IDs", not badph, badph[:5])
        pg.select_option("#pPh", "utharsika"); pg.wait_for_timeout(150)
        check("PH selection syncs with the main tab", pg.input_value("#fPh") == "utharsika")

        pg.select_option("#pPh", ""); pg.fill("#pId", "1645252"); pg.wait_for_timeout(400)
        want = sorted(k for k, (e, _) in exp.items() if e["ph"] and "1645252" in e["id"])
        got = sorted(c["key"] for c in keyed(scrape_all(pg)))
        check("eBay ID search (partial '1645252') shows exactly the matching rows", got == want and len(got) > 0, (len(want), len(got)))
        pg.fill("#pId", ""); pg.fill("#pSku", "rpr44wh"); pg.wait_for_timeout(400)
        n, _ = counts(pg)
        check("SKU search (case-insensitive 'rpr44wh') count", n == sum(1 for k, (e, _) in exp.items() if e["ph"] and "rpr44wh" in e["sku"].lower()), n)
        pg.fill("#pSku", "zzz-none"); pg.wait_for_timeout(400)
        check("no-match search shows a clear message", counts(pg)[0] == 0 and "No eBay ID or SKU matches" in pg.inner_text("#pt"))
        pg.fill("#pId", ""); pg.fill("#pSku", ""); pg.select_option("#pPh", ""); pg.wait_for_timeout(400)
        pg.select_option("#pCat", "Lampshade"); pg.wait_for_timeout(300)
        n_cat = counts(pg)[0]; want_cat = sum(1 for k, (e, v) in exp.items() if e["ph"] and v["cat"] == "Lampshade")
        pg.select_option("#pCat", "__nocat__"); pg.wait_for_timeout(300); n_no = counts(pg)[0]
        opts = pg.eval_on_selector_all("#pCat option", "e=>e.map(x=>x.value)")
        check("Category dropdown: options = every PH Sheet category + All + No category; filter counts match",
              n_cat == want_cat and opts[0] == "" and opts[-1] == "__nocat__" and set(opts[1:-1]) == {v["cat"] for e, v in exp.values() if v["cat"]}
              and n_no == sum(1 for k, (e, v) in exp.items() if e["ph"] and not v["cat"]), (n_cat, want_cat, n_no, len(opts)))
        pg.select_option("#pCat", "")
        pg.click("#pClr"); pg.wait_for_timeout(300)
        check("Clear Filters resets PH, dates, searches, sales calendar (1 Sep 2026 -> latest order date)", pg.input_value("#pPh") == "" and pg.input_value("#pId") == "" and pg.input_value("#pSku") == ""
              and pg.input_value("#pTo") == snaps[-1] and pg.input_value("#psFrom") == "2026-09-01" and pg.input_value("#psTo") == END)
        # the sort / Excel checks below compare with the September (monthly extract) values: select 1-30 Sep again
        pg.fill("#psTo", "2026-09-30"); pg.wait_for_timeout(600)

        pg.select_option("#pPh", "utharsika"); pg.wait_for_timeout(200)
        pg.click("#pt th[data-k=bos]"); pg.wait_for_timeout(300)
        vals = [c["bos"] for c in scrape_all(pg)]
        nums = [float(x.replace(",", "")) for x in vals if x]
        check("sort by BOS ascending, blanks last", nums == sorted(nums) and all(not x for x in vals[len(nums):]), vals[:5])
        pg.click("#pt th[data-k=bos]"); pg.wait_for_timeout(300)
        vals = [c["bos"] for c in scrape_all(pg)]; nums = [float(x.replace(",", "")) for x in vals if x]
        check("sort by BOS descending, blanks last", nums == sorted(nums, reverse=True) and all(not x for x in vals[len(nums):]))
        pg.click("#pt th[data-k=bos]"); pg.wait_for_timeout(300)

        # ---- Excel downloads ---- (sales calendar set to 1-30 Sep above; headers carry that range)
        DEF_RNG = "(1–30 Sep 2026)"
        def download(ph):
            pg.select_option("#pPh", ph); pg.wait_for_timeout(200)
            order = keyed(scrape_all(pg))
            with pg.expect_download() as dl: pg.click("#pDl")
            path = pathlib.Path(tempfile.gettempdir()) / dl.value.suggested_filename; dl.value.save_as(path)
            wb = openpyxl.load_workbook(path)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            errs = []
            if ws.title != "Price & SKU Sales Analysis": errs.append(("sheet", ws.title))
            if [re.sub(r" \(\d.*\)$", "", str(c)) for c in rows[0]] != PCOLS or DEF_RNG not in str(rows[0][4]): errs.append(("header", rows[0]))
            if ws.freeze_panes != "A2": errs.append(("freeze", ws.freeze_panes))
            if len(rows) - 1 != len(order): errs.append(("rows", len(rows) - 1, len(order)))
            prev = None
            for i, (x, c) in enumerate(zip(rows[1:], order)):
                e, v = exp[c["key"]]
                first = i == 0 or c["key"][:3] != prev   # Excel: ID once per ID, no page-top repeats
                want = [e["id"] if first else None, e["sku"] or None, (re.sub(r"\s+", " ", v["cat"]) or None),
                        None if v["lyp"] is None else round(v["lyp"], 4), None if v["typ"] is None else round(v["typ"], 4),
                        None if v["bos"] is None else float(v["bos"]), None if v["eb"] is None else float(v["eb"]),
                        None if v["tyo"] == "" else v["tyo"]]
                got = list(x)
                lp_got, lp_want = got.pop(5), LPJ.get(f'{e["id"]}|{e["mp"]}|{e["sku"]}')
                if (lp_got is None) != (lp_want is None) or (lp_got is not None and abs(lp_got - float(lp_want[0])) > 1e-9):
                    errs.append((i + 2, "listing price", lp_want, lp_got))
                if got[2] is not None: got[2] = re.sub(r"\s+", " ", got[2])
                if got[:3] != want[:3] or got[7:] != want[7:] or any((a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9) for a, b in zip(got[3:7], want[3:7])):
                    errs.append((i + 2, want, got))
                prev = c["key"][:3]
            if any(isinstance(v, str) and re.search(r"undefined|NaN|Infinity", v) for r in rows for v in r): errs.append("broken text")
            return path.name, len(rows) - 1, errs
        # Current eBay Listing Price: listing_prices.json spot-checked against a direct ledsone query (same match rule)
        import random, psycopg2
        LPF = json.load(open(BASE / "04_HTML_Report" / "data" / "listing_prices.json", encoding="utf-8")); LPJ = LPF["prices"]
        smp = random.Random(7).sample(sorted(LPJ), min(300, len(LPJ)))
        con = psycopg2.connect(os.environ["WLP_SOURCE_DB_URL"]); con.set_session(readonly=True, autocommit=True); cur = con.cursor()
        cur.execute("""SELECT item_id||'|'||site||'|'||sku, price::text, currency, is_child FROM listings.ebay_listings
                       WHERE wrong_sku=0 AND is_ended=0 AND item_id||'|'||site||'|'||sku = ANY(%s)""", (smp,))
        db = {}
        for k, pr, cu, ch in cur.fetchall(): db.setdefault(k, []).append((ch, pr, cu))
        con.close()
        lp_bad = [(k, LPJ[k], db.get(k)) for k in smp if [list(x[1:]) for x in ([y for y in db.get(k, []) if y[0] == 1] or db.get(k, []))][:1] != [LPJ[k]]]
        check(f"Current eBay Listing Price: {len(smp)} random eBay ID x SKU keys == direct ledsone listings.ebay_listings query "
              f"({LPF['matched']:,} of {LPF['dashboard_keys']:,} rows priced, synced {LPF['synced_at']})", not lp_bad, lp_bad[:4])
        for ph in ("utharsika", ""):
            name, n, errs = download(ph)
            check(f"Excel download ({ph or 'All PHs'}): opens, sheet/header/freeze OK, {n:,} rows == page, values + ID-once format exact", not errs, errs[:4])
        # ---- sales calendar: 1 Oct -> END and 16 Sep -> END, every PH-owned row vs a direct order_transaction query ----
        import psycopg2
        con = psycopg2.connect(os.environ["DATABASE_URL"]); con.set_session(readonly=True, autocommit=True); cur = con.cursor()
        pg.select_option("#pPh", ""); pg.fill("#pId", ""); pg.fill("#pSku", ""); pg.wait_for_timeout(300)
        rb, nrows = [], 0
        for f, t in [("2026-10-01", END), ("2026-09-16", END)] if END > "2026-09-30" else [("2026-09-16", "2026-09-30")]:
            pg.fill("#psFrom", "2026-09-01"); pg.fill("#psTo", t); pg.fill("#psFrom", f); pg.wait_for_timeout(700)
            ly0, ly1 = "2025" + f[4:], "2025" + t[4:]
            cur.execute("""SELECT item_id, market_place, sku, CASE WHEN order_date < '2026-01-01' THEN 'LY' ELSE 'TY' END,
                   SUM(COALESCE(item_price,0)*COALESCE(quantity,0))::numeric, SUM(COALESCE(quantity,0))::numeric, COUNT(DISTINCT order_id)
                   FROM public.order_transaction WHERE source_name='EBAY' AND order_status='Completed' AND ss_name IN %s
                   AND ((order_date >= %s AND order_date < DATE %s + 1) OR (order_date >= %s AND order_date < DATE %s + 1)) GROUP BY 1,2,3,4""",
                        (("so_926407", "led_sone", "electricalsone", "huettenlampen", "ledsonede"), ly0, ly1, f, t))
            db = {(i, mp, s, pp): (q, u, o) for i, mp, s, pp, q, u, o in cur.fetchall()}
            hp = lambda q, u: str((q / u).quantize(Decimal("0.01"), ROUND_HALF_UP)) if u else ""
            for c in keyed(scrape_all(pg)):
                i, _, mp, s = c["key"]
                if not s: continue
                nrows += 1
                L, T = db.get((i, mp, s, "LY"), (0, 0, 0)), db.get((i, mp, s, "TY"), (0, 0, 0))
                got = (c["lyp"].replace(",", ""), c["typ"].replace(",", ""), c["tyo"])
                want = (hp(L[0], L[1]), hp(T[0], T[1]), str(T[2]))
                if got != want: rb.append((f, t, i, mp, s, want, got))
        con.close()
        check(f"sales calendar 1 Oct–{END} and 16 Sep–{END}: every PH-owned eBay ID x SKU row's LY/TY Price and order count == direct DB query ({nrows:,} rows)",
              not rb and nrows > 0, rb[:5])
        check("no JS errors / console errors", not errors, errors[:5])
        br.close()

        # ---- date comparison on a throw-away copy with a synthetic second snapshot ----
        h, m, (P, end) = payload(HTML)
        d2 = "2099-01-01"; s1 = P["price"]["snaps"][day]
        k1, k2 = sorted(s1["bos"])[:2]
        s2 = json.loads(json.dumps(s1)); s2["bos"][k1] = str(Decimal(s1["bos"][k1]) + 1)       # changed on the later date
        s1n = json.loads(json.dumps(s1)); del s1n["bos"][k2]                                    # absent on the earlier date
        P["price"]["snaps"] = {day: s1n, d2: s2}; P["price"]["dates"] = [day, d2]
        tmp = pathlib.Path(tempfile.mkdtemp()) / "compare_test.html"
        tmp.write_text(h[:m.end()] + json.dumps(P, separators=(",", ":"), ensure_ascii=False).replace("</", r"<\/") + h[end:], encoding="utf-8")
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1600, "height": 1000})
        pg.goto(tmp.as_uri()); pg.click("#tabBtnPrice"); pg.wait_for_selector("#pt tbody tr")
        pg.select_option("#pFrom", day); pg.select_option("#pTo", d2); pg.wait_for_timeout(200)
        def rows_for(k):
            pg.fill("#pSku", k); out = []
            for ph in ("", NOPH):
                pg.select_option("#pPh", ph); pg.wait_for_timeout(300)
                out += [c for c in scrape_all(pg) if c["rule"] in (0, 1) and (c["sku"] == k or c["sku"].split("+")[0] == k)]
            return out
        a, b2 = rows_for(k1), rows_for(k2)
        ok1 = bool(a) and all(c["bos_was"] and c["bos_was"].startswith("was ") for c in a)
        ok2 = bool(b2) and all(c["bos_was"] and "Not Available" in c["bos_was"] for c in b2)
        check("date comparison (test copy, synthetic 2nd date): changed value shows 'was', missing earlier value shows 'Not Available'",
              ok1 and ok2 and "compared with" in pg.inner_text("#pNote"), (k1, len(a), [c["bos_was"] for c in a[:2]], k2, len(b2), [c["bos_was"] for c in b2[:2]]))
        br.close()

    st = dict(st)
    print(json.dumps(st, indent=1))
    REP.mkdir(exist_ok=True)
    ok = all(r["ok"] for r in results)
    json.dump({"overall": "PASS" if ok else "FAIL", "snapshot": day, "snapshots": snaps, "bos_published_at": bos_raw.get("published_at"),
               "counts": st, "checks": results}, open(REP / "price_tab_validation.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} passed")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
