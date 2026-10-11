# Targets:
#   dev      dependencies only; mount the repo at /app
#   test     the Python suite
#   app      the server: API + agents + the built web app on :8000 (what gets deployed)
#   runtime  the command-line tool

FROM python:3.12-slim AS dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN pip install --no-cache-dir "numpy>=2.0" "pydantic>=2.7" "pyarrow>=16" "pytest>=8" "httpx>=0.27" \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "agent-framework-core>=1.21" "agent-framework-openai>=1.16"

FROM dev AS test
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
CMD ["pytest", "-q"]

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GROUNDTRUTH_WEB_DIR=/app/web
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[api,agents,azure]" && useradd --create-home --uid 10001 groundtruth
COPY --from=web /web/dist ./web
USER groundtruth
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "groundtruth.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
ENTRYPOINT ["groundtruth"]
