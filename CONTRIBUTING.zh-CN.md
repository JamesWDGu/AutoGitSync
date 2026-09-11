# 参与开发

[首页](README.zh-CN.md) · [English](CONTRIBUTING.md) | **简体中文**

保持 AutoGitSync 极简：Python 3.9+、标准库和 Git。配置只走环境变量，只有 `GIT_REPO`
必填。能用现有设计解决的问题，不要引入数据库、应用配置文件或额外依赖。

## 开发

```text
app/cron.py       Five-field cron parser
app/git_sync.py   Environment configuration and sync engine
app/main.py       Scheduler, control CLI, and health endpoint
app/i18n.py       English messages and Chinese translations
tests/           Self-contained unittest tests with local bare repositories
docs/            User reference and advanced usage
```

模块是平铺的，不是 Python 包。测试将 `app` 加入 `sys.path`。

```bash
python3 -m unittest discover -s tests -t .
```

测试不需要网络、Docker 或第三方 Python 包。静态检查可在临时开发虚拟环境中安装 pyflakes：

```bash
python3 -m venv /tmp/autogitsync-lint
/tmp/autogitsync-lint/bin/python -m pip install pyflakes
/tmp/autogitsync-lint/bin/python -m pyflakes app tests tools
```

## 内存基准

```bash
python3 tools/benchmark_memory.py --files 100000
```

脚本使用模拟的目录清单和 Git 输出，不同步真实文件、不访问远端，也不需要 Docker。
通过 `tracemalloc` 测量 Python 内存分配，不代表容器 RSS，也不包含原生库、Git 子进程
或文件系统缓存。单独的解析测试不计入事先构造好的输入字符串。

对比不同版本时，在独立进程中使用相同的解释器和文件数量。通过 `--app-dir` 指定另一个
检出目录下的 `app` 来测量基线。回归测试检查对象释放和逐项遍历，而不是断言依赖平台的
MiB 阈值。真实容器峰值仍需在 Docker 主机上测量。

## 修改检查清单

- 保持本地优先、限定范围删除、空源保护、凭据脱敏及单实例锁，试运行也必须加锁。
- 每项缺陷修复都添加回归测试。使用临时目录和本地裸 Git 仓库，不依赖个人路径或外部服务。
- 代码、注释和提交消息使用英文。运行消息通过 `t(...)` 包装，中文翻译放在 `app/i18n.py`。
- 两种语言的 README 和参考文档保持一致。测试会核对配置表与代码默认值，并检查本地文档链接。
- README 只作为入口，高级内容放到 `docs`，不要重新变成完整手册。
- 修改工作流时解析 YAML，对每个 `run` 块执行 `bash -n`，复杂脚本要本地实跑。
  容器行为由 CI 冒烟测试验证。

英文提交标题使用 `feat:`、`feat!:`、`fix:`、`docs:` 或 `ci:`，正文解释动机和影响。
不要提交个人/设备信息或密钥。真实发布镜像地址是有意保留的，确保示例可复制。

## 本地构建镜像

部署示例默认使用发布镜像，不从源码构建。`make build` 是独立的开发操作：

```bash
make build IMAGE=autogitsync:dev
```

要通过 Compose 测试此镜像，临时将服务的 `image` 改为 `autogitsync:dev`，
再按 README 执行检查、试运行和启动。`make up` 或 `make build` 都不会自动切换配置的镜像。
构建和运行容器需要 Docker daemon。

标准镜像有 Git，但没有 SSH 客户端。自定义 SSH 部署需要添加客户端，显式提供凭据及
经过验证的主机密钥；不要把私钥构建进镜像。

## CI 与发布

使用仓库内的[发布流程 skill](.agents/skills/autogitsync-release/SKILL.md)，统一是否发版、
版本递增、授权边界和发布恢复的决策。
[发布检查清单（英文）](.agents/skills/autogitsync-release/references/release-checklist.md)
覆盖验证、精确 SHA 检查关卡、镜像仓库核验及最终报告。skill 及附带文件统一维护英文，不另建中文副本。

skill 位于 `.agents/skills`，可供工具按项目发现。在已信任的 pi 项目中新开会话，执行：

```text
/skill:autogitsync-release
```

加载 skill 不代表授权推送或发版。仅文档/skill 的修改通常不递增版本、不创建新 Release；
push 到 main 仍会运行现有镜像工作流。

| 工作流 | 职责 |
| --- | --- |
| `.github/workflows/ci.yml` | Python 测试、配置检查、pyflakes、Dockerfile lint |
| `.github/workflows/docker.yml` | 测试 → 容器冒烟测试 → 多架构镜像发布 → Release |

发布必须同时依赖**单元测试和容器冒烟测试**。PR 不发布产物。
push 到 `main` 更新 `:main`、`:latest` 和 SHA 标签；推送新的 `v*` 版本标签后，
发布版本/minor 镜像标签，并在镜像发布成功后创建 GitHub Release。稳定版本发布也会更新
`:latest`；需要可复现部署时，请固定版本标签。

维护者应在审查和验证后选择尚未使用的版本标签，不要复用或重写已经发布的标签。
CI 注入 `AUTOGITSYNC_VERSION`；除了工作流结果，也要核验 `--version` 或镜像配置。

GHCR 发布使用工作流的 `GITHUB_TOKEN`，包可见性及仓库权限需要允许目标用户拉取。
可选 Docker Hub 镜像同步使用仓库变量 `DOCKERHUB_USERNAME` 和 secret `DOCKERHUB_TOKEN`；
未设置前者时自动跳过该任务。
