# ControlPlane composition tests

Renders `apis/ctp/composition.yaml` against ControlPlane XR fixtures and asserts the
composed resources. One entry per case in `test/_cases.py`; `test/__main__.py`
wraps each in a CompositionTest and prints the `items` array. Run with
`up test run tests/test-controlplane`.

## Case rationale

`test/_cases.py` is pure data (generated mechanically from the retired
`test.yaml`). The non-obvious cases and what they pin are below; cases not listed
are self-explanatory from their name.

| Case | What it pins |
|---|---|
| `basic` | cert-manager is always installed, decoupled from the knative/license gates. |
| `network-custom` | Custom network params pass straight through to the Network XR. |
| `backup-enabled` | Cloud MRs use a namespaced ProviderConfig in the XR namespace (matching the AKS/Network building blocks), not a ClusterProviderConfig. |
| `backup-cross-region` | The StorageAccount lives in the DR region (`bucketRegion`); the cluster/network stay in the control-plane location. |
| `backup-uxp-deployed` | The OIDC issuer is read from the composed AKS XR's `status.aks.oidcUrl`. |
| `with-knative` | cert-manager is the always-on release, not a knative-scoped one. |
| `availability-zones` | `availabilityZones` and the `1.35` version pass straight through to the AKS XR. |
| `vm-size-drift` | The running vmSize (`Standard_D4s_v3`) differs from the desired (`Standard_D2s_v3`); it is read from `status.aks.nodes` to drive `NodePoolVmSizeImmutable` and `status.controlplane.nodes.currentVmSize` (clusterName from `status.aks.clusterName`). The AKS XR is still composed with the desired vmSize; drift is reported only on status. |
| `with-k8gb` | `nsName` is derived from spec params, so it is emitted even with no observed endpoint. |
| `with-k8gb-endpoint` | The CoreDNS observe Object reports the provisioned Azure Standard LB IP; the XR surfaces the k8gb status contract for FleetGslb. |
| `with-argocd-deployed` | cert-manager deployed -> issuer + certificate render; argocd deployed -> the root Application renders. |
| `management-mode-provision` | Provision maps to a no-Delete policy on the AKS XR and every in-cluster Release/Object (via the comprehensive orphan loop); an explicit observe-only policy (k8gb CoreDNS) is preserved, not overwritten. |
| `management-mode-deprovision` | Deprovision maps to adopt-and-delete (Create/Delete/Observe, no Update) on the AKS XR and every in-cluster Release/Object; the explicit observe-only policy (k8gb CoreDNS) is preserved. |
| `namespaced-backup` | The backup chain (StorageAccount/Container/BackupConfig) always renders once backup is enabled; workload-identity resources require the OIDC issuer plus UXP deployed. |
| `namespaced-k8gb` | The Helm install target and in-cluster target namespace are the fixed child-cluster namespace, not the XR namespace. |
| `namespaced-argocd` | The Helm install target is the fixed child-cluster namespace, not the XR namespace. |
| `namespaced-vpa` | The Helm install target is the fixed child-cluster namespace, not the XR namespace. |
| `with-k8gb-ip-pending` | No PublicIP observed -> `k8gb_public_ip` is empty -> glue must not be populated. |
