import json, collections
d = json.load(open("04_HTML_Report/data/dataset.json", encoding="utf-8"))
rows = d["rows"]; m = d["meta"]
sale = [r for r in rows if r["sku"] is not None]
nosale = [r for r in rows if r["sku"] is None]
print("rows", len(rows), "sku rows", len(sale), "listing-only rows", len(nosale))
keys = collections.Counter((r["ss"], r["mp"], r["id"], r["sku"]) for r in rows)
print("duplicate row keys", sum(1 for k, c in keys.items() if c > 1))
print("blank SKU in sales rows", sum(1 for r in sale if not str(r["sku"]).strip()))
print("blank eBay ID", sum(1 for r in rows if not str(r["id"]).strip()))
print("non-numeric eBay ID", sum(1 for r in rows if not str(r["id"]).isdigit()))
print("shared-listing sku rows", sum(1 for r in sale if r["shared"]), "listings", len({(r['ss'],r['mp'],r['id']) for r in sale if r['shared']}))
print("same SKU on >1 listing (per acct/mp)", sum(1 for c in collections.Counter((r['ss'],r['mp'],r['sku']) for r in sale).values() if c > 1))
print("sales rows without traffic TY", sum(1 for r in sale if r["TY"]["sales"] > 0 and r["TY"]["views"] is None))
print("sales rows without traffic LY", sum(1 for r in sale if r["LY"]["sales"] > 0 and r["LY"]["views"] is None))
print("orders>views (TY listing)", sum(1 for r in sale if r["TY"]["views"] is not None and r["TY"]["lorders"] > r["TY"]["views"]))
print("zero/neg sales with orders", sum(1 for r in sale for p in ("LY","TY") if r[p]["orders"] > 0 and r[p]["sales"] <= 0))
print("new TY (LY=0,TY>0)", sum(1 for r in sale if r["LY"]["sales"] == 0 and r["TY"]["sales"] > 0))
print("lost (LY>0,TY=0)", sum(1 for r in sale if r["LY"]["sales"] > 0 and r["TY"]["sales"] == 0))
print("segment coverage (sku rows)", sum(1 for r in sale if r["seg"]), "/", len(sale))
print("segment multi-label", sum(1 for r in rows if r["seg"] and "/" in r["seg"]))
print("== coverage days")
for c in sorted(m["coverage"], key=lambda x: (x[0], x[1], x[2], x[3])):
    print(c)
print("== non-completed status TY/LY")
agg = collections.defaultdict(lambda: [0, 0.0])
for ss, mp, per, st, n, v in m["status_mix"]:
    agg[(per, st)][0] += n; agg[(per, st)][1] += v
for k in sorted(agg): print(k, agg[k][0], round(agg[k][1], 2))
