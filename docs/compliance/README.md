# Open Source Compliance and Security Assurance

This directory is the public evidence set for the Open Byte Inc. open source
program that governs the QuantDinger supplied software defined below.

## Program status

| Framework | Status | Evidence |
| --- | --- | --- |
| REUSE Specification | Enforced | [`REUSE.toml`](../../REUSE.toml), [`LICENSES/`](../../LICENSES), and the [REUSE CI workflow](../../.github/workflows/reuse.yml) |
| OpenChain ISO/IEC 5230:2020 | Self-assessed | [Self-assessment](OPENCHAIN_ISO_5230_SELF_ASSESSMENT.md) and [license compliance procedure](LICENSE_COMPLIANCE_PROCEDURE.md) |
| OpenChain ISO/IEC 18974:2023 | Self-assessed | [Self-assessment](OPENCHAIN_ISO_18974_SELF_ASSESSMENT.md) and [security assurance procedure](SECURITY_ASSURANCE_PROCEDURE.md) |

The OpenChain statements describe a **self-assessed management program**, not
an independent certification of the QuantDinger software. Open Byte Inc. is
responsible for the accuracy of those statements. Approval and merge of the
adoption change by the Project Lead records management adoption, participant
acknowledgement, and the effective date of the assessments.

## Defined scope

The program covers:

- source code, documentation, installation material, and release metadata in
  `OpenByteInc/QuantDinger`;
- the public QuantDinger backend container image built from this repository;
- the QuantDinger MCP package and container built from this repository; and
- open source components incorporated into those supplied releases.

The program does not cover separately maintained frontend or mobile
repositories, private company systems, user-modified builds, deployment
infrastructure operated by users, or third-party hosted services. Those items
must be assessed under their own programs before they are represented as
conformant.

## Program documents

- [Open source program](OPEN_SOURCE_PROGRAM.md): policy, scope, roles,
  competency, awareness, resources, metrics, and review control.
- [License compliance procedure](LICENSE_COMPLIANCE_PROCEDURE.md): component
  approval, obligations, distribution, artifacts, inquiries, remediation, and
  contributions.
- [Security assurance procedure](SECURITY_ASSURANCE_PROCEDURE.md): threat and
  vulnerability handling throughout the lifecycle.
- [Release compliance checklist](RELEASE_COMPLIANCE_CHECKLIST.md): release
  evidence and approval record.
- [Program review record](PROGRAM_REVIEW_RECORD.md): periodic review,
  participant acknowledgement, metrics, and corrective actions.
- [ISO/IEC 5230 self-assessment](OPENCHAIN_ISO_5230_SELF_ASSESSMENT.md).
- [ISO/IEC 18974 self-assessment](OPENCHAIN_ISO_18974_SELF_ASSESSMENT.md).

## Public inquiry routes

- License compliance: email `support@quantdinger.com` with subject
  `[Open Source License]`.
- Security vulnerabilities: follow [`SECURITY.md`](../../SECURITY.md) and use
  subject `[Security]`.
- Non-sensitive process corrections: open a regular GitHub issue.

Do not put vulnerability details, credentials, personal data, or confidential
legal material in a public issue.

## Review cadence

The Project Lead reviews this program on material changes and at least once
every 18 months. The current review and next due date are recorded in
[`PROGRAM_REVIEW_RECORD.md`](PROGRAM_REVIEW_RECORD.md).
