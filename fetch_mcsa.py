"""Fetch M-CSA entries with pagination, cache to JSON."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

OUT = Path("D:/documents/ProteoSphereV2/cache/mcsa/mcsa_entries.json")
URL = "https://www.ebi.ac.uk/thornton-srv/m-csa/api/entries/?format=json"


def main() -> None:
    results = []
    url = URL
    page = 1
    while url:
        print(f"[mcsa] fetching page {page}: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "proteosphere/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        results.extend(d.get("results", []))
        url = d.get("next")
        page += 1
        time.sleep(0.25)  # rate-limit polite
        if page > 50:
            print("[mcsa] safety limit hit")
            break
    print(f"[mcsa] total entries: {len(results)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"count": len(results), "results": results}), encoding="utf-8")
    print(f"[mcsa] saved to {OUT}")


if __name__ == "__main__":
    main()
