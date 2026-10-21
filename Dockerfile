# Both images are held by digest, because a tag is a name its owner may move;
# Dependabot's docker entry moves the digest through review.
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
# build. hopin itself stays out of the environment, as in CI, and runs from src.
RUN uv sync --locked --no-dev --no-install-project --no-build \
 && useradd --no-create-home --uid 1001 hopin
COPY src ./src
ENV PYTHONPATH=/app/src
USER hopin

EXPOSE 8080
CMD ["python", "-m", "hopin.main"]
