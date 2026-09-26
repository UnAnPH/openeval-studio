# AWS Production Deployment Guide (London `eu-west-2`)

Complete guide to deploying OpenEval Studio to AWS using a lean, cost-optimized single-instance architecture on **Amazon EC2 (t3.micro amd64)** with automated **Caddy TLS**, an internal **PostgreSQL 16** Docker container, and scheduled **Amazon S3** backups.

Always-on cost: **~$13–15 / month** (or **$0–1 / month** within the AWS 12-month Free Tier). When destroyed via `make cloud-off`, the burn rate is **$0.00 / hour**.

---

## 1. Architecture Topology

```mermaid
flowchart TD
    subgraph Clients["Clients"]
        Visitor["Public Visitor / Recruiter (/demo)"]
        Owner["Authenticated Studio User (/)"]
        AgentHooks["IDE Agent Gate Hooks (Bearer oe_live_...)"]
    end

    subgraph AWSCloud["AWS London Region (eu-west-2)"]
        subgraph S3Service["Amazon S3 (Private Backup Bucket)"]
            S3Backups[("Postgres Dumps<br/>postgres/YYYY-MM-DD.sql.gz<br/>14-day Lifecycle Expiration")]
        end

        subgraph VPC["VPC (10.20.0.0/16)"]
            subgraph PublicSubnet["Public Subnet (eu-west-2a)"]
                EC2["Amazon EC2 (t3.micro amd64)<br/>10GB gp3 SSD + 2GB Swap<br/>IAM Instance Profile: S3 Backup Writer"]

                subgraph SecurityGroup["Security Group Perimeter"]
                    Port443["Port 443 (HTTPS) -> 0.0.0.0/0"]
                    Port80["Port 80 (HTTP) -> 0.0.0.0/0"]
                    Port22["Port 22 (SSH) -> admin_cidr only"]
                end

                subgraph DockerNetwork["Internal Bridge Network (openeval-net)"]
                    Caddy["openeval-caddy (Caddy 2)<br/>Ports 80/443 -> Auto Let's Encrypt TLS"]
                    StudioApp["openeval-studio (Port 8000 internal)<br/>FastAPI + Alembic + React 19 UI"]
                    PostgresDB[("openeval-postgres (Postgres 16)<br/>Port 5432 internal only<br/>Mounted: /var/lib/openeval/pg")]
                end

                EC2 --- SecurityGroup
                SecurityGroup --- Caddy
                Caddy -->|reverse_proxy| StudioApp
                StudioApp -->|SQLAlchemy 2| PostgresDB
                EC2 -.->|pg_dump_to_s3.sh @ 03:15 UTC| S3Backups
            end
        end
    end

    Visitor -->|HTTPS /demo| Port443
    Owner -->|HTTPS / (Session Cookie)| Port443
    AgentHooks -->|POST /api/watcher/evaluate| Port443
```

### Architectural Principles
1. **Single EC2 Instance & Automatic HTTPS:** Caddy serves ports 80 and 443 with automated Let's Encrypt certificates. No Application Load Balancer (ALB) or external CDN required, eliminating ~$24/mo in idle load balancer fees.
2. **Internal PostgreSQL 16 on Instance Disk:** PostgreSQL runs inside Docker on the internal bridge network `openeval-net`. Port 5432 is strictly internal and never exposed to the host or public internet. Data is persisted to `/var/lib/openeval/pg` on the gp3 root disk.
3. **Automated S3 Backups & 14-Day Expiration:** A systemd timer triggers `scripts/pg_dump_to_s3.sh` daily at 03:15 UTC. Gzipped dumps stream directly to a private S3 bucket using EC2 IAM instance profile credentials (no static access keys on the instance). Old archives expire automatically after 14 days.
4. **Prebuilt Release Artifacts:** Deployments pull precompiled images (`ghcr.io/unanph/openeval-studio:prod-*`) built by GitHub Actions CI. No code compilation occurs on the EC2 host.
5. **Multi-Tenant Per-User Isolation:** Each evaluation and review row is indexed by `user_id`. Navigating to `/demo` provides unauthenticated read-only access to curated agent fixtures; logged-in users access their private studios and manage their own agent sessions.

> [!IMPORTANT]
> **One-Time GHCR Package Visibility Requirement:**
> On the first push to GitHub Container Registry (`ghcr.io/unanph/openeval-studio`), GitHub defaults new package visibility to **Private**.
> To allow the EC2 instance to pull images without authentication, navigate to your GitHub Profile/Org -> **Packages** -> `openeval-studio` -> **Package settings** -> **Change visibility** -> set to **Public**.
> Alternatively, configure a Personal Access Token (`GHCR_PULL_TOKEN` with `read:packages` scope) on the EC2 host via `echo $GHCR_PULL_TOKEN | docker login ghcr.io -u <username> --password-stdin`.

---

## 2. Cost Analysis (London `eu-west-2`)

