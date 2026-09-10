# Usage and troubleshooting

[Home](../README.md) · **English** | [简体中文](usage.zh-CN.md)

## File matching

Regexes match relative paths such as `svc-a/compose.yaml`. Use single quotes in Compose:

```yaml
environment:
  INCLUDE: '^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$'
```

| Regex | Selects |
| --- | --- |
| `.*` | Everything except `.git` entries |
| `\.(conf|ya?ml)$` | Configuration files by extension, at any depth |
| `^[^/]+/(?:.*/)?compose\.ya?ml$` | `compose.yaml` / `compose.yml` in subdirectories, not the root |
| `^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$` | The same plus `.env`, `.env.local`, etc. |

`EXCLUDE` takes precedence. A leading `^[^/]+/` requires a subdirectory; `(?:.*/)?`
allows deeper nesting without also matching names such as `my-compose.yaml`.

The target repository's `.gitignore` is respected. If `.env` files are skipped, check the
warning and remove the relevant ignore rule only if those files should really be stored
in Git. Do not use history truncation as a reason to commit live secrets.

## Filesystem safety

- Mount the source read-only and keep the work copy separate. The example uses a named
  volume at `/data`, avoiding a work copy nested under the source.
- If you use bind mounts, make them siblings, e.g. `./configs:/source:ro` and `./data:/data`.
  Physically overlapping work copies are skipped with a warning; identical or textually
  nested source/work-copy paths are rejected.
- File and directory symlinks are stored as **links**, not followed. Link target paths
  themselves are committed, so do not use links containing private device paths.
- Replacing a remote link with a regular file never writes through the link. File/directory
  swaps that would remove an unmatched or excluded file fail instead of silently deleting it.
  Resolve the layout conflict or deliberately adjust the filters before retrying.
- Empty-source protection is not protection against every accidental partial deletion.
  Review `--dry-run` when changing mounts or filters.

## Limit branch history

```yaml
environment:
  FORCE_PUSH_LATEST: "3"
```

`0` preserves normal history. A positive N keeps at most the last N commits on the target
branch; `1` keeps only its latest state. This also runs when file contents have not changed,
so enabling or lowering the limit does not require editing a source file. Unchanged runs
do not create extra commits just to fill the limit.

This mode requires force-push permission and may overwrite concurrent remote commits.
The oldest retained commit becomes a root; retained trees, messages, and dates are reused.
The reported commit ID is the new branch head after rewriting. A dry run reports the
configured limit but never rewrites or pushes history.

**It does not erase secrets everywhere.** Other branches, tags, clones, forks, and hosting
caches may still contain old data. Revoke or rotate an exposed credential first, then follow
your hosting provider's sensitive-data removal procedure. Use a dedicated sync branch.

## Manual sync and inspection

While the Compose service is running:

```bash
docker compose logs -f --tail=100
docker compose exec -T autogitsync python /app/main.py --trigger
```

`--trigger` requests a sync through the internal endpoint, using `LISTEN` and `API_TOKEN`.
Success means **accepted**, not finished; inspect the logs or `/status` for the result.
No host port is needed. If `LISTEN` is disabled, use offline `--once` instead.

`--check` scans source files without changing the work copy and can run at any time.
`--once` and `--dry-run` are **offline commands**: both need the work-copy lock, because
previewing a sync also fetches, resets, copies, and stages files.

```bash
docker compose stop autogitsync
docker compose run --rm --no-deps autogitsync --dry-run
# To apply immediately while stopped, use --once instead of --dry-run.
docker compose up -d
```

In a source checkout, `make trigger`, `make check`, `make once`, and `make dry-run` wrap
these commands. The last two require the daemon to be stopped. They refuse to run when
another process holds the lock; do not bypass it or delete the lock file.

## Health and HTTP API

| Endpoint | Meaning |
| --- | --- |
| `GET /healthz` | Liveness; returns 200 while the endpoint responds. Used by Docker's health check. |
| `GET /status` | Last result, counters, next run; returns 503 if the last sync failed. |
| `POST /sync` | Request a sync; returns 202. Requires a Bearer token when `API_TOKEN` is set. |

Container `healthy` means the process responds, **not that the last sync succeeded**.
Disabling `LISTEN` makes the built-in health check succeed without probing.

To query from the host, add this to the service (or uncomment it in the repository example):

```yaml
ports:
  - "127.0.0.1:8080:8080"
```

```bash
curl -s http://127.0.0.1:8080/status
```

Keep the endpoint private. `API_TOKEN` protects sync requests, not status output.
No host port is required for scheduled syncs, container health checks, or `--trigger`.

## Other deployment options

### Plain Docker

Create `configs` and populate it, then replace the repository and token below:

```bash
docker run -d --name autogitsync --restart unless-stopped \
  -e GIT_REPO=https://github.com/your-name/my-configs.git \
  -e GIT_TOKEN=your-write-token \
  --mount type=bind,source="$PWD/configs",target=/source,readonly \
  --mount type=volume,source=autogitsync-data,target=/data \
  ghcr.io/jameswdgu/autogitsync:latest
```

For a preview, replace `-d --name autogitsync --restart unless-stopped` with `--rm`
and append `--dry-run`; only do this while no daemon uses the same volume.

### Use the repository's Compose example

In a source checkout:

```bash
cp .env.example .env
mkdir -p configs
# Edit .env and put your source files in ./configs.
make check
make dry-run
make up
```

Only repository credentials are interpolated from `.env`; set other application variables
in the Compose `environment` section. There is no application configuration file.
For local image builds, see [Contributing](../CONTRIBUTING.md).

### Update an existing deployment

```bash
docker compose pull
docker compose up -d
```

Review changes before upgrading when using `:latest`. Pin a version tag for controlled
updates. If adopting the new repository example, note that it uses `./configs` instead of
`./example-source`, UTC/default scheduling instead of an explicit cron/time zone, and a
named work-copy volume instead of `./data`. Keep your old mounts, filters, schedule, and
time zone if they are intentional. A new work-copy volume clones the remote again; the
old `./data` is not migrated or deleted. `docker compose down` keeps named volumes;
do not add `--volumes` unless you intend to remove them.

## Common errors

| Symptom | Check |
| --- | --- |
| Push/authentication failure | Token write permission, repository URL, branch protection, and `GIT_USERNAME`. |
| No matching files | Mount path and `INCLUDE` / `EXCLUDE`; use `--check`. |
| Some files missing | Target `.gitignore` warnings and file matching; links are not dereferenced. |
| Cannot lock data directory | Stop the daemon for offline commands, or use `--trigger` while it runs. |
| Unmanaged path conflict | A file/directory swap would delete an out-of-scope path; resolve the layout or filters. |
| First run is slow | Initial clone fetches branch history. Persist `/data` to reuse the work copy. |
| Healthy but not syncing | Read `/status` and logs; liveness alone is not a successful-sync check. |
