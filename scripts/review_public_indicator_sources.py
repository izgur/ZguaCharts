"""Review a fixed set of public scripts; never fetch protected script source."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "utbot": "n8ss8BID-UT-Bot-Alerts",
    "squeeze_lb": "nqQ1DT5a-Squeeze-Momentum-Indicator-LazyBear",
    "wavetrend_lb": "2KE8wTuF-Indicator-WaveTrend-Oscillator-WT",
    "vixfix": "og7JPrRA-CM-Williams-Vix-Fix-Finds-Market-Bottoms",
    "supertrend_kivanc": "r6dAP7yi",
    "macd_mtf": "OQx7vju0-MacD-Custom-Indicator-Multiple-Time-Frame-All-Available-Options",
    "smc_luxalgo": "CnB3fSph-Smart-Money-Concepts-SMC-LuxAlgo",
    "sr_breaks": "JDFoWQbL-Support-and-Resistance-Levels-with-Breaks-LuxAlgo",
    "msb_ob": "DkE7UniD",
    "sr_channels": "Ej53t8Wv-Support-Resistance-Channels",
}


def get(url):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
        return response.read().decode("utf-8")


def main():
    folder = ROOT / "reports" / "indicator-source-review"
    folder.mkdir(parents=True, exist_ok=True)
    reviewed = []
    for key, slug in TARGETS.items():
        row = {"id": key, "url": "https://www.tradingview.com/script/" + slug + "/"}
        try:
            page = get(row["url"])
            match = re.search(r'"script"\s*:\s*(\{)', page)
            if not match:
                raise ValueError("Published script metadata not available")
            metadata, _ = json.JSONDecoder().raw_decode(page[match.start(1):])
            row.update(access=metadata.get("access"), version=metadata.get("version_maj"))
            if metadata.get("access") != 1:
                row["status"] = "protected; not fetched"
            else:
                url = "https://pine-facade.tradingview.com/pine-facade/get/" + quote(metadata["script_id_part"], safe="") + "/" + str(metadata["version_maj"])
                published = json.loads(get(url))
                source = published.get("source", "")
                if not source:
                    raise ValueError("Public source response was empty")
                row.update(lines=len(source.splitlines()), sha256=hashlib.sha256(source.encode()).hexdigest(),
                           header=source.splitlines()[:6])
                restricted = "CC BY-NC" in source or "creativecommons.org/licenses/by-nc" in source
                row["status"] = "restricted licence; no port" if restricted else "public source retrieved; port review required"
                # Restricted works are not redistributed in this project's source exports.
                if not restricted:
                    (folder / (key + ".pine")).write_text(source, encoding="utf-8")
        except Exception as exc:
            row.update(status="source retrieval failed", error=str(exc))
        reviewed.append(row)
        print(json.dumps(row), flush=True)
    (folder / "manifest.json").write_text(json.dumps(reviewed, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
