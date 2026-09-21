output "elastic_ip" {
  value       = aws_eip.web.public_ip
  description = "Static Public Elastic IPv4 address of the OpenEval EC2 instance."
}

output "ec2_ssh_command" {
  value       = "ssh ubuntu@${aws_eip.web.public_ip}"
  description = "SSH Command to connect to the EC2 server."
}

output "rds_endpoint" {
  value       = aws_db_instance.postgres.endpoint
  description = "Internal private RDS PostgreSQL endpoint (accessible from EC2)."
}

output "rds_database_url" {
  value       = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/${var.db_name}"
  description = "Connection string for OpenEval Studio backend."
  sensitive   = true
}

output "dns_records_instructions" {
  value       = <<EOT
============================================================
  DNS CONFIGURATION FOR NAME.COM (Once Domain is Claimed)
============================================================
1. In your Name.com DNS Management Console, add:
   - Type: A Record  | Host: @    | Answer: ${aws_eip.web.public_ip}
   - Type: A Record  | Host: demo | Answer: ${aws_eip.web.public_ip}

Before DNS is claimed, you can access your deployment directly at:
   - Public Demo:   http://${aws_eip.web.public_ip}:8001
   - Live Studio:   http://${aws_eip.web.public_ip}:8000
============================================================
EOT
  description = "Instructions for pointing openeval.studio and demo.openeval.studio."
}
