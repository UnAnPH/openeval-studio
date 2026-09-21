# Enterprise AWS Deployment Guide (London `eu-west-2`)

Complete guide to deploying OpenEval Studio to AWS using **Terraform (IaC)**, **Amazon RDS for PostgreSQL**, and **Docker Compose**, operating within the AWS Free Tier and your $100–$200 credits.

---

## 1. Architecture Topology

```mermaid
flowchart TD
    subgraph Clients["Clients"]
        PublicUser["Public Recruiter / Visitor"]
        Jayson["Your Browser (Jayson)"]
        MacIDE["Your Local Mac (Cursor / Claude Code / Antigravity)"]
    end

    subgraph AWS["AWS London Region (eu-west-2)"]
        subgraph VPC["Custom VPC (10.20.0.0/16)"]
            subgraph PublicSubnet["Public Web Subnet (10.20.1.0/24)"]
                EIP["Elastic IP (Static IPv4)"]
                EC2["EC2 Server (t3.micro - Free Tier)<br/>30GB gp3 SSD + 3GB Swapfile"]
                
                subgraph DockerStack["Docker Compose Stack"]
                    Caddy["Caddy 2 Reverse Proxy<br/>Ports 80 & 443 (Auto-TLS)"]
                    LiveApp["openeval-live (Port 8000)<br/>OPENEVAL_DEMO_SEED=0<br/>Real Trajectories & Gemini Evals"]
                    DemoApp["openeval-demo (Port 8001)<br/>OPENEVAL_DEMO_SEED=1<br/>Sanitized Curated Fixtures"]
                end
            end

            subgraph PrivateSubnets["Private DB Subnets across AZs (10.20.10.0/24 & 10.20.11.0/24)"]
                RDS[("Amazon RDS PostgreSQL<br/>db.t4g.micro (750h/mo Free Tier)<br/>Private Endpoint Port 5432")]
            end
        end
    end

    PublicUser -->|HTTPS: demo.openeval.studio| Caddy
    Caddy --> DemoApp

    Jayson -->|HTTPS: openeval.studio (Auth: jayson)| Caddy
    MacIDE -->|POST /api/watcher/evaluate (Bearer Token)| Caddy
    Caddy --> LiveApp

    LiveApp -->|VPC Internal Port 5432| RDS
```

---

## 2. Step-by-Step Deployment

### Step 1: Provision Infrastructure with Terraform
From your local terminal:
```bash
cd terraform/aws

# 1. Initialize Terraform
terraform init

# 2. Preview the plan
terraform plan

# 3. Apply infrastructure (creates VPC, Subnets, SG, RDS, EC2, Elastic IP)
terraform apply
```
*Note: Amazon RDS typically takes 5–8 minutes to initialize.*

When `terraform apply` finishes, note the outputs:
- `elastic_ip`: Your static public IPv4 address.
- `rds_endpoint`: Your internal database host.
- `rds_database_url`: Full PostgreSQL connection string.

---

### Step 2: SSH into the Provisioned EC2 Instance
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<ELASTIC_IP>
```

Verify Docker and swap are ready:
```bash
docker --version
free -h   # Should show 1GB RAM + 3GB Swap!
```

---

### Step 3: Clone the Repository & Configure `.env`
```bash
git clone https://github.com/UnAnPH/openeval-studio.git
cd openeval-studio

# Generate a secure password hash for Caddy Basic Auth:
docker run --rm caddy:2-alpine caddy hash-password --plaintext "YourPasswordHere"
# Copy the generated $2a$... hash string
```

Create your production `.env` file:
```bash
cat << 'EOF' > .env
# Domains
DOMAIN_LIVE=openeval.studio
DOMAIN_DEMO=demo.openeval.studio

# Admin Authentication for openeval.studio
ADMIN_USER=jayson
ADMIN_PASSWORD_HASH=$2a$14$PasteYourBcryptHashHere

# Security & Watcher
OPENEVAL_API_KEY=your_secret_watcher_token_12345
GEMINI_API_KEY=your_google_gemini_api_key
OPENEVAL_WATCHER_USE_LLM=1

# Amazon RDS PostgreSQL Database URL (from terraform output)
DATABASE_URL=postgresql://openeval:OpenEval2026SecurePass!@openeval-postgres.cxxxx.eu-west-2.rds.amazonaws.com:5432/openeval
EOF
```

---

### Step 4: Launch OpenEval Studio
```bash
docker compose -f docker-compose.aws.yml up -d --build
```

Verify all three containers are healthy:
```bash
docker compose -f docker-compose.aws.yml ps
```

---

### Step 5: Configure Domain DNS on Name.com
In your Name.com account:
1. Go to **DNS Management** for `openeval.studio`.
2. Add the following records:
   - **Type:** `A` | **Host:** `@` | **Answer:** `<ELASTIC_IP>`
   - **Type:** `A` | **Host:** `demo` | **Answer:** `<ELASTIC_IP>`
3. Set TTL to `300` (5 minutes).

> **Before DNS is active:** You can immediately test the ports directly over HTTP:
> - Public Demo: `http://<ELASTIC_IP>:8001`
> - Private Live Studio: `http://<ELASTIC_IP>:8000`

---

### Step 6: Connect Your Local Mac IDE to the Cloud
On your local machine, add these lines to your `~/.zshrc`:
```bash
export OPENEVAL_WATCHER_URL="https://openeval.studio/api/watcher/evaluate"
export OPENEVAL_API_KEY="your_secret_watcher_token_12345"
```
Test the connection:
```bash
python3 scripts/test_cloud_watcher.py https://openeval.studio your_secret_watcher_token_12345
```

---

## 3. Teardown / Destroy
If you ever want to completely tear down all cloud resources to ensure zero ongoing charges:
```bash
cd terraform/aws
terraform destroy
```
