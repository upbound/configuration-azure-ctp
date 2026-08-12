"""
00-prelude — shared extractors and helpers.

Read-only logic that inspects parameters and observed state to derive values
consumed by every other section. Azure analog of configuration-aws-ctp's
prelude.py: OIDC issuer URL comes from the AKS cluster's
status.atProvider.oidcIssuerUrl, and the Workload Identity client ID is read
back from the observed UserAssignedIdentity.
"""

import re
from typing import Dict, List, Optional


def stamp(resource_dict: dict, config: Dict, azure_tags: bool = False) -> None:
    """Stamp a resource with the current reconciliation timestamp.

    Every resource carries `last-reconcile-date` as a metadata annotation so
    an operator can see when this composition function last touched it.
    Azure managed resources that accept native tags (ResourceGroup,
    VirtualNetwork, KubernetesCluster, StorageAccount, UserAssignedIdentity)
    also get the timestamp in `spec.forProvider.tags`.
    """
    meta = resource_dict.setdefault("metadata", {})
    ann = meta.setdefault("annotations", {})
    ann["last-reconcile-date"] = config["last_reconcile_date"]

    if azure_tags:
        fp = resource_dict.setdefault("spec", {}).setdefault("forProvider", {})
        tags = fp.setdefault("tags", {})
        tags["last-reconcile-date"] = config["last_reconcile_date"]


def check_license_conflict(id_val: str, license_param: Optional[Dict],
                           all_ctps: List[Dict]) -> str:
    """Return the name of another ControlPlane that already claims the same
    license secret (namespace/name pair), or "" if there is no conflict."""
    if not license_param or not all_ctps:
        return ""

    my_ns = license_param.get("secretRef", {}).get("namespace", "default")
    my_name = license_param.get("secretRef", {}).get("name", "")
    my_key = f"{my_ns}/{my_name}"

    for ctp in all_ctps:
        c_name = ctp.get("metadata", {}).get("name", "")
        if c_name and c_name != id_val:
            c_license = ctp.get("spec", {}).get("parameters", {}).get("license", {})
            if c_license and c_license.get("secretRef"):
                c_ns = c_license["secretRef"].get("namespace", "default")
                c_name2 = c_license["secretRef"].get("name", "")
                c_key = f"{c_ns}/{c_name2}"
                if c_name2 and c_key == my_key:
                    return c_name
    return ""


def extract_oidc_info(backup: Dict, observed: Dict) -> tuple:
    """Extract (oidc_issuer_url, oidc_host) from the composed AKS XR.

    configuration-azure-aks surfaces the workload-identity OIDC issuer at
    status.aks.oidcUrl (populated from the KubernetesCluster's
    oidcIssuerUrl). Returns empty strings until the AKS XR reports it.
    """
    if backup.get("enabled") != "yes":
        return "", ""

    obs = observed.get("aks")
    if not obs:
        return "", ""

    res = obs.resource if hasattr(obs, "resource") else obs
    issuer_url = res.get("status", {}).get("aks", {}).get("oidcUrl", "")
    issuer_host = issuer_url.replace("https://", "").rstrip("/") if issuer_url else ""

    return issuer_url, issuer_host


def get_workload_identity_client_id(observed: Dict) -> str:
    """Return the clientId of the observed UserAssignedIdentity that backs
    UXP's Workload Identity, or "" if not yet synced.

    The provider-azure-managedidentity provider exposes the client ID at
    status.atProvider.clientId once Azure assigns one.
    """
    obs = observed.get("backup-identity")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("atProvider", {}).get("clientId", "")


def get_workload_identity_principal_id(observed: Dict) -> str:
    """Return the principalId of the observed UserAssignedIdentity, or "" if
    not yet synced. provider-azure-authorization's RoleAssignment v2
    namespaced variant has no principalIdRef resolver, so we have to read
    the value from observed state and pass it as a string."""
    obs = observed.get("backup-identity")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("atProvider", {}).get("principalId", "")


def get_storage_account_id(observed: Dict) -> str:
    """Return the Azure resource ID of the observed StorageAccount, or "" if
    not yet synced. Same constraint as principal_id: RoleAssignment has no
    scopeRef resolver, only the plain `scope` string field."""
    obs = observed.get("backup-storage-account")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("atProvider", {}).get("id", "")


def is_release_deployed(observed: Dict, name: str) -> bool:
    """True when the observed Helm Release has atProvider.state == 'deployed'."""
    obs = observed.get(name)
    if not obs:
        return False

    res = obs.resource if hasattr(obs, "resource") else obs
    state = res.get("status", {}).get("atProvider", {}).get("state", "")
    return state == "deployed"


