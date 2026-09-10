# 使用与排错

[首页](../README.zh-CN.md) · [English](usage.md) | **简体中文**

## 文件匹配

正则匹配 `svc-a/compose.yaml` 这样的相对路径。在 Compose 中使用单引号：

```yaml
environment:
  INCLUDE: '^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$'
```

| 正则 | 匹配范围 |
| --- | --- |
| `.*` | 除 `.git` 条目外的全部文件 |
| `\.(conf|ya?ml)$` | 任意深度、指定扩展名的配置文件 |
| `^[^/]+/(?:.*/)?compose\.ya?ml$` | 子目录中的 `compose.yaml` / `compose.yml`，不包含根目录 |
| `^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$` | 同上，加上 `.env`、`.env.local` 等 |

`EXCLUDE` 优先。开头的 `^[^/]+/` 要求至少一层子目录，`(?:.*/)?` 允许更深层级，
同时不会误匹配 `my-compose.yaml`。

服务尊重目标仓库的 `.gitignore`。如果 `.env` 被跳过，请查看告警；只有确认这些文件
应当存入 Git 时，才移除对应忽略规则。不要因为有历史截断功能就提交仍在使用的密钥。

## 文件系统安全

- 源目录只读挂载，工作副本与其分离。示例使用 `/data` 命名卷，避免工作副本嵌入源目录。
- 使用 bind mount 时，让目录互为兄弟，例如 `./configs:/source:ro` 和 `./data:/data`。
  物理重叠的工作副本会被跳过并告警；源目录和工作副本相同或容器路径互相嵌套时拒绝运行。
- 文件和目录的符号链接都按**链接本身**保存，不跟随目标复制内容。
  链接目标路径会被提交，因此不要使用包含私人设备路径的链接。
- 用普通文件替换远端链接时，不会写入链接指向的位置。文件/目录替换如果会删除
  未匹配或被排除的文件，会报错停止；请先解决布局冲突或有意调整筛选范围。
- 空目录保护不能阻止所有意外的部分删除。调整挂载或筛选条件时，请检查 `--dry-run`。

## 限制分支历史

```yaml
environment:
  FORCE_PUSH_LATEST: "3"
```

`0` 正常保留历史。正整数 N 表示目标分支最多保留最近 N 个提交；`1` 只保留最新状态。
即使文件没有变化也会执行，因此启用或降低限制不需要先修改源文件。
无变更的同步不会为了凑满 N 个提交而创建新提交。

此模式需要强推权限，可能覆盖并发推送的远端提交。最老的保留提交会变成根提交，
保留的文件树、消息、时间会沿用。结果中的提交 ID 是重写后的分支头。
试运行会显示配置的历史限制，但不会重写或推送历史。

**这不等于彻底清除密钥。** 其他分支、标签、克隆、fork 和托管平台缓存可能仍保留旧数据。
如果凭据已经泄露，应先吊销或轮换，再按托管平台的敏感数据清理流程处理。
建议使用专用同步分支。

## 手动同步与检查

Compose 服务运行时：

```bash
docker compose logs -f --tail=100
docker compose exec -T autogitsync python /app/main.py --trigger
```

`--trigger` 通过容器内部接口请求同步，读取 `LISTEN` 和 `API_TOKEN`。
成功仅表示**请求已接受**，不代表同步完成；结果请查看日志或 `/status`。
不需要宿主机端口。关闭 `LISTEN` 时，请改用离线 `--once`。

`--check` 只扫描源文件，不修改工作副本，可随时使用。
`--once` 和 `--dry-run` 都是**离线命令**：试运行也会 fetch、reset、复制、暂存文件，
所以同样需要工作副本锁。

```bash
docker compose stop autogitsync
docker compose run --rm --no-deps autogitsync --dry-run
# To apply immediately while stopped, use --once instead of --dry-run.
docker compose up -d
```

在源码目录中，`make trigger`、`make check`、`make once`、`make dry-run` 是这些命令的简写。
后两者需要先停止守护进程。另一个进程持锁时会拒绝执行，不要绕过锁或删除锁文件。

## 健康状态与 HTTP API

| 接口 | 含义 |
| --- | --- |
| `GET /healthz` | 存活探测，接口能响应就返回 200；容器健康检查使用此接口。 |
| `GET /status` | 上次结果、计数、下次时间；上次同步失败时返回 503。 |
| `POST /sync` | 请求同步，返回 202；设置 `API_TOKEN` 后需要 Bearer token。 |

容器的 `healthy` 只代表进程能响应，**不代表最近一次同步成功**。
关闭 `LISTEN` 后，内置健康检查跳过探测并返回成功。

如果需要从宿主机查询，在服务中添加以下配置，或取消项目示例中的对应注释：

```yaml
ports:
  - "127.0.0.1:8080:8080"
```

```bash
curl -s http://127.0.0.1:8080/status
```

保持接口私有。`API_TOKEN` 只保护同步请求，不保护状态输出。
定时同步、容器健康检查和 `--trigger` 都不需要映射宿主机端口。

## 其他部署方式

### 直接使用 Docker

创建并填充 `configs` 后，替换下面的仓库地址和 token：

```bash
docker run -d --name autogitsync --restart unless-stopped \
  -e GIT_REPO=https://github.com/your-name/my-configs.git \
  -e GIT_TOKEN=your-write-token \
  --mount type=bind,source="$PWD/configs",target=/source,readonly \
  --mount type=volume,source=autogitsync-data,target=/data \
  ghcr.io/jameswdgu/autogitsync:latest
```

如果需要预览，把 `-d --name autogitsync --restart unless-stopped` 换成 `--rm`，
并在镜像地址后添加 `--dry-run`。同一数据卷没有守护进程运行时才能这样做。

### 使用项目自带的 Compose 示例

在源码目录中执行：

```bash
cp .env.example .env
mkdir -p configs
# Edit .env and put your source files in ./configs.
make check
make dry-run
make up
```

`.env` 只用于插入仓库凭据，其他应用变量直接设置在 Compose 的 `environment` 中。
没有应用配置文件。本地镜像构建见[参与开发](../CONTRIBUTING.zh-CN.md)。

### 更新现有部署

```bash
docker compose pull
docker compose up -d
```

使用 `:latest` 时，升级前应查看变更；希望可控升级可固定版本标签。
如果替换为新的项目示例，请注意：源目录由 `./example-source` 改为 `./configs`，
显式 cron/时区改为默认调度与 UTC，工作副本由 `./data` 改为命名卷。
原有挂载、筛选条件、周期和时区如果是有意设置，请保留。
新卷会重新克隆远端，不会迁移或删除旧的 `./data`。
`docker compose down` 保留命名卷；除非有意删除，不要添加 `--volumes`。

## 常见错误

| 现象 | 检查项 |
| --- | --- |
| 推送或认证失败 | token 写权限、仓库地址、分支保护和 `GIT_USERNAME`。 |
| 没有匹配文件 | 挂载路径和 `INCLUDE` / `EXCLUDE`，用 `--check` 检查。 |
| 部分文件缺失 | 目标 `.gitignore` 告警和筛选规则；符号链接不会展开内容。 |
| 无法锁定数据目录 | 离线命令需要先停止守护进程，运行时使用 `--trigger`。 |
| 管理范围外路径冲突 | 文件/目录替换会删除非受管路径，需要解决布局或筛选冲突。 |
| 首次运行较慢 | 首次克隆需要获取分支历史；持久化 `/data` 可复用工作副本。 |
| healthy 但没有同步 | 查看 `/status` 和日志，存活探测不代表同步成功。 |
