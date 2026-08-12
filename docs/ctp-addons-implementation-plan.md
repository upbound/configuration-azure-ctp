# ctp add-ons implementation plan (azure-ctp)

- **Date:** 2026-07-16
- **Status:** Ready for implementation.
- **Scope:** `configuration-azure-ctp` only. One PR, one commit per step,
  composition tests per step, a single installation e2e at the end.
- **Context / why:** this implements the **producer** role of the fleet GSLB/DNS
  design - install the add-ons on the child AKS cluster and surface the k8gb
  status contract that the parent-side FleetGslb aggregator consumes. Children run
  k8gb + CoreDNS with **no external-dns and no DNS credentials**; the parent writes
  the single main-zone delegation. Full decision record (cross-cutting, documented
  in aws-ctp for convenience):
  `configuration-aws-ctp/docs/gslb-dns-architecture.md`. The FleetGslb aggregator
  and resilient-ctp `Gslb` ownership are separate workstreams. **After this PR GSLB
  is not yet functional end-to-end** - nothing writes the NS delegation until
  FleetGslb exists.
- **Azure vs. AWS:** the AWS plan has a dedicated "AWS Load Balancer Controller"
  step because EKS defaults a `type: LoadBalancer` Service to a Classic ELB, which
  cannot do UDP. **AKS does not have this problem** - its built-in cloud provider
  provisions an Azure Standard Load Balancer that supports UDP natively, so **no
  LB-controller add-on and no workload identity are needed here.** That step is
  omitted; this plan is 5 steps.

## Goal of this PR

Extend the `ControlPlane` composition so a child AKS cluster gets:
- **cert-manager** installed **unconditionally** (always-on); the **Envoy
  Gateway** HTTP data plane installed when `k8gb.enabled OR argocd.enabled`,
- **k8gb** (operator + CoreDNS via a native Azure Standard LB) installed when
  `k8gb.enabled`,
- **ArgoCD** (+ UI Gateway/HTTPRoute + a root app-of-apps `Application`) when `argocd.enabled`,
- the **status contract** `status.controlplane.k8gb.coreDNSEndpoint`, `nsName`,
  `glueAddresses`, and `delegationRecord` surfaced for the FleetGslb aggregator to
  consume. `delegationRecord` is a multi-line NS + A record; `coreDNSEndpoint` is
  informational only.

## Repo orientation (for fresh context)

- Composition function in `functions/ctp/` (Python). Entry point
  `function/fn.py::compose(...)`; sibling modules under `function/`: `prelude.py` (helpers), `network.py`,
  `aks.py`, `uxp.py`, `usages.py`, `backup.py`, `workload_identity.py`,
  `licensing.py`, `vpa.py`, `knative.py`, `runtime_config.py`, `status.py`.
  (The step narratives below predate the embedded-layout migration and say
  `main.py` for the dispatcher that conditionally calls each
  `add_*_resources(...)`; that logic now lives in `function/fn.py`. The new
  `function/main.py` is the gRPC serving CLI, a separate file.)
- XRD: `apis/ctp/definition.yaml`. Composition: `apis/ctp/composition.yaml`
  (pipeline: `function-extra-resources` fetches all `ControlPlane`s into
  `allControlPlanes`, then the Python `ctp` function, then `function-auto-ready`).
- **Cloud params:** `spec.parameters.location` (Azure region, e.g. `eastus`);
  there is **no** `project`/`region` param. Cluster metadata is read from the
  composed **AKS XR** `status.aks.{oidcUrl, clusterName, nodes.vmSize}`
  (`prelude.py`). The composed lower configs are XRs:
  `azure.platform.upbound.io/v1alpha1` `Network` and `AKS`.
- **Existing add-on pattern to copy (knative):**
  - Param under `spec.parameters.<feature>.enabled` (`"yes"`/`"no"`).
  - Module `add_<feature>_resources(...)` invoked conditionally from `main.py`.
  - Helm installs use `helm.m.crossplane.io/v1beta1` `Release`; raw manifests use
    `kubernetes.m.crossplane.io/v1alpha1` `Object`.
  - **Child-cluster** resources use `providerConfigRef: {name: <id>, kind: ProviderConfig}`
    (the `id` param); **Azure** MRs use the Azure `provider_config`.
  - **provider-helm stale-Ready workaround:** stamp
    `metadata.annotations["crossplane.io/ready"] = "True"` once the release reports
    `status.atProvider.state == "deployed"` (see `is_release_deployed` in
    `prelude.py` and how `uxp.py`/`knative.py` use it).
  - Every resource passes through `prelude.stamp(...)` (last-reconcile annotation).
