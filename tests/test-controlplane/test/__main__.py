"""Generate the ControlPlane CompositionTests and print them as YAML."""

import yaml
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as k8s
from models.io.upbound.dev.meta.compositiontest import v1alpha1 as compositiontest

from test._cases import CASES

items = []
for case in CASES:
    test = compositiontest.CompositionTest(
        metadata=k8s.ObjectMeta(name=case["name"]),
        spec=compositiontest.Spec(**case["spec"]),
    )
    items.append(test.model_dump(by_alias=True, exclude_none=True))

# The test runner expects an "items" array, one entry per test.
print(yaml.dump({"items": items}))
