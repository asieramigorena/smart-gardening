# Infrastructure

Terraform for the smart gardening server on AWS (`eu-south-2`, Spain): one EC2 instance that runs
PostgreSQL and the API in Docker, reachable only through AWS SSM Session Manager.

```
infra/
  mise.toml             # Terraform/TFLint versions + AWS_PROFILE (also used by CI)
  .tflint.hcl           # TFLint config shared by every root module
  backend.hcl.example   # template for backend.hcl (git-ignored, contains the account ID)
  bootstrap/            # S3 bucket for the Terraform state
  main/                 # everything else: server, IAM, DB password, backups bucket
```

What `main` creates:

| Resource | Name | Notes |
|---|---|---|
| EC2 instance | `smart-gardening-server` | Amazon Linux 2023, `t3.small`, 30 GB gp3 encrypted, IMDSv2 only, Docker + compose plugin, `/opt/smart-gardening` |
| Security group | `smart-gardening-server` | No inbound rules, all outbound |
| IAM role + instance profile | `smart-gardening-ec2` | SSM Session Manager, read `/smart-gardening/*` parameters, read/write the backups bucket |
| SSM parameter (SecureString) | `/smart-gardening/db/password` | Random 32-character password, URL-safe |
| S3 bucket | `smart-gardening-backups-<account_id>` | Private, SSE-S3, objects deleted after 14 days |

## Requirements

