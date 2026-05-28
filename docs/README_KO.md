<div align="center">
  <a href="https://github.com/brokermr810/QuantDinger">
    <img src="screenshots/logo.jpg" alt="QuantDinger Logo" width="220" height="220">
  </a>

  <h1>QuantDinger</h1>
  <h3>프라이빗 AI 퀀트 운영체제</h3>
  <p><strong>차트, 멀티 LLM 리서치, Python 전략, 기관급 백테스트, 멀티 venue 라이브 실행을 하나의 Docker 스택으로—완전 셀프호스트, 내 키, 내 데이터.</strong></p>
  <p><em>오픈소스 quant OS: AI 보조 코딩 → 백테스트 → 페이퍼 → 라이브(crypto/IBKR/MT5/Alpaca), Agent Gateway·MCP 내장.</em></p>

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
      <a href="https://www.youtube.com/watch?v=tNAZ9uMiUUw"><strong>데모 영상</strong></a>
      <span style="color: #d8dee4;"> &nbsp;·&nbsp; </span>
      <a href="https://www.quantdinger.com"><strong>공식 사이트</strong></a>
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

## 목차

[빠른 시작](#빠른-시작) · [기술 하이라이트](#기술-하이라이트) · [관련 저장소](#관련-저장소) · [MCP / Agent](#mcp--agent-게이트웨이) · [개요](#제품-개요) · [기능](#기능-하이라이트) · [스크린샷](#비주얼-투어) · [아키텍처](#아키텍처) · [설치](#설치-및-첫-실행) · [문서](#문서-목록) · [FAQ](#자주-묻는-질문) · [라이선스](#라이선스)

---

> QuantDinger는 **셀프호스트·로컬 우선** 퀀트 **OS**입니다. 매수 버튼 챗봇이 아니라 **멀티 LLM 리서치**, **Python 네이티브 전략**, **서버 사이드 백테스트**, **멀티 브로커 라이브**(10+ crypto venue, IBKR, MT5, Alpaca)를 하나의 프로덕션급 스택에 통합합니다.

<div align="center">
  <img src="screenshots/ezgif.com-animated-gif-maker.gif" alt="QuantDinger 빠른 데모" width="920" style="border-radius: 12px; border: 1px solid #eaeef2;">
  <p><sub><em>제로에서 실행까지—차트, AI 리서치, 전략 워크플로를 몇 분 만에.</em></sub></p>
</div>

<div align="center">
  <img src="screenshots/architecture.png" alt="QuantDinger 아키텍처" width="960">
  <p><sub><em>5계층 엔진 폐쇄 루프: <strong>아이디어 → 인디케이터 → 전략 → 백테스트 → 최적화 → 실행 → 모니터링</strong></em></sub></p>
</div>

## 기술 하이라이트

| | QuantDinger의 강점 |
|---|-------------------|
| **풀스택 quant OS** | 차트, IDE, AI, 백테스트, 라이브 bot, 퀵 트레이드, 브로커 관리를 한 제품에. |
| **Agent 네이티브** | **Agent Gateway** + PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/) — Cursor / Claude Code / Codex, 감사 로그. |
| **이중 전략 런타임** | `IndicatorStrategy`(벡터 시그널)와 `ScriptStrategy`(`on_bar` 이벤트). |
| **멀티 venue** | CCXT crypto, IBKR, MT5, Alpaca — 통합 브로커 계정 페이지. |
| **프로덕션 인프라** | PostgreSQL 16 + Redis 7, Worker, GHCR 멀티 아키 이미지. |
| **보안** | 기본 `SECRET_KEY` 거부, Agent 토큰 해시 저장, 기본 페이퍼만. |

## 빠른 시작

