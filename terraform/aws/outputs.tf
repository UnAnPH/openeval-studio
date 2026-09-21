output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "Public DNS Name of the AWS Application Load Balancer."
}

output "alb_preview_url" {
  value       = "http://${aws_lb.main.dns_name}"
  description = "Direct URL to preview the Application Load Balancer right away in your browser."
}

output "ec2_public_ip" {
  value       = aws_eip.web.public_ip
  description = "Elastic IP of the EC2 instance (for SSH access)."
}

output "ec2_ssh_command" {
  value       = "ssh ubuntu@${aws_eip.web.public_ip}"
  description = "SSH Command to connect to the EC2 server."
}

output "rds_endpoint" {
  value       = aws_db_instance.postgres.endpoint
  description = "Internal private RDS PostgreSQL endpoint."
}

output "rds_database_url" {
  value       = "postgresql://${var.db_username}:${local.rds_password}@${aws_db_instance.postgres.endpoint}/${var.db_name}"
  description = "PostgreSQL connection string for the backend."
  sensitive   = true
}

output "dns_records_instructions" {
  value       = <<EOT
============================================================
  DNS CONFIGURATION FOR NAME.COM (Once Domain is Claimed)
============================================================
In your Name.com DNS Management Console, add:
   - Type: CNAME  | Host: @    | Answer: ${aws_lb.main.dns_name}
   - Type: CNAME  | Host: demo | Answer: ${aws_lb.main.dns_name}

Before DNS is active, you can preview the deployment directly at:
   - Live Studio (Default):  http://${aws_lb.main.dns_name}
   - EC2 Direct Live:        http://${aws_eip.web.public_ip}:8000
   - EC2 Direct Demo:        http://${aws_eip.web.public_ip}:8001
============================================================
EOT
  description = "Instructions for pointing openeval.studio and demo.openeval.studio."
}
