"""Download pinned, local-only Pyodide assets and their verified scientific wheels."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.29.2"
BASE = "https://cdn.jsdelivr.net/pyodide/v" + VERSION + "/full/"
ROOT = Path(__file__).resolve().parents[1] / "static" / "pyodide"


def download(name, sha=None):
    path = ROOT / name
    if path.exists() and (not sha or hashlib.sha256(path.read_bytes()).hexdigest() == sha):
        return
    with urlopen(BASE + name, timeout=120) as response:
        data = response.read()
    if sha and hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("Hash mismatch: " + name)
    path.write_bytes(data)
    print(name + " " + str(len(data)), flush=True)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"):
        download(name)
    packages = json.loads((ROOT / "pyodide-lock.json").read_text())["packages"]
    loaded = set()
    def package(name):
        if name in loaded: return
        loaded.add(name)
        entry = packages[name]
        for dependency in entry.get("depends", []): package(dependency)
        download(entry["file_name"], entry["sha256"])
    package("numpy"); package("pandas")
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in ROOT.iterdir() if path.is_file()}
    (ROOT / "asset-hashes.json").write_text(json.dumps({"version": VERSION, "files": manifest}, indent=2))


if __name__ == "__main__": main()
