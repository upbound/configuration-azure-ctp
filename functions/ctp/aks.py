"""02-aks — AKS cluster (KubernetesCluster) + ProviderConfig + connection secret.

provider-azure-containerservice exposes `KubernetesCluster` for AKS. We
enable OIDC issuer + Workload Identity so the backup section can wire up
federated credentials without rebuilding the cluster. The kubeconfig is
written to a connection secret that is then referenced by a Helm
ProviderConfig so UXP can be installed onto the new cluster.
"""

from crossplane.function import resource
from crossplane.function.proto.v1 import run_function_pb2 as fnv1

from .prelude import stamp


def add_aks_resources(rsp, id_val, location, provider_config, version, nodes,
                     mgmt_policies, config):
    aks = {
        "apiVersion": "containerservice.azure.m.upbound.io/v1beta1",
        "kind": "KubernetesCluster",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "aks-cluster"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "location": location,
                "kubernetesVersion": version,
                "dnsPrefix": id_val,
                "resourceGroupNameRef": {
                    "name": id_val
                },
                "defaultNodePool": {
                    "name": "default",
                    "nodeCount": nodes.get("count", 2),
                    "vmSize": nodes.get("vmSize", "Standard_D2s_v3"),
                    "vnetSubnetIdRef": {
                        "name": f"{id_val}-aks"
                    }
                },
                "identity": {
                    "type": "SystemAssigned"
                },
                "oidcIssuerEnabled": True,
                "workloadIdentityEnabled": True,
                "networkProfile": {
                    "networkPlugin": "azure",
                    # serviceCidr and dnsServiceIp must NOT overlap the
                    # VirtualNetwork address space (10.0.0.0/16). Azure CNI
                    # defaults serviceCidr to 10.0.0.0/16, which clashes
                    # exactly with our VNet, so set both explicitly here.
                    "serviceCidr": "172.16.0.0/16",
                    "dnsServiceIp": "172.16.0.10"
                }
            },
            "writeConnectionSecretToRef": {
                "name": f"{id_val}-aks-kubeconfig"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(aks, config, azure_tags=True)
    resource.update(rsp.desired.resources["aks-cluster"], aks)

    # Helm ProviderConfig pointed at the AKS kubeconfig connection secret, so
    # the UXP Helm Release in uxp.py can target this new cluster. Mirrors the
    # pattern used by configuration-aws-eks's EKS XR.
    helm_pc = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "ProviderConfig",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "helm-provider-config",
                # ProviderConfigs have no native Ready condition — stamp
                # them ready so function-auto-ready aggregates correctly.
                "crossplane.io/ready": "True"
            }
        },
        "spec": {
            "credentials": {
                "source": "Secret",
                "secretRef": {
                    "name": f"{id_val}-aks-kubeconfig",
                    "namespace": "default",
                    "key": "kubeconfig"
                }
            }
        }
    }
    stamp(helm_pc, config)
    resource.update(rsp.desired.resources["helm-provider-config"], helm_pc)
    # ProviderConfigs have no native Ready condition. Set the function's
    # protobuf-level Ready=TRUE directly so the composite controller doesn't
    # treat them as unready. The annotation alone isn't enough — Crossplane's
    # composite aggregation reads this field from each pipeline function's
    # response.
    rsp.desired.resources["helm-provider-config"].ready = fnv1.Ready.READY_TRUE

    # Matching Kubernetes ProviderConfig so provider-kubernetes Object
    # resources (BackupConfig, RBAC, license, knative CR, runtime config, …)
    # target the new cluster.
    kubernetes_pc = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "ProviderConfig",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "kubernetes-provider-config",
                "crossplane.io/ready": "True"
            }
        },
        "spec": {
            "credentials": {
                "source": "Secret",
                "secretRef": {
                    "name": f"{id_val}-aks-kubeconfig",
                    "namespace": "default",
                    "key": "kubeconfig"
                }
            }
        }
    }
    stamp(kubernetes_pc, config)
    resource.update(rsp.desired.resources["kubernetes-provider-config"], kubernetes_pc)
    rsp.desired.resources["kubernetes-provider-config"].ready = fnv1.Ready.READY_TRUE
