# configuration-azure-ctp

Azure analog of `configuration-aws-ctp`. Provisions an Azure AKS cluster
configured as an Upbound control plane, installs UXP, and optionally wires
backup (Azure Blob), enterprise license, provider VPA, and Knative.

Translation of AWS → Azure concepts:

| AWS                                    | Azure                                                       |
| -------------------------------------- | ----------------------------------------------------------- |
| EKS cluster + NodeGroup                | AKS KubernetesCluster + KubernetesClusterNodePool           |
| VPC + Subnets                          | ResourceGroup + VirtualNetwork + Subnet                     |
| `region`                               | `location`                                                  |
| `instanceType` (t3.medium)             | `vmSize` (Standard_D2s_v3)                                  |
| IRSA (OIDC + IAM Role + Policy + SA)   | Workload Identity (UserAssignedIdentity + FederatedIdentityCredential + RoleAssignment) |
| S3 Bucket                              | StorageAccount + Container (Blob)                           |
| `eks.amazonaws.com/role-arn` SA anno   | `azure.workload.identity/client-id` SA annotation           |
| `arn:aws:s3:::name`                    | Storage account resource ID + container name                |

## Installation

```bash
# Build the package
up project build

# Install onto an existing UXP control plane via the produced .uppkg
up xpkg push <your-org>/configuration-azure-ctp:<version> -f _output/configuration-azure-ctp.uppkg
# then on the target control plane:
# kubectl apply -f - <<EOF
# apiVersion: pkg.crossplane.io/v1
# kind: Configuration
# metadata:
#   name: configuration-azure-ctp
# spec:
#   package: xpkg.upbound.io/<your-org>/configuration-azure-ctp:<version>
# EOF
```

For local development against a KIND-based dev control plane:

```bash
up project run --local        # spins up a KIND cluster and installs the package
```

### Upper (management) cluster: configure the Azure provider

The management cluster running this package needs an Azure `ProviderConfig`
(namespaced, in the `default` namespace) so the providers can authenticate.
See `examples/install/` for three flavors:

* `azure-providerconfig-secret.yaml` — service principal in a Secret (use this on
  local KIND / any cluster without a trusted OIDC issuer).
* `azure-providerconfig-upbound.yaml` — Upbound's federated OIDC broker
  (use this when the management plane runs on Upbound Cloud Spaces).
* `azure-providerconfig-oidc.yaml` — generic `OIDCTokenFile` (use this when the
  management plane is self-hosted UXP on AKS/EKS/GKE with Workload Identity
  already wired).

Gather the values you'll need for any of the three:

```bash
# Subscription + tenant — needed by every flavor
az account show --query '{subscriptionId:id, tenantId:tenantId, name:name}' -o table

# Service principal for the Secret flavor (long-lived credentials, dev-only)
SUB_ID=$(az account show --query id -o tsv)
az ad sp create-for-rbac --sdk-auth \
  --name upbound-azure-ctp-dev \
  --role Contributor \
  --scopes /subscriptions/$SUB_ID > sp.json
# Paste sp.json into examples/install/azure-credentials.yaml (under stringData.credentials).
# NOTE: Contributor alone is NOT sufficient when backup is enabled — see
# "Required Azure permissions" below.

# UserAssignedIdentity clientID for the Upbound / OIDCTokenFile flavors
az identity create \
  --name upbound-crossplane \
  --resource-group <rg> \
  --location <loc>
az identity show \
  --name upbound-crossplane \
  --resource-group <rg> \
  --query '{clientId:clientId, principalId:principalId}' -o table
```

Apply the chosen ProviderConfig (and the credentials secret if you used the
Secret flavor) once, then proceed to applying `ControlPlane` XRs.

### Required Azure permissions

This package creates resources that span multiple Azure RBAC boundaries.
The principal that authenticates the providers (service principal in the
Secret flavor, UserAssignedIdentity in the OIDC flavors) needs more than
the default `Contributor` role:

| Operation | Resource | Role required |
| --- | --- | --- |
| Create ResourceGroup, VNet, Subnet, AKS, StorageAccount, Container, UserAssignedIdentity, FederatedIdentityCredential | most things | `Contributor` |
| Create **RoleAssignment** (needed by `backup.enabled: yes` to grant the backup identity blob-data access) | `Microsoft.Authorization/roleAssignments` | `Contributor` does **NOT** include this. Need `User Access Administrator` or `Owner`. |
| Register Azure AD applications, if you also create the federated app yourself | Azure AD | Microsoft Graph permissions (separate from subscription RBAC) |

