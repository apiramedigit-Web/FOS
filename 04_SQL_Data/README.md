# 04_SQL_Data
All SQL used in this task, read-only (SELECT) against order_management_copy. 28 statements, all checked valid with EXPLAIN on 2026-10-01.
- Discovery_Queries/discovery_queries_run_2026-10-01.sql – 10 queries run during discovery (Q1–Q9 via MCP, Q10 listing_data SKU coverage) + perm_check.py
- Extraction_Queries/extract_queries.sql – the 9 queries that produce the report data (from extract.py; SQL_ADS and SQL_CREATED run on ledsone)
- Validation_Queries/validate_data_queries.sql – 3 reconciliation queries (from validate_data.py; the ads query runs on ledsone)
- Validation_Queries/audit_fields_queries.sql – 6 field-audit queries (from audit_fields.py; 2 on ledsone)
- Validation_Queries/*.py – the scripts themselves
- Query_Results/ – outputs of those scripts
- export_all_sql.py – regenerates the three .sql files above from the scripts (no DB connection)
