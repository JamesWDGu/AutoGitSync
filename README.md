# AutoGitSync

把一个本地目录定时同步到 Git 仓库。单容器、零第三方依赖（Python 3.12 + git）、
**配置全是环境变量，没有配置文件**。

```bash
docker run -d --restart unless-stopped \
  -e GIT_REPO=https://github.com/your-name/my-configs.git \
  -e GIT_TOKEN=ghp_xxxxxxxx \
  -v /etc/nginx:/source:ro \
  ghcr.io/jameswdgu/autogitsync:latest
```

上面这条命令就够了：每 5 分钟把 `/source` 里的文件**按原有相对路径**推到仓库。

同步规则只有三条：

| 情况 | 结果 |
| --- | --- |
| 两边都改了同一个文件 | **以本地为准** |
| 本地删了某个文件 | git 上也删掉（镜像式同步） |
| 推送时远端刚好被别人改过 | 自动重拉重放后重试，不强推、不丢远端历史 |

---

## 用法

**用 docker compose（推荐）：**

```bash
cp .env.example .env         # 填 GIT_REPO / GIT_TOKEN
# 编辑 docker-compose.yml，把 ./example-source 换成你要同步的目录

make check                   # 先看生效的配置、匹配到的文件、接下来 5 次时间
make dry-run                 # 再看将要新增 / 修改 / 删除什么（都不会提交）
make up                      # 启动 = docker compose up -d --build
make logs
```

**直接 docker run**（不用 compose）：

```bash
# 把 GIT_REPO 换成你要同步到的仓库，/etc/nginx 换成要同步的目录
docker run -d --name autogitsync --restart unless-stopped \
  -e GIT_REPO=https://github.com/your-name/my-configs.git \
  -e GIT_TOKEN=ghp_xxxxxxxx \
  -e INCLUDE='\.(conf|ya?ml)$' \
  -v /etc/nginx:/source:ro \
  -v "$PWD/data:/data" \
  ghcr.io/jameswdgu/autogitsync:latest
```

想用本地构建的镜像，就加一步 `docker build -t autogitsync:latest .`，
再把上面的镜像地址换成 `autogitsync:latest`。

> 建议给 `/data` 挂一个卷：里面是 git 工作副本，有它就不用每轮重新 clone。

## 配置

只有 `GIT_REPO` 是必填的，其他都有默认值（私有仓库再补一个 `GIT_TOKEN`）。

**必填**

| 变量 | 说明 |
| --- | --- |
| `GIT_REPO` | 仓库地址。`https://` 会注入 token；也支持本地路径 / `file://` / ssh |

