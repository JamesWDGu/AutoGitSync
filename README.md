# AutoGitSync

极简的「本地目录 → Git 仓库」定时同步服务，单容器运行，**零第三方依赖**（Python 3.12 + git）。

配置**全部通过环境变量传入，没有配置文件**，只有一个必填项：

```bash
docker run -d --restart unless-stopped \
  -e AGS_GIT_REPO=https://github.com/you/my-configs.git \
  -e AGS_GIT_TOKEN=ghp_xxxxxxxx \
  -v /etc/nginx:/source:ro \
  ghcr.io/<owner>/<repo>:latest
```

它就会每 5 分钟把 `/source` 下的文件**按原有相对路径**同步到仓库，并且：

- 冲突（远端与本地都有改动）→ **以本地为准**
- 本地没有的文件 → **从 git 中删除**（镜像式同步）
- 推送时远端刚好被别人更新 → 自动重拉并重放本地内容后重试，**不强推、不丢远端历史**

---

## 1. 快速开始

```bash
# 1) 告诉 docker compose 要同步到哪、同步什么（也可以直接在 shell 里 export）
cp .env.example .env        # 填 AGS_GIT_REPO / AGS_GIT_TOKEN

# 2) 改 docker-compose.yml 里的这一行，指向你要同步的目录
#      - ./example-source:/source:ro

# 3) 先看看会发生什么（不会提交任何东西）
make check      # 打印当前生效的配置、匹配到的文件、接下来 5 次执行时间
make dry-run    # 试运行：显示将要新增/修改/删除的内容

# 4) 启动
make up         # = docker compose up -d --build
make logs
```

不用 compose，直接 `docker run`：

```bash
# 本地构建；如果你已经把仓库推到 GitHub，也可以直接拉 CI 构建好的多架构镜像：
#   docker pull ghcr.io/<owner>/<repo>:latest
docker build -t autogitsync:latest .

docker run -d --name autogitsync --restart unless-stopped \
  -e AGS_GIT_REPO=https://github.com/you/my-configs.git \
  -e AGS_GIT_TOKEN=ghp_xxxxxxxx \
  -e AGS_INCLUDE='\.(conf|ya?ml)$' \
  -e AGS_SCHEDULE='*/5 * * * *' \
  -e TZ=Asia/Shanghai \
  -v /etc/nginx:/source:ro \
  -v "$PWD/data:/data" \
  -p 127.0.0.1:8080:8080 \
  autogitsync:latest
```

## 2. 配置：全是环境变量

**唯一必填项是 `AGS_GIT_REPO`**（私有仓库再加 `AGS_GIT_TOKEN`），其余全部有默认值。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGS_GIT_REPO` | **必填** | 仓库地址。`https://` 会按要求注入 token；也支持本地路径 / `file://` / ssh（不注入） |
| `AGS_GIT_TOKEN` | 空 | 访问令牌，建议用 docker secret 或 `.env` 注入 |
| `AGS_GIT_BRANCH` | `main` | 目标分支，不存在时自动创建 |
| `AGS_GIT_USERNAME` | `x-access-token` | HTTP Basic 用户名。GitHub 用 `x-access-token`，GitLab 用 `oauth2`，Gitea 任意非空即可 |
| `AGS_SOURCE` | `/source` | 要同步的目录（容器内路径，挂载进来即可） |
| `AGS_INCLUDE` | `.*` | 正则，作用于形如 `app/settings.conf` 的**相对路径**（`re.search` 语义，写 `\.conf$` 就行） |
| `AGS_EXCLUDE` | 空 | 排除正则。`AGS_SOURCE` 里的 `.git` 目录永远跳过 |
| `AGS_SCHEDULE` | 空 | cron 表达式，5 字段：`分 时 日 月 周`（支持 `*/5`、`9-17`、`1,3`、`@daily` 等） |
| `AGS_INTERVAL` | `5m` | 固定间隔：`30s` / `5m` / `2h` / `1d`；配了 `AGS_SCHEDULE` 时以 cron 为准 |
| `AGS_DELETE_MISSING` | `true` | 本地已删除的文件是否也从 git 删除 |
| `AGS_ALLOW_EMPTY` | `false` | 安全阀：本地一个匹配文件都没有时，是否允许删空远端 |
| `AGS_WORKDIR` | `/data/repo` | git 工作副本目录，建议挂持久化卷 |
| `AGS_RUN_ON_START` | `true` | 启动后是否立刻同步一次 |
| `AGS_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AGS_LISTEN` | `0.0.0.0:8080` | 健康端点监听地址，设为空字符串关闭 |
| `AGS_API_TOKEN` | 空 | 非空时 `POST /sync` 需要 `Authorization: Bearer <token>` |
| `AGS_COMMIT_MESSAGE` | `sync: {count} file(s) changed at {time}` | 提交信息模板，占位符：`{count}` `{changed}` `{deleted}` `{time}` `{source}` `{host}` |
| `AGS_PUSH_RETRIES` | `3` | 推送被拒（远端同时更新）时的重试次数 |
| `AGS_GIT_AUTHOR_NAME` / `AGS_GIT_AUTHOR_EMAIL` | `AutoGitSync` / `autogitsync@localhost` | 提交者信息 |
| `TZ` | `UTC` | 影响 cron 表达式按哪个时区解释（例如 `Asia/Shanghai`） |

