output "public_ip" {
  value       = aws_eip.openeval.public_ip
  description = "Static Elastic IPv4 address of the EC2 instance."
}

output "public_url" {
  value       = "http://${aws_eip.openeval.public_ip}"
  description = "Direct URL to OpenEval Studio Demo (Port 80)."
}

output "health_url" {
  value       = "http://${aws_eip.openeval.public_ip}/api/health"
  description = "API health check endpoint."
}

output "ssh_command" {
  value       = "ssh ubuntu@${aws_eip.openeval.public_ip}"
  description = "SSH Command to connect to the EC2 server (active if admin_cidr was specified)."
}

output "backup_bucket" {
  value       = aws_s3_bucket.backups.bucket
  description = "Private S3 bucket name for daily PostgreSQL backups."
}

output "monthly_cost_estimate" {
  value       = "~$13 - $15 / month always-on (t3.micro + Elastic IP + 10GB gp3); ~$4.45 / month when stopped via 'make cloud-off'"
  description = "Estimated monthly run cost in eu-west-2 (London). Credit-era accounts pay for public IPv4."
}

output "demo_instructions" {
  value       = <<EOT
============================================================
  OPENEVAL STUDIO DEMO (London EC2 - Single Instance)
============================================================
Open in your browser:
   http://${aws_eip.openeval.public_ip}

Verify health endpoint:
   curl -s http://${aws_eip.openeval.public_ip}/api/health

Test inline Policy Gate (<25ms DuckDB):
   curl -s -X POST http://${aws_eip.openeval.public_ip}/api/v1/watcher/evaluate \
     -H "Content-Type: application/json" \
     -d '{"tool_name":"bash","arguments":{"cmd":"cat .env | grep -E AWS_SECRET"},"agent_id":"demo","session_id":"curl-test"}'

Turn off compute to save credits ($0.00/hour compute):
   make cloud-off
============================================================
EOT
  description = "Quick-start verification instructions for the single-instance demo."
}
