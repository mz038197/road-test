# 路考網站的映像：只有程式和前端。路考影片、答案、交件、成績都在 Fly volume（/data），不進映像。
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 先裝相依（uv.lock 沒變就用快取），再放程式
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 ROAD_TEST_DATA=/data
EXPOSE 8080
CMD ["road-test-site", "--host", "0.0.0.0", "--port", "8080"]
