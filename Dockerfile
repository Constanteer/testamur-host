FROM python:3.12-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git zstd \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none

ENV PATH="/root/.elan/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TESTAMUR_ALLOWED_HOSTS=*

WORKDIR /app
COPY . /app

WORKDIR /app/mathhub_app
RUN lake update && lake exe cache get

WORKDIR /app
RUN mkdir -p /data/testamur /data/mathhub \
    && chmod +x /app/deploy/start.sh

EXPOSE 8080
CMD ["/app/deploy/start.sh"]
