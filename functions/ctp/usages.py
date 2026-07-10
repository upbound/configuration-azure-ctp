"""04-usages — deletion-order Usage guards.

The Helm Release must finish uninstalling before the AKS cluster is deleted,
and the AKS cluster must be gone before the network (VNet/Subnet/ResourceGroup,
owned by the composed Network XR) is removed. Mirrors configuration-aws-ctp:
guards target the composed AKS + Network XRs, not the underlying MRs.
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
                "apiVersion": "azure.platform.upbound.io/v1alpha1",
                "kind": "AKS",
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

    usage_aks_network = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-aks-network",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-aks-network"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "azure.platform.upbound.io/v1alpha1",
                "kind": "Network",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "azure.platform.upbound.io/v1alpha1",
                "kind": "AKS",
                "resourceRef": {
                    "name": id_val
                }
            },
            "reason": "AKS cluster must be fully deleted before the network is removed",
            "replayDeletion": True
        }
    }

    for usage in (usage_release_aks, usage_aks_network):
        stamp(usage, config)
    resource.update(rsp.desired.resources["usage-release-aks"], usage_release_aks)
    resource.update(rsp.desired.resources["usage-aks-network"], usage_aks_network)
