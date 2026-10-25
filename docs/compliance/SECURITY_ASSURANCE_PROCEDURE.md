# Open Source Security Assurance Procedure

This procedure governs open source security assurance for the supplied software
defined in [`README.md`](README.md). It complements [`SECURITY.md`](../../SECURITY.md),
the [security assessment](../security/SECURITY_ASSESSMENT.md), the
[secure development practices](../security/SECURE_DEVELOPMENT.md), and the
[dependency policy](../security/DEPENDENCY_POLICY.md).

## 1. Component inventory across the lifecycle

Open source components are recorded in reviewed manifests, lock files,
Dockerfiles, workflows, Git history, and release SBOM/provenance attestations.
Changes enter through pull requests and remain traceable to a commit and release
artifact. Tagged container releases retain an image-digest-bound SBOM and
provenance attestation. These records are kept under the program retention rule.

## 2. Threat and vulnerability identification

The project identifies structural and technical threats through architecture
review, the documented threat assessment, code review, authorization and
release-gate tests, CodeQL, Bandit, secret scanning, dependency audit,
Dependabot, SonarQube Cloud, OpenSSF Scorecard, and responsible disclosure.

Known vulnerabilities are detected before and after release through scheduled
security CI, dependency update monitoring, GitHub security facilities, upstream
advisories, and third-party reports. Monitoring applies to the supported
`main` branch and current supplied release.

## 3. Triage, treatment, and records

The security response function:

1. creates a confidential case or GitHub Security Advisory;
2. records the affected component and release, source of the report, severity,
   exploitability, exposure, and available fix;
3. selects fix, mitigation, component replacement/removal, or an explicitly
   approved no-action decision with rationale and review date;
4. assigns an owner and target under the timelines in `SECURITY.md`;
5. verifies the change with appropriate tests and rescans; and
6. records disclosure, release, and closure evidence.

Private case material remains access-controlled. Public advisories, release
notes, upgrade instructions, dependency pull requests, and resolved findings
provide externally appropriate evidence.

## 4. Release security gate

Every supplied release must:

- pass applicable required CI and security checks;
- contain no known unmitigated exploitable medium-or-higher vulnerability;
- resolve or explicitly approve lower or non-exploitable findings with a
  recorded owner, rationale, compensating control, and review date;
- produce the required component inventory/SBOM and provenance;
- preserve signing and traceability to the reviewed commit; and
- complete the release compliance checklist.

A maintainer cannot approve their own undocumented exception. The Project Lead
accepts any residual material risk. A failed or unavailable control blocks the
release unless the Project Lead records an equivalent verification and a
time-bounded corrective action.

## 5. Third-party inquiries and customer communication

Reports use the private route in [`SECURITY.md`](../../SECURITY.md):
`support@quantdinger.com` with subject `[Security]`. The response function
acknowledges, assesses, remediates, and coordinates disclosure according to the
published targets.

When warranted, users receive a GitHub Security Advisory, release note,
security entry, upgrade or mitigation guidance, and affected/fixed version
information. Risk information supplied to customers or partners is limited to
what is accurate, useful, and safe to disclose. Exploitable detail is withheld
until coordinated disclosure is appropriate.

## 6. Continuous improvement and metrics

The Project Lead reviews recurring findings, dependency age, response timing,
release gate exceptions, SBOM coverage, inquiry outcomes, and control failures.
Material changes update the threat assessment, test coverage, workflow, policy,
or staffing. Evidence and actions are entered in
[`PROGRAM_REVIEW_RECORD.md`](PROGRAM_REVIEW_RECORD.md) and the relevant issue,
advisory, or pull request.
