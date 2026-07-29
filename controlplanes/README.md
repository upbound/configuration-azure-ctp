# Provisioned control planes

Each `*.yaml` here is one persistent Azure AKS+UXP control plane, declared as a
`ControlPlane` XR (the desired state) - just the XR, no `E2ETest` boilerplate and
no credentials.

Each file's `spec.parameters.managementMode` decides its lifecycle. The
provisioning pipeline makes two passes over this folder, keying on the **explicit**
mode (a file without a `managementMode` is ignored by both):

- **reconcile** - control planes set to `Provision` or `ObserveOnly`:
  created/adopted/updated, then orphaned on teardown.
- **decommission** - control planes set to `Deprovision`: adopted, then deleted (Azure
  torn down).

`tests/provision/reconcile` and `tests/provision/decommission` (Python E2E tests)
each load this folder and keep only their subset. The credential comes from
`UP_CLOUD_CREDENTIALS`, which `up test` injects into the test container.

## Rules

- Every `ControlPlane` file here must set `metadata.namespace: default`, matching
  the pipeline's ProviderConfig namespace - a namespaced ProviderConfig resolves
  credentials from its own namespace. The pipeline runs on an ephemeral bootstrap
  cluster where `default` always exists, so it never depends on a pre-created
  namespace (these static files can't create one).
- `parameters.id` is the Azure naming key - short, lowercase, alphanumeric,
  stable (drives `{id}-rg`, `{id}-vnet`, `{id}-sn`, `{id}-aks`, and the
  decommission poll keys on it). Changing it provisions a new control plane
  instead of adopting the existing one.
- `parameters.managementMode` (required here): `Provision` (create + adopt +
  update, never delete - the steady state for a persistent control plane),
  `ObserveOnly` (adopt + watch, no changes), `Deprovision` (decommission - see below).
  Omitting it defaults to `Full` at the XRD (standard lifecycle, not acted on by the
  pipeline), so always set an explicit mode here.
- Immutable AKS fields (`nodes.vmSize`, `nodes.availabilityZones`) reprovision via
  the backup + `installFrom` path, not in place.

## Add a control plane

    cp controlplanes/cp1.yaml controlplanes/<name>.yaml
    # edit metadata.name, a unique id, location, nodes, add-ons; set managementMode.

## Run locally

Running e2e is a manual, owner-driven step (real Azure). Export the base64
service-principal credential as `UP_CLOUD_CREDENTIALS` (what `up test` forwards
into the container), then run the pass you want:

    export UP_CLOUD_CREDENTIALS="$(cat /path/to/sp-credentials.base64)"   # base64 SP JSON
    up test run tests/provision/reconcile    --e2e --local   # Provision/ObserveOnly
    up test run tests/provision/decommission --e2e --local --skip-control-plane-cleanup

## Decommission (`managementMode: Deprovision`)

Set a control plane's `managementMode` to `Deprovision`, then run the decommission pass.
`up test`'s delete phase returns as soon as the composite XR is
background-collected (< 1s), long before the ~15-min Azure cascade
(add-ons/releases -> AKS -> network -> resource group) finishes, so keep KIND
alive with `--skip-control-plane-cleanup` and wait until it drains:

    up test run tests/provision/decommission --e2e --local --skip-control-plane-cleanup
    kubectl get managed -A   # repeat until empty

`.github/workflows/provision.yaml` does this automatically: it polls the
kept-alive KIND until no managed resources remain (~30-min ceiling). Blunt
alternative: `az group delete -n <id>-rg`.

## After a decommission: delete the file

Optionally confirm deletion on the Azure side first:

    az group show -n <id>-rg   # want: ResourceGroupNotFound

Then **delete the control plane's file**:

    git rm controlplanes/<name>.yaml

A file left at `Deprovision` is **not inert**. The policy keeps `Create`, so the
next dispatch re-creates the entire control plane, waits for `Ready`, and destroys
it again - ~30 min of real Azure spend, reported as success.

`Create` cannot be dropped to prevent this. Crossplane defines no Delete-capable
managementPolicies combination that omits `Create` - a delete-without-create policy
is not a supported [combination](https://docs.crossplane.io/latest/managed-resources/managed-resources/#managementpolicies).
It is also the adopt path: the bootstrap KIND holds no managed-resource state, and
the composed Azure MRs carry no `crossplane.io/external-name` (neither
configuration-azure-network nor -aks sets one). Removing the file is the only safeguard.
