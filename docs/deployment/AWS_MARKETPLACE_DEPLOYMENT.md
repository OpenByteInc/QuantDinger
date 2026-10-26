# AWS Marketplace single-node deployment

This guide applies to the free QuantDinger container product on AWS
Marketplace. The software price is zero. Your AWS account still pays for the
EC2 instance, EBS storage, public IPv4 address, data transfer, and any optional
AWS services that you enable.

The Marketplace delivery option deploys the entire stack to **one EC2
instance**. The six images shown on the product version page are components of
one application: backend and workers, desktop web client, mobile web client,
PostgreSQL, Redis, and Kafka. They do not create six servers.

## Before you launch

You need permission to create CloudFormation, EC2, VPC, IAM, Systems Manager,
and security group resources. Accept the Marketplace terms before launching
the CloudFormation template.

Choose a narrow `AllowedCidr`, normally your current public IP followed by
`/32`, such as `203.0.113.10/32`. The template intentionally has no default for
this parameter. Using `0.0.0.0/0` exposes both HTTP interfaces to the internet;
do not use broad public access until you have placed HTTPS and the appropriate
network controls in front of the application.

The default `t3.large`, 30 GiB system disk, and 100 GiB encrypted persistent
data volume are the minimum recommended starting configuration for the complete
single-node stack. Select a larger instance when running concurrent backtests,
live workers, or larger market-data workloads.

## Launch

1. Open the QuantDinger product in AWS Marketplace and accept the terms.
2. Choose the CloudFormation deployment option for the desired release.
3. Review the template, enter a stack name, restrict `AllowedCidr`, and choose
   the instance type, system volume size, and persistent data volume size.
4. Acknowledge that CloudFormation creates an IAM role, then create the stack.
5. Wait for the stack status to become `CREATE_COMPLETE`. Initial image pulls
   and database migration can take 10-30 minutes. The template does not report
   `CREATE_COMPLETE` until the backend, desktop client, and mobile client have
   all passed their health checks. A failed installation therefore rolls the
   stack back instead of presenting a false success state.

The stack outputs contain:

- `WebUrl` for the desktop web client;
- `MobileWebUrl` for the mobile web client;
- `InstanceId` for Systems Manager administration; and
- `AdminPasswordParameter`, the Parameter Store path containing the generated
  administrator password.

Retrieve the initial password from Systems Manager Parameter Store in the same
region, or use the AWS CLI:

```bash
aws ssm get-parameter \
  --name /quantdinger/STACK_NAME/admin-password \
  --with-decryption \
  --query Parameter.Value \
  --output text
```

Sign in with user `admin` and the retrieved password, then rotate that password
and configure the required broker, market-data, AI, and notification providers.

## Architecture and data

CloudFormation creates an isolated VPC, a public subnet, an internet gateway,
a security group, an IAM instance role, and one Amazon Linux 2023 x86_64 EC2
instance. SSH is not exposed. Use AWS Systems Manager Session Manager for shell
access.

Application configuration, Docker images and named volumes, PostgreSQL data,
Kafka data, and local backups are stored on a separate encrypted EBS data
volume. CloudFormation retains this volume if the stack or EC2 instance is
replaced. Docker uses `/opt/quantdinger/docker-data` rather than the EC2 root
disk, so image pulls and application data do not exhaust the operating-system
volume. The deployment directory is `/opt/quantdinger`; generated secrets are stored in
`/opt/quantdinger/.env` with owner-only permissions.

The deployment also downloads the commit-pinned `.env.example` that matches
the Compose definition. The one-time `env-sync` container uses it to add newly
introduced optional settings without replacing existing values or generated
secrets.

This is a convenient starter and evaluation topology. For production workloads,
add HTTPS, monitoring, off-instance backups, recovery testing, and an
availability design appropriate to your objectives. A single EC2 instance is
not highly available.

## Upgrade

Use the deployment template attached to the new Marketplace version. Do not
manually change image tags to unreviewed values.

1. Accept the new Marketplace version if AWS asks you to do so.
2. Take an EBS snapshot or copy a verified database backup off the instance.
3. In CloudFormation, choose **Update stack**, replace the current template
   with the new versioned template, and review the change set.
4. Confirm that the update changes release parameters and the Systems Manager
   deployment association, but does not replace the EC2 instance or VPC unless
   the release notes explicitly require it.
5. Execute the change set and wait for `UPDATE_COMPLETE`.
6. Verify `WebUrl`, `MobileWebUrl`, login, workers, broker connectivity, and
   application health.

Before changing containers, the update automation writes a compressed
PostgreSQL dump under `/opt/quantdinger/backups` and retains the five most recent
local dumps. It then pulls the immutable images, runs database migration, starts
the stack, and performs health checks. Updating the stack does not reset the
administrator password or application configuration.

## Troubleshooting and rollback

Open the CloudFormation resource events first. The deployment command is an
AWS Systems Manager association, so its detailed output is available in
Systems Manager under **State Manager** or **Run Command**. Connect to the host
with Session Manager and inspect the application when needed:

```bash
cd /opt/quantdinger
sudo docker compose --env-file .env -f docker-compose.yml ps
sudo docker compose --env-file .env -f docker-compose.yml logs --tail=200 migration backend
```

The automation retains `.env.previous`, `docker-compose.yml.previous`, and the
latest database dumps. For an application-only rollback, restore the previous
Compose file and image settings, then start the stack. If a database migration
is not backward compatible, stop application writers and restore the verified
pre-upgrade database dump before starting the previous application release.
Follow the release notes and test restoration before relying on a backup.

If deployment fails because the Marketplace images cannot be pulled, confirm
that the product terms are accepted in the same AWS account and that the
instance role and outbound network access have not been restricted.

## Deletion and cost control

Deleting the CloudFormation stack terminates the EC2 instance and deletes its
system disk, but intentionally retains the encrypted data volume containing
PostgreSQL data and local backups. The `RetainedDataVolumeId` stack output
identifies that volume. Export the data or create a snapshot before manually
deleting it.

Stopping the EC2 instance stops compute charges but EBS and public IPv4 charges
may continue. Deleting the stack releases the public IPv4 address and removes
the other non-retained resources created by this template. The retained data
volume and any snapshots continue to incur normal AWS charges until you delete
them.
