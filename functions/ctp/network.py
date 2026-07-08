"""01-network — Virtual network via the Network XR.

Composes a single Network XR from configuration-azure-network (kind
`Network`, azure.platform.upbound.io/v1alpha1) instead of emitting raw
ResourceGroup/VirtualNetwork/Subnet managed resources. The Network XR creates
the ResourceGroup + VirtualNetwork + general Subnet, all labelled
`azure.platform.upbound.io/network-id=<id>` so the AKS XR (and this package's
own Azure MRs) can select them.
"""

from crossplane.function import resource

from .prelude import stamp


def add_network_resources(rsp, id_val, location, provider_config, mgmt_policies,
                          network_param, config):
    network = {
        "apiVersion": "azure.platform.upbound.io/v1alpha1",
        "kind": "Network",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "network"
            }
        },
        "spec": {
            "parameters": {
                "id": id_val,
                "region": location,
                "addressRange": network_param.get("addressRange", "10.0.0.0/16"),
                "generalSubnetRange": network_param.get("generalSubnetRange", "10.0.1.0/24"),
                "managementPolicies": mgmt_policies,
                "providerConfigName": provider_config
            }
        }
    }
    # XR; no forProvider.tags — the underlying composition handles Azure tags.
    stamp(network, config)
    resource.update(rsp.desired.resources["network"], network)
