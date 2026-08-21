#!/usr/bin/env bash
# T-15 — AWS Lambda + Function URL deploy path.
#
# Builds the container image from a checkout of THIS repo's current
# commit (run from the repo root, or point BUILD_DIR at one), pushes to
# ECR, and updates the Lambda function to the new image. The Dockerfile
# lives in this repo's root (D-26) - BUILD_DIR defaults to "." here,
# not a separate sibling clone.
#
# D-21: docker buildx attaches OCI provenance/SBOM attestations by
# default, which turns the pushed artifact into a multi-manifest image
# INDEX. Lambda's update-function-code rejects that - it wants a single
# image manifest. --provenance=false --sbom=false --oci-mediatypes=false
# below are not optional flourishes; without them this script produces
# an image Lambda will not accept, and the failure shows up as "the
# console/CLI says success" while the function silently keeps running
# the old image (see D-22).
#
# D-22: a successful `update-function-code` call is not proof the
# function actually changed. This script asserts LastModified advanced
# after the update - guarded, because the deploying IAM principal may
# lack lambda:GetFunctionConfiguration (D-23: least-privilege granted at
# resource-creation time doesn't necessarily cover later operations).
# When that read is denied, this script reports UNVERIFIED and tells you
# to check behaviorally (curl the Function URL) instead of failing.

set -euo pipefail

FUNCTION_NAME="${FUNCTION_NAME:-ps91-t15}"
REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="${ECR_REPO:-ps91-t15}"
BUILD_DIR="${BUILD_DIR:-.}"
GIT_SHA="$(git -C "$BUILD_DIR" rev-parse --short HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
IMAGE_TAG="t15-lambda-${GIT_SHA}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"
IMAGE_URI="${ECR_URI}:${IMAGE_TAG}"

echo "== Building ${IMAGE_URI} from ${BUILD_DIR} (commit ${GIT_SHA}) =="
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# D-21: single-manifest output only - no provenance/SBOM attestation
# index, no OCI media types. This is the exact fix, not a style choice.
docker buildx build \
  --platform=linux/amd64 \
  --provenance=false \
  --sbom=false \
  --build-arg GIT_SHA="$GIT_SHA" \
  --build-arg BUILD_TIME="$BUILD_TIME" \
  --output type=image,name="$IMAGE_URI",oci-mediatypes=false,push=true \
  "$BUILD_DIR"

echo "== D-21 assertion: pushed manifest must NOT be an OCI image index =="
MEDIA_TYPE="$(
  aws ecr batch-get-image \
    --repository-name "$ECR_REPO" --region "$REGION" \
    --image-ids imageTag="$IMAGE_TAG" \
    --query 'images[0].imageManifest' --output text \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("mediaType",""))'
)"
case "$MEDIA_TYPE" in
  *image.index*|*manifest.list*)
    echo "FAIL: pushed manifest media type is '${MEDIA_TYPE}' - an image index/manifest" \
         "list. Lambda will reject this. Check --provenance/--sbom/oci-mediatypes flags." >&2
    exit 1
    ;;
  *)
    echo "OK: manifest media type is '${MEDIA_TYPE}' - single image, Lambda-acceptable."
    ;;
esac

echo "== Updating ${FUNCTION_NAME} to ${IMAGE_URI} =="
aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" --region "$REGION" \
  --image-uri "$IMAGE_URI" >/dev/null

echo "== D-22 assertion: LastModified must advance after the update =="
# D-23: guarded - the deploying principal may not have
# lambda:GetFunctionConfiguration even though it can update the function.
BEFORE_TS="${LAST_KNOWN_MODIFIED:-}"
if AFTER_TS="$(aws lambda get-function-configuration \
      --function-name "$FUNCTION_NAME" --region "$REGION" \
      --query LastModified --output text 2>/dev/null)"; then
  if [ -n "$BEFORE_TS" ] && [ "$AFTER_TS" = "$BEFORE_TS" ]; then
    echo "FAIL: LastModified (${AFTER_TS}) did not advance - the update did not apply," \
         "even though the API call reported success (this is exactly D-22)." >&2
    exit 1
  fi
  echo "OK: LastModified is now ${AFTER_TS}."
else
  echo "UNVERIFIED: lambda:GetFunctionConfiguration is denied for this principal (D-23)." \
       "Cannot confirm LastModified advanced from here. Verify behaviorally instead:" \
       "curl the Function URL's /livez, /readyz, and the three canonical scenarios," \
       "and check the response schema for precedent/calibration/sub-score fields - their" \
       "presence is itself proof the new build is live (see D-16)."
fi

echo "== Done: ${IMAGE_URI} =="
