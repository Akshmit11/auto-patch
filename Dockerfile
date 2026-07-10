# AutoPatch agent image (multi-stage)
# Stage: builder — install deps with uv
FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --no-dev --no-editable

# Stage: runtime — slim non-root agent
FROM python:3.11-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 autopatch \
    && useradd --system --uid 1001 --gid autopatch --create-home autopatch

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/README.md /app/
COPY demo /app/demo
COPY eval /app/eval

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER autopatch

ENTRYPOINT ["autopatch"]
CMD ["--help"]
