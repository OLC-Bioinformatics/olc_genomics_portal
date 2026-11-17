packer {
  required_version = ">= 1.10.0"

  required_plugins {
    azure = {
      source  = "github.com/hashicorp/azure"
      version = "~> 2.6"
    }
  }
}

variable "subscription_id" {
  type = string
}

variable "location" {
  type = string
}

variable "source_image_version" {
  type = string
}

variable "image_version" {
  type = string
}

variable "resource_group" {
  type = string
}

variable "network_resource_group" {
  type = string
}

variable "virtual_network_name" {
  type = string
}

variable "subnet_name" {
  type = string
}

variable "gallery_name" {
  type = string
}

variable "image_name" {
  type = string
}

variable "build_vm_size" {
  type = string
}

source "azure-arm" "cowbat" {
  use_azure_cli_auth = true
  subscription_id    = var.subscription_id
  os_type            = "Linux"
  vm_size            = var.build_vm_size
  ssh_username       = "packer"
  ssh_timeout        = "30m"
  security_type      = "TrustedLaunch"

  shared_image_gallery {
    subscription   = var.subscription_id
    resource_group = var.resource_group
    gallery_name   = var.gallery_name
    image_name     = var.image_name
    image_version  = var.source_image_version
  }

  build_resource_group_name = var.resource_group

  virtual_network_resource_group_name    = var.network_resource_group
  virtual_network_name                   = var.virtual_network_name
  virtual_network_subnet_name            = var.subnet_name
  private_virtual_network_with_public_ip = false

  shared_image_gallery_destination {
    subscription         = var.subscription_id
    resource_group       = var.resource_group
    gallery_name         = var.gallery_name
    image_name           = var.image_name
    image_version        = var.image_version
    replication_regions  = [var.location]
    storage_account_type = "Standard_ZRS"
  }

  shared_image_gallery_timeout = "2h"
}

build {
  name = "foodport-cowbat"

  sources = [
    "source.azure-arm.cowbat"
  ]

  provisioner "file" {
    source      = "${path.root}/../../../GoC-GdC-Root-A.crt"
    destination = "/tmp/GoC-GdC-Root-A.crt"
  }

  provisioner "shell" {
    script          = "${path.root}/../scripts/provision.sh"
    execute_command = "chmod +x {{ .Path }}; sudo -E {{ .Path }}"
  }
}
