"""Runtime message language: English by default, Chinese optional via ``LOG_LANG``.

Every user-visible runtime string (logs, error messages, ``--check`` output, CLI help) is
written in English at the call site and passed through :func:`t`.  The Chinese catalog below
maps those English templates to their translation, so the code stays readable and there is
only one place to update when a message changes.

``LOG_LANG`` accepts ``en`` / ``zh`` (``zh-CN``, ``zh_CN`` … also work).  Anything else
falls back to English.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DEFAULT_LANGUAGE", "LANGUAGES", "language", "set_language", "t"]

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "zh")

_language = DEFAULT_LANGUAGE

# English template -> Chinese translation.  Keys must match the source exactly;
# tests/test_i18n.py asserts that every t("...") template has an entry here.
_ZH = {
    # -- cron field names ---------------------------------------------------
    "minute": "分钟",
    "minutes": "分钟",
    "hour": "小时",
    "hours": "小时",
    "day": "天",
    "days": "天",
    "day of month": "日",
    "month": "月",
    "day of week": "星期",
    # -- cron parser --------------------------------------------------------
    'Field "%s": %r is not a valid value': "字段「%s」中的 %r 不是合法数值",
    'Field "%s": %d is out of range %d-%d': "字段「%s」的值 %d 超出范围 %d-%d",
    'Field "%s" contains an empty value': "字段「%s」存在空的取值",
    'Field "%s": step %r is invalid (must be a positive integer)':
        "字段「%s」中的步长 %r 非法（必须为正整数）",
    'Field "%s": range %r starts after it ends': "字段「%s」的区间 %r 起始值大于结束值",
    'Field "%s" did not resolve to any value': "字段「%s」没有解析出任何取值",
    "cron expression is empty": "cron 表达式为空",
    "unsupported alias %r, available: %s": "不支持的别名 %r，可用：%s",
    " (this service only supports 5 fields: minute hour day month weekday)":
        "（本服务只支持 5 字段：分 时 日 月 周）",
    "cron expression needs 5 fields, got %d: %r%s": "cron 表达式需要 5 个字段，实际 %d 个：%r%s",
    "no matching time found within %d days: %s": "在 %d 天内未找到匹配时间：%s",
    # -- config parsing -----------------------------------------------------
    "Invalid interval: %r (examples: 30s / 5m / 2h / 1d)":
        "周期格式非法：%r（示例：30s / 5m / 2h / 1d）",
    "Interval must be greater than 0: %r": "周期必须大于 0：%r",
    "Invalid port: %r (example: 0.0.0.0:8080, empty disables the endpoint)":
        "端口非法：%r（示例：0.0.0.0:8080，留空表示关闭）",
    "Port out of range: %r": "端口超出范围：%r",
    "%s must be a boolean (true/false), got %r": "%s 必须是布尔值（true/false），当前是 %r",
    "%s must be an integer, got %r": "%s 必须是整数，当前是 %r",
    "%s cannot be less than %d, got %d": "%s 不能小于 %d，当前是 %d",
    "Invalid LOG_LEVEL: %r": "LOG_LEVEL 取值非法：%r",
    "GIT_REPO is required (Git repository URL)": "必须设置环境变量 GIT_REPO（Git 仓库地址）",
    "GIT_BRANCH cannot be empty": "GIT_BRANCH 不能为空",
    "SOURCE_DIR cannot be empty": "SOURCE_DIR 不能为空",
    "SOURCE_DIR is not an existing directory: %s (did you forget to mount it?)":
        "SOURCE_DIR 不是一个已存在的目录：%s（记得把目录挂载进来）",
    "INCLUDE is not a valid regex: %s (%s)": "INCLUDE 不是合法正则：%s（%s）",
    "EXCLUDE is not a valid regex: %s (%s)": "EXCLUDE 不是合法正则：%s（%s）",
    "SOURCE_DIR and REPO_DIR cannot be the same directory: %s":
        "SOURCE_DIR 与 REPO_DIR 不能是同一个目录：%s",
    "SOURCE_DIR and REPO_DIR cannot be nested: %s / %s":
        "SOURCE_DIR 与 REPO_DIR 不能互相嵌套：%s / %s",
    "SCHEDULE is not a valid cron expression: %s": "SCHEDULE 不是合法的 cron 表达式：%s",
    # -- sync result --------------------------------------------------------
    "failed: %s": "失败：%s",
    "dry run: %d added/updated, %d deleted": "试运行：新增/更新 %d，删除 %d",
    "no changes": "无变更",
    "committed %s: %d added/updated, %d deleted in %.1fs":
        "提交 %s：新增/更新 %d，删除 %d，耗时 %.1fs",
    # -- git engine ---------------------------------------------------------
    "git executable not found, make sure git is installed in the image":
        "未找到 git 可执行文件，请确认镜像中已安装 git",
    "git %s failed (exit %d): %s": "git %s 执行失败（exit %d）：%s",
    "no output": "无输出",
    "remote branch %s does not exist yet, it will be created by this push":
        "远端分支 %s 不存在，将在本次推送时创建",
    "work directory is not empty and is not a git repository, please clean it: %s":
        "工作目录非空且不是 git 仓库，请清理后重试：%s",
    "first run: cloning %s (branch %s)": "首次运行：克隆 %s（分支 %s）",
    "first run: remote has no branch %s yet, initializing an empty repository":
        "首次运行：远端暂无分支 %s，初始化空仓库",
    "SOURCE_DIR and REPO_DIR point to the same directory: %s":
        "SOURCE_DIR 与 REPO_DIR 指向同一个目录：%s",
    "SOURCE_DIR contains the work copy %s (the two mounts overlap on the host); skipping it "
    "- mount the data volume outside the synced directory":
        "SOURCE_DIR 里包含了工作副本 %s（宿主机上两个挂载目录重叠了），已跳过它；"
        "建议把数据卷和同步目录分开挂载",
    "failed to delete %s: %s": "删除 %s 失败：%s",
    "no file in %s matches %r, but the remote has %d managed file(s); skipping this run to "
    "avoid wiping the repository (set ALLOW_EMPTY=true if this is intended)":
        "本地目录 %s 中没有任何匹配 %r 的文件，但远端有 %d 个受管文件；"
        "为避免误删整个仓库已跳过本次同步（确认无误可设置 ALLOW_EMPTY=true）",
    "%d managed file(s) are excluded by the target repository's .gitignore and will not be "
    "committed: %s%s; remove the matching rule from that .gitignore (or exclude them with EXCLUDE)":
        "%d 个受管文件被目标仓库的 .gitignore 排除，git 不会提交它们：%s%s；"
        "要从仓库的 .gitignore 里去掉对应规则（或改用 EXCLUDE 明确排除）",
    "push rejected (the remote moved meanwhile), retry %d/%d: %s":
        "推送被拒绝（远端在此期间已更新），第 %d/%d 次重试：%s",
    "push still failing after %d attempts": "推送重试 %d 次后仍然失败",
    # -- schedule / daemon --------------------------------------------------
    "cron %r (container local time, %s)": "cron %r（容器本地时区，%s）",
    "local time": "本地时区",
    "every %g %s": "每 %g %s",
    "every %g seconds": "每 %g 秒",
    "sync requested": "已请求立即同步",
    "health endpoint cannot listen on %s:%d (%s), continuing without it":
        "健康端点无法监听 %s:%d（%s），服务继续运行",
    "health endpoint listening on http://%s:%d/healthz (/status for status, POST /sync to trigger)":
        "健康端点已启动：http://%s:%d/healthz（/status 查看状态，POST /sync 立即同步）",
    "LISTEN is invalid, cannot probe: %s": "LISTEN 配置非法，无法探测：%s",
    "health endpoint disabled, skipping check": "健康端点未启用，跳过检查",
    "health check failed: %s returned %d": "健康检查失败：%s 返回 %d",
    "health check failed: %s (%s)": "健康检查失败：%s（%s）",
    "cannot lock the data directory (another instance may be running): %s (%s)":
        "无法锁定数据目录（可能已有实例在运行）：%s（%s）",
    "received %s, stopping the scheduler (the current sync will finish first)":
        "收到信号 %s，停止调度（当前同步会先跑完）",
    "AutoGitSync %s starting: %s -> %s#%s, schedule %s":
        "AutoGitSync %s 启动：%s -> %s#%s，周期 %s",
    "sync rules: include=%r exclude=%r delete_missing=%s repo_dir=%s":
        "同步规则：include=%r exclude=%r delete_missing=%s repo_dir=%s",
    "GIT_REPO is an http(s) URL but GIT_TOKEN is not set; pushing to a private repository will fail":
        "GIT_REPO 是 http(s) 地址但未设置 GIT_TOKEN，私有仓库将无法推送",
    "sync failed: %s": "同步失败：%s",
    "unexpected error during sync": "同步出现未预期错误",
    "sync finished: %s": "同步完成：%s",
    "next sync at %s": "下次同步时间：%s",
    "AutoGitSync stopped": "AutoGitSync 已停止",
    "done: %s": "完成：%s",
    # -- --check output -----------------------------------------------------
    "effective configuration (all from environment variables):":
        "当前生效的配置（来自环境变量）:",
    "set": "已设置",
    "not set": "未设置",
    "matched files: %d (out of %d file(s) in the directory)":
        "匹配文件: %d 个（目录内共 %d 个文件）",
    "  ... (%d more)": "  …（其余 %d 个）",
    "schedule   : %s": "同步周期   : %s",
    "next 5 runs:": "接下来 5 次:",
    "note: nothing matched, the sync will refuse to delete anything (ALLOW_EMPTY=false)":
        "提示: 目前没有匹配到任何文件，同步时会拒绝执行删除（ALLOW_EMPTY=false）",
    "configuration error: %s": "配置错误：%s",
    "schedule configuration error: %s": "周期配置错误：%s",
    # -- CLI help -----------------------------------------------------------
    "Sync a local directory to a Git repository on a schedule (local wins on conflicts). "
    "Configured entirely through environment variables.":
        "把本地目录按原有路径定时同步到 Git 仓库（冲突以本地为准），配置全部来自环境变量",
    "sync once and exit": "立即同步一次后退出",
    "show what would change, without committing or pushing": "试运行：显示将要发生的变更，不提交不推送",
    "print the effective configuration and sync plan, then exit":
        "打印当前生效的配置与同步计划后退出",
    "probe the health endpoint (used by the Docker HEALTHCHECK)":
        "探测健康端点（供 Docker HEALTHCHECK 使用）",
    "override LOG_LEVEL": "覆盖 LOG_LEVEL",
}


def set_language(value: str) -> str:
    """Select the message language from ``LOG_LANG``; returns the language in use."""
    global _language
    text = (value or "").strip().lower()
    for candidate in LANGUAGES:
        if text == candidate or text.startswith(candidate + "_") or text.startswith(candidate + "-"):
            _language = candidate
            return _language
    _language = DEFAULT_LANGUAGE
    return _language


def language() -> str:
    """Language currently used for runtime messages."""
    return _language


def t(message: str, *args: Any) -> str:
    """Return ``message`` in the current language, formatted with ``args``."""
    text = _ZH.get(message, message) if _language == "zh" else message
    return text % args if args else text
