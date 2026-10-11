# `dev` has the dependencies only (mount the repo at /app), `test` bakes the
# code in and runs the suite, `runtime` is the command-line tool.
FROM python:3.12-slim AS dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN pip install --no-cache-dir "numpy>=2.0" "pydantic>=2.7" "pyarrow>=16" "pytest>=8"

FROM dev AS test
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
CMD ["pytest", "-q"]

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
ENTRYPOINT ["pitchside"]
