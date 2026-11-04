"""Look up OpenAlex source records (journals / conference proceedings) by name.

Usage: python -I ps/openalex_sources.py "IEEE Transactions on Image Processing" "Pattern Recognition" ...
Each name costs one OpenAlex search ($0.001). Goes through netlib (rate limits + ledger).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netlib  # noqa: E402

FIELDS = "id,display_name,type,issn_l,issn,works_count,host_organization_name,alternate_titles,abbreviated_title"


def lookup(name: str, n: int = 4) -> list[dict]:
    data = netlib.call("openalex", "https://api.openalex.org/sources",
                       {"search": name, "per_page": n, "select": FIELDS,
                        "api_key": netlib.env("OPENALEX_API_KEY")})
    out = []
    for s in data.get("results", []):
        out.append({"openalex": s["id"].rsplit("/", 1)[-1], "name": s["display_name"], "type": s.get("type"),
                    "issn_l": s.get("issn_l"), "issn": s.get("issn"), "works": s.get("works_count"),
                    "abbr": s.get("abbreviated_title"), "publisher": s.get("host_organization_name")})
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = {name: lookup(name) for name in sys.argv[1:]}
    print(json.dumps(result, ensure_ascii=False, indent=1))
