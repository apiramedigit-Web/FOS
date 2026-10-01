"""Build the standalone HTML from dataset.json + reconciliation.json + Ebay.xlsx rules (rules.py).

Run order: 03_SQL/extract.py -> 06_Validation/validate_data.py -> this -> 06_Validation/validate_html.py
"""
import json, os
import rules

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data", "dataset.json")
IDS_OUT = os.path.join(HERE, "data", "id_rows.json")
RECON = os.path.join(BASE, "06_Validation", "reports", "reconciliation.json")
TEMPLATE = os.path.join(HERE, "template", "report_template.html")
OUT = os.path.join(HERE, "eBay_Final_Output_Sales_September_2026.html")
SMAP = {"COST_PER_SALE": "S", "ON_SITE": "A"}


def r4(x):
    return None if x is None else round(x, 4)


def main():
    d = json.load(open(DATA, encoding="utf-8"))
    recon = json.load(open(RECON, encoding="utf-8"))
    if recon["n_failed"]:
        raise SystemExit(f"reconciliation has {recon['n_failed']} failures - not building")
    m = d["meta"]
    ids = rules.build_ids(d["rows"], m["coverage"])
    json.dump(ids, open(IDS_OUT, "w", encoding="utf-8"), separators=(",", ":"))

    # Every eBay ID in scope is embedded (14,890). v3 kept only IDs with Sales/Ad Sales and so
    # dropped real ad impressions/clicks/spend and views (see 06_Validation/reports/field_audit_*).
    rows = []
    for x in ids:
        per = lambda p: [r4(x[p]["s"]), x[p]["o"], r4(x[p]["u"]), r4(x[p]["pxq"]), x[p]["v"], x[p]["im"]]
        ads = {SMAP[s]: {p: [r4(v) for v in vals] for p, vals in pv.items()} for s, pv in x["ads"].items()}
        # a[8] = listing_data SKUs, only for IDs with no Completed order (no order SKU)
        rows.append([x["acct"], x["mp"], x["id"], x["skus"], per("LY"), per("TY"), ads, x["seg"],
                     [] if x["skus"] else x["lsku"], x["created"]])
    payload = json.dumps({"generated_at": m["generated_at"], "rows": rows},
                         separators=(",", ":"), ensure_ascii=False).replace("</", r"<\/")
    html = open(TEMPLATE, encoding="utf-8").read()
    assert html.count("/*__DATA__*/null") == 1
    open(OUT, "w", encoding="utf-8").write(html.replace("/*__DATA__*/null", payload))
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1e6:.2f} MB, {len(rows)} eBay IDs)")


if __name__ == "__main__":
    main()
