# 升级与回滚

升级应被当作一次可回退的变更，而不是简单执行 `pull`。以下流程适用于 Docker Compose 自托管环境。

## 升级前

1. 阅读目标版本发布说明，确认数据库、配置和券商接口变更。
2. 记录当前 Git 提交、镜像摘要、`IMAGE_TAG` 和服务状态。
3. 按[备份、恢复与迁移](BACKUP_RESTORE_MIGRATION_CN.md)备份数据库、`.env`、`CREDENTIAL_ENCRYPTION_KEY` 和自定义资产。
4. 暂停实盘策略或进入维护窗口，确认没有未处理订单和长任务。
5. 先在预发布环境使用生产备份的脱敏副本演练。

## 标准升级

预构建镜像部署：

```bash
docker compose pull
docker compose up -d migration
docker compose up -d
docker compose ps
```

源码部署还应先拉取指定标签或提交，再按项目构建方式重建镜像。生产环境建议固定版本标签，避免长期使用不可预测的 `latest`。

升级后依次验证：

1. `/api/health` 和容器健康状态。
2. 管理员登录、权限和系统设置。
3. 数据状态、研究、策略编译和一次小范围回测。
4. 券商连接、账户查询和模拟交易。
5. 任务队列、通知、日志、磁盘和错误率。

## 何时回滚

出现以下任一情况，应停止扩大影响并评估回滚：健康检查持续失败、数据库迁移失败、核心页面不可用、订单或账户状态异常、错误率持续升高、关键任务积压。

## 回滚方法

### 仅应用镜像回滚

只有在数据库结构向后兼容时，才可把 `IMAGE_TAG` 恢复到原版本并重新启动：

```bash
docker compose pull
docker compose up -d
```

### 数据库已发生不兼容迁移

1. 停止所有写入服务。
2. 恢复升级前的应用版本和完整数据库备份。
3. 使用同一份 `.env` 和 `CREDENTIAL_ENCRYPTION_KEY`。
4. 在隔离地址验证后再恢复流量。

不要靠手工删除迁移记录“回滚”数据库，也不要执行 `docker compose down -v`。

## 变更记录模板

每次升级至少记录：变更人、时间窗口、旧版本、目标版本、备份位置、迁移结果、验收结果、回滚触发条件和最终结论。生产事故应补充时间线与后续措施。

相关文档：[配置参考](CONFIGURATION_REFERENCE_CN.md) · [兼容性矩阵](COMPATIBILITY_MATRIX_CN.md) · [错误知识库](ERROR_KNOWLEDGE_BASE_CN.md)
