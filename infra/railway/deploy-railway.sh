#!/usr/bin/env bash
# D-33 — Railway deploy path.
#
# `git push origin master` triggers Railway's connected GitHub auto-deploy
# on its own - this script exists only for the step that push does NOT
# do: GIT_SHA/BUILD_TIME. Confirmed by direct inspection (`railway run
# env`, full runtime environment) that this service - built from the
# root Dockerfile, not Nixpacks - gets no RAILWAY_GIT_COMMIT_SHA or any
# other git-metadata variable from Railway. The Dockerfile's
# `ARG GIT_SHA=unknown` only sets a build-time default; Railway's own
# runtime variable of the same name overrides it at container start. If
# nothing re-sets that variable, GET /v1/version reports whatever GIT_SHA
# was last set by hand, forever, regardless of what actually shipped -
# the exact failure this script prevents.
#
# Run this AFTER pushing and AFTER Railway's deploy has gone healthy
# (`railway logs --build` or the Railway dashboard), not before - setting
# the variable does not build anything, it only fixes what /v1/version
# will report once the new build is live.

set -euo pipefail

GIT_SHA="$(git rev-parse HEAD)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "== Setting Railway GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} =="
railway variables --set "GIT_SHA=${GIT_SHA}" --set "BUILD_TIME=${BUILD_TIME}"

echo "== Verify: GET /v1/version should report ${GIT_SHA} within ~30s =="
echo "   curl -s \$RAILWAY_URL/v1/version"
