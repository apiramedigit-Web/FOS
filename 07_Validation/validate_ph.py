"""PH filter validation: all 30 PHs + All PHs + Unassigned (Playwright, headless Chromium).

Expected PH -> eBay ID sets are re-derived from the raw source evidence (05_Evidence/ph_mapping/01_sheet_mapping.csv;
the Sheet is the source of truth since 2026-10-02), not from ph_map.json. Expected metrics are an independent recompute from dataset.json
(validate_html.expected_ids). For every PH option:
  * displayed (account, marketplace, eBay ID) set == expected; no duplicates; no row of another PH
  * every row matches the recompute (all 21 columns) and the header totals follow the PH
  * Σ displayed LY/TY Sales, Orders, Views, Ad Impressions, Ad Clicks, Ad Spend, LY/TY Ad Sales
    (Std and Adv separately) per account x marketplace == expected
Plus PH x Account / Segment / Search combinations, ID coverage and data-quality checks.
Writes reports/ph_validation.json and 05_Evidence/ph_mapping/04-06_*.csv; exit 1 on any failure.
"""
import csv, json, pathlib, re, sys
from collections import defaultdict, Counter
from playwright.sync_api import sync_playwright
from validate_html import HTML, GET_ROWS, ADK, STRATS, expected_ids, check_row, num, close, shown, all_pages

