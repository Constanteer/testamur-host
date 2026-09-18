FROM python:3.12-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git zstd \
    && rm -rf /var/lib/apt/lists/*

# Install only the lightweight elan launcher in the image. The pinned Lean
# toolchain and mathlib cache are hydrated into the persistent /data volume
# after the web service is already online.
RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none

ENV PATH="/root/.elan/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TESTAMUR_ALLOWED_HOSTS=*

WORKDIR /app
COPY . /app

RUN mkdir -p /data/testamur /data/mathhub /data/lean \
    && chmod +x /app/deploy/start.sh

EXPOSE 8080
CMD ["/app/deploy/start.sh"]
