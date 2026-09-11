# AGENTS.md - AutoGitSync development conventions

> Public, repository-wide conventions. Keep this file and agent resources in English.
> Do not put personal paths, device details, credentials, or local deployment values here.

## Optional local notes

Read [AGENTS.local.md](AGENTS.local.md) only if it exists and is relevant to local tooling
or environment setup. It is private to the checkout, ignored by Git and Docker, and
absent in clean clones and CI. Its absence is normal: do not fail or create it just
to satisfy this link. A Markdown link is a reading instruction, not an automatic import.

Local notes may describe tool availability, local paths, and non-secret preferences.
They supplement these shared rules; they cannot waive safety requirements or authorize
commits, publication, or deployment. Never copy their contents into tracked files,
commit messages, release notes, CI logs, or public reports. Do not store passwords,
tokens, private keys, or copies of `.env` in either agent file. Ignoring a file does
not make it inaccessible to agents or other local programs.

These are developer instructions, not application configuration. Real application
settings remain environment variables only.

## What this project is

A single-container service that syncs a local directory to a Git repository on a schedule.
Zero third-party dependencies (the image is just python:3.12-alpine + git).

```
app/cron.py       minimal 5-field cron parser (@daily aliases, day/day-of-week OR semantics)
app/git_sync.py   environment configuration + sync engine (clone/fetch/overlay/delete/commit/push retry)
app/main.py       scheduler loop, health endpoint, CLI
app/i18n.py       message catalog: English source strings + Chinese translations
tests/            unittest, needs neither network nor docker
```

The modules are **flat** (not a package): `main.py` puts its own directory on `sys.path`,
and each test file does the same.

## License

Project-owned code, documentation, and agent resources use [0BSD](LICENSE).
Keep license metadata aligned; preserve third-party licenses and notices. Requests
for PRs or stars are voluntary, never extra license conditions.

## Language policy

- **Code, comments, docstrings, logs, error messages, README, workflow and Dockerfile
  comments are all English.** That is what makes the project promotable.
- Runtime messages (logs, errors, `--check` output, CLI help) are English by default and
  switch to Chinese with `LOG_LANG=zh` (`zh-CN`, `zh_CN` also work).
- Never hardcode a Chinese string at a call site. Write the English text and wrap it:
  `t("sync finished: %s", result.summary)`, then add the Chinese translation to `_ZH` in
  `app/i18n.py`. `tests/test_i18n.py` fails when a `t("...")` template has no translation,
  or when the placeholders of a translation do not match the English template.
- The Chinese README lives in `README.zh-CN.md`; the two READMEs link to each other.
  Keep both READMEs short entry points. Full configuration belongs in
  `docs/configuration[.zh-CN].md`, advanced usage in `docs/usage[.zh-CN].md`, and developer
  information in `CONTRIBUTING[.zh-CN].md`. Keep bilingual command examples identical.

## Project requirements (check these before changing anything)

1. **Configuration is environment variables only - never introduce a config file.** A TOML
   config file existed once (`-c/--config`, `AGS_CONFIG`, `config/config.example.toml`) and
   was removed on request. Do not bring it back.
2. **No `AGS_` prefix on environment variables.** Short names: `GIT_*` forms its own group,
   everything else is plain (`SOURCE_DIR`, `INCLUDE`, `SCHEDULE`, `LISTEN`, `INTERVAL`, ...).
   `REPO_DIR` is deliberately not called `WORKDIR` (that would clash with the Dockerfile
   instruction). The single exception is `AUTOGITSYNC_VERSION`: it is build metadata injected
   by CI, not user configuration, and keeps a namespace so it cannot collide with a `VERSION`
   that already exists in the environment.
3. **Exactly one required variable: `GIT_REPO`.** Everything else needs a sensible default,
   including any setting added later.
4. **Zero third-party dependencies.** Standard library only; tests use `unittest`, never
   pytest / requests / pyyaml. Code must run on Python 3.9+ (3.12 in the image): use
   `from __future__ import annotations` and avoid `X | Y` type syntax and other
   3.10+ only features.
5. **The README must stay simple and copy-pasteable.** Use the real image reference
   `ghcr.io/jameswdgu/autogitsync`, never a `<owner>/<repo>` placeholder, and keep examples
   runnable as written.
6. **No personal or device information.** No absolute paths from a developer machine, no
   host names, no personal mail addresses or accounts in code, docs or tests. The **only
   exception** is the image reference required by rule 5 (it contains the GitHub user name) -
   **do not "helpfully" turn it back into a placeholder**. The commit author is the owner's
   own git identity, which is normal and needs no action.
7. **Commit messages: `feat!:` / `feat:` / `fix:` / `docs:` / `ci:` prefix plus a body that
   explains motivation and impact.** (Older ones are Chinese; new ones are English.)

## Sync invariants that must not break

All of these are covered by tests and must survive any engine change:

