"""99-status — XR status writeback + ClaimConditions.

Aggregates composed-resource readiness, derives feature flags, and surfaces
operator-facing conditions (Ready, NodePoolVmSizeImmutable, LicenseConflict).
"""

from crossplane.function import resource

from .prelude import extract_coredns_endpoint


def update_status(rsp, id_val, params, uxp_version, uxp_deployed, backup,
                 client_id, container_ref, observed, nodes, ng_actual_vm_size,
                 ng_size_mismatch, cluster_name, vpa, knative, k8gb,
                 k8gb_geo_tag, license_conflict, config):
    # rsp.desired.composite.resource is a google.protobuf.Struct — convert
    # so we can read fields out of the partially-built XR.
    xr_dict = resource.struct_to_dict(rsp.desired.composite.resource)
    creation_time = xr_dict.get("metadata", {}).get("creationTimestamp", "")

    status = {
        "controlplane": {
            "created": creation_time,
            "lastReconcileDate": config["last_reconcile_date"],
            "uxp": {
                "version": uxp_version,
                "ready": uxp_deployed
            }
        }
    }

    if backup.get("enabled") == "yes":
        backup_status = {
            "enabled": "yes",
            "containerRef": container_ref
        }
        if client_id:
            backup_status["identityClientId"] = client_id

        schedule_obs = observed.get("backup-schedule")
        if schedule_obs:
            res = schedule_obs.resource if hasattr(schedule_obs, "resource") else schedule_obs
            # UXP's BackupSchedule status field is `lastBackup` (no "Time"
            # suffix) — easy bug to introduce because the value IS a
            # timestamp and many similar APIs use `lastBackupTime`.
            last_backup = (
                res.get("status", {})
                   .get("atProvider", {})
                   .get("manifest", {})
                   .get("status", {})
                   .get("lastBackup")
            )
            if last_backup:
                backup_status["lastBackupTime"] = last_backup

        status["controlplane"]["backup"] = backup_status

    total = 0
    synced = 0
    ready = 0
    synced_and_ready = 0

    for _name, obs_res in observed.items():
        res = obs_res.resource if hasattr(obs_res, "resource") else obs_res
        is_synced = False
        is_ready = False
        for cond in res.get("status", {}).get("conditions", []):
            if cond.get("type") == "Synced" and cond.get("status") == "True":
                is_synced = True
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                is_ready = True

        total += 1
        if is_synced:
            synced += 1
        if is_ready:
            ready += 1
        if is_synced and is_ready:
            synced_and_ready += 1

    status["controlplane"]["resources"] = {
        "total": total,
        "synced": synced,
        "ready": ready,
        "syncedAndReady": synced_and_ready
    }

    if cluster_name:
        status["controlplane"]["clusterName"] = cluster_name

    status["controlplane"]["nodes"] = {
        "vmSize": nodes.get("vmSize", "")
    }
    if ng_actual_vm_size:
        status["controlplane"]["nodes"]["currentVmSize"] = ng_actual_vm_size

    if vpa:
        status["controlplane"]["providerVerticalPodAutoscaling"] = {
            "enabled": vpa.get("enabled", "no")
        }

    if knative:
        status["controlplane"]["knative"] = {
            "enabled": knative.get("enabled", "no")
        }

    if k8gb:
        k8gb_status = {"enabled": k8gb.get("enabled", "no")}
        if k8gb.get("enabled") == "yes":
            endpoint = extract_coredns_endpoint(observed)
            if endpoint:
                dns_zone = k8gb.get("dnsZone", "")
                parent_zone = k8gb.get("parentZone", "")
                # k8gb NS-delegation convention (v0.15.0): the per-cluster NS is
                # gslb-ns-<loadBalancedZone dashed>-<geoTag> under the parent
                # zone; the glue A points it at the CoreDNS endpoint. This is the
                # contract FleetGslb consumes — keep it aligned with k8gb.
                ns_name = f"gslb-ns-{dns_zone.replace('.', '-')}-{k8gb_geo_tag}.{parent_zone}"
                k8gb_status["coreDNSEndpoint"] = endpoint
                k8gb_status["delegationRecord"] = (
                    f"{dns_zone}. NS {ns_name}. ; {ns_name}. A {endpoint}"
                )
        status["controlplane"]["k8gb"] = k8gb_status

    conditions = []

    if synced_and_ready == total and total > 0:
        conditions.append({
            "type": "Ready",
            "status": "True",
            "reason": "Available",
            "message": "Control plane is ready"
        })
    else:
        conditions.append({
            "type": "Ready",
            "status": "False",
            "reason": "Creating",
            "message": f"Waiting for resources: {synced_and_ready}/{total} ready"
        })

    if ng_size_mismatch:
        conditions.append({
            "type": "NodePoolVmSizeImmutable",
            "status": "True",
            "reason": "ImmutableField",
            "message": (
                f"AKS default node pool vmSize is immutable. Current: {ng_actual_vm_size}, "
                f"Desired: {nodes.get('vmSize')}. To change VM size, "
                "provision a new ControlPlane with backup.installFrom pointing to "
                "this control plane's backup."
            )
        })

    if license_conflict:
        conditions.append({
            "type": "LicenseConflict",
            "status": "True",
            "reason": "DuplicateSecret",
            "message": (
                f"License secret is already claimed by ControlPlane "
                f"'{license_conflict}'. Each ControlPlane must use a unique "
                "license secret."
            )
        })

    if conditions:
        status["controlplane"]["conditions"] = conditions

    rsp.desired.composite.resource.update({"status": status})
