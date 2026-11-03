# All three images are held by digest, because a tag is a name its owner may move;
# Dependabot's docker entry moves the digest through review.
# The page is built here and only its output travels on, so the final image
# carries no Node. The adapter writes to ../src/hopin/web/build.
FROM node:26-slim@sha256:e4db4c914bf8ad241bd24804fc25d272d89bad97e68cdc8a1414862790a02c39 AS page-build
WORKDIR /build/frontend
# Node 26 bundles no corepack; the corepack installed here then provisions the
# pnpm version that package.json's packageManager pins.
RUN npm install --global --ignore-scripts corepack@0.36.0 && corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/patches ./patches
RUN pnpm install --frozen-lockfile
COPY frontend ./
# The maps are for the coverage report in CI; the image serves none.
RUN pnpm build && find /build/src/hopin/web/build -name '*.map' -delete

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS python-base

COPY --from=uv /uv /usr/local/bin/uv

# The interpreter is the image's own, so uv never downloads another one.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY pyproject.toml uv.lock ./

# git exists only in this stage, because uv needs it to fetch the two git-pinned
# libraries; the final stage copies the environment and never carries it.
FROM python-base AS dependencies
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Two builds run, the git-pinned libraries named in pyproject.toml (tool.uv),
# with exact build tools. --no-build refuses every other source distribution,
# whose build script would run third-party code during the image build, so
# third-party packages are wheel-only. hopin itself is installed in a second
# step below, after its source is copied, so that its own build runs through
# the exact-pinned hatchling.
RUN uv sync --locked --no-dev --no-install-project --no-build

FROM python-base
COPY --from=dependencies /opt/venv /opt/venv
RUN useradd --no-create-home --uid 1001 hopin
COPY src ./src
COPY --from=page-build /build/src/hopin/web/build ./src/hopin/web/build
# Only the project is installed here, as a wheel built from the copied source;
# --no-build keeps the install itself from running any other build script.
RUN uv build --wheel --out-dir /tmp/wheel \
 && uv pip install --python /opt/venv/bin/python --no-deps --no-build /tmp/wheel/*.whl \
 && rm -r /tmp/wheel
USER hopin

EXPOSE 8080
CMD ["hopin", "serve"]