| Resource | Configuration | Monthly Cost (Post-Free Tier) | AWS Free Tier (First 12 Mo) |
| :--- | :--- | :--- | :--- |
| **Compute** | EC2 `t3.micro` (amd64, 2 vCPU, 1GB RAM) | ~$8.60 | **$0.00** (750 hrs/mo free) |
| **Public IPv4** | 1 In-use auto-assigned Public IPv4 | ~$3.65 | **$0.00** (covered under free tier allowance) |
| **Storage** | 10 GB gp3 Root SSD volume | ~$0.96 | **$0.00** (up to 30GB free) |
| **Backups** | Amazon S3 Standard (~100MB gzipped dumps) | ~$0.05 | **$0.00** (up to 5GB free) |
| **Total Always-On** | | **~$13 – $15 / mo** | **~$0.00 – $1.00 / mo** |
| **Teardown (`make cloud-off`)** | All resources destroyed | **$0.00 / mo** | **$0.00 / mo** |

*Eliminated from prior architecture:* Application Load Balancer (~$16.40/mo + ~$7.30 for 2 public IPs), Amazon RDS db.t4g.micro (~$11.60/mo + storage), secondary public and private subnets, 30GB disk.

> [!WARNING]
> **Instance-Only Boot Secrets (`pg_password` & `session_secret`):**
> On first boot, cloud-init securely generates `/var/lib/openeval/pg_password` (24 bytes hex) and `/var/lib/openeval/session_secret` (32 bytes hex) on the EC2 instance root disk. These secrets live **only on the instance disk** and are never committed to version control.
> If an instance is replaced or terminated, restoring database operations requires either:
> 1. Restoring the S3 database dump alongside the original `/var/lib/openeval/pg_password` and `/var/lib/openeval/session_secret`, OR
> 2. Booting with fresh randomly-generated secrets and updating the restored Postgres database credentials to match.

---

## 3. Quickstart: Managing with the Cloud Power Switch

The repository includes `scripts/cloud_switch.py` (wrapped by `make` and `cloud.sh`):

```bash
# 1. Turn ON the cloud instance (~2-3 minutes)
make cloud-on

# 2. Check live AWS status and estimated burn rate
make cloud-status

# 3. Turn OFF and destroy all resources when finished ($0.00/hr clean slate)
make cloud-off
```

When `make cloud-on` finishes, it automatically polls `http://<EC2_PUBLIC_IP>/api/health` until HTTP 200 is confirmed (checking `body["db"] == "connected"` and `body["status"] == "ok"`) and prints the direct URL.

---

## 4. Manual Deployment via Terraform

If you prefer to invoke Terraform directly:

```bash
cd terraform/aws

# 1. Initialize Terraform
terraform init

# 2. Preview the plan
terraform plan

# 3. Apply infrastructure
# To enable SSH from your IP, pass -var='admin_cidr=["YOUR_IP/32"]'
terraform apply -auto-approve
```

### Outputs
- `public_url`: Direct HTTP/HTTPS URL (`https://openeval.studio` or `http://<public-ip>`).
- `public_ip`: Public IPv4 address.
- `health_url`: Endpoint to probe readiness (`http://<public-ip>/api/health`).
- `backup_bucket`: Dedicated S3 backup bucket name.
- `ssh_command`: SSH login command (if `admin_cidr` was supplied).

---

## 5. Backup & Restore Runbook

### Scheduled Backups
Backups run automatically every day at **03:15 UTC** via systemd service `openeval-backup.service`.
The script `scripts/pg_dump_to_s3.sh`:
1. Verifies PostgreSQL readiness (`pg_isready`) before executing dump.
2. Executes `pg_dump` inside the `openeval-postgres` container.
3. Compresses the SQL stream using `gzip`.
4. Uploads the compressed archive to `s3://${BACKUP_BUCKET}/postgres/YYYY-MM-DD.sql.gz`.

### Manual Backup
To trigger an immediate backup on the EC2 host:
```bash
sudo systemctl start openeval-backup.service
sudo journalctl -u openeval-backup.service --no-pager
```

### Database Restore Procedure
To restore the database from an existing S3 archive:
```bash
# SSH into EC2 instance
ssh ubuntu@<EC2_PUBLIC_IP>

# Run the restore script with the target S3 archive URI
bash /home/ubuntu/openeval-studio/scripts/pg_restore_from_s3.sh s3://openeval-backups-<account-id>/postgres/2026-09-26.sql.gz
```
The restore script will:
1. Request interactive confirmation before touching data.
2. Stop the application container (`openeval-studio`).
3. Restore the gzipped SQL dump into `openeval-postgres`.
4. Restart the application container and verify health probe.

---

## 6. Verification & Health Probes

### 1. Verify Health Probe
```bash
curl -s http://<EC2_PUBLIC_IP>/api/health | jq .
```
Expected output:
```json
{
  "status": "ok",
  "db": "connected",
  "version": "0.1.0",
  "demo_seed": true
}
```

### 2. Verify Multi-Tenant Policy Gate Evaluation
```bash
curl -s -X POST http://<EC2_PUBLIC_IP>/api/watcher/evaluate \
  -H "Authorization: Bearer <USER_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"bash","tool_input":"cat .env | grep -E AWS_SECRET","agent_id":"demo","session_id":"curl-eval-1"}' | jq .
```
Response will immediately return `decision: "deny"` or `escalate` evaluated against deterministic policy rules in <25ms.
