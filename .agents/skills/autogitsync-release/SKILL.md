---
name: autogitsync-release
description: >-
  Manage AutoGitSync release decisions, semantic version selection, commits,
  pushes, annotated tags, CI gates, GHCR verification, GitHub Release notes,
  and release recovery. Use when asked to commit changes, decide whether a
  new version is needed, release a version, or repair a publication.
license: MIT
---

# AutoGitSync release workflow

This skill is a procedure, not an auto-publisher. Loading it does not authorize
commits, remote pushes, tags, release edits, or deployment changes. Respond in the
user's language; write code, commit messages, tag annotations, and release notes
in English. Do not add application dependencies or configuration files.

Resolve linked files relative to this skill directory. Run repository commands
from the repository root, three directories above the skill directory, not from a remembered
working directory. Do not embed developer paths, tokens, run IDs, commit hashes,
or the current release number in this skill.

## Read before acting

- [Contributing](../../../CONTRIBUTING.md) and local `AGENTS.md`, if present.
  `AGENTS.md` is optional, ignored, and must never be committed.
- [CI](../../../.github/workflows/ci.yml),
  [publication workflow](../../../.github/workflows/docker.yml),
  [version source](../../../app/main.py), and [Dockerfile](../../../Dockerfile).
- [Release checklist](references/release-checklist.md), including verification
  and recovery. Maintain the skill, checklist, and templates in English only.
- [Release regression tests](../../../tests/test_release.py) before changing
  annotation fetching or note generation.

Actual workflows and remote state take precedence over remembered behavior. If
this procedure conflicts with them, explain the discrepancy before proceeding.

## 1. Establish scope and authority

Distinguish these requests:

| Request | Allowed default |
| --- | --- |
| Assess whether a release is needed | Read and report only |
| Implement a change | Local edits and tests; do not infer publication permission |
| Commit changes | Commit only; push only if requested or clearly included in scope |
| Submit changes to the remote repository | Commit and push the reviewed files; no version tag |
| Commit and release a new version | Select a version, validate, commit, push, gate, tag, and verify |
| Repair release text | Update only approved text; do not rebuild or retag version images |

A release recommendation is not release authorization. Ask once when the scope is
ambiguous; do not repeatedly ask for steps already explicitly authorized. A push
to `main` currently triggers an image build/publication even for documentation-only
changes. Explain that side effect when relevant; it is not a new version release.
Never use this skill to bypass a user's approval boundary or workflow permissions.

## 2. Inspect the complete unreleased change set

Record the starting branch, worktree/index changes, remote URL, and main SHA.
Preserve unrelated edits and never stage `.env`, source data, local notes, or
credentials. Derive the GitHub repository identity from the actual remote rather
than hardcoding an account name.

Refresh remote tags and inspect both GitHub Releases and relevant registry tags
when deciding a release. A local tag list, the source fallback, or GitHub's latest
Release alone is insufficient: an existing remote tag may be reserved, unpublished,
or part of a failed release. Compare stable versions numerically, not lexically or
by creation time, and distinguish annotated tag object IDs from peeled commit SHAs.

Review all commits and changed files since the latest applicable stable release,
including earlier unreleased commits. Do not classify only the current diff or
choose a version from commit-message prefixes alone. Explain divergent histories,
maintenance branches, or inconsistent remote state instead of guessing.

## 3. Decide whether to release and how to increment

| Change since the applicable stable release | Default decision |
| --- | --- |
| Documentation, this skill, tests, benchmark tooling, or formatting only; no shipped behavior changes | NO RELEASE |
| CI or release-note repair only; no shipped artifact changes | NO RELEASE |
| Compatible correctness, safety, security, performance, or memory fix | PATCH |
| Compatible new user-facing capability or optional configuration | MINOR |
| Incompatible environment, CLI, HTTP, sync, deployment, or supported-runtime contract | MAJOR |
| Container/base-runtime/dependency change affecting the shipped artifact | Assess compatibility and risk; normally PATCH if compatible |
| Mixed changes | Highest required increment across the entire unreleased set |

