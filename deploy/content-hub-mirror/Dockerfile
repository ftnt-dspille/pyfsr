# Content Hub mirror: nginx serving a merged content-hub.json + local content,
# with an optional mutual-TLS reverse proxy to the entitled Fortinet upstream.
FROM python:3.12-slim

# nginx (serve + reverse-proxy), openssl (self-signed cert), createrepo-c +
# wget (Option C: build a local connector yum-repo index over mirrored RPMs),
# rpm (rpmbuild: turn an uploaded connector tgz into an installable RPM here so
# callers never touch rpmbuild — see connector_publish.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx openssl createrepo-c wget rpm \
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
COPY connector_publish.py /app/connector_publish.py
COPY connector-build/cyops-connector.spec.in /app/connector-build/cyops-connector.spec.in
COPY admin/ /app/admin/
COPY entrypoint.sh /app/entrypoint.sh
COPY chctl /usr/local/bin/chctl
RUN chmod +x /app/entrypoint.sh /usr/local/bin/chctl

ENV OUTPUT_DIR=/srv \
    LOCAL_CONTENT_DIR=/local-content \
    ARTIFACTS_DIR=/artifacts \
    ADMIN_PORT=9000 \
    ADMIN_ENABLED=1 \
    UPSTREAM_PROXY=1 \
    CONNECTORS_LOCAL_DIR=/connectors-local \
    CONTENT_HUB_DIR=/published/content-hub \
    CONNECTORS_CINFO=/published/connectors-all.json \
    CONNECTOR_SPEC_IN=/app/connector-build/cyops-connector.spec.in

EXPOSE 80 443 9000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost/healthz')" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