def is_knative_serving_ready(observed: Dict) -> bool:
    """True when the KnativeServing CR reports Ready=True in its embedded
    manifest status (provider-kubernetes Object)."""
    obs = observed.get("knative-serving-cr")
    if not obs:
        return False

    res = obs.resource if hasattr(obs, "resource") else obs
    manifest_status = res.get("status", {}).get("atProvider", {}).get("manifest", {}).get("status", {})
    for cond in manifest_status.get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def is_license_applied(observed: Dict) -> bool:
    """True when the License Object reports Ready=True (license accepted)."""
    obs = observed.get("uxp-license")
    if not obs:
        return False

    res = obs.resource if hasattr(obs, "resource") else obs
    for cond in res.get("status", {}).get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def build_manager_args(vpa: Optional[Dict], knative: Optional[Dict],
                       vpa_ready: bool, knative_ready: bool,
                       features_licensed: bool) -> List[str]:
    """Assemble the upbound.manager.args list for the UXP Helm Release based on
    which optional features are enabled, deployed, and licensed."""
    args: List[str] = []

    if vpa and vpa.get("enabled") == "yes" and vpa_ready and features_licensed:
        args.append("--enable-provider-vpa")

    if knative and knative.get("enabled") == "yes" and knative_ready and features_licensed:
        args.append("--enable-knative-runtime")

    return args


def parse_blob_location(location: str) -> tuple:
    """Parse a backup location of the form "<storage-account>/<container>"
    into (storage_account_name, container_name). Returns ("", "") for
    malformed input."""
    if not location:
        return "", ""
    match = re.match(r"^([a-z0-9]+)/([a-z0-9-]+)$", location)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def get_nodepool_actual_vm_size(observed: Dict) -> str:
    """Return the running default-node-pool vmSize from the composed AKS XR's
    status.aks.nodes.vmSize, or "" until the XR surfaces it
    (configuration-azure-aks v2.0.1+)."""
    obs = observed.get("aks")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("aks", {}).get("nodes", {}).get("vmSize", "")


def get_cluster_name(observed: Dict) -> str:
    """Return the AKS cluster name from the composed AKS XR's
    status.aks.clusterName, or "" until the XR surfaces it
    (configuration-azure-aks v2.0.1+)."""
    obs = observed.get("aks")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("aks", {}).get("clusterName", "")


def get_cluster_principal_id(observed: Dict) -> str:
    """Return the AKS cluster's SystemAssigned identity principalId from the
    composed AKS XR's status.aks.identityPrincipalId (configuration-azure-aks
    >= the release that surfaces it), or "" until present."""
    obs = observed.get("aks")
    if not obs:
        return ""
    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("aks", {}).get("identityPrincipalId", "")


def derive_k8gb_geo_tag(k8gb_param: Optional[Dict], location: str,
                        id_val: str) -> str:
    """The k8gb clusterGeoTag, unique per control plane. Defaults to
    azure-<location>-<id> (a bare azure-<location> collides when two CPs share a
    location); an explicit clusterGeoTag param overrides it."""
    tag = (k8gb_param or {}).get("clusterGeoTag")
    return tag if tag else f"azure-{location}-{id_val}"


def derive_k8gb_ext_geo_tags(id_val: str, dns_zone: str, my_tag: str,
                             all_ctps: List[Dict]) -> str:
    """Comma-separated geo tags of same-cloud peer control planes that have
    k8gb enabled on the same dnsZone. Cross-cloud peers are injected by the
    fleet layer (FleetGslb) and are out of scope here; empty is fine for a
    single-cluster start."""
    tags = set()
    for ctp in all_ctps:
        c_name = ctp.get("metadata", {}).get("name", "")
        if not c_name or c_name == id_val:
            continue
        c_params = ctp.get("spec", {}).get("parameters", {})
        c_k8gb = c_params.get("k8gb", {}) or {}
        if c_k8gb.get("enabled") != "yes" or c_k8gb.get("dnsZone") != dns_zone:
            continue
        c_tag = derive_k8gb_geo_tag(c_k8gb, c_params.get("location", ""), c_name)
        if c_tag and c_tag != my_tag:
            tags.add(c_tag)
    return ",".join(sorted(tags))


def extract_coredns_endpoint(observed: Dict) -> str:
    """The k8gb CoreDNS LoadBalancer endpoint (IP or hostname) read from the
    observe-only Object on the child CoreDNS Service. Azure Standard LBs surface
    an IP (the value needed for NS glue). Empty until the LB is provisioned."""
    obs = observed.get("k8gb-coredns-observe")
    if not obs:
        return ""
    res = obs.resource if hasattr(obs, "resource") else obs
    ingress = (res.get("status", {})
                  .get("atProvider", {})
                  .get("manifest", {})
                  .get("status", {})
                  .get("loadBalancer", {})
                  .get("ingress", []))
    if not ingress:
        return ""
    first = ingress[0]
    return first.get("ip") or first.get("hostname") or ""


def extract_k8gb_public_ip(observed: Dict) -> str:
    """The pinned k8gb CoreDNS static IP, read from the composed Azure PublicIP
    MR's status.atProvider.ipAddress. Empty until Azure allocates it."""
    obs = observed.get("k8gb-ip")
    if not obs:
        return ""
    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("atProvider", {}).get("ipAddress", "")


def extract_k8gb_public_ip_id(observed: Dict) -> str:
    """The pinned k8gb Public IP's Azure resource ID (RoleAssignment scope).
    Empty until the PublicIP MR reports it."""
    obs = observed.get("k8gb-ip")
    if not obs:
        return ""
    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("atProvider", {}).get("id", "")
