"""03-uxp — UXP v2 Helm Release on the new AKS cluster."""

from crossplane.function import resource

from .prelude import stamp


def add_uxp_release(rsp, id_val, uxp_version, uxp_deployed, mgr_args, config):
    annotations = {
        "crossplane.io/composition-resource-name": "uxp-release"
    }
    # provider-helm v1.2.2 leaves Ready=Unavailable even after state=deployed;
    # marking the resource ready here lets function-auto-ready short-circuit
    # without misrepresenting the underlying state.
    if uxp_deployed:
        annotations["crossplane.io/ready"] = "True"

    values = {
        "upbound": {
            "manager": {
                "metering": {
                    "podSecurityContext": {
                        "fsGroup": 65532,
                        "fsGroupChangePolicy": "OnRootMismatch"
                    }
                }
            }
        }
    }

    if mgr_args:
        values["upbound"]["manager"]["args"] = mgr_args

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-uxp",
            "namespace": "default",
            "annotations": annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "crossplane",
                    "repository": "oci://xpkg.upbound.io/upbound",
                    "version": uxp_version
                },
                "namespace": "crossplane-system",
                "skipCreateNamespace": False,
                "wait": True,
                "waitTimeout": "20m",
                "values": values
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }

    stamp(release, config)
    resource.update(rsp.desired.resources["uxp-release"], release)
