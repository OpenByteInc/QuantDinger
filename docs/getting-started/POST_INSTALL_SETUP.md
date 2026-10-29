# QuantDinger post-installation setup

Installation alone does not make an instance safe or usable. This checklist turns a newly started stack into a maintainable research and backtesting system.

## 1. Administrator security

Sign in with the installer-created administrator, verify the email, replace any temporary or shared password, minimize administrator membership, and confirm system/browser time and time zone.

## 2. Site and deployment mode

- Match `FRONTEND_URL` to the real web and H5 origins.
- Use `self_hosted` for personal systems and `saas` only for multi-user hosting.
- Set `ENABLE_REGISTRATION=false` when public registration is unnecessary.
- Set `ALLOW_LOCAL_DESKTOP_BROKERS=false` when the server cannot reach desktop brokers.
- After `.env` changes, run `docker compose up -d` to recreate affected services.

## 3. AI and market data

Add and test one model provider. Keep model secrets server-side. Configure the target market provider, test historical and current data, and verify instruments, venue, currency, time zone, adjustment, and freshness.

## 4. Notifications and identity

Configure and test required email, SMS, or Telegram channels. Production OAuth callback URLs must exactly match HTTPS origins. See [email](../deployment/NOTIFICATION_EMAIL_CONFIG_EN.md), [SMS](../deployment/NOTIFICATION_SMS_CONFIG_EN.md), [Telegram](../deployment/NOTIFICATION_TELEGRAM_CONFIG_EN.md), and [OAuth](../deployment/OAUTH_CONFIG_EN.md).

## 5. Account and trading boundaries

Begin with paper or read-only accounts. Grant no withdrawal permission, verify account type, settlement currency, time zone, and order minimums, and use `signal` or paper mode for the first deployment.

## 6. Backup and monitoring

Complete a backup and restore drill before valuable data accumulates. Monitor containers, disk, database connections, queue lag, workers, and certificate expiry. Define upgrade, rollback, and log-retention procedures. Store an encrypted `.env` backup separately from public database backups.

## 7. Acceptance

Verify web and H5 login, backend health, one AI and market provider, one research task, one backtest, notification delivery, backup restore, user isolation, and disabled live trading.

Continue with the [30-minute quickstart](QUICKSTART_30_MINUTES.md) or [first run](FIRST_RUN.md). Administrators should also read the [configuration reference](../deployment/CONFIGURATION_REFERENCE.md), [backup and restore](../deployment/BACKUP_RESTORE_MIGRATION.md), and [upgrade and rollback](../deployment/UPGRADE_ROLLBACK.md). If a step fails, use the [error knowledge base](../deployment/ERROR_KNOWLEDGE_BASE.md), [installation FAQ](../deployment/INSTALLATION_FAQ.md), or [administrator troubleshooting](../deployment/ADMIN_AND_SETTINGS_TROUBLESHOOTING_EN.md).