**常用**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GIT_TOKEN` | 空 | 访问令牌，私有仓库需要 |
| `SOURCE_DIR` | `/source` | 要同步的目录（挂载进来的容器内路径） |
| `INCLUDE` | `.*` | 只同步匹配这个正则的**相对路径**，例如 `\.conf$` |
| `SCHEDULE` | 空 | cron 周期，5 字段，例如 `*/5 * * * *`、`@daily` |
| `INTERVAL` | `5m` | 或者用固定间隔：`30s` / `5m` / `2h`；设了 cron 就以 cron 为准 |
| `DELETE_MISSING` | `true` | 本地删掉的文件是否也从 git 删除 |
| `TZ` | `UTC` | cron 按哪个时区解释，例如 `Asia/Shanghai` |

**其余（按需）**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EXCLUDE` | 空 | 排除正则；`SOURCE_DIR` 里的 `.git` 永远跳过 |
| `GIT_BRANCH` | `main` | 目标分支，不存在时自动创建 |
| `GIT_USERNAME` | `x-access-token` | Basic 用户名：GitHub 用它，GitLab 用 `oauth2`，Gitea 任意非空 |
| `REPO_DIR` | `/data/repo` | git 工作副本目录 |
| `RUN_ON_START` | `true` | 启动后是否立刻同步一次 |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LISTEN` | `0.0.0.0:8080` | 健康端点，设空字符串关闭 |
| `API_TOKEN` | 空 | 非空时 `POST /sync` 需要 Bearer 令牌 |
| `ALLOW_EMPTY` | `false` | 见下方「安全阀」 |
| `COMMIT_MESSAGE` | `sync: {count} file(s) changed at {time}` | 占位符还有 `{changed}` `{deleted}` `{source}` `{host}` |
| `PUSH_RETRIES` | `3` | 推送被拒时的重试次数 |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | `AutoGitSync` / `autogitsync@localhost` | 提交者信息 |

布尔值写 `true/false`、`1/0`、`yes/no`、`on/off` 都行。配置写错（目录不存在、
正则或 cron 非法等）会在启动时直接报错退出，不会带着错误配置跑起来。

### `INCLUDE` 的常用写法

`INCLUDE` 作用于**相对路径**（形如 `svc-a/compose.yaml`），用 `re.search` 匹配，所以锚定结尾就够了：

```regex
.*                                                     # 全部同步（默认）
\.(conf|ya?ml|env)$                                    # 按后缀
^[^/]+/(?:.*/)?compose\.ya?ml$                         # 各子目录里的 compose.yaml / compose.yml
^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$   # 各子目录里的 compose + .env（含 .env.local）
```

开头的 `^[^/]+/` 保证「至少在一层子目录里」，这样根目录的同名文件不会被选中；
`(?:.*/)?` 允许任意深度。写成 `^[^/]+/.*compose\.ya?ml$` 会连 `svc-a/my-compose.yaml` 也匹配上。

### 安全阀

默认 `INCLUDE=.*` 意味着**分支内容 = `SOURCE_DIR` 的快照**，只想托管一部分文件
就把 `INCLUDE` 收窄。

另外，如果挂载的目录里一个匹配文件都没有、而远端仍有受管文件，服务会直接报错跳过本轮，
避免「目录挂错」把仓库清空；确认无误可以设 `ALLOW_EMPTY=true`。

## 运维

```bash
make logs       # 跟随日志
make once       # 立即同步一次（也可以放进宿主机 crontab / systemd timer）
make dry-run    # 试运行
make check      # 打印生效的配置
```

想从宿主机查状态或手动触发，先按 `docker-compose.yml` 里的注释放开 `ports`：

```bash
curl -s http://127.0.0.1:8080/status | jq    # 状态、上次结果、下次时间
curl -X POST http://127.0.0.1:8080/sync      # 让容器马上同步一次（202）
```

| 端点 | 说明 |
| --- | --- |
| `GET /healthz` | 存活探测，固定 200（镜像的 `HEALTHCHECK` 用的就是它） |
| `GET /status` | 详细状态；上次同步失败时返回 503 |
| `POST /sync` | 立即同步（设了 `API_TOKEN` 则需 Bearer 令牌） |

健康端点在容器内监听，容器自身的 `HEALTHCHECK` 也走容器内的 loopback，
**不映射端口也能正常工作**；映射只是为了让你从宿主机访问上面三个接口。
健康检查只读 `LISTEN`，所以把端口从 8080 改成别的也不会让容器误报 `unhealthy`。
日志走 stdout（`docker logs` 可看），token 在日志和报错里一律显示为 `***`。

## GitHub Actions

推到 GitHub 就生效，不需要配任何 secret。

| 触发 | 做什么 |
| --- | --- |
| PR | 跑测试 + 构建镜像 + 启动容器做冒烟测试，不发布 |
| push 到 `main` | 发布 `ghcr.io/jameswdgu/autogitsync:main`、`:latest`、`:sha-xxxxxxx` |
| push 标签 `v1.2.1` | 发布 `:1.2.1`、`:1.2`，版本号写进镜像，并自动创建 GitHub Release |

镜像都是 `linux/amd64` + `linux/arm64`。`:latest` 跟随 `main`，打 tag 不会移动它。

```bash
docker pull ghcr.io/jameswdgu/autogitsync:latest

git tag v1.2.1 && git push origin v1.2.1     # 发一个新版本（镜像 + Release）
```

> GHCR 的包如果是私有的，需要先 `docker login ghcr.io` 才能拉；想公开就去
> 仓库 → `Packages` → `Package settings` → `Change visibility`。
>
> 想同时发到 Docker Hub：加 Variable `DOCKERHUB_USERNAME` + Secret `DOCKERHUB_TOKEN`
> （Read & Write），`dockerhub` job 会自动把同一份镜像搬过去；不配就自动跳过。

## 常见问题

**推送被拒 / 认证失败？** 确认 token 有仓库写权限（GitHub fine-grained PAT 需要
`Contents: Read and write`），以及 `GIT_USERNAME` 是否匹配你的平台。

**`.env` 之类的文件没同步上去？** 先看日志里有没有「被目标仓库的 `.gitignore` 排除」的告警：
`git add` 会**静默跳过**仓库 `.gitignore` 里的文件，而很多仓库模板默认就写了 `.env`。
把那条规则从仓库的 `.gitignore` 里去掉即可。另外 `INCLUDE` 只写 `\.env$` 会漏掉
`.env.local` 这类变体，用 `\.env(?:\.[^/]+)?$` 更稳。

**`SOURCE_DIR` 里包含了 `data/repo`（工作副本）？** 说明这两个 volume 在宿主机上重叠了，
把数据卷挪到同步目录外面（例如 `./configs:/source:ro` + `./data:/data`）。
服务检测到这种重叠会跳过工作副本并打告警，不会再自我复制；但如果容器路径文本上就是嵌套的
（比如 `SOURCE_DIR=/data`、`REPO_DIR=/data/repo`），启动时会直接报错拒绝运行。

**能不删除远端文件吗？** 设 `DELETE_MISSING=false`，就只做「本地 → git」的单向增量。

**为什么没有数据库、也不怕中途崩溃？** 状态就是远端分支本身：每轮都从远端最新提交
重新重放本地文件，所以服务无状态、可随时重启。

**第一次很慢？** 要先 clone 整个仓库到 `/data/repo`，之后每轮只做增量 fetch。

## 开发

```
app/cron.py       cron 解析器（5 字段，支持 @daily 等别名）
app/git_sync.py   环境变量配置 + 同步引擎
app/main.py       调度循环、健康端点、CLI
tests/            66 个测试，用本地裸仓库当远端，不需要网络
```

```bash
make test        # Python 3.9+ 直接跑，无依赖
```

## 许可

MIT
