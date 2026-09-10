# AutoGitSync

**English** | [简体中文](README.zh-CN.md)

Sync a local directory to Git on a schedule. **Local files win**, relative paths stay
intact, and unchanged files produce no new commit by default.

One container. Environment variables only. No third-party Python dependencies.

## Quick start

Create a `docker-compose.yml` with the following content. Replace the repository URL
and token with your own; the token needs permission to **push**, even to a public repository.

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

Create `configs` next to that file and put the files you want to sync inside it.
The named volume keeps the Git work copy separate from your source files.

> **Before starting:** the first sync runs immediately, then every 5 minutes. By default,
> locally missing files are deleted from the target branch within the managed scope.
> Use a dedicated branch, narrow `INCLUDE`, or set `DELETE_MISSING: "false"` if needed.

Check the plan before starting the service:

```bash
mkdir -p configs
# Add the files you want to sync to ./configs before continuing.
docker compose run --rm autogitsync --check
docker compose run --rm autogitsync --dry-run
docker compose up -d
docker compose logs -f --tail=100
```

No source checkout, local image build, Make, or host port is required.
Prefer plain Docker? See [other deployment options](docs/usage.md).

## Common configuration

Set these under `environment` in Compose. **Only `GIT_REPO` is required**; other settings
have defaults. Authentication may instead be provided through your Git/SSH environment.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GIT_REPO` | required | Target repository URL |
| `GIT_TOKEN` | `""` | Token with repository write permission |
| `GIT_BRANCH` | `main` | Target branch |
| `INCLUDE` | `.*` | Regex selecting relative file paths, e.g. `\.conf$` |
| `INTERVAL` | `5m` | Fixed interval, e.g. `30s`, `5m`, `2h` |
| `SCHEDULE` | `""` | Five-field cron, e.g. `0 * * * *`; overrides `INTERVAL` |
| `DELETE_MISSING` | `true` | Delete managed files that disappeared locally |
| `LOG_LANG` | `en` | Runtime language: `en` or `zh` |

[All configuration options](docs/configuration.md) · [File matching examples](docs/usage.md)

## Sync behavior

- **One way:** source files are never modified; local content overwrites remote changes.
- **Scoped deletion:** only paths matching `INCLUDE` and not matching `EXCLUDE` are managed.
  A file/directory conflict that would remove an unmanaged file fails instead.
- **Empty-source protection:** if nothing matches locally while managed files remain
  remotely, the sync refuses to delete them unless `ALLOW_EMPTY=true`.
- **History preserved by default:** rejected pushes are retried on top of the updated
  remote. `FORCE_PUSH_LATEST=N` optionally limits branch history using force pushes.
- **Ignored files stay ignored:** the target repository's `.gitignore` is respected;
  skipped managed files produce a warning.

History limits are **not a secret-erasure guarantee**. Do not commit credentials you
would not want stored in Git. See the [history and safety notes](docs/usage.md).

## Documentation

- [Configuration reference](docs/configuration.md) — all variables, defaults, scheduling.
- [Usage and troubleshooting](docs/usage.md) — filters, history limits, manual syncs,
  health endpoints, Docker alternatives, and common errors.
- [Contributing](CONTRIBUTING.md) — local builds, tests, and releases.

Published images support `linux/amd64` and `linux/arm64`. `:latest` follows `main`;
use a version tag when you want to pin a release.

## License

[MIT](LICENSE)
