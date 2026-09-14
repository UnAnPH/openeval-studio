variable "prefix" {
  type        = string
  default     = "openeval"
  description = "Prefix for all Azure resource names."
}

variable "location" {
  type        = string
  default     = "italynorth"
  description = "Azure region to deploy into (e.g. italynorth, eastus, westeurope)."
}

variable "vm_size" {
  type        = string
  default     = "Standard_B2s"
  description = "Azure VM Size (Standard_B2s: 2 vCPU, 4GB RAM, cost ~$30/month)."
}

variable "admin_username" {
  type        = string
  default     = "azureuser"
  description = "Administrator SSH username for the VM."
}

variable "ssh_public_key_path" {
  type        = string
  default     = "~/.ssh/id_rsa.pub"
  description = "Path to your local SSH public key."
}

variable "dns_label" {
  type        = string
  default     = "openeval-studio"
  description = "Unique DNS label prefix for Azure public IP (e.g. openeval-studio.<location>.cloudapp.azure.com)."
}

variable "auto_shutdown_time" {
  type        = string
  default     = "0200"
  description = "FinOps Auto-Shutdown time (HHmm format in 24-hour clock, e.g. 0200 for 2:00 AM) to preserve $80 credit."
}

variable "timezone" {
  type        = string
  default     = "W. Europe Standard Time"
  description = "Timezone for auto-shutdown schedule."
}
