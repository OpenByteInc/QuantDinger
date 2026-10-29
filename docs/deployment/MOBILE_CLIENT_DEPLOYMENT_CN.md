# QuantDinger 移动端部署指南

移动端有三种交付方式：直接使用移动 H5、构建 Android App、构建 iOS App。它们都连接已有 QuantDinger 后端，不能替代服务端部署。

## 1. 最省事：移动 H5

标准 Docker 栈已包含移动 H5，默认地址为 `http://127.0.0.1:8889`。公网部署建议配置独立域名，例如 `https://m.example.com`，并将 `/api/` 同源反向代理到后端。

服务器 `.env` 中应包含桌面站和手机站：

```ini
FRONTEND_URL=https://app.example.com,https://m.example.com
```

先在手机浏览器验证登录、OAuth 回跳、研究报告、账户和实盘监控，再考虑打包原生应用。

## 2. 获取移动端源码

```bash
git clone https://github.com/OpenByteInc/QuantDinger-Mobile.git
cd QuantDinger-Mobile
corepack enable
pnpm install --frozen-lockfile
```

常见连接方式：

```ini
VITE_DEFAULT_SERVER_URL=https://api.example.com
VITE_PUBLIC_WEB_BASE_URL=https://m.example.com
```

这些地址必须能从真实手机访问，不能使用手机无法解析的 `localhost`。私有部署地址写入本机 `.env.local` 或 CI 密钥，不要提交到仓库。

## 3. Android

要求 Node.js、pnpm、Android Studio、对应 Android SDK 和 JDK。仓库包含 Android 工程：

```bash
pnpm build:android
pnpm cap:android
```

也可以命令行构建调试包：

```bash
cd android
./gradlew assembleDebug
```

Windows 使用 `gradlew.bat assembleDebug`。调试 APK 位于 `android/app/build/outputs/apk/debug/`。

正式发布必须使用自己的包名、签名密钥、版本号、隐私政策和商店资料。签名密钥不能提交到 Git。

## 4. iOS

iOS 只能在 macOS 上使用 Xcode 构建，并需要 Apple Developer 账号才能真机签名或发布。仓库不提交自动生成的 `ios/` 工程，首次构建先执行：

```bash
pnpm exec cap add ios
pnpm build:ios
pnpm cap:ios
```

在 Xcode 中设置唯一 Bundle Identifier、Team、Signing、版本和最低系统版本，然后在真机验证 HTTPS、OAuth 回跳和通知权限。发布时用 Product → Archive 上传 App Store Connect。

这意味着 Windows 用户可以运行 QuantDinger 服务端和构建 Android，但不能完成 iOS 签名；需要 macOS/Xcode 或受控的 macOS CI 构建机。

## 5. 远端 H5 壳与内置资源

- 远端 H5 壳：Capacitor `server.url` 指向 `https://m.example.com`。页面更新通常无需重新发布 App，但域名必须可信且持续可用。
- 内置资源：构建时把 Vue 资源打进安装包，并用 `VITE_DEFAULT_SERVER_URL` 指向后端。前端更新需要重新发包。

选择一种方式并记录版本策略，不要在不同环境中混用未说明的地址来源。

## 6. 发布前回归清单

- 登录、退出、令牌过期和密码找回。
- OAuth 从系统浏览器返回 App。
- 新增券商的账户识别、连接状态、订单、持仓、快捷交易和币种。
- 跨资产研究报告、数据质量和风险提示折叠卡片。
- 桌面端创建的策略能在手机端查看、暂停并处理异常。
- 深色/浅色主题、多语言、离线与弱网错误提示。
- 不在日志、安装包或崩溃报告中泄露 Token 和交易凭据。

服务端安装见[安装总览](../getting-started/INSTALLATION_CN.md)，公网域名和 HTTPS 见[云服务器部署](CLOUD_DEPLOYMENT_CN.md)。

