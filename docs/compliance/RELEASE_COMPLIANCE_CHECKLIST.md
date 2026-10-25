# Release Compliance Checklist

Copy this checklist into the release pull request or other durable release
approval record. Mark non-applicable items `N/A` with a reason. A required item
may not be silently omitted.

## Release identity

- Release/tag:
- Commit:
- Container/package digest:
- Release owner:
- Reviewer:
- Date:

## Scope and component record

- [ ] Release contents are inside the approved program scope.
- [ ] Dependency manifests, locks, Dockerfiles, and workflow references match
      the release.
- [ ] New or changed open source components have a recorded source, version,
      license, intended use, and approval.
- [ ] The component inventory/SBOM and provenance are generated and tied to the
      released digest.

## License compliance

- [ ] REUSE lint passes on the release commit.
- [ ] License obligations, compatibility, interaction, and attribution were
      reviewed for changed components.
- [ ] Modified or vendored components retain required notices and markings.
- [ ] Required license texts, notices, attributions, source/source offer, and
      installation information are delivered with or alongside the release.
- [ ] Any legal question is resolved by the legal expertise function.
- [ ] Compliance artifacts and the approval record are archived.

## Security assurance

- [ ] Required test and security workflows pass.
- [ ] Known vulnerability monitoring results were reviewed.
- [ ] No unmitigated exploitable medium-or-higher vulnerability is known.
- [ ] Every accepted residual risk has an owner, rationale, compensating
      control, review date, and Project Lead approval.
- [ ] Security, upgrade, or disclosure guidance is included when warranted.
- [ ] Artifact provenance and signature verification information are available.

## Approval

- [ ] Release owner confirms the evidence is complete.
- [ ] Reviewer confirms the release satisfies the program or records why it is
      blocked.
- [ ] Project Lead approves any exception or material residual risk.
