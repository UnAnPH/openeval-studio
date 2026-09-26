# Multi-Cloud Infrastructure as Code (Terraform)

Modular Terraform configurations for deploying OpenEval Studio across cloud providers:

- **[`aws/`](aws/):** Production deployment on AWS in **London (`eu-west-2`)** featuring a single **EC2 (`t3.micro` amd64)** instance with prebuilt GHCR Docker containers, 10GB gp3 SSD, local DuckDB storage, and FinOps budget guardrails ($12–16/mo always-on; $0.00/hr when destroyed via `make cloud-off`).
- **[`azure/`](azure/):** Alternative deployment on **Microsoft Azure** (`italynorth`) featuring a `Standard_B2s` VM with automated shutdown schedules (`azurerm_dev_test_global_vm_shutdown_schedule`) to preserve free credits.
