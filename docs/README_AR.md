<div align="center" dir="rtl">
  <a href="https://github.com/brokermr810/QuantDinger">
    <img src="screenshots/logo.jpg" alt="QuantDinger Logo" width="220" height="220">
  </a>

  <h1>QuantDinger</h1>
  <h3>نظام تشغيل كمّي خاص مدعوم بالذكاء الاصطناعي</h3>
  <p><strong>حزمة Docker واحدة للرسوم، بحوث متعددة LLM، استراتيجيات بايثون، اختبار رجعي بمستوى مؤسسي، وتنفيذ مباشر متعدد الأسواق—استضافة ذاتية كاملة، مفاتيحك وبياناتك.</strong></p>
  <p><em>quant OS مفتوح المصدر: برمجة بمساعدة AI → اختبار رجعي → ورقي → مباشر (crypto/IBKR/MT5/Alpaca) مع Agent Gateway وMCP.</em></p>

  <div align="center" style="max-width: 680px; margin: 1.25rem auto 0; padding: 20px 22px 22px; border: 1px solid #d1d9e0; border-radius: 16px;" dir="ltr">
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
      <a href="https://www.youtube.com/watch?v=tNAZ9uMiUUw"><strong>فيديو تجريبي</strong></a>
      <span style="color: #d8dee4;"> &nbsp;·&nbsp; </span>
      <a href="https://www.quantdinger.com"><strong>الموقع</strong></a>
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

  <p style="margin-top: 1.45rem; margin-bottom: 10px;" dir="ltr">
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

<div dir="rtl">

## جدول المحتويات

[بدء سريع](#بدء-سريع) · [أبرز التقنيات](#أبرز-التقنيات) · [مستودعات ذات صلة](#مستودعات-ذات-صلة) · [MCP / Agent](#mcp--agent-gateway) · [نظرة عامة](#نظرة-عامة-على-المنتج) · [الميزات](#أبرز-الميزات) · [لقطات](#جولة-بصرية) · [البنية](#البنية) · [التثبيت](#التثبيت-والتشغيل-الأول) · [الوثائق](#قائمة-الوثائق) · [أسئلة شائعة](#أسئلة-شائعة) · [الترخيص](#الترخيص)

---

> QuantDinger **نظام تشغيل كمّي ذاتي الاستضافة** — ليس chatbot بزر شراء. يوحّد **بحوث متعددة LLM** و**محركات استراتيجية بايثون** و**اختبارًا رجعيًا على الخادم** و**تداولًا مباشرًا متعدد الوسطاء** (10+ crypto venue، IBKR، MT5، Alpaca) في حزمة production تتحكم بها بالكامل.

</div>

<div align="center">
  <img src="screenshots/ezgif.com-animated-gif-maker.gif" alt="QuantDinger demo" width="920" style="border-radius: 12px; border: 1px solid #eaeef2;">
  <p dir="rtl"><sub><em>من الصفر إلى التشغيل—رسوم، بحث AI، وسير عمل الاستراتيجية في دقائق.</em></sub></p>
</div>

<div align="center">
  <img src="screenshots/architecture.png" alt="بنية QuantDinger" width="960">
  <p dir="rtl"><sub><em>حلقة مغلقة من 5 طبقات: <strong>فكرة → مؤشر → استراتيجية → اختبار رجعي → تحسين → تنفيذ → مراقبة</strong></em></sub></p>
</div>

<div dir="rtl">

## أبرز التقنيات

| | ما يميز QuantDinger |
|---|---------------------|
| **quant OS متكامل** | رسوم، IDE، AI، اختبار رجعي، bots مباشرة، quick trade، إدارة حسابات الوسطاء—منتج واحد |
| **Agent-native** | **Agent Gateway** + PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/) — Cursor / Claude Code / Codex مع سجل تدقيق |
| **محركان للاستراتيجية** | `IndicatorStrategy` (إشارات متجهة) و`ScriptStrategy` (`on_bar`) |
| **أسواق متعددة** | CCXT crypto، IBKR، MT5، Alpaca — صفحة حسابات وسيط موحّدة |
| **بنية production** | PostgreSQL 16 + Redis 7، Workers، صور GHCR multi-arch |
| **أمان** | يرفض `SECRET_KEY` الافتراضي، رموز مُ hash، ورقي افتراضيًا |

