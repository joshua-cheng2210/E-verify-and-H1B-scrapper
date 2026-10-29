"""Download the E-Verify employer list as one CSV per state.

Uses the same headless-browser + Tableau Embedding API approach as
everify_search.py, but sets the "State" filter (a plain categorical filter,
so it can be set through the API directly) instead of typing a name.

An employer that hires in several states appears in each of those states' files.

Usage:
  python everify_states.py                    # every state -> .../Claude/Projects/CS/E verify/Employer List XX.csv
  python everify_states.py MN CA TX           # just these states
  python everify_states.py --out data --years 5
  python everify_states.py --skip-existing    # resume an interrupted run


python everify_states.py AK AL AR AS AZ CA CO CT DC DE FL FM GA GU HI IA ID IL IN KS KY LA MA MD ME MI MN MO MP MS MT NC ND NE NH NJ NM NV NY OH OK OR PA PR RI SC SD TN TX UT VA VI VT WA WI WV WY
"""
import argparse
import csv
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_OUT = r"C:\Users\Admin\Documents\Claude\Projects\CS\E verify"

from everify_search import JS_READ_ROWS, JS_SET_DATE_RANGE, JS_WAIT_FOR_VIZ, PAGE_URL, RENAME, RESULT_SHEET

# Lists the State filter's possible values, so territories (PR, GU, VI...) are included.
JS_LIST_STATES = """async () => {
  const ws = document.getElementById('tableau-viz').workbook.activeSheet
    .worksheets.find(w => w.name === 'Employer List');
  const f = (await ws.getFiltersAsync()).find(f => f.fieldName === 'State');
  const domain = await f.getDomainAsync('database');
  return domain.values.map(v => v.value).filter(v => v && !v.includes('%') && v !== 'Null');  // drop '%null%'
}"""

JS_SET_STATE = """async (state) => {
  const ws = document.getElementById('tableau-viz').workbook.activeSheet
    .worksheets.find(w => w.name === 'Employer List');
  await ws.applyFilterAsync('State', [state], 'replace');
}"""


def write_csv(path, data):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(RENAME.values()))
        w.writeheader()
        for raw in data["rows"]:
            rec = dict(zip(data["columns"], raw))
            w.writerow({nice: rec.get(col, "").replace("Null", "") for col, nice in RENAME.items()})


_log_file = None


def log(msg):
    """Print progress to the console and, with --log, append it to the log file."""
    print(msg, file=sys.stderr, flush=True)
    if _log_file:
        _log_file.write(msg + "\n")
        _log_file.flush()


def main():
    global _log_file
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("states", nargs="*", help="State codes (default: all)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"Output folder (default {DEFAULT_OUT})")
    ap.add_argument("--years", type=int, default=40, help="Enrolled within the last N years (default 40 = all)")
    ap.add_argument("--skip-existing", action="store_true", help="Skip states whose CSV already exists")
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
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90_000)
        page.evaluate(JS_WAIT_FOR_VIZ)
        page.evaluate(JS_SET_DATE_RANGE, args.years)

        states = [s.upper() for s in args.states] or page.evaluate(JS_LIST_STATES)
        log(f"{len(states)} state(s): {' '.join(states)}")

        total = 0
        for i, state in enumerate(states, 1):
            path = out / f"Employer List {state}.csv"
            if args.skip_existing and path.exists():
                log(f"[{i}/{len(states)}] {state}: exists, skipped")
                continue
            t = time.time()
            page.evaluate(JS_SET_STATE, state)
            data = page.evaluate(JS_READ_ROWS, RESULT_SHEET)
            write_csv(path, data)
            total += len(data["rows"])
            log(f"[{i}/{len(states)}] {state}: {len(data['rows']):,} rows -> {path} "
                f"({time.time() - t:.0f}s)")

        browser.close()
    log(f"Done. {total:,} rows written to {out}/")


if __name__ == "__main__":
    main()
