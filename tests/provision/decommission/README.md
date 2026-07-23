# Decommission E2E test

Adopts then deletes every control plane under `controlplanes/` whose
`managementMode` is `Full`, tearing down its Azure resources.
`.github/workflows/provision.yaml` keeps KIND alive and polls until Azure drains.
