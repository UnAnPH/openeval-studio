terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
# 1. NETWORKING: VPC, Subnets & Routing
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

# Public Subnet for EC2 (London eu-west-2a)
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.prefix}-public-subnet"
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

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Private Subnet 1 for RDS (London eu-west-2a)
resource "aws_subnet" "db_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.10.0/24"
  availability_zone = "${var.aws_region}a"

  tags = {
    Name = "${var.prefix}-db-subnet-a"
  }
}

# Private Subnet 2 for RDS (London eu-west-2b - Multi-AZ compliant)
resource "aws_subnet" "db_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.11.0/24"
  availability_zone = "${var.aws_region}b"

  tags = {
    Name = "${var.prefix}-db-subnet-b"
  }
}

resource "aws_db_subnet_group" "rds" {
  name        = "${var.prefix}-db-subnet-group"
  description = "Subnet group for OpenEval RDS PostgreSQL instance"
  subnet_ids  = [aws_subnet.db_a.id, aws_subnet.db_b.id]

  tags = {
    Name = "${var.prefix}-db-subnet-group"
  }
}

# -------------------------------------------------------------------
# 2. SECURITY GROUPS (Least-Privilege Isolation)
# -------------------------------------------------------------------

# EC2 Web Security Group
resource "aws_security_group" "web" {
  name        = "${var.prefix}-ec2-sg"
  description = "Allow inbound SSH, HTTP, and HTTPS traffic"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP (Lets Encrypt ACME and Redirect)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS (Dashboard & Watcher Telemetry)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
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

# RDS Security Group: strictly allow 5432 from EC2 web security group only
resource "aws_security_group" "rds" {
  name        = "${var.prefix}-rds-sg"
  description = "Allow PostgreSQL inbound strictly from OpenEval EC2 instance"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }

  egress {
    description = "Allow all outbound within VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.20.0.0/16"]
  }

  tags = {
    Name = "${var.prefix}-rds-sg"
  }
}

# -------------------------------------------------------------------
# 3. DATABASE: Amazon RDS for PostgreSQL (Free Tier Eligible)
# -------------------------------------------------------------------

resource "aws_db_instance" "postgres" {
  identifier             = "${var.prefix}-postgres"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t4g.micro" # 750h/month Free Tier eligible
  allocated_storage      = 20             # 20GB gp3 storage is 100% Free Tier eligible
  storage_type           = "gp3"
  db_name                = var.db_name
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  skip_final_snapshot    = true
  deletion_protection    = false

  tags = {
    Name = "${var.prefix}-postgres-db"
  }
}

# -------------------------------------------------------------------
# 4. COMPUTE: Ubuntu 24.04 LTS EC2 + Elastic IP
# -------------------------------------------------------------------

resource "aws_key_pair" "deployer" {
  key_name   = "${var.prefix}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

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
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  key_name               = aws_key_pair.deployer.key_name

  root_block_device {
    volume_size           = 30 # 30GB is 100% Free Tier eligible
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = file("${path.module}/cloud-init.yaml")

  tags = {
    Name = "${var.prefix}-server"
  }
}

resource "aws_eip" "web" {
  instance = aws_instance.openeval.id
  domain   = "vpc"

  tags = {
    Name = "${var.prefix}-elastic-ip"
  }
}

# -------------------------------------------------------------------
# 5. FINOPS: Budget Guardrail ($5.00 Threshold)
# -------------------------------------------------------------------

resource "aws_budgets_budget" "cost_guard" {
  count             = var.budget_notification_email != "" ? 1 : 0
  name              = "${var.prefix}-monthly-budget"
  budget_type       = "COST"
  limit_amount      = "5.0"
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
