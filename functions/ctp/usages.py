"""04-usages — deletion-order Usage guards.

Base guards: the UXP Helm Release must finish uninstalling before the AKS
cluster is deleted, and the AKS cluster must be gone before the network
(VNet/Subnet/ResourceGroup, owned by the composed Network XR) is removed.
Mirrors configuration-aws-ctp: guards target the composed AKS + Network XRs,
not the underlying MRs.

Add-on guards: every child-cluster Release/Object added by an add-on (k8gb,
ArgoCD) also gets an `of: AKS, by: <resource>` guard, so it finishes
uninstalling before the AKS cluster/kubeconfig is torn out from under it
(otherwise the child Objects orphan-finalize). Emitted only when the add-on is
enabled.
"""

from crossplane.function import resource

from .prelude import stamp


def _emit_aks_usage(rsp, id_val, cr_name, by_api_version, by_kind, by_name,
                    reason, config):
    """Emit an `of: AKS, by: <resource>` Usage guarding a child-cluster
    resource against premature AKS deletion."""
    usage = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-{cr_name}",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": cr_name
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
                "apiVersion": by_api_version,
                "kind": by_kind,
                "resourceRef": {
                    "name": by_name
                }
            },
            "reason": reason,
            "replayDeletion": True
        }
    }
    stamp(usage, config)
    resource.update(rsp.desired.resources[cr_name], usage)


def add_usage_resources(rsp, id_val, config, k8gb_enabled=False,
                        argocd_enabled=False):
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

    if k8gb_enabled:
        _emit_aks_usage(
            rsp, id_val, "usage-k8gb-aks",
            "helm.m.crossplane.io/v1beta1", "Release",
            f"{id_val}-k8gb",
            "k8gb Release must finish uninstalling before the AKS cluster is deleted",
            config)
        # The observe-only CoreDNS Object also guards the AKS cluster so it does
        # not orphan-finalize when the cluster/kubeconfig is torn out first.
        _emit_aks_usage(
            rsp, id_val, "usage-k8gb-coredns-aks",
            "kubernetes.m.crossplane.io/v1alpha1", "Object",
            f"{id_val}-k8gb-coredns",
            "k8gb CoreDNS observe Object must be removed before the AKS cluster is deleted",
            config)
