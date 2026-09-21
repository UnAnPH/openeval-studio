variable "aws_region" {
  type        = string
  default     = "eu-west-2"
  description = "Target AWS Region (London)."
}

variable "prefix" {
  type        = string
  default     = "openeval"
  description = "Resource name prefix."
}

variable "instance_type" {
  type        = string
  default     = "t3.micro"
  description = "EC2 instance size (t3.micro is 100% Free Tier eligible)."
}

variable "ssh_public_key_path" {
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
  description = "Path to SSH public key for EC2 login."
}

variable "db_name" {
  type        = string
  default     = "openeval"
  description = "Initial PostgreSQL database name on RDS."
}

variable "db_username" {
  type        = string
  default     = "openeval"
  description = "Master username for Amazon RDS PostgreSQL."
}

variable "db_password" {
  type        = string
  default     = null
  description = "Master password for Amazon RDS PostgreSQL. If left null, a high-entropy random password is automatically generated."
  sensitive   = true
}

variable "admin_cidr" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDR blocks allowed for SSH access to EC2 (recommend setting to your personal IP, e.g. ['203.0.113.50/32'])."
}

variable "budget_notification_email" {
  type        = string
  default     = ""
  description = "Optional email address to receive AWS Budget threshold alerts."
}
