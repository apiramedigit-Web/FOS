"""Daily price snapshot for the "Price & SKU Sales Analysis" tab (read-only).

Sources:
  * BOS Balance Price (Healthy): the public feed behind https://cppc-sandbox-viewer.vercel.app/balanceprice
    (supabase function balanceprice-net-feed), field net_healthy_gbp per seller_sku (rows with sku_status ASSIGNED).
  * BLOS eBay Price: https://blos.vercel.app/api/listing-channel-price?platform=ebay&channel=<channel>, field
    recommended_price per sku, after POST /api/auth/login. Credentials come from BLOS_USERNAME / BLOS_PASSWORD in the
    environment, else from the existing store product-page/.env (BLOS_ENV_FILE overrides the path). They are never
    printed or written anywhere.
Both sources give only the current state, so each run stores one dated snapshot; history starts at the first run and is
never back-filled.

Writes 04_HTML_Report/data/price_snapshots/<YYYY-MM-DD>.json and raw responses to 05_Evidence/price_sources/<date>/.
"""
import datetime, gzip, http.cookiejar, json, os, re, time, urllib.error, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(BASE, "04_HTML_Report", "data", "price_snapshots")
EV_DIR = os.path.join(BASE, "05_Evidence", "price_sources")
BOS_FEED = "https://qobwxdaazzcmpwcjycyy.supabase.co/functions/v1/balanceprice-net-feed"
BOS_PAGE = "https://cppc-sandbox-viewer.vercel.app/balanceprice"
BLOS = "https://blos.vercel.app"
# BLOS eBay channels (site -> channels as listed on the BLOS screen). sunsone is not a BLOS channel (the API returns 0
# rows; kept so a future channel shows up). led_sone listings on the Germany site have no BLOS channel.
SCREEN_CHANNELS = {"UK": ("ledsone", "electricalsone"), "DE": ("ledsonede", "huettenlampen", "electricalsone-de")}
CHANNELS = ("ledsone", "electricalsone", "sunsone", "ledsonede", "huettenlampen", "electricalsone-de")
LCP = BLOS + "/listing-channel-price"
BOS_FIELDS = {"seller_sku", "sku_status", "net_healthy_gbp", "category", "currency"}
BLOS_FIELDS = {"sku", "recommended_price", "site", "currency"}


