FROM python:3.11.14-slim-bookworm AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY requirements/u1-runtime.txt /requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes --prefix=/install -r /requirements.txt
FROM python:3.11.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=researchloop.config.portable_settings
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=dependencies /install /usr/local
RUN groupadd -g 10001 twiggle && useradd -u 10001 -g 10001 -M twiggle && mkdir -m 700 /artifacts && chown twiggle:twiggle /artifacts
COPY researchloop /app/researchloop
COPY manage.py setup.sh twiggle.sh /app/
COPY ops /app/ops
COPY tests/portable /app/tests/portable
USER 10001:10001