- **cert-manager today:** built inside `knative.py` as `knative-certmanager-release`,
  gated in `main.py:151` by `knative.enabled == "yes"` and `features_licensed`;
  readiness read at `main.py:104`
  (`is_release_deployed(observed, "knative-certmanager-release")`).
- **Tests:** composition tests in `tests/test-controlplane/`; e2e in
  `tests/e2etest-controlplane/`.
- **Verify:** `up project build` then `up test run tests/*` (offline). e2e:
  `up test run tests/* --e2e` (CI runs it only on the `run-e2e-tests` label).
- **Skills (mandated):** author Python composition code via
  `control-plane-project:author-composition-python`; verify via
  `control-plane-project:verify-configuration`; run e2e via
  `control-plane-project:e2e-test-configuration`.

## Locked decisions

- cert-manager: **always installed** (not gated). Free component; no license gate.
- nginx-ingress: **Superseded** - replaced by the Envoy Gateway (Gateway API)
  data plane, gated on `k8gb.enabled OR argocd.enabled` (k8gb is now pinned to
  v0.20.0). Historical text below, kept for
  context: **always installed** (not gated). *Caveat: provisions an idle
  Azure LB on baseline control planes that create no Ingress (knative uses its own
  networking, not nginx). If that cost matters, gate behind
  `k8gb.enabled OR argocd.enabled` - open for confirmation.*
- **No AWS-LB-Controller-equivalent step.** AKS provisions an Azure Standard LB
  with UDP support natively; CoreDNS is exposed by a plain `type: LoadBalancer`
  Service. Mixed TCP+UDP on port 53 in one Service relies on the Kubernetes
  `MixedProtocolLBService` gate (GA in 1.26, on by default in supported AKS) - one
  Service with both ports is expected to work; verify on the target AKS version.
- k8gb: gated by `k8gb.enabled`; **CoreDNS exposed via a native Azure Standard LB
  serving UDP:53 (UDP-only)**, **`extdns.enabled: false`** (no external-dns).
  **Pin the k8gb chart to a version whose `Gslb` CRD matches resilient-ctp's
  consumer (`k8gb.absa.oss/v1beta1`; resilient-ctp installs v0.15.0) - do NOT track
  latest**, or the producer/consumer CRD contract drifts. Reuse resilient-ctp's
  k8gb operator values shape (`dnsZones`, `clusterGeoTag`, `extGslbClustersGeoTags`,
  `edgeDNSServers`, `deployCrds/deployRbac`) but **not** its hostNetwork nginx.
- ArgoCD: gated by `argocd.enabled`; params `argocd.hostname` and `argocd.url`
  (public git repo for the root app-of-apps).
- **Teardown: every new child-cluster `Release`/`Object` gets an
  `of: AKS, by: <resource>` `Usage` guard.** Child Objects orphan-finalize if the
  AKS cluster/kubeconfig is deleted first (fleet-wide deletion-ordering gap; only
  the base guards exist today). Applies to k8gb, the CoreDNS observe Object, and
  all argocd Objects.
- No e2e per step; **one** installation e2e at the end, behind `run-e2e-tests`.

## Step 1 - cert-manager decouple (always-on refactor)

- Create `functions/ctp/function/certmanager.py` with `add_certmanager_resources(rsp, id_val, config)`
  that emits the cert-manager `Release` currently built inside `knative.py`
  (chart `cert-manager` from `https://charts.jetstack.io`, `crds.enabled: true`,
  `wait: true`, stale-Ready workaround). Use a stable resource name, e.g.
  `certmanager-release`.
- In `main.py`: call `add_certmanager_resources(...)` **unconditionally** (drops
  both the `knative.enabled` and `features_licensed` gates it lives behind today);
  update the readiness read to
  `certmanager_ready = is_release_deployed(observed_resources, "certmanager-release")`
  (was `"knative-certmanager-release"`).
- In `knative.py`: **remove** the cert-manager `Release`; keep the KnativeServing
  CR gate on `certmanager_ready` (now sourced from the always-on release), i.e.
  separate cert-manager readiness from the knative chain so k8gb/argocd do not
  become coupled to knative.
- Tests: cert-manager `Release` now asserted in the **baseline** case (no knative).
- Verify: `up project build` + `up test run tests/*`.

## Step 2 - nginx-ingress (always-on, new)

**Superseded:** replaced by the Envoy Gateway (Gateway API) data plane, gated on
`k8gb.enabled OR argocd.enabled` (k8gb is now pinned to v0.20.0). Kept below for
historical context.

- Create `functions/ctp/function/ingress.py` with `add_ingress_resources(rsp, id_val, config)`:
  an `ingress-nginx` `Release` (repo `https://kubernetes.github.io/ingress-nginx`,
  pinned + renovate), **standard LoadBalancer service** (not hostNetwork),
  `wait: true`, stale-Ready workaround, child-cluster ProviderConfig.
