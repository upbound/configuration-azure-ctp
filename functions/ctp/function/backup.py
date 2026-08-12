"""05-backup — StorageAccount + Container, observe AKS, BackupConfig, RBAC, BackupSchedule.

All resources here are gated on backup.enabled == "yes" (the caller in
fn.py handles that gate). The BackupConfig/RBAC Objects are emitted
unconditionally inside that gate — provider-kubernetes Object resources stay
pending until UXP installs the BackupConfig CRD, then reconcile naturally.

Azure analog notes:
* The backup location is parsed from `<storage-account-name>/<container>`
  rather than an S3 ARN.
* StorageAccount and Container are imported (managementPolicies do not
  include Delete) so the data survives ControlPlane deletion.
* The BackupConfig's objectStorage.provider is "Azure" and config.endpoint
  is the blob endpoint for the storage account.
* credentials.source remains "InjectedIdentity" — the backup controller
  pod uses the Azure Workload Identity token mounted via the federated
  credential set up in workload_identity.py.
"""

from crossplane.function import resource

from .prelude import stamp


def add_backup_resources(rsp, id_val, location, bucket_region, provider_config,
                        storage_account, container_name,
                        backup, uxp_deployed, client_id, config):
    # The StorageAccount lives in the ResourceGroup owned by the Network XR,
    # selected by the network-id label. bucket_region defaults to the cluster
    # location but may differ for cross-region DR (an Azure resource may sit in
    # a different region than its resource group).
    storage_acct = {
        "apiVersion": "storage.azure.m.upbound.io/v1beta1",
        "kind": "Account",
        "metadata": {
            "name": storage_account,
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-storage-account",
                "crossplane.io/external-name": storage_account
            }
        },
        "spec": {
            "managementPolicies": ["Observe", "Create", "Update", "LateInitialize"],
            "forProvider": {
                "location": bucket_region,
                "resourceGroupNameSelector": {
                    "matchLabels": {
                        "azure.platform.upbound.io/network-id": id_val
                    }
                },
                "accountTier": "Standard",
                "accountReplicationType": "LRS",
                "accountKind": "StorageV2"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(storage_acct, config, azure_tags=True)
    resource.update(rsp.desired.resources["backup-storage-account"], storage_acct)

    container = {
        "apiVersion": "storage.azure.m.upbound.io/v1beta1",
        "kind": "Container",
        "metadata": {
            "name": f"{id_val}-backup-container",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-container",
                "crossplane.io/external-name": container_name
            }
        },
        "spec": {
            "managementPolicies": ["Observe", "Create", "Update", "LateInitialize"],
            "forProvider": {
                "storageAccountNameRef": {
                    "name": storage_account
                },
                "containerAccessType": "private"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ProviderConfig"
            }
        }
    }
    # Container does not accept Azure tags.
    stamp(container, config)
    resource.update(rsp.desired.resources["backup-container"], container)

    # BackupConfig — the thanos objstore library requires config.endpoint;
    # without it the Azure blob client cannot resolve the storage account.
    # credentials.source: InjectedIdentity uses the Workload Identity token
    # mounted on the upbound-controller-manager pod.
    backup_config = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-config",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-config"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "admin.uxp.upbound.io/v1beta1",
                    "kind": "BackupConfig",
                    "metadata": {
                        "name": f"{id_val}-backup"
                    },
                    "spec": {
                        "objectStorage": {
                            "provider": "Azure",
                            "bucket": container_name,
                            "credentials": {
                                "source": "InjectedIdentity"
                            },
                            # `config` is passed through to thanos-io/objstore.
                            # Azure's Config struct uses YAML tag `storage_account`
                            # (NOT `storage_account_name` despite UXP's misleading
                            # pre-validation error message). The container name
                            # comes from the `bucket` field at the parent level.
                            #
                            # IMPORTANT: do NOT set `user_assigned_id` here.
                            # thanos-io/objstore's Azure provider, when it sees
                            # `user_assigned_id`, constructs
                            # `azidentity.NewManagedIdentityCredential(...)` which
                            # uses Azure IMDS (`169.254.169.254`) for auth — that
                            # path is for VMs with a user-assigned MI attached, not
                            # for AKS Workload Identity. With the field unset,
                            # thanos falls through to `NewDefaultAzureCredential`
                            # which tries `WorkloadIdentityCredential` first, using
                            # the AZURE_CLIENT_ID / AZURE_TENANT_ID /
                            # AZURE_FEDERATED_TOKEN_FILE env vars injected by the
                            # AKS workload-identity mutating webhook. That's
                            # exactly what we want.
                            "config": {
                                "storage_account": storage_account
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
    stamp(backup_config, config)
    resource.update(rsp.desired.resources["backup-config"], backup_config)

    # RBAC: the UXP Helm chart's default ClusterRole for
    # upbound-controller-manager does not grant access to
    # storeconfigs.secrets.crossplane.io. Backup export walks all Crossplane
    # resources including StoreConfigs, so without this extra ClusterRole the
    # backup fails at the export step with a 403.
    backup_rbac = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-rbac",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-rbac"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRole",
                    "metadata": {
                        "name": "upbound-backup-storeconfigs"
                    },
                    "rules": [
                        {
                            "apiGroups": ["secrets.crossplane.io"],
                            "resources": ["storeconfigs"],
                            "verbs": ["get", "list"]
                        }
                    ]
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(backup_rbac, config)
    resource.update(rsp.desired.resources["backup-rbac"], backup_rbac)

    backup_rbac_binding = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-rbac-binding",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-rbac-binding"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRoleBinding",
                    "metadata": {
                        "name": "upbound-backup-storeconfigs"
                    },
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "ClusterRole",
                        "name": "upbound-backup-storeconfigs"
                    },
                    "subjects": [
                        {
                            "kind": "ServiceAccount",
                            "name": "upbound-controller-manager",
                            "namespace": "crossplane-system"
                        }
                    ]
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(backup_rbac_binding, config)
    resource.update(rsp.desired.resources["backup-rbac-binding"], backup_rbac_binding)

    if backup.get("schedule"):
        backup_schedule = {
            "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
            "kind": "Object",
            "metadata": {
                "name": f"{id_val}-backup-schedule",
                "namespace": config["namespace"],
                "annotations": {
                    "crossplane.io/composition-resource-name": "backup-schedule"
                }
            },
            "spec": {
                "forProvider": {
                    "manifest": {
                        "apiVersion": "admin.uxp.upbound.io/v1beta1",
                        "kind": "BackupSchedule",
                        "metadata": {
                            "name": f"{id_val}-schedule"
                        },
                        "spec": {
                            "schedule": backup["schedule"],
                            "configRef": {
                                "name": f"{id_val}-backup"
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
        stamp(backup_schedule, config)
        resource.update(rsp.desired.resources["backup-schedule"], backup_schedule)
