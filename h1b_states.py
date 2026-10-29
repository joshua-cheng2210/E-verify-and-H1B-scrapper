"""Download H-1B employer data (USCIS H-1B Employer Data Hub) as one CSV per state.

Two sources, because USCIS publishes them differently:

  * FY2009-2023: official CSV exports from the hub's archive page. Full detail:
    initial/continuing approvals and denials, NAICS, Tax ID, city, ZIP.
  * FY2024 onward: only in the Tableau dashboard. Read the same way as
    everify_states.py (headless browser + Tableau Embedding API), one fiscal year
    and state at a time. The dashboard only exposes employer name and total
    approvals, so the other columns are blank for those years.

Output: "H1B Employers XX.csv" per state, rows sorted newest fiscal year first.

Usage:
  python h1b_states.py                       # all states, all years
  python h1b_states.py MN CA                 # just these states
  python h1b_states.py --from-year 2020
  python h1b_states.py --log logs\\h1b.log
"""
import argparse
import csv
import io
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

from everify_search import JS_WAIT_FOR_VIZ

DEFAULT_OUT = r"C:\Users\Admin\Documents\Claude\Projects\CS\H1B"
PAGE_URL = "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub"
ARCHIVE_URL = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
ARCHIVE_YEARS = range(2009, 2024)  # archive page has 2009-2023
SHEET = "Approvals/Receipts"

COLUMNS = ["Fiscal Year", "Employer", "Initial Approval", "Initial Denial", "Continuing Approval",
           "Continuing Denial", "Total Approvals", "NAICS", "Tax ID", "State", "City", "ZIP", "Source"]

# The dashboard's Fiscal Year field name has trailing spaces, so look it up instead of hardcoding.
JS_SETUP = """async () => {
  const ws = document.getElementById('tableau-viz').workbook.activeSheet
    .worksheets.find(w => w.name === 'Approvals/Receipts');
  await ws.clearFilterAsync('AGG(Rank)');   // the dashboard shows only the top 100 by default
  const fs = await ws.getFiltersAsync();
  const fy = fs.find(f => f.fieldName.startsWith('Fiscal Year'));
  const st = fs.find(f => f.fieldName === 'Petitioner State');
  return {
    fyField: fy.fieldName,
    years: (await fy.getDomainAsync('database')).values.map(v => v.formattedValue),
    states: (await st.getDomainAsync('database')).values.map(v => v.formattedValue)
      .filter(v => v && !v.includes('%') && v !== 'Null'),
  };
}"""

JS_QUERY = """async ([fyField, year, state]) => {
  const ws = document.getElementById('tableau-viz').workbook.activeSheet
    .worksheets.find(w => w.name === 'Approvals/Receipts');
  await ws.applyFilterAsync(fyField, [year], 'replace');
  await ws.applyFilterAsync('Petitioner State', [state], 'replace');
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

_log_file = None


def log(msg):
    print(msg, file=sys.stderr, flush=True)
    if _log_file:
        _log_file.write(msg + "\n")
        _log_file.flush()


def load_archive(year, cache_dir, http):
    """Download (once, through the browser) and parse one fiscal year's official CSV export."""
    path = cache_dir / f"h1b_datahubexport-{year}.csv"
    if not path.exists():
        resp = http.get(ARCHIVE_URL.format(year=year), timeout=120_000)
        if not resp.ok:
            raise RuntimeError(f"FY{year} archive download failed: HTTP {resp.status}")
        path.write_bytes(resp.body())
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    for rec in csv.DictReader(io.StringIO(text)):
        rec = {k.strip(): (v or "").strip() for k, v in rec.items() if k}
        try:
            total = int(rec.get("Initial Approval") or 0) + int(rec.get("Continuing Approval") or 0)
        except ValueError:
            total = ""
        yield {**{c: rec.get(c, "") for c in COLUMNS}, "Total Approvals": total, "Source": "archive CSV"}


def main():
    global _log_file
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("states", nargs="*", help="State codes (default: all)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"Output folder (default {DEFAULT_OUT})")
    ap.add_argument("--from-year", type=int, default=2009, help="Earliest fiscal year (default 2009)")
    ap.add_argument("--show", action="store_true", help="Show the browser window")
    ap.add_argument("--log", help="Also append progress and errors to this file")
    args = ap.parse_args()

    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        _log_file = open(args.log, "a", encoding="utf-8")
    log(f"=== Started {time.strftime('%Y-%m-%d %H:%M:%S')}: {' '.join(sys.argv[1:])}")
    try:
        run(args)
    except Exception:
        log(traceback.format_exc().rstrip())
        log(f"=== FAILED {time.strftime('%Y-%m-%d %H:%M:%S')}")
        sys.exit(1)
    log(f"=== Finished {time.strftime('%Y-%m-%d %H:%M:%S')}")


def run(args):
    out = Path(args.out)
    cache = out / "_archive_source"
    cache.mkdir(parents=True, exist_ok=True)
    wanted = {s.upper() for s in args.states}
    by_state = defaultdict(list)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90_000)

        # 1) Archive years: full-detail CSVs, split by state. uscis.gov rejects plain
        #    scripted downloads, so fetch them through the browser session.
        for year in ARCHIVE_YEARS:
            if year < args.from_year:
                continue
            n = 0
            for rec in load_archive(year, cache, page.context.request):
                st = rec["State"].upper()
                # skip blanks and junk values (one archive row has a control character)
                if len(st) == 2 and st.isalpha() and (not wanted or st in wanted):
                    by_state[st].append(rec)
                    n += 1
            log(f"FY{year}: {n:,} rows from archive CSV")

        # 2) Newer years: only available in the dashboard.
        page.evaluate(JS_WAIT_FOR_VIZ)
        meta = page.evaluate(JS_SETUP)

        years = sorted(y for y in meta["years"]
                       if y.isdigit() and int(y) > max(ARCHIVE_YEARS) and int(y) >= args.from_year)
        states = sorted(wanted) if wanted else meta["states"]
        log(f"Dashboard years {', '.join(years)} x {len(states)} state(s)")

        for year in years:
            t, n = time.time(), 0
            for state in states:
                data = page.evaluate(JS_QUERY, [meta["fyField"], year, state])
                for raw in data["rows"]:
                    rec = dict(zip(data["columns"], raw))
                    by_state[state].append({
                        "Fiscal Year": year,
                        "Employer": rec.get("Employer (Petitioner) Name", ""),
                        "Total Approvals": rec.get("SUM(Total Approvals)", "").replace(",", ""),
                        "State": state,
                        "Source": "dashboard",
                    })
                    n += 1
            log(f"FY{year}: {n:,} rows from dashboard ({time.time() - t:.0f}s)")
        browser.close()

    # 3) One file per state.
    for state in sorted(by_state):
        rows = sorted(by_state[state], key=lambda r: (-int(r["Fiscal Year"]), r["Employer"]))
        path = out / f"H1B Employers {state}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, restval="")
            w.writeheader()
            w.writerows(rows)
        log(f"{state}: {len(rows):,} rows -> {path}")
    log(f"Done. {sum(len(v) for v in by_state.values()):,} rows in {len(by_state)} files.")


if __name__ == "__main__":
    main()