- Wire unconditionally in `main.py` (subject to the always-on caveat above).
- Tests: nginx `Release` asserted in baseline.
- Verify: build + tests.

## Step 3 - k8gb producer (gated `k8gb.enabled`; defines the status contract)

- **XRD** `apis/ctp/definition.yaml`:
  - `spec.parameters.k8gb`: `enabled` (`yes`/`no`, default `no`), `dnsZone`,
    `parentZone`, `clusterGeoTag` (optional; unique-per-CP default derived
    in-function - see below), `strategy` (`failover`/`roundRobin`/`geoip`,
    default `failover`).
  - `status.controlplane.k8gb`: `enabled`, `coreDNSEndpoint`, `nsName`,
    `glueAddresses`, `delegationRecord`. `delegationRecord` is a multi-line NS + A
    record; `coreDNSEndpoint` is informational only.
- **`functions/ctp/function/k8gb.py`** `add_k8gb_resources(...)`:
  - k8gb `Release` (chart `k8gb`, repo `https://www.k8gb.io`, **version pinned to
    match resilient-ctp's `Gslb` v1beta1 consumer - not latest**): values reuse
    resilient-ctp's operator shape - `k8gb.dnsZones`, `k8gb.clusterGeoTag`,
    `k8gb.extGslbClustersGeoTags`, `k8gb.edgeDNSServers`,
    `k8gb.deployCrds/deployRbac`; plus **`extdns.enabled: false`**. Stale-Ready
    workaround.
  - **Expose CoreDNS via a native Azure Standard LB serving UDP:53 (UDP-only).**
    No LB-controller add-on is needed (AKS handles it). **Verify the exact
    k8gb/coredns chart keys** for LB exposure (e.g. `k8gb.coreDNSExposed` and the
    coredns subchart's `serviceType` / `serviceAnnotations`) - do not assume. For a
    static, glue-stable public IP (deferred, see Assumptions) the mechanism is a
    pre-provisioned Azure Standard `PublicIPAddress` (a provider-azure MR via
    `provider_config`) referenced by
    `service.beta.kubernetes.io/azure-load-balancer-ipv4` (+
    `azure-load-balancer-resource-group` if the IP is in another RG).
  - UDP-only CoreDNS via `coredns.servers[].zones[].use_tcp=false` - verify keys
    against k8gb chart v0.20.0 with `helm template` before the e2e gate.
  - `clusterGeoTag` default must be **unique per control plane** - `azure-<location>`
    collides if two CPs share a location; incorporate the CP `id`
    (e.g. `azure-<location>-<id-suffix>`) or require the param.
  - `extGslbClustersGeoTags` derived from **same-cloud peers** in `allControlPlanes`
    (delivered by `function-extra-resources`, as used for `check_license_conflict`)
    that have k8gb enabled + same `dnsZone`. Cross-cloud peers come from the fleet
    layer (out of scope here); empty is acceptable for a single-cluster start.
    **Ownership seam:** when FleetGslb lands it injects cross-cloud geo-tags -
    decide now whether FleetGslb owns the whole list or only a distinct cross-cloud
    value, so there are not two writers to this Helm value.
  - **Observe-only `Object`** (`managementPolicies: ["Observe"]`) on the child k8gb
    CoreDNS `Service` to read `status.loadBalancer.ingress`.
- **`functions/ctp/function/usages.py`**: add `Usage` guards (`of: AKS, by: ...`) for
  **both** the k8gb `Release` **and** the CoreDNS observe `Object`, emitted only
  when k8gb is enabled.
- **`functions/ctp/function/status.py`**: populate `status.controlplane.k8gb` - `enabled`,
  `coreDNSEndpoint` (from the observed CoreDNS Service Object, informational only),
  `nsName` (the k8gb NS name for this cluster), `glueAddresses` (pinned static
  IP(s) backing the NS glue), `delegationRecord` (a multi-line NS + A record - one
  NS line plus one A line per glue address). **This is the contract the FleetGslb
  aggregator reads - keep field names stable, and make the NS names match k8gb's
  `ClusterNSName`/`ExtClusterNSNames` convention** (not an ad-hoc format), or
  FleetGslb's later writes will not line up. `glueAddresses` must ultimately hold
  the pinned static IP for glue (see the static-IP note in Assumptions).
- **`main.py`**: `if k8gb and k8gb.get("enabled") == "yes": add_k8gb_resources(...)`.
- Tests: k8gb `Release` + CoreDNS observe `Object` + both `Usage`s render when
  enabled, absent when disabled.
- Verify: build + tests.

## Step 4 - argocd (gated `argocd.enabled`; app-of-apps)

- **XRD**: `spec.parameters.argocd`: `enabled` (`yes`/`no`, default `no`),
  `hostname` (UI Ingress host), `url` (public git repo).
- **`functions/ctp/function/argo.py`** `add_argocd_resources(...)`:
  - ArgoCD `Release` (chart `argo-cd`, repo `https://argoproj.github.io/argo-helm`,
    pinned + renovate), `wait: true`, stale-Ready workaround, child ProviderConfig.
  - UI `Ingress` (host `argocd.hostname`, nginx ingress class) with TLS. For a
    standalone CP a local cert-manager `Certificate` is fine; the **global**
    hostname's production cert is issued by the parent and synced down (see the
    GSLB decision record §8) - this PR only needs the UI reachable. Applied via
    provider-kubernetes `Object`(s).
  - **Root `Application` (app-of-apps)** as a provider-kubernetes `Object`
    (`argoproj.io/v1alpha1`, kind `Application`): `spec.source.repoURL = argocd.url`,
    `path: "."`, `targetRevision: HEAD`, destination in-cluster, automated sync.
    **Gate this Object on the ArgoCD release being deployed** (so the Application
    CRD exists), same pattern as the KnativeServing CR gate. Public repo -> no
    repo Secret.
- **`functions/ctp/function/usages.py`**: `of: AKS, by: ...` `Usage` guards for the argocd
  `Release` and each argocd `Object` (Ingress, Certificate, Application), emitted
  only when argocd is enabled.
- **`main.py`**: `if argocd and argocd.get("enabled") == "yes": add_argocd_resources(...)`.
- Tests: ArgoCD `Release`, UI `Ingress`/`Certificate`, root `Application`, and the
  `Usage` guards render when enabled; absent when disabled.
- Verify: build + tests.

## Step 5 - installation e2e (same PR, behind `run-e2e-tests`)

- New `tests/e2etest-addons/` mirroring `tests/e2etest-controlplane/`: a
  `ControlPlane` with `k8gb.enabled: "yes"` (+ `dnsZone`/`parentZone`/`strategy`)
  and `argocd.enabled: "yes"` (+ `hostname`/`url`).
- Assertions (management-plane-visible signals):
  - `Release` MRs **Synced + Ready**: cert-manager, nginx-ingress, k8gb, argocd
    (with `wait: true`, `state=deployed` implies the chart's resources are up).
  - k8gb CoreDNS observe-`Object` shows an LB ingress; XR
    `status.controlplane.k8gb.coreDNSEndpoint` is non-empty. **This exercises the
    real Azure UDP LB - it will fail if the CoreDNS Service is not dual-protocol on
    the target AKS version.**
  - ArgoCD root `Application` `Object` applied (bonus: Synced/Healthy if cheap).
  - XR `Ready=True`.
- **Scope:** this is an *installation* e2e. It does not test DNS failover -
  nothing writes the NS delegation yet (FleetGslb is a separate workstream), so
  GSLB is not functional end-to-end after this PR.
- Run via the `control-plane-project:e2e-test-configuration` skill (wraps
  `up test run --e2e` with monitoring/stuck-detection). Expect 10-20+ min.

## Cross-cutting (every step)

- Update `README.md` parameter table; add `examples/controlplane/with-k8gb.yaml`
  and `examples/controlplane/with-argocd.yaml`.
- No new package dependencies expected - cert-manager/nginx/k8gb/argocd all reuse
  `helm.m.crossplane.io` + `kubernetes.m.crossplane.io` (already used by knative);
  the (deferred) static Public IP reuses the existing Azure provider MRs. Confirm
  they resolve.
- Keep the FleetGslb status contract (`coreDNSEndpoint`, `nsName`, `glueAddresses`,
  `delegationRecord`) stable once defined in Step 3. `delegationRecord` is a
  multi-line NS + A record; `coreDNSEndpoint` is informational only.

## Assumptions / deferred

- Public git repo for ArgoCD (repo credentials Secret deferred).
- Cross-cloud k8gb mesh membership (`extGslbClustersGeoTags` across clouds) is a
  fleet-layer concern; this PR wires only same-cloud peers (or none).
- Stable CoreDNS glue IP (Azure Standard Public IP) - deferred here, but
  **required the moment FleetGslb starts writing glue**: NS glue must be an A
  record (IP), and a dynamic LB IP is not durable. Pin a pre-provisioned Public IP
  when the delegation goes live.
- Cross-cluster CoreDNS reachability on `:53` (peer k8gb + external resolvers) and
  its security posture - a fleet-layer concern, not exercised by this PR.
- aws-ctp / gcp-ctp are the sibling producers; the add-on modules match, but the
  CoreDNS LB layer is cloud-specific (AWS needs the LB controller; Azure/GCP use
  native LBs with a pre-provisioned static IP).
