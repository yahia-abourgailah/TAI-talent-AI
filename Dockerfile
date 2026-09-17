FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
RUN useradd --system --uid 10001 --home-dir /srv talent

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY alembic.ini ./
COPY migrations ./migrations
# The development console and the example careers page. Mounted only when TALENT_ENV=dev.
COPY web ./web

USER talent
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