> 布尔值支持 `true/false`、`1/0`、`yes/no`、`on/off`（大小写不敏感，留空即 false）。
> `AGS_SOURCE` 不存在、正则/端口/cron 写错等都会在启动时直接报错退出（退出码 2），
> 不会带着错误配置跑起来。

## 3. 同步语义

每轮同步都是同一个确定性流程：

1. **回到远端状态**：`fetch` + `reset --hard` + `clean -fdx`，工作副本总是从远端分支最新提交开始；
2. **覆盖本地文件**：把 `AGS_SOURCE` 中匹配 `AGS_INCLUDE`/`AGS_EXCLUDE` 的文件按原有相对路径写入；
3. **删除远端多余文件**：远端存在、本地不存在、且匹配规则的文件被删除；
4. **提交并推送**。若推送被拒（远端在此期间前进），则重拉 → 重放本地文件 → 重新提交 → 重试。

由此得到的行为：

| 场景 | 结果 |
| --- | --- |
| 本地新增/修改 | 推送到 git，路径保持不变 |
| 本地删除 | git 上同步删除（`AGS_DELETE_MISSING=true` 时） |
| 远端改了、本地也改了 | **本地为准** |
| 远端有、本地没有 | git 上删除 |
| 远端有、本地没有，且路径不匹配 `AGS_INCLUDE` | 保持不动（例如仓库自带的 `README.md`） |
| 远端与本地内容一致 | 不产生提交（日志显示 `无变更`） |
| 推送时远端被他人更新 | 自动重试；远端提交作为父提交保留，不做 force push |

**删除是镜像式行为，请注意**：如果 `AGS_INCLUDE` 用默认的 `.*`，那么这个分支的内容就等于
`AGS_SOURCE` 目录的快照。只想托管一部分文件就把 `AGS_INCLUDE` 收窄（例如 `\.conf$`）。
另外，为了避免「目录挂载错了」把仓库清空：当本地一个匹配文件都没有、而远端仍有受管文件时，
服务会直接报错跳过本轮（除非显式设置 `AGS_ALLOW_EMPTY=true`）。

## 4. 运行与运维

```bash
make logs            # 跟随日志
make once            # 立即同步一次（适合放到宿主机 crontab / systemd timer）
make dry-run         # 试运行，只显示变更
make check           # 打印当前生效的配置与同步计划
make restart
make down
```

也可以完全不用常驻进程，交给宿主机调度：

```cron
*/5 * * * * docker run --rm \
  -e AGS_GIT_REPO=https://github.com/you/my-configs.git -e AGS_GIT_TOKEN=xxx \
  -e AGS_INCLUDE='\.conf$' \
  -v /srv/data:/data -v /etc/nginx:/source:ro \
  ghcr.io/<owner>/<repo>:latest --once
```

手动触发：

```bash
curl -X POST http://127.0.0.1:8080/sync          # 202，下一轮循环立即执行
curl -s http://127.0.0.1:8080/status | jq        # 运行状态、上次结果、下次时间
```

| 端点 | 说明 |
| --- | --- |
| `GET /healthz` | 存活探测，固定返回 200（`Dockerfile` 的 `HEALTHCHECK` 调的就是 `--healthcheck`） |
| `GET /status` | 详细状态：`runs` `failures` `last_run` `next_run` `last_error`；上次同步失败时返回 503 |
| `POST /sync` | 请求立即同步（设置了 `AGS_API_TOKEN` 则需要 Bearer 令牌） |

容器健康检查只依赖 `AGS_LISTEN`：HEALTHCHECK 与守护进程共享同一份环境变量，
所以把端口从 8080 改成别的也不会让容器无端变成 `unhealthy`；想彻底关掉端点就设
`AGS_LISTEN=""`，健康检查会直接判定通过。

日志全部输出到 stdout（`docker logs` 可见），token 在任何日志与报错里都会被替换为 `***`。
服务会自己持有 `/data/.autogitsync.lock`，同一个数据目录不会被两个进程同时操作。

## 5. 目录结构

```
app/cron.py        5 字段 cron 解析器（含 @daily 等别名，日/周为 OR 语义）
app/git_sync.py    环境变量配置 + 同步引擎（clone/fetch/覆盖/删除/提交/推送重试）
app/main.py        守护进程：调度循环、健康端点、CLI
tests/             62 个测试，使用真实本地裸仓库做端到端验证，无需网络
.github/workflows/ ci.yml（测试/静态检查）、docker.yml（构建 + 冒烟 + 发布）
Dockerfile  docker-compose.yml  Makefile
```

