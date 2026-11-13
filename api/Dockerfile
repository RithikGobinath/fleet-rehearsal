FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY api/requirements.lock requirements.lock
RUN apt-get update \
    && apt-get install -y --no-install-recommends zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.lock \
    && pip check \
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
RUN pip install --no-cache-dir --no-deps -r requirements-test.lock && pip check
USER 10001
