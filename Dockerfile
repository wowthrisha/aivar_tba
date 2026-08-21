# T-15 (AWS deploy) — Lambda container image via the AWS Lambda Web Adapter.
# LWA lets the existing FastAPI/uvicorn app run on Lambda completely
# unmodified (no Mangum handler, no app code changes) - verified against
# aws/aws-lambda-web-adapter's official README and examples/fastapi sample
# before writing this file. Non-AWS base images are explicitly supported
# by the adapter's own docs.
FROM public.ecr.aws/docker/library/python:3.13-slim

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.1 /lambda-adapter /opt/extensions/lambda-adapter

# GET /v1/version reports these - "unknown" if not passed at build time.
# Baked into the image at build, not read from the Lambda's own config,
# so no IAM write permission is needed to make them show up (D-23).
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA=$GIT_SHA
ENV BUILD_TIME=$BUILD_TIME

ENV PORT=8000
ENV AWS_LWA_READINESS_CHECK_PATH=/livez

WORKDIR /var/task

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

CMD exec uvicorn app.main:app --host 0.0.0.0 --port=$PORT