def get(opener, url, data=None, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                         headers={"content-type": "application/json"} if data else {})
            with opener.open(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if k == tries - 1:
                raise SystemExit(f"{url.split('?')[0]} -> HTTP {e.code} {body}")
        except urllib.error.URLError as e:
            if k == tries - 1:
                raise SystemExit(f"{url.split('?')[0]} -> {e.reason}")
        time.sleep(5 * (k + 1))


def blos_creds():
    u, p = os.environ.get("BLOS_USERNAME"), os.environ.get("BLOS_PASSWORD")
    if u and p:
        return u, p
    path = os.environ.get("BLOS_ENV_FILE", os.path.join(os.path.expanduser("~"), "product-page", ".env"))
    env = {}
    for line in open(path, encoding="utf-8"):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    if not env.get("BLOS_USERNAME") or not env.get("BLOS_PASSWORD"):
        raise SystemExit("BLOS credentials not found (BLOS_USERNAME / BLOS_PASSWORD)")
    return env["BLOS_USERNAME"], env["BLOS_PASSWORD"]


MONEY = re.compile(r"^[£€]?(\d+(?:\.\d+)?)$")


def blos_screen(user, pwd, api_rows):
    """Read the BLOS "SKU Price" table (read-only, default screen state + Show Packs) for every channel.

    The API leaves recommended_price empty for combo SKUs; the screen's RECOMMENDED PRICE column computes them (BLOS's own
    calculation). Returns {channel: {"rec": {sku: value}, "rows": [[sku, healthy, rec_exc, rec, live], ...]}}."""
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport={"width": 1700, "height": 1100}).new_page()
        r = pg.request.post(BLOS + "/api/auth/login", data=json.dumps({"username": user, "password": pwd}),
                            headers={"content-type": "application/json"})
        if r.status != 200:
            raise SystemExit(f"BLOS screen login -> HTTP {r.status}")
        pg.goto(LCP, wait_until="networkidle")
        pg.get_by_text("SKU Price", exact=True).click()
        pg.wait_for_selector("tbody tr", timeout=60000)
        pg.get_by_text("Show Packs", exact=False).first.click()
        for site, chans in SCREEN_CHANNELS.items():
            pg.get_by_role("button", name=re.compile("^Site " + site)).click()
            pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
            for ch in chans:
                # a stale table (previous channel) is caught below: channels price the same SKU differently and every
                # value must equal this channel's API recommended_price
                pg.get_by_role("button", name=ch, exact=True).click()
                pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
                want = api_rows[ch]
                for _ in range(120):                       # wait until the table holds this channel's full row set
                    if pg.locator("tbody tr").count() == want:
                        break
                    pg.wait_for_timeout(500)
                hd = pg.eval_on_selector_all("thead th", "e=>e.map(x=>x.innerText.trim())")
                need = ["SKU", "PRODUCT COST (HEALTHY)", "REC PRICE (EXC POSTAGE)", "RECOMMENDED PRICE", "LIVE PRICE", "CARRIER CHARGE"]
                if any(h not in hd for h in need):
                    raise SystemExit(f"BLOS screen {ch}: columns changed {hd}")
                idx = [hd.index(h) for h in need]
                cells = pg.eval_on_selector_all("tbody tr", "e=>e.map(r=>[...r.cells].map(c=>c.innerText.trim()))")
                rows = [[re.sub(r"×\d+$", "", c[idx[0]])] + [c[i] for i in idx[1:]] for c in cells]
                if len(rows) != want:
                    raise SystemExit(f"BLOS screen {ch}: {len(rows)} rows on screen, API has {want}")
                rec, post, bad = {}, {}, []
                for s, _h, e, v, _l, _c in rows:
                    if v in ("—", "-", ""):
                        continue
                    m = MONEY.match(v.replace(",", ""))
                    if not m:
                        bad.append((s, v)); continue
                    rec[s] = m.group(1)
                    # postage status from BLOS's own columns: RECOMMENDED PRICE above REC PRICE (EXC POSTAGE) = BLOS
                    # added the carrier charge into the price (its Rule 1); equal = postage not included
                    me = MONEY.match(e.replace(",", "")) if e not in ("—", "-", "") else None
                    post[s] = None if not me else ("I" if float(m.group(1)) > float(me.group(1)) else "N")
                if bad:
                    raise SystemExit(f"BLOS screen {ch}: unparseable RECOMMENDED PRICE {bad[:5]}")
                out[ch] = {"rec": rec, "post": post, "rows": rows}
        br.close()
    return out


def save_raw(day, name, raw):
    os.makedirs(os.path.join(EV_DIR, day), exist_ok=True)
    with gzip.open(os.path.join(EV_DIR, day, name + ".json.gz"), "wb") as f:
        f.write(raw)


