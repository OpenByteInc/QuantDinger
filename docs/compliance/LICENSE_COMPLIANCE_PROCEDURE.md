# Open Source License Compliance Procedure

This procedure applies to the supplied software defined in
[`README.md`](README.md) and implements the policy in
[`OPEN_SOURCE_PROGRAM.md`](OPEN_SOURCE_PROGRAM.md).

## 1. Identify and record components

1. A contributor declares a new or changed dependency in the ecosystem's
   manifest and lock file, Dockerfile, workflow, or other reviewable build file.
2. The reviewer identifies the component, version or immutable reference,
   source, intended use, license expression, notices, and whether it is copied,
   modified, linked, executed, or redistributed.
3. The repository records direct Python dependencies in
   `backend_api_python/requirements*.txt`, MCP dependencies in
   `mcp_server/pyproject.toml`, container inputs in Dockerfiles, and automation
   inputs in `.github/workflows/`.
4. Tagged container builds produce a BuildKit SBOM/provenance attestation tied
   to the published image digest. Git history, the lock file, and the
   attestation together form the release component record.
5. The reviewer archives the decision in the pull request. A material or
   unusual license decision includes the analysis or a link to a controlled
   legal record.

## 2. Review and approve obligations

Before approval, the maintainer determines:

- whether the license permits the planned use and distribution;
- required copyright, license, notice, attribution, source, written offer,
  modification, relinking, patent, or marking obligations;
- whether combining or interacting components creates additional obligations;
- whether a modified component must be identified or its source supplied;
- whether licenses or additional terms are incompatible; and
- what artifact and retention period the release requires.

Straightforward permissive licenses may be approved by a competent maintainer.
Copyleft, source-available, dual-license, non-standard, unclear, or conflicting
terms are escalated to the legal expertise function. No component is approved
by assumption or solely because a package manager can install it.

## 3. Source and binary distribution

### Source releases

The release must preserve the applicable repository license, `LICENSES/`
texts, copyright/license metadata, notices, source changes, and any additional
component obligations. REUSE lint must pass on the release commit.

### Binary and container releases

The release must be traceable to reviewed source and its component inventory.
Required license texts, notices, attributions, source or source offer, and
installation information must be delivered in or alongside the artifact as the
applicable licenses require. The container image is published by the controlled
release workflow with SBOM/provenance and signing evidence.

### Modified, integrated, and interacting components

Modifications remain visible in Git history and are marked when the upstream
license requires it. Vendored or copied code retains upstream notices and
license information. The reviewer evaluates static/dynamic linking, plugins,
generated material, IPC/API boundaries, and combined distribution when they
may affect obligations or compatibility.

## 4. Compliance artifacts

Depending on the component and distribution, compliance artifacts include:

- the project and component license texts;
- copyright, attribution, notice, and modification information;
- corresponding source or a valid source offer;
- REUSE metadata and the REUSE lint result;
- dependency manifests, locks, and release SBOM/provenance;
- the approved release pull request and checklist; and
- legal or remediation records where required.

The release owner verifies delivery and archives the evidence under the
retention rule in the open source program.

## 5. Non-compliance and inquiries

Third parties may email `support@quantdinger.com` with subject
`[Open Source License]`. The Project Lead assigns an owner, acknowledges the
inquiry within five business days, preserves relevant evidence, and determines
whether legal expertise is required.

Suspected non-compliance blocks an affected release. For a distributed release,
the owner assesses impact, corrects metadata or artifacts, provides source or
notices, replaces/removes the component, notifies affected recipients when
appropriate, and documents closure. A disputed interpretation is escalated to
qualified counsel. Confidential or personal information is not placed in the
public issue tracker.

## 6. Contributions to external projects

A contribution must follow the destination project's contribution and license
terms, contain only material the contributor has the right to submit, avoid
Open Byte Inc. confidential information and credentials, and receive Project
Lead approval when made in the course of company work or when it is material to
QuantDinger.

Contributions to QuantDinger use DCO sign-off and are licensed under the
repository's applicable license as described in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md). The contribution policy is
communicated through that guide and the required DCO check.
