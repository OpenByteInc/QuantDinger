# OpenChain ISO/IEC 18974:2023 Self-Assessment

| Field | Value |
| --- | --- |
| Organization | Open Byte Inc. |
| Program | QuantDinger public supplied-software security assurance program |
| Scope | Defined in [`README.md`](README.md) |
| Checklist basis | OpenChain ISO/IEC 18974 Self-Certification Checklist, Generation 2, Version 1, 2025-08-18 |
| Assessment date | 2026-10-25 |
| Assessment status | Self-assessed; effective when the Project Lead approves and merges the adoption change |
| Review interval | At least every 18 months |

OpenChain conformance is a statement about the scoped organizational program.
It does not guarantee that software is vulnerability-free and is not an
independent certification. Open Byte Inc. is solely responsible for this
self-assessment.

## Evidence matrix

| ID | Official checklist requirement (concise) | Status | Evidence |
| --- | --- | --- | --- |
| 4.1.1-1 | Supplied-software security assurance policy | Yes | [`OPEN_SOURCE_PROGRAM.md`](OPEN_SOURCE_PROGRAM.md), section 1; [`SECURITY_ASSURANCE_PROCEDURE.md`](SECURITY_ASSURANCE_PROCEDURE.md) |
| 4.1.1-2 | Communicate policy to participants | Yes | Program sections 4-5; README and maintainer onboarding |
| 4.1.2-1 | Roles and responsibilities | Yes | Program section 3; [`GOVERNANCE.md`](../../GOVERNANCE.md) |
| 4.1.2-2 | Competencies for each role | Yes | Program section 3 |
| 4.1.2-3 | Participant list and role mapping | Yes | [`MAINTAINERS.md`](../../MAINTAINERS.md); [`PROGRAM_REVIEW_RECORD.md`](PROGRAM_REVIEW_RECORD.md) |
| 4.1.2-4 | Assessed participant competence | Yes | Program review record |
| 4.1.2-5 | Periodic process review and change records | Yes | Program section 8; program review record; pull requests |
| 4.1.2-6 | Verify alignment with current practice and assignments | Yes | Program sections 3 and 8; access and review records |
| 4.1.3-1 | Awareness of policy and location | Yes | Program sections 4-5 |
| 4.1.3-2 | Awareness of objectives | Yes | Program sections 1 and 4 |
| 4.1.3-3 | Awareness of expected contributions | Yes | Program section 4; release checklist |
| 4.1.3-4 | Awareness of non-compliance implications | Yes | Program section 4 |
| 4.1.4-1 | Written scope and limits | Yes | Compliance index and program section 2 |
| 4.1.4-2 | Program performance metrics | Yes | Program section 7 |
| 4.1.4-3 | Evidence from reviews, updates, and audits | Yes | Program section 8; program review record; CI and pull requests |
| 4.1.5-1 | Identify structural and technical threats | Yes | Security procedure section 2; [security assessment](../security/SECURITY_ASSESSMENT.md) |
| 4.1.5-2 | Detect known vulnerabilities | Yes | Security procedure section 2; security CI and Dependabot |
| 4.1.5-3 | Follow up identified vulnerabilities | Yes | Security procedure section 3; [`SECURITY.md`](../../SECURITY.md) |
| 4.1.5-4 | Communicate vulnerabilities to customers when warranted | Yes | Security procedure section 5; advisories and release notes |
| 4.1.5-5 | Post-release monitoring | Yes | Scheduled security CI, Dependabot, and upstream/advisory monitoring |
| 4.1.5-6 | Continuous and repeated pre-release security testing | Yes | Required pull-request security checks and release checklist |
| 4.1.5-7 | Address identified risks before release | Yes | Security procedure section 4 |
| 4.1.5-8 | Export risk information to third parties as appropriate | Yes | Security procedure section 5; SBOM, advisories, and release guidance |
| 4.2.1-1 | Third-party vulnerability inquiry route | Yes | [`SECURITY.md`](../../SECURITY.md) and compliance index |
| 4.2.1-2 | Internal inquiry response procedure | Yes | Security procedure sections 3 and 5; `SECURITY.md` targets |
| 4.2.2-1 | Program people, groups, or functions | Yes | Program section 3; maintainer record |
| 4.2.2-2 | Adequate staffing and funding | Yes | Program section 6 |
| 4.2.2-3 | Expertise for known vulnerabilities | Yes | Program section 3; security response function |
| 4.2.2-4 | Assigned security assurance responsibilities | Yes | Program section 3; security procedure |
| 4.3.1-1 | Continuously record and archive open source components | Yes | Security procedure section 1; manifests, locks, Dockerfiles, SBOM/provenance |
| 4.3.1-2 | Actual component records | Yes | Repository manifests and digest-bound release attestations |
| 4.3.2-1 | Detect and resolve component vulnerabilities | Yes | Security procedure sections 2-4 |
| 4.3.2-2 | Records of findings and actions, including no-action | Yes | Security procedure section 3; advisories, alerts, PRs, and controlled cases |
| 4.4.1-1 | Documentation confirms all requirements are met | Yes | This matrix and linked evidence set |
| 4.4.2-1 | Conformance reviewed within 18 months | Yes | Program section 8; program review record |

## Result

All checklist requirements are mapped to documented controls and evidence.
Approval and merge by the Project Lead adopts the program, records the initial
participant acknowledgement, and makes this self-assessment effective. Public
listing with OpenChain is optional and is a separate organizational submission.

## Source and attribution

This evidence matrix follows the official
[OpenChain ISO/IEC 18974 self-certification checklist](https://openchainproject.org/checklist-iso-18974-2023).
The OpenChain checklist is made available by the OpenChain Project under
CC BY 4.0. Requirement text above is intentionally concise; the official
checklist controls if wording differs.
