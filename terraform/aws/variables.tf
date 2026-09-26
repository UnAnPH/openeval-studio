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
  description = "EC2 instance size (t3.micro amd64, matches GHCR image)."
}

variable "ssh_public_key_path" {
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
  description = "Path to SSH public key for EC2 login (used if admin_cidr is set)."
}

variable "admin_cidr" {
  type        = list(string)
  default     = []
  description = "CIDR blocks allowed for SSH access to EC2 (default empty [] leaves SSH closed; set to your IP e.g. ['203.0.113.50/32'] to enable)."
}

variable "budget_notification_email" {
  type        = string
  default     = ""
  description = "Optional email address to receive AWS Budget threshold alerts."
}