**필요:** [Docker](https://docs.docker.com/get-docker/) + Compose v2. **Node.js 불필요**(GHCR 프론트).

### 한 줄 설치 (Linux / macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
```

기본 `~/quantdinger`. 재실행 시 최신 이미지 pull. → **`http://localhost:8888`** (`quantdinger` / `123456`, 즉시 비밀번호 변경).

### 표준: 저장소 클론 (macOS / Linux)

```bash
git clone https://github.com/brokermr810/QuantDinger.git && cd QuantDinger && cp backend_api_python/env.example backend_api_python/.env && chmod +x scripts/generate-secret-key.sh && ./scripts/generate-secret-key.sh && docker-compose up -d --build
```

`docker-compose`가 없으면 `docker compose`를 사용하세요.

### Windows (PowerShell)

**Docker Desktop**을 켠 뒤 PowerShell에서:

```powershell
git clone https://github.com/brokermr810/QuantDinger.git
Set-Location QuantDinger
Copy-Item backend_api_python\env.example -Destination backend_api_python\.env
$key = & python -c "import secrets; print(secrets.token_hex(32))" 2>$null
if (-not $key) { $key = & py -c "import secrets; print(secrets.token_hex(32))" 2>$null }
if (-not $key) { Write-Error "Python 3를 PATH에 추가하세요." }
(Get-Content backend_api_python\.env) -replace '^SECRET_KEY=.*$', "SECRET_KEY=$key" | Set-Content backend_api_python\.env -Encoding utf8
docker-compose up -d --build
```

### Windows (Git Bash)

Git for Windows Bash에서는 위 macOS/Linux 한 줄 명령을 그대로 사용할 수 있습니다.

---

브라우저에서 **`http://localhost:8888`** → **`quantdinger` / `123456`** 로그인 후 **관리자 비밀번호를 즉시 변경**하세요. 자세한 내용은 [설치 및 첫 실행](#설치-및-첫-실행)을 참고하세요.

## 관련 저장소

| 저장소 | 내용 |
|--------|------|
| **[QuantDinger](https://github.com/brokermr810/QuantDinger)** (본 저장소) | 백엔드, Compose, 문서, 프리빌드 Web |
| **[QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue)** | **Web 프론트 소스**(Vue) — `v*` 태그가 `ghcr.io/brokermr810/quantdinger-frontend`를 자동 발행 |
| **[QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)** | **모바일 클라이언트**(오픈소스) |

<h2 id="mcp--agent-게이트웨이">MCP / Agent 게이트웨이</h2>

**Cursor / Claude Code / Codex** 등을 위한 **Model Context Protocol(MCP)** 및 **Agent Gateway**(`/api/agent/v1`). 상세는 영어 문서가 기준입니다:

- **연결 가이드:** [**MCP_SETUP.md**](agent/MCP_SETUP.md) — 호스팅 / 자체 호스팅, 로컬 stdio, 원격 HTTP, Claude Code CLI를 모두 한 페이지에 정리.
- [AGENT_QUICKSTART.md](agent/AGENT_QUICKSTART.md) · [AI_INTEGRATION_DESIGN.md](agent/AI_INTEGRATION_DESIGN.md) · [agent-openapi.json](agent/agent-openapi.json)
- MCP 서버: [`../mcp_server/README.md`](../mcp_server/README.md) · PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/)

**보안:** 모든 Agent 호출은 감사 로그에 기록됩니다. 거래(T) 토큰은 기본 **페이퍼만**; 라이브는 서버 `AGENT_LIVE_TRADING_ENABLED=true`와 토큰 `paper_only=false`가 모두 필요합니다.

## 제품 개요

셀프호스트 **AI + Python 전략 + 백테스트 + 라이브** 통합 환경. TradingView + Notebook + 챗 AI + 거래소 bot 조합을 **하나의 감사 가능한 Docker 스택**으로 대체. 자격 증명은 **PostgreSQL**과 **`.env`**로 관리.

## 기능 하이라이트

- **리서치 & AI** — 멀티 LLM, NL→코드, Agent / MCP(scoped token, SSE).
- **구축** — `IndicatorStrategy` / `ScriptStrategy`, 프로 K라인 UI.
- **검증** — 서버 사이드 백테스트(에퀴티, 드로다운, 체결 로그).
- **운영** — 10+ crypto, IBKR / MT5 / Alpaca, 통합 브로커 계정, Telegram / Discord / Webhook.
- **플랫폼** — Docker + GHCR, Postgres 16, Redis 7, OAuth, 멀티유저, 과금, AWS Marketplace.

## 아키텍처

**설계 원칙:** 시세·전략/백테스트·실행 분리. Nginx + Vue SPA, Flask + Gunicorn, PostgreSQL 16, Redis 7. 배포: `install.sh` 한 줄, GHCR zero-repo, full repo Compose, AWS AMI, [SaaS](https://ai.quantdinger.com).

## 비주얼 투어

<table align="center" width="100%">
  <tr>
    <td colspan="2" align="center">
      <a href="https://www.youtube.com/watch?v=wHIvvv6fmHA">
        <img src="screenshots/video_demo.png" alt="데모" width="80%" style="border-radius: 12px;">
      </a>
      <br/><sub><a href="https://www.youtube.com/watch?v=wHIvvv6fmHA"><strong>▶ 데모 영상 보기</strong></a></sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="screenshots/v31.png" alt="IDE" style="border-radius: 6px;"><br/><sub>인디케이터 IDE·차트·백테스트</sub></td>
    <td width="50%" align="center"><img src="screenshots/v32.png" alt="AI" style="border-radius: 6px;"><br/><sub>AI 자산 분석</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="screenshots/v33.png" alt="Bots" style="border-radius: 6px;"><br/><sub>트레이딩 봇</sub></td>
    <td align="center"><img src="screenshots/v34.png" alt="Live" style="border-radius: 6px;"><br/><sub>라이브 전략·성과</sub></td>
  </tr>
</table>

## 설치 및 첫 실행

1. 클론 후 `cp backend_api_python/env.example backend_api_python/.env`
2. **`SECRET_KEY` 필수 설정**(플레이스홀더면 백엔드가 시작되지 않음). Linux/macOS: `./scripts/generate-secret-key.sh`
3. `docker-compose up -d --build`
   - **대안 (저장소 불필요)**: backend + frontend 모두 GHCR의 프리빌드 멀티 아키텍처(amd64/arm64) 이미지를 직접 풀:
     ```bash
     curl -O https://raw.githubusercontent.com/brokermr810/QuantDinger/main/docker-compose.ghcr.yml
     curl -o backend.env https://raw.githubusercontent.com/brokermr810/QuantDinger/main/backend_api_python/env.example
     docker compose -f docker-compose.ghcr.yml up -d
     ```
     기본 이미지: `ghcr.io/brokermr810/quantdinger-{backend,frontend}:latest`. 양쪽을 동시에 고정하려면 로컬 `.env`에 `IMAGE_TAG=v3.0.9`, 한쪽만 고정하려면 `BACKEND_TAG` / `FRONTEND_TAG`.
   - **프론트엔드 로컬 개발**: `QuantDinger-Vue`를 `./QuantDinger-Vue/`(gitignore)에 클론 후 `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`. 자세한 내용은 [영어 README](../README.md#alternative-build-the-frontend-from-vue-source) 참고.
4. **Web:** `http://localhost:8888` · **API 헬스:** `http://localhost:5000/api/health`
5. 프로덕션 전 기본 관리자 비밀번호 변경. `backend_api_python/.env`의 **`FRONTEND_URL`**을 실제 URL에 맞추세요.

AI 기능은 `env.example`의 **AI / LLM** 섹션을 `.env`에 반영한 뒤 backend를 재시작하세요. 긴 체크리스트는 [영어 README](../README.md) 또는 [简体中文](README_CN.md)를 참고하세요.

## 문서 목록

| 문서 | 설명 |
|------|------|
| [English README](../README.md) | 전체(영어) |
| [简体中文](README_CN.md) | 전체(중국어 간체) |
| [CHANGELOG](CHANGELOG.md) | 변경 이력 |
| [Agent 빠른 시작](agent/AGENT_QUICKSTART.md)(영어) | Agent Gateway / curl 예제 |
| [전략 가이드(영어)](STRATEGY_DEV_GUIDE.md) | 인디케이터·스크립트 전략 개발 |

기타: [multi-user-setup.md](multi-user-setup.md) · [IBKR](IBKR_TRADING_GUIDE_EN.md) · [MT5](MT5_TRADING_GUIDE_EN.md) — 상세는 영어 문서가 중심입니다.

## 자주 묻는 질문

**정말 셀프호스트 가능한가요?** 네. Docker Compose로 자체 인프라에 배포합니다.

**암호화폐만인가요?** 아니요. IBKR / Alpaca(미국 주식 · ETF · 암호화폐), MT5(FX)도 지원합니다.

**Python으로 전략을 쓸 수 있나요?** 네. `IndicatorStrategy`와 `ScriptStrategy`를 지원합니다.

**상업적 이용?** 백엔드는 **Apache 2.0**. [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue) 프론트는 별도 소스 가용 라이선스—상업 이용 전 조항을 확인하세요. 모바일은 [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile) 라이선스를 따릅니다.

**모바일은?** [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)을 참고하세요.

## 거래소 추천 링크(참고)

| 거래소 | 링크 |
|--------|------|
| Binance | [가입](https://www.bsmkweb.cc/register?ref=QUANTDINGER) |
| OKX | [가입](https://www.xqmnobxky.com/join/QUANTDINGER) |
| Bybit | [가입](https://partner.bybit.com/b/DINGER) |

## 라이선스

- 백엔드: **Apache License 2.0** ([`../LICENSE`](../LICENSE))
- 동봉 Web UI: 프리빌드 배포. 소스는 [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue)(별도 라이선스)
- 상표: [`../TRADEMARKS.md`](../TRADEMARKS.md)

## 면책

QuantDinger는 합법적인 연구·교육·규정 준수 거래용입니다. **투자 조언이 아닙니다.** 이용은 본인 책임입니다.

## 커뮤니티

- [Telegram](https://t.me/quantdinger) · [Discord](https://discord.com/invite/tyx5B6TChr) · [Issues](https://github.com/brokermr810/QuantDinger/issues)
- Email: [support@quantdinger.com](mailto:support@quantdinger.com)

## Star 히스토리

[![Star History Chart](https://api.star-history.com/svg?repos=brokermr810/QuantDinger&type=Date)](https://star-history.com/#brokermr810/QuantDinger&Date)

## 감사의 말

Flask, Pandas, CCXT, Vue.js, KLineCharts, ECharts 등 오픈소스 커뮤니티에 감사드립니다.

<p align="center"><sub>도움이 되었다면 GitHub Star 부탁드립니다.</sub></p>
