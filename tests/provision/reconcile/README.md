# Reconcile E2E test

Provisions/adopts/updates every control plane under `controlplanes/` whose
`managementMode` is `Provision` or `ObserveOnly`; they are
orphaned on teardown. Driven by `.github/workflows/provision.yaml`.
