<div align="center">
  <a href="https://github.com/brokermr810/QuantDinger">
    <img src="screenshots/logo.jpg" alt="QuantDinger Logo" width="220" height="220">
  </a>

  <h1>QuantDinger</h1>
  <h3>ระบบปฏิบัติการเทรดเชิงปริมาณ AI ส่วนตัวของคุณ</h3>
  <p><strong>สแต็ก Docker เดียวสำหรับกราฟ มัลติ LLM วิจัย กลยุทธ์ Python แบ็คเทสต์ระดับสถาบัน และไลฟ์หลาย venue—โฮสต์เอง คีย์ของคุณ ข้อมูลของคุณ</strong></p>
  <p><em>quant OS โอเพนซอร์ส: AI ช่วยเขียนโค้ด → แบ็คเทสต์ → เทรดจำลอง → ไลฟ์ (crypto/IBKR/MT5/Alpaca) พร้อม Agent Gateway และ MCP</em></p>

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
      <a href="https://www.youtube.com/watch?v=tNAZ9uMiUUw"><strong>วิดีโอสาธิต</strong></a>
      <span style="color: #d8dee4;"> &nbsp;·&nbsp; </span>
      <a href="https://www.quantdinger.com"><strong>เว็บไซต์</strong></a>
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

## สารบัญ

[เริ่มต้นอย่างรวดเร็ว](#เริ่มต้นอย่างรวดเร็ว) · [ไฮไลต์ทางเทคนิค](#ไฮไลต์ทางเทคนิค) · [ที่เก็บที่เกี่ยวข้อง](#ที่เก็บที่เกี่ยวข้อง) · [MCP / Agent](#mcp--agent-gateway) · [ภาพรวม](#ภาพรวมผลิตภัณฑ์) · [คุณสมบัติ](#ไฮไลต์คุณสมบัติ) · [ภาพหน้าจอ](#ทัวร์ภาพ) · [สถาปัตยกรรม](#สถาปัตยกรรม) · [ติดตั้ง](#การติดตั้งและการเริ่มต้น) · [เอกสาร](#รายการเอกสาร) · [คำถามที่พบบ่อย](#คำถามที่พบบ่อย) · [ใบอนุญาต](#ใบอนุญาต)

---

> QuantDinger เป็น **quant OS แบบ self-hosted และเน้นโลคัล** — ไม่ใช่แชทบอทที่มีปุ่มซื้อ รวม **มัลติ LLM วิจัย** **กลยุทธ์ Python เนทีฟ** **แบ็คเทสต์ฝั่งเซิร์ฟเวอร์** และ **ไลฟ์หลายโบรกเกอร์** (10+ crypto venue, IBKR, MT5, Alpaca) ในสแต็กระดับ production ที่คุณควบคุมได้ทั้งหมด

<div align="center">
  <img src="screenshots/ezgif.com-animated-gif-maker.gif" alt="QuantDinger demo" width="920" style="border-radius: 12px; border: 1px solid #eaeef2;">
  <p><sub><em>จากศูนย์ถึงรันได้—กราฟ AI วิจัย และเวิร์กโฟลว์กลยุทธ์ในไม่กี่นาที</em></sub></p>
</div>

<div align="center">
  <img src="screenshots/architecture.png" alt="สถาปัตยกรรม QuantDinger" width="960">
  <p><sub><em>ลูปปิด 5 ชั้น: <strong>ไอเดีย → อินดิเคเตอร์ → กลยุทธ์ → แบ็คเทสต์ → ปรับแต่ง → ดำเนินการ → มอนิเตอร์</strong></em></sub></p>
</div>

## ไฮไลต์ทางเทคนิค

| | จุดเด่นของ QuantDinger |
|---|------------------------|
| **quant OS ครบวงจร** | กราฟ IDE AI แบ็คเทสต์ bot ไลฟ์ quick trade จัดการบัญชีโบรกเกอร์—ผลิตภัณฑ์เดียว |
| **Agent-native** | **Agent Gateway** + PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/) — Cursor / Claude Code / Codex พร้อม audit log |
| **runtime กลยุทธ์คู่** | `IndicatorStrategy` (สัญญาณเวกเตอร์) และ `ScriptStrategy` (`on_bar`) |
| **หลาย venue** | CCXT crypto, IBKR, MT5, Alpaca — หน้าบัญชีโบรกเกอร์รวม |
| **infra ระดับ production** | PostgreSQL 16 + Redis 7, Worker, อิมเมจ GHCR multi-arch |
| **ความปลอดภัย** | ปฏิเสธ `SECRET_KEY` ค่าเริ่มต้น, token แฮช, เทรดจำลองเป็นค่าเริ่มต้น |

