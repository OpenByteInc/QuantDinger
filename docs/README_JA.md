<div align="center">
  <a href="https://github.com/brokermr810/QuantDinger">
    <img src="screenshots/logo.jpg" alt="QuantDinger Logo" width="220" height="220">
  </a>

  <h1>QuantDinger</h1>
  <h3>プライベート AI クオンツ OS</h3>
  <p><strong>チャート、マルチ LLM リサーチ、Python 戦略、機関級バックテスト、マルチ venue ライブ執行を 1 つの Docker スタックで—完全セルフホスト、自前キー、自前データ。</strong></p>
  <p><em>オープンソース quant OS：AI 支援コーディング → バックテスト → ペーパー → ライブ（crypto / IBKR / MT5 / Alpaca）、Agent Gateway・MCP 内蔵。</em></p>

  <div align="center" style="max-width: 680px; margin: 1.25rem auto 0; padding: 20px 22px 22px; border: 1px solid #d1d9e0; border-radius: 16px;">
    <p style="margin: 0 0 14px; line-height: 1.65;">
      <a href="../README.md"><strong>English</strong></a>
      <span style="color: #afb8c1;"> · </span>
      <a href="README_CN.md"><strong>简体中文</strong></a>
      <span style="color: #afb8c1;"> · </span>
      <a href="README_JA.md"><strong>日本語</strong></a>
      <span style="color: #afb8c1;"> · </span>
      <a href="README_KO.md"><strong>한국어</strong></a>
      <span style="color: #afb8c1;"> · </span>
      <a href="README_TH.md"><strong>ไทย</strong></a>
      <span style="color: #afb8c1;"> · </span>
      <a href="README_VI.md"><strong>Tiếng Việt</strong></a>
      <span style="color: #afb8c1;"> · </span>
      <a href="README_AR.md"><strong>العربية</strong></a>
    </p>
    <p style="margin: 0 0 18px; padding-bottom: 16px; border-bottom: 1px solid #eaeef2; line-height: 2;">
      <a href="https://ai.quantdinger.com"><strong>SaaS</strong></a>
      <span style="color: #d8dee4;"> &nbsp;·&nbsp; </span>
      <a href="https://www.youtube.com/watch?v=tNAZ9uMiUUw"><strong>デモ動画</strong></a>
      <span style="color: #d8dee4;"> &nbsp;·&nbsp; </span>
      <a href="https://www.quantdinger.com"><strong>公式サイト</strong></a>
      <span style="color: #d8dee4;"> &nbsp;·&nbsp; </span>
      <a href="https://aws.amazon.com/marketplace/pp/prodview-naanrb7d2mbc6"><strong>AWS Marketplace</strong></a>
    </p>
    <p style="margin: 0; line-height: 2;">
      <a href="https://t.me/quantdinger"><img src="https://img.shields.io/badge/Telegram-Join-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram"></a>
      &nbsp;
      <a href="https://discord.com/invite/tyx5B6TChr"><img src="https://img.shields.io/badge/Discord-Server-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord"></a>
      &nbsp;
      <a href="https://youtube.com/@quantdinger"><img src="https://img.shields.io/badge/YouTube-%40quantdinger-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="YouTube"></a>
      &nbsp;
      <a href="https://x.com/QuantDinger_EN"><img src="https://img.shields.io/badge/X-%40QuantDinger_EN-000000?style=flat-square&logo=x&logoColor=white" alt="X"></a>
    </p>
  </div>

  <p style="margin-top: 1.45rem; margin-bottom: 10px;">
    <a href="../LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square&logo=apache" alt="License"></a>
    <img src="https://img.shields.io/github/v/release/brokermr810/QuantDinger?style=flat-square&color=orange&label=Version" alt="Version">
    <img src="https://img.shields.io/badge/Python-3.10%2B%20%7C%20Docker%203.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
    <img src="https://img.shields.io/badge/Agent%20Gateway-MCP%20Ready-6f42c1?style=flat-square" alt="Agent Gateway">
    <img src="https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL">
    <img src="https://img.shields.io/github/stars/brokermr810/QuantDinger?style=flat-square&logo=github" alt="Stars">
  </p>
</div>

---

## 目次

