"""09a-certmanager — always-on cert-manager Helm Release.

cert-manager is installed unconditionally on every control plane (decoupled
from the knative/license gates it used to live behind): the k8gb and ArgoCD
add-ons need it for Ingress TLS regardless of whether knative is enabled. Its
readiness is surfaced separately so those add-ons never couple to knative.

Cloud-agnostic — identical to the AWS sibling.
"""

from crossplane.function import resource

from .prelude import stamp


def add_certmanager_resources(rsp, id_val, certmanager_ready, config):
    annotations = {
        "crossplane.io/composition-resource-name": "certmanager-release"
    }
    # provider-helm stale-Ready workaround (same as uxp.py): mark ready once the
    # chart reports state=deployed so function-auto-ready accepts it.
    if certmanager_ready:
        annotations["crossplane.io/ready"] = "True"

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-certmanager",
            "namespace": "default",
            "annotations": annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "cert-manager",
                    "repository": "https://charts.jetstack.io",
                    # renovate: datasource=helm depName=cert-manager registryUrl=https://charts.jetstack.io
                    "version": "v1.20.3"
                },
                "namespace": "cert-manager",
                "skipCreateNamespace": False,
                "wait": True,
                "set": [
                    {"name": "crds.enabled", "value": "true"}
                ]
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(release, config)
    resource.update(rsp.desired.resources["certmanager-release"], release)
