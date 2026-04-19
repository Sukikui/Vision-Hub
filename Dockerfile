FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /opt/vision-hub

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY main.py ./main.py
COPY vision_hub ./vision_hub

RUN uv sync --locked --no-dev

CMD ["/opt/vision-hub/.venv/bin/python", "main.py"]
