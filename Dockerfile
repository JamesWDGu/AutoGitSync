# AutoGitSync —— 极简的「本地目录 -> Git 仓库」定时同步服务
#
# 镜像只依赖 python:3.12-alpine + git，没有任何第三方 Python 包。

FROM python:3.12-alpine

LABEL org.opencontainers.image.title="AutoGitSync" \
      org.opencontainers.image.description="把本地目录按原有路径定时同步到 Git 仓库（冲突以本地为准）" \
      org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache git tzdata ca-certificates \
    && git config --system --add safe.directory '*' \
    && git config --system advice.detachedHead false

WORKDIR /app
COPY app/ /app/

# AGS_CONFIG   : 默认配置文件路径
# AGS_HEALTH_ADDR: 健康检查兜底地址（配置文件读取失败时使用）
# AGS_VERSION  : 版本号，CI 构建时注入 git tag（docker run <image> --version 可查看）
# TZ           : 影响 cron 表达式按哪个时区解释
ARG AGS_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGS_CONFIG=/config/config.toml \
    AGS_HEALTH_ADDR=0.0.0.0:8080 \
    AGS_VERSION=${AGS_VERSION} \
    TZ=UTC

RUN mkdir -p /data /config && chmod 700 /data

# git 工作副本 + 单实例锁都放在 /data，建议挂载持久化卷（可选）
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/app/main.py", "--healthcheck"]

ENTRYPOINT ["python", "/app/main.py"]
CMD ["--config", "/config/config.toml"]

# 如需以非 root 运行（宿主机挂载目录的属主需要与 UID 一致）：
#   docker run --user 1000:1000 ...
