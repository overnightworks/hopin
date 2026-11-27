# Release

This runbook is for whoever dispatches a HopIn release: what the release does and where the image goes next.

HopIn runs on the platform (k3s with Flux) from its attested private image in GHCR; the platform's repository owns its manifests and the live settings. [Deployment View](../ARCHITECTURE.md#4-deployment-view) says what runs.

A release is the `Release` workflow, which an agent dispatches with a version `YYYY.MM.DD.N` once review and CI are green. It gates `main`, builds and attests the image in GHCR and tags `vYYYY.MM.DD.N`. The image ref reaches production in a platform pull request the operator merges.
