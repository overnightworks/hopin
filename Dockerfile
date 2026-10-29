# All three images are held by digest, because a tag is a name its owner may move;
# Dependabot's docker entry moves the digest through review.
# The page is built here and only its output travels on, so the final image
# carries no Node. The adapter writes to ../src/hopin/web/build.
FROM node:24-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c AS page-build
WORKDIR /build/frontend
# corepack provisions the pnpm version that package.json's packageManager pins.
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/patches ./patches
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /usr/local/bin/uv

# The interpreter is the image's own, so uv never downloads another one.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Dependencies are installed as wheels only: --no-build refuses a source
# distribution, whose build script would run third-party code during the image
# build. hopin itself is installed in a second step below, after its source is
# copied, so that the one build that runs is its own, through the exact-pinned
# hatchling.
RUN uv sync --locked --no-dev --no-install-project --no-build \
 && useradd --no-create-home --uid 1001 hopin
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
