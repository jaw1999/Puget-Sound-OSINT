#!/usr/bin/env python3
"""
Discover working WSDOT ferry camera URLs.

The URL patterns vary by terminal, so we need to probe multiple patterns.
"""

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Known terminal slugs and variations to try
TERMINALS = {
    "anacortes": ["anacortes", "ana"],
    "bainbridge": ["bainbridge", "bainbridgeisland", "bainbridge_island"],
    "bremerton": ["bremerton", "brem"],
    "clinton": ["clinton"],
    "coupeville": ["coupeville", "keystone"],
    "edmonds": ["edmonds"],
    "fauntleroy": ["fauntleroy"],
    "fridayharbor": ["fridayharbor", "friday_harbor", "friday"],
    "kingston": ["kingston"],
    "lopez": ["lopez", "lopezisland"],
    "mukilteo": ["mukilteo"],
    "orcas": ["orcas", "orcasisland"],
    "pointdefiance": ["pointdefiance", "point_defiance", "pt_defiance", "ptdefiance"],
    "porttownsend": ["porttownsend", "port_townsend", "pt_townsend"],
    "seattle": ["seattle", "colman", "colmandock"],
    "southworth": ["southworth"],
    "tahlequah": ["tahlequah"],
    "vashon": ["vashon", "vashonisland"],
}

# URL patterns to try
URL_PATTERNS = [
    "https://images.wsdot.wa.gov/wsf/{slug}/terminal/{slug}.jpg",
    "https://images.wsdot.wa.gov/wsf/{slug}/{slug}.jpg",
    "https://images.wsdot.wa.gov/wsf/{slug}/wsf_{slug}.jpg",
    "https://images.wsdot.wa.gov/ferries/{slug}.jpg",
    "https://images.wsdot.wa.gov/wsf/{slug}.jpg",
]


def check_url(url: str) -> tuple:
    """Check if URL returns 200."""
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        return (url, resp.status_code == 200, resp.status_code)
    except Exception as e:
        return (url, False, str(e))


def discover_terminal(terminal: str, slugs: list) -> dict:
    """Discover working URL for a terminal."""
    urls_to_try = []
    for slug in slugs:
        for pattern in URL_PATTERNS:
            urls_to_try.append(pattern.format(slug=slug))

    for url in urls_to_try:
        _, ok, status = check_url(url)
        if ok:
            return {"terminal": terminal, "url": url, "status": "OK"}

    return {"terminal": terminal, "url": None, "status": "NOT FOUND"}


def main():
    print("Discovering WSDOT Ferry Camera URLs...")
    print("=" * 60)

    working = []
    not_found = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(discover_terminal, term, slugs): term
            for term, slugs in TERMINALS.items()
        }

        for future in as_completed(futures):
            result = future.result()
            if result["url"]:
                working.append(result)
                print(f"✓ {result['terminal']}: {result['url']}")
            else:
                not_found.append(result)
                print(f"✗ {result['terminal']}: NOT FOUND")

    print("\n" + "=" * 60)
    print(f"Found: {len(working)}/{len(TERMINALS)}")

    if working:
        print("\n# Working URLs (copy to cameras.yaml):")
        for r in sorted(working, key=lambda x: x["terminal"]):
            print(f"  - id: {r['terminal']}")
            print(f"    url: {r['url']}")

    # Also try the known third-party cameras
    print("\n\nChecking third-party cameras...")
    third_party = [
        ("clinton_wsdot", "https://images.wsdot.wa.gov/wsf/clinton/terminal/clinton.jpg"),
        ("clinton_uphill", "http://camserv.whidbeyhost.com/boothhill.jpg"),
        ("clinton_east", "http://camserv.whidbeyhost.com/clinteast.jpg"),
        ("clinton_west", "http://camserv.whidbeyhost.com/clintwest.jpg"),
        ("clinton_dock", "http://camserv.whidbeyhost.com/boothdock.jpg"),
    ]

    for name, url in third_party:
        _, ok, status = check_url(url)
        status_str = "✓ OK" if ok else f"✗ {status}"
        print(f"  {name}: {status_str}")


if __name__ == "__main__":
    main()
