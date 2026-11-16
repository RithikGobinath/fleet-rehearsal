FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5 AS runtime
WORKDIR /app
ENV PATH="/opt/fleet/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY api/requirements.lock requirements.lock
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m venv /opt/fleet \
    && python -m pip install --no-cache-dir --only-binary=:all: -r requirements.lock \
    && python -m pip check \
    && useradd --uid 10001 fleet
COPY api/fleet fleet
COPY fixtures fixtures
USER 10001
EXPOSE 8000
CMD ["uvicorn", "fleet.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM runtime AS test
USER root
COPY api/tests tests
COPY api/requirements-test.lock requirements-test.lock
RUN python -m pip install --no-cache-dir --no-deps -r requirements-test.lock && python -m pip check
USER 10001