## 6. 自动构建与发布（GitHub Actions）

推到 GitHub 后开箱即用，**不需要配置任何 secret**。

| 触发 | 行为 |
| --- | --- |
| PR、push 到 `main`、手动运行 | `CI`：单元测试（Python 3.12 + 3.13）、pyflakes、环境变量装配校验、hadolint |
| PR | `Docker`：测试 + 构建镜像 + **冒烟测试**（真的把容器跑起来验证同步/健康端点/优雅退出），不发布 |
| push 到 `main` | 发布 `ghcr.io/<owner>/<repo>:main`、`:latest`、`:sha-xxxxxxx`（linux/amd64 + arm64） |
| push 标签 `v1.2.3` | 额外发布 `:1.2.3`、`:1.2`，并把版本号写进镜像（`docker run <image> --version` 可看到） |
| 手动 `workflow_dispatch` | 可用 `publish` 开关决定这次只构建还是也发布 |

> `:latest` 跟随 `main` 分支；打 tag 只产生语义化版本标签，不会移动 `latest`。
> 想要「最新发行版」请显式用版本号（例如 `:1.2.3`）。

发布后的镜像（GHCR 用内置的 `$GITHUB_TOKEN`，零配置）：

```bash
docker pull ghcr.io/<owner>/<repo>:latest
docker run --rm ghcr.io/<owner>/<repo>:latest --version
```

> 如果 GHCR 里的包是私有的，别人需要先 `docker login ghcr.io` 才能拉；
> 想公开就去 仓库 → `Packages` → 该包 → `Package settings` → `Change visibility`。

发一个版本：

```bash
git tag v1.2.0
git push origin v1.2.0     # 自动构建多架构镜像并发布 :1.2.0 与 :1.2
```

**可选：同时发布到 Docker Hub。** 在仓库 `Settings → Secrets and variables → Actions` 里加：

- Variable `DOCKERHUB_USERNAME` = 你的 Docker Hub 用户名
- Secret `DOCKERHUB_TOKEN` = Docker Hub 的 Access Token（权限选 Read & Write）

配置后 `dockerhub` job 会自动把同一份多架构镜像**搬运**（`docker buildx imagetools create`，
不重新构建）到 `docker.io/<用户名>/<仓库名小写>:<同样的标签>`；没配置则该 job 自动跳过。

镜像里跑的冒烟测试会把容器真正启动起来，验证：`--check` / `--once` 同步出正确文件、
不匹配 `AGS_INCLUDE` 的文件不被同步、二次同步幂等、健康端点（含默认 8080 与自定义端口）
可用、镜像内置 `HEALTHCHECK` 能转为 `healthy`、`docker stop` 触发 SIGTERM 后退出码为 0。
所以 Dockerfile 一旦改坏，PR 阶段就会被拦住，不会发布出去。

## 7. 常见问题

**认证失败 / 推送被拒？** 确认 token 有仓库写权限（GitHub fine-grained PAT 需要
`Contents: Read and write`），以及 `AGS_GIT_USERNAME` 是否匹配你的平台。服务不会交互式
索要密码，认证失败会直接报错并在下一轮重试。

**cron 用的是哪个时区？** 容器本地时区，用 `TZ` 环境变量控制（compose 示例里是
`Asia/Shanghai`）。`@daily` 表示该时区的 00:00。

**符号链接怎么处理？** 指向文件的软链接会按其内容复制成普通文件；指向目录的软链接不会被跟进。
`AGS_SOURCE` 内的 `.git` 目录始终跳过。

**能不删除任何远端文件吗？** 设 `AGS_DELETE_MISSING=false`，此时只做「本地 → git」的单向增量。

**为什么没有数据库/状态文件？** 状态就是远端分支本身：每轮都从远端最新状态重放本地文件，
所以服务是无状态的、可随时重启，也不会出现「同步到一半崩了」的半成品状态。

**首次运行很慢？** 第一次需要 clone 整个仓库到 `/data/repo`。把 `/data` 挂载到持久化卷即可，
之后每轮只做 `fetch` 增量。

**怎么确认当前到底用的是哪些参数？** `docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --check`
（或 `make check`）会把所有生效的环境变量原样打印出来。

## 8. 本地测试

```bash
make test        # = python3 -m unittest discover -s tests -t . -v
```

测试用本地裸仓库（`git init --bare`）当远端，不需要任何依赖或网络，覆盖：首次推送、
幂等无变更、修改/删除、本地为准、推送竞态重试、目录与文件互转、二进制与可执行位、
精确的 include/exclude、空目录保护、试运行不留痕、token 不泄漏、凭据注入（校验 git 实际
发出的 `Authorization: Basic` 头）、环境变量解析与校验、健康端点。

## 9. 许可

MIT
