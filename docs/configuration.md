# Configuration reference

[Home](../README.md) · **English** | [简体中文](configuration.zh-CN.md)

All application settings are environment variables. Only `GIT_REPO` is required.
In the tables, `""` means an empty string. Paths refer to paths **inside the container**.

## Repository

| Variable | Default | Description |
| --- | --- | --- |
| `GIT_REPO` | required | Repository URL; HTTP(S), local paths, and `file://` are supported. SSH URLs require a separately configured SSH client and credentials. |
| `GIT_TOKEN` | `""` | HTTP(S) token with repository write permission. Public visibility does not grant push access. |
| `GIT_BRANCH` | `main` | Target branch; created if missing. |
| `GIT_USERNAME` | `x-access-token` | HTTP Basic authentication username; choose one accepted by your Git provider. |
| `GIT_AUTHOR_NAME` | `AutoGitSync` | Commit author name. |
| `GIT_AUTHOR_EMAIL` | `autogitsync@localhost` | Commit author email. |
| `COMMIT_MESSAGE` | `sync: {count} file(s) changed at {time}` | Template; also supports `{changed}`, `{deleted}`, `{source}`, and `{host}`. The last two expose the source path or container hostname if used. |
| `PUSH_RETRIES` | `3` | Additional attempts after a failed push. |

Prefer a separate `GIT_TOKEN` over credentials embedded in `GIT_REPO`. Token injection is
for HTTP(S) only; use HTTPS for network repositories. The standard image does not install
an SSH client. See [local builds](../CONTRIBUTING.md) if you need to extend it.

## Files and history

| Variable | Default | Description |
| --- | --- | --- |
| `SOURCE_DIR` | `/source` | Local directory to scan; mount it read-only. |
| `REPO_DIR` | `/data/repo` | Git work copy. Keep it separate from the source and persist `/data`. |
| `INCLUDE` | `.*` | Inclusion regex matched against relative paths with `re.search`. |
| `EXCLUDE` | `""` | Exclusion regex; takes precedence over `INCLUDE`. `.git` entries are always skipped. |
| `DELETE_MISSING` | `true` | Remove managed paths missing from the source. |
| `ALLOW_EMPTY` | `false` | Permit deletion when no local file matches but remote managed files remain. |
| `FORCE_PUSH_LATEST` | `0` | Keep at most N commits on the target branch using a force push. `0` disables truncation. Also applies when file contents have not changed. |

See [usage](usage.md) for regex recipes, symlink handling, path conflicts, and the limits of
history rewriting. `FORCE_PUSH_LATEST` is a nonnegative integer, not a boolean.

## Scheduling

| Variable | Default | Description |
| --- | --- | --- |
| `RUN_ON_START` | `true` | Sync immediately when the daemon starts. |
| `SCHEDULE` | `""` | Five-field cron expression; overrides `INTERVAL` when nonempty. |
| `INTERVAL` | `5m` | Fixed delay after a sync finishes, when no cron schedule is configured. |
| `TZ` | `UTC` | Container time zone used for cron and timestamps. |

- Cron fields: minute, hour, day of month, month, day of week. Ranges, lists, steps,
  and aliases such as `@daily` are supported. Restricted day-of-month and day-of-week
  fields use OR semantics.
- Intervals accept values such as `30s`, `5m`, `2h`, `1d`; a bare number means seconds.
- Syncs run serially. Missed cron slots during a running sync are not replayed.
- A manual trigger requests the next sync; multiple pending requests are coalesced.

## Runtime and endpoint

| Variable | Default | Description |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `LOG_LANG` | `en` | English by default; `zh`, `zh-CN`, and `zh_CN` select Chinese. Other values fall back to English. |
| `LISTEN` | `0.0.0.0:8080` | HTTP endpoint address. Empty string or port `0` disables it. |
| `API_TOKEN` | `""` | When nonempty, `POST /sync` requires this Bearer token. Does not protect GET endpoints. |

Logs, errors, CLI help, and `--check` messages follow `LOG_LANG`. Endpoint field names
remain stable. `--check` shows the sync plan and key settings, not every variable.

Booleans accept `true/false`, `1/0`, `yes/no`, and `on/off`. Quote boolean strings and
regexes in Compose. Invalid required settings, directories, regexes, cron expressions,
and ports fail at startup with exit code `2`.

`AUTOGITSYNC_VERSION` is build metadata, not a user setting. CI injects it through a
build argument; `--version` and `/status` report it.
