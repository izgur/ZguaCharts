"""Export trusted tactic source; custom execution stays in browser workers."""
from pathlib import Path
import json
from flask import Blueprint, jsonify, Response, request
from services import hidden_subprocess as subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("ZGUA", "RelativeStrengthV2", "RelativeStrengthFreshV3", "EmaBounceV2", "SimpleAtrTrendV2", "PullbackReclaimV2", "BreakoutRetestV2")
blueprint = Blueprint("tactic_sources", __name__, url_prefix="/api/tactic-source")

@blueprint.before_request
def require_tactic_access():
    from services.account_security import protect_tactic_request
    return protect_tactic_request()

def generated(arg):
    result = subprocess.run(["node", str(ROOT / "cli/tactic_source.js"), arg], cwd=str(ROOT), capture_output=True, text=True, timeout=15)
    if result.returncode: raise RuntimeError("Tactic source generation failed")
    return result.stdout

@blueprint.get("/runtime.js")
def runtime():
    response = Response(generated("--runtime"), mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    return response

@blueprint.get("/worker.js")
def worker():
    response = Response((ROOT / "static/custom-tactic-worker.js").read_text(encoding="utf-8"), mimetype="application/javascript")
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src " + request.host_url.rstrip('/') + "/api/tactic-source/runtime.js 'unsafe-eval'; connect-src 'none'; worker-src 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response

@blueprint.get("/<name>")
def source(name):
    if name not in NAMES: return jsonify({"error": "Unknown tactic"}), 404
    response = jsonify(json.loads(generated(name)))
    response.headers["Cache-Control"] = "no-store"
    return response
