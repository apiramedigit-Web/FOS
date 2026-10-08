"""PH (Portfolio Holder) -> eBay ID mapping from two sources, combined.

Sources (read-only):
  * Google Sheet 1X2_ruF6uelZ0hvRwmok7mheNECt3tOJj1XOJl9dUWjk, tab "Master Sheet Ebay 4th Cycle" (latest cycle),
    read via its public xlsx export. Layout: row 1 = "PH - Category" block header (spans the account columns),
    row 2 = per-column count, row 3 = account, rows 4+ = eBay item IDs.
  * ledsone staff.ph_categories -> staff.ph_category_products (source_id=2 = eBay) -> staff.users (the 30 PHs).
    order_management_copy public.ph_categories / ph_cate_products is a copy; it is checked for equality.

Business decisions (user, 2026-10-02):
  1. The Google Sheet is the source of truth (user, 2026-10-02; replaces "use both sources"): an eBay ID belongs to
     every PH the Sheet assigns it to. DB assignments are not used for ownership (03_reconciliation evidence only).
  2. Dropdown = the 30 PHs in staff.ph_categories, named by staff.users.username.
  3. An ID with two PHs is shown under both PHs (never one chosen).
  4. Sheet names resolve to the DB user ("use db"), incl. Sheet "Ilakkiya" -> Illakkiya.
  5. IDs with no PH stay in the report under "Unassigned".
Writes 04_HTML_Report/data/ph_map.json and 05_Evidence/ph_mapping/*.
"""
import csv, datetime, io, json, os, re, urllib.request
from collections import defaultdict, Counter
import openpyxl
import psycopg2
from extract import ledsone_dsn

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "04_HTML_Report", "data", "ph_map.json")
EV = os.path.join(BASE, "05_Evidence", "ph_mapping")
SHEET_ID = "1X2_ruF6uelZ0hvRwmok7mheNECt3tOJj1XOJl9dUWjk"
SHEET_TAB = "Master Sheet Ebay 4th Cycle"
ID_RE = re.compile(r"\d{12}")

# Sheet PH name -> staff.users.username where they are not spelled the same. Evidence: the Sheet block's IDs
# equal the DB user's IDs (Paulroshan 242/242, Tharshika Nell 90/90, Tharsiga Jaf 160 of 172, Illakiya 102/102).
# "Ilakkiya" (took over Thojika's categories) has no own DB user; user decision 2026-10-02: use the DB user.
ALIAS = {"Paulroshan": "paulr", "Tharsiga Jaf": "Tharsika(jaffna)", "Tharshika Nell": "Tharsiga(nelli)",
         "Illakiya": "Illakkiya", "Ilakkiya": "Illakkiya"}

SQL_DB = """
SELECT u.username, u.first_name, pc.id, pc.category_name, p.ref_id, p.assign_date::text, p.source_id
FROM staff.ph_categories pc JOIN staff.users u ON u.id = pc.user_id
LEFT JOIN staff.ph_category_products p ON p.ph_category_id = pc.id
ORDER BY u.username, pc.id, p.ref_id"""
SQL_OMC = """
SELECT pc.user_id, p.ref_id FROM public.ph_categories pc
JOIN public.ph_cate_products p ON p.ass_cate_id = pc.id WHERE p.which_channel = 2"""
SQL_OMC_USERS = "SELECT DISTINCT pc.user_id FROM public.ph_categories pc"
SQL_LED_USERS = "SELECT DISTINCT user_id FROM staff.ph_categories"
SQL_LED_PAIRS = """SELECT pc.user_id, p.ref_id FROM staff.ph_categories pc
JOIN staff.ph_category_products p ON p.ph_category_id = pc.id WHERE p.source_id = 2"""


def ph_of(header):
    h = re.sub(r"\s+", " ", header).strip()
    if h.startswith("Rectangular Flush mount-"):          # the one block headed "Category-PH"
        return h.rsplit("-", 1)[1].strip()
    return re.split(r"\s*-\s*", h, maxsplit=1)[0].strip()


