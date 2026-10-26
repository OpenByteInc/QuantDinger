from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPOSITORY_ROOT / "deploy" / "aws-marketplace" / "quantdinger-single-node.yaml"
GUIDE = REPOSITORY_ROOT / "docs" / "deployment" / "AWS_MARKETPLACE_DEPLOYMENT.md"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_marketplace_template_uses_immutable_artifacts() -> None:
    template = _template_text()

    assert ":latest" not in template
    assert "raw.githubusercontent.com/OpenByteInc/QuantDinger/" in template
    assert "[0-9a-f]{40}/docker-compose" in template
    for component in ("backend", "frontend", "mobile", "postgres", "redis", "kafka"):
        assert f"2026.10.26-{component}" in template


def test_marketplace_template_does_not_open_administration_ports() -> None:
    template = _template_text()

    assert "FromPort: 22" not in template
    assert "FromPort: 5432" not in template
    assert "FromPort: 6379" not in template
    assert "FromPort: 29092" not in template
    assert "Default: 0.0.0.0/0" not in template
    assert "AssociatePublicIpAddress: false" in template
    assert "AmazonSSMManagedInstanceCore" in template
    assert "DeletionPolicy: Retain" in template
    assert "UpdateReplacePolicy: Retain" in template


def test_marketplace_template_generates_secrets_and_backs_up_before_update() -> None:
    template = _template_text()

    assert "POSTGRES_PASSWORD=$(openssl rand -hex 24)" in template
    assert "SECRET_KEY=$(openssl rand -hex 32)" in template
    assert "CREDENTIAL_ENCRYPTION_KEY=$(openssl rand -hex 32)" in template
    assert "--type SecureString" in template
    assert "pg_dump" in template
    assert "BACKUP_PATH=" in template


def test_marketplace_guide_warns_about_data_deletion_and_aws_costs() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert "software price is zero" in guide
    assert "Deleting the CloudFormation stack" in guide
    assert "retains the encrypted data volume" in guide
    assert "continue to incur normal AWS charges" in guide
    assert "six images" in guide
    assert "one EC2" in guide
