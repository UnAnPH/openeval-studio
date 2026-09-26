terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "OpenEval-Studio"
      Environment = "Production"
      ManagedBy   = "Terraform"
      CostCenter  = "FreeTier"
    }
  }
}

# -------------------------------------------------------------------
# 1. NETWORKING: Single Public Subnet in London (eu-west-2a)
# -------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.prefix}-vpc"
  }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.prefix}-igw"
  }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.prefix}-public-subnet-a"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "${var.prefix}-public-rt"
  }
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

# -------------------------------------------------------------------
# 2. SECURITY GROUP: Port 80 Open, Port 22 strictly to admin_cidr
# -------------------------------------------------------------------

resource "aws_security_group" "web" {
  name        = "${var.prefix}-ec2-sg"
  description = "OpenEval Demo: Port 80 public ingress, SSH restricted to admin_cidr"
  vpc_id      = aws_vpc.main.id

  # Public HTTP access directly to OpenEval Studio
  ingress {
    description = "HTTP Public Ingress"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Public HTTPS access directly to OpenEval Studio via Caddy
  ingress {
    description = "HTTPS Public Ingress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # SSH Admin Ingress (Only active if var.admin_cidr is configured)
  dynamic "ingress" {
    for_each = length(var.admin_cidr) > 0 ? [1] : []
    content {
      description = "SSH Admin Access"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.admin_cidr
    }
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.prefix}-ec2-sg"
  }
}

# -------------------------------------------------------------------
# 3. STORAGE & IAM: Private S3 Backups & Least-Privilege Role
# -------------------------------------------------------------------

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "backups" {
  bucket        = "${var.prefix}-backups-${random_id.bucket_suffix.hex}"
  force_destroy = false

  tags = {
    Name = "${var.prefix}-pg-backups"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "expire-backups-14-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 14
    }
  }
}

resource "aws_iam_role" "ec2_role" {
  name = "${var.prefix}-ec2-backup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "s3_backup" {
  name        = "${var.prefix}-s3-backup-policy"
  description = "Least privilege policy for PostgreSQL S3 daily backups"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.backups.arn}/postgres/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.backups.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "s3_backup" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.s3_backup.arn
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "${var.prefix}-ec2-profile"
  role = aws_iam_role.ec2_role.name
}

# -------------------------------------------------------------------
# 4. COMPUTE: Single London EC2 Instance (t3.micro amd64 + 10GB gp3)
# -------------------------------------------------------------------

resource "aws_key_pair" "deployer" {
  count      = fileexists(pathexpand(var.ssh_public_key_path)) ? 1 : 0
  key_name   = "${var.prefix}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "openeval" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public_a.id
  vpc_security_group_ids      = [aws_security_group.web.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2_profile.name
  key_name                    = length(aws_key_pair.deployer) > 0 ? aws_key_pair.deployer[0].key_name : null
  associate_public_ip_address = true

  root_block_device {
    volume_size           = 10
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/cloud-init.yaml", {
    backup_bucket = aws_s3_bucket.backups.bucket
  })

  tags = {
    Name = "${var.prefix}-server"
  }
}

# -------------------------------------------------------------------
# 4. FINOPS: Budget Guardrail ($15.00 Threshold Alert)
# -------------------------------------------------------------------

resource "aws_budgets_budget" "cost_guard" {
  count             = var.budget_notification_email != "" ? 1 : 0
  name              = "${var.prefix}-monthly-budget"
  budget_type       = "COST"
  limit_amount      = "15.0"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
