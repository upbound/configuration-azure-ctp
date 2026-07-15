"""06-workload-identity — UserAssignedIdentity + FederatedIdentityCredential +
RoleAssignment + ServiceAccount annotation + controller restart + optional
Restore-from-backup.

Azure analog of IRSA. Gated by the caller on backup.enabled == "yes", OIDC
issuer URL present, and UXP deployed — see compose() in main.py.

Where AWS IRSA uses an OIDC Provider + IAM Role with a federated trust
policy + a Policy + a RolePolicyAttachment, Azure Workload Identity uses:

  UserAssignedIdentity        — the identity the SA will impersonate
  FederatedIdentityCredential — binds the AKS OIDC issuer + SA subject to
                                that identity
  RoleAssignment              — grants Storage Blob Data Contributor on the
                                container so the backup controller can read
                                and write blobs

The SA is then annotated with `azure.workload.identity/client-id` and the
controller deployment is rolled to pick up the new token projection.
"""

from crossplane.function import resource

from .prelude import parse_blob_location, stamp


def add_workload_identity_resources(rsp, id_val, location, provider_config,
                                   oidc_issuer_url, storage_account,
                                   container_name, observed, install_from,
                                   client_id, principal_id,
                                   storage_account_id, config):
    # UserAssignedIdentity — the principal the backup controller SA will
    # federate into. provider-azure-managedidentity exposes
    # status.atProvider.clientId once Azure assigns one.
    identity = {
        "apiVersion": "managedidentity.azure.m.upbound.io/v1beta1",
        "kind": "UserAssignedIdentity",
        "metadata": {
            "name": f"{id_val}-backup-identity",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-identity"
            }
        },
        "spec": {
            "forProvider": {
                "name": f"{id_val}-backup-identity",
                "location": location,
                "resourceGroupNameSelector": {
                    "matchLabels": {
                        "azure.platform.upbound.io/network-id": id_val
                    }
                }
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(identity, config, azure_tags=True)
    resource.update(rsp.desired.resources["backup-identity"], identity)

    # Federated credential binds the AKS OIDC issuer to the
    # crossplane-system/upbound-controller-manager service account on the
    # workload-identity audience.
    federated = {
        "apiVersion": "managedidentity.azure.m.upbound.io/v1beta1",
        "kind": "FederatedIdentityCredential",
        "metadata": {
            "name": f"{id_val}-backup-federation",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-federation"
            }
        },
        "spec": {
            "forProvider": {
                "resourceGroupNameSelector": {
                    "matchLabels": {
                        "azure.platform.upbound.io/network-id": id_val
                    }
                },
                "parentIdRef": {
                    "name": f"{id_val}-backup-identity"
                },
                "audience": ["api://AzureADTokenExchange"],
                "issuer": oidc_issuer_url,
                "subject": "system:serviceaccount:crossplane-system:upbound-controller-manager"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ProviderConfig"
            }
        }
    }
    # FederatedIdentityCredential does not accept Azure tags.
    stamp(federated, config)
    resource.update(rsp.desired.resources["backup-federation"], federated)

    # RoleAssignment — grant Storage Blob Data Contributor on the storage
    # account so the backup controller can list/read/write blobs.
    #
    # provider-azure-authorization's v2 namespaced RoleAssignment dropped
    # the principalIdRef and scopeRef resolvers — only the plain string
    # fields `principalId` and `scope` are available. We therefore have to
    # wait until the UserAssignedIdentity reports its principalId AND the
    # StorageAccount reports its full Azure resource ID, then emit the
    # RoleAssignment with literal values. Until both are observed, skip the
    # emit so the function output validates cleanly.
    if principal_id and storage_account_id:
        role_assignment = {
            "apiVersion": "authorization.azure.m.upbound.io/v1beta1",
            "kind": "RoleAssignment",
            "metadata": {
                "name": f"{id_val}-backup-role",
                "namespace": "default",
                "annotations": {
                    "crossplane.io/composition-resource-name": "backup-role"
                }
            },
            "spec": {
                "forProvider": {
                    "principalId": principal_id,
                    "roleDefinitionName": "Storage Blob Data Contributor",
                    "scope": storage_account_id
                },
                "providerConfigRef": {
                    "name": provider_config,
                    "kind": "ProviderConfig"
                }
            }
        }
        # RoleAssignment does not accept Azure tags.
        stamp(role_assignment, config)
        resource.update(rsp.desired.resources["backup-role"], role_assignment)

    # Patch the UXP ServiceAccount with the Workload Identity client-id
    # annotation; the next pod restart picks up the token-mount projection.
    # The pod also needs the `azure.workload.identity/use: "true"` label —
    # we apply it via the controller-restart Object below so the SA
    # annotation and the pod label converge together.
    sa_annotations = {}
    if client_id:
        sa_annotations["azure.workload.identity/client-id"] = client_id

    sa_patch = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-sa",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-sa"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "v1",
                    "kind": "ServiceAccount",
                    "metadata": {
                        "name": "upbound-controller-manager",
                        "namespace": "crossplane-system",
                        "annotations": sa_annotations,
                        "labels": {
                            "azure.workload.identity/use": "true"
                        }
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(sa_patch, config)
    resource.update(rsp.desired.resources["backup-sa"], sa_patch)

    # Rolling restart of the controller deployment to pick up the new SA
    # projection. The kubectl.kubernetes.io/restartedAt value is treated as a
    # template marker by the AWS sibling; here it is a literal string written
    # once, which is enough to force a single rollout when the Object is
    # first applied. We also set the workload-identity pod label so the
    # mutating webhook injects the projected token.
    controller_restart = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-controller-restart",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "controller-restart"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": "upbound-controller-manager",
                        "namespace": "crossplane-system",
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": "{{ now }}"
                        }
                    },
                    "spec": {
                        "template": {
                            "metadata": {
                                "labels": {
                                    "azure.workload.identity/use": "true"
                                }
                            }
                        }
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(controller_restart, config)
    resource.update(rsp.desired.resources["controller-restart"], controller_restart)

    if install_from:
        src_account, src_container = parse_blob_location(install_from.get("location", ""))
        restore_name = install_from.get("name", "")

        if src_account and src_container and restore_name:
            restore = {
                "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
                "kind": "Object",
                "metadata": {
                    "name": f"{id_val}-backup-restore",
                    "namespace": "default",
                    "annotations": {
                        "crossplane.io/composition-resource-name": "backup-restore"
                    }
                },
                "spec": {
                    "forProvider": {
                        "manifest": {
                            "apiVersion": "admin.uxp.upbound.io/v1beta1",
                            "kind": "Restore",
                            "metadata": {
                                "name": f"{id_val}-restore"
                            },
                            "spec": {
                                "backupRef": {
                                    "name": restore_name
                                },
                                "backupLocation": {
                                    "provider": "Azure",
                                    "bucket": src_container,
                                    "credentials": {
                                        "source": "InjectedIdentity"
                                    },
                                    "config": {
                                        "endpoint": f"{src_account}.blob.core.windows.net"
                                    }
                                }
                            }
                        }
                    },
                    "providerConfigRef": {
                        "name": id_val,
                        "kind": "ProviderConfig"
                    }
                }
            }
            stamp(restore, config)
            resource.update(rsp.desired.resources["backup-restore"], restore)
