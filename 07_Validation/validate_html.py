"""Browser validation of the Final OutPut Sales HTML (Playwright, headless Chromium).

- Every row on every page is compared with an independent recompute from dataset.json.
- For every Account x Marketplace, the sum of the displayed rows is reconciled to the source
  SQL totals in reports/reconciliation.json (Sales, Orders, Views, Organic Impressions,
  Ad Impressions, Ad Clicks, Ad Spend, Ad Sales per strategy).
Writes reports/html_validation.json + screenshots; exit 1 on any failure.
"""
import json, re, sys, pathlib
from collections import defaultdict
from playwright.sync_api import sync_playwright

BASE = pathlib.Path(__file__).resolve().parent.parent
HTML = BASE / "04_HTML_Report" / "eBay_Final_Output_Sales_September_2026.html"
DATA = BASE / "04_HTML_Report" / "data" / "dataset.json"
ACCT_OVR = json.load(open(BASE / "04_HTML_Report" / "data" / "account_overrides.json", encoding="utf-8"))["ids"]
REP = BASE / "06_Validation" / "reports"
REQ_COLS = ["eBay ID", "SKU", "LY Sales", "TY Sales", "YoY %", "LY Ad Sales", "TY Ad Sales", "LY Views", "TY Views",
            "LY Orders", "TY Orders", "LY Conversion %", "TY Conversion %", "Ad Impressions",
            "Ad Clicks", "Ad Spend", "Ad Sales", "ROAS/ACOS", "Segment"]
# LY/TY Price moved to the "Price & SKU Sales Analysis" tab (2026-10-07); checked by validate_price_tab.py
ACCTS = ["Sunsone", "Ledsone", "Electricalsone", "Huttenlampen", "ledsone uk de", "ledsone de"]
SS = {"Sunsone": "so_926407", "Ledsone": "led_sone", "Electricalsone": "electricalsone",
      "Huttenlampen": "huettenlampen", "ledsone uk de": "led_sone", "ledsone de": "ledsonede"}
STRATS = (("COST_PER_SALE", "S", "Standard"), ("ON_SITE", "A", "Advanced"))
# page cell key -> (period, ad index, decimals); source check name
ADK = {"LYAdSales": ("LY", 3, 2, "Ad Sales"), "TYAdSales": ("TY", 3, 2, "Ad Sales"), "AdImpressions": ("TY", 0, 0, "Ad Impressions"),
       "AdClicks": ("TY", 1, 0, "Ad Clicks"), "AdSpend": ("TY", 2, 2, "Ad Spend"), "AdSales": ("TY", 3, 2, "Ad Sales")}
