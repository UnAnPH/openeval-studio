# Infrastructure as Code (Terraform)

Modular Terraform configurations for deploying OpenEval Studio on a single VM per
cloud. Both modules install Docker; the AWS cloud-init also starts the production
Compose stack (PostgreSQL 16, FastAPI app, Caddy).

- **[`aws/`](aws/):** London (`eu-west-2`), one **EC2 `t3.micro` (amd64)** with
  prebuilt GHCR images, a **10 GB gp3** root volume, **PostgreSQL 16** in a Docker
  container on that volume, nightly `pg_dump` backups to a private S3 bucket, and
  FinOps guardrails. Always-on cost is **~$13–15/month** (instance + Elastic IP +
  gp3); destroyed via `make cloud-off` it is **$0.00/hour**. Within the AWS
  12-month Free Tier the run cost is **$0–1/month**.
- **[`azure/`](azure/):** `italynorth`, a **`Standard_B2s`** VM with a scoped
  network security group and an automated daily shutdown schedule
  (`azurerm_dev_test_global_vm_shutdown_schedule`) to preserve free credits.

Commands (from the repo root):

```bash
make cloud-on       # apply AWS stack and wait for the health endpoint
make cloud-status   # resources + estimated hourly burn
make cloud-off      # destroy all AWS resources
```

Full architecture, backups, and manual Terraform steps: `docs/AWS_DEPLOYMENT.md`.
For the storage rationale (PostgreSQL 16 for the watcher store, DuckDB only for
offline `.eval` analytics), see [../TRADEOFFS.md](../TRADEOFFS.md).
