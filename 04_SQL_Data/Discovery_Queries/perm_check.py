import os, psycopg2
c = psycopg2.connect(os.environ["DATABASE_URL"]); cur = c.cursor()
cur.execute("select current_database(), current_user")
print(cur.fetchone())
for t in ["public.order_transaction","public.traffic_data","public.ppc","public.ppc_performance","analytics.ph_segment"]:
    try:
        cur.execute(f"select 1 from {t} limit 1"); print(t, "OK")
    except Exception as e:
        print(t, "DENIED", str(e).splitlines()[0]); c.rollback()
