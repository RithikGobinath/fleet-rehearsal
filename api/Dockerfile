FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY api/requirements.lock requirements.lock
RUN pip install --no-cache-dir -r requirements.lock \
    && pip check \
    && useradd --uid 10001 fleet
COPY api/fleet fleet
COPY api/tests tests
COPY fixtures fixtures
USER 10001
EXPOSE 8000
CMD ["uvicorn", "fleet.main:app", "--host", "0.0.0.0", "--port", "8000"]
