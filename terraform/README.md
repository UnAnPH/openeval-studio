# Multi-Cloud Infrastructure as Code (Terraform)

Modular Terraform configurations for deploying OpenEval Studio across cloud providers:

- **[`aws/`](aws/):** Production deployment on AWS in **London (`eu-west-2`)** featuring an **AWS Application Load Balancer (ALB)** with host-based routing, **Amazon RDS for PostgreSQL** (`db.t4g.micro`), and an EC2 instance with automated 3GB swap memory and FinOps budget guardrails.
- **[`azure/`](azure/):** Alternative deployment on **Microsoft Azure** (`italynorth`) featuring a `Standard_B2s` VM with automated shutdown schedules (`azurerm_dev_test_global_vm_shutdown_schedule`) to preserve free credits.
