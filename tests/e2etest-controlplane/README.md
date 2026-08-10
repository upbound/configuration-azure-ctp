# ControlPlane E2E test

Provisions one real AKS ControlPlane and asserts Ready=True (UXP + Workload
Identity backup + k8gb producer + ArgoCD + cert-manager + Envoy Gateway). Uses
Upbound-injected identity; installation-only. Run with
`up test run tests/e2etest-controlplane --e2e`.
