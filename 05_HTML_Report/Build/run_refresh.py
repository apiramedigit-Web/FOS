"""Daily refresh of the eBay Final Output Sales dashboard (existing pipeline + Price & SKU tab + daily data).

    python run_refresh.py               full refresh: PH map, sales/traffic/ads, day-level data, prices, build, all validations
    python run_refresh.py --prices-only  new BOS/BLOS price snapshot + rebuild + validations (no sales re-extract)

Each step must succeed before the next runs. The dashboard and its data files are backed up first and RESTORED if any
step fails, so a failed run never leaves an unvalidated dashboard in place (dated snapshot files stay, they are history).
Every run appends one line to logs/run_log.jsonl. It never publishes to the Hub and never commits to Git.
Scheduled daily at 08:00 by the Windows task "EFOS_Daily_Final_Output_Sales_Refresh" (logs/scheduled_task.xml).
"""
import datetime, json, os, shutil, subprocess, sys, tempfile, time

BASE = os.path.dirname(os.path.abspath(__file__))
FULL = [("03_SQL", "extract_ph.py"), ("03_SQL", "extract.py"), ("06_Validation", "validate_data.py"),
        ("03_SQL", "extract_daily.py"), ("03_SQL", "extract_prices.py"), ("03_SQL", "extract_listing_prices.py"), ("04_HTML_Report", "build.py"),
        ("06_Validation", "validate_html.py"), ("06_Validation", "validate_ph.py"), ("06_Validation", "validate_price_tab.py"),
        ("06_Validation", "validate_main_daily.py")]
PRICES_ONLY = [s for s in FULL if s[1] not in ("extract_ph.py", "extract.py", "validate_data.py", "extract_daily.py")]
# outputs restored on failure (relative to BASE)
GUARDED = ["04_HTML_Report/eBay_Final_Output_Sales_September_2026.html", "04_HTML_Report/data/dataset.json",
           "04_HTML_Report/data/id_rows.json", "04_HTML_Report/data/ph_map.json", "04_HTML_Report/data/daily_detail.json", "04_HTML_Report/data/listing_prices.json",
           "06_Validation/reports/reconciliation.json"]
LOG = os.path.join(BASE, "logs", "run_log.jsonl")


def main():
    mode = "prices-only" if "--prices-only" in sys.argv else "full"
    steps = PRICES_ONLY if mode == "prices-only" else FULL
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    started = datetime.datetime.now()
    bak = tempfile.mkdtemp(prefix="efos_backup_")
    for f in GUARDED:
        src = os.path.join(BASE, f)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(bak, f)), exist_ok=True); shutil.copy2(src, os.path.join(bak, f))
    done, failed = [], None
    for folder, script in steps:
        t = time.time()
        print(f"== {folder}/{script}", flush=True)
        rc = subprocess.call([sys.executable, script], cwd=os.path.join(BASE, folder), env=env)
        print(f"   exit {rc} in {time.time() - t:.0f}s", flush=True)
        done.append({"step": f"{folder}/{script}", "exit": rc, "secs": round(time.time() - t)})
        if rc:
            failed = f"{folder}/{script}"
            break
    if failed:
        for f in GUARDED:
            b = os.path.join(bak, f)
            if os.path.exists(b):
                shutil.copy2(b, os.path.join(BASE, f))
        print(f"STOPPED at {failed}: previous dashboard and data restored", flush=True)
    else:
        print("refresh complete - all validations passed", flush=True)
    shutil.rmtree(bak, ignore_errors=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"started": started.isoformat(timespec="seconds"), "finished": datetime.datetime.now().isoformat(timespec="seconds"),
                            "mode": mode, "status": "FAILED" if failed else "OK", "failed_step": failed, "steps": done}) + "\n")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
