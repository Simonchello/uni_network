FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

FROM base AS builder

WORKDIR /app
COPY web/requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r requirements.txt

FROM base AS runtime

RUN useradd -r -u 1000 -m -d /home/app app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --chown=app:app web/ ./web/

USER app
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/healthz').status==200 else 1)"

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8001"]
