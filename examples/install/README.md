# Install examples

These manifests configure the Azure provider on the upper (management) cluster
where the `configuration-azure-ctp` package is installed. Create the `platform`
namespace, then pick one of the three credential flavors and apply it once
before applying a `ControlPlane` XR.

| File                              | Source                  | When to use                                                                 |
| --------------------------------- | ----------------------- | --------------------------------------------------------------------------- |
| `azure-providerconfig-secret.yaml`    | `Secret`            | Quickest to set up. Long-lived service-principal secret. Local dev / KIND.  |
| `azure-providerconfig-upbound.yaml`   | `Upbound` (OIDC)    | **Recommended** for Upbound Cloud Spaces control planes — federated, secretless. |
| `azure-providerconfig-oidc.yaml`      | `OIDCTokenFile`     | Self-hosted UXP on a cluster with Workload Identity (AKS/EKS/GKE) already wired. |

All three install a namespaced `ProviderConfig` named `default` in the
`platform` namespace, which matches the default `providerConfigName` on the
`ControlPlane` XR and the namespace the composed managed resources are created
in. Override `spec.parameters.providerConfigName` on the XR to use a different
name.

## Create the namespace

```bash
kubectl apply -f namespace.yaml
```

## Secret-based setup

```bash
cp azure-credentials.yaml.example azure-credentials.yaml
# edit azure-credentials.yaml with your service principal JSON
kubectl apply -f azure-credentials.yaml
kubectl apply -f azure-providerconfig-secret.yaml
```

## Upbound federated OIDC setup

One-time Azure-side setup (per Upbound organization):

1. Create a UserAssignedIdentity in the target subscription.
2. Grant it the roles it needs (Contributor on the subscription, or narrower).
3. Add a Federated Credential on the identity:
   * Issuer  = `https://proidc.upbound.io`
   * Subject = the provider's service-account subject — see
     [Upbound's managed-identities docs][upbound-mi]
   * Audience = `api://AzureADTokenExchange`
4. Copy `clientID`, `tenantID`, `subscriptionID` into
   `azure-providerconfig-upbound.yaml` and apply.

[upbound-mi]: https://docs.upbound.io/concepts/control-planes/configuration/managed-identities/

## OIDCTokenFile setup

Used for self-hosted UXP. See the comments in
`azure-providerconfig-oidc.yaml` — you must pre-wire Workload Identity on the
upper cluster yourself.

## Note on the AKS cluster's own Workload Identity

This configuration *creates* AKS clusters with `oidcIssuerEnabled: true` and
`workloadIdentityEnabled: true`, then wires a UserAssignedIdentity +
FederatedIdentityCredential for the UXP backup controller on the new cluster
(see `functions/ctp/workload_identity.py`). That is independent of the
ProviderConfig manifests in this directory, which authenticate the *upper*
cluster's provider pods to Azure.
