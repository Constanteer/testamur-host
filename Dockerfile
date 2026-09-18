FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN mkdir -p /data
EXPOSE 8080
CMD ["sh","-c","python -m apps.web.hosted_app --host 0.0.0.0 --port ${PORT:-8080} --data-dir ${TESTAMUR_DATA_DIR:-/data} --secure-cookie"]
