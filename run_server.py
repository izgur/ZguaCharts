from __future__ import annotations

from app import app


if __name__ == "__main__":
    from services.chart_alerts import start_monitor
    start_monitor()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False, threaded=True)
