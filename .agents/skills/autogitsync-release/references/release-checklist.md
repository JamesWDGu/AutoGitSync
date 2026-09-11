# Release checklist

[Back to skill](../SKILL.md)

Use this after the skill's scope and version decision. This is not a one-command
publisher: complete each gate before the next phase. All shell variables below
are task-local values, not new application configuration. Never execute a push,
tag, Release edit, or deployment operation without the corresponding authorization.

## A. Inventory and decision record

From the repository root, inspect without changing the worktree:

```bash
git status --short
git diff --stat
git diff --cached --stat
git branch --show-current
git remote get-url origin
git ls-remote origin refs/heads/main 'refs/tags/v*'
```

- Record the starting SHA and any unrelated edits; do not reset, stash, or stage them.
- Refresh relevant remote history/tags using non-destructive fetches; investigate
  conflicting local tags rather than force-updating them in the developer checkout.
- Derive the GitHub repository slug from `origin`. Check remote tags, published/draft
  Releases, and any occupied version-image tag. Authenticate only through approved
  credentials; treat 401/403 and rate limits as blockers, not proof of absence.
- Determine the latest applicable stable baseline numerically and review the full
  unreleased range, including changes already committed before this task.
- Record `NO RELEASE`, `PATCH`, `MINOR`, or `MAJOR`, the compatibility rationale,
  proposed version if any, migration impact, and authorization scope.
- For docs/skill/test/tooling/CI-only changes with no shipped behavior changes,
  normally commit/push without changing `app/main.py` or creating a version tag.
  A base image or packaging fix may affect users and must not be dismissed as CI-only.

## B. Version and local validation

Only after release approval, set the fallback in `app/main.py` to the chosen
unprefixed version. Do not hardcode the version in Docker build defaults or add a
second version file. Keep the `AUTOGITSYNC_VERSION` override working.

```bash
python3 -m unittest discover -s tests -t .
python3 -m pyflakes app tests tools
git diff --check
env -u AUTOGITSYNC_VERSION python3 app/main.py --version
AUTOGITSYNC_VERSION=release-check python3 app/main.py --version
```

Use the disposable lint environment described in the contributing guide if needed.
Test Python 3.9 compatibility and the CI interpreter versions where available;
report actual results and unavailable local interpreters, not a memorized test count.

Additional gates by changed area:

| Area | Required checks |
| --- | --- |
| Sync engine | Local-wins replay, push retries, filtered deletion, empty source, symlink boundaries, special filenames, ignored paths, history retention |
| Daemon/control | Single-instance locks including dry runs, trigger authentication, failure/startup handling, health configuration, clean shutdown |
| Memory/performance | Regression tests and comparable measurements; distinguish Python allocations, RSS, child-process memory, and image size |
| Configuration/docs | Bilingual examples, full environment/default tables, local links; preserve the short READMEs |
| Workflow | Parse YAML, run `bash -n` on every `run` block, execute changed complex logic locally using fixtures/stubs |
| Tag/notes handling | Run `tests/test_release.py`, including commit-pinned shallow fetch, annotated/lightweight tags, create/edit paths |
| Container | CI's actual image smoke test; a local Docker build only when a daemon is available |

No local Docker daemon is required for this procedure. Do not substitute an assumed
image success for CI. Keep `publish` dependent on both `test` and `smoke`. Preserve
English templates, translations, token redaction, environment-only configuration,
zero runtime Python dependencies, and the existing source/data volume semantics.

## C. Commit and main-branch gate

- Fill the [commit template](../assets/commit-message.md), including motivation,
  impact and validation. Use an allowed prefix from the skill, not an invented one.
- Stage an explicit file allowlist and inspect the entire staged diff. Exclude local
  `AGENTS.md`, `.env`, mounted source/data, private paths, credentials, and scratch files.
- Commit and push only when authorized. `main` currently builds/publishes `:main`,
  `:latest`, and a short-SHA tag even for documentation-only commits.
- For `NO RELEASE`, verify the authorized push and relevant CI, report that the
  version stayed unchanged, and stop. Do not continue into tagging.
- For a release, record the intended release SHA and wait for both `CI` and `Docker`
  for that exact SHA and main-branch event. Verify required jobs, not just run names.
- If code changes or remote main moves before tagging, stop and reassess; rerun gates
  for any newly selected SHA. Never tag an untested replacement commit.

## D. Annotated tag and tag workflow

Recheck the worktree, remote main, and version availability immediately before
creating the tag. Validate canonical stable `vMAJOR.MINOR.PATCH` syntax, including
no numeric leading zeroes. `RELEASE_SHA` must be the exact tested main commit.

Fill the [tag template](../assets/tag-message.md) in a temporary file. Replace all
placeholders and review the body: it becomes the public change summary. Include
migration requirements or explicitly state that none are needed. Preserve literal
backticks and dollar signs; use safe file writes/quoted arguments, not shell eval.

Only after those checks and explicit release authorization:

```bash
: "${TAG:?Set the reviewed stable version tag}"
: "${RELEASE_SHA:?Set the tested release commit SHA}"
: "${NOTES_FILE:?Set the reviewed tag-annotation file path}"
git ls-remote origin refs/heads/main "refs/tags/${TAG}" "refs/tags/${TAG}^{}"
```

Inspect that output and stop if the version is occupied or remote main differs.
Then create and push only the selected tag, without force:

```bash
git tag -a "$TAG" "$RELEASE_SHA" -F "$NOTES_FILE"
git push origin "refs/tags/${TAG}:refs/tags/${TAG}"
```

