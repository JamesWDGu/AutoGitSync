# AutoGitSync - a minimal scheduled "local directory -> Git repository" sync service.
#
# The image only needs python:3.12-alpine + git; there is no third-party Python package.
# Everything is configured through environment variables, there is no config file and the
# only required variable is GIT_REPO.

FROM python:3.12-alpine

LABEL org.opencontainers.image.title="AutoGitSync" \
      org.opencontainers.image.description="Sync a local directory to a Git repository on a schedule (local wins on conflicts)" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache git tzdata ca-certificates \
    && git config --system --add safe.directory '*' \
    && git config --system advice.detachedHead false

WORKDIR /app
COPY app/ /app/

# Environment variables (full list in docs/configuration.md):
#   required  GIT_REPO             repository URL (https or a local path)
#   common    GIT_TOKEN            access token with repository write permission
#             SOURCE_DIR           directory to sync, default /source
#             INCLUDE              regex matched against relative paths, default everything
#             SCHEDULE             cron schedule, e.g. "*/5 * * * *"; or INTERVAL=5m
#             LOG_LANG             runtime message language: en (default) or zh
#   others    EXCLUDE / DELETE_MISSING / ALLOW_EMPTY / LOG_LEVEL / LISTEN / ...
#             TZ                   time zone used to interpret the cron schedule
#   AUTOGITSYNC_VERSION            version number, injected by CI from the git tag
ARG AUTOGITSYNC_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOURCE_DIR=/source \
    REPO_DIR=/data/repo \
    LISTEN=0.0.0.0:8080 \
    AUTOGITSYNC_VERSION=${AUTOGITSYNC_VERSION} \
    TZ=UTC

RUN mkdir -p /data && chmod 700 /data

# The git work copy and the single instance lock live in /data; mounting a volume is optional
# but recommended.
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/app/main.py", "--healthcheck"]

# No CMD: without arguments the entrypoint runs as a daemon, with arguments it goes straight
# to the CLI, e.g. `docker run <image> --check`.
ENTRYPOINT ["python", "/app/main.py"]

# To run as a non-root user (the mounted directories must be owned by that UID):
#   docker run --user 1000:1000 ...
