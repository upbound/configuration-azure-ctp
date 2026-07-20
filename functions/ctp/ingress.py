"""09b-ingress — nginx ingress controller Helm Release.

Installed when an add-on that needs an Ingress is enabled (k8gb or ArgoCD), so
plain control planes do not pay for an idle cloud load balancer. Uses a
standard LoadBalancer Service (not hostNetwork) — the add-on UIs are reached
through it.

Cloud-agnostic — identical to the AWS sibling.
"""

from crossplane.function import resource

from .prelude import stamp


def add_ingress_resources(rsp, id_val, ingress_ready, config):
    annotations = {
        "crossplane.io/composition-resource-name": "ingress-nginx-release"
    }
    # provider-helm stale-Ready workaround (same as uxp.py).
    if ingress_ready:
        annotations["crossplane.io/ready"] = "True"

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-ingress-nginx",
            "namespace": "default",
            "annotations": annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "ingress-nginx",
                    "repository": "https://kubernetes.github.io/ingress-nginx",
                    # renovate: datasource=helm depName=ingress-nginx registryUrl=https://kubernetes.github.io/ingress-nginx
                    "version": "4.11.3"
                },
                "namespace": "ingress-nginx",
                "skipCreateNamespace": False,
                "wait": True,
                "set": [
                    {"name": "controller.service.type", "value": "LoadBalancer"}
                ]
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(release, config)
    resource.update(rsp.desired.resources["ingress-nginx-release"], release)
