"""02-aks — AKS cluster via the AKS XR.

Composes a single AKS XR from configuration-azure-aks (kind `AKS`,
azure.platform.upbound.io/v1alpha1) instead of emitting a raw
KubernetesCluster + ProviderConfigs. The AKS composition:
  * creates the KubernetesCluster (selecting the ResourceGroup + subnet by the
    `network-id` label produced by the Network XR), with OIDC issuer +
    Workload Identity enabled,
  * writes the kubeconfig connection secret,
  * creates a Helm and a Kubernetes ProviderConfig BOTH named `<id>`, and
  * surfaces the OIDC issuer at status.aks.oidcUrl.

Because the ProviderConfigs are named `<id>`, every downstream module here
(uxp, backup, workload_identity, licensing, knative, runtime_config) keeps
referencing `providerConfigRef.name = id_val` unchanged.

The ControlPlane XRD keeps the Azure-idiomatic `nodes.vmSize`; it is mapped to
the AKS XR's `nodes.instanceType` here.
"""

from crossplane.function import resource

from .prelude import stamp


def add_aks_resources(rsp, id_val, location, provider_config, version, nodes,
                     mgmt_policies, config):
    nodes_out = {
        "count": nodes.get("count", 2),
        "instanceType": nodes.get("vmSize", "Standard_D2s_v3")
    }
    # Optional, immutable once set — only forward it when the operator asked
    # for zones so the AKS default node pool stays zone-less otherwise.
    if nodes.get("availabilityZones"):
        nodes_out["availabilityZones"] = nodes["availabilityZones"]

    aks = {
        "apiVersion": "azure.platform.upbound.io/v1alpha1",
        "kind": "AKS",
        "metadata": {
            "name": id_val,
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "aks"
            }
        },
        "spec": {
            "parameters": {
                "id": id_val,
                "region": location,
                "version": version,
                "nodes": nodes_out,
                "managementPolicies": mgmt_policies,
                "providerConfigName": provider_config
            }
        }
    }
    stamp(aks, config)
    resource.update(rsp.desired.resources["aks"], aks)
