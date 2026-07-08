"""11-nodepool-observe — observe-only KubernetesClusterNodePool for vmSize
drift detection. The status section uses the observed vmSize to surface a
NodePoolVmSizeImmutable condition when the running VM size differs from the
desired one.

In Azure, the AKS default node pool's vmSize is immutable once created. The
remediation is identical in spirit to the AWS NodeGroup case: provision a
new ControlPlane with the desired vmSize and restore from backup.
"""

from crossplane.function import resource

from .prelude import stamp


def add_nodepool_observe(rsp, id_val, location, provider_config, config):
    nodepool_observe = {
        "apiVersion": "containerservice.azure.m.upbound.io/v1beta1",
        "kind": "KubernetesClusterNodePool",
        "metadata": {
            "name": f"{id_val}-nodepool-observe",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "nodepool-observe",
                "crossplane.io/external-name": "default"
            }
        },
        "spec": {
            "managementPolicies": ["Observe"],
            "forProvider": {
                # The KubernetesCluster is created inside the AKS XR and named
                # "<id>-aks" (see configuration-azure-aks). Reference it by that
                # name to resolve its Azure resource ID for the observe.
                "kubernetesClusterIdRef": {
                    "name": f"{id_val}-aks"
                }
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    # Observe-only: tags would not propagate, so annotation only.
    stamp(nodepool_observe, config)
    resource.update(rsp.desired.resources["nodepool-observe"], nodepool_observe)
