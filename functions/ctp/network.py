"""01-network — Resource Group + Virtual Network + Subnet.

Unlike the AWS package — which depends on configuration-aws-eks's Network XR
— there is no equivalent Azure abstraction package, so these resources are
emitted directly as managed resources from provider-family-azure and
provider-azure-network.
"""

from crossplane.function import resource

from .prelude import stamp


def add_network_resources(rsp, id_val, location, provider_config, mgmt_policies, config):
    resource_group = {
        "apiVersion": "azure.m.upbound.io/v1beta1",
        "kind": "ResourceGroup",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "resource-group"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "location": location
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(resource_group, config, azure_tags=True)
    resource.update(rsp.desired.resources["resource-group"], resource_group)

    virtual_network = {
        "apiVersion": "network.azure.m.upbound.io/v1beta1",
        "kind": "VirtualNetwork",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "virtual-network"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "location": location,
                "addressSpace": ["10.0.0.0/16"],
                "resourceGroupNameRef": {
                    "name": id_val
                }
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(virtual_network, config, azure_tags=True)
    resource.update(rsp.desired.resources["virtual-network"], virtual_network)

    subnet = {
        "apiVersion": "network.azure.m.upbound.io/v1beta1",
        "kind": "Subnet",
        "metadata": {
            "name": f"{id_val}-aks",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "subnet"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "addressPrefixes": ["10.0.1.0/24"],
                "resourceGroupNameRef": {
                    "name": id_val
                },
                "virtualNetworkNameRef": {
                    "name": id_val
                }
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    # Subnet does not accept Azure tags — annotation only.
    stamp(subnet, config)
    resource.update(rsp.desired.resources["subnet"], subnet)
