"""08-vpa — Vertical Pod Autoscaler Helm Release.

VPA needs the Kubernetes metrics API to compute pod recommendations. AKS
ships metrics-server as a managed addon (in kube-system, owned by the
cluster's addon-manager), so this Azure variant only installs VPA itself.
Trying to install Fairwinds' bundled metrics-server alongside the AKS addon
fails because provider-helm refuses to adopt resources without Helm
ownership annotations.

The AWS sibling installs metrics-server explicitly because EKS does not
ship one by default.
"""

from crossplane.function import resource

from .prelude import stamp


def add_vpa_resources(rsp, id_val, vpa, vpa_ready, config):
    vpa_release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-vpa",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "vpa-release"
            }
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "vpa",
                    "repository": "https://charts.fairwinds.com/stable",
                    # renovate: datasource=helm depName=vpa registryUrl=https://charts.fairwinds.com/stable
                    "version": "4.10.1"
                },
                "namespace": "kube-system",
                "skipCreateNamespace": False,
                "wait": True
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(vpa_release, config)
    resource.update(rsp.desired.resources["vpa-release"], vpa_release)
