# Content Hub mirror: nginx serving a merged content-hub.json + local content,
# with an optional mutual-TLS reverse proxy to the entitled Fortinet upstream.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx openssl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# pyfsr provides the catalog builder/validator (build_catalog.py imports it).
# Prefer a locally-built wheel dropped in ./wheels (run ./build.sh) so the image
# has the current content_catalog module; fall back to the published package.
COPY wheels/ /tmp/wheels/
RUN if ls /tmp/wheels/*.whl >/dev/null 2>&1; then \
        pip install --no-cache-dir /tmp/wheels/*.whl; \
    else \
        pip install --no-cache-dir pyfsr; \
    fi \
    && rm -rf /tmp/wheels

# Admin GUI/API deps (Flask); build_catalog + admin app both import pyfsr.
RUN pip install --no-cache-dir flask

COPY build_catalog.py /app/build_catalog.py
COPY admin/ /app/admin/
COPY entrypoint.sh /app/entrypoint.sh
COPY chctl /usr/local/bin/chctl
RUN chmod +x /app/entrypoint.sh /usr/local/bin/chctl

ENV OUTPUT_DIR=/srv \
    LOCAL_CONTENT_DIR=/local-content \
    ARTIFACTS_DIR=/artifacts \
    ADMIN_PORT=9000 \
    ADMIN_ENABLED=1 \
    UPSTREAM_PROXY=1

EXPOSE 80 443 9000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost/healthz')" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