[クイックスタート](#クイックスタート) · [技術ハイライト](#技術ハイライト) · [関連リポジトリ](#関連リポジトリ) · [MCP / Agent](#mcp--agent-ゲートウェイ) · [概要](#製品概要) · [機能](#機能ハイライト) · [スクリーンショット](#ビジュアルツアー) · [アーキテクチャ](#アーキテクチャ) · [インストール](#インストールと初回起動) · [ドキュメント](#ドキュメント一覧) · [FAQ](#よくある質問) · [ライセンス](#ライセンス)

---

> QuantDinger は **セルフホスト・ローカルファースト** のクオンツ **OS** です。買いボタン付きチャットボットではなく、**マルチ LLM リサーチ**、**Python ネイティブ戦略**、**サーバサイドバックテスト**、**マルチブローカーライブ**（10+ crypto venue、IBKR、MT5、Alpaca）を 1 つの本番グレードスタックに統合します。

<div align="center">
  <img src="screenshots/ezgif.com-animated-gif-maker.gif" alt="QuantDinger クイックデモ" width="920" style="border-radius: 12px; border: 1px solid #eaeef2;">
  <p><sub><em>ゼロから起動まで—チャート、AI リサーチ、戦略ワークフローを数分で。</em></sub></p>
</div>

<div align="center">
  <img src="screenshots/architecture.png" alt="QuantDinger アーキテクチャ" width="960">
  <p><sub><em>五層エンジンのクローズドループ：<strong>アイデア → インジケータ → 戦略 → バックテスト → 最適化 → 執行 → 監視</strong></em></sub></p>
</div>

## 技術ハイライト

| | QuantDinger の強み |
|---|-------------------|
| **フルスタック quant OS** | チャート、IDE、AI、バックテスト、ライブ bot、クイックトレード、ブローカー管理を 1 製品に。 |
| **Agent ネイティブ** | **Agent Gateway** + PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/) — Cursor / Claude Code / Codex 連携、監査ログ付き。 |
| **二重戦略ランタイム** | `IndicatorStrategy`（ベクトル化シグナル）と `ScriptStrategy`（`on_bar` イベント駆動）。 |
| **マルチ venue** | CCXT crypto、IBKR、MT5、Alpaca — 統合ブローカーアカウントページ。 |
| **本番インフラ** | PostgreSQL 16 + Redis 7、Worker、GHCR マルチアーキイメージ。 |
| **セキュリティ** | デフォルト `SECRET_KEY` 拒否、Agent トークンはハッシュ保存、デフォルトはペーパーのみ。 |

## クイックスタート

