# AutoGitSync

**English** | [简体中文](README.zh-CN.md)

Sync a local directory to a Git repository on a schedule. Single container, zero
third-party dependencies (Python 3.12 + git), **configured entirely through environment
variables - no config file**.

```bash
docker run -d --restart unless-stopped \
  -e GIT_REPO=https://github.com/your-name/my-configs.git \
  -e GIT_TOKEN=ghp_xxxxxxxx \
  -v /etc/nginx:/source:ro \
  ghcr.io/jameswdgu/autogitsync:latest
```

That is all it takes: every 5 minutes the files under `/source` are pushed to the
repository, keeping their **original relative paths**.

Three rules describe the whole sync behaviour:

| Situation | Result |
| --- | --- |
| Both sides changed the same file | **local wins** |
| A file was deleted locally | it is deleted from Git too (mirror sync) |
| The remote moved while pushing | it re-fetches, replays the local files and retries - no force push, no lost remote history (unless `FORCE_PUSH_LATEST` is set) |

---

## Usage

**With docker compose (recommended):**

```bash
cp .env.example .env         # set GIT_REPO / GIT_TOKEN
# edit docker-compose.yml and point ./example-source at the directory you want to sync

make check                   # show the effective config, matched files and next 5 runs
make dry-run                 # show what would be added / changed / deleted (nothing is committed)
make up                      # start it (= docker compose up -d --build)
make logs
```

**Plain docker run** (without compose):

```bash
# replace GIT_REPO with your repository and /etc/nginx with the directory to sync
docker run -d --name autogitsync --restart unless-stopped \
  -e GIT_REPO=https://github.com/your-name/my-configs.git \
  -e GIT_TOKEN=ghp_xxxxxxxx \
  -e INCLUDE='\.(conf|ya?ml)$' \
  -v /etc/nginx:/source:ro \
  -v "$PWD/data:/data" \
  ghcr.io/jameswdgu/autogitsync:latest
```

To use a locally built image instead, run `docker build -t autogitsync:latest .` first and
replace the image above with `autogitsync:latest`.

> Mount a volume at `/data`: that is where the git work copy lives, so it does not have to
> be cloned again on every start.

## Configuration

Only `GIT_REPO` is required, everything else has a default (private repositories also need
`GIT_TOKEN`).

**Required**

| Variable | Description |
| --- | --- |
| `GIT_REPO` | Repository URL. An `https://` URL gets the token injected; local paths, `file://` and ssh URLs are used as is |

**Common**

| Variable | Default | Description |
| --- | --- | --- |
| `GIT_TOKEN` | empty | Access token, needed for private repositories |
| `SOURCE_DIR` | `/source` | Directory to sync (the path inside the container) |
| `INCLUDE` | `.*` | Only sync relative paths matching this regex, e.g. `\.conf$` |
| `SCHEDULE` | empty | Cron schedule, 5 fields, e.g. `*/5 * * * *` or `@daily` |
| `INTERVAL` | `5m` | Or a fixed interval: `30s` / `5m` / `2h`; `SCHEDULE` wins when both are set |
| `DELETE_MISSING` | `true` | Also delete files from Git when they disappeared locally |
| `TZ` | `UTC` | Time zone used to interpret the cron schedule, e.g. `Asia/Shanghai` |
| `LOG_LANG` | `en` | Language of runtime messages: `en` / `zh` (logs, errors, `--check` output) |

**Everything else (optional)**