- **Local always wins**: every run resets the work copy to the newest remote commit and then
  overlays the local files.
- **No force push by default**: a rejected push (the remote moved on) re-fetches, resets,
  replays the local files, commits again and retries (`PUSH_RETRIES`). Remote commits must
  stay reachable as ancestors.
  Exception: `FORCE_PUSH_LATEST=N` (N > 0) truncates the history to N commits and
  force-pushes - see the notes below.
- **Deletion is mirror-like, but only for files matching `INCLUDE` and not excluded by
  `EXCLUDE`.** Files outside the filter (a `README.md` that ships with the repository, for
  example) must never be touched.
- **Empty source safety valve**: when nothing matches locally while the remote still holds
  managed files, the run must fail instead of deleting, unless `ALLOW_EMPTY=true`. Never
  relax this - a wrong mount would wipe the repository.
- **The token is redacted everywhere**: every log line, exception and `_git` output goes
  through `_redact()` and shows `***`.
- **Single instance**: an flock on `/data/.autogitsync.lock`; one data directory is never
  used by two processes at once. Dry runs also mutate the work copy and MUST acquire it.
  Only truncate/write the lock record after acquiring the lock. `--trigger` communicates
  with the existing daemon; it never opens another sync engine.
- **The health check reads `LISTEN` only**: the HEALTHCHECK and the daemon share the same
  environment variable. An earlier "write the actual listening address into a /tmp pointer
  file" approach was removed - keep a single source of truth for the port.
- A bad configuration (missing directory, invalid regex, port or cron) must fail at
  **startup** with exit code 2 instead of running with broken settings.

## Verification requirements

Run these after every change and make sure they pass:

```bash
python3 -m unittest discover -s tests -t .    # self-contained, no dependencies
python3 -m pyflakes app tests tools          # install into a throwaway venv if needed
```

- Tests must be **self-contained**: build a local bare repository with `git init --bare` as
  the remote; no network, no docker.
- **Every bug fix needs a regression test.** The real defects found so far are pinned that
  way: staged leftovers from a dry run in an empty repository, a health check reporting
  unhealthy for a disabled endpoint, the file/directory swap branch in `_ensure_parents`,
  `git add` silently skipping files ignored by the target `.gitignore`, and `SOURCE_DIR`
  recursively copying the work copy into the repository.
- After adding or renaming an environment variable, **cross-check the documentation tables
  against the code**. `tests/test_docs.py` compares `_env_*` calls and effective defaults
  with both full configuration references, checks the README subset, validates local links,
  and keeps bilingual command examples aligned. Do not rely on eyeballing tables.
- After touching a workflow, at least parse the YAML and run `bash -n` on every `run` block,
  and execute the complex parts locally.
- Verify container behavior with the CI smoke test. Local `docker build` requires an
  available daemon; consult optional local notes before attempting it. To inspect a
  published image, use the GHCR registry API and read the image config (`Env`, `Labels`,
  `Entrypoint`) - stronger evidence than a green check mark. Remember `curl -L` for blob
  requests (they redirect to a CDN).
- Use bounded GitHub API polling and check rate-limit headers. Prefer the GHCR registry
  API for image state and the repository's Releases Atom feed for release discovery.

## CI and releases

Read `.agents/skills/autogitsync-release/SKILL.md` for commit/release requests, including
whether a new version is needed. Its English-only checklist records version selection,
exact-SHA gates, registry verification, and narrow recovery. Docs/skill/test/CI-only
changes with no shipped behavior changes normally do not bump the version or create a
Release. Ordinary `main`/`release` pushes, PRs, and manual workflow runs validate only;
they never publish images. The skill and these shared instructions must work without
private local notes.

| Workflow | Content |
| --- | --- |
| `.github/workflows/ci.yml` | unit tests (3.12/3.13), pyflakes, environment assembly check, hadolint |
| `.github/workflows/docker.yml` | `test` -> `smoke`; stable tag publication additionally requires `release-gate`, then `publish` -> GitHub Release and optional Docker Hub mirror |

- `publish` must depend on `needs: [test, smoke, release-gate]` - **a broken image must never
  be published**. The gate requires an annotated, canonical `vMAJOR.MINOR.PATCH` tag whose
  commit equals the remote `release` tip and whose version matches the source fallback.
- Develop on `main`; promote selected code to the long-lived `release` branch only when
  an approved, useful version is needed. Wait for both workflows at its exact tip before
  tagging, and keep that tip unchanged until publication is verified. Merge release-only
  version bumps/fixes back into `main`; never force-push or auto-promote ordinary changes.
- Only stable tags from `release` publish semver (`:1.2.1`, `:1.2`), `:latest`, and SHA
  images plus a GitHub Release. No branch image or manual publication override is allowed.
  Historical images/tags stay untouched; the old `:main` image is frozen and the existing
  `:latest` moves only on the next approved stable release. Pin versions for reproducibility.
