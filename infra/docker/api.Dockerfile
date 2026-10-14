ARG PYTHON_BASE=ghcr.io/wkarts/reforma-tributaria-base-python:py3.13-r1
FROM ${PYTHON_BASE}
ARG APP_VERSION
ARG BUILD_SHA
LABEL org.opencontainers.image.source="https://github.com/wkarts/ARGWS-Plataforma-Tributaria" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${BUILD_SHA}"
WORKDIR /app
COPY apps/api/ /app/
RUN pip install --no-cache-dir . && chown -R 10001:10001 /app
ENV HOME=/app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