GET_ROWS = """sel=>[...document.querySelectorAll(sel)].map(r=>{const o={};for(const c of r.cells){const k=c.dataset.k||'_'+c.cellIndex;o[k]=c.innerText.trim();
  const sp=[...c.querySelectorAll(':scope > .s')];
  if(sp.length){for(const x of sp){const b=x.querySelector('b');o[k+(b.classList.contains('std')?'S':'A')]=x.innerText.replace(b.innerText,'').trim();}}
  else if(o[k]==='Not advertised'||o[k]==='Not live in Sep 2025'){o[k+'S']=o[k+'A']=o[k];}
  if(k==='lyv'||k==='tyv'){const s=c.querySelector('[title]');o[k+'_t']=s?s.title:'';}
  if(k==='sku'){const s=c.querySelector('[title]');o.sku_t=s?s.title:'';o.sku_src=!!c.querySelector('.src');o.sku_lines=[...c.querySelectorAll('.one')].map(x=>x.textContent);}
  if(k==='seg')o.segs=[...c.querySelectorAll('[data-seg]')].map(b=>b.dataset.seg);}
  const g=r.querySelectorAll('[data-k=glys],[data-k=gtys]');if(g.length){o.glys=g[0].innerText;o.gtys=g[1].innerText;}
  o.id=r.dataset.id;o.acct=r.dataset.acct;o.mp=r.dataset.mp;o.grp=r.dataset.grp;o.ph=r.dataset.ph;return o;})"""
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:600]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def num(s):
    s = (s or "").strip().replace(",", "").replace("%", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def close(a, b, tol=0.011):
    return a is not None and b is not None and abs(a - b) <= tol


def acct_override(acct, mp, i):
    """Display-only account group (04_HTML_Report/data/account_overrides.json, business decision 2026-10-07)."""
    o = ACCT_OVR.get(i)
    return o["acct"] if o and (acct, mp) == (o["from_acct"], o["mp"]) else acct


def expected_ids():
    ids = {}
    for r in json.load(open(DATA, encoding="utf-8"))["rows"]:
        a = acct_override(r["acct"], r["mp"], r["id"])
        k = (a, r["mp"], r["id"])
        d = ids.setdefault(k, {"acct": a, "mp": r["mp"], "id": r["id"], "skus": set(), "lsku": r.get("lsku") or [], "created": r.get("created"),
                               "ads": {S: v for st, S, _ in STRATS for s2, v in r["ads"].items() if s2 == st},
                               **{p: {"s": 0, "u": 0, "pxq": 0, "o": r[p]["lorders"], "v": r[p]["views"], "im": r[p]["impr"]} for p in ("LY", "TY")}})
        if r["sku"]: d["skus"].add(r["sku"])
        for p in ("LY", "TY"):
            d[p]["s"] += r[p]["sales"]; d[p]["u"] += r[p]["units"]; d[p]["pxq"] += r[p]["pxq"]
    for d in ids.values():
        L, T = d["LY"]["s"], d["TY"]["s"]
        seg = set()
        if T > L: seg.add("A – YoY Winner")
        if L > 0 and T == 0: seg.add("D – Lost Performer")
        if L > 0 and 0 < T < L: seg.add("Undetermined: B or D")
        for _, S, nm in STRATS:
            ly = (d["ads"].get(S, {}).get("LY") or [0, 0, 0, 0])[3]; ty = (d["ads"].get(S, {}).get("TY") or [0, 0, 0, 0])[3]
            if ly > 0 and ty == 0: seg.add(f"C – Lost Ad Sales ({nm})")
        d["seg"] = seg or {"No segment rule met"}
        lyad = any((d["ads"].get(S, {}) or {}).get("LY") for _, S, _ in STRATS)
        # independent restatement of the "Not live in Sep 2025" display rule
        d["nl"] = bool(d["created"] and d["created"] > "2025-09-30" and L == 0 and d["LY"]["o"] == 0 and d["LY"]["v"] is None and not lyad)
    return ids


def check_row(c, d, bad):
    L, T = d["LY"], d["TY"]
    for k, e in (("lys", L["s"]), ("tys", T["s"]), ("lyo", L["o"]), ("tyo", T["o"])):
        if not close(num(c[k]), round(e, 2)): bad.append((d["id"], k, e, c[k]))
    ey = None if L["s"] == 0 else (T["s"] - L["s"]) / L["s"] * 100
    if (ey is None and c["yoy"] != "N/A") or (ey is not None and not close(num(c["yoy"]), ey, 0.0051)): bad.append((d["id"], "yoy", ey, c["yoy"]))
    if d["nl"]:
        for k in ("lyv", "lyc", "LYAdSales"):
            if c[k] != "Not live in Sep 2025": bad.append((d["id"], k, "Not live in Sep 2025", c[k]))
    for p, k in (("LY", "ly"), ("TY", "ty")):
        x = d[p]
        if p == "LY" and d["nl"]:
            pass
        elif x["v"] is None:
            if c[k + "v"] != "No data" or c[k + "c"] != "No data": bad.append((d["id"], p + " views/conv nodata", c[k + "v"], c[k + "c"]))
        else:
            if not close(num(c[k + "v"]), x["v"], 0.5): bad.append((d["id"], p + " views", x["v"], c[k + "v"]))
            if x["v"] == 0:
                if c[k + "c"] != "N/A": bad.append((d["id"], p + " conv", "N/A", c[k + "c"]))
            elif not close(num(c[k + "c"]), x["o"] / x["v"] * 100, 0.0051): bad.append((d["id"], p + " conv", x["o"] / x["v"] * 100, c[k + "c"]))
            im = re.search(r"Impressions: ([\d,]+|no data)", c[k + "v_t"])
            if not im or (x["im"] is None) != (im.group(1) == "no data") or (x["im"] is not None and num(im.group(1)) != x["im"]):
                bad.append((d["id"], p + " impressions tooltip", x["im"], c[k + "v_t"]))
        if k + "p" in c: bad.append((d["id"], p + " price column still on the main table", c[k + "p"]))
    for col, (p, idx, dp, _) in list(ADK.items()) + [("ra", ("TY", None, None, None))]:
        if col == "LYAdSales" and d["nl"]:
            continue
        none = not any(d["ads"].get(S, {}).get(p) for _, S, _ in STRATS)
        for _, S, nm in STRATS:
            a = d["ads"].get(S, {}).get(p); g = c.get(col + S)
            if a is None:
                if g != ("Not advertised" if none else "–"): bad.append((d["id"], col + S, "absent", g))
            elif col == "ra":
                ro, ac = (g or " / ").split(" / ")
                if (a[2] == 0) != (ro == "N/A") or (a[2] and not close(num(ro), a[3] / a[2], 0.0051)): bad.append((d["id"], "ROAS" + S, a, g))
                if (a[3] == 0) != (ac == "N/A") or (a[3] and not close(num(ac), a[2] / a[3] * 100, 0.0051)): bad.append((d["id"], "ACOS" + S, a, g))
            elif not close(num(g), round(a[idx], dp), 0.011 if dp else 0.5): bad.append((d["id"], col + S, a[idx], g))
    if set(c["segs"]) != d["seg"]: bad.append((d["id"], "segment", d["seg"], c["segs"]))
    if d["skus"]:
        if c["sku_lines"] != sorted(d["skus"]) or c["sku_src"]: bad.append((d["id"], "sku (all visible)", sorted(d["skus"]), c["sku_lines"]))
    elif d["lsku"]:
        if c["sku_lines"] != d["lsku"] or not c["sku_src"]: bad.append((d["id"], "listing sku (all visible)", d["lsku"], c["sku_lines"]))
    elif c["sku"] != "Not available in source data": bad.append((d["id"], "sku label", c["sku"]))


def shown(pg):
    m = re.search(r"of ([\d,]+) eBay IDs", pg.inner_text("#count"))
    return int(m.group(1).replace(",", "")) if m else None


def all_pages(pg):
    rows, grps = [], {}
    while True:
        rows += pg.evaluate(GET_ROWS, "#t tbody tr[data-id]")
        for g in pg.evaluate(GET_ROWS, "#t tbody tr.grp"):
            if "(continued)" not in g["_0"]: grps[g["grp"]] = g
        if pg.is_disabled("#pgNext"): break
        pg.click("#pgNext")
    return rows, grps


def main():
    ids = expected_ids()
    # PH filter: default "All PHs" = IDs owned by any PH; "Unassigned" = the rest (validate_ph.py checks each PH)
    phmap = json.load(open(BASE / "04_HTML_Report" / "data" / "ph_map.json", encoding="utf-8"))["map"]
    pool = {k: d for k, d in ids.items() if phmap.get(d["id"])}
    recon =json.load(open(REP / "reconciliation.json", encoding="utf-8"))
    src = {(c["check"], c["key"]): c["source"] for c in recon["checks"]}
    errors = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(HTML.as_uri()); pg.wait_for_selector("#t tbody tr[data-id]")
        # these checks are for the September monthly table: select 1-30 Sep (the page default is 1 Sep -> latest data date)
        pg.fill("#sdTo", "2026-09-30"); pg.wait_for_timeout(600)
        check("file exists and opens in Chromium", HTML.exists())

        # ---- structure ----
        hd = pg.eval_on_selector_all("#t thead th", "e=>e.map(x=>x.firstChild.textContent.trim())")
        check(f"header = the {len(REQ_COLS)} Final OutPut Sales columns in workbook order (no LY/TY Price)", hd == REQ_COLS, hd)
        labels = pg.eval_on_selector_all("#tabMain .filters label", "e=>e.map(x=>x.childNodes[0].textContent.trim())")
        check("filters = PH, Category, Account, Segment + eBay ID/SKU search (Date From / Date To sit in the September Comparison Table card)",
              labels == ["PH", "Category", "Account", "Segment", "Search eBay ID / SKU"]
              and pg.eval_on_selector_all("#dRange label", "e=>e.map(x=>x.childNodes[0].textContent.trim())") == ["Date From", "Date To"], labels)
        check("Account options = 'All accounts' + 6 workbook accounts in order",
              pg.eval_on_selector_all("#fAcct option", "e=>e.map(x=>x.textContent)") == ["All accounts"] + ACCTS)
        all_segs = sorted({s for d in ids.values() for s in d["seg"]})
        seg_opts = pg.eval_on_selector_all("#fSeg option", "e=>e.map(x=>x.textContent)")
        check("Segment options = 'All segments' + every segment present", seg_opts == ["All segments"] + all_segs, seg_opts)
        check("default view = All PHs, all accounts, all segments (every PH-owned ID)",
              pg.input_value("#fPh") == "" and pg.input_value("#fAcct") == "" and pg.input_value("#fSeg") == "" and shown(pg) == len(pool), pg.inner_text("#count"))
        check("pager: First/Prev/Page x of y/Next/Last + rows per page",
              all(pg.locator(i).count() == 1 for i in ("#pgFirst", "#pgPrev", "#pgNext", "#pgLast", "#pgSize")) and pg.inner_text("#pgInfo").startswith("Page 1 of"))
        pg.screenshot(path=str(REP / "screenshot_1920.png"))
        pg.select_option("#pgSize", "200"); pg.wait_for_timeout(150)

        # ---- every row, every page ----
        rows, grps = all_pages(pg)                      # All PHs
        pg.select_option("#fPh", "__none__"); pg.wait_for_timeout(150)
        rows_u, grps_u = all_pages(pg)                  # Unassigned
        pg.select_option("#fPh", "")
        rows += rows_u
        check(f"rows rendered across all pages (All PHs + Unassigned) = every eBay ID in scope ({len(ids):,})",
              len(rows) == len(ids) and len({(r["acct"], r["mp"], r["id"]) for r in rows}) == len(ids), (len(rows), len(ids)))
        bad, broken, heights = [], [], []
        for c in rows:
            d = ids.get((c["acct"], c["mp"], c["id"]))
            if d is None: bad.append((c["id"], "unexpected")); continue
            check_row(c, d, bad)
            for k, v in c.items():
                if isinstance(v, str) and ((v == "" and not k.startswith("_") and not k.endswith("_t") and k not in ("grp", "ph")) or re.search(r"undefined|NaN|Infinity|null|\[object", v)):
                    broken.append((c["id"], k, v))
        check(f"all {len(rows):,} rows match independent recompute ({len(REQ_COLS)} columns, Std/Adv separately, tooltips, SKU source, segments)", not bad, bad[:6])
        check("no empty / undefined / NaN / Infinity / null cell on any page", not broken, broken[:5])
        no_sku = sum(1 for d in ids.values() if not d["skus"] and not d["lsku"])
        nl_exp = sum(1 for d in ids.values() if d["nl"]); nl_got = sum(1 for c in rows if c["lyv"] == "Not live in Sep 2025")
        check(f"'Not live in Sep 2025' shown on exactly the {nl_exp:,} listings created after Sep 2025 with no LY data", nl_exp == nl_got and nl_exp > 0, (nl_exp, nl_got))
        check(f"SKU shown for every ID that has one in any source (only {no_sku:,} have none anywhere)",
              sum(1 for c in rows if c["sku"] == "Not available in source data") == no_sku)

        # ---- reconcile: Σ displayed rows per account x marketplace == source SQL ----
        agg = defaultdict(lambda: defaultdict(float)); n = defaultdict(int)
        for c in rows:
            key = (SS[c["acct"]], c["mp"]); n[key] += 1; A = agg[key]
            for f in ("lys", "tys", "lyo", "tyo"): A[f] += num(c[f])
            for p, k in (("LY", "lyv"), ("TY", "tyv")):
                if num(c[k]) is not None: A[k] += num(c[k]); A[k + "_has"] = 1
                m = re.search(r"Impressions: ([\d,]+)", c.get(k + "_t", ""))
                if m: A[k + "_im"] += num(m.group(1)); A[k + "_imhas"] = 1
            for col in ADK:
                for _, S, _ in STRATS:
                    v = num(c.get(col + S))
                    if v is not None: A[col + S] += v; A[col + S + "_has"] = 1
        bad = []
        for (ss, mp), A in agg.items():
            tol = max(0.05, n[(ss, mp)] * 0.0051)
            pairs = [("Sales", "lys", "LY"), ("Sales", "tys", "TY"), ("Orders (listing-level)", "lyo", "LY"), ("Orders (listing-level)", "tyo", "TY"),
                     ("Views", "lyv", "LY"), ("Views", "tyv", "TY"), ("Organic Impressions", "lyv_im", "LY"), ("Organic Impressions", "tyv_im", "TY")]
            for chk, f, p in pairs:
                s = src.get((chk, f"{ss} | {mp} | {p}"))
                if s is None:
                    if A.get(f): bad.append((ss, mp, chk, p, "source none", A[f]))
                elif not close(A.get(f, 0), s, tol): bad.append((ss, mp, chk, p, s, A.get(f)))
            for col, (p, _, _, chk) in ADK.items():
                for st, S, _ in STRATS:
                    s = src.get((chk, f"{ss} | {mp} | {p} | {st}"))
                    if s is None:
                        if A.get(col + S + "_has"): bad.append((ss, mp, col + S, "source none", A[col + S]))
                    elif not close(A.get(col + S, 0), s, tol): bad.append((ss, mp, col + S, s, A.get(col + S)))
        src_keys = {tuple(k.split(" | ")[:2]) for (_, k) in src}
        check(f"Σ displayed rows reconcile to source SQL for all {len(agg)} account x marketplace (Sales, Orders, Views, Impressions, every ad metric per strategy)", not bad, bad[:6])
        check("every source account x marketplace has rows on the page", src_keys <= set(agg), src_keys - set(agg))
        badg = []
        for sub, gg in ((pool, grps), ({k: d for k, d in ids.items() if k not in pool}, grps_u)):
            for gk, g in gg.items():
                acct, mp = gk.split(" · ")
                e = [sum(d[p]["s"] for d in sub.values() if d["acct"] == acct and d["mp"] == mp) for p in ("LY", "TY")]
                if not close(num(g["glys"]), e[0], 0.011) or not close(num(g["gtys"]), e[1], 0.011): badg.append((gk, e, g["glys"], g["gtys"]))
        check(f"all {len(grps) + len(grps_u)} account header totals (LY/TY Sales; All PHs and Unassigned) correct", not badg and set(grps) | set(grps_u) == {a + " · " + m for a, m in {(c["acct"], c["mp"]) for c in rows}}, badg[:4])

        # ---- filters ----
        bad = []
        for seg in all_segs:
            pg.select_option("#fSeg", seg); pg.wait_for_timeout(120)
            exp = [d for d in pool.values() if seg in d["seg"]]
            vis = pg.evaluate(GET_ROWS, "#t tbody tr[data-id]")
            if shown(pg) != len(exp) or any(seg not in v["segs"] for v in vis): bad.append((seg, len(exp)))
            if not exp: continue
            g = pg.evaluate(GET_ROWS, "#t tbody tr.grp")[0]; acct, mp = g["grp"].split(" · ")
            e = sum(d["TY"]["s"] for d in exp if d["acct"] == acct and d["mp"] == mp)
            if not close(num(g["gtys"]), e, 0.011): bad.append((seg, "filtered header total", e, g["gtys"]))
        pg.select_option("#fSeg", "")
        check("Segment filter: each option shows exactly its IDs; multi-segment IDs keep all labels; header totals follow the filter", not bad, bad)
        bad = []
        for a in ACCTS:
            pg.select_option("#fAcct", a); pg.wait_for_timeout(120)
            exp = sum(1 for d in pool.values() if d["acct"] == a)
            vis = pg.evaluate(GET_ROWS, "#t tbody tr[data-id]")
            if shown(pg) != exp or {v["acct"] for v in vis} != ({a} if exp else set()): bad.append((a, exp))
        pg.select_option("#fAcct", "")
        check("Account filter: each account shows exactly its IDs; 'All accounts' = all 6", not bad, bad)
        pg.select_option("#fAcct", "Ledsone"); pg.select_option("#fSeg", "C – Lost Ad Sales (Advanced)"); pg.wait_for_timeout(120)
        exp = sum(1 for d in pool.values() if d["acct"] == "Ledsone" and "C – Lost Ad Sales (Advanced)" in d["seg"])
        vis = pg.evaluate(GET_ROWS, "#t tbody tr[data-id]")
        check("Account + Segment combined; ad values still shown under filter",
              shown(pg) == exp and all(num(v.get("LYAdSalesA")) is not None for v in vis), exp)
        pg.select_option("#fAcct", ""); pg.select_option("#fSeg", "")
        # ---- search: partial, case-insensitive on eBay ID + order SKUs + listing SKUs ----
        def exp_q(q, acct=""):
            q = q.lower()
            return sorted(d["id"] for d in pool.values() if (not acct or d["acct"] == acct) and
                          (q in d["id"].lower() or any(q in x.lower() for x in d["skus"]) or any(q in x.lower() for x in d["lsku"])))
        one_id = next(d for d in pool.values() if d["skus"])
        lst = next(d for d in pool.values() if not d["skus"] and d["lsku"])
        tests = [(one_id["id"], ""), (sorted(one_id["skus"])[0], ""), ("lsca2l", ""), (lst["lsku"][0], ""), ("CRFF500BM", "Ledsone"), ("31761", "")]
        bad = []
        for q, acct in tests:
            pg.select_option("#fAcct", acct); pg.fill("#fQ", q); pg.wait_for_timeout(400)
            e = exp_q(q, acct); got = []
            while True:
                got += [x["id"] for x in pg.evaluate(GET_ROWS, "#t tbody tr[data-id]")]
                if pg.is_disabled("#pgNext"): break
                pg.click("#pgNext")
            if sorted(got) != e or shown(pg) != len(e): bad.append((q, acct, len(e), len(got), shown(pg)))
        pg.select_option("#fAcct", ""); pg.fill("#fQ", "zzz-no-such-sku"); pg.wait_for_timeout(400)
        nomatch = shown(pg) == 0 and "No eBay ID or SKU matches" in pg.inner_text("#t")
        pg.click("#fQx"); pg.wait_for_timeout(400)
        cleared = pg.input_value("#fQ") == "" and shown(pg) == len(pool)
        check("search: eBay ID, SKU, partial/case-insensitive, listing SKU, combined with Account; no-match message; clear button",
              not bad and nomatch and cleared, (bad, nomatch, cleared))
        hdr = pg.eval_on_selector_all("#t thead th", "e=>e.map(x=>x.innerHTML)")
        check("no sort arrows / sort handlers on headers (removed per user)", not any("▲" in h or "▼" in h for h in hdr) and pg.locator("th[data-sk]").count() == 0)

        mp_ = b.new_page(viewport={"width": 800, "height": 900})
        wrap = REP / "_mobile_wrapper.html"
        wrap.write_text(f'<!doctype html><iframe id=f src="{HTML.as_uri()}" style="width:390px;height:860px;border:0"></iframe>', encoding="utf-8")
        mp_.goto(wrap.as_uri()); mp_.frame_locator("#f").locator("#t tbody tr").first.wait_for()
        fr = [f for f in mp_.frames if f.url == HTML.as_uri()][0]
        sw, cw = fr.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        check("390px: page itself has no horizontal scroll (table scrolls in its box)", sw <= cw, (sw, cw))
        check("no JS errors / console errors", not errors, errors[:5])
        b.close()
    failed = [r for r in results if not r["ok"]]
    json.dump({"n": len(results), "failed": len(failed), "results": results}, open(REP / "html_validation.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
