# Continuous Integration

CI runs on GitHub Actions for every pull request and every push to `dev` and `main`.
It only **checks** code: there is no deployment (CD), no AWS access, and no secrets other
than the automatic `GITHUB_TOKEN`, which is read-only.

## Workflows

| Workflow file | Check name | Runs when | What it does |
|---|---|---|---|
| `.github/workflows/security.yml` | `Security / gitleaks` | Always | Scans the new commits for secrets (API keys, tokens, passwords) with gitleaks. |
| `.github/workflows/python.yml` | `Python / lint` | `node/` or `ML/` changed | `ruff check` with real-error rules only (syntax errors, undefined names, unused imports). No style rules. |
| `.github/workflows/infra.yml` | `Infra / terraform` | `infra/` changed | `terraform fmt -check`, `terraform init -backend=false`, `terraform validate` and `tflint`. |

`.github/dependabot.yml` opens a weekly PR against `dev` to update the actions used by the workflows.

## Required checks

Configure these as required status checks in the branch protection rules of `dev` and `main`:

- `Security / gitleaks`
- `Python / lint`
- `Infra / terraform`

All three can be required even though Python and Infra only run on some PRs (see below).

## Design decisions

**No `paths` filter on the triggers.** If a workflow is skipped by `on.<event>.paths`, it never
reports a status, and a required check stays "Expected — Waiting for status" forever, which blocks
the merge. Instead, each workflow always starts, and a small `changes` job
([dorny/paths-filter](https://github.com/dorny/paths-filter)) decides whether the real job runs.
A skipped job counts as passed for required checks.
If the `changes` job itself fails, the real job runs anyway (it is only skipped when the filter
explicitly reports no changes), so a broken filter can never let a PR merge unchecked.

On pushes the filter compares against the previous commit of the same branch
(`base: ${{ github.ref }}`). Without it, it would compare against `main`, and almost every push to
`dev` would look like it changed everything.

**Least privilege.** Workflows default to `contents: read`. Only the jobs that list a PR's files or
commits also get `pull-requests: read`. Checkouts use `persist-credentials: false`, so the token
is not left in `.git/config`. gitleaks does not comment on PRs (that would need write access);
findings appear in the job log and summary, and a SARIF report is attached as an artifact
when secrets are found.

**Pinned versions.** Actions are pinned to a commit SHA (with the version in a comment) because a
tag can be moved to different code. Tools are pinned too, so a new upstream release cannot
break CI without a code change:

| Tool | Version | Where |
|---|---|---|
| ruff | 0.17.5 | `python.yml` |
| Python | 3.11 (Raspberry Pi OS Bookworm) | `python.yml` |
| Terraform | 1.17.2 | `infra.yml` |
| TFLint | v0.66.0 | `infra.yml` |
| gitleaks | 8.30.0 (same as the pre-commit hook) | `security.yml` |

Dependabot updates the action SHAs. The tool versions are updated by hand.

**Concurrency.** A new push to a PR cancels the previous run of the same workflow. Runs on
`dev` and `main` are never cancelled, so every commit there gets a result.

**What gitleaks scans.** On a PR it scans the PR's commits; on a push, the pushed commits. It does
not rescan the full history on every run. To scan the full history, run it locally (see below).

## Running the checks locally

```sh
# Python lint (same command as CI)
pipx run --spec ruff==0.17.5 ruff check node ML --select E4,E7,E9,F

# Terraform
cd infra
terraform fmt -check -recursive -diff
terraform init -backend=false
terraform validate
tflint --init && tflint --recursive

# Secrets: staged changes are checked by the pre-commit hook on every commit.
# Full history:
gitleaks git -v .
```

To run the workflows themselves, use [act](https://github.com/nektos/act) (needs Docker), e.g.
`act push -W .github/workflows/python.yml`. Events that rely on the GitHub API (`pull_request`) are
not fully reproducible with act.

## Changing CI

- Adding a Python rule: add it to `--select` only after the existing code passes it, or fix the
  code in the same PR.
- Adding a path to a filter: edit the `filters` block of the `changes` job, not `on:`.
- Adding a new workflow: keep `permissions: contents: read`, pin actions to a SHA, and use the
  `changes` job pattern if it should only run for some paths. Validate with
  [actionlint](https://github.com/rhysd/actionlint) and [zizmor](https://github.com/zizmorcore/zizmor).
