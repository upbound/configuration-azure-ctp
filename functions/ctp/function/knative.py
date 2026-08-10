"""09-knative — knative-operator + KnativeServing CR.

cert-manager (a hard dependency of Knative Serving) is installed separately and
unconditionally by certmanager.py; this module gates the KnativeServing CR on
that always-on cert-manager plus the knative-operator both reaching
state=deployed, otherwise provider-kubernetes would reject the manifest because
the CRDs do not exist yet.

Cloud-agnostic — identical to the AWS sibling.
"""

from crossplane.function import resource

from .prelude import stamp


def add_knative_resources(rsp, id_val, knative_op_ready,
                         knative_deps_ready, knative_serving_ready,
                         observed, config):
    # Add the provider-helm v1.2.2 stale-Ready workaround: when the chart
    # has reached state=deployed in observed state, stamp the resource as
    # ready so function-auto-ready accepts it. Same pattern as uxp.py.
    operator_annotations = {
        "crossplane.io/composition-resource-name": "knative-operator-release"
    }
    if knative_op_ready:
        operator_annotations["crossplane.io/ready"] = "True"

    operator_release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-knative-operator",
            "namespace": config["namespace"],
            "annotations": operator_annotations
        },
        "spec": {
            # Install once and leave alone.
            #
            # The knative-operator chart ships an empty `operator-webhook-certs`
            # Secret (`# The data is populated at install time.`) — the
            # operator-webhook pod populates ca-cert.pem/server-cert.pem/
            # server-key.pem at runtime. Under provider-helm's default
            # continuous-reconciliation behavior, each reconcile re-renders the
            # chart with an empty Secret, compares against the populated live
            # Secret, sees "drift", triggers a Helm upgrade, the upgrade resets
            # the Secret, the operator regenerates the certs, repeat — a tight
            # upgrade loop (~25s) that never lets the Release reach Ready.
            #
            # Dropping Update (and Delete, to keep the operator running across
            # XR deletes) from managementPolicies stops the loop: provider-helm
            # creates the release on first reconcile, then only observes. To
            # bump the chart version, delete and recreate the Release MR.
            "managementPolicies": ["Create", "Observe"],
            "forProvider": {
                "chart": {
                    "name": "knative-operator",
                    "repository": "https://knative.github.io/operator",
                    # renovate: datasource=helm depName=knative-operator registryUrl=https://knative.github.io/operator
                    "version": "v1.21.1"
                },
                # Chart install target namespace on the workload cluster
                # (unrelated to the XR's own namespace) - fixed at "default".
                "namespace": "default",
                "skipCreateNamespace": False,
                "wait": True
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(operator_release, config)
    resource.update(rsp.desired.resources["knative-operator-release"], operator_release)

    if knative_deps_ready:
        # The Knative operator does NOT auto-create the knative-serving
        # namespace — applying a KnativeServing CR into a missing namespace
        # fails. Create the namespace explicitly before the CR.
        serving_ns = {
            "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
            "kind": "Object",
            "metadata": {
                "name": f"{id_val}-knative-serving-ns",
                "namespace": config["namespace"],
                "annotations": {
                    "crossplane.io/composition-resource-name": "knative-serving-ns"
                }
            },
            "spec": {
                "forProvider": {
                    "manifest": {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {
                            "name": "knative-serving"
                        }
                    }
                },
                "providerConfigRef": {
                    "name": id_val,
                    "kind": "ProviderConfig"
                }
            }
        }
        stamp(serving_ns, config)
        resource.update(rsp.desired.resources["knative-serving-ns"], serving_ns)

        serving_cr = {
            "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
            "kind": "Object",
            "metadata": {
                "name": f"{id_val}-knative-serving-cr",
                "namespace": config["namespace"],
                "annotations": {
                    "crossplane.io/composition-resource-name": "knative-serving-cr"
                }
            },
            "spec": {
                "forProvider": {
                    "manifest": {
                        "apiVersion": "operator.knative.dev/v1beta1",
                        "kind": "KnativeServing",
                        "metadata": {
                            "name": "knative-serving",
                            "namespace": "knative-serving"
                        },
                        "spec": {
                            "config": {
                                "network": {
                                    "auto-tls": "Enabled",
                                    "http-protocol": "Redirected"
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
        stamp(serving_cr, config)
        resource.update(rsp.desired.resources["knative-serving-cr"], serving_cr)