| Variable | Default | Description |
| --- | --- | --- |
| `EXCLUDE` | empty | Exclusion regex; `.git` inside `SOURCE_DIR` is always skipped |
| `GIT_BRANCH` | `main` | Target branch, created when missing |
| `GIT_USERNAME` | `x-access-token` | Basic auth user: GitHub uses this, GitLab uses `oauth2`, Gitea accepts anything non-empty |
| `REPO_DIR` | `/data/repo` | Where the git work copy is kept |
| `RUN_ON_START` | `true` | Sync once right after startup |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LISTEN` | `0.0.0.0:8080` | Health endpoint; set to an empty string to disable it |
| `API_TOKEN` | empty | When set, `POST /sync` requires a Bearer token |
| `ALLOW_EMPTY` | `false` | See "Safety valve" below |
| `FORCE_PUSH_LATEST` | `0` | When > 0: force-push, keeping only the last N commits - see "Leaving no history behind" |
| `COMMIT_MESSAGE` | `sync: {count} file(s) changed at {time}` | Placeholders: `{changed}` `{deleted}` `{source}` `{host}` as well |
| `PUSH_RETRIES` | `3` | How often a rejected push is retried |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | `AutoGitSync` / `autogitsync@localhost` | Commit identity |

Messages are English by default; `LOG_LANG=zh` (or `zh-CN`) switches logs, error messages
and the `--check` output to Chinese.

Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`. A bad configuration (missing
directory, invalid regex, port or cron expression) makes the service exit with code 2 at
startup instead of running with broken settings.

### Common `INCLUDE` recipes

`INCLUDE` is matched against **relative paths** (like `svc-a/compose.yaml`) with
`re.search`, so anchoring the end is enough:

```regex
.*                                                     # everything (default)
\.(conf|ya?ml|env)$                                    # by extension
^[^/]+/(?:.*/)?compose\.ya?ml$                         # compose.yaml / compose.yml in subdirectories
^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$   # the same plus .env and .env.local
```

The leading `^[^/]+/` requires at least one subdirectory, so a file with the same name in
the root is not matched; `(?:.*/)?` allows any depth. Writing
`^[^/]+/.*compose\.ya?ml$` would also match `svc-a/my-compose.yaml`.

### Leaving no history behind (`FORCE_PUSH_LATEST`)

By default `DELETE_MISSING=true` removes locally deleted files from the repository, but the
**old content stays in the commit log**: `git log -p` or checking out an older commit still
reveals it. If the synced files may contain secrets, set:

```bash
-e FORCE_PUSH_LATEST=1      # the remote only ever holds the newest state
-e FORCE_PUSH_LATEST=3      # keep the last 3 runs, older history is truncated
```

After every push the branch history is rewritten down to its last N commits (the oldest
kept commit becomes a parentless root commit) and force-pushed. Measured over 5 syncs with
a `secret.env` that is deleted halfway through:

| Setting | Can `secret.env` still be found in the history? |
| --- | --- |
| `0` (default) | yes - history accumulates normally |
| `1` | no |
| `2` | yes - the last 2 states are kept, including the one before the deletion |

Keep in mind:

- the **branch must allow force pushes**; a protected branch rejects them and the error from
  git shows up in the log;
- force-pushing discards commits other people pushed to that branch - this switch is the
  extreme form of "local wins";
- hosting platforms (GitHub/GitLab) may still keep unreachable objects for a while (push
  events, access by raw SHA), so removing a secret for good still needs repository-side
  cleanup.

### Safety valve

With the default `INCLUDE=.*` the **branch content equals a snapshot of `SOURCE_DIR`**.
Narrow `INCLUDE` if you only want to manage part of it.

Also, when nothing in the mounted directory matches while the remote still holds managed
files, the run fails instead of deleting anything - so mounting the wrong directory cannot
wipe the repository. Set `ALLOW_EMPTY=true` to override that when it is really intended.

## Operations

```bash
make logs       # follow the logs
make once       # sync once immediately (also handy from a host crontab / systemd timer)
make dry-run    # show what would change
make check      # print the effective configuration
```

To query the service or trigger a sync from the host, uncomment `ports` in
`docker-compose.yml` first:

```bash
curl -s http://127.0.0.1:8080/status | jq    # status, last run, next run
curl -X POST http://127.0.0.1:8080/sync      # sync right now (202)
```

