# Changelog

QuantDinger uses semantic version tags in the form `vMAJOR.MINOR.PATCH`.
Release notes describe user-visible changes, compatibility considerations, and
security fixes. The canonical release history is available on the
[GitHub Releases page](https://github.com/OpenByteInc/QuantDinger/releases).

## Unreleased

- Added OpenSSF Baseline and Best Practices compliance documentation.
- Added DCO verification for pull requests.
- Added keyless Sigstore signing for published backend container images.
- Tightened default GitHub Actions token permissions.
- Added MartLLM as an OpenAI-compatible LLM provider (`LLM_PROVIDER=martllm`).

## Security changes

Security fixes are identified in release notes when disclosure is safe. Earlier
coordinated disclosures and upgrade guidance are recorded in
[SECURITY.md](SECURITY.md#security-advisories).

For releases predating the automated GitHub Release workflow, the immutable git
tag and repository history remain the authoritative version and change record.

