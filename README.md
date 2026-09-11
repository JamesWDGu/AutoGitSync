# AutoGitSync

**English** | [简体中文](README.zh-CN.md)

> [!IMPORTANT]
> **🤖 AI writes the code. I set the bar—and use it myself.**
>
> - **100% AI-generated, zero human-written code.** Part learning and research,
>   but production-grade is the bar—not a throwaway demo.
> - **Copy, fork, even commercialize** under [0BSD](LICENSE) (if anyone actually wants to 🤣).
>   General improvements? PRs back here are welcome, so everyone benefits.
> - **Bring your AI:** [AGENTS.md](AGENTS.md) and [skills](.agents/skills/) are included.
>   Open a PR; I'll review it as soon as I can after notification. AI quality directly
>   affects code quality—review and test what it writes.
> - **Low on tokens?** Open an issue. If it makes sense for this project,
>   I'll put my spare tokens to work on it.
> - **Found some inspiration?** Leave a star so I know those tokens didn't burn in vain ⭐.

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
- **History preserved by default:** `FORCE_PUSH_LATEST` defaults to `0`, disabling history
  truncation; rejected pushes are retried on top of the updated remote. To limit history,
  set it to a positive integer. For example, `FORCE_PUSH_LATEST=3` uses force pushes to keep
  at most the 3 most recent commits on the target branch.
- **Ignored files stay ignored:** the target repository's `.gitignore` is respected;
  skipped managed files produce a warning.

History limits are **not a secret-erasure guarantee**. Do not commit credentials you
would not want stored in Git. See the [history and safety notes](docs/usage.md).

## Documentation

- [Configuration reference](docs/configuration.md) — all variables, defaults, scheduling.
- [Usage and troubleshooting](docs/usage.md) — filters, history limits, manual syncs,
  health endpoints, Docker alternatives, and common errors.
- [Contributing](CONTRIBUTING.md) — local builds, tests, and releases.

Published images support `linux/amd64` and `linux/arm64`. Only stable version releases
from the `release` branch update `:latest`; ordinary pushes never publish images.
Use a version tag when you want to pin a release.

## License

[0BSD](LICENSE) for project-owned code, documentation, and agent resources.
Third-party components retain their own licenses.
