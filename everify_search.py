"""Search the E-Verify employer list from the command line.

The search on e-verify.gov is an embedded Tableau dashboard; the results are
rendered server-side, so there is no plain JSON API to call. This script opens
the page in a headless browser, types into the dashboard's "Business Name" box
(a wildcard / contains match), widens the "Date Enrolled" filter so older
employers are included, then reads the rows through Tableau's official
Embedding API (getSummaryDataReaderAsync).

Setup (once):
  pip install playwright
  python -m playwright install chromium

Usage:
  python everify_search.py "walmart"
  python everify_search.py "acme corp" --csv acme.csv
  python everify_search.py "acme" --years 5      # only enrolled in last 5 years
  python everify_search.py "acme" --show         # watch the browser do it
"""
import argparse
import csv
import json
import sys

from playwright.sync_api import sync_playwright

PAGE_URL = "https://www.e-verify.gov/e-verify-employer-search"
TABLEAU_HOST = "bigdataanalyticspub-sb.uscis.dhs.gov"
RESULT_SHEET = "Employer List"

# Runs inside the page. Waits until the <tableau-viz> element has loaded its workbook.
JS_WAIT_FOR_VIZ = """async () => {
  const viz = document.getElementById('tableau-viz');
  for (let i = 0; i < 90; i++) {
    try {  // the workbook getter throws until the viz is interactive
      if (viz && viz.workbook.activeSheet) return true;
    } catch (e) {}
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error('Tableau dashboard did not load');
}"""

# By default the dashboard only shows employers enrolled in the *current year*.
JS_SET_DATE_RANGE = """async (years) => {
  const ws = document.getElementById('tableau-viz').workbook.activeSheet
    .worksheets.find(w => w.name === 'Employer List');
  await ws.applyRelativeDateFilterAsync('Date Enrolled',
    { periodType: 'years', rangeType: 'last-n', rangeN: years });
}"""

# Reads every row of the result sheet, page by page.
JS_READ_ROWS = """async (sheetName) => {
  const ws = document.getElementById('tableau-viz').workbook.activeSheet
    .worksheets.find(w => w.name === sheetName);
  const reader = await ws.getSummaryDataReaderAsync(10000, { ignoreSelection: true });
  const rows = [];
  let columns = [];
  for (let p = 0; p < reader.pageCount; p++) {
    const page = await reader.getPageAsync(p);
    columns = page.columns.map(c => c.fieldName);
    for (const r of page.data) rows.push(r.map(c => c.formattedValue));
  }
  await reader.releaseAsync();
  return { columns, rows };
}"""

# Friendlier names for Tableau's internal column names; "DAY(...)" and
# "SUM(Number of Records)" are helper fields we drop.
RENAME = {
    "Employer": "Employer",
    "Doing Business As": "DBA",
    "Account Status": "Status",
    "Date Enrolled": "Enrolled",
    "Date Terminated": "Terminated",
    "Workforce Size": "Workforce Size",
    "SUM(Number of Hiring Sites)": "Hiring Sites",
    "Hiring Site Locations": "Hiring States",
    "Opted into E-Verify+": "E-Verify+",
    "Last Updated Date": "Last Updated",
}


def search(name, years=40, show=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not show)
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90_000)
        page.evaluate(JS_WAIT_FOR_VIZ)
        page.evaluate(JS_SET_DATE_RANGE, years)

        # The dashboard lives in a cross-origin iframe; Playwright can still drive it.
        frame = next(f for f in page.frames if TABLEAU_HOST in f.url)
        box = frame.get_by_role("textbox", name="Filter Business Name").locator("visible=true").first
        # Tableau fires a 'filterchanged' event on <tableau-viz> once the filter is applied.
        page.evaluate("""() => {
          window.__filtered = false;
          document.getElementById('tableau-viz')
            .addEventListener('filterchanged', () => { window.__filtered = true; }, { once: true });
        }""")
        box.click()
        box.fill(name)
        box.press("Enter")
        page.wait_for_function("window.__filtered === true", timeout=90_000)

        data = page.evaluate(JS_READ_ROWS, RESULT_SHEET)
        browser.close()

    rows = []
    for raw in data["rows"]:
        rec = dict(zip(data["columns"], raw))
        rows.append({nice: rec.get(col, "").replace("Null", "") for col, nice in RENAME.items()})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="Part of the employer's legal or DBA name (case-insensitive)")
    ap.add_argument("--csv", help="Write results to this CSV file")
    ap.add_argument("--json", action="store_true", help="Print one JSON object per row")
    ap.add_argument("--years", type=int, default=40, help="Enrolled within the last N years (default 40 = all)")
    ap.add_argument("--show", action="store_true", help="Show the browser window")
    args = ap.parse_args()

    rows = search(args.name, years=args.years, show=args.show)
    if not rows:
        print("No results.")
        return

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.csv}")
    elif args.json:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    else:
        for r in rows:
            dba = f" (DBA {r['DBA']})" if r["DBA"] else ""
            term = f", terminated {r['Terminated']}" if r["Terminated"] else ""
            print(f"{r['Status']:<10} {r['Employer']}{dba}")
            print(f"           enrolled {r['Enrolled']}{term} | {r['Workforce Size']} employees"
                  f" | {r['Hiring Sites'] or '?'} sites | states: {r['Hiring States'] or '-'}")
    print(f"\n{len(rows)} match(es)", file=sys.stderr)


if __name__ == "__main__":
    main()
