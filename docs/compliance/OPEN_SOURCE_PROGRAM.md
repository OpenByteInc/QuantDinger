# Open Byte Inc. QuantDinger Open Source Program

| Field | Value |
| --- | --- |
| Owner | Open Byte Inc. |
| Program lead | QuantDinger Project Lead |
| Version | 1.0 |
| Approval mechanism | Project Lead approval and merge of the adoption change |
| Review frequency | On material change and at least every 18 months |

## 1. Policy

Open Byte Inc. will supply the QuantDinger software within the defined scope
only when:

1. incorporated open source components and their licenses are identified;
2. applicable license obligations, restrictions, rights, notices, source
   availability requirements, and interaction risks have been reviewed;
3. required compliance artifacts accompany or remain available for the
   release;
4. known vulnerability and structural security risks are identified, assessed,
   and treated according to the documented release gates; and
5. evidence of the review is retained.

No schedule or commercial objective overrides an unresolved license obligation,
a prohibited license interaction, or an unaccepted release-blocking security
risk.

## 2. Scope and limits

The scoped supplied software is defined in the [compliance index](README.md).
The Project Lead determines scope by identifying the repositories, build
pipelines, packages, and container images that Open Byte Inc. directly supplies
and controls. A material new package, repository, distribution method, or
acquisition is outside the program until its scope and controls are reviewed
and this statement is updated.

The program does not make assurances about a user's operating system, network,
cloud account, secrets management, modified builds, or a third-party service.
It also does not make frontend or mobile repositories conformant merely because
they interoperate with the scoped software.

## 3. Participants, roles, and responsibilities

| Role or function | Current participant | Responsibilities | Required competence |
| --- | --- | --- | --- |
| Program Sponsor and Project Lead | `@brokermr810`, as listed in [`MAINTAINERS.md`](../../MAINTAINERS.md) | Approve scope, policy, staffing, exceptions, releases, corrective actions, and external statements | Repository governance, release approval, software licensing escalation, risk acceptance, and incident coordination |
| Maintainer | People listed in [`MAINTAINERS.md`](../../MAINTAINERS.md) | Review dependency and source changes, verify release evidence, respond to inquiries, and preserve records | Repository architecture, dependency manifests, CI evidence, license and security procedures, and coordinated disclosure |
| Security response function | Project Lead and authorized maintainers | Triage private reports, coordinate remediation and disclosure, and retain vulnerability action records | Threat assessment, severity and exploitability analysis, secure remediation, and confidential case handling |
| Legal expertise function | Qualified Open Byte Inc. corporate or external counsel engaged by the Project Lead | Interpret licenses, obligations, compatibility, export or regulatory questions, and disputed compliance matters | Appropriate legal qualification and open source licensing experience |
| Contributor | A person submitting a change under [`CONTRIBUTING.md`](../../CONTRIBUTING.md) | Provide accurate origin and license information, sign off commits, and follow review and security rules | Competence relevant to the submitted change and awareness of contribution obligations |

The Project Lead keeps private contact details for external counsel and security
reporters outside the public repository. Engineering does not make a legal
interpretation when qualified legal advice is required. A release with such an
unresolved question is blocked until the legal expertise function resolves it.

## 4. Competency and awareness

Competence is assessed before privileged access is granted and during each
access or program review. The evidence may include prior merged work, completed
review of the program documents, successful operation of the CI and release
process, incident handling, or role-appropriate training. A participant who
does not demonstrate the required competence receives supervised work, limited
access, or training before performing the role independently.

Program participants must know:

- the policy and the documents in this directory;
- the objectives of correct license fulfillment, traceable components,
  vulnerability handling, and trustworthy releases;
- their expected review, escalation, documentation, and response duties; and
- that failure can cause a blocked or withdrawn release, loss of access,
  corrective action, legal exposure, user harm, or security incidents.

The adoption merge records the Project Lead's initial competence assessment and
acknowledgement. Later participants acknowledge the program in the pull request
or access-review record that grants or renews their role. The public participant
and access record remains [`MAINTAINERS.md`](../../MAINTAINERS.md); confidential
training, legal, and incident records are retained in company-controlled
systems.

## 5. Communication

The program is communicated through links in the project README,
documentation index, contribution guide, release checklist, and maintainer
onboarding. Material changes are proposed by pull request and require Project
Lead approval. Maintainers communicate urgent security changes privately until
coordinated disclosure is safe.

## 6. Resources

Open Byte Inc. assigns the Project Lead and required maintainer time to operate
the program and provides the repository, CI, artifact storage, security
advisories, dependency monitoring, and qualified external expertise required
for the scoped releases. If staffing, funding, or expertise is insufficient,
the affected release is delayed or its scope is reduced; conformance is not
claimed for an uncontrolled release.

## 7. Program metrics

Each review records, as applicable:

- REUSE and required CI pass status;
- percentage of tagged releases with component inventory/SBOM evidence;
- open release-blocking license or vulnerability findings;
- vulnerability response and remediation performance against `SECURITY.md`;
- inquiry acknowledgement performance;
- unresolved risk exceptions and corrective actions;
- participant, access, scope, and workflow changes; and
- age of the last formal program review.

## 8. Change, audit, and retention control

Program changes use pull requests so the diff, reviewer, tests, and decision are
recorded. Release evidence is retained in Git history, GitHub Actions, GitHub
Releases, container attestations, security advisories, dependency alerts, and
approved pull requests.

Compliance artifacts and review records are retained for at least six years
after the last distribution of the applicable release, or longer when a
license, law, contract, legal hold, or unresolved matter requires it. Private
records remain access-controlled.

The Project Lead reviews conformance within every 18-month period and after a
material program failure. Results, evidence, deviations, and corrective actions
are recorded in [`PROGRAM_REVIEW_RECORD.md`](PROGRAM_REVIEW_RECORD.md).
