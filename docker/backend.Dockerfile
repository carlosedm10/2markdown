FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=0
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    libsnappy-dev \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    fonts-liberation \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-dev --no-install-project 2>/dev/null || \
    (uv lock && uv sync --no-dev --no-install-project)

COPY . .

RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

CMD ["sleep", "infinity"]
