"""E2E test: reconcile the non-Full control planes under controlplanes/.

Loads every controlplanes/*.yaml, keeps those whose managementMode is explicitly
Provision or ObserveOnly, and builds one E2ETest that provisions/adopts them -
they are orphaned on teardown. The credential comes from UP_CLOUD_CREDENTIALS,
which up test injects into the (otherwise isolated) test container; the
managementMode lives in each control-plane file, so nothing else needs to cross
in. See .github/workflows/provision.yaml.
"""

import glob
import os
from pathlib import Path

import yaml
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as k8s
from models.io.upbound.dev.meta.e2etest import v1alpha1 as e2etest

# False -> Provision/ObserveOnly control planes; True -> only the Full ones.
# A file without an explicit managementMode is ignored by both passes.
DECOMMISSION = False

creds = os.environ.get("UP_CLOUD_CREDENTIALS") or os.environ.get("AZURE_CREDS") or ""

# Find the repo's controlplanes/ by walking up (works nested and inside up's
# /project container).
controlplanes = next(
    (p / "controlplanes" for p in Path(__file__).resolve().parents if (p / "controlplanes").is_dir()),
    None,
)

items: list = []
if creds and controlplanes:
    manifests: list[dict] = []
    for path in sorted(glob.glob(str(controlplanes / "*.yaml"))):
        if path.endswith(".tmpl.yaml"):
            continue
        control_plane = yaml.safe_load(Path(path).read_text())
        mode = (
            control_plane.get("spec", {})
            .get("parameters", {})
            .get("managementMode")
        )
        # Act only on an explicit mode; a file without managementMode defaults to
        # Full at the XRD but is ignored here (same guard as provision.yaml).
        wanted = (mode == "Full") if DECOMMISSION else (mode in ("Provision", "ObserveOnly"))
        if not wanted:
            continue
        manifests.append(control_plane)

    if manifests:
        # Secret (base64 SP JSON) + namespaced ProviderConfig, both in default -
        # a namespaced ProviderConfig reads its Secret from its own namespace.
        azure_secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "azure-creds", "namespace": "default"},
            "type": "Opaque",
            "data": {"credentials": creds},
        }
        azure_providerconfig = {
            "apiVersion": "azure.m.upbound.io/v1beta1",
            "kind": "ProviderConfig",
            "metadata": {"name": "default", "namespace": "default"},
            "spec": {
                "credentials": {
                    "source": "Secret",
                    "secretRef": {
                        "namespace": "default",
                        "name": "azure-creds",
                        "key": "credentials",
                    },
                }
            },
        }
        test = e2etest.E2ETest(
            metadata=k8s.ObjectMeta(name="decommission" if DECOMMISSION else "reconcile"),
            spec=e2etest.Spec(
                crossplane=e2etest.Crossplane(
                    autoUpgrade=e2etest.AutoUpgrade(channel="Stable"),
                ),
                defaultConditions=["Ready"],
                manifests=manifests,
                extraResources=[azure_secret, azure_providerconfig],
                skipDelete=False,
                timeoutSeconds=5400,
            ),
        )
        items = [test.model_dump(by_alias=True, exclude_none=True)]

# The test runner expects an "items" array, one entry per test.
print(yaml.dump({"items": items}))
