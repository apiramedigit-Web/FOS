"""Ebay.xlsx workbook rules (sheets: Workflow, Segmentation, Root Cause Matrix).

Only rules the workbook defines explicitly are evaluated. Undefined expressions
("recent sales improving", "TY Sales ≈ 0" for TY > 0, "Flat", "stable",
Stable/New Ad Sales, Increase/Maintain/Reduce/Restart) are flagged, never thresholded.
Grain = eBay ID (Workflow step 1 "Identify relevant eBay IDs", step 2 "Segment IDs").
"""
from collections import OrderedDict

SEG_A = "A – YoY Winner"
SEG_B = "B – YoY Recovery"
SEG_C = "C – Lost Ad Sales"
SEG_D = "D – Lost Performer"
SEG_U = "Undetermined: B or D"
SEG_NONE = "No segment rule met"
STRAT = OrderedDict([("COST_PER_SALE", "Standard"), ("ON_SITE", "Advanced")])

UNDEF_BD = ("TY Sales < LY Sales with TY > 0: B requires \"recent sales improving\" and D requires "
            "\"TY Sales ≈ 0\". Neither is defined in the workbook.")


def ad_sales(idr, strat, per):
    """Ad sales for a strategy/period. No ad row = 0: the feed writes all-zero rows for
    every advertised listing every day, so absence means not in any campaign."""
    a = idr["ads"].get(strat, {}).get(per)
    return (a[3] if a else 0.0), a is not None


def segments(idr):
    L, T = idr["LY"]["s"], idr["TY"]["s"]
    out = []
    if T > L:                                   # A: TY Sales > LY Sales
        out.append(SEG_A)
    if L > 0 and T == 0:                        # D: LY > 0 and TY ≈ 0 (TY = 0 satisfies ≈ 0 at any tolerance)
        out.append(SEG_D)
    if L > 0 and 0 < T < L:                     # B / D undetermined
        out.append(SEG_U)
    for strat, nm in STRAT.items():             # C: LY Ad Sales > 0 and TY Ad Sales = 0
        ly, _ = ad_sales(idr, strat, "LY")
        ty, _ = ad_sales(idr, strat, "TY")
        if ly > 0 and ty == 0:
            out.append(f"{SEG_C} ({nm})")
    return out or [SEG_NONE]


def step2_yoy(idr):
    L, T = idr["LY"]["s"], idr["TY"]["s"]
    if T > L: return "YoY Winner"
    if L > 0 and T == 0: return "Lost"
    if L > 0 and 0 < T < L: return "Recovery / Decline: undetermined (rules undefined)"
    if L == 0 and T == 0: return "No sales LY or TY"
    return "Not categorised (TY Sales = LY Sales)"


def step3_ad(idr, strat):
    ly, _ = ad_sales(idr, strat, "LY")
    ty, has_ty = ad_sales(idr, strat, "TY")
    if ly > 0 and ty == 0:
        return "Lost Ad Sales" + ("" if has_ty else " (not in any TY campaign)")
    if ly == 0 and ty == 0:
        return "No ad sales LY or TY"
    return "Stable / New Ad Sales: undefined"


def _dir(ly, ty):
    if ly is None or ty is None: return None
    return "↑" if ty > ly else "↓" if ty < ly else "="


def ctr(p):
    return None if not p["im"] else p["v"] / p["im"] * 100


def conv(p):
    return None if not p["v"] else p["o"] / p["v"] * 100


def root_cause(idr):
    """Root Cause Matrix. Confirmed = rule fully defined. Candidate (?) = the directional part
    of the signal is observed, but its 'stable' / magnitude part is undefined."""
    L, T = idr["LY"], idr["TY"]
    out = []
    for strat, nm in STRAT.items():
        ly, _ = ad_sales(idr, strat, "LY"); ty, _ = ad_sales(idr, strat, "TY")
        if ly > 0 and ty == 0:
            out.append(f"Ad Issue ({nm})")
    if _dir(L["v"], T["v"]) == "↓" or _dir(L["im"], T["im"]) == "↓":
        out.append("Traffic Loss?")
    if _dir(ctr(L) if L["v"] is not None else None, ctr(T) if T["v"] is not None else None) == "↓":
        out.append("Click/CTR Loss?")
    if T["o"] < L["o"]:
        out.append("Conversion Loss?")
    return out


def build_ids(rows, coverage):
    """Aggregate eBay ID + SKU rows (dataset.json) to one row per eBay ID."""
    days = {(c[1], c[2], c[3]): c[4] for c in coverage if c[0] == "traffic"}
    ids = OrderedDict()
    for r in rows:
        k = (r["ss"], r["mp"], r["id"])
        d = ids.get(k)
        if d is None:
            d = ids[k] = {"acct": r["acct"], "ss": r["ss"], "mp": r["mp"], "id": r["id"], "skus": [],
                          "ads": r["ads"], "LY": {}, "TY": {}, "lsku": r.get("lsku") or [], "created": r.get("created")}
            for p in ("LY", "TY"):
                x = r[p]
                d[p] = {"s": 0.0, "u": 0.0, "pxq": 0.0, "o": x["lorders"], "v": x["views"], "im": x.get("impr")}
        if r["sku"] is not None:
            d["skus"].append(r["sku"])
        for p in ("LY", "TY"):
            d[p]["s"] += r[p]["sales"]; d[p]["u"] += r[p]["units"]; d[p]["pxq"] += r[p]["pxq"]
    out = []
    for (ss, mp, _), d in ids.items():
        d["skus"].sort()
        d["seg"] = segments(d)
        d["wf"] = {"s2": step2_yoy(d), "s3S": step3_ad(d, "COST_PER_SALE"), "s3A": step3_ad(d, "ON_SITE"),
                   "rc": root_cause(d)}
        d["tdays"] = {"LY": days.get((ss, mp, "LY"), 0), "TY": days.get((ss, mp, "TY"), 0)}
        out.append(d)
    return out


