# AutoGitSync —— 极简的「本地目录 -> Git 仓库」定时同步服务
#
# 镜像只依赖 python:3.12-alpine + git，没有任何第三方 Python 包。
# 所有配置都通过环境变量传入，没有配置文件；唯一必填项是 GIT_REPO。

FROM python:3.12-alpine

LABEL org.opencontainers.image.title="AutoGitSync" \
      org.opencontainers.image.description="把本地目录按原有路径定时同步到 Git 仓库（冲突以本地为准）" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache git tzdata ca-certificates \
    && git config --system --add safe.directory '*' \
    && git config --system advice.detachedHead false

WORKDIR /app
COPY app/ /app/

# 环境变量（完整列表见 README）：
#   必填  GIT_REPO              Git 仓库地址（https 或本地路径）
#   常用  GIT_TOKEN             访问令牌，私有仓库必填，建议用 secret 注入
#         SOURCE_DIR            要同步的目录，默认 /source
#         INCLUDE               文件匹配正则，默认全部同步
#         SCHEDULE              cron 周期，如 "*/5 * * * *"；或用 INTERVAL=5m
#   其他  EXCLUDE / DELETE_MISSING / ALLOW_EMPTY / LOG_LEVEL / LISTEN / …
#         TZ                    影响 cron 表达式按哪个时区解释
#   AUTOGITSYNC_VERSION       版本号，CI 构建时注入 git tag（无需手工设置）
ARG AUTOGITSYNC_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOURCE_DIR=/source \
    REPO_DIR=/data/repo \
    LISTEN=0.0.0.0:8080 \
    AUTOGITSYNC_VERSION=${AUTOGITSYNC_VERSION} \
    TZ=UTC

RUN mkdir -p /data && chmod 700 /data

# git 工作副本与单实例锁都放在 /data，建议挂载持久化卷（可选）
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/app/main.py", "--healthcheck"]

# CMD 交给调用方：不带参数即守护模式，`docker run <image> --check` 之类则直接进 CLI
ENTRYPOINT ["python", "/app/main.py"]

# 如需以非 root 运行（宿主机挂载目录的属主需要与 UID 一致）：
#   docker run --user 1000:1000 ...
