"""07-license — UXP enterprise license Secret + License CR.

Module is named `licensing` to avoid shadowing the stdlib `license` builtin.
Cloud-agnostic — identical to the AWS sibling.
"""

from crossplane.function import resource

from .prelude import stamp


def add_license_resources(rsp, id_val, license_param, config):
    secret_ref = license_param.get("secretRef", {})
    lic_name = secret_ref.get("name", "")
    lic_ns = secret_ref.get("namespace", "default")

    # Provider-kubernetes copies the license data from the source Secret on
    # the management cluster (lic_ns/lic_name) into crossplane-system on the
    # new control plane. The `references[].patchesFrom` block does the copy.
    license_secret = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-uxp-license-secret",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "uxp-license-secret"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": lic_name,
                        "namespace": "crossplane-system"
                    },
                    "type": "Opaque",
                    "data": {}
                }
            },
            "references": [
                {
                    "patchesFrom": {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "name": lic_name,
                        "namespace": lic_ns,
                        "fieldPath": 'data["license.json"]'
                    },
                    "toFieldPath": 'data["license.json"]'
                }
            ],
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(license_secret, config)
    resource.update(rsp.desired.resources["uxp-license-secret"], license_secret)

    license_cr = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-uxp-license",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "uxp-license"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "licensing.upbound.io/v1alpha1",
                    "kind": "License",
                    "metadata": {
                        # UXP's License validation webhook requires the CR
                        # to be named exactly "uxp" — it is a singleton.
                        "name": "uxp"
                    },
                    "spec": {
                        "secretRef": {
                            "name": lic_name,
                            "namespace": "crossplane-system"
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
    stamp(license_cr, config)
    resource.update(rsp.desired.resources["uxp-license"], license_cr)