# Rule register shown in the report (workbook wording preserved).
RULES = [
    ("Segmentation", "A – YoY Winner", "TY Sales > LY Sales", "Implemented", "Exact rule."),
    ("Segmentation", "B – YoY Recovery", "TY Sales < LY Sales but recent sales improving", "Undefined",
     "\"recent sales improving\" has no period or measure in the workbook. IDs with TY < LY and TY > 0 are flagged \"" + SEG_U + "\"."),
    ("Segmentation", "C – Lost Ad Sales", "LY Ad Sales > 0 and TY Ad Sales = 0", "Implemented",
     "Evaluated separately for Standard and Advanced PPC (never summed). A listing with no TY ad row has TY Ad Sales = 0: the feed writes all-zero rows for every advertised listing on every day."),
    ("Segmentation", "D – Lost Performer", "LY Sales > 0 and TY Sales ≈ 0", "Partly implemented",
     "Assigned only where TY Sales = 0, which satisfies \"≈ 0\" under any tolerance. For TY > 0 the tolerance of \"≈\" is undefined, so it is flagged \"" + SEG_U + "\"."),
    ("Segmentation", "Overlapping segments", "(not stated)", "Undefined",
     "The workbook doesn't say whether segments are exclusive or ranked, so every matching label is shown."),
    ("Workflow 1", "ID Selection", "Identify relevant eBay IDs", "Undefined",
     "\"relevant\" isn't defined, so every ID with data is kept. The table defaults to IDs with non-zero Step 1 Key Data (Sales or Ad Sales, LY or TY). That is a view filter; a toggle shows all."),
    ("Workflow 2", "YoY Segmentation", "YoY Winner / Recovery / Decline / Lost", "Partly implemented",
     "Winner (TY > LY) and Lost (LY > 0, TY = 0) follow the Segmentation sheet. \"Recovery\" depends on an undefined term and \"Decline\" has no rule."),
    ("Workflow 3", "Ad-Sales Gap", "Lost Ad Sales / Stable Ad Sales / New Ad Sales", "Partly implemented",
     "Lost Ad Sales follows rule C. Stable and New Ad Sales have no rule in the workbook."),
    ("Workflow 4", "Traffic Analysis", "Traffic Up / Flat / Down", "Undefined",
     "LY/TY Impressions, Views and CTR are shown with the observed direction (↑ ↓ =). \"Flat\" has no band, so no class is assigned."),
    ("Workflow 5", "Conversion Analysis", "Conversion Up / Flat / Down", "Undefined",
     "LY/TY Views, Orders and Conversion % are shown with the observed direction. \"Flat\" has no band."),
    ("Workflow 6", "Price Analysis", "Competitive / Higher / Lower (needs Competitor Price)", "Data not available",
     "There is no competitor price in the source data. LY/TY Price are shown."),
    ("Workflow 7", "Listing Analysis", "Issue Found / No Issue (Title, Images, Item Specifics, Description, Category)", "Data not available",
     "Listing-content data and issue criteria are not in the uploaded/source data."),
    ("Workflow 8", "Demand Analysis", "Demand Up / Stable / Down (eBay Product Research, sold data, sell-through)", "Data not available",
     "eBay Product Research / market data is not in the source data."),
    ("Workflow 9", "Ad Optimization", "Increase / Maintain / Reduce / Restart", "Undefined",
     "No decision rule is given. Ad impressions, clicks, spend, sales and ROAS/ACOS are shown."),
    ("Root Cause", "Traffic Loss", "Views/Impressions ↓", "Candidate only",
     "Shown as \"Traffic Loss?\" where TY < LY on Views or Impressions. The size of \"↓\" isn't defined (Workflow 4 has an undefined \"Flat\" band)."),
    ("Root Cause", "Click/CTR Loss", "Views stable but clicks ↓", "Candidate only",
     "\"stable\" is undefined. Shown as \"Click/CTR Loss?\" where TY CTR < LY CTR (CTR = Views ÷ Impressions)."),
    ("Root Cause", "Conversion Loss", "Clicks/views stable but orders ↓", "Candidate only",
     "\"stable\" is undefined. Shown as \"Conversion Loss?\" where TY Orders < LY Orders."),
    ("Root Cause", "Price Issue", "TY price > competitive market", "Data not available", "No competitor/market price."),
    ("Root Cause", "Ad Issue", "LY ad sales > 0, TY ad sales = 0", "Implemented", "Same rule as C, per strategy."),
    ("Root Cause", "Demand Loss", "Market sales/demand ↓", "Data not available", "No market demand data."),
    ("Root Cause", "Listing Issue", "Content/image/item-specific issue", "Data not available", "No listing-quality data or criteria."),
]
