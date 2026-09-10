# Linux desktop for the stock strands browser: Xvfb + openbox + x11vnc +
# noVNC + a Temporal worker running the native LocalChromiumBrowser activity.
# Build context: orchestrator/
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc openbox novnc websockify curl x11-utils procps tini xterm mousepad \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/orchestrator
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
COPY desktop/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright

# Source only; credentials and provider keys never enter this image.
COPY browser_activity.py desktop_worker.py config.py telemetry.py ./
COPY computer_use_activity.py desktop_observation.py workspace_state.py ./
COPY desktop/start-desktop.sh ./desktop/start-desktop.sh

ARG DESKTOP_UID=1000
RUN useradd --uid "$DESKTOP_UID" --create-home desktop \
    && chown -R desktop:desktop /opt/orchestrator
USER desktop
ENV DISPLAY=:99 \
    STRANDS_BROWSER_HEADLESS=false \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/orchestrator

HEALTHCHECK --interval=5s --timeout=4s --start-period=30s --retries=3 \
    CMD xdpyinfo >/dev/null 2>&1 && curl --fail --silent http://127.0.0.1:6080/vnc.html >/dev/null && pgrep -f '^python -u desktop_worker.py$' >/dev/null

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["bash", "/opt/orchestrator/desktop/start-desktop.sh"]
