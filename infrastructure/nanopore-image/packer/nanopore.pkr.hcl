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

variable "image_version" {
  type = string
}

variable "build_resource_group" {
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

variable "poresippr_repository_url" {
  type = string
}

variable "poresippr_repository_commit" {
  type = string

  validation {
    condition = can(
      regex(
        "^[0-9a-f]{40}$",
        var.poresippr_repository_commit
      )
    )

    error_message = "PoreSippR commit must be a full lowercase SHA-1."
  }
}

variable "poresippr_targets_url" {
  type      = string
  sensitive = true
}

variable "poresippr_targets_sha256" {
  type = string

  validation {
    condition = can(
      regex(
        "^[0-9a-f]{64}$",
        var.poresippr_targets_sha256
      )
    )

    error_message = "PoreSippR targets checksum must be a SHA-256."
  }
}

source "azure-arm" "nanopore" {
  use_azure_cli_auth = true

  subscription_id = var.subscription_id

  os_type         = "Linux"
  image_publisher = "Canonical"
  image_offer     = "ubuntu-24_04-lts"
  image_sku       = "server"
  image_version   = "latest"

  vm_size = var.build_vm_size

  build_resource_group_name = var.build_resource_group

  virtual_network_resource_group_name = var.network_resource_group
  virtual_network_name                = var.virtual_network_name
  virtual_network_subnet_name         = var.subnet_name

  private_virtual_network_with_public_ip = false

  ssh_username = "packer"

  shared_image_gallery_destination {
    subscription         = var.subscription_id
    resource_group       = var.build_resource_group
    gallery_name         = var.gallery_name
    image_name           = var.image_name
    image_version        = var.image_version
    replication_regions  = [var.location]
    storage_account_type = "Standard_LRS"
  }
}

build {
  name = "foodport-nanopore"

  sources = [
    "source.azure-arm.nanopore"
  ]

  provisioner "file" {
    source      = "${path.root}/../files/foodport-image.json"
    destination = "/tmp/foodport-image.json"
  }

  provisioner "file" {
    source      = "${path.root}/../files/poresippr-environment.yml"
    destination = "/tmp/poresippr-environment.yml"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/provision-base.sh"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/install-nvidia-grid.sh"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/install-dorado.sh"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/install-micromamba.sh"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/install-poresippr-environment.sh"
  }

  provisioner "shell" {
    environment_vars = [
      "PORESIPPR_TARGETS_URL=${var.poresippr_targets_url}",
      "PORESIPPR_TARGETS_SHA256=${var.poresippr_targets_sha256}",
    ]

    script = "${path.root}/../scripts/install-poresippr-targets.sh"
  }

  provisioner "shell" {
    environment_vars = [
      "PORESIPPR_REPOSITORY_URL=${var.poresippr_repository_url}",
      "PORESIPPR_REPOSITORY_COMMIT=${var.poresippr_repository_commit}",
    ]

    script = "${path.root}/../scripts/install-poresippr-repository.sh"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/validate-image.sh"
  }

  provisioner "shell" {
    script = "${path.root}/../scripts/deprovision.sh"
  }

  post-processor "manifest" {
    output     = "packer-manifest.json"
    strip_path = true

    custom_data = {
      image_version = var.image_version
      image_name    = var.image_name
      environment   = "development"
    }
  }
}
