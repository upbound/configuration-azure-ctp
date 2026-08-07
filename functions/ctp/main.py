"""
Composition function for Azure AKS Control Plane with UXP backup support.

Azure analog of configuration-aws-ctp. Each section below is implemented in a
sibling module:

  prelude.py            (00) shared extractors and helpers
  network.py            (01) ResourceGroup + VirtualNetwork + Subnet
  aks.py                (02) KubernetesCluster + Helm/Kubernetes ProviderConfigs
  uxp.py                (03) UXP v2 Helm Release
  k8gb.py               (04b) k8gb operator + CoreDNS producer
  argo.py               (05b) ArgoCD add-on (UI Gateway/HTTPRoute + app-of-apps)
  usages.py             (04) deletion-order Usage guards
  backup.py             (05) StorageAccount, Container, observe AKS, BackupConfig, RBAC, Schedule
  workload_identity.py  (06) UserAssignedIdentity, FederatedIdentityCredential,
                              RoleAssignment, SA annotation, controller restart, restore
  licensing.py          (07) License Secret + License CR
  vpa.py                (08) VPA + metrics-server Helm Releases
  certmanager.py        (09a) always-on cert-manager Helm Release
  gateway.py            (09b) Envoy Gateway data plane (k8gb/argocd)
  knative.py            (09) knative-operator + serving CR
  runtime_config.py     (10) UpboundRuntimeConfig (ProviderVPA + Knative caps)
  status.py             (99) XR status writeback + ClaimConditions

Cluster metadata (OIDC issuer, running node-pool vmSize, cluster name) is read
from the composed AKS XR's status.aks (configuration-azure-aks v2.0.1+); the only
observe-only resource composed here is the k8gb CoreDNS Service Object (to read
its LoadBalancer endpoint for the status contract).
"""

from datetime import datetime, timezone

from crossplane.function import resource
from crossplane.function.proto.v1 import run_function_pb2 as fnv1

from .aks import add_aks_resources
from .argo import add_argocd_resources
from .backup import add_backup_resources
from .certmanager import add_certmanager_resources
from .gateway import add_gateway_resources
from .k8gb import add_k8gb_resources
from .knative import add_knative_resources
from .licensing import add_license_resources
from .network import add_network_resources
from .prelude import (
    build_manager_args,
    check_license_conflict,
    derive_k8gb_ext_geo_tags,
    derive_k8gb_geo_tag,
    extract_k8gb_public_ip,
    extract_k8gb_public_ip_id,
    extract_oidc_info,
    get_cluster_name,
    get_cluster_principal_id,
    get_nodepool_actual_vm_size,
    get_storage_account_id,
    get_workload_identity_client_id,
    get_workload_identity_principal_id,
    is_knative_serving_ready,
    is_license_applied,
    is_release_deployed,
    parse_blob_location,
)
from .runtime_config import add_runtime_config
from .status import update_status
from .usages import add_usage_resources
from .uxp import add_uxp_release
from .vpa import add_vpa_resources
from .workload_identity import add_workload_identity_resources


# managementMode -> Crossplane managementPolicies. Provision and ObserveOnly
# never include Delete, so the provisioned control plane is orphaned (never torn
# down) when the XR is removed. Full (default) is the standard "*" lifecycle.
# Deprovision is the pipeline's decommission signal: adopt (Observe/Create) and
# Delete, but no Update/LateInitialize - a drifted or broken cluster must not have
# changes pushed to it on the way out, only be torn down.
_MODE_POLICIES = {
    "Provision": ["Observe", "Create", "Update", "LateInitialize"],
    "ObserveOnly": ["Observe"],
    "Full": ["*"],
    "Deprovision": ["Create", "Delete", "Observe"],
}