**Symptom if RBAC is insufficient:** the `RoleAssignment` MR sits at
`Synced=False` with a Kubernetes Warning event like:

```text
async create failed: ... 403 Forbidden ... AuthorizationFailed:
The client '<sp-client-id>' does not have authorization to perform
action 'Microsoft.Authorization/roleAssignments/write' over scope ...
```

**Two fixes**, pick whichever fits your security posture:

```bash
# Look up the SP's client ID (same value as appId / clientId from sp.json)
SP_CLIENT_ID=<your-sp-client-id>
SUB_ID=$(az account show --query id -o tsv)

# Option A — least-privilege: grant User Access Administrator on just the
# resource group the ControlPlane XR creates. The RG name equals the XR's
# spec.parameters.id (e.g. "prod-control-plane"), so you may want to grant
# this at the subscription level if you provision multiple control planes.
az role assignment create \
  --assignee $SP_CLIENT_ID \
  --role "User Access Administrator" \
  --scope /subscriptions/$SUB_ID/resourceGroups/<ctp-id>

# Option B — broader, simpler: grant Owner on the whole subscription.
# Owner = Contributor + role-assignment authority.
az role assignment create \
  --assignee $SP_CLIENT_ID \
  --role Owner \
  --scope /subscriptions/$SUB_ID
```

Azure RBAC propagation takes 10–30 seconds. After it lands, Crossplane's
next reconcile of the `RoleAssignment` MR will succeed — no manual XR
action required.

**You can skip this entirely** if `spec.parameters.backup.enabled` is left
at the default `"no"`: the RoleAssignment is gated on backup being on, so
`Contributor` alone is sufficient for the rest of the composition (network,
AKS, UXP, license, VPA, Knative).

### UXP enterprise license: apply ONLY as a Secret on the management cluster

When `spec.parameters.license.secretRef` is set on a `ControlPlane` XR, the
composition copies the license payload from a Secret on the management
(upper) cluster into a Secret on the newly-created downstream (inner) AKS
cluster, then creates a `License` CR there. The downstream cluster's UXP
license-controller validates the signature against the downstream cluster's
node count / cluster type.

**Apply the license JSON ONLY as a Kubernetes Secret on the management
cluster** — one Secret can serve as the source for any number of
`ControlPlane` XRs:

```bash
kubectl create secret generic uxp-license \
  --from-file=license.json=./license.json \
  -n crossplane-system \
  --dry-run=client -o yaml | kubectl apply -f -
```

Then each `ControlPlane` XR references it:

```yaml
spec:
  parameters:
    license:
      secretRef:
        name: uxp-license
        namespace: crossplane-system
```

**Do NOT run `up uxp license apply <license.json>` on the management cluster**
when the license is intended for a downstream control plane. That command
creates **both** a Secret (which is what we need) AND a `License` CR on the
management cluster itself. The License CR on the management cluster is
useless to the composition — the composition only reads the Secret — but
the management cluster's UXP license-controller will still validate the CR
against its own node count / cluster type, and you'll see a (cosmetic)
`LicenseInvalid` status on the management cluster that doesn't reflect what
will happen on the downstream cluster.

If you've already run `up uxp license apply` on the management cluster, you
can safely delete the management-side License CR without breaking anything:

```bash
kubectl delete license.licensing.upbound.io/uxp
```

The composition continues to read the Secret as the source of truth.

#### License-validity gating

The license's embedded `restrictions.clusterType` (e.g. `SingleNodeKind` for
dev licenses) is checked on the **downstream cluster** during validation. A
dev license restricted to single-node Kind clusters will fail validation on
a multi-node AKS regardless of `nodes.count`, because the cluster-type check
is "is this a Kind cluster" — not just "is it single-node". To run the full
backup / VPA / Knative enterprise features on an AKS-backed `ControlPlane`,
you need a license whose embedded claims do not include a Kind-only
restriction. Inspect the embedded claims with:

```bash
kubectl get secret -n crossplane-system uxp-license \
  -o jsonpath='{.data.license\.json}' | base64 -d | python3 -m json.tool
```

