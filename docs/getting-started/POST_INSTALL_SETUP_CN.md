# QuantDinger 部署后首次设置

安装完成不等于已经可以安全使用。本清单用于把一个刚启动的实例配置成可研究、可回测、可维护的系统。

## 1. 登录与管理员安全

1. 使用安装时创建的管理员登录。
2. 立即确认邮箱并更换临时或共享密码。
3. 只保留必要管理员；普通使用使用普通账号。
4. 检查系统时间、时区和浏览器时间一致，避免令牌和行情时间异常。

## 2. 站点与部署模式

在 `.env` 和系统设置中确认：

- `FRONTEND_URL` 与实际 Web/H5 域名一致。
- 个人部署使用 `self_hosted`；多用户托管才使用 `saas`。
- 不需要公开注册时设置 `ENABLE_REGISTRATION=false`。
- 服务器无法访问本地桌面券商时设置 `ALLOW_LOCAL_DESKTOP_BROKERS=false`。
- 修改 `.env` 后用 `docker compose up -d` 重建受影响服务。

## 3. AI 与行情数据

- 添加至少一个模型提供商并执行连接测试。
- 模型密钥只存服务端，不复制到浏览器、移动 App 或聊天记录。
- 配置目标市场的数据源，分别验证历史 K 线和最新行情。
- 检查标的、交易所、币种、时区、复权和数据更新时间。

## 4. 通知与身份提供商

按需配置邮件、短信或 Telegram，并发送测试消息。启用 Google/GitHub 登录时，生产回调地址必须与 HTTPS 域名完全一致。相关文档：

- [邮件通知](../deployment/NOTIFICATION_EMAIL_CONFIG_CN.md)
- [短信通知](../deployment/NOTIFICATION_SMS_CONFIG_CN.md)
- [Telegram 通知](../deployment/NOTIFICATION_TELEGRAM_CONFIG_CN.md)
- [OAuth 配置](../deployment/OAUTH_CONFIG_CN.md)

## 5. 账户与交易边界

- 先连接模拟或只读账户，执行连接测试。
- API Key 只授予必要权限，不授予提现权限。
- 核对账户类型、结算币种、时区和最小下单量。
- 第一次部署使用 `signal` 或 paper 模式；不要直接启动真实资金。

## 6. 备份和监控

- 在产生重要数据前完成一次数据库备份和恢复演练。
- 监控容器健康、磁盘、数据库连接、队列积压、Worker 和证书到期。
- 明确升级窗口、回滚版本和日志保留期限。
- 保存 `.env` 的加密备份，但不要与数据库备份放在同一个公开位置。

## 7. 验收

完成以下项目后再交付用户：

- Web 与移动 H5 均能登录，健康检查成功。
- AI 模型和至少一个行情源通过测试。
- 一次研究和一次回测正常完成。
- 通知测试送达，备份可恢复。
- 普通用户无法访问管理员页面或其他用户数据。
- 真实交易保持关闭，直到完成[实盘安全清单](../trading/LIVE_TRADING_SAFETY_CN.md)。

下一步进入[30 分钟上手](QUICKSTART_30_MINUTES_CN.md)或[第一次运行](FIRST_RUN_CN.md)。管理员还应阅读[配置参考](../deployment/CONFIGURATION_REFERENCE_CN.md)、[备份与恢复](../deployment/BACKUP_RESTORE_MIGRATION_CN.md)和[升级与回滚](../deployment/UPGRADE_ROLLBACK_CN.md)。若某一步失败，查看[错误知识库](../deployment/ERROR_KNOWLEDGE_BASE_CN.md)、[安装 FAQ](../deployment/INSTALLATION_FAQ_CN.md)和[管理员设置排障](../deployment/ADMIN_AND_SETTINGS_TROUBLESHOOTING_CN.md)。
