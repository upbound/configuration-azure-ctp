"""05b-argo — ArgoCD add-on (UI Ingress + app-of-apps).

Installs ArgoCD on the child cluster, exposes its UI through an nginx Ingress
with a local (self-signed) cert-manager Certificate, and bootstraps a root
app-of-apps Application pointing at a public git repo.

Gating mirrors the other add-on chains:
- the self-signed ClusterIssuer + Certificate wait on cert-manager being ready
  (its CRDs must exist);
- the root Application waits on the ArgoCD release being deployed (the
  Application CRD must exist).

The Helm release name is pinned to `argocd` (external-name) so the server
Service is `argocd-server`, which the Ingress references.

Cloud-agnostic — identical to the AWS sibling.
"""

from crossplane.function import resource

from .prelude import stamp


def _child_object(id_val, cr_name, manifest):
    return {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-{cr_name}",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": cr_name
            }
        },
        "spec": {
            "forProvider": {"manifest": manifest},
            "providerConfigRef": {"name": id_val, "kind": "ProviderConfig"}
        }
    }


def add_argocd_resources(rsp, id_val, argocd_param, argocd_deployed,
                         certmanager_ready, config):
    hostname = argocd_param.get("hostname", "")
    url = argocd_param.get("url", "")

    release_annotations = {
        "crossplane.io/composition-resource-name": "argocd-release",
        # Pin the Helm release name so the server Service is `argocd-server`.
        "crossplane.io/external-name": "argocd"
    }
    if argocd_deployed:
        release_annotations["crossplane.io/ready"] = "True"

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-argocd",
            "namespace": "default",
            "annotations": release_annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "argo-cd",
                    "repository": "https://argoproj.github.io/argo-helm",
                    # renovate: datasource=helm depName=argo-cd registryUrl=https://argoproj.github.io/argo-helm
                    "version": "10.1.4"
                },
                "namespace": "argocd",
                "skipCreateNamespace": False,
                "wait": True,
                "values": {
                    # nginx terminates TLS; argocd-server serves plain HTTP so
                    # there is no redirect loop behind the Ingress.
                    "configs": {
                        "params": {
                            "server.insecure": True
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
    stamp(release, config)
    resource.update(rsp.desired.resources["argocd-release"], release)

    # UI Ingress (nginx). TLS secret is filled by the Certificate below.
    ingress = _child_object(id_val, "argocd-ingress", {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": "argocd-server",
            "namespace": "argocd",
            "annotations": {
                "nginx.ingress.kubernetes.io/backend-protocol": "HTTP"
            }
        },
        "spec": {
            "ingressClassName": "nginx",
            "rules": [
                {
                    "host": hostname,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": "argocd-server",
                                        "port": {"number": 80}
                                    }
                                }
                            }
                        ]
                    }
                }
            ],
            "tls": [
                {"hosts": [hostname], "secretName": "argocd-server-tls"}
            ]
        }
    })
    stamp(ingress, config)
    resource.update(rsp.desired.resources["argocd-ingress"], ingress)

    # Self-signed issuer + Certificate for the UI (a real global-hostname cert
    # is issued by the parent and synced down later — see gslb-dns §8). Both
    # need cert-manager's CRDs, so wait until it is ready.
    if certmanager_ready:
        issuer = _child_object(id_val, "argocd-issuer", {
            "apiVersion": "cert-manager.io/v1",
            "kind": "ClusterIssuer",
            "metadata": {"name": "argocd-selfsigned"},
            "spec": {"selfSigned": {}}
        })
        stamp(issuer, config)
        resource.update(rsp.desired.resources["argocd-issuer"], issuer)

        certificate = _child_object(id_val, "argocd-cert", {
            "apiVersion": "cert-manager.io/v1",
            "kind": "Certificate",
            "metadata": {"name": "argocd-server-cert", "namespace": "argocd"},
            "spec": {
                "secretName": "argocd-server-tls",
                "dnsNames": [hostname],
                "issuerRef": {
                    "name": "argocd-selfsigned",
                    "kind": "ClusterIssuer"
                }
            }
        })
        stamp(certificate, config)
        resource.update(rsp.desired.resources["argocd-cert"], certificate)

    # Root app-of-apps. Needs the Application CRD, so wait for the release.
    if argocd_deployed:
        application = _child_object(id_val, "argocd-app", {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": "root", "namespace": "argocd"},
            "spec": {
                "project": "default",
                "source": {
                    "repoURL": url,
                    "path": ".",
                    "targetRevision": "HEAD"
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": "default"
                },
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True}
                }
            }
        })
        stamp(application, config)
        resource.update(rsp.desired.resources["argocd-app"], application)
