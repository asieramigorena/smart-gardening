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
