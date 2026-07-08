"""04-usages — deletion-order Usage guards.

Helm Release must finish uninstalling before the AKS cluster is deleted, and
the AKS cluster must be gone before the VNet/Subnet/ResourceGroup are
removed.
"""

from crossplane.function import resource

from .prelude import stamp


def add_usage_resources(rsp, id_val, config):
    usage_release_aks = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-release-aks",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-release-aks"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "containerservice.azure.m.upbound.io/v1beta1",
                "kind": "KubernetesCluster",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "helm.m.crossplane.io/v1beta1",
                "kind": "Release",
                "resourceRef": {
                    "name": f"{id_val}-uxp"
                }
            },
            "reason": "UXP Helm Release must finish uninstalling before the AKS cluster is deleted",
            "replayDeletion": True
        }
    }

    usage_aks_subnet = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-aks-subnet",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-aks-subnet"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "network.azure.m.upbound.io/v1beta1",
                "kind": "Subnet",
                "resourceRef": {
                    "name": f"{id_val}-aks",
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "containerservice.azure.m.upbound.io/v1beta1",
                "kind": "KubernetesCluster",
                "resourceRef": {
                    "name": id_val
                }
            },
            "reason": "AKS cluster must be fully deleted before the subnet is removed",
            "replayDeletion": True
        }
    }

    usage_subnet_vnet = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-subnet-vnet",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-subnet-vnet"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "network.azure.m.upbound.io/v1beta1",
                "kind": "VirtualNetwork",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "network.azure.m.upbound.io/v1beta1",
                "kind": "Subnet",
                "resourceRef": {
                    "name": f"{id_val}-aks"
                }
            },
            "reason": "Subnet must be fully deleted before the virtual network is removed",
            "replayDeletion": True
        }
    }

    usage_vnet_rg = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-vnet-rg",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-vnet-rg"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "azure.m.upbound.io/v1beta1",
                "kind": "ResourceGroup",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "network.azure.m.upbound.io/v1beta1",
                "kind": "VirtualNetwork",
                "resourceRef": {
                    "name": id_val
                }
            },
            "reason": "Virtual network must be fully deleted before the resource group is removed",
            "replayDeletion": True
        }
    }

    for usage in (usage_release_aks, usage_aks_subnet, usage_subnet_vnet, usage_vnet_rg):
        stamp(usage, config)
    resource.update(rsp.desired.resources["usage-release-aks"], usage_release_aks)
    resource.update(rsp.desired.resources["usage-aks-subnet"], usage_aks_subnet)
    resource.update(rsp.desired.resources["usage-subnet-vnet"], usage_subnet_vnet)
    resource.update(rsp.desired.resources["usage-vnet-rg"], usage_vnet_rg)
