output "public_ip" {
  value       = aws_instance.openeval.public_ip
  description = "Public IPv4 address of the EC2 demo instance."
}

output "public_url" {
  value       = "http://${aws_instance.openeval.public_ip}"
  description = "Direct URL to OpenEval Studio Demo (Port 80)."
}

output "health_url" {
  value       = "http://${aws_instance.openeval.public_ip}/api/health"
  description = "API health check endpoint."
}

output "ssh_command" {
  value       = "ssh ubuntu@${aws_instance.openeval.public_ip}"
  description = "SSH Command to connect to the EC2 server (active if admin_cidr was specified)."
}

output "monthly_cost_estimate" {
  value       = "~$12 - $16 / month (or $0 - $1 in AWS 12-month free tier); $0.00 / hour when destroyed via 'make cloud-off'"
  description = "Estimated monthly run cost in eu-west-2 (London)."
}

output "demo_instructions" {
  value       = <<EOT
============================================================
  OPENEVAL STUDIO DEMO (London EC2 - Single Instance)
============================================================
Open in your browser:
   http://${aws_instance.openeval.public_ip}

Verify health endpoint:
   curl -s http://${aws_instance.openeval.public_ip}/api/health

Test inline Policy Gate (<25ms DuckDB):
   curl -s -X POST http://${aws_instance.openeval.public_ip}/api/watcher/evaluate \
     -H "Content-Type: application/json" \
     -d '{"tool_name":"bash","arguments":{"cmd":"cat .env | grep -E AWS_SECRET"},"agent_id":"demo","session_id":"curl-test"}'

Turn off to stop all billing ($0.00/hour):
   make cloud-off
============================================================
EOT
  description = "Quick-start verification instructions for the single-instance demo."
}
