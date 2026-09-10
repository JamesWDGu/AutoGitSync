# 配置参考

[首页](../README.zh-CN.md) · [English](configuration.md) | **简体中文**

应用配置全部来自环境变量，只有 `GIT_REPO` 必填。表中的 `""` 表示空字符串，
`required` 表示必填。路径均为**容器内路径**。

## 仓库

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GIT_REPO` | required | 仓库地址，支持 HTTP(S)、本地路径和 `file://`。SSH 地址需要另行配置 SSH 客户端和凭据。 |
| `GIT_TOKEN` | `""` | 具有仓库写权限的 HTTP(S) token；仓库公开不代表允许匿名推送。 |
| `GIT_BRANCH` | `main` | 目标分支，不存在时创建。 |
| `GIT_USERNAME` | `x-access-token` | HTTP Basic 认证用户名，请使用 Git 托管平台接受的值。 |
| `GIT_AUTHOR_NAME` | `AutoGitSync` | 提交作者名称。 |
| `GIT_AUTHOR_EMAIL` | `autogitsync@localhost` | 提交作者邮箱。 |
| `COMMIT_MESSAGE` | `sync: {count} file(s) changed at {time}` | 提交消息模板，还支持 `{changed}`、`{deleted}`、`{source}`、`{host}`。后两项会将源路径或容器主机名写入提交消息。 |
| `PUSH_RETRIES` | `3` | 推送失败后的额外尝试次数。 |

建议单独设置 `GIT_TOKEN`，不要把凭据嵌入 `GIT_REPO`。token 注入只适用于 HTTP(S)，
网络仓库应使用 HTTPS。标准镜像没有安装 SSH 客户端，需要时可参考[本地构建](../CONTRIBUTING.zh-CN.md)扩展镜像。

## 文件和历史

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SOURCE_DIR` | `/source` | 待扫描的源目录，建议只读挂载。 |
| `REPO_DIR` | `/data/repo` | Git 工作副本；与源目录分离，并持久化 `/data`。 |
| `INCLUDE` | `.*` | 包含正则，使用 `re.search` 匹配相对路径。 |
| `EXCLUDE` | `""` | 排除正则，优先于 `INCLUDE`；始终跳过 `.git` 条目。 |
| `DELETE_MISSING` | `true` | 删除本地已不存在的受管路径。 |
| `ALLOW_EMPTY` | `false` | 本地没有匹配文件、远端仍有受管文件时，是否允许删除。 |
| `FORCE_PUSH_LATEST` | `0` | 通过强推让目标分支最多保留 N 个提交，`0` 关闭截断；文件内容未变化时也会执行限制。 |

正则、符号链接、路径冲突及历史重写的限制见[使用说明](usage.zh-CN.md)。
`FORCE_PUSH_LATEST` 必须是非负整数，不是布尔值。

## 调度

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RUN_ON_START` | `true` | 守护进程启动后立即同步。 |
| `SCHEDULE` | `""` | 五字段 cron；非空时优先于 `INTERVAL`。 |
| `INTERVAL` | `5m` | 未配置 cron 时，每次同步完成后等待的固定间隔。 |
| `TZ` | `UTC` | 容器时区，用于 cron 和时间戳。 |

- cron 字段依次是分、时、日、月、星期；支持范围、列表、步长及 `@daily` 等别名。
  当日和星期字段都有限制时，使用 OR 语义。
- 固定间隔支持 `30s`、`5m`、`2h`、`1d` 等格式；纯数字表示秒。
- 同步串行执行，不补跑同步过程中错过的 cron 时间点。
- 手动触发请求下一次同步，多次待处理请求会合并。

## 运行与接口

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL`。 |
| `LOG_LANG` | `en` | 默认英文；`zh`、`zh-CN`、`zh_CN` 切换中文，其他值回退英文。 |
| `LISTEN` | `0.0.0.0:8080` | HTTP 接口地址；空字符串或端口 `0` 表示关闭。 |
| `API_TOKEN` | `""` | 非空时 `POST /sync` 需要此 Bearer token，不保护 GET 接口。 |

日志、错误、CLI 帮助和 `--check` 消息随 `LOG_LANG` 切换，接口字段名保持不变。
`--check` 显示同步计划及关键配置，不是全部变量清单。

布尔值支持 `true/false`、`1/0`、`yes/no`、`on/off`。在 Compose 中给布尔字符串和正则
加引号。必填配置、目录、正则、cron 或端口非法时，启动退出码为 `2`。

`AUTOGITSYNC_VERSION` 是构建元数据，不是用户配置。CI 通过构建参数注入，
`--version` 和 `/status` 会报告此值。
