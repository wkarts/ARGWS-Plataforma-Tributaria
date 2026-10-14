FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/app
WORKDIR /app
COPY apps/api/ /app/
COPY deploy/self/prepare_secrets.py /usr/local/bin/prepare-secrets.py
RUN pip install --no-cache-dir . \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER 0:0
ENTRYPOINT ["python", "/usr/local/bin/prepare-secrets.py"]
EXPOSE 8000