BASE = pathlib.Path(__file__).resolve().parent.parent
EV = BASE / "05_Evidence" / "ph_mapping"
REP = BASE / "06_Validation" / "reports"
PH_MAP = BASE / "04_HTML_Report" / "data" / "ph_map.json"
NOPH = "__none__"
ALIAS = {"Paulroshan": "paulr", "Tharsiga Jaf": "Tharsika(jaffna)", "Tharshika Nell": "Tharsiga(nelli)",
         "Illakiya": "Illakkiya", "Ilakkiya": "Illakkiya"}            # same evidence/decision as extract_ph.py
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:800]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def rd(name):
    with open(EV / name, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def metric_sums(rows_iter):
    """Σ per account x marketplace of the KPI set, from page cells (dict of strings)."""
    A = defaultdict(lambda: defaultdict(float))
    for c in rows_iter:
        a = A[(c["acct"], c["mp"])]; a["ids"] += 1
        for f in ("lys", "tys", "lyo", "tyo", "lyv", "tyv"):
            v = num(c[f]); a[f] += v or 0
        for col in ADK:
            for _, S, _ in STRATS:
                a[col + S] += num(c.get(col + S)) or 0
    return A


def exp_sums(ds):
    A = defaultdict(lambda: defaultdict(float))
    for d in ds:
        a = A[(d["acct"], d["mp"])]; a["ids"] += 1
        a["lys"] += round(d["LY"]["s"], 2); a["tys"] += round(d["TY"]["s"], 2)
        a["lyo"] += d["LY"]["o"]; a["tyo"] += d["TY"]["o"]
        a["lyv"] += 0 if d["nl"] else (d["LY"]["v"] or 0); a["tyv"] += d["TY"]["v"] or 0
        for col, (p, idx, dp, _) in ADK.items():
            if col == "LYAdSales" and d["nl"]:
                continue
            for _, S, _ in STRATS:
                x = d["ads"].get(S, {}).get(p)
                if x: a[col + S] += round(x[idx], dp)
    return A


def main():
    ids = expected_ids()
    # ---- expected PH sets from the raw source evidence --------------------------------
    sheet, db = rd("01_sheet_mapping.csv"), rd("02_db_mapping.csv")
    db_users = {r["username"] for r in rd("02b_db_ph_users.csv")}
    by_low = {u.lower(): u for u in db_users}
    E = defaultdict(set)
    for r in sheet:
        u = ALIAS.get(r["sheet_ph"]) or by_low.get(r["sheet_ph"].lower())
        E[r["ebay_id"]].add(u)          # Sheet = source of truth (user, 2026-10-02); DB rows are not ownership
    phs = sorted(db_users, key=str.lower)
    pm = json.load(open(PH_MAP, encoding="utf-8"))
    check("30 PHs in the DB PH table (staff.ph_categories) = dropdown list", len(phs) == 30 and pm["phs"] == phs, (len(phs), pm["phs"]))
    check("every Sheet PH name resolves to one of the 30 DB PHs", None not in {p for v in E.values() for p in v},
          sorted({r["sheet_ph"] for r in sheet if not (ALIAS.get(r["sheet_ph"]) or by_low.get(r["sheet_ph"].lower()))}))
    check("ph_map.json == Sheet mapping re-derived from raw evidence", {k: sorted(v, key=str.lower) for k, v in E.items()} == pm["map"])
    check("no blank PH / blank or malformed eBay ID in the mapping",
          all(re.fullmatch(r"\d{12}", k) for k in E) and all(p and p.strip() for v in E.values() for p in v))
    check("order_management_copy PH tables identical to ledsone staff tables", pm["meta"]["db"]["order_management_copy_copy_identical"])

    item_ph = lambda d: E.get(d["id"], set())
    exp_sets = {p: {k for k, d in ids.items() if p in item_ph(d)} for p in phs}
    exp_sets[""] = {k for k, d in ids.items() if item_ph(d)}
    exp_sets[NOPH] = {k for k, d in ids.items() if not item_ph(d)}
    check("All PHs and Unassigned are disjoint and together = every eBay ID in the report",
          not (exp_sets[""] & exp_sets[NOPH]) and len(exp_sets[""]) + len(exp_sets[NOPH]) == len(ids))

    # ---- coverage evidence -------------------------------------------------------------
    in_dash = {d["id"] for d in ids.values()}
    sheet_acct = defaultdict(set)
    for r in sheet: sheet_acct[r["ebay_id"]].add(r["account"])
    cov = [[i, "|".join(sorted(v, key=str.lower)), "|".join(sorted(sheet_acct.get(i, []))) or "(DB only)",
            "no Sep 2025/2026 order, traffic or ad row in the 5 extracted eBay accounts" ]
           for i, v in sorted(E.items()) if i not in in_dash]
    with open(EV / "04_mapped_ids_not_in_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["ebay_id", "phs", "sheet_account", "reason"]); w.writerows(cov)

    errors, kpi_rows, per_ph = [], [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(HTML.as_uri()); pg.wait_for_selector("#t tbody tr[data-id]")
        # these checks are for the September monthly table: select 1-30 Sep (the page default is 1 Sep -> latest data date)
        pg.fill("#sdTo", "2026-09-30"); pg.wait_for_timeout(600)
        opts = pg.eval_on_selector_all("#fPh option", "e=>e.map(x=>[x.value,x.textContent])")
        check("PH options = All PHs + 30 exact DB PH names + Unassigned", opts == [["", "All PHs"]] + [[p, p] for p in phs] + [[NOPH, "Unassigned (no PH)"]], opts[:4])
        check("default PH = All PHs", pg.input_value("#fPh") == "" and shown(pg) == len(exp_sets[""]), pg.inner_text("#count"))
        pg.select_option("#pgSize", "200"); pg.wait_for_timeout(150)

        for key in [""] + phs + [NOPH]:
            label = {"": "All PHs", NOPH: "Unassigned"}.get(key, key)
            pg.select_option("#fPh", key); pg.wait_for_timeout(150)
            rows, grps = all_pages(pg) if shown(pg) else ([], {})
            got = [(c["acct"], c["mp"], c["id"]) for c in rows]
            exp = exp_sets[key]
            dup = [k for k, n in Counter(got).items() if n > 1]
            missing, unexpected = exp - set(got), set(got) - exp
            leak = [c["id"] for c in rows if (key == NOPH and c["ph"]) or (key == "" and not c["ph"])
                    or (key not in ("", NOPH) and key not in c["ph"].split("|"))]
            bad = []
            for c in rows:
                check_row(c, ids[(c["acct"], c["mp"], c["id"])], bad)
                if set(filter(None, c["ph"].split("|"))) != item_ph(ids[(c["acct"], c["mp"], c["id"])]): bad.append((c["id"], "ph tag", c["ph"]))
                if any(isinstance(v, str) and re.search(r"undefined|NaN|Infinity|\[object", v) for v in c.values()): bad.append((c["id"], "broken value"))
            P, X = metric_sums(rows), exp_sums(ids[k] for k in exp)
            mis = []
            for am in sorted(set(P) | set(X)):
                for f in sorted(set(P[am]) | set(X[am])):
                    tol = 0.5 if f in ("ids", "lyo", "tyo", "lyv", "tyv") or f.startswith(("AdImpressions", "AdClicks")) else max(0.05, X[am]["ids"] * 0.0051)
                    if abs(P[am].get(f, 0) - X[am].get(f, 0)) > tol: mis.append((am, f, X[am].get(f, 0), P[am].get(f, 0)))
                e, p_ = X[am], P[am]
                kpi_rows.append([label, am[0], am[1], int(e["ids"]), int(p_["ids"])] +
                                [round(e.get(f, 0), 2) for f in ("lys", "tys", "lyo", "tyo", "lyv", "tyv")] +
                                [round(e.get(c + S, 0), 2) for c in ("AdImpressions", "AdClicks", "AdSpend", "LYAdSales", "TYAdSales") for _, S, _ in STRATS] +
                                [("N/A" if not e.get("AdSpend" + S) else round(e.get("AdSales" + S, 0) / e["AdSpend" + S], 2)) for _, S, _ in STRATS] +
                                [("N/A" if not e.get("AdSales" + S) else round(e["AdSpend" + S] / e["AdSales" + S] * 100, 2)) for _, S, _ in STRATS] +
                                ["OK" if not [m for m in mis if m[0] == am] else "MISMATCH"])
            badg = []
            for gk, g in grps.items():
                acct, mp = gk.split(" · ")
                e = [sum(ids[k][p]["s"] for k in exp if k[0] == acct and k[1] == mp) for p in ("LY", "TY")]
                if not close(num(g["glys"]), e[0], 0.011) or not close(num(g["gtys"]), e[1], 0.011): badg.append((gk, e, g["glys"], g["gtys"]))
            ok = not (missing or unexpected or dup or leak or bad or mis or badg) and shown(pg) == len(exp)
            per_ph.append({"ph": label, "expected": len(exp), "shown": len(got), "missing": sorted(missing)[:20], "unexpected": sorted(unexpected)[:20],
                           "duplicates": dup[:20], "cross_ph": leak[:20], "row_errors": bad[:10], "kpi_mismatch": mis[:10], "header_mismatch": badg[:5], "ok": ok})
            check(f"PH '{label}': {len(exp):,} expected IDs == shown; rows, header totals and KPIs reconcile", ok,
                  {k: v for k, v in per_ph[-1].items() if v and k not in ("ph", "ok", "expected", "shown")})
        pg.select_option("#fPh", "")

        # ---- PH x other filters ---------------------------------------------------------
        multi = next((d for d in ids.values() if len(item_ph(d)) > 1), None)    # Sheet-only mapping may have none
        big = max(phs, key=lambda p: len(exp_sets[p]))
        other = next(d for k, d in ids.items() if item_ph(d) and big not in item_ph(d))
        mine = next(d for k, d in ids.items() if k in exp_sets[big] and d["skus"])
        bad = []
        def run(ph, acct="", seg="", q=""):
            pg.select_option("#fPh", ph); pg.select_option("#fAcct", acct); pg.select_option("#fSeg", seg); pg.fill("#fQ", q); pg.wait_for_timeout(400)
            got = []
            while True:
                got += [(x["acct"], x["mp"], x["id"], x["ph"]) for x in pg.evaluate(GET_ROWS, "#t tbody tr[data-id]")]
                if pg.is_disabled("#pgNext"): break
                pg.click("#pgNext")
            return got
        def expect(ph, acct="", seg="", q=""):
            ql = q.lower()
            return sorted(k for k in exp_sets[ph] if (not acct or k[0] == acct) and (not seg or seg in ids[k]["seg"]) and
                          (not q or ql in k[2].lower() or any(ql in s.lower() for s in ids[k]["skus"]) or any(ql in s.lower() for s in ids[k]["lsku"])))
        segs = sorted({s for k in exp_sets[big] for s in ids[k]["seg"]})
        accts = sorted({k[0] for k in exp_sets[big]})
        tests = [(big, a, "", "") for a in accts] + [(big, "", s, "") for s in segs] + [(big, accts[0], segs[0], "")] + \
                [(big, "", "", mine["id"]), (big, "", "", sorted(mine["skus"])[0]), (big, "", "", other["id"]),
                 ("", "", "", other["id"]), (NOPH, "", "", other["id"])] + \
                ([(p, "", "", multi["id"]) for p in sorted(item_ph(multi))] if multi else [])
        for t in tests:
            got = run(*t); e = expect(*t)
            if sorted(g[:3] for g in got) != e or shown(pg) != len(e): bad.append((t, len(e), len(got)))
        run("")
        check(f"PH x Account / Segment / Search combinations ({len(tests)} cases) return exactly the expected IDs; "
              f"another PH's ID is not found under '{big}'; a 2-PH ID is found under both", not bad, bad[:6])
        check("no JS errors / console errors", not errors, errors[:5])
        b.close()

    multi_n = sum(1 for d in ids.values() if len(item_ph(d)) > 1)
    s_ph = sum(len(exp_sets[p]) for p in phs)
    check(f"Σ per-PH ID counts ({s_ph:,}) = All PHs ({len(exp_sets['']):,}) + extra listings for IDs with 2+ PHs",
          s_ph == sum(len(item_ph(ids[k])) for k in exp_sets[""]))
    head = ["ph", "account", "marketplace", "expected_ids", "shown_ids", "ly_sales", "ty_sales", "ly_orders", "ty_orders", "ly_views", "ty_views",
            "ad_impr_std", "ad_impr_adv", "ad_clicks_std", "ad_clicks_adv", "ad_spend_std", "ad_spend_adv", "ly_ad_sales_std", "ly_ad_sales_adv",
            "ty_ad_sales_std", "ty_ad_sales_adv", "roas_std", "roas_adv", "acos_pct_std", "acos_pct_adv", "page_vs_expected"]
    with open(EV / "05_kpi_reconciliation_per_ph.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(head); w.writerows(kpi_rows)
    with open(EV / "06_ph_id_counts.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["ph", "mapped_ids_union", "ids_in_report", "ids_with_2plus_phs", "mapped_ids_not_in_report", "result"])
        for r in per_ph:
            p = {"All PHs": "", "Unassigned": NOPH}.get(r["ph"], r["ph"])
            mapped = {i for i, v in E.items() if (p == "" and v) or p in v} if p != NOPH else set()
            w.writerow([r["ph"], len(mapped), r["expected"], sum(1 for k in exp_sets[p] if len(item_ph(ids[k])) > 1) if p != NOPH else 0,
                        len(mapped - in_dash), "PASS" if r["ok"] else "FAIL"])
    failed = [r for r in results if not r["ok"]]
    json.dump({"n": len(results), "failed": len(failed), "multi_ph_ids_in_report": multi_n, "mapped_ids_not_in_report": len(cov),
               "results": results, "per_ph": per_ph}, open(REP / "ph_validation.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False, default=str)
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