def compose(req: fnv1.RunFunctionRequest, rsp: fnv1.RunFunctionResponse):
    """Main composition function entry point."""
    config = {
        "last_reconcile_date": datetime.now(timezone.utc).strftime(
            "%A %Y-%m-%d %H:%M:%S UTC"
        ),
    }

    xr = resource.struct_to_dict(req.observed.composite.resource)
    params = xr.get("spec", {}).get("parameters", {})

    # The XR is namespaced (apis/ctp/definition.yaml scope: Namespaced); every
    # composed resource and the AKS sub-XR's kubeconfig connection secret
    # co-locate in the XR's own namespace. Falls back to "default" when unset.
    config["namespace"] = xr.get("metadata", {}).get("namespace") or "default"

    id_val = params.get("id", "")
    location = params.get("location", "")
    provider_config = params.get("providerConfigName", "default")
    version = params.get("version", "1.34")
    nodes = params.get("nodes", {})
    network_param = params.get("network", {})
    backup = params.get("backup", {"enabled": "no"})
    install_from = backup.get("installFrom")
    license_param = params.get("license")
    management_mode = params.get("managementMode", "Full")
    mgmt_policies = _MODE_POLICIES.get(management_mode, _MODE_POLICIES["Full"])
    uxp_version = params.get("uxp", {}).get("version", "2.2.1-up.1")
    vpa = params.get("providerVerticalPodAutoscaling")
    knative = params.get("knative")
    k8gb = params.get("k8gb")
    argocd = params.get("argocd")

    k8gb_enabled = bool(k8gb) and k8gb.get("enabled") == "yes"
    argocd_enabled = bool(argocd) and argocd.get("enabled") == "yes"

    # function-extra-resources delivers `allControlPlanes` via the
    # apiextensions.crossplane.io/extra-resources context key.
    context_dict = resource.struct_to_dict(req.context)
    extra_ctx = context_dict.get("apiextensions.crossplane.io/extra-resources", {})
    all_ctps = extra_ctx.get("allControlPlanes", [])

    license_conflict = check_license_conflict(id_val, license_param, all_ctps)

    # k8gb geo tags: this cluster's unique tag, plus same-cloud k8gb peers on
    # the same dnsZone (cross-cloud peers are injected later by FleetGslb).
    k8gb_geo_tag = ""
    k8gb_ext_geo_tags = ""
    if k8gb_enabled:
        k8gb_geo_tag = derive_k8gb_geo_tag(k8gb, location, id_val)
        k8gb_ext_geo_tags = derive_k8gb_ext_geo_tags(
            id_val, k8gb.get("dnsZone", ""), k8gb_geo_tag, all_ctps)

    observed_resources = {
        name: resource.struct_to_dict(res.resource)
        for name, res in req.observed.resources.items()
    }

    k8gb_public_ip = ""
    k8gb_public_ip_id = ""
    k8gb_cluster_principal_id = ""
    if k8gb_enabled:
        k8gb_public_ip = extract_k8gb_public_ip(observed_resources)
        k8gb_public_ip_id = extract_k8gb_public_ip_id(observed_resources)
        k8gb_cluster_principal_id = get_cluster_principal_id(observed_resources)

    oidc_issuer_url, _oidc_host = extract_oidc_info(backup, observed_resources)

    client_id = get_workload_identity_client_id(observed_resources)
    principal_id = get_workload_identity_principal_id(observed_resources)
    storage_account_id = get_storage_account_id(observed_resources)

    uxp_deployed = is_release_deployed(observed_resources, "uxp-release")
    vpa_ready = is_release_deployed(observed_resources, "vpa-release")
    certmanager_ready = is_release_deployed(observed_resources, "certmanager-release")
    gateway_ready = is_release_deployed(observed_resources, "envoy-gateway-release")
    k8gb_deployed = is_release_deployed(observed_resources, "k8gb-release")
    argocd_deployed = is_release_deployed(observed_resources, "argocd-release")
    knative_op_ready = is_release_deployed(observed_resources, "knative-operator-release")
    knative_deps_ready = certmanager_ready and knative_op_ready
    knative_serving_ready = is_knative_serving_ready(observed_resources)
    knative_fully_ready = knative_deps_ready and knative_serving_ready

    license_applied = is_license_applied(observed_resources)
    features_licensed = not license_param or license_applied

    mgr_args = build_manager_args(vpa, knative, vpa_ready, knative_fully_ready, features_licensed)

    storage_account, container_name = parse_blob_location(backup.get("location", ""))
    # The backup StorageAccount may live in a different region than the cluster
    # (cross-region DR). Only the StorageAccount location uses bucket_region;
    # the Azure blob endpoint is account-based, so nothing else needs it.
    bucket_region = backup.get("bucketRegion") or location

    ng_actual_vm_size = get_nodepool_actual_vm_size(observed_resources)
    ng_size_mismatch = bool(ng_actual_vm_size) and ng_actual_vm_size != nodes.get("vmSize", "")
    cluster_name = get_cluster_name(observed_resources)

    # --- Compose resources ---
    add_network_resources(rsp, id_val, location, provider_config, mgmt_policies,
                         network_param, config)
    add_aks_resources(rsp, id_val, location, provider_config, version, nodes,
                     mgmt_policies, config)
    add_uxp_release(rsp, id_val, uxp_version, uxp_deployed, mgr_args, config)
    add_usage_resources(rsp, id_val, config, k8gb_enabled=k8gb_enabled,
                        argocd_enabled=argocd_enabled,
                        k8gb_role_emitted=bool(k8gb_public_ip_id and k8gb_cluster_principal_id))

    # cert-manager is always installed (free component, no license gate) so the
    # k8gb/argocd add-ons can rely on it for Gateway TLS independently of knative.
    add_certmanager_resources(rsp, id_val, certmanager_ready, config)

    # Envoy Gateway is installed only when an add-on needs an HTTP data plane, so
    # plain control planes do not run an idle gateway. Unlike nginx it provisions
    # no cloud LB until a Gateway resource exists.
    if k8gb_enabled or argocd_enabled:
        add_gateway_resources(rsp, id_val, gateway_ready, config)

    # k8gb producer: operator + CoreDNS via a native Azure Standard LB, plus the
    # observe Object that feeds status.controlplane.k8gb.
    if k8gb_enabled:
        add_k8gb_resources(rsp, id_val, k8gb, k8gb_geo_tag, k8gb_ext_geo_tags,
                           k8gb_deployed, location, provider_config,
                           k8gb_public_ip_id, k8gb_cluster_principal_id, config)

    if argocd_enabled:
        add_argocd_resources(rsp, id_val, argocd, argocd_deployed,
                             certmanager_ready, gateway_ready, config)

    if backup.get("enabled") == "yes":
        add_backup_resources(rsp, id_val, location, bucket_region, provider_config,
                            storage_account, container_name,
                            backup, uxp_deployed, client_id, config)

    if backup.get("enabled") == "yes" and oidc_issuer_url and uxp_deployed:
        add_workload_identity_resources(rsp, id_val, location, provider_config,
                                       oidc_issuer_url, storage_account,
                                       container_name, observed_resources,
                                       install_from, client_id, principal_id,
                                       storage_account_id, config)

    if license_param and not license_conflict:
        add_license_resources(rsp, id_val, license_param, config)

    if vpa and vpa.get("enabled") == "yes" and features_licensed:
        add_vpa_resources(rsp, id_val, vpa, vpa_ready, config)

    if knative and knative.get("enabled") == "yes" and features_licensed:
        add_knative_resources(rsp, id_val, knative_op_ready,
                             knative_deps_ready, knative_serving_ready,
                             observed_resources, config)

    if (vpa and vpa.get("enabled") == "yes" and vpa_ready) or \
       (knative and knative.get("enabled") == "yes" and knative_fully_ready):
        add_runtime_config(rsp, id_val, vpa, knative, vpa_ready,
                          knative_fully_ready, config)

    # --- Comprehensive orphan policy ---
    # Every composed managed resource (Helm Release, provider-kubernetes Object,
    # and Azure MRs all carry spec.forProvider) inherits mgmt_policies, so
    # Provision/ObserveOnly never delete the provisioned control plane on
    # teardown. Resources with an explicit policy (backup storage, k8gb CoreDNS
    # observe, knative serving) and composed XRs / Usage guards (no forProvider)
    # are left untouched.
    for _name in list(rsp.desired.resources.keys()):
        _res = resource.struct_to_dict(rsp.desired.resources[_name].resource)
        _spec = _res.get("spec", {})
        if "forProvider" not in _spec or "managementPolicies" in _spec:
            continue
        _res["spec"]["managementPolicies"] = mgmt_policies
        resource.update(rsp.desired.resources[_name], _res)

    update_status(rsp, id_val, params, uxp_version, uxp_deployed, backup,
                 client_id, backup.get("location", ""), observed_resources,
                 nodes, ng_actual_vm_size, ng_size_mismatch, cluster_name, vpa,
                 knative, k8gb, k8gb_geo_tag, k8gb_public_ip, k8gb_deployed, license_conflict, config)
