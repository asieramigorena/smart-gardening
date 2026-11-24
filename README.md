# Smart Gardening

## Development setup

Install the git hooks once after cloning (runs gitleaks and `terraform fmt` on every commit):

    pipx install pre-commit
    pre-commit install

`pipx` installs pre-commit in an isolated environment and keeps it on your `PATH`.
On recent Debian/Ubuntu, a plain `pip install` outside a virtualenv is blocked (PEP 668).

If a hook fails, the commit is aborted: remove the secret (or run `terraform fmt`) and commit again.
A secret that reaches a pushed commit stays in the git history and must be revoked, so do not
bypass the hooks with `git commit --no-verify`.

Create your local environment file in the repository root and fill in the secrets (e.g. `AEMET_KEY`):

    cp .env.example .env

`.env` holds secrets, so it is git-ignored and never committed. `.env.example` is its committed
template: it lists every variable the code needs, with public values filled in and secrets left
empty. When you add a new variable, add it to `.env.example` too.

### Raspberry Pi node

Raspberry Pi 5 + Grove Base Hat. Wiring: soil moisture on A0, light on A2, DHT11 on D5, LED on D16.

1. Add the device to `var.devices` in `infra/` and apply (creates its IoT thing and certificate).
2. On the Pi: `git clone` this repository, then `cd smart-gardening/node && ./setup.sh`.
   It installs the packages, enables I2C (reboot and run it again if asked) and creates `node/.venv`.
3. From your laptop, with the `smart-gardening` AWS profile:
   `node/provision.sh <pi-host> <device-id> <zone>` (e.g. `pi-01 bed-1`).
4. On the Pi: `./setup.sh` again. It starts the `smart-gardening-node` service, which publishes
   one reading every hour.

Check it with `./run.sh -m gardening_node.check_sensors` (sensors) and
`journalctl -u smart-gardening-node -f` (service).

## CI

Every pull request to `dev` or `main` runs these checks; all must pass before merging:

- `Security / gitleaks`: no secrets in the new commits (also catches anything that skipped the pre-commit hook)
- `Python / lint`: ruff on `node/` and `ML/` (real errors only, no style)
- `Infra / terraform`: fmt, validate and tflint on `infra/`

Dependabot opens a weekly PR to update the GitHub Actions versions; review and merge it like any other PR.

Details, design decisions and how to run the checks locally: [docs/ci.md](docs/ci.md).
