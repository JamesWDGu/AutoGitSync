# AutoGitSync

[English](README.md) | **简体中文**

> [!IMPORTANT]
> **🤖 AI 写代码，我提需求、把关，也亲自用。**
>
> - **100% AI 生成，人工手写 0 行。** 为学习和研究而做，但按产品级标准要求，不是用完就扔的 demo。
> - **随意 copy / fork / 商用**，采用 [0BSD](LICENSE)（真有人想拿它赚钱吗 🤣）。
>   通用改进欢迎 PR 回来，方便大家取用。
> - **给 AI 的说明也备好了：** [AGENTS.md](AGENTS.md) 和 [skills](.agents/skills/)。
>   欢迎自己提 PR，收到通知我会尽快处理。AI 的水平直接影响代码质量，记得审查和测试。
> - **tokens 不够？** 提个 issue。需求对项目有意义，我就用空闲 tokens 帮你实现。
> - **有启发？** 点颗 star，让我知道这堆 tokens 没白烧 ⭐。

把本地目录定时同步到 Git。**以本地文件为准**，保留原有相对路径，默认没有变化就不产生新提交。

单容器运行，全部通过环境变量配置，没有第三方 Python 依赖。

## 快速开始

创建 `docker-compose.yml`，填入以下内容。把仓库地址和 token 换成自己的；
token 需要仓库的**推送权限**，公开仓库也不例外。

```yaml
services:
  autogitsync:
    image: ghcr.io/jameswdgu/autogitsync:latest
    restart: unless-stopped
    environment:
      GIT_REPO: https://github.com/your-name/my-configs.git
      GIT_TOKEN: your-write-token
    volumes:
      - ./configs:/source:ro
      - repo-data:/data

volumes:
  repo-data:
```

在同级目录创建 `configs`，把需要同步的文件放进去。
命名卷用于保存 Git 工作副本，不会混进源目录。

> **启动前注意：** 服务启动后立即同步一次，随后每 5 分钟同步。默认会删除目标分支中
> 本地已不存在的受管文件。建议使用专用分支；如果只管理部分文件，请设置 `INCLUDE`，
> 或用 `DELETE_MISSING: "false"` 关闭删除。

先检查配置和变更，再启动：

```bash
mkdir -p configs
# Add the files you want to sync to ./configs before continuing.
docker compose run --rm autogitsync --check
docker compose run --rm autogitsync --dry-run
docker compose up -d
docker compose logs -f --tail=100
```

不需要下载项目源码、本地构建镜像、安装 Make，也不需要映射宿主机端口。
想直接使用 Docker？见[其他部署方式](docs/usage.zh-CN.md)。

## 常用配置

在 Compose 的 `environment` 中添加即可。**只有 `GIT_REPO` 必填**，其他配置都有默认值。
认证也可以通过已有的 Git/SSH 环境提供。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `GIT_REPO` | required | 目标仓库地址 |
| `GIT_TOKEN` | `""` | 具有仓库写权限的 token |
| `GIT_BRANCH` | `main` | 目标分支 |
| `INCLUDE` | `.*` | 匹配相对路径的正则，例如 `\.conf$` |
| `INTERVAL` | `5m` | 固定间隔，例如 `30s`、`5m`、`2h` |
| `SCHEDULE` | `""` | 五字段 cron，例如 `0 * * * *`；优先于 `INTERVAL` |
| `DELETE_MISSING` | `true` | 删除本地已不存在的受管文件 |
| `LOG_LANG` | `en` | 运行消息语言：`en` 或 `zh` |

[全部配置](docs/configuration.zh-CN.md) · [文件匹配示例](docs/usage.zh-CN.md)

## 同步规则

- **单向同步：** 不修改源文件，本地内容覆盖远端改动。
- **限定管理范围：** 只管理匹配 `INCLUDE` 且未被 `EXCLUDE` 排除的路径。
  文件/目录冲突如果会删除管理范围外的文件，本轮同步会报错停止。
- **空目录保护：** 本地没有匹配文件、远端仍有受管文件时，拒绝删除；
  只有明确设置 `ALLOW_EMPTY=true` 才允许继续。
- **默认保留历史：** `FORCE_PUSH_LATEST` 默认为 `0`，不截断历史；推送冲突时基于更新后的远端重试。
  如需限制历史，将其设为正整数。例如 `FORCE_PUSH_LATEST=3` 表示通过强推，让目标分支最多保留最近 3 个提交。
- **尊重忽略规则：** 不绕过目标仓库的 `.gitignore`，被忽略的受管文件会产生告警。

限制历史**不等于彻底清除密钥**，不要提交不应进入 Git 的凭据。
详见[历史与安全说明](docs/usage.zh-CN.md)。

## 文档

- [配置参考](docs/configuration.zh-CN.md)：全部变量、默认值、调度规则。
- [使用与排错](docs/usage.zh-CN.md)：文件筛选、历史限制、手动同步、健康接口、
  其他 Docker 部署方式及常见错误。
- [参与开发](CONTRIBUTING.zh-CN.md)：本地构建、测试和发布。

发布镜像支持 `linux/amd64` 和 `linux/arm64`。只有从 `release` 分支发布稳定版本才更新
`:latest`，普通推送不会发布镜像；需要固定版本时，请使用对应的版本标签。

## 许可

项目自身的代码、文档及 agent 资源采用 [0BSD](LICENSE)。第三方组件保留各自的许可证。
