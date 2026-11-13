# gcboard server image. Railway builds this automatically (docs/deploy-railway.md).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra server --no-install-project
COPY gcboard ./gcboard
COPY examples ./examples
RUN uv sync --frozen --no-dev --extra server

# HOST: listen on all interfaces inside the container; Railway sets PORT.
# FORWARDED_ALLOW_IPS: trust the platform proxy's X-Forwarded-For, so per-IP
# limits on login see real client addresses. Only run this image behind a proxy.
ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 FORWARDED_ALLOW_IPS="*" PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["gcboard-server"]
