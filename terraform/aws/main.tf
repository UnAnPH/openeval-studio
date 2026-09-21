terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}

resource "random_password" "db_password" {
  length           = 24
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

locals {
  rds_password = var.db_password != null ? var.db_password : random_password.db_password.result
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
# 1. NETWORKING: VPC & Multi-AZ Subnets
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

# Public Subnet A (London eu-west-2a)
resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.prefix}-public-subnet-a"
  }
}

# Public Subnet B (London eu-west-2b - Required for Multi-AZ ALB)
resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.2.0/24"
  availability_zone       = "${var.aws_region}b"
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.prefix}-public-subnet-b"
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

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}

# Private Subnets for RDS (eu-west-2a and eu-west-2b)
resource "aws_subnet" "db_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.10.0/24"
  availability_zone = "${var.aws_region}a"

  tags = {
    Name = "${var.prefix}-db-subnet-a"
  }
}

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
  description = "Subnet group for OpenEval RDS PostgreSQL"
  subnet_ids  = [aws_subnet.db_a.id, aws_subnet.db_b.id]

  tags = {
    Name = "${var.prefix}-db-subnet-group"
  }
}

# -------------------------------------------------------------------
# 2. SECURITY GROUPS (Layered Perimeter Isolation)
# -------------------------------------------------------------------

# ALB Security Group: Inbound HTTP/HTTPS from the world
resource "aws_security_group" "alb" {
  name        = "${var.prefix}-alb-sg"
  description = "Public HTTP and HTTPS ingress for Application Load Balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP Public Ingress"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS Public Ingress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Forward to backend instances"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.20.0.0/16"]
  }

  tags = {
    Name = "${var.prefix}-alb-sg"
  }
}

# EC2 Web Security Group: Inbound strictly from ALB + SSH for admin
resource "aws_security_group" "web" {
  name        = "${var.prefix}-ec2-sg"
  description = "Allow SSH from admin and backend ports strictly from ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH Admin Access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.admin_cidr
  }

  ingress {
    description     = "Port 8000 (Live Studio) from ALB strictly"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Port 8001 (Demo Studio) from ALB strictly"
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
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

# RDS Security Group: Inbound strictly from EC2 on port 5432
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
# 3. DATABASE: Amazon RDS for PostgreSQL (Free Tier: 750 hrs/mo)
# -------------------------------------------------------------------

resource "aws_db_instance" "postgres" {
  identifier             = "${var.prefix}-postgres"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  storage_type           = "gp3"
  db_name                = var.db_name
  username               = var.db_username
  password               = local.rds_password
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
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.web.id]
  key_name               = aws_key_pair.deployer.key_name

  root_block_device {
    volume_size           = 30
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
# 5. INGRESS: AWS Application Load Balancer (ALB) - 750 hrs/mo Free
# -------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${var.prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.public_a.id, aws_subnet.public_b.id]

  tags = {
    Name = "${var.prefix}-alb"
  }
}

# Target Group: Live Studio (Port 8000)
resource "aws_lb_target_group" "live" {
  name        = "${var.prefix}-tg-live"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    path                = "/api/health"
    protocol            = "HTTP"
    port                = "8000"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = {
    Name = "${var.prefix}-tg-live"
  }
}

# Target Group: Demo Studio (Port 8001)
resource "aws_lb_target_group" "demo" {
  name        = "${var.prefix}-tg-demo"
  port        = 8001
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    path                = "/api/health"
    protocol            = "HTTP"
    port                = "8001"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = {
    Name = "${var.prefix}-tg-demo"
  }
}

# Attach EC2 to Live Target Group
resource "aws_lb_target_group_attachment" "live" {
  target_group_arn = aws_lb_target_group.live.arn
  target_id        = aws_instance.openeval.id
  port             = 8000
}

# Attach EC2 to Demo Target Group
resource "aws_lb_target_group_attachment" "demo" {
  target_group_arn = aws_lb_target_group.demo.arn
  target_id        = aws_instance.openeval.id
  port             = 8001
}

# ALB HTTP Listener (Port 80)
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  # Default Action: Route to Live Studio
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.live.arn
  }
}

# Host-Based Routing Rule: demo.openeval.studio -> Demo Target Group
resource "aws_lb_listener_rule" "demo" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.demo.arn
  }

  condition {
    host_header {
      values = ["demo.openeval.studio"]
    }
  }
}

# -------------------------------------------------------------------
# 6. FINOPS: Budget Guardrail ($5.00 Threshold Alert)
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
