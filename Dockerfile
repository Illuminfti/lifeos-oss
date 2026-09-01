FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIFEOS_BRAIN=/data/brain

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir '.[mcp,telegram]' \
    && useradd --create-home --uid 10001 lifeos \
    && mkdir -p /data/brain \
    && chown -R lifeos:lifeos /data /app

USER lifeos
VOLUME ["/data/brain"]
ENTRYPOINT ["lifeos"]
CMD ["doctor"]
