FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ZGUA_QUIET=1 \
    ZGUA_ENV=production \
    ZGUA_CACHE_CANDLE_BUDGET=60000 \
    NODE_OPTIONS=--max-old-space-size=128

WORKDIR /app

COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-production.lock ./
RUN pip install --no-cache-dir -r requirements-production.lock

COPY . .
RUN python scripts/install_indicator_runtime.py

EXPOSE 10000

CMD ["python", "scripts/start_web.py"]