## بدء سريع

**المتطلبات:** [Docker](https://docs.docker.com/get-docker/) + Compose v2. **لا حاجة لـ Node.js** (الواجهة من GHCR).

### تثبيت بسطر واحد (Linux / macOS)

</div>

```bash
curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
```

<div dir="rtl">

افتراضيًا `~/quantdinger`. أعد التشغيل لسحب أحدث الصور. → **`http://localhost:8888`** (`quantdinger` / `123456`، غيّر كلمة المرور فورًا).

### قياسي: استنساخ المستودع (macOS / Linux)

</div>

```bash
git clone https://github.com/brokermr810/QuantDinger.git && cd QuantDinger && cp backend_api_python/env.example backend_api_python/.env && chmod +x scripts/generate-secret-key.sh && ./scripts/generate-secret-key.sh && docker-compose up -d --build
```

<div dir="rtl">

إن لم يتوفر `docker-compose` جرّب `docker compose`.

### Windows (PowerShell)

شغّل **Docker Desktop** ثم في PowerShell:

</div>

```powershell
git clone https://github.com/brokermr810/QuantDinger.git
Set-Location QuantDinger
Copy-Item backend_api_python\env.example -Destination backend_api_python\.env
$key = & python -c "import secrets; print(secrets.token_hex(32))" 2>$null
if (-not $key) { $key = & py -c "import secrets; print(secrets.token_hex(32))" 2>$null }
if (-not $key) { Write-Error "أضف Python 3 إلى PATH." }
(Get-Content backend_api_python\.env) -replace '^SECRET_KEY=.*$', "SECRET_KEY=$key" | Set-Content backend_api_python\.env -Encoding utf8
docker-compose up -d --build
```

<div dir="rtl">

### Windows (Git Bash)

في Bash المرفق مع Git for Windows يمكن استخدام أمر السطر الواحد لنظامي macOS/Linux.

---

افتح **`http://localhost:8888`**، سجّل الدخول بـ **`quantdinger` / `123456`**، ثم **غيّر كلمة مرور المسؤول فورًا**. للتفاصيل راجع [التثبيت والتشغيل الأول](#التثبيت-والتشغيل-الأول).

## مستودعات ذات صلة

| المستودع | المحتوى |
|----------|---------|
| **[QuantDinger](https://github.com/brokermr810/QuantDinger)** (هذا المستودع) | الخلفية، Compose، الوثائق، ويب مُجمَّع |
| **[QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue)** | **مصدر الويب** (Vue) — وسم `v*` ينشر تلقائيًا `ghcr.io/brokermr810/quantdinger-frontend` |
| **[QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile)** | **عميل الجوال** (مفتوح المصدر) |

</div>

<h2 id="mcp--agent-gateway" dir="rtl">MCP / Agent Gateway</h2>

<div dir="rtl">

لـ **Cursor / Claude Code / Codex**: **Model Context Protocol (MCP)** و**Agent Gateway** (`/api/agent/v1`). التفاصيل الكاملة بالإنجليزية هي المصدر الأساسي:

- **دليل التوصيل:** [**MCP_SETUP.md**](agent/MCP_SETUP.md) — مستضاف / ذاتي الاستضافة، stdio محلي، HTTP عن بُعد، Claude Code CLI، كله في صفحة واحدة.
- [AGENT_QUICKSTART.md](agent/AGENT_QUICKSTART.md) · [AI_INTEGRATION_DESIGN.md](agent/AI_INTEGRATION_DESIGN.md) · [agent-openapi.json](agent/agent-openapi.json)
- خادم MCP: [`../mcp_server/README.md`](../mcp_server/README.md) · PyPI [`quantdinger-mcp`](https://pypi.org/project/quantdinger-mcp/)

**الأمان:** تُسجَّل جميع استدعاءات Agent في سجل التدقيق. رموز التداول (T) افتراضيًا **ورقي فقط**؛ التداول المباشر يتطلب `AGENT_LIVE_TRADING_ENABLED=true` على الخادم و`paper_only=false` على الرمز.

## نظرة عامة على المنتج

بيئة **AI + استراتيجيات بايثون + اختبار رجعي + تداول مباشر** ذاتية الاستضافة. تستبدل مجموعة TradingView + Notebook + chat AI + bots ب**حزمة Docker قابلة للتدقيق**. الاعتمادات في **PostgreSQL** و**`.env`**.

## أبرز الميزات

- **البحث والذكاء الاصطناعي** — LLM متعدد، NL→كود، Agent / MCP (scoped token، SSE).
- **البناء** — `IndicatorStrategy` / `ScriptStrategy`، واجهة شموع احترافية.
- **التحقق** — اختبار رجعي على الخادم (equity، drawdown، سجل الصفقات).
- **التشغيل** — 10+ crypto، IBKR / MT5 / Alpaca، صفحة وسيط موحّدة، Telegram / Discord / Webhook.
- **المنصة** — Docker + GHCR، Postgres 16، Redis 7، OAuth، متعدد المستخدمين، فوترة، AWS Marketplace.

## البنية

**المبدأ:** فصل بيانات السوق · الاستراتيجية/الاختبار · التنفيذ. Nginx + Vue SPA، Flask + Gunicorn، PostgreSQL 16، Redis 7. النشر: `install.sh` بسطر واحد، GHCR zero-repo، full repo Compose، AWS AMI، [SaaS](https://ai.quantdinger.com).

## جولة بصرية

<table align="center" width="100%" dir="ltr">
  <tr>
    <td colspan="2" align="center">
      <a href="https://www.youtube.com/watch?v=wHIvvv6fmHA">
        <img src="screenshots/video_demo.png" alt="فيديو" width="80%" style="border-radius: 12px;">
      </a>
      <br/><sub><a href="https://www.youtube.com/watch?v=wHIvvv6fmHA"><strong>▶ مشاهدة العرض</strong></a></sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="screenshots/v31.png" alt="IDE" style="border-radius: 6px;"><br/><sub>IDE للمؤشرات، الرسوم، الاختبار الرجعي</sub></td>
    <td width="50%" align="center"><img src="screenshots/v32.png" alt="AI" style="border-radius: 6px;"><br/><sub>تحليل الأصول بالذكاء الاصطناعي</sub></td>
  </tr>
  <tr>
    <td align="center"><img src="screenshots/v33.png" alt="Bots" style="border-radius: 6px;"><br/><sub>بوتات التداول</sub></td>
    <td align="center"><img src="screenshots/v34.png" alt="Live" style="border-radius: 6px;"><br/><sub>استراتيجيات مباشرة والأداء</sub></td>
  </tr>
</table>

## التثبيت والتشغيل الأول

1. استنسخ المستودع ثم `cp backend_api_python/env.example backend_api_python/.env`
2. **يجب تعيين `SECRET_KEY`** (القيمة الافتراضية تمنع تشغيل الخلفية). Linux/macOS: `./scripts/generate-secret-key.sh`
3. `docker-compose up -d --build`
   - **بديل (بدون استنساخ المستودع)**: اسحب صور backend + frontend الجاهزة المتعددة المعماريات (amd64/arm64) من GHCR مباشرة:
     ```bash
     curl -O https://raw.githubusercontent.com/brokermr810/QuantDinger/main/docker-compose.ghcr.yml
     curl -o backend.env https://raw.githubusercontent.com/brokermr810/QuantDinger/main/backend_api_python/env.example
     docker compose -f docker-compose.ghcr.yml up -d
     ```
     الصور الافتراضية: `ghcr.io/brokermr810/quantdinger-{backend,frontend}:latest`. لتثبيت إصدار محدد اضبط `IMAGE_TAG=v3.0.9` في ملف `.env` محلي (أو `BACKEND_TAG` / `FRONTEND_TAG` لتجاوز جانب واحد فقط).
   - **التطوير المحلي للواجهة**: استنسخ `QuantDinger-Vue` إلى `./QuantDinger-Vue/` (مُتجاهَل من Git) وشغّل `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`. التفاصيل في [README الإنجليزي](../README.md#alternative-build-the-frontend-from-vue-source).
4. **الويب:** `http://localhost:8888` · **صحة API:** `http://localhost:5000/api/health`
5. غيّر كلمة مرور المسؤول الافتراضية قبل الإنتاج. اضبط **`FRONTEND_URL`** في `backend_api_python/.env` على عنوانك الفعلي.

للميزات الذكية: انسخ قسم **AI / LLM** من `env.example` إلى `.env` وأعد تشغيل الخلفية. قائمة تحقق كاملة في [README الإنجليزي](../README.md) أو [简体中文](README_CN.md).

## قائمة الوثائق

| الوثيقة | الوصف |
|---------|--------|
| [English README](../README.md) | النسخة الكاملة (إنجليزي) |
| [简体中文](README_CN.md) | النسخة الكاملة (صيني مبسّط) |
| [CHANGELOG](CHANGELOG.md) | سجل الإصدارات |
| [Agent سريع](agent/AGENT_QUICKSTART.md) (إنجليزي) | Agent Gateway / أمثلة curl |
| [دليل الاستراتيجية (إنجليزي)](STRATEGY_DEV_GUIDE.md) | تطوير استراتيجيات المؤشر/السكربت |

أخرى: [multi-user-setup.md](multi-user-setup.md) · [IBKR](IBKR_TRADING_GUIDE_EN.md) · [MT5](MT5_TRADING_GUIDE_EN.md) — التفاصيل غالبًا بالإنجليزية.

## أسئلة شائعة

**هل يمكن الاستضافة الذاتية حقًا؟** نعم، عبر Docker Compose على بنيتك.

**هل للعملات المشفّرة فقط؟** لا. يدعم IBKR / Alpaca (أسهم · ETF · عملات مشفّرة) وMT5 (فوركس).

**هل يمكن كتابة استراتيجيات بايثون؟** نعم، `IndicatorStrategy` و`ScriptStrategy`.

**الاستخدام التجاري؟** الخلفية **Apache 2.0**. الواجهة [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue) بترخيص منفصل—اقرأه قبل الاستخدام التجاري. الجوال وفق [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile).

**هل يوجد تطبيق جوال؟** راجع [QuantDinger-Mobile](https://github.com/brokermr810/QuantDinger-Mobile).

## روابط إحالة للبورصات (مرجعية)

| البورصة | الرابط |
|---------|--------|
| Binance | [تسجيل](https://www.bsmkweb.cc/register?ref=QUANTDINGER) |
| OKX | [تسجيل](https://www.xqmnobxky.com/join/QUANTDINGER) |
| Bybit | [تسجيل](https://partner.bybit.com/b/DINGER) |

## الترخيص

- الخلفية: **Apache License 2.0** ([`../LICENSE`](../LICENSE))
- واجهة الويب المرفقة: توزيع مُجمَّع. المصدر في [QuantDinger-Vue](https://github.com/brokermr810/QuantDinger-Vue) (ترخيص منفصل)
- العلامات التجارية: [`../TRADEMARKS.md`](../TRADEMARKS.md)

## إخلاء مسؤولية

QuantDinger مخصّص للبحث والتعليم والتداول المتوافق مع القانون **الشرعي**. **ليس نصيحة استثمارية.** الاستخدام على مسؤوليتك.

## المجتمع

- [Telegram](https://t.me/quantdinger) · [Discord](https://discord.com/invite/tyx5B6TChr) · [Issues](https://github.com/brokermr810/QuantDinger/issues)
- البريد: [support@quantdinger.com](mailto:support@quantdinger.com)

## اتجاه النجوم

[![Star History Chart](https://api.star-history.com/svg?repos=brokermr810/QuantDinger&type=Date)](https://star-history.com/#brokermr810/QuantDinger&Date)

## شكر وتقدير

شكرًا لمجتمعات المصدر المفتوح مثل Flask وPandas وCCXT وVue.js وKLineCharts وECharts.

<p align="center"><sub>إن كان المشروع مفيدًا، نرحب بنجمة على GitHub.</sub></p>

</div>