def read_sheet():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
    raw = urllib.request.urlopen(url, timeout=120).read()
    os.makedirs(os.path.join(EV, "source"), exist_ok=True)
    with open(os.path.join(EV, "source", f"ph_sheet_{datetime.date.today()}.xlsx"), "wb") as f:
        f.write(raw)
    ws = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)[SHEET_TAB]
    rows = list(ws.iter_rows(values_only=True))
    hdr, acc = rows[0], rows[2]
    cells, bad = [], []
    for i, r in enumerate(rows[3:], start=4):
        for c, v in enumerate(r):
            if v is None or str(v).strip() == "":
                continue
            # block header = nearest non-blank row-1 cell at or left of this column
            h = next(str(hdr[k]) for k in range(min(c, len(hdr) - 1), -1, -1) if hdr[k] is not None and str(hdr[k]).strip())
            s = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
            rec = {"sheet_ph": ph_of(h), "block": re.sub(r"\s+", " ", h).strip(),
                   "account": (acc[c] if c < len(acc) else None), "value": s, "row": i, "col": c + 1}
            (cells if ID_RE.fullmatch(s) else bad).append(rec)
    return cells, bad


def main():
    cells, bad = read_sheet()

    lc = psycopg2.connect(ledsone_dsn()); lc.set_session(readonly=True); cur = lc.cursor()
    cur.execute("select current_database()"); assert cur.fetchone()[0] == "ledsone"
    cur.execute(SQL_DB); db_rows = cur.fetchall()
    cur.execute(SQL_LED_USERS); led_users = {r[0] for r in cur.fetchall()}
    cur.execute(SQL_LED_PAIRS); led_pairs = Counter((u, ref) for u, ref in cur.fetchall())
    lc.close()
    oc = psycopg2.connect(os.environ["DATABASE_URL"]); oc.set_session(readonly=True); cur = oc.cursor()
    cur.execute("select current_database()"); assert cur.fetchone()[0] == "order_management_copy"
    cur.execute(SQL_OMC_USERS); omc_users = {r[0] for r in cur.fetchall()}
    cur.execute(SQL_OMC); omc_pairs = Counter((u, ref) for u, ref in cur.fetchall())
    oc.close()
    omc_same = omc_users == led_users and omc_pairs == led_pairs

    # ---- the 30 PHs ----------------------------------------------------------
    phs = sorted({r[0] for r in db_rows}, key=str.lower)
    by_lower = {}
    for un, fn, *_ in db_rows:
        by_lower[un.lower()] = un
    def resolve(name):
        return ALIAS.get(name) or by_lower.get(name.lower())
    unresolved = sorted({c["sheet_ph"] for c in cells if resolve(c["sheet_ph"]) is None})
    if unresolved:
        raise SystemExit(f"Sheet PH names with no DB user: {unresolved} - add evidence-backed ALIAS or ask the business")

    # ---- per (PH, ID) source flags --------------------------------------------
    S = defaultdict(set); D = defaultdict(set)
    for c in cells:
        S[c["value"]].add(resolve(c["sheet_ph"]))
    db_dup = Counter()
    for un, fn, cid, cat, ref, ad, src in db_rows:
        if src == 2 and ref:
            D[ref].add(un); db_dup[(un, ref)] += 1
    # The Sheet is the source of truth (user, 2026-10-02); DB assignments are kept as reconciliation evidence only
    ids = sorted(S)
    mapping = {i: sorted(S[i], key=str.lower) for i in ids}

    os.makedirs(EV, exist_ok=True)
    def wcsv(name, head, rows):
        with open(os.path.join(EV, name), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(head); w.writerows(rows)
    wcsv("01_sheet_mapping.csv", ["sheet_ph", "resolved_db_user", "block", "account", "ebay_id", "sheet_row", "sheet_col"],
         [[c["sheet_ph"], resolve(c["sheet_ph"]), c["block"], c["account"], c["value"], c["row"], c["col"]] for c in cells])
    wcsv("01b_sheet_invalid_cells.csv", ["sheet_ph", "block", "account", "value", "sheet_row", "sheet_col"],
         [[c["sheet_ph"], c["block"], c["account"], c["value"], c["row"], c["col"]] for c in bad])
    wcsv("02_db_mapping.csv", ["db_user", "first_name", "category_id", "category_name", "ebay_id", "assign_date", "rows_for_user_id"],
         [[un, fn, cid, cat, ref, ad, db_dup[(un, ref)]] for un, fn, cid, cat, ref, ad, src in db_rows if src == 2])
    users = {}
    for un, fn, cid, cat, ref, ad, src in db_rows:
        u = users.setdefault(un, [un, fn, set(), set()]); u[2].add(cid)
        if src == 2 and ref: u[3].add(ref)
    wcsv("02b_db_ph_users.csv", ["username", "first_name", "ph_categories_all_channels", "ebay_ids_in_db"],
         [[u[0], u[1], len(u[2]), len(u[3])] for u in sorted(users.values(), key=lambda x: x[0].lower())])
    recon = []
    for i in sorted(set(S) | set(D)):
        for p in sorted(S.get(i, set()) | D.get(i, set()), key=str.lower):
            s, d = p in S.get(i, ()), p in D.get(i, ())
            recon.append([i, p, int(s), int(d), "MATCH" if s and d else "Sheet only" if s else "DB only",
                          len(mapping.get(i, [])), int(s)])
    wcsv("03_reconciliation_ph_id.csv", ["ebay_id", "ph", "in_sheet", "in_db", "status", "phs_for_id", "used_in_dashboard"], recon)

    st = Counter(r[4] for r in recon)
    per_ph = []
    for p in phs:
        mine = [i for i in ids if p in mapping[i]]
        per_ph.append({"ph": p, "ids": len(mine),
                       "sheet": sum(1 for i in mine if p in S.get(i, ())), "db": sum(1 for i in mine if p in D.get(i, ())),
                       "match": sum(1 for i in mine if p in S.get(i, ()) and p in D.get(i, ())),
                       "multi_ph": sum(1 for i in mine if len(mapping[i]) > 1)})
    meta = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "sheet": {"id": SHEET_ID, "tab": SHEET_TAB, "id_cells": len(cells), "invalid_cells": len(bad),
                  "distinct_ids": len(S), "ph_names": sorted({c["sheet_ph"] for c in cells}),
                  "duplicate_ph_id_cells": sum(v - 1 for v in Counter((resolve(c["sheet_ph"]), c["value"]) for c in cells).values() if v > 1)},
        "db": {"source": "ledsone staff.ph_categories/ph_category_products (source_id=2)/users", "phs": len(phs),
               "phs_with_ebay_ids": len({p for v in D.values() for p in v}), "distinct_ids": len(D),
               "duplicate_ph_id_rows": sum(v - 1 for v in db_dup.values() if v > 1),
               "order_management_copy_copy_identical": omc_same},
        "alias": ALIAS, "status_counts": dict(st),
        "ids": len(ids), "multi_ph_ids": sum(1 for v in mapping.values() if len(v) > 1),
        "phs_with_zero_ids": [r["ph"] for r in per_ph if r["ids"] == 0], "per_ph": per_ph,
    }
    with open(os.path.join(EV, "00_ph_source_summary.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "phs": phs, "map": mapping}, f, separators=(",", ":"), ensure_ascii=False)
    print(f"PHs={len(phs)} ids={len(ids)} multi_ph={meta['multi_ph_ids']} status={dict(st)} omc_copy_identical={omc_same}")
    print("PHs with zero eBay IDs:", meta["phs_with_zero_ids"])


if __name__ == "__main__":
    main()