- [mise](https://mise.jdx.dev): installs the pinned Terraform and TFLint (`mise install` in `infra/`)
  and sets `AWS_PROFILE=smart-gardening` inside `infra/`. Run `mise trust` the first time.
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) with a
  profile called `smart-gardening` (see [Set up your AWS CLI profile](#1-set-up-your-aws-cli-profile)).
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
  for the AWS CLI (needed for shells and port forwarding).

## Deploying

Run everything from `infra/` (so mise sets the versions and the profile). Always read the plan
before typing `yes`.

### 1. Bootstrap (once)

The state bucket cannot hold its own state before it exists, so the first apply uses local state
and then moves it into the bucket.

```sh
cd infra/bootstrap

# Temporarily replace the S3 backend with a local one. *_override.tf files are git-ignored.
cat > backend_override.tf <<'EOF'
terraform {
  backend "local" {}
}
EOF

terraform init
terraform plan -out=tfplan
terraform apply tfplan

# Write the partial backend config used by both modules.
terraform output -raw backend_hcl > ../backend.hcl

# Move the state into the bucket (key bootstrap/terraform.tfstate). Answer "yes" to copy it.
rm backend_override.tf tfplan
terraform init -migrate-state -backend-config=../backend.hcl

# Check: no changes, and the state is in S3. Then delete the local copies.
terraform plan
aws s3 ls "s3://$(terraform output -raw tfstate_bucket)/bootstrap/"
rm -f terraform.tfstate terraform.tfstate.backup
```

On another machine, or after a fresh clone, just run
`terraform init -backend-config=../backend.hcl` in `bootstrap/`. If you do not have `backend.hcl`,
copy `backend.hcl.example` and fill in the account ID (`aws sts get-caller-identity`).

The bucket has `prevent_destroy`: Terraform refuses to delete it.

### 2. Main

```sh
cd infra/main
terraform init -backend-config=../backend.hcl
terraform plan -out=tfplan
terraform apply tfplan
terraform output
```

The outputs include ready-to-copy commands (`ssm_shell_command`, `ssm_postgres_tunnel_command`,
`db_password_command`). The instance needs 2-3 minutes after creation to install Docker and
register with SSM. Check it with:

```sh
aws ssm describe-instance-information --query 'InstanceInformationList[].[InstanceId,PingStatus]'
```

The first-boot log is in `/var/log/cloud-init-output.log` on the server.

## For the team: using the server

You do not need Terraform for any of this, only the AWS CLI v2 and the Session Manager plugin.

### 1. Set up your AWS CLI profile

Ask Andrei for an access key, then:

```sh
aws configure --profile smart-gardening
# AWS Access Key ID:     <your key id>
# AWS Secret Access Key: <your secret>
# Default region name:   eu-south-2
# Default output format: json

export AWS_PROFILE=smart-gardening   # or add --profile smart-gardening to every command
aws sts get-caller-identity          # should print your user
```

Your IAM user needs: `ssm:StartSession` on instances tagged `Project=smart-gardening` and on the
documents `SSM-SessionManagerRunShell` and `AWS-StartPortForwardingSession`,
`ssm:TerminateSession`/`ssm:ResumeSession` on your own sessions, `ssm:GetParameter` on
`/smart-gardening/*`, and `ec2:DescribeInstances` to find the instance ID.

Never commit access keys. They live only in `~/.aws/credentials`.

### 2. Find the instance ID

```sh
aws ec2 describe-instances \
  --filters Name=tag:Name,Values=smart-gardening-server Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text
```

Below, `<instance_id>` is that value (it is also the `instance_id` Terraform output).

### 3. Open a shell on the server

```sh
aws ssm start-session --target <instance_id>
```

You log in as `ssm-user`, which has `sudo`. Docker and the compose plugin are installed; put the
compose project in `/opt/smart-gardening`:

```sh
sudo -i
cd /opt/smart-gardening
docker compose up -d
```

Type `exit` to close the session.

### 4. Read the database password

```sh
aws ssm get-parameter --name /smart-gardening/db/password \
  --with-decryption --query Parameter.Value --output text
```

The same command works **on the server without any credentials**: the instance role can read
`/smart-gardening/*`. Use it to build the `.env` for compose instead of copying the password
around, e.g.:

```sh
echo "POSTGRES_PASSWORD=$(aws ssm get-parameter --name /smart-gardening/db/password \
  --with-decryption --query Parameter.Value --output text)" > /opt/smart-gardening/.env
chmod 600 /opt/smart-gardening/.env
```

The password only contains letters, digits, `-` and `_`, so it is safe inside a
`postgres://` URL. Do not change the parameter by hand: Terraform would set it back on the next
apply. To rotate it, ask for `terraform apply -replace=random_password.db`.

### 5. Connect to PostgreSQL from your laptop (port forwarding)

> **Important:** the Postgres container must publish its port **only on the server's
> loopback**: `ports: ["127.0.0.1:5432:5432"]` in `docker-compose.yml`, never `"5432:5432"`.
> The tunnel below reaches `localhost:5432` on the server, so nothing else is needed.

Open the tunnel and keep the terminal open while you work:

```sh
aws ssm start-session --target <instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5432"],"localPortNumber":["5432"]}'
```

Then connect to `localhost:5432` as if the database were local:

```sh
psql "postgresql://<db_user>:<password>@localhost:5432/<db_name>"
```

In DBeaver: new PostgreSQL connection, host `localhost`, port `5432`, your database, user and
password. If you already run Postgres on your laptop, use another local port
(`"localPortNumber":["15432"]`) and connect to `localhost:15432`.

### 6. Backups

The instance role can read and write `s3://smart-gardening-backups-<account_id>/` (exact name in the
`backups_bucket` output). Objects are deleted automatically after 14 days. From the server, no
credentials are needed:

```sh
aws s3 cp dump.sql.gz s3://<backups_bucket>/
```

Containers can use the instance role too (IMDSv2 hop limit is 2).

## Decisions

- **Default VPC.** A custom VPC with private subnets would need a NAT gateway (more expensive than
  the server) or VPC endpoints. For a one-server MVP the default VPC is enough: the instance has a
  public IP only for outbound traffic, and its security group has no inbound rules.
- **No SSH.** No key pair, no port 22, no inbound rules at all. Shells and database access go
  through SSM Session Manager, which uses IAM for authentication and needs no open ports.
- **The DB password is also in the Terraform state.** `random_password` stores its result in the
  state. The state bucket is private (public access blocked, ACLs disabled), encrypted (SSE-S3),
  versioned and TLS-only, and only project members with S3 access can read it.
- **The instance is never replaced by AMI or user_data changes.** The database lives on the
  instance's disk, so `lifecycle.ignore_changes = [ami, user_data]`. New AMIs are ignored (update
  the OS in place with `sudo dnf upgrade`), and edits to `user_data.sh` only apply to a new
  instance. Changing `availability_zone` still replaces it: do not.
- **CPU credits `standard`.** `t3` instances default to `unlimited`, which can add charges under
  sustained load. We have no AWS Budgets alerts, so the instance is capped at its baseline.
- **AMI lookup via `aws_ami`**, not the public SSM parameter, because our IAM permissions do not
  allow reading `/aws/service/*` parameters. The kernel line (6.18) is pinned so the lookup is
  deterministic.
- **One state bucket, S3 native locking** (`use_lockfile`), no DynamoDB table. Old versions of
  the state are kept for 90 days, which is plenty of time to notice and roll back a bad apply.
  Both buckets also reject plain HTTP requests, just in case.
- **Docker Compose comes straight from GitHub.** Amazon Linux 2023 doesn't package the compose
  plugin, so `user_data.sh` downloads a pinned release and checks its SHA-256 before installing
  it. Since `user_data` only runs on the first boot, bumping the version there won't touch the
  running server. To upgrade it, repeat the download by hand on the server (same commands, new
  version and checksum) and update `user_data.sh` so a future instance gets the same one.

## Editor setup (VS Code)

Not required, but it makes working on this folder a lot nicer:

- **HashiCorp Terraform** (`hashicorp.terraform`): syntax highlighting, completion for AWS
  resources and formatting. Turn on format on save so you never fail the `fmt` check in CI:

  ```json
  "[terraform]": {
    "editor.defaultFormatter": "hashicorp.terraform",
    "editor.formatOnSave": true
  }
  ```

- **mise** (`hverlin.mise-vscode`): makes VS Code use the Terraform version from `mise.toml`
  instead of whatever is installed on your system.
- **GitHub Actions** (`github.vscode-github-actions`) and **YAML** (`redhat.vscode-yaml`) for the
  workflows and, later, `docker-compose.yml`.
- **ShellCheck** (`timonwong.shellcheck`) for `user_data.sh`.

Completion only works in a module after `terraform init` has run there at least once.
