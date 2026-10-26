# AWS Marketplace deployment assets

This directory contains the deployment template used by the QuantDinger AWS
Marketplace delivery option.

## Delivery model

`quantdinger-single-node.yaml` creates one Amazon Linux 2023 EC2 instance in a
dedicated VPC and runs the complete QuantDinger container stack on that host.
The six container images are application components, not six EC2 instances.

The stack:

- exposes the desktop and mobile web clients only to the configured CIDR;
- keeps PostgreSQL, Redis, Kafka, and the backend port on loopback interfaces;
- uses Systems Manager Session Manager instead of opening SSH;
- generates deployment secrets and stores the initial administrator password
  as a SecureString in Systems Manager Parameter Store;
- creates a PostgreSQL logical backup before each version update; and
- uses immutable Marketplace image tags and a commit-pinned Compose file.

## Release procedure

1. Update the image tags, `ReleaseVersion`, and `ComposeUrl` in the template.
2. Validate the template and perform an end-to-end deployment in a disposable
   AWS account or isolated test VPC.
3. Verify first install, administrator login, service health, backup creation,
   in-place update, and documented rollback.
4. Upload the validated template to the public, versioned release bucket.
5. Attach that immutable object URL as the AWS Marketplace deployment resource.
6. Never overwrite a previously published template object. Publish a new
   versioned key for every Marketplace release.

The customer guides are:

- [English](../../docs/deployment/AWS_MARKETPLACE_DEPLOYMENT.md)
- [简体中文](../../docs/deployment/AWS_MARKETPLACE_DEPLOYMENT_CN.md)

