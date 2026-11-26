# Continuous Integration

CI runs on GitHub Actions for every pull request and every push to `dev` and `main`.
It only **checks** code: there is no deployment (CD), no AWS access, and no secrets other
than the automatic `GITHUB_TOKEN`, which is read-only.

## Workflows

| Workflow file | Check name | Runs when | What it does |
|---|---|---|---|
| `.github/workflows/security.yml` | `Security / gitleaks` | Always | Scans the new commits for secrets (API keys, tokens, passwords) with gitleaks. |
| `.github/workflows/python.yml` | `Python / lint` | `node/`, `ML/`, `DB/`, `tools/` or `docs/contracts/` changed | `ruff check` with real-error rules only (syntax errors, undefined names, unused imports). No style rules. |
| `.github/workflows/python.yml` | `Python / contracts` | `docs/contracts/` changed | Runs `docs/contracts/test_contracts.py` (unittest + jsonschema): the message schemas are valid draft 2020-12, valid examples pass and invalid ones fail. |
| `.github/workflows/python.yml` | `Python / ingest` | `DB/`, `tools/` or `docs/contracts/` changed | Unit tests (unittest) of the ingestion service (`DB/ingest/tests`, SQS and PostgreSQL mocked), of the simulator (`tools/simulator`) and of the end-to-end check (`tools/e2e_check`, AWS and PostgreSQL mocked). |
| `.github/workflows/python.yml` | `Python / node` | `node/` or `docs/contracts/` changed | Unit tests (unittest) of the Raspberry Pi node (`node/tests`): config, readings message against the contract, sensor failures and DHT11 decoding. No hardware and no network needed. |
| `.github/workflows/infra.yml` | `Infra / terraform` | `infra/` changed | `terraform fmt -check`, then `terraform init -backend=false`, `terraform validate` and `tflint` in each root module (`infra/bootstrap`, `infra/main`). |

`.github/dependabot.yml` opens a weekly PR against `dev` to update the actions used by the workflows.

## Required checks

Configure these as required status checks in the branch protection rules of `dev` and `main`:

- `Security / gitleaks`
- `Python / lint`
- `Python / contracts`
- `Python / ingest`
- `Python / node`
- `Infra / terraform`

All six can be required even though the Python and Infra jobs only run on some PRs (see below).

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
| jsonschema | 4.26.0 (+ rfc3339-validator 0.1.4) | `docs/contracts/requirements.txt` |
| Ingestion dependencies (boto3, psycopg, jsonschema) | see file | `DB/ingest/requirements.txt` |
| End-to-end check dependencies (same versions as the ingestion service) | see file | `tools/e2e_check/requirements.txt` |
| Node dependencies (installed with `--no-deps`, as on the Pi) | see file | `node/requirements.txt` |
| Terraform | 1.17.2 | `infra/mise.toml` |
| TFLint | 0.66.0 (+ AWS ruleset 0.50.0) | `infra/mise.toml` (ruleset: `infra/.tflint.hcl`) |
| mise | 2026.11.0 | `infra.yml` |
| gitleaks | 8.30.0 (same as the pre-commit hook) | `security.yml` |

Dependabot updates the action SHAs. The tool versions are updated by hand.

Terraform and TFLint have a single source of truth, `infra/mise.toml`: the Infra workflow installs
them with [jdx/mise-action](https://github.com/jdx/mise-action), and developers get the same
versions locally with `mise install`. To upgrade, change `infra/mise.toml` only.

**Concurrency.** A new push to a PR cancels the previous run of the same workflow. Runs on
`dev` and `main` are never cancelled, so every commit there gets a result.

**What gitleaks scans.** On a PR it scans the PR's commits; on a push, the pushed commits. It does
not rescan the full history on every run. To scan the full history, run it locally (see below).

## Running the checks locally

```sh
# Python lint (same command as CI)
pipx run --spec ruff==0.17.5 ruff check node ML DB tools docs/contracts --select E4,E7,E9,F

# Message contract test (in a virtualenv)
pip install -r docs/contracts/requirements.txt
python -m unittest discover -s docs/contracts -p "test_*.py"

# Ingestion service, simulator and end-to-end check tests (in a virtualenv)
pip install -r DB/ingest/requirements.txt -r tools/e2e_check/requirements.txt
python -m unittest discover -s DB/ingest/tests -t DB/ingest
python -m unittest discover -s tools/simulator
python -m unittest discover -s tools/e2e_check

# Node tests (in a virtualenv; --no-deps as on the Pi, see node/setup.sh)
pip install --no-deps -r node/requirements.txt
PYTHONPATH=node/src python -m unittest discover -s node/tests -t node

# Terraform (versions from infra/mise.toml; run `mise install` once)
cd infra
terraform fmt -check -recursive -diff
for module in bootstrap main; do
  terraform -chdir="$module" init -backend=false
  terraform -chdir="$module" validate
done
tflint --init
for module in bootstrap main; do tflint --chdir="$module" --config="$PWD/.tflint.hcl"; done

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
- Changing the message contract: see [`docs/contracts/README.md`](contracts/README.md). Every
  schema change needs matching valid/invalid examples, or `Python / contracts` fails.
- Adding a Terraform root module under `infra/`: add it to `MODULES` in `infra.yml`.
- Adding a new workflow: keep `permissions: contents: read`, pin actions to a SHA, and use the
  `changes` job pattern if it should only run for some paths. Validate with
  [actionlint](https://github.com/rhysd/actionlint) and [zizmor](https://github.com/zizmorcore/zizmor).