| Endpoint | Description |
| --- | --- |
| `GET /healthz` | Liveness probe, always 200 (this is what the image's `HEALTHCHECK` calls) |
| `GET /status` | Detailed status; returns 503 after a failed sync |
| `POST /sync` | Trigger a sync (needs a Bearer token when `API_TOKEN` is set) |

The endpoint listens inside the container and the built-in `HEALTHCHECK` probes it over the
container's own loopback, so **publishing a port is not required** for the service to work -
it is only needed for the three endpoints above. The health check reads `LISTEN` only, so
changing the port never makes the container report `unhealthy`. Logs go to stdout
(`docker logs`); the token is always redacted to `***` in logs and errors.

## GitHub Actions

Everything is wired up once the repository is on GitHub - no secrets to configure.

| Trigger | What happens |
| --- | --- |
| PR | run the tests, build the image and smoke-test it inside a real container; nothing is published |
| push to `main` | publish `ghcr.io/jameswdgu/autogitsync:main`, `:latest`, `:sha-xxxxxxx` |
| push tag `v1.2.3` | publish `:1.2.3` and `:1.2`, bake the version into the image and create a GitHub Release |

Images are `linux/amd64` + `linux/arm64`. `:latest` follows `main`; tagging does not move it.

```bash
docker pull ghcr.io/jameswdgu/autogitsync:latest

git tag v1.2.3 && git push origin v1.2.3     # release a new version (image + Release)
```

> GHCR packages are private by default: to let others pull without logging in, open
> repository → `Packages` → the package → `Package settings` → `Change visibility`.
>
> To mirror to Docker Hub as well, add the Variable `DOCKERHUB_USERNAME` and the Secret
> `DOCKERHUB_TOKEN` (Read & Write) under `Settings → Secrets and variables → Actions`; the
> `dockerhub` job then copies the same multi-arch image over. Without them it is skipped.

## FAQ

**Push rejected / authentication failed?** Check that the token may write to the repository
(a GitHub fine-grained PAT needs `Contents: Read and write`) and that `GIT_USERNAME` matches
your platform.

**A `.env` file is not showing up in the repository?** Look for a warning about files being
excluded by the target repository's `.gitignore` - `git add` **silently skips** ignored
files, and many templates ship a `.gitignore` containing `.env`. Remove that line from the
repository's `.gitignore`. Note also that `INCLUDE=\.env$` misses variants such as
`.env.local`; `\.env(?:\.[^/]+)?$` is safer.

**`SOURCE_DIR` contains `data/repo` (the work copy)?** The two volumes overlap on the host;
move the data volume outside the synced directory (e.g. `./configs:/source:ro` plus
`./data:/data`). The service detects the overlap, skips the work copy and warns instead of
copying it into itself - but if the container paths are literally nested (e.g.
`SOURCE_DIR=/data` and `REPO_DIR=/data/repo`), it refuses to start.

**Can it leave remote files alone?** Set `DELETE_MISSING=false` for a one-way
"local → Git" increment.

**Why is there no database and no state file that could break?** The state is the remote
branch itself: every run replays the local files on top of the latest remote commit, so the
service is stateless and can be restarted at any time.

**The first run is slow?** It clones the whole repository into `/data/repo` first. Mount a
volume at `/data` and later runs only fetch incrementally.

**Which settings are actually in effect?** `make check` (or
`docker compose run --rm --no-deps --entrypoint python autogitsync /app/main.py --check`)
prints every effective environment variable.

## Development

```
app/cron.py       5-field cron parser (aliases like @daily, day/day-of-week OR semantics)
app/git_sync.py   environment configuration + sync engine
app/main.py       scheduler loop, health endpoint, CLI
app/i18n.py       message catalog (English source strings, Chinese translations)
tests/            79 tests, using real local bare repositories - no network needed
```

```bash
make test        # runs on Python 3.9+, no dependencies
```

## License

MIT
