# Voice Clone model index: repository guide

Source review: 21 November 2026. This is a source map, not a claim about live deployment health.

main is an index and deployment-contract branch. It does not contain a self-contained inference server or publish a model image.

## Source map

| Path | Purpose |
| --- | --- |
| [`deployment/image-contract.json`](../deployment/image-contract.json) | Branch role, fixed service slots and image selection contract |
| [`scripts/resolve_salad_image.py`](../scripts/resolve_salad_image.py) | Read-only image-contract validator/resolver |
| [`docs/SHARED_SALAD_ARCHITECTURE.md`](../docs/SHARED_SALAD_ARCHITECTURE.md) | Model-switch compatibility and rollback requirements |
| [`.github/workflows/deployment-contract.yml`](../.github/workflows/deployment-contract.yml) | Offline contract validation |

## Development and validation

Python 3.11 or newer; no third-party packages are needed for the index validator. Run python3 scripts/resolve_salad_image.py --validate --branch main. For inference, inspect the selected model branch and its own dependencies and image workflow first.

## Build and storage policy

The target retention policy is 3 days for PR/test packages, 7 days for ordinary development candidates and sanitized failure diagnostics, and versioned GitHub Releases for supported downloads. The workflow's explicit retention-days is authoritative; this document alone does not change expiry. Release evidence consumed by another workflow needs its own retention exception.

Before retiring any artifact, identify its full source SHA and active consumers. Preserve active integration candidates and the current and previous verified release outside short-lived Actions storage. A candidate that needs longer testing must be copied to a named prerelease with checksums before expiry. No automatic artifact, branch, release or package deletion is introduced by this guide.

Heavy Windows/container packaging should use explicit release or manual triggers where source tests can run independently. Existing frozen-binary, media and compatibility tests remain release gates. Do not cancel an in-progress publication or deployment just to save CI minutes.

## Release record

For each release record: component/version, full source SHA, package SHA256 or container digest, relevant dependency/model versions, exact verification results, unresolved platform acceptance, and previous verified version/rollback procedure. Use sanitized screenshots from the actual app when available; no fabricated UI or unverified production claims.

## Maintenance checklist

- Keep README setup instructions and app/version mappings aligned with the source.
- Review dependency updates with the affected tests; do not enable blind automatic merges.
- Keep workflow token permissions scoped to the job that needs them.
- Inspect repository rules, environment protection and available security features before proposing account-setting changes.
- Keep historical research labelled and separate from the current source contract.
- Use the [contribution guide](../CONTRIBUTING.md) and issue/PR templates for review.
