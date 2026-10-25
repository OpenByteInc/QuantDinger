# Open Source Program Review Record

## Current review

| Field | Record |
| --- | --- |
| Review type | Initial adoption and self-assessment |
| Review date | 2026-10-25; effective on Project Lead approval and merge of the adoption change |
| Reviewer | QuantDinger Project Lead (`@brokermr810`) |
| Program scope | Public `OpenByteInc/QuantDinger` supplied software defined in [`README.md`](README.md) |
| Standards | REUSE Specification; OpenChain ISO/IEC 5230:2020; OpenChain ISO/IEC 18974:2023 |
| Outcome | Controls and evidence mapped; self-assessment becomes effective on approval and merge |
| Next review due | 2028-04-24, or earlier after a material program change or failure |

## Initial competence and awareness record

| Participant | Roles | Competence evidence | Awareness evidence | Assessment |
| --- | --- | --- | --- | --- |
| `@brokermr810` | Program Sponsor, Project Lead, Maintainer, security response | Repository administration, release approval, protected-branch and CI operation, OpenSSF Baseline adoption, REUSE and security workflow adoption, coordinated security documentation | Approval and merge of this program; continued privileged access is governed by `MAINTAINERS.md` | Meets role requirements on effective date |

Future participants are added to `MAINTAINERS.md` and their competence and
awareness assessment is recorded in the access-grant or review pull request.

## Initial control evidence

| Metric/control | Initial evidence |
| --- | --- |
| License metadata | `REUSE.toml`, `LICENSES/Apache-2.0.txt`, Apache-2.0 project license |
| Automated license check | Required REUSE workflow and live REUSE status badge |
| Component records | Python requirements and lock, MCP `pyproject.toml`, Dockerfiles, Git history, BuildKit release SBOM/provenance |
| Dependency monitoring | Dependabot and scheduled `pip-audit` |
| Source and secret analysis | CodeQL, Bandit, Gitleaks, SonarQube Cloud |
| Release traceability | Git tag/release, immutable workflow inputs, image digest, provenance, SBOM, and keyless signing |
| Vulnerability intake | Private email route and GitHub Security Advisory process in `SECURITY.md` |
| Finding and exception records | Source scanner/advisory records and linked pull requests; every release-blocking finding must be resolved or formally treated before release |

## Change log

| Date | Change | Review result | Corrective action |
| --- | --- | --- | --- |
| 2026-10-25, effective on adoption merge | Program v1.0, release checklist, and both OpenChain self-assessments adopted | Initial controls mapped to all checklist items | Re-run the formal review on material change and by 2028-04-24 |
