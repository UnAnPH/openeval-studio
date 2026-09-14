output "public_ip_address" {
  value       = azurerm_public_ip.pip.ip_address
  description = "Static Public IPv4 address of the OpenEval VM."
}

output "azure_fqdn" {
  value       = azurerm_public_ip.pip.fqdn
  description = "Default Azure Fully Qualified Domain Name."
}

output "ssh_connection_string" {
  value       = "ssh ${var.admin_username}@${azurerm_public_ip.pip.fqdn}"
  description = "SSH Command to connect to the VM."
}

output "caddy_dashboard_url" {
  value       = "https://${azurerm_public_ip.pip.fqdn}"
  description = "OpenEval Studio Web Dashboard URL."
}
