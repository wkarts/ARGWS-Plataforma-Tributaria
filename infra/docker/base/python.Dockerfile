FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --home-dir /app --create-home --shell /usr/sbin/nologin app
LABEL org.opencontainers.image.source="https://github.com/wkarts/reforma-tributaria-platform"
