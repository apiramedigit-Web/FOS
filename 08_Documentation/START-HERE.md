# START HERE: eBay Final Output Sales, September 2026

**Final report:** `05_HTML_Report/Current/eBay_Final_Output_Sales_September_2026.html` (open it in Chrome).
**Status and open items:** `11_Closure/closure_note.md`.

## Where things are
| Folder | Contents | Origin |
|---|---|---|
| 01_Requirements | Ebay.xlsx (source of truth) and the CSV export of its last sheet | Copied from the uploads |
| 02_Discovery | Existing-asset, source and duplicate-truth findings | Written from the 2026-10-01 checks |
| 03_Data_Mapping | Source-to-target mapping for all 21 columns | Taken from the build code |
| 04_SQL_Data/Discovery_Queries | Read-only discovery SQL and the permission check | Queries actually run |
| 04_SQL_Data/Validation_Queries | Reconciliation and field-audit scripts (contain the SQL) | Copied from the working project |
| 04_SQL_Data/Query_Results | Reconciliation, field audit and data-quality outputs | Copied script outputs |
| 05_HTML_Report/Current | Final HTML | Copied build output |
| 05_HTML_Report/Previous | v3 HTML, template and build from before the repair | Copied from the archive |
| 05_HTML_Report/Build | extract.py, build.py, rules.py, template | Copied from the working project |
| 06_Evidence/* | Source coverage, reconciliation, ad audits, segment counts, screenshots | Copied or generated from project data |
| 07_Validation | Browser validation script and result, and the reconciliation script | Copied |
| 08_Documentation | README (full project notes), this file, asset manifest | – |
| 09_Skills | References to the skill files used (no copies) | – |
| 10_Prompts | Prompts log | – |
| 11_Closure | Closure note | – |

## Duplicate-truth rule
The pipeline runs in **`C:\Users\LED 222\eBay_Final_Output_Sales`**. Files here are dated copies; their checksums are in `asset_manifest.txt`. After a rebuild, re-copy the outputs and regenerate the manifest. Don't edit the copies.

## Screenshots note
Screenshots in `06_Evidence/Screenshot_Evidence` were taken by the validation runs during the build. Some show earlier layouts: `screenshot_sorted.png` shows sorting that was later removed, and `screenshot_desktop.png`, `_1440`, `_1600`, `_mobile` and `_ledsone_p6` predate the final design. `screenshot_1920.png` and `screenshot_search.png` show the final design.