Look for `restrictions.clusterType` in the output.

## Inner (AKS) cluster: what you get out of the box

When this composition provisions an AKS cluster, the cluster is configured with:

* `oidcIssuerEnabled: true` — AKS publishes an OIDC issuer URL at
  `status.atProvider.oidcIssuerUrl` that Azure AD trusts natively. Use it
  as the `Issuer` field on any Federated Credential you create on a
  UserAssignedIdentity.
* `workloadIdentityEnabled: true` — the Workload Identity mutating webhook
  is installed, so any pod labelled `azure.workload.identity/use: "true"`
  receives a projected SA token automatically.
* UXP installed in `crossplane-system`. UXP's backup controller already uses
  Workload Identity (wired by `functions/ctp/workload_identity.py`) when
  `spec.parameters.backup.enabled: "yes"`.

This composition deliberately does **not** install Azure providers or a
generic Azure `ProviderConfig` inside the inner cluster: a control plane
created with this package may be used to manage resources in Azure, AWS,
GCP, on-prem, or any mix. Choosing and configuring the right providers is
the user's call. Everything they need is already on the cluster — issuer,
webhook, identity primitives — so wiring Workload Identity for any future
provider is a `kubectl apply` away.

To grab the AKS cluster's OIDC issuer for follow-on Federated Credential
setup:

```bash
ISSUER=$(kubectl get kubernetescluster.containerservice.azure.m.upbound.io \
  <ctp-id> -n default -o jsonpath='{.status.atProvider.oidcIssuerUrl}')
echo "$ISSUER"
```

Then create your federated credential, e.g. for a provider running under
`upbound-system/upbound-provider-<name>` on the inner UXP:

```bash
az identity federated-credential create \
  --identity-name <your-identity> \
  --resource-group <rg> \
  --name <some-name> \
  --issuer "$ISSUER" \
  --subject 'system:serviceaccount:upbound-system:<provider-sa>' \
  --audiences api://AzureADTokenExchange
```

## Add-ons

Optional platform add-ons layered on top of UXP. **cert-manager** is installed
unconditionally on every control plane (a free dependency of Knative/k8gb/ArgoCD
Ingress TLS). **nginx-ingress** is installed only when `k8gb` or `argocd` is
enabled, so plain control planes do not pay for an idle Azure load balancer.

### k8gb (global failover)

When `k8gb.enabled: "yes"`, the control plane becomes a **producer** in the fleet
GSLB architecture (see `docs/ctp-addons-implementation-plan.md`): it installs the
k8gb operator and CoreDNS exposed through an Azure Standard Load Balancer serving
`:53`, and surfaces `status.controlplane.k8gb.coreDNSEndpoint` + `delegationRecord`
for the parent-side FleetGslb aggregator to consume. Parameters: `dnsZone`
(load-balanced zone), `parentZone`, `clusterGeoTag` (defaults to
`azure-<location>-<id>`), and `strategy` (`failover`/`roundRobin`/`geoip`). See
`examples/controlplane/with-k8gb.yaml`. Unlike AWS EKS, AKS's native cloud
provider gives the CoreDNS Service a UDP-capable Standard LB, so no
load-balancer-controller add-on is needed. GSLB is not yet functional end-to-end
— nothing writes the NS delegation until FleetGslb exists.

### ArgoCD

When `argocd.enabled: "yes"`, ArgoCD is installed with a UI Ingress
(`argocd.hostname`, nginx + a self-signed cert-manager Certificate) and a root
app-of-apps `Application` pointing at the public git repo `argocd.url`. See
`examples/controlplane/with-argocd.yaml`.

## Examples

See `examples/controlplane/`:

* `basic.yaml` — minimal AKS control plane with UXP (defaults: AKS K8s
  v1.34, 3× `Standard_D2s_v3` nodes, `eastus`).
* `with-backup.yaml` — full feature set: backup chain with Workload
  Identity, scheduled backups, enterprise license, ProviderVPA, Knative
  function runtime.
* `with-k8gb.yaml` — k8gb global-failover producer (operator + CoreDNS via an
  Azure Standard LB + the `status.controlplane.k8gb` contract).
* `with-argocd.yaml` — ArgoCD add-on (UI Ingress + root app-of-apps).
* `uxp-ctp-1.yaml` — opinionated production-style example (5 nodes,
  `Standard_D4s_v3`, 15-min backup schedule).

