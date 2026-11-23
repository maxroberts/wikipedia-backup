"""Browsing benchmark: time kiwix-serve requests for one ZIM file on one drive.

Used in Stage 5 to compare the SSD hot copy with the HDD archive copy of the same ZIM
("Browsing benchmark" in ``docs/kiwix_backup_plan.md``). Run it once per copy:

    uv run python scripts/bench_browse.py --zim /Volumes/BackUp_T7/wikipedia-storage/X.zim --label ssd
    uv run python scripts/bench_browse.py --zim /Volumes/Starfish_Prime/kiwix-data/zim/X.zim --label hdd

What it does:

1. Starts its own kiwix-serve (the installed one, ``software.tool_path``) on
   ``127.0.0.1:--port``, serving just that ZIM file. So it measures disk and kiwix-serve,
   not Wi-Fi, and doesn't touch the launchd server.
2. For each request type, it requests 5 different items **cold** (the first request for
   that item in this run), then the same 5 again **warm**.
   - ``text``: an article's HTML.
   - ``images``: an article's HTML plus every ``<img>`` it references, fetched one after
     another.
   - ``suggest``: a title suggestion (the search box's autocomplete).
   - ``search``: a full-text search.
3. Prints the median and maximum time per request type and condition, in seconds, and
   writes all timings to ``--out`` as JSON.

Limits (also recorded in the plan):

- "Cold" means "not requested before in this run". It does **not** clear macOS's page
  cache, so data cached by an earlier run or by the other copy's run can make a later run
  look faster. Run each copy only once per boot, or alternate the order between runs.
- It doesn't force the HDD to spin down first, so spin-up delay isn't measured.
- The default article and search lists are for English Wikipedia. Override them with
  ``--text``, ``--image`` and ``--terms`` for another ZIM, e.g. the small test ZIM.
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from wikibackup.config import load_config
from wikibackup.software import tool_path

TEXT_ARTICLES = ["Epistemology", "Common_law", "Monetary_policy", "Linear_algebra", "Ontology"]
IMAGE_ARTICLES = ["Solar_System", "Tyrannosaurus", "Leonardo_da_Vinci", "Paris", "Great_Barrier_Reef"]
TERMS = ["quantum entanglement", "medieval castles", "volcanic eruption", "baroque music",
         "coral reef bleaching"]


def get(url: str) -> bytes:
    """GET ``url`` and return the body; raises on HTTP errors."""
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def timed(fn) -> float:
    """Seconds taken by ``fn()``."""
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def fetch_with_images(base: str, book: str, article: str) -> int:
    """Fetch an article and every ``<img src>`` in it, one at a time; return the number of
    images. Relative image paths are resolved against the article's URL."""
    page_url = f"{base}/content/{book}/{article}"
    html = get(page_url).decode("utf-8", "replace")
    sources = re.findall(r'<img[^>]+src="([^"]+)"', html)
    for src in sources:
        get(urllib.parse.urljoin(page_url, src))
    return len(sources)


def wait_for(url: str, seconds: float = 30) -> None:
    """Wait until ``url`` answers, or raise ``TimeoutError``."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            get(url)
            return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"kiwix-serve didn't answer at {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--zim", type=Path, required=True, help="ZIM file to serve")
    parser.add_argument("--label", required=True, help="name for this run, e.g. ssd or hdd")
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--text", default=",".join(TEXT_ARTICLES), help="comma-separated")
    parser.add_argument("--image", default=",".join(IMAGE_ARTICLES), help="comma-separated")
    parser.add_argument("--terms", default=",".join(TERMS), help="comma-separated")
    parser.add_argument("--out", type=Path, default=None, help="JSON output (default: bench_<label>.json)")
    args = parser.parse_args()

    book = args.zim.name.removesuffix(".zim")
    base = f"http://127.0.0.1:{args.port}"
    serve = tool_path(load_config(), "kiwix-serve")
    proc = subprocess.Popen(
        [str(serve), "--address", "127.0.0.1", "--port", str(args.port), str(args.zim)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results: dict[str, dict[str, list[float]]] = {}
    try:
        wait_for(base + "/")
        q = urllib.parse.quote
        jobs = {
            "text": [lambda a=a: get(f"{base}/content/{book}/{a}") for a in args.text.split(",")],
            "images": [lambda a=a: fetch_with_images(base, book, a) for a in args.image.split(",")],
            "suggest": [lambda t=t: get(f"{base}/suggest?content={book}&term={q(t[:6])}")
                        for t in args.terms.split(",")],
            "search": [lambda t=t: get(f"{base}/search?content={book}&pattern={q(t)}")
                       for t in args.terms.split(",")],
        }
        for kind, calls in jobs.items():
            cold = [timed(c) for c in calls]
            warm = [timed(c) for c in calls]
            results[kind] = {"cold": cold, "warm": warm}
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print(f"{args.label}: {args.zim}")
    print(f"{'request':<10}{'cold median':>12}{'cold max':>10}{'warm median':>13}{'warm max':>10}")
    for kind, r in results.items():
        print(f"{kind:<10}{statistics.median(r['cold']):>12.3f}{max(r['cold']):>10.3f}"
              f"{statistics.median(r['warm']):>13.3f}{max(r['warm']):>10.3f}")
    out = args.out or Path(f"bench_{args.label}.json")
    out.write_text(json.dumps({"label": args.label, "zim": str(args.zim), "seconds": results}, indent=2))
    print(f"timings written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
