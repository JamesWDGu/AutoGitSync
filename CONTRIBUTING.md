# Contributing

[Home](README.md) · **English** | [简体中文](CONTRIBUTING.zh-CN.md)

Keep AutoGitSync small: Python 3.9+, the standard library, and Git. Configuration remains
environment-only, with `GIT_REPO` the only required variable. Do not introduce a database,
application config file, or dependency to solve a problem the existing design can handle.

## Development

```text
app/cron.py       Five-field cron parser
app/git_sync.py   Environment configuration and sync engine
app/main.py       Scheduler, control CLI, and health endpoint
app/i18n.py       English messages and Chinese translations
tests/           Self-contained unittest tests with local bare repositories
docs/            User reference and advanced usage
```

Modules are flat, not a Python package. Tests add `app` to `sys.path`.

```bash
python3 -m unittest discover -s tests -t .
```

No network, Docker, or third-party Python package is needed for the tests.
For linting, install pyflakes in a disposable development virtual environment:

```bash
python3 -m venv /tmp/autogitsync-lint
/tmp/autogitsync-lint/bin/python -m pip install pyflakes
/tmp/autogitsync-lint/bin/python -m pyflakes app tests tools
```

## Memory benchmark

```bash
python3 tools/benchmark_memory.py --files 100000
```

This uses synthetic directory inventories and Git output, without syncing real files,
accessing a remote, or requiring Docker. It measures Python allocations with `tracemalloc`,
not container RSS, native allocations, Git subprocess memory, or filesystem caches.
The isolated parser case excludes its already-created input string from the measurement.

Compare revisions in separate processes using the same interpreter and file count. Pass
`--app-dir` with another checkout's `app` directory to measure a baseline. The tests check
object lifetimes and incremental traversal rather than asserting platform-specific MiB
thresholds. Real container peaks still need measurement on a Docker host.

## Change checklist

- Preserve local-wins sync semantics, filtered deletion, empty-source protection,
  credential redaction, and single-instance locking, including dry runs.
- Add a regression test for each bug fix. Use temporary directories and local bare Git
  repositories, never personal paths or external services.
- Keep code, comments, and commit messages English. Wrap runtime messages in `t(...)`
  and add their Chinese translations in `app/i18n.py`.
- Keep both language versions of the README and reference documents aligned. The tests
  compare configuration tables with code defaults and check local documentation links.
- Keep the README an entry point, not a full manual. Put advanced details in `docs`.
- When changing workflows, parse YAML, validate every `run` block with `bash -n`, and
  exercise complex scripts locally. Container behavior is verified by the CI smoke test.

Use an English `feat:`, `feat!:`, `fix:`, `docs:`, or `ci:` commit subject and a body
explaining motivation and impact. Do not commit personal/device information or secrets.
The real published image reference is intentional and should remain copy-pasteable.

## Local image builds

The deployment example uses the published image; it does not build from source.
`make build` is a separate development action:

```bash
make build IMAGE=autogitsync:dev
```

To test that image with Compose, temporarily change the service's `image` to
`autogitsync:dev`, then run the same check/dry-run/start commands as in the README.
Do not assume `make up` or `make build` switches the configured image automatically.
Building and running containers requires a Docker daemon.

The default image includes Git but no SSH client. Custom SSH deployments must add the
client and explicitly provide credentials and verified host keys; never bake private keys
into an image.

## CI and releases

| Workflow | Responsibility |
| --- | --- |
| `.github/workflows/ci.yml` | Python tests, configuration checks, pyflakes, Dockerfile lint |
| `.github/workflows/docker.yml` | Tests → container smoke test → multi-arch publication → release |

Publication must depend on **both tests and the container smoke test**. PR builds do not
publish. Pushes to `main` update `:main`, `:latest`, and SHA tags. Pushing a new `v*` version
tag publishes version/minor tags and creates a GitHub Release after the image is published.
Stable version releases also update `:latest`; pin a version tag for reproducible deployments.

Maintainers: choose an unused version tag only after review and verification. Do not reuse
or rewrite a published tag. CI injects `AUTOGITSYNC_VERSION`; verify `--version` or the image
configuration as well as the workflow result.

GHCR publication uses the workflow's `GITHUB_TOKEN`. Package visibility and repository
permissions must allow the intended audience to pull. Optional Docker Hub mirroring uses
the repository variable `DOCKERHUB_USERNAME` and secret `DOCKERHUB_TOKEN`; without the
variable, that job is skipped.