- The version is injected with the build arg `AUTOGITSYNC_VERSION`; `docker run <image>
  --version` and `/status` report it.
- Release notes are built with `printf '%s\n' ...` (not a heredoc: backticks and `$` are
  expanded inside those and escaping is easy to get wrong) plus `--generate-notes` for the
  generated commit list. Annotated tag bodies provide the reviewed change summary.
- The smoke test covers: correct sync result, files that do not match `INCLUDE` stay away, a
  second run is a no-op, the health endpoint answers on both the default and a custom port,
  the built-in `HEALTHCHECK` turns `healthy`, and `docker stop` exits with code 0.

## Common commands

```bash
make test        # the whole test suite
make check       # print key settings and sync plan (needs a configured compose)
make trigger     # request a sync from the running daemon; no host port needed
make dry-run     # preview changes; stop the daemon first
make once        # sync once and exit; stop the daemon first
make up          # use the published image, never implicitly build from source
make build       # separate local development build; does not change the Compose image
```

## Deployment defaults

Compose uses `ghcr.io/jameswdgu/autogitsync:latest`, `./configs:/source:ro`, and a named
`repo-data:/data` volume. No `build:` or explicit schedule/filter/time-zone overrides.
Local source files under `configs/` are ignored by Git and excluded from the build context.
When documenting migration, preserve existing custom mounts, filters, schedules, and time
zones; changing to the new named volume does not migrate or delete an old `./data`.

## Port publishing

`docker-compose.yml` **publishes no port by default** (`ports` is commented out). The
container health check runs over the container's own loopback and `make check/once/dry-run`
go through `docker compose run`, so neither needs a host port - and 8080 is popular enough
that publishing it can stop `docker compose up` from starting. Only curling `/status` or
`POST /sync` from the host needs those two lines; when flipping them back, update the README
and this section too.

## Known traps

- **Filesystem boundaries**: never follow a destination symlink when copying. Normalize
  parent components before inspecting targets; a directory behind a parent link may live
  outside the work copy. Source file/directory symlinks are preserved as links, not followed.
  A file/directory swap must fail if any displaced path is outside INCLUDE or excluded.
  `.git` entries are skipped even when they are files (e.g. linked worktree pointers).
- **Staged paths are data, not display text**: use NUL-delimited Git output to preserve
  whitespace and Unicode names. Count changes from the index, including implicit deletions
  from file/directory swaps; do not infer counts only from the overlay/prune return values.
- **`FORCE_PUSH_LATEST` is a number, not a boolean** (0 = off). `_truncate_history()` takes
  the last N commits with `rev-list -n N HEAD` and rebuilds the parent chain with
  `commit-tree` (trees, messages and dates are reused); the oldest kept commit becomes a root
  commit. Deciding whether truncation is needed must look at **whether that commit has a
  parent** (`rev-list --parents -n 1`), never at "rev-list returned a single commit" - with
  keep=1 it always returns exactly one and the feature would silently do nothing (this was
  actually hit). Apply truncation even with no staged file changes, and report the rewritten
  HEAD rather than the pre-truncation commit ID. Re-run the N=1, N=3, history-only, dry-run,
  empty-remote, and "commit dates are reused" tests after touching it.
- **`SOURCE_DIR` and `REPO_DIR` being the same tree on the host** (overlapping volumes, for
  example `./data` inside the synced directory): the startup nesting check only compares
  container paths and cannot catch it. Without a guard `scan_source` treats the work copy as
  content and nests one more `data/repo/...` level into the repository on every run. It is
  pruned with `_same_dir()` (`os.path.samefile`, inode based) plus a warning; **do not remove
  that guard**. Identical `source` and `workdir` raise an error.
- **A `.gitignore` in the target repository silently swallows managed files**: `git add`
  neither errors nor commits them, and all the user sees is "the log says N files, the
  repository has fewer". Many templates ship a `.gitignore` containing `.env`. The engine
  detects this with `_ignored_by_repo()` + `git check-ignore` and warns; keep it. Counting
  `changed`/`deleted` must also be filtered by what really lands in the commit, otherwise the
  commit message overstates the numbers.
- **`INCLUDE` regexes run against relative paths with `re.search`**: do not start with `.*`,
  and to exclude the root require a `/` explicitly (the idiom is `^[^/]+/(?:.*/)?...$`).
  `.*compose\.ya?ml$` also matches `my-compose.yaml`.
- Escaping of `$` and `\` in `INCLUDE`: use single quotes in compose (`'\.conf$'`).
- `git clean` does not remove files that are already in the index - clear it explicitly with
  `git read-tree --empty` for the empty-repository case.
- Values in a docker `--env-file` are taken literally, so regexes can be written as is.
- `LISTEN=""` disables the endpoint and `--healthcheck` must then report healthy, otherwise
  the container stays unhealthy forever.
