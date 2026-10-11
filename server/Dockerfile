# Visitor-play game server (#280, #281) on a Docker Hugging Face Space.
# Build context is the Space repo root, staged by scripts/deploy-play-space.sh.
# No secrets here: they arrive as Space secrets at runtime.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

# HF Spaces runs the container as uid 1000. Copy as root, chown, then drop privileges, so
# ownership doesn't depend on BuildKit.
RUN useradd -m -u 1000 user
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY configs ./configs
COPY schema ./schema
# The Neon spend ledger and game stream create their tables from this at startup.
COPY neon/schema.sql ./neon/schema.sql
RUN chown -R user:user /app

USER user
ENV HOME=/home/user \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:/home/user/.local/bin:$PATH
RUN uv sync --frozen --extra server --no-dev

# Render sets $PORT; HF Spaces doesn't, so it stays on 7860.
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '7860'), timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec uvicorn --factory societysim.server.app:create_app --host 0.0.0.0 --port ${PORT:-7860}"]
