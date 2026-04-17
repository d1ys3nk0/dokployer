FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN uv pip install --system .

CMD ["dokployer", "--help"]
