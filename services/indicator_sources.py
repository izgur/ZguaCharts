"""Read-only exports of allowlisted indicator implementation files."""
from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path
import zipfile

from flask import Blueprint, jsonify, send_file
from indicators import INDICATOR_DEFAULTS, INDICATOR_LABELS, INDICATOR_PARAMETERS
from indicator_catalog import CATALOG
from community_indicators import CATALOG as COMMUNITY_CATALOG
from services.indicator_codegen import single_source

blueprint = Blueprint("indicator_sources", __name__, url_prefix="/api/indicator-source")
ROOT = Path(__file__).resolve().parents[1]


def focus_line(source, key, catalog=False):
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == ("calculate" if catalog else "build_indicator_payload"))
    for node in ast.walk(function):
        if isinstance(node, ast.If):
            constants = [item.s if isinstance(item, ast.Str) else item.value
                         for item in ast.walk(node.test) if isinstance(item, (ast.Constant, ast.Str))]
            if key in constants:
                return node.lineno
    return function.lineno


def source_payload(key):
    if key not in INDICATOR_LABELS:
        raise KeyError(key)
    files = []
    source = single_source(key)
    files.append({"name": key + ".py", "code": source, "focusLine": 1,
                  "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()})
    return {"id": key, "label": INDICATOR_LABELS[key], "parameters": INDICATOR_PARAMETERS[key],
            "defaults": INDICATOR_DEFAULTS[key], "files": files,
            "origin": (COMMUNITY_CATALOG[key]["origin"] + " Author: " + COMMUNITY_CATALOG[key]["author"] + ". " + COMMUNITY_CATALOG[key]["licence"]) if key in COMMUNITY_CATALOG else "Native Python implementation; not a claim of TradingView parity.",
            "sourceUrl": COMMUNITY_CATALOG[key]["sourceUrl"] if key in COMMUNITY_CATALOG else None,
            "dependencies": "NumPy and pandas; portable native formulas with no project imports."}


@blueprint.get("/<key>")
def view_source(key):
    try:
        response = jsonify(source_payload(key))
        response.headers["Cache-Control"] = "no-store"
        return response
    except KeyError:
        return jsonify({"error": "Unknown indicator"}), 404


@blueprint.get("/<key>/download")
def download_source(key):
    try:
        payload = source_payload(key)
    except KeyError:
        return jsonify({"error": "Unknown indicator"}), 404
    runner = ('import json\nimport sys\nfrom ' + key + ' import indicator\n\n'
              'with open(sys.argv[1], encoding="utf-8") as handle:\n'
              '    candles = json.load(handle)\n'
              'result = indicator(candles)\n'
              'print(json.dumps(result, allow_nan=False))\n')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in payload["files"]:
            archive.writestr(item["name"], item["code"])
        archive.writestr("run_indicator.py", runner)
        archive.writestr("requirements.txt", "numpy\npandas\n")
        archive.writestr("COMMUNITY-NOTICES.md", (ROOT / "docs" / "community-indicators.md").read_text(encoding="utf-8"))
        archive.writestr("MPL-2.0.txt", (ROOT / "docs" / "MPL-2.0.txt").read_text(encoding="utf-8"))
        archive.writestr("README.txt", payload["label"] + "\n" + payload["origin"] + "\n" +
                         payload["dependencies"] + "\n\nRun: python run_indicator.py candles.json\n"
                         "Input: JSON array with time (Unix seconds), open, high, low, close, volume.\n"
                         "Only this indicator and its required helper functions are included.\n")
        manifest = {k: v for k, v in payload.items() if k != "files"}
        manifest["files"] = [{"name": file["name"], "sha256": file["sha256"]} for file in payload["files"]]
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    stream.seek(0)
    return send_file(stream, mimetype="application/zip", as_attachment=True,
                     download_name="zguacharts-" + key + "-source.zip", max_age=0)


@blueprint.get("/worker.js")
def indicator_worker():
    from flask import request
    response = send_file(ROOT / "static" / "custom-indicator-worker.js", mimetype="application/javascript", max_age=0)
    asset_root = request.host_url.rstrip("/") + "/static/pyodide/"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src " + asset_root + " 'wasm-unsafe-eval'; connect-src " + asset_root + "; worker-src 'none'; object-src 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response
