FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl bash nodejs npm \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin devbot \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY prompts ./prompts
COPY alembic.ini ./

RUN pip install --no-cache-dir . \
    && mkdir -p /workspace \
    && chown -R devbot:devbot /app /workspace

USER devbot
RUN curl https://cursor.com/install -fsS | bash
ENV PATH="/home/devbot/.local/bin:${PATH}"
ENV AGENT_WORKSPACE=/workspace
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