For this application the public contract includes documented defaults, mounts,
ports, filters, deletion safety, authentication, supported Python versions, and
normal operational commands, not just a library API. A tiny diff can be breaking.
A memory optimization without an interface change is normally a patch, not a minor.

If `NO RELEASE`, leave the source version unchanged and create no version tag or
GitHub Release. Finish the authorized commit/push and its checks. Documentation
alone does not require a release merely to make `main` and a release SHA equal.
If the user explicitly requires a docs-only release, record the approved exception;
ask once only if intent is unclear. Do not ignore the request or invent a feature increment.

Use stable `vMAJOR.MINOR.PATCH` Git tags and unprefixed image versions. Reset PATCH
when increasing MINOR, and reset MINOR/PATCH when increasing MAJOR. Choose an unused
version greater than the applicable stable baseline, checking occupied tags,
Releases, and version images. Never reuse a partially published version blindly.
Prereleases, build-metadata versions, and maintenance-line backports need an explicit
plan: the current stable-release workflow must not be assumed to handle their
Release flags or moving image aliases safely.

State the decision before edits: release needed or not, reason, bump level, proposed
version if any, and authorized operations. Examples:

- Only add this skill: no release, no version change; commit/push if authorized.
- Reduce allocation peaks with compatible behavior: recommend a patch release.
- Add an optional control command: recommend a minor release.
- Remove an environment variable without a compatibility path: recommend a major release.

## 4. Prepare, validate, and commit

For an approved release, update only the fallback version in `app/main.py` to the
selected unprefixed version. Preserve the `AUTOGITSYNC_VERSION` override and the
Dockerfile's development default. Do not replace historical versions throughout
the repository or introduce a second authoritative version file.

Follow the checklist's local validation and exact-SHA CI gates. Use the
[commit template](assets/commit-message.md) and stage an explicit file allowlist.
The allowed subject prefixes are `feat!:`, `feat:`, `fix:`, `docs:`, and `ci:`;
include a body explaining motivation, impact, migration needs, and real validation.
If unrelated changes are already staged, stop and agree on isolation before committing;
an allowlist alone does not exclude them. No force push, automatic stash, history rewrite,
or secret/private-path disclosure is part of the normal release procedure.

For a no-release task, stop after the authorized commit/push and report that no
version was created. For a release, push the reviewed main commit first and wait
for both required workflows at that exact SHA before creating a tag.

## 5. Tag, publish, and independently verify

After successful main CI, recheck remote main, the selected version's availability,
and the local worktree. If any input changed, stop and re-evaluate. Create an
annotated tag at the tested commit using the [tag template](assets/tag-message.md),
then push only that new tag. Do not push all local tags.

The tag workflow must pass its own tests and smoke gate, publish both architectures,
and create the Release. Verify Git refs, GitHub Release content, GHCR image manifests,
per-platform config, and version/revision alignment as described in the checklist.
A successful push, a green aggregate run, or an HTTP 200 from the registry is not
sufficient evidence of a complete, correct release.

If `main` advances after the release, its image can legitimately differ from the
version image. Compare each artifact to its own expected commit, not current HEAD.
Never equate `latest` with the newest stable release without checking its digest.

## 6. Recover narrowly and report evidence

Follow the checklist's failure branches. Do not overwrite released tags/images to
fix notes, rerun an entire publishing pipeline casually, or introduce a new release
solely for a CI/documentation follow-up. Do not search unrelated credential stores
or create permission-bearing maintenance workflows without explicit approval.

Report each reached stage separately: committed, pushed, tagged, image published,
Release created, and verification complete. Include the relevant SHAs, exact version
or no-release decision, validation results, immutable image reference/digest when
released, skipped optional jobs, unresolved blockers, and worktree status.
Distinguish release code from later CI-only commits. Never claim runtime RSS savings
from Python allocation benchmarks, or image-size savings from memory-only changes.
Remove only temporary resources created for this task, after verification; do not
modify deployment volumes or run `--once` against a user's configured repository.
