subscription_id = "dcdc7934-5cce-43de-a6ed-22e2e163c2e1"
location        = "Canada Central"
image_version   = "0.0.5"

build_resource_group = "CFDC-FoodPort-Batch-rg"

network_resource_group = "CFDC-FoodPort-network-rg"
virtual_network_name   = "CFDC-FoodPort-vnet"
subnet_name            = "CFDC-FoodPort-BatchNodes-snet"

gallery_name = "development"
image_name   = "nanopore"

build_vm_size = "Standard_D4s_v5"

poresippr_repository_url    = "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git"
poresippr_repository_commit = "2108b9428c51f2335ed4cd3f0e1c417db3ba0563"
poresippr_targets_sha256    = "6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9"