The tag workflow must run tests and image smoke tests before publishing, then create
the GitHub Release. Its optional Docker Hub mirror may legitimately be skipped.
Do not use an old main run or a run from another ref as evidence for the tag run.

A commit-pinned shallow checkout can turn an annotated remote tag into a lightweight
local ref. The release job explicitly fetches the remote tag object before reading
`%(contents:body)`. That local checkout repair is not permission to rewrite remote
tags. Preserve the regression coverage when changing checkout or note generation.
The workflow uses `printf` for image instructions and `--generate-notes` for the
initial generated changelog; do not assume an edit automatically preserves it.

## E. Independent verification and completion

Poll the exact SHA/ref with a bounded budget: normally one run-list request per
minute, at most ten attempts before reporting pending status. Fetch job details at
completion, not on every tick. Inspect rate-limit headers and back off early when
the remaining budget is low. Public read-only HTTP, the repository's Releases Atom
feed, and GHCR can be used when `gh` is unauthenticated; none grants write access.

Verify all of the following:

1. **Git:** remote main and tag state; distinguish the annotated tag object's SHA
   from the peeled release commit. No existing release tag was moved.
2. **CI:** required tests/static checks and image smoke/publish jobs succeeded for
   the appropriate SHA/ref. A skipped Release job on main is normal; on a stable
   tag it is not. Optional Docker Hub absence is not a GHCR release failure.
3. **GitHub Release:** exact tag, published rather than draft/prerelease, reviewed
   annotation body, generated changelog, correct image instructions and version.
   Inspect actual body text: creation success does not prove the summary is present.
4. **GHCR:** retrieve the OCI index, platform manifests, and config blobs. Confirm
   `linux/amd64` and `linux/arm64`; ignore `unknown/unknown` attestation entries when
   counting runtime platforms. Check content digests, not just tag existence.
5. **Runtime metadata:** `AUTOGITSYNC_VERSION` and
   `org.opencontainers.image.version` equal the unprefixed release version;
   `org.opencontainers.image.revision` equals the peeled release commit. Confirm
   entrypoint `python /app/main.py` and healthcheck
   `python /app/main.py --healthcheck`. When practical, compare `/app/*.py` from the
   image layer to the tagged source, not a later local checkout.
6. **Published references:** the fixed version tag is correct and any existing
   fixed-version images are unchanged. Record the digest. Check moving aliases
   separately; do not turn a concurrent alias update into a false release failure.

The expected version relationship is:

| Artifact | Expected version/revision |
| --- | --- |
| Tagged source fallback | Selected version, without `v` |
| Version image | Selected version and peeled release SHA |
| `main` image | Build metadata `main` and the main commit that produced that image |
| `latest` | Whatever main build or stable release last published it; inspect its digest |
| Minor alias | Moving alias within that minor line; inspect the current registry state |
| Local development image | Dockerfile build-argument default `dev`, unless explicitly overridden |

Use `ghcr.io/jameswdgu/autogitsync` for published-image examples. GHCR blob downloads
redirect to a CDN; use a redirect-following client (`curl -L` when using curl).
Resolve pull credentials normally and never print tokens or persist them in reports.
A later main push can move `latest` without changing an already published release.
Recommend an exact version or digest when a user needs a fixed deployment.

## F. Failure, repair, and rollback

| Failure | Narrow response |
| --- | --- |
| Main validation fails | Fix and retest; do not tag |
| Tag tests/build fail before publication | Diagnose first; rerun the same commit only for a transient failure and only after checking for partial publication |
| Fix requires changed code after a remote tag exists | Use a new commit and unused version; do not move the tag |
| Image exists but Release creation failed | Recover only the missing Release stage with authorized credentials; preserve the existing version-image digest |
| Release summary missing or incorrect | Body-only edit from the reviewed annotation; preserve tag, images, generated changelog, and unrelated metadata |
| Permission/authentication missing | Report the missing permission and ask for approved access; do not scrape credential stores or silently add privileged workflows |
| Incorrect version image or runtime behavior | Stop promotion, explain impact, and prepare a corrected new version; never overwrite the existing version image |
| Main advances after a valid release | Verify main and the fixed release against their own SHAs; a CI-only follow-up normally does not need another release |
| User requests rollback | Prefer a previously verified version/digest; get separate deployment approval and preserve source/data volumes |

Do not blindly rerun the entire tag workflow: it may rebuild from a changed base,
overwrite a version image, move aliases, or replace generated Release text. Review
partial state before any retry. A missing summary is not an application failure and
does not itself justify a new patch release.

A temporary maintenance branch/workflow is an exceptional recovery option requiring
explicit approval of its scope and permissions. Keep it narrowly scoped, use the
minimum permissions, validate the payload, and never expose CI tokens. Do not add
one-off maintenance workflows to main merely to edit an existing Release. After
verification, remove only the branch/worktree created for the task and confirm no
unexpected edits would be discarded. Do not delete or rewrite the published tag.

End with an evidence-based report: no-release/release decision, commits and push
status, version/tag if created, CI results, published image/digest and Release
status, any follow-up CI-only SHA, optional skips, blockers, and worktree cleanliness.
Do not report a queued build or a pushed tag as a completed release. Do not deploy,
delete volumes, or trigger a user's real sync as an implicit publication check.

## Standards and source of truth

- [Agent Skills specification](https://agentskills.io/specification)
- [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
- [Docker metadata-action v5 tag behavior](https://github.com/docker/metadata-action/tree/v5#latest-tag)
- [Repository publication workflow](../../../../.github/workflows/docker.yml)

Recheck the actual repository workflow and remote state on every release; these
references explain the format and policy, not a cached statement of what is latest.
