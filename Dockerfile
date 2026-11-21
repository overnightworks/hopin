# All three images are held by digest, because a tag is a name its owner may move;
# Dependabot's docker entry moves the digest through review.
# The page is built here and only its output travels on, so the final image
# carries no Node. The adapter writes to ../src/hopin/web/build.
FROM node:26-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc AS page-build
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

FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS python-base

COPY --from=uv /uv /usr/local/bin/uv

# The interpreter is the image's own, so uv never downloads another one.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
# uv reads the workspace's members from their manifests, so the lock check needs the probe's.
COPY pyproject.toml uv.lock ./
COPY packages/probe/pyproject.toml ./packages/probe/pyproject.toml

# git exists only in this stage, because uv needs it to fetch the git-pinned
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

# The probe stage comes before the serving stage, which stays last so that a build without a target is the serving
# image. Chromium, Piper and the probe's package never reach the serving stage.
FROM python-base AS probe-dependencies
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN uv sync --locked --no-dev --package hopin-probe --no-install-workspace --no-build

FROM python-base AS probe
COPY --from=probe-dependencies /opt/venv /opt/venv
# Chromium and the libraries it needs come with the playwright version the lock holds; curl is for the voice.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright HOME=/tmp
RUN apt-get update \
 && apt-get install --yes --no-install-recommends curl ca-certificates \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY scripts/fetch_probe_voice.sh /usr/local/bin/fetch_probe_voice.sh
RUN fetch_probe_voice.sh /opt/piper-voice
COPY src ./src
COPY packages/probe ./packages/probe
# Both wheels are built from the copied source; hopin's is for the constants and words of its page, which the probe
# reads. --no-build keeps the install itself from running any other build script.
RUN uv build --wheel --out-dir /tmp/wheel \
 && uv build --wheel --package hopin-probe --out-dir /tmp/wheel \
 && uv pip install --python /opt/venv/bin/python --no-deps --no-build /tmp/wheel/*.whl \
 && rm -r /tmp/wheel
RUN useradd --no-create-home --uid 1001 probe
USER 1001
ENTRYPOINT ["hopin-probe"]

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
# A pod's runAsNonRoot can verify only a numeric user; 1001 is hopin's uid.
USER 1001

ARG SERVICE_VERSION
ENV SERVICE_VERSION=$SERVICE_VERSION

EXPOSE 8080
CMD ["hopin", "serve"]
