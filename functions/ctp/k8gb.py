"""04b-k8gb — k8gb operator + CoreDNS producer (docs/ctp-addons-implementation-plan.md).

Installs the k8gb operator and its CoreDNS on the child cluster, exposing
CoreDNS via an Azure Standard LoadBalancer serving :53, and observes that
Service so the XR can surface the k8gb status contract (coreDNSEndpoint +
nsName + glueAddresses + delegationRecord) for the FleetGslb aggregator to consume.

- Chart pinned to v0.20.0. `installLegacyCrds: true` (set explicitly below) keeps
  the `k8gb.absa.oss/v1beta1` `Gslb` CRD group installed alongside the new
  `k8gb.io/v1beta1`, so configuration-resilient-ctp's consumer contract holds and
  does not depend on the chart default.
- `extdns.enabled: false`: this package is a producer only; the parent-side
  FleetGslb writes the NS delegation, not per-child external-dns.
- CoreDNS is exposed by a plain `type: LoadBalancer` Service; AKS provisions an
  Azure Standard LB that supports UDP natively, so no LB-controller add-on is
  needed (unlike AWS EKS). A glue-stable static Public IP is deferred (see the
  implementation plan) — the LB IP is read back via the observe Object below.
- The Helm release name is pinned to `k8gb` (external-name) so its CoreDNS
  Service is `k8gb-coredns` in namespace `k8gb`, the name k8gb expects.
"""

from crossplane.function import resource

from .prelude import stamp


def add_k8gb_resources(rsp, id_val, k8gb_param, geo_tag, ext_geo_tags,
                       k8gb_deployed, config):
    dns_zone = k8gb_param.get("dnsZone", "")
    parent_zone = k8gb_param.get("parentZone", "")

    values = {
        "k8gb": {
            "deployCrds": True,
            "deployRbac": True,
            # Explicit (not relying on the chart default): keeps the legacy
            # k8gb.absa.oss/v1beta1 Gslb CRD for the resilient-ctp consumer.
            "installLegacyCrds": True,
            "clusterGeoTag": geo_tag,
            "extGslbClustersGeoTags": ext_geo_tags,
            "dnsZones": [
                {
                    "loadBalancedZone": dns_zone,
                    "parentZone": parent_zone
                }
            ],
            "edgeDNSServers": ["1.1.1.1"]
        },
        # Producer only — the parent (FleetGslb) writes the NS delegation.
        "extdns": {"enabled": False},
        # AKS provisions an Azure Standard LB with native UDP support; a plain
        # LoadBalancer Service is enough (no cloud LB-controller annotations).
        # UDP-only CoreDNS on :53. The coredns subchart renders a UDP-only
        # Service when every server zone sets use_tcp: false; Azure Standard LB
        # (like GKE L4) does not accept a mixed TCP+UDP Service on one port on
        # older clusters, and DNS glue lookups are UDP.
        "coredns": {
            "serviceType": "LoadBalancer",
            "servers": [
                {
                    "zones": [{"zone": ".", "use_tcp": False}],
                    "port": 5353,
                    "servicePort": 53,
                    "plugins": [
                        {"name": "prometheus", "parameters": "0.0.0.0:9153"}
                    ]
                }
            ]
        }
    }

    release_annotations = {
        "crossplane.io/composition-resource-name": "k8gb-release",
        # Pin the Helm release name so CoreDNS is `k8gb-coredns` in ns `k8gb`.
        "crossplane.io/external-name": "k8gb"
    }
    if k8gb_deployed:
        release_annotations["crossplane.io/ready"] = "True"

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-k8gb",
            "namespace": config["namespace"],
            "annotations": release_annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "k8gb",
                    "repository": "https://www.k8gb.io",
                    # renovate: datasource=helm depName=k8gb registryUrl=https://www.k8gb.io
                    # Pinned: Gslb CRD group is the producer/consumer contract; legacy CRDs stay on.
                    "version": "v0.20.0"
                },
                "namespace": "k8gb",
                "skipCreateNamespace": False,
                "wait": True,
                "values": values
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(release, config)
    resource.update(rsp.desired.resources["k8gb-release"], release)

    # Observe-only Object on the child CoreDNS Service to read its LB endpoint.
    coredns_observe = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-k8gb-coredns",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "k8gb-coredns-observe"
            }
        },
        "spec": {
            "managementPolicies": ["Observe"],
            "forProvider": {
                "manifest": {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {
                        "name": "k8gb-coredns",
                        "namespace": "k8gb"
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(coredns_observe, config)
    resource.update(rsp.desired.resources["k8gb-coredns-observe"], coredns_observe)
