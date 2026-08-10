"""E2E test: one real AKS ControlPlane, full stack, asserts Ready=True.

Spins up a real AKS cluster via the composition and exercises the whole stack in
one run: UXP + Workload-Identity backup + the k8gb producer (operator + CoreDNS
via a native Azure Standard LB) + ArgoCD (UI Gateway/HTTPRoute + app-of-apps),
which also pulls in cert-manager (always-on) and the Envoy Gateway data plane.

WHY ONE TEST: e2e auth uses `source: Upbound` workload-identity federation to the
shared AAD app `dare-oidc-provider` (appId bcf40abd-...). Azure federates the
token to a federated identity credential (FIC) whose subject embeds THIS test's
name:
    mcp:solutions/configuration-azure-ctp-uptest-controlplane:provider:provider-azure
The app is at its 20-FIC cap, so keep a SINGLE e2e test named `controlplane` and
one matching FIC (issuer https://proidc.upbound.io, audience
api://AzureADTokenExchange). Do NOT rename or split this test without registering
the new subject(s).

Credentials: the Azure ProviderConfig "default", namespaced in the ControlPlane's
namespace (platform), uses Upbound-injected identity
(clientID/tenantID/subscriptionID = the shared solutions e2e Azure identity, the
same values used by configuration-azure-aks/-network). No pre-provisioned Secret
is required.

Asserts the ControlPlane XR reaches Ready=True; function-auto-ready aggregates
every composed resource, so Ready implies UXP + backup chain + Workload Identity +
every add-on (cert-manager, Envoy Gateway, k8gb + CoreDNS, ArgoCD) came up. The
k8gb CoreDNS endpoint is surfaced on status.controlplane.k8gb.coreDNSEndpoint
(verify non-empty manually - E2ETest cannot assert arbitrary status fields).

Scope: installation only. It does NOT test DNS failover - nothing writes the NS
delegation yet (FleetGslb is a separate workstream). No license is supplied; the
backup wiring and add-ons need none. CoreDNS serving TCP+UDP:53 on one Service
relies on the AKS MixedProtocolLBService gate (GA/on in supported AKS).

managementMode Full (incl. delete) so this correctness test cleans up its Azure
resources; provisioning manifests use Provision to orphan instead. Requires Azure
quota for one Standard_D2s_v3 two-node AKS cluster plus the add-on load balancers.
Expected runtime: 40-70 minutes.
"""

import yaml
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as k8s
from models.io.upbound.dev.meta.e2etest import v1alpha1 as e2etest

test = e2etest.E2ETest(
    metadata=k8s.ObjectMeta(name="controlplane"),
    spec=e2etest.Spec(
        crossplane=e2etest.Crossplane(
            autoUpgrade=e2etest.AutoUpgrade(channel="Stable"),
        ),
        defaultConditions=["Ready"],
        timeoutSeconds=5400,
        cleanupTimeoutSeconds=1800,
        extraResources=[
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "platform"},
            },
            {
                "apiVersion": "azure.m.upbound.io/v1beta1",
                "kind": "ProviderConfig",
                "metadata": {"name": "default", "namespace": "platform"},
                "spec": {
                    "credentials": {"source": "Upbound"},
                    "clientID": "bcf40abd-283c-494b-b186-03d6c864be51",
                    "tenantID": "b9925bc4-8383-4c37-b9d2-fa456d1bb1c7",
                    "subscriptionID": "038f2b7c-3265-43b8-8624-c9ad5da610a8",
                },
            },
        ],
        manifests=[
            {
                "apiVersion": "azure.platform.upbound.io/v1alpha1",
                "kind": "ControlPlane",
                "metadata": {"name": "e2e-test-cp", "namespace": "platform"},
                "spec": {
                    "parameters": {
                        "id": "e2etestcp",
                        "location": "eastus",
                        "version": "1.34",
                        "managementMode": "Full",
                        "nodes": {"count": 2, "vmSize": "Standard_D2s_v3"},
                        "backup": {
                            "enabled": "yes",
                            "location": "e2etestcpbackup/uxp-backups",
                        },
                        "k8gb": {
                            "enabled": "yes",
                            "dnsZone": "gslb.example.com",
                            "parentZone": "example.com",
                            "strategy": "failover",
                        },
                        "argocd": {
                            "enabled": "yes",
                            "hostname": "argocd.example.com",
                            "url": "https://github.com/argoproj/argocd-example-apps",
                        },
                    },
                },
            },
        ],
        skipDelete=False,
    ),
)

# The test runner expects an "items" array, one entry per test.
item = test.model_dump(by_alias=True, exclude_none=True)
# Strip the two model-default fields the retired test.yaml never carried, so the
# emitted E2ETest is field-for-field identical to it (yaml.dump sorts keys, so the
# byte order differs, but no field is added or dropped; both defaults equal the
# platform values anyway): spec.crossplane.state == "Running",
# spec.setupTimeoutSeconds == 600
item["spec"]["crossplane"].pop("state", None)
item["spec"].pop("setupTimeoutSeconds", None)
print(yaml.dump({"items": [item]}))
