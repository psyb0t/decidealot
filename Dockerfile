# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm@sha256:d193c6f51a7dbd10395d6328de3a7edb0516fb0608ca138036576f574c3e07d2 AS application-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/app-venv

COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && /opt/app-venv/bin/python -c "import decidealot"

FROM python:3.12-slim-bookworm@sha256:d193c6f51a7dbd10395d6328de3a7edb0516fb0608ca138036576f574c3e07d2 AS laya-builder

ENV UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /usr/local/bin/uv

WORKDIR /build

COPY requirements-laya-cpu.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/laya-venv \
    && uv pip install --python /opt/laya-venv/bin/python --require-hashes --torch-backend cpu -r requirements-laya-cpu.txt

FROM python:3.12-slim-bookworm@sha256:d193c6f51a7dbd10395d6328de3a7edb0516fb0608ca138036576f574c3e07d2 AS von-builder

ENV UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /usr/local/bin/uv

WORKDIR /build

COPY requirements-von-cpu.txt ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/von-venv \
    && uv pip install --python /opt/von-venv/bin/python --require-hashes --torch-backend cpu -r requirements-von-cpu.txt

FROM python:3.12-slim-bookworm@sha256:d193c6f51a7dbd10395d6328de3a7edb0516fb0608ca138036576f574c3e07d2 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    PATH=/opt/app-venv/bin:$PATH \
    DECIDEALOT_DEVICE=cpu \
    DECIDEALOT_IMAGE_VARIANT=cpu \
    DECIDEALOT_LOG_FILE=/tmp/decidealot.log \
    TZ=UTC

LABEL io.modelcontextprotocol.server.name="io.github.psyb0t/decidealot"

RUN groupadd --gid 10001 decidealot \
    && useradd --uid 10001 --gid decidealot --no-create-home --shell /usr/sbin/nologin decidealot \
    && mkdir --parents /etc/decidealot /models \
    && printf 'cpu\n' > /etc/decidealot/image-variant \
    && chown decidealot:decidealot /models

COPY --from=application-builder /opt/app-venv /opt/app-venv
COPY --from=laya-builder /opt/laya-venv /opt/laya-venv
COPY --from=von-builder /opt/von-venv /opt/von-venv
COPY src/decidealot/provider_entrypoint.py /opt/decidealot/provider_entrypoint.py

USER decidealot
WORKDIR /app

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=900s --retries=5 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"

ENTRYPOINT ["decidealot"]
