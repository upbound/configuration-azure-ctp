# Azure AKS Control Plane composition function

The Python composition function for `configuration-azure-ctp`. Entrypoint:
`function.main:cli`; composition logic in `function/fn.py` (`compose`), with one
sibling module per composed section (network, aks, uxp, backup, k8gb, argo, ...).
