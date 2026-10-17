# __PROJECT__ API container (any Docker host: Render, Fly, a Hugging Face Space...).
# Secrets come from the host's environment variables (see .env.example), never the image.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv \
 && useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PORT=8000 UV_COMPILE_BYTECODE=1
WORKDIR /home/user/app

COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=user app ./app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os, urllib.request; urllib.request.urlopen(f\"http://localhost:{os.environ.get('PORT', '8000')}/health\")"
CMD ["sh", "-c", ".venv/bin/uvicorn app.api:app --host 0.0.0.0 --port ${PORT}"]
