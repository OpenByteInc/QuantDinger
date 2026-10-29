# QuantDinger mobile client deployment

QuantDinger supports mobile H5, Android, and iOS delivery. Every option connects to an existing QuantDinger backend; none replaces the server deployment.

## 1. Mobile H5

The standard Docker stack includes mobile H5 at `http://127.0.0.1:8889`. For production, use a dedicated origin such as `https://m.example.com` and proxy `/api/` to the backend on the same origin.

```ini
FRONTEND_URL=https://app.example.com,https://m.example.com
```

Validate sign-in, OAuth return, reports, accounts, and live monitoring in a mobile browser before packaging native apps.

## 2. Get the source

```bash
git clone https://github.com/OpenByteInc/QuantDinger-Mobile.git
cd QuantDinger-Mobile
corepack enable
pnpm install --frozen-lockfile
```

Typical endpoints are:

```ini
VITE_DEFAULT_SERVER_URL=https://api.example.com
VITE_PUBLIC_WEB_BASE_URL=https://m.example.com
```

They must be reachable from a real phone. Keep private deployment URLs in `.env.local` or CI secrets, not Git.

## 3. Android

Install Node.js, pnpm, Android Studio, the required Android SDK, and JDK. The repository includes the Android project:

```bash
pnpm build:android
pnpm cap:android
```

For a debug APK, run `./gradlew assembleDebug` under `android` (`gradlew.bat` on Windows). Production releases require your package ID, signing key, version, privacy policy, and store metadata. Never commit signing keys.

## 4. iOS

iOS requires macOS, Xcode, and an Apple Developer account for device signing or distribution. The generated `ios/` project is intentionally not committed. Create it once:

```bash
pnpm exec cap add ios
pnpm build:ios
pnpm cap:ios
```

Set a unique Bundle Identifier, Team, signing, version, and deployment target in Xcode. Test HTTPS, OAuth return, and permissions on a device, then use Product, Archive for App Store Connect.

Windows can host QuantDinger and build Android, but cannot complete iOS signing. Use a Mac or controlled macOS CI host.

## 5. Remote shell versus bundled assets

- Remote H5 shell: Capacitor `server.url` loads `https://m.example.com`; normal UI updates may not require an app release.
- Bundled assets: Vue assets ship inside the app and `VITE_DEFAULT_SERVER_URL` points to the backend; UI changes require a new release.

Choose and document one versioning policy per environment.

## 6. Release regression

Test authentication and expiry, OAuth return, broker identity and trading surfaces, cross-asset reports and risk fields, live-strategy control, themes, localization, weak-network errors, and secret redaction.

See the [installation overview](../getting-started/INSTALLATION.md) and [cloud deployment](CLOUD_DEPLOYMENT_EN.md) for the server side.

