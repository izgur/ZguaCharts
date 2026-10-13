"""Validate production before starting the existing bounded Gunicorn server."""
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.deployment import database_errors, production_mode, validate_environment, runtime_assets_errors


def gunicorn_command(env=None):
    env = os.environ if env is None else env
    port = env.get("PORT", "10000")
    if not isinstance(port, str) or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("PORT")
    return [sys.executable, "-m", "gunicorn", "app:app", "--bind", "0.0.0.0:" + port,
            "--workers", "1", "--threads", "2", "--timeout", "180",
            "--max-requests", "500", "--max-requests-jitter", "50"]


def main():
    errors = validate_environment()
    if os.environ.get('RENDER') and not production_mode():
        errors.append('ZGUA_ENV')
    try:
        command = gunicorn_command()
    except ValueError:
        command = None
        errors.append("PORT")
    if not errors and production_mode():
        errors = runtime_assets_errors() or database_errors()
    if errors:
        for error in errors:
            print("FAIL " + error, flush=True)
        return 1
    child_env = os.environ.copy()
    # Prevent ambient Gunicorn arguments from overriding the bounded configuration.
    child_env.pop("GUNICORN_CMD_ARGS", None)
    try:
        child = subprocess.Popen(command, cwd=str(ROOT), env=child_env)
    except OSError:
        print("FAIL web.start", flush=True)
        return 1
    previous = {}
    def forward(signum, frame):
        if child.poll() is None:
            child.send_signal(signum)
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, forward)
        code = child.wait()
        return code if code >= 0 else 128 - code
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
