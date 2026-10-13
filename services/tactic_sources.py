"""Export trusted tactic source; custom execution stays in browser workers."""
from pathlib import Path
import json
from functools import lru_cache
from threading import Lock
from flask import Blueprint, jsonify, Response, request
from services import hidden_subprocess as subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("ZGUA", "RelativeStrengthV2", "RelativeStrengthFreshV3", "EmaBounceV2", "SimpleAtrTrendV2", "PullbackReclaimV2", "BreakoutRetestV2")
blueprint = Blueprint("tactic_sources", __name__, url_prefix="/api/tactic-source")

@blueprint.before_request
def require_tactic_access():
    from services.account_security import protect_tactic_request, request_role
    if request_role() not in ('admin','tactics') and request.method=='GET' and request.path in ('/api/tactic-source/RelativeStrengthV2','/api/tactic-source/runtime.js','/api/tactic-source/worker.js'):
        return None
    return protect_tactic_request()

_generation_lock = Lock()


def generated(arg):
    files = [ROOT / "cli/tactic_source.js"] + sorted((ROOT / "core").rglob("*.js"))
    version = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files)
    with _generation_lock:
        return cached_generated(arg, version)


@lru_cache(maxsize=8)
def cached_generated(arg, version):
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
    from services.chart_accounts import account_origin
    response = Response((ROOT / "static/custom-tactic-worker.js").read_text(encoding="utf-8"), mimetype="application/javascript")
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src " + account_origin() + "/api/tactic-source/runtime.js 'unsafe-eval'; connect-src 'none'; worker-src 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response

@blueprint.get("/<name>")
def source(name):
    if name not in NAMES: return jsonify({"error": "Unknown tactic"}), 404
    response = jsonify(json.loads(generated(name)))
    response.headers["Cache-Control"] = "no-store"
    return response