## เริ่มต้นอย่างรวดเร็ว

**สิ่งที่ต้องมี:** [Docker](https://docs.docker.com/get-docker/) + Compose v2 **ไม่ต้องใช้ Node.js** (ดึง frontend จาก GHCR)

### ติดตั้งบรรทัดเดียว (Linux / macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
```

ติดตั้งที่ `~/quantdinger` โดยค่าเริ่มต้น รันซ้ำเพื่อ pull อิมเมจล่าสุด → **`http://localhost:8888`** (`quantdinger` / `123456` เปลี่ยนรหัสทันที)

### มาตรฐาน: โคลนที่เก็บ (macOS / Linux)

```bash
git clone https://github.com/brokermr810/QuantDinger.git && cd QuantDinger && cp backend_api_python/env.example backend_api_python/.env && chmod +x scripts/generate-secret-key.sh && ./scripts/generate-secret-key.sh && docker-compose up -d --build
```

หากไม่มี `docker-compose` ให้ลอง `docker compose`

### Windows (PowerShell)

เปิด **Docker Desktop** แล้วรันใน PowerShell:

```powershell
git clone https://github.com/brokermr810/QuantDinger.git
Set-Location QuantDinger
Copy-Item backend_api_python\env.example -Destination backend_api_python\.env
$key = & python -c "import secrets; print(secrets.token_hex(32))" 2>$null
if (-not $key) { $key = & py -c "import secrets; print(secrets.token_hex(32))" 2>$null }
if (-not $key) { Write-Error "เพิ่ม Python 3 ลงใน PATH" }
(Get-Content backend_api_python\.env) -replace '^SECRET_KEY=.*$', "SECRET_KEY=$key" | Set-Content backend_api_python\.env -Encoding utf8
docker-compose up -d --build
```

### Windows (Git Bash)

ใน Bash ของ Git for Windows สามารถใช้คำสั่งบรรทัดเดียวของ macOS/Linux ได้

---

เปิด **`http://localhost:8888`** ล็อกอินด้วย **`quantdinger` / `123456`** แล้ว **เปลี่ยนรหัสผู้ดูแลระบบทันที** รายละเอียดเพิ่มเติมอยู่ที่ [การติดตั้งและการเริ่มต้น](#การติดตั้งและการเริ่มต้น)

## ที่เก็บที่เกี่ยวข้อง

| ที่เก็บ | เนื้อหา |
|---------|---------|
| **[QuantDinger](https://github.com/brokermr810/QuantDinger)** (repo นี้) | แบ็กเอนด์ Compose เอกสาร Web สำเร็จรูป |
| **[QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue)** | **ซอร์สเว็บฟรอนต์** (Vue) — แท็ก `v*` จะเผยแพร่ `ghcr.io/brokermr810/quantdinger-frontend` อัตโนมัติ |
| **[QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)** | **ไคลเอนต์มือถือ** (โอเพนซอร์ส) |

<h2 id="mcp--agent-gateway">MCP / Agent Gateway</h2>

สำหรับ **Cursor / Claude Code / Codex** มี **Model Context Protocol (MCP)** และ **Agent Gateway** (`/api/agent/v1`) รายละเอียดเชิงลึกอยู่ในเอกสารภาษาอังกฤษ:

- **คู่มือเชื่อมต่อ:** [**MCP_SETUP.md**](agent/MCP_SETUP.md) — โฮสต์ / Self-hosted, stdio ในเครื่อง, HTTP ระยะไกล, Claude Code CLI รวมในที่เดียว
- [AGENT_QUICKSTART.md](agent/AGENT_QUICKSTART.md) · [AI_INTEGRATION_DESIGN.md](agent/AI_INTEGRATION_DESIGN.md) · [agent-openapi.json](agent/agent-openapi.json)
- เซิร์ฟเวอร์ MCP: [`../mcp_server/README.md`](../mcp_server/README.md) · PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/)

**ความปลอดภัย:** การเรียก Agent ทุกครั้งถูกบันทึกใน audit log โทเค็นการเทรด (T) เริ่มต้นเป็น **เทรดจำลองเท่านั้น** การเทรดจริงต้องมีทั้ง `AGENT_LIVE_TRADING_ENABLED=true` บนเซิร์ฟเวอร์และ `paper_only=false` บนโทเค็น

## ภาพรวมผลิตภัณฑ์

สภาพแวดล้อม **AI + กลยุทธ์ Python + แบ็คเทสต์ + ไลฟ์** แบบ self-hosted แทนที่ชุด TradingView + Notebook + แชท AI + bot ด้วย **สแต็ก Docker ที่ตรวจสอบได้** ข้อมูลรับรองใน **PostgreSQL** และ **`.env`**

## ไฮไลต์คุณสมบัติ

- **วิจัย & AI** — มัลติ LLM, NL→โค้ด, Agent / MCP (scoped token, SSE)
- **สร้าง** — `IndicatorStrategy` / `ScriptStrategy`, UI แท่งเทียนโปร
- **ตรวจสอบ** — แบ็คเทสต์ฝั่งเซิร์ฟเวอร์ (equity, drawdown, บันทึกเทรด)
- **ปฏิบัติการ** — 10+ crypto, IBKR / MT5 / Alpaca, หน้าบัญชีโบรกเกอร์รวม, Telegram / Discord / Webhook
- **แพลตฟอร์ม** — Docker + GHCR, Postgres 16, Redis 7, OAuth, หลายผู้ใช้, การเรียกเก็บเงิน, AWS Marketplace

## สถาปัตยกรรม

**หลักการ:** แยกข้อมูลตลาด · กลยุทธ์/แบ็คเทสต์ · การดำเนินการ Nginx + Vue SPA, Flask + Gunicorn, PostgreSQL 16, Redis 7 การ deploy: `install.sh` บรรทัดเดียว, GHCR zero-repo, full repo Compose, AWS AMI, [SaaS](https://ai.quantdinger.com)

## ทัวร์ภาพ

<table align="center" width="100%">
  <tr>
    <td colspan="2" align="center">
      <a href="https://www.youtube.com/watch?v=wHIvvv6fmHA">
        <img src="screenshots/video_demo.png" alt="วิดีโอ" width="80%" style="border-radius: 12px;">
      </a>
      <br/><sub><a href="https://www.youtube.com/watch?v=wHIvvv6fmHA"><strong>▶ ดูวิดีโอสาธิต</strong></a></sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="screenshots/v31.png" alt="IDE" style="border-radius: 6px;"><br/><sub>IDE อินดิเคเตอร์ กราฟ การทดสอบย้อนหลัง</sub></td>
    <td width="50%" align="center"><img src="screenshots/v32.png" alt="AI" style="border-radius: 6px;"><br/><sub>การวิเคราะห์สินทรัพย์ด้วย AI</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="screenshots/v33.png" alt="Bots" style="border-radius: 6px;"><br/><sub>บอทเทรด</sub></td>
    <td align="center"><img src="screenshots/v34.png" alt="Live" style="border-radius: 6px;"><br/><sub>กลยุทธ์จริงและผลงาน</sub></td>
  </tr>
</table>

## การติดตั้งและการเริ่มต้น

1. โคลนแล้ว `cp backend_api_python/env.example backend_api_python/.env`
2. **ต้องตั้ง `SECRET_KEY`** (ถ้าเป็นค่า placeholder แบ็กเอนด์จะไม่เริ่ม) Linux/macOS: `./scripts/generate-secret-key.sh`
3. `docker-compose up -d --build`
   - **ทางเลือก (ไม่ต้อง clone repo)**: ดึงอิมเมจ backend + frontend สำเร็จรูปแบบหลายสถาปัตยกรรม (amd64/arm64) จาก GHCR โดยตรง:
     ```bash
     curl -O https://raw.githubusercontent.com/brokermr810/QuantDinger/main/docker-compose.ghcr.yml
     curl -o backend.env https://raw.githubusercontent.com/brokermr810/QuantDinger/main/backend_api_python/env.example
     docker compose -f docker-compose.ghcr.yml up -d
     ```
     อิมเมจเริ่มต้น: `ghcr.io/brokermr810/quantdinger-{backend,frontend}:latest` ตรึงทั้งสองด้านพร้อมกันด้วย `IMAGE_TAG=v3.0.9` ใน `.env` ภายในเครื่อง (หรือ `BACKEND_TAG` / `FRONTEND_TAG` เพื่อตรึงทีละด้าน)
   - **พัฒนาฟรอนต์เอนด์ในเครื่อง**: โคลน `QuantDinger-Vue` ไปยัง `./QuantDinger-Vue/` (gitignore แล้ว) แล้วรัน `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build` รายละเอียดดู [README ภาษาอังกฤษ](../README.md#alternative-build-the-frontend-from-vue-source)
4. **เว็บ:** `http://localhost:8888` · **สุขภาพ API:** `http://localhost:5000/api/health`
5. เปลี่ยนรหัสผู้ดูแลเริ่มต้นก่อนโปรดักชัน ตั้ง **`FRONTEND_URL`** ใน `backend_api_python/.env` ให้ตรง URL จริง

ฟีเจอร์ AI: คัดลอกส่วน **AI / LLM** จาก `env.example` ไปยัง `.env` แล้วรีสตาร์ทแบ็กเอนด์ รายการตรวจสอบแบบเต็มอยู่ใน [README ภาษาอังกฤษ](../README.md) หรือ [简体中文](README_CN.md)

## รายการเอกสาร

| เอกสาร | คำอธิบาย |
|---------|----------|
| [English README](../README.md) | ฉบับเต็ม (อังกฤษ) |
| [简体中文](README_CN.md) | ฉบับเต็ม (จีนตัวย่อ) |
| [CHANGELOG](CHANGELOG.md) | ประวัติเวอร์ชัน |
| [Agent เริ่มต้น](agent/AGENT_QUICKSTART.md) (อังกฤษ) | Agent Gateway / ตัวอย่าง curl |
| [คู่มือกลยุทธ์ (อังกฤษ)](STRATEGY_DEV_GUIDE.md) | พัฒนากลยุทธ์อินดิเคเตอร์·สคริปต์ |

อื่น ๆ: [multi-user-setup.md](multi-user-setup.md) · [IBKR](IBKR_TRADING_GUIDE_EN.md) · [MT5](MT5_TRADING_GUIDE_EN.md) — รายละเอียดหลักเป็นภาษาอังกฤษ

## คำถามที่พบบ่อย

**โฮสต์เองได้จริงหรือ?** ได้ ใช้ Docker Compose บนโครงสร้างของคุณ

**มีแค่คริปโตหรือ?** ไม่ใช่ รองรับ IBKR / Alpaca (หุ้นสหรัฐ · ETF · คริปโต) และ MT5 (FX)

**เขียนกลยุทธ์ด้วย Python ได้ไหม?** ได้ รองรับ `IndicatorStrategy` และ `ScriptStrategy`

**ใช้เชิงพาณิชย์?** แบ็กเอนด์เป็น **Apache 2.0** ฟรอนต์ [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue) มีใบอนุญาตแยก—อ่านก่อนใช้เชิงพาณิชย์ มือถือตาม [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)

**มีแอปมือถือไหม?** ดู [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)

## ลิงก์แนะนำตลาด (อ้างอิง)

| ตลาด | ลิงก์ |
|------|------|
| Binance | [ลงทะเบียน](https://www.bsmkweb.cc/register?ref=QUANTDINGER) |
| OKX | [ลงทะเบียน](https://www.xqmnobxky.com/join/QUANTDINGER) |
| Bybit | [ลงทะเบียน](https://partner.bybit.com/b/DINGER) |

## ใบอนุญาต

- แบ็กเอนด์: **Apache License 2.0** ([`../LICENSE`](../LICENSE))
- UI เว็บที่แนบมา: แจกแบบสำเร็จรูป ซอร์สที่ [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue) (ใบอนุญาตแยก)
- เครื่องหมายการค้า: [`../TRADEMARKS.md`](../TRADEMARKS.md)

## ข้อจำกัดความรับผิด

QuantDinger มีไว้สำหรับการวิจัย การศึกษา และการเทรดที่สอดคล้องกฎหมายเท่านั้น **ไม่ใช่คำแนะนำการลงทุน** การใช้งานเป็นความรับผิดชอบของคุณ

## ชุมชน

- [Telegram](https://t.me/quantdinger) · [Discord](https://discord.com/invite/tyx5B6TChr) · [Issues](https://github.com/brokermr810/QuantDinger/issues)
- Email: [support@quantdinger.com](mailto:support@quantdinger.com)

## แนวโน้ม Star

[![Star History Chart](https://api.star-history.com/svg?repos=brokermr810/QuantDinger&type=Date)](https://star-history.com/#brokermr810/QuantDinger&Date)

## ขอบคุณ

ขอบคุณชุมชนโอเพนซอร์ส เช่น Flask, Pandas, CCXT, Vue.js, KLineCharts, ECharts

<p align="center"><sub>ถ้ามีประโยชน์ ช่วยกด Star บน GitHub ด้วย</sub></p>