def main():
    now = datetime.datetime.now()
    day = now.date().isoformat()
    plain = urllib.request.build_opener()

    raw = get(plain, BOS_FEED)
    bos = json.loads(raw)
    rows = bos.get("rows") or []
    missing = BOS_FIELDS - set(rows[0]) if rows else BOS_FIELDS
    if not bos.get("ok") or missing:
        raise SystemExit(f"BOS feed schema changed / not ok: missing {sorted(missing)}")
    save_raw(day, "bos_balanceprice_feed", raw)
    healthy, dup = {}, 0
    for r in rows:
        if r["sku_status"] != "ASSIGNED" or r["net_healthy_gbp"] in (None, ""):
            continue
        dup += r["seller_sku"] in healthy
        healthy[r["seller_sku"]] = {"v": str(r["net_healthy_gbp"]), "cat": r["category"], "cur": r["currency"]}
    if dup:
        raise SystemExit(f"BOS feed has {dup} duplicate seller_sku values - not choosing one")

    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    u, p = blos_creds()
    get(op, BLOS + "/api/auth/login", json.dumps({"username": u, "password": p}).encode())
    if not any(c.name == "session" for c in cj):
        raise SystemExit("BLOS login returned no session")
    chans, api_n, api_skus, api_flag = {}, {}, {}, {}
    for ch in CHANNELS:
        raw = get(op, f"{BLOS}/api/listing-channel-price?platform=ebay&channel={ch}")
        d = json.loads(raw)
        if not isinstance(d, list) or (d and BLOS_FIELDS - set(d[0])):
            raise SystemExit(f"BLOS {ch}: schema changed")
        save_raw(day, f"blos_ebay_{ch}", raw)
        prices, dup = {}, 0
        for r in d:
            if r["recommended_price"] in (None, ""):
                continue
            dup += r["sku"] in prices
            prices[r["sku"]] = str(r["recommended_price"])
        if dup or len({r["sku"] for r in d}) != len(d):
            raise SystemExit(f"BLOS {ch}: duplicate sku rows - not choosing one")
        api_n[ch], api_skus[ch] = len(d), {r["sku"] for r in d}
        api_flag[ch] = {r["sku"]: bool(r.get("carrier_incl_in_price")) for r in d}
        chans[ch] = {"rows": len(d), "site_currency": sorted({(r["site"], r["currency"]) for r in d}),
                     "api_priced": len(prices), "prices": prices, "source": "api recommended_price"}

    # the BLOS screen's RECOMMENDED PRICE column (also covers combos, which the API leaves empty)
    screen = blos_screen(u, p, api_n)
    del u, p
    for ch, s in screen.items():
        save_raw(day, f"blos_screen_{ch}", json.dumps({"columns": ["sku", "product_cost_healthy", "rec_price_exc_postage",
                                                                   "recommended_price", "live_price", "carrier_charge"], "rows": s["rows"]}).encode())
        if {r[0] for r in s["rows"]} != api_skus[ch]:
            raise SystemExit(f"BLOS screen {ch}: SKU set differs from the API")
        api = chans[ch]["prices"]
        diff = [(k, v, s["rec"].get(k)) for k, v in api.items() if s["rec"].get(k) is None or float(s["rec"][k]) != float(v)]
        if diff:
            raise SystemExit(f"BLOS screen {ch}: RECOMMENDED PRICE disagrees with API recommended_price {diff[:5]}")
        flag = api_flag[ch]
        pdiff = [(k, s["post"].get(k), flag[k]) for k in api if (s["post"].get(k) == "I") != flag[k]]
        if pdiff:
            raise SystemExit(f"BLOS {ch}: postage status from screen columns disagrees with API carrier_incl_in_price {pdiff[:5]}")
        chans[ch].update(prices=s["rec"], screen_priced=len(s["rec"]),
                         postage=s["post"], postage_source="screen RECOMMENDED PRICE > REC PRICE (EXC POSTAGE) = Included; equal = Not Included; equals API carrier_incl_in_price wherever the API prices the SKU",
                         source="BLOS SKU Price screen RECOMMENDED PRICE (default state, Show Packs); equals API recommended_price wherever the API has one")

    snap = {"date": day, "fetched_at": now.isoformat(timespec="seconds"),
            "bos": {"page": BOS_PAGE, "feed": BOS_FEED, "feed_source": bos.get("source"), "published_at": bos.get("published_at"),
                    "rows": len(rows), "healthy": healthy},
            "blos": {"api": BLOS + "/api/listing-channel-price", "screen": LCP + " (SKU Price)", "field": "recommended_price",
                     "channels": chans}}
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(os.path.join(SNAP_DIR, day + ".json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, separators=(",", ":"), ensure_ascii=False)
    print(f"snapshot {day}: BOS healthy={len(healthy)} (feed rows {len(rows)}, published {bos.get('published_at')}); "
          + "; ".join(f"BLOS {c}: rows={v['rows']} api_priced={v['api_priced']} priced={len(v['prices'])}" for c, v in chans.items()))


if __name__ == "__main__":
    main()