**前提:** [Docker](https://docs.docker.com/get-docker/) + Compose v2。**Node.js 不要**（GHCR からフロント取得）。

### 一行インストール（Linux / macOS）

```bash
curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
```

デフォルト `~/quantdinger`。再実行で最新イメージを pull。→ **`http://localhost:8888`**（`quantdinger` / `123456`、直ちにパスワード変更）。

### 標準：リポジトリをクローン（macOS / Linux）

```bash
git clone https://github.com/brokermr810/QuantDinger.git && cd QuantDinger && cp backend_api_python/env.example backend_api_python/.env && chmod +x scripts/generate-secret-key.sh && ./scripts/generate-secret-key.sh && docker-compose up -d --build
```

`docker-compose` が無い場合は `docker compose` を試してください。

### Windows（PowerShell）

**Docker Desktop** を起動し、PowerShell で：

```powershell
git clone https://github.com/brokermr810/QuantDinger.git
Set-Location QuantDinger
Copy-Item backend_api_python\env.example -Destination backend_api_python\.env
$key = & python -c "import secrets; print(secrets.token_hex(32))" 2>$null
if (-not $key) { $key = & py -c "import secrets; print(secrets.token_hex(32))" 2>$null }
if (-not $key) { Write-Error "Python 3 を PATH に入れてください。" }
(Get-Content backend_api_python\.env) -replace '^SECRET_KEY=.*$', "SECRET_KEY=$key" | Set-Content backend_api_python\.env -Encoding utf8
docker-compose up -d --build
```

### Windows（Git Bash）

Git for Windows の Bash なら、上記 macOS/Linux の 1 行コマンドが使えます。

---

ブラウザで **`http://localhost:8888`** を開き、**`quantdinger` / `123456`** でログインし、**直ちに管理者パスワードを変更**してください。詳細は下記 [インストールと初回起動](#インストールと初回起動) を参照。

## 関連リポジトリ

| リポジトリ | 内容 |
|------------|------|
| **[QuantDinger](https://github.com/brokermr810/QuantDinger)**（本倉庫） | バックエンド、Compose、ドキュメント、プリビルド Web |
| **[QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue)** | **Web フロントソース**（Vue）— `v*` タグで `ghcr.io/brokermr810/quantdinger-frontend` を自動発行 |
| **[QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)** | **モバイルクライアント**（オープンソース） |

<h2 id="mcp--agent-ゲートウェイ">MCP / Agent ゲートウェイ</h2>

**Cursor / Claude Code / Codex** など向けに **Model Context Protocol（MCP）** と **Agent Gateway**（`/api/agent/v1`）を提供。詳細は英語ドキュメントが一次情報です：

- **接続レシピ:** [**MCP_SETUP.md**](agent/MCP_SETUP.md) — ホスト版 / セルフホスト、ローカル stdio、リモート HTTP、Claude Code CLI、すべてここに集約。
- [AGENT_QUICKSTART.md](agent/AGENT_QUICKSTART.md) · [AI_INTEGRATION_DESIGN.md](agent/AI_INTEGRATION_DESIGN.md) · [agent-openapi.json](agent/agent-openapi.json)
- MCP サーバー: [`../mcp_server/README.md`](../mcp_server/README.md) · PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/)

**セキュリティ:** 全 Agent 呼び出しは監査ログに記録。取引系（T）トークンはデフォルト **ペーパーのみ**；ライブにはサーバ側 `AGENT_LIVE_TRADING_ENABLED=true` とトークン `paper_only=false` の両方が必要です。

## 製品概要

セルフホスト可能な **AI + Python 戦略 + バックテスト + ライブ** の一体環境。TradingView + Notebook + チャット AI + 取引所 bot の寄せ集めを **1 つの監査可能な Docker スタック** に置き換えます。認証情報は **PostgreSQL** と **`.env`** で管理。

## 機能ハイライト

- **リサーチ & AI** — マルチ LLM、NL→コード、Agent / MCP（scoped token、SSE ジョブ）。
- **構築** — `IndicatorStrategy` / `ScriptStrategy`、プロ K 線 UI。
- **検証** — サーバサイドバックテスト（エクイティ、ドローダウン、約定ログ）。
- **運用** — 10+ crypto、IBKR / MT5 / Alpaca、統合ブローカーアカウント、Telegram / Discord / Webhook。
- **プラットフォーム** — Docker + GHCR、Postgres 16、Redis 7、OAuth、マルチユーザー、課金スイッチ、AWS Marketplace。

## アーキテクチャ

**設計原則：** 行情取得・戦略/バックテスト・執行を分離。Nginx + Vue SPA、Flask + Gunicorn、PostgreSQL 16、Redis 7。デプロイ：`install.sh` 一行、GHCR ゼロ repo、フル repo Compose、AWS AMI、[SaaS](https://ai.quantdinger.com)。

## ビジュアルツアー

<table align="center" width="100%">
  <tr>
    <td colspan="2" align="center">
      <a href="https://www.youtube.com/watch?v=wHIvvv6fmHA">
        <img src="screenshots/video_demo.png" alt="デモ動画" width="80%" style="border-radius: 12px;">
      </a>
      <br/><sub><a href="https://www.youtube.com/watch?v=wHIvvv6fmHA"><strong>▶ デモ動画を見る</strong></a></sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="screenshots/v31.png" alt="IDE" style="border-radius: 6px;"><br/><sub>インジケータ IDE・チャート・バックテスト</sub></td>
    <td width="50%" align="center"><img src="screenshots/v32.png" alt="AI" style="border-radius: 6px;"><br/><sub>AI アセット分析</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="screenshots/v33.png" alt="Bots" style="border-radius: 6px;"><br/><sub>トレーディングボット</sub></td>
    <td align="center"><img src="screenshots/v34.png" alt="Live" style="border-radius: 6px;"><br/><sub>ライブ戦略・パフォーマンス</sub></td>
  </tr>
</table>

## インストールと初回起動

1. リポジトリをクローンし、`cp backend_api_python/env.example backend_api_python/.env`
2. **`SECRET_KEY` を必ず設定**（プレースホルダのままではバックエンドが起動しません）。Linux/macOS: `./scripts/generate-secret-key.sh`
3. `docker-compose up -d --build`
   - **代替（リポジトリ不要）**：プリビルド多架構（amd64/arm64）の backend + frontend を GHCR から直接プルする場合：
     ```bash
     curl -O https://raw.githubusercontent.com/brokermr810/QuantDinger/main/docker-compose.ghcr.yml
     curl -o backend.env https://raw.githubusercontent.com/brokermr810/QuantDinger/main/backend_api_python/env.example
     docker compose -f docker-compose.ghcr.yml up -d
     ```
     デフォルトイメージは `ghcr.io/brokermr810/quantdinger-{backend,frontend}:latest`。両側を同時に固定するならローカル `.env` で `IMAGE_TAG=v3.0.9`、片側だけなら `BACKEND_TAG` / `FRONTEND_TAG` を設定。
   - **フロントエンドのローカル開発**: `QuantDinger-Vue` を `./QuantDinger-Vue/` (gitignore 済) にクローンして `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`。詳細は[英語 README](../README.md#alternative-build-the-frontend-from-vue-source)。
4. **Web:** `http://localhost:8888` · **API ヘルス:** `http://localhost:5000/api/health`
5. 本番前にデフォルト管理者パスワードを変更。`backend_api_python/.env` の **`FRONTEND_URL`** を実際の URL に合わせる。

AI 機能には `env.example` の **AI / LLM** 節を `.env` に反映し、backend を再起動してください。詳細なチェックリストは [英語 README](../README.md) または [简体中文](README_CN.md) を参照。

## ドキュメント一覧

| 文書 | 内容 |
|------|------|
| [English README](../README.md) | 完全版（英語） |
| [简体中文](README_CN.md) | 完全版（中国語簡体字） |
| [CHANGELOG](CHANGELOG.md) | 変更履歴 |
| [Agent クイックスタート](agent/AGENT_QUICKSTART.md)（英語） | Agent Gateway / curl 例 |
| [戦略ガイド（英語）](STRATEGY_DEV_GUIDE.md) | インジケーター／スクリプト戦略の開発 |

その他: [multi-user-setup.md](multi-user-setup.md) · [IBKR](IBKR_TRADING_GUIDE_EN.md) · [MT5](MT5_TRADING_GUIDE_EN.md) — 詳細は英語版が中心です。

## よくある質問

**本当にセルフホストできる？** はい。Docker Compose で自分のインフラ上に展開します。

**暗号だけ？** いいえ。IBKR・Alpaca（米株・ETF・暗号資産）、MT5（FX）にも対応。

**Python で戦略を書ける？** はい。`IndicatorStrategy` と `ScriptStrategy` をサポート。

**商用利用？** バックエンドは **Apache 2.0**。[QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue) フロントは別のソース利用可能ライセンス—商用前に同梱条項を確認してください。モバイルは [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile) のライセンスに従います。

**モバイルは？** [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile) を参照。

## 取引所紹介リンク（参考）

| 取引所 | リンク |
|--------|--------|
| Binance | [登録](https://www.bsmkweb.cc/register?ref=QUANTDINGER) |
| OKX | [登録](https://www.xqmnobxky.com/join/QUANTDINGER) |
| Bybit | [登録](https://partner.bybit.com/b/DINGER) |

## ライセンス

- バックエンド: **Apache License 2.0**（[`../LICENSE`](../LICENSE)）
- 同梱 Web UI: プリビルド配布。ソースは [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue)（別ライセンス）
- 商標: [`../TRADEMARKS.md`](../TRADEMARKS.md)

## 免責事項

QuantDinger は合法的な研究・教育・コンプライアントな取引向けです。**投資助言ではありません。** 利用は自己責任で。

## コミュニティ

- [Telegram](https://t.me/quantdinger) · [Discord](https://discord.com/invite/tyx5B6TChr) · [Issues](https://github.com/brokermr810/QuantDinger/issues)
- Email: [support@quantdinger.com](mailto:support@quantdinger.com)

## Star 履歴

[![Star History Chart](https://api.star-history.com/svg?repos=brokermr810/QuantDinger&type=Date)](https://star-history.com/#brokermr810/QuantDinger&Date)

## 謝辞

Flask、Pandas、CCXT、Vue.js、KLineCharts、ECharts などオープンソースコミュニティに感謝します。

<p align="center"><sub>役に立ったら GitHub Star をお願いします。</sub></p>