The XR's `spec.parameters.version` is constrained to AKS standard-support
Kubernetes versions: `1.32`, `1.33`, `1.34` (default), `1.35`. Earlier
versions (e.g. `1.31`) require AKS Long-Term Support / Premium tier and are
not enumerated. Verify availability in your target region with
`az aks get-versions --location <loc> -o table`.

## Testing

* Composition tests: `up test run tests/test-controlplane` — 21 tests
  covering basic dispatch, backup, license, schedule, VPA, Knative,
  install-from restore, RBAC, namespace targeting, and the k8gb/ArgoCD add-ons.
* E2E (real Azure): `up test run tests/e2etest-controlplane --e2e` — a single
  comprehensive test (`controlplane`) that provisions a real AKS cluster and
  exercises the full stack: UXP + Workload-Identity backup + the k8gb producer +
  ArgoCD (behind the `run-e2e-tests` CI label). Auth uses `source: Upbound`
  federation, which requires one federated identity credential matching the
  subject `mcp:solutions/configuration-azure-ctp-uptest-controlplane:provider:provider-azure`
  on the shared `dare-oidc-provider` app — see the test file header.

The E2E test requires:
- A working Azure `ProviderConfig` named `default` in the `default` namespace
  (see `examples/install/` for the three flavors).
- An Azure principal with `User Access Administrator` (or `Owner`) when
  `backup.enabled: yes` (see "Required Azure permissions" above).
- A `uxp-license` Secret in `crossplane-system` containing a license whose
  embedded `restrictions.clusterType` does NOT restrict to single-node
  Kind clusters.
- Sufficient regional vCPU quota (≥ 20 in the chosen VM family).

## Validation

Validated end-to-end on a fresh `up project run --local` dev control plane
plus a real AKS cluster in Azure `eastus`:

| Capability | Status |
| --- | --- |
| `up project build` | ✓ |
| `up test run tests/test-controlplane` (14 tests) | ✓ pass |
| Provisioning: ResourceGroup → VNet/Subnet → AKS → Helm/Kubernetes ProviderConfigs → UXP installed → backup chain + Workload Identity wired | ✓ XR reaches `Ready=True` in ~12 min on a 3-node `Standard_D2s_v3` cluster |
| Enterprise license activated on inner cluster (`LicenseValid=True, plan: enterprise`) with a non-restricted license | ✓ |
| Scheduled `BackupSchedule` fires and writes blobs to the configured Storage Account container | ✓ (`*/5 * * * *` cadence used during validation) |
| `status.controlplane.backup.lastBackupTime` populates from `BackupSchedule.status.lastBackup` | ✓ |
| Workload Identity end-to-end (AKS OIDC issuer → FederatedIdentityCredential → projected SA token → Azure AD token → blob write) | ✓ |
| Clean teardown: XR delete cascades through MR deletion (deletion-order Usages enforced) and the underlying Azure resource group is fully drained | ✓ |

Known caveats:

1. **`BackupSchedule` does not run on create** — UXP's controller waits
   for the next cron boundary before its first run. Manual `Backup` CRs
   work immediately but don't update the Schedule's `lastBackup`. See
   `../PORTING_NOTES.md` §4.5.
2. **StorageAccount and Container** are emitted with
   `managementPolicies: ["Observe", "Create", "Update", "LateInitialize"]`
   so backup data survives XR deletion. To delete those resources on XR
   teardown, patch the MRs to `["*"]` before deleting the XR.
3. **Inner-cluster license** must be a non-`SingleNodeKind` license to
   activate enterprise features (backup execution, ProviderVPA,
   FunctionKnativeRuntime).

For a cross-package porting reference (gotchas applicable to AWS, GCP, and
future cloud variants), see `../PORTING_NOTES.md`.

## Production readiness

The package is functionally complete and verified end-to-end on a real
AKS cluster. Production deployment additionally requires:

- Human sign-off after review of the XR shape, generated child resources,
  and Azure-side resource provisioning in the deployer's own subscription.
- A production-tier Azure principal (Owner or scoped User Access
  Administrator at the subscription/resource-group level).
- An enterprise UXP license without dev-only restrictions.
- Production vCPU quota for the chosen instance family and region.
