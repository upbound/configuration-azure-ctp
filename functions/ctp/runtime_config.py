"""10-runtime-config — UpboundRuntimeConfig with merged ProviderVPA / Knative
capabilities. Emitted only once at least one capability is actually ready.

Cloud-agnostic — identical to the AWS sibling.
"""

from crossplane.function import resource

from .prelude import stamp


def add_runtime_config(rsp, id_val, vpa, knative, vpa_ready, knative_ready, config):
    capabilities = []

    if vpa and vpa.get("enabled") == "yes" and vpa_ready:
        capabilities.append("ProviderVPA")

    if knative and knative.get("enabled") == "yes" and knative_ready:
        capabilities.append("FunctionKnativeRuntime")

    if not capabilities:
        return

    runtime_config = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-upbound-runtime-config",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "upbound-runtime-config"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "pkg.upbound.io/v1beta1",
                    "kind": "UpboundRuntimeConfig",
                    "metadata": {
                        "name": "default"
                    },
                    "spec": {
                        "capabilities": capabilities
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(runtime_config, config)
    resource.update(rsp.desired.resources["upbound-runtime-config"], runtime_config)
