#!/usr/bin/env node
/* eslint-disable no-console */
/**
 * i18n 全自动 AI 翻译补齐工具
 *
 * 用法:
 *   # 1. 配置一个 API key (任选其一):
 *   set ANTHROPIC_API_KEY=sk-ant-xxx
 *   set OPENAI_API_KEY=sk-xxx
 *   set DEEPSEEK_API_KEY=sk-xxx
 *   set OPENROUTER_API_KEY=sk-or-xxx
 *
 *   # 2. 跑一次 dry-run 看效果(不写盘):
 *   node scripts/i18n-fill-ai.js --lang=ja-JP --batch=5 --dry-run
 *
 *   # 3. 正式跑(单语言):
 *   node scripts/i18n-fill-ai.js --lang=ja-JP
 *
 *   # 4. 一口气跑完所有缺失语言:
 *   node scripts/i18n-fill-ai.js --all
 *
 * 参数:
 *   --lang=xx-YY       指定目标语言(或 --all)
 *   --all              所有非 zh-CN / 非 en-US 语言
 *   --provider=...     anthropic | openai | deepseek | openrouter(默认 anthropic)
 *   --model=...        覆盖默认模型
 *   --batch=N          每次请求翻译的 key 数(默认 80)
 *   --concurrency=N    批次并发(默认 3)
 *   --dry-run          只预览不写文件
 *   --no-cache         不读缓存,强制重新翻译
 *   --retry=N          单批失败重试次数(默认 3)
 *
 * 机制:
 *   - 主 key 集 = zh-CN ∪ en-US 的全部 key
 *   - 目标语言缺失的 key -> 发给 AI 翻译(同时提供 zh-CN + en-US 作为参考)
 *   - 翻译结果缓存到 scripts/.i18n-cache/<lang>.json,避免重复消耗
 *   - 翻译完毕 append 到目标文件 `const locale = { ... }` 的末尾,不破坏原有结构和注释
 *   - 会先备份目标文件到 <file>.bak
 */

const fs = require('fs')
const path = require('path')

const LANG_DIR = path.join(__dirname, '..', 'QuantDinger-Vue-src', 'src', 'locales', 'lang')
const CACHE_DIR = path.join(__dirname, '.i18n-cache')
const ALL_LANGS = ['ar-SA', 'de-DE', 'en-US', 'fr-FR', 'ja-JP', 'ko-KR', 'th-TH', 'vi-VN', 'zh-TW']
const BASE = 'zh-CN'
const REF = 'en-US'

const LANG_META = {
  'ar-SA': { name: 'Arabic (Saudi Arabia)', note: 'RTL language, use natural Arabic phrasing for UI.' },
  'de-DE': { name: 'German (Germany)', note: 'Use formal "Sie" for user-facing labels, concise UI style.' },
  'en-US': { name: 'English (US)', note: 'Concise, neutral UI English; sentence-case for labels; use standard fintech/trading terminology.' },
  'fr-FR': { name: 'French (France)', note: 'Keep UI labels short; use formal "vous".' },
  'ja-JP': { name: 'Japanese', note: 'Use polite-but-concise UI Japanese (です/ます for sentences, nominal style for labels).' },
  'ko-KR': { name: 'Korean', note: 'Use polite-but-concise UI Korean (합쇼체 for buttons, nominal style for labels).' },
  'th-TH': { name: 'Thai', note: 'Keep UI labels short and natural.' },
  'vi-VN': { name: 'Vietnamese', note: 'Keep UI labels concise; use natural Vietnamese phrasing.' },
  'zh-TW': { name: 'Traditional Chinese (Taiwan)', note: 'Convert Simplified -> Traditional + switch to Taiwan terminology (例: 预测→預測, 软件→軟體, 网络→網路, 数据库→資料庫).' }
}

// ---------- args ----------
const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    if (a.startsWith('--')) {
      const [k, v] = a.slice(2).split('=')
      return [k, v === undefined ? true : v]
    }
    return [a, true]
  })
)
const TARGET_LANGS = args.all ? ALL_LANGS : (args.lang ? String(args.lang).split(',') : null)
if (!TARGET_LANGS) {
  console.error('[!] Specify --lang=xx-YY or --all')
  process.exit(1)
}
for (const l of TARGET_LANGS) {
  if (!ALL_LANGS.includes(l)) {
    console.error(`[!] Unsupported lang: ${l}. Valid: ${ALL_LANGS.join(', ')}`)
    process.exit(1)
  }
}
const PROVIDER = (args.provider || 'anthropic').toLowerCase()
const BATCH = Number(args.batch || 80)
const CONCURRENCY = Number(args.concurrency || 3)
const RETRY = Number(args.retry || 3)
const DRY_RUN = !!args['dry-run']
const NO_CACHE = !!args['no-cache']

// ---------- provider config ----------
const PROVIDERS = {
  anthropic: {
    url: 'https://api.anthropic.com/v1/messages',
    keyEnv: 'ANTHROPIC_API_KEY',
    defaultModel: 'claude-3-5-haiku-latest',
    headers: (key) => ({
      'x-api-key': key,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json'
    }),
    buildBody: (model, system, user) => ({
      model,
      max_tokens: 8192,
      system,
      messages: [{ role: 'user', content: user }]
    }),
    extractText: (j) => {
      const c = j?.content
      if (Array.isArray(c)) return c.map(x => x.text || '').join('')
      return ''
    }
  },
  openai: {
    url: 'https://api.openai.com/v1/chat/completions',
    keyEnv: 'OPENAI_API_KEY',
    defaultModel: 'gpt-4o-mini',
    headers: (key) => ({
      Authorization: `Bearer ${key}`,
      'content-type': 'application/json'
    }),
    buildBody: (model, system, user) => ({
      model,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user }
      ],
      response_format: { type: 'json_object' },
      temperature: 0.2
    }),
    extractText: (j) => j?.choices?.[0]?.message?.content || ''
  },
  deepseek: {
    url: 'https://api.deepseek.com/chat/completions',
    keyEnv: 'DEEPSEEK_API_KEY',
    defaultModel: 'deepseek-chat',
    headers: (key) => ({
      Authorization: `Bearer ${key}`,
      'content-type': 'application/json'
    }),
    buildBody: (model, system, user) => ({
      model,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user }
      ],
      response_format: { type: 'json_object' },
      temperature: 0.2
    }),
    extractText: (j) => j?.choices?.[0]?.message?.content || ''
  },
  openrouter: {
    url: 'https://openrouter.ai/api/v1/chat/completions',
    keyEnv: 'OPENROUTER_API_KEY',
    defaultModel: 'anthropic/claude-3.5-haiku',
    headers: (key) => ({
      Authorization: `Bearer ${key}`,
      'content-type': 'application/json',
      'HTTP-Referer': 'https://quantdinger.local',
      'X-Title': 'QuantDinger i18n'
    }),
    buildBody: (model, system, user) => ({
      model,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user }
      ],
      temperature: 0.2
    }),
    extractText: (j) => j?.choices?.[0]?.message?.content || ''
  }
}

if (!PROVIDERS[PROVIDER]) {
  console.error(`[!] Unsupported provider: ${PROVIDER}. Valid: ${Object.keys(PROVIDERS).join(', ')}`)
  process.exit(1)
}
const P = PROVIDERS[PROVIDER]
const API_KEY = process.env[P.keyEnv]
if (!API_KEY) {
  console.error(`[!] Missing env ${P.keyEnv} for provider ${PROVIDER}`)
  process.exit(1)
}
const MODEL = args.model || P.defaultModel

// ---------- parse/write lang files ----------
// Extract all key-value pairs by regex. Values stored as raw source literal (kept as-is when re-emitting).
// Supports single-line literals with single/double quotes.
const KV_RE = /^[ \t]*(['"])([\w\-.]+)\1\s*:\s*(['"])((?:(?!\3)[^\\]|\\.)*)\3\s*,?[ \t]*$/gm

function extractEntries (filePath) {
  const src = fs.readFileSync(filePath, 'utf8')
  const map = new Map()
  let m
  KV_RE.lastIndex = 0
  while ((m = KV_RE.exec(src)) !== null) {
    const key = m[2]
    const val = m[4]
    if (!map.has(key)) map.set(key, val)
  }
  return { src, map }
}

function findLocaleBlockEnd (src) {
  // Find the `}` that closes `const locale = {`
  // Strategy: locate `const locale = {`, then scan forward with a brace/string-aware parser.
  const startIdx = src.indexOf('const locale = {')
  if (startIdx < 0) throw new Error('cannot find `const locale = {` in file')
  let i = src.indexOf('{', startIdx) + 1
  let depth = 1
  const n = src.length
  while (i < n) {
    const ch = src[i]
    if (ch === "'" || ch === '"' || ch === '`') {
      const quote = ch
      i++
      while (i < n && src[i] !== quote) {
        if (src[i] === '\\') i++
        i++
      }
      i++
      continue
    }
    if (ch === '/' && src[i + 1] === '/') {
      while (i < n && src[i] !== '\n') i++
      continue
    }
    if (ch === '/' && src[i + 1] === '*') {
      i += 2
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++
      i += 2
      continue
    }
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) return i
    }
    i++
  }
  throw new Error('cannot find closing `}` of locale block')
}

function encodeJsString (s) {
  // Emit with single quotes for consistency with existing files
  return "'" + String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n') + "'"
}

function appendEntriesToFile (filePath, lang, entries /* [{key, value}] */) {
  const src = fs.readFileSync(filePath, 'utf8')
  const closeIdx = findLocaleBlockEnd(src)

  // Find the last non-whitespace char before `}` to know whether we need a leading comma
  let j = closeIdx - 1
  while (j >= 0 && /\s/.test(src[j])) j--
  const prevChar = src[j]
  const needLeadingComma = prevChar !== ',' && prevChar !== '{'

  const header = `\n\n  // ==== Auto-filled by scripts/i18n-fill-ai.js (${new Date().toISOString()}) ====\n`
  const body = entries.map(e => `  ${encodeJsString(e.key)}: ${encodeJsString(e.value)}`).join(',\n')
  const insertion = (needLeadingComma ? ',' : '') + header + body + '\n'
  const newSrc = src.slice(0, closeIdx) + insertion + src.slice(closeIdx)

  if (!DRY_RUN) {
    fs.writeFileSync(filePath + '.bak', src, 'utf8')
    fs.writeFileSync(filePath, newSrc, 'utf8')
  }
}

// ---------- AI ----------
const SYSTEM_PROMPT = (targetLangName, note) => `You are a professional i18n translator for QuantDinger — a web application covering crypto/stock charts, technical indicators, backtesting, live trading, and automated trading bots.

TARGET LANGUAGE: ${targetLangName}
${note ? 'STYLE GUIDANCE: ' + note : ''}

RULES (critical, follow exactly):
1. Input is a JSON object mapping key -> { "zh": "Simplified Chinese source", "en": "English reference" }
2. Output ONLY a JSON object mapping the same keys -> translated string value. No markdown, no prose, no code fences, no explanation.
3. Preserve every placeholder exactly: {name}, {count}, %d, %s, %.2f, {{variable}}, HTML tags, newlines \\n.
4. Do NOT translate these proper nouns/terms: BTC, ETH, USDT, USDC, API, OKX, Binance, Bitget, Bybit, Gate, MEXC, HTX, Kraken, TradingView, RSI, MACD, EMA, SMA, VWAP, Bollinger, ATR, QuantDinger, AI, JSON.
5. Trading/finance domain vocabulary must be accurate:
   - 做多/做空/持仓/平仓/加仓/止盈/止损/回测/实盘/挂单/市价/限价/网格/马丁格尔/定投/趋势/资金费率/杠杆 etc.
6. UI labels should be short and idiomatic — don't translate "确定" into a full sentence; use the target language's standard "OK / Confirm" equivalent.
7. For error/validation messages, keep the tone consistent (imperative / descriptive).
8. Do NOT add surrounding quotes inside the JSON value; just the translated text.

Return ONLY valid JSON.`

function buildUserPayload (batch /* [{key, zh, en}] */) {
  const obj = {}
  for (const it of batch) {
    obj[it.key] = { zh: it.zh, en: it.en || '' }
  }
  return 'Translate the following UI strings. Return a JSON object mapping each key to the translated value (no wrapping, no extra keys):\n\n' + JSON.stringify(obj, null, 2)
}

async function callAI (systemPrompt, userPrompt) {
  const body = P.buildBody(MODEL, systemPrompt, userPrompt)
  const resp = await fetch(P.url, {
    method: 'POST',
    headers: P.headers(API_KEY),
    body: JSON.stringify(body)
  })
  if (!resp.ok) {
    const txt = await resp.text()
    throw new Error(`HTTP ${resp.status}: ${txt.slice(0, 500)}`)
  }
  const j = await resp.json()
  const text = P.extractText(j)
  if (!text) throw new Error('Empty response from AI: ' + JSON.stringify(j).slice(0, 500))
  return text
}

function parseJSONResponse (text) {
  // Strip possible code fences
  let t = text.trim()
  if (t.startsWith('```')) {
    t = t.replace(/^```[a-z]*\n?/i, '').replace(/\n?```\s*$/, '')
  }
  // Some models prepend explanations; find the first { and last }
  const first = t.indexOf('{')
  const last = t.lastIndexOf('}')
  if (first < 0 || last < 0) throw new Error('No JSON object found in response')
  const jsonStr = t.slice(first, last + 1)
  return JSON.parse(jsonStr)
}

async function translateBatch (batch, systemPrompt) {
  const userPrompt = buildUserPayload(batch)
  let lastErr = null
  for (let attempt = 1; attempt <= RETRY; attempt++) {
    try {
      const text = await callAI(systemPrompt, userPrompt)
      const obj = parseJSONResponse(text)
      // Validate: all expected keys present
      const result = {}
      let missing = 0
      for (const it of batch) {
        const v = obj[it.key]
        if (typeof v === 'string' && v.length > 0) {
          result[it.key] = v
        } else {
          missing++
        }
      }
      if (missing > 0 && missing === batch.length) {
        throw new Error('All keys missing in translation response')
      }
      if (missing > 0) {
        console.warn(`[!] Batch returned ${missing}/${batch.length} missing keys, keeping partial`)
      }
      return result
    } catch (e) {
      lastErr = e
      console.warn(`[retry ${attempt}/${RETRY}] batch failed: ${e.message}`)
      await new Promise(r => setTimeout(r, 1500 * attempt))
    }
  }
  throw lastErr
}

// ---------- main per-lang flow ----------
function loadCache (lang) {
  if (NO_CACHE) return {}
  const p = path.join(CACHE_DIR, `${lang}.json`)
  if (!fs.existsSync(p)) return {}
  try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return {} }
}
function saveCache (lang, data) {
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true })
  fs.writeFileSync(path.join(CACHE_DIR, `${lang}.json`), JSON.stringify(data, null, 2), 'utf8')
}

async function processLang (lang, baseEntries, refEntries) {
  const filePath = path.join(LANG_DIR, `${lang}.js`)
  const { map: targetMap } = extractEntries(filePath)
  const meta = LANG_META[lang] || { name: lang, note: '' }

  // master key set = union(base, ref), preserve base order first then extra ref keys
  const masterKeys = []
  const seen = new Set()
  for (const k of baseEntries.keys()) {
    masterKeys.push(k); seen.add(k)
  }
  for (const k of refEntries.keys()) {
    if (!seen.has(k)) { masterKeys.push(k); seen.add(k) }
  }

  const missing = masterKeys.filter(k => !targetMap.has(k))
  console.log(`\n[${lang}] ${meta.name}  existing=${targetMap.size}  missing=${missing.length}`)
  if (missing.length === 0) return

  const cache = loadCache(lang)
  const todo = missing.filter(k => !cache[k])
  const cached = missing.length - todo.length
  if (cached > 0) console.log(`[${lang}] ${cached} from cache`)

  if (todo.length > 0) {
    const system = SYSTEM_PROMPT(meta.name, meta.note)
    const batches = []
    for (let i = 0; i < todo.length; i += BATCH) {
      const slice = todo.slice(i, i + BATCH).map(k => ({
        key: k,
        zh: baseEntries.get(k) || refEntries.get(k) || '',
        en: refEntries.get(k) || ''
      }))
      batches.push(slice)
    }
    console.log(`[${lang}] translating ${todo.length} keys in ${batches.length} batch(es), concurrency=${CONCURRENCY}`)

    let done = 0
    async function runOne (batch, idx) {
      const t0 = Date.now()
      const out = await translateBatch(batch, system)
      Object.assign(cache, out)
      saveCache(lang, cache)
      done += batch.length
      console.log(`[${lang}] batch ${idx + 1}/${batches.length}  ${batch.length} keys  ${(Date.now() - t0)}ms  (total ${done}/${todo.length})`)
    }

    const queue = batches.map((b, i) => ({ b, i }))
    const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
      while (queue.length) {
        const { b, i } = queue.shift()
        try { await runOne(b, i) } catch (e) {
          console.error(`[${lang}] batch ${i + 1} FAILED: ${e.message}`)
        }
      }
    })
    await Promise.all(workers)
  }

  // Build final entries array
  const entries = missing
    .filter(k => typeof cache[k] === 'string' && cache[k].length > 0)
    .map(k => ({ key: k, value: cache[k] }))

  console.log(`[${lang}] writing ${entries.length}/${missing.length} entries ${DRY_RUN ? '(dry-run, not saved)' : 'to file'}`)
  if (entries.length > 0) {
    appendEntriesToFile(filePath, lang, entries)
  }
}

async function main () {
  console.log(`Provider: ${PROVIDER}  model: ${MODEL}  batch: ${BATCH}  concurrency: ${CONCURRENCY}  dry-run: ${DRY_RUN}`)
  const { map: baseEntries } = extractEntries(path.join(LANG_DIR, `${BASE}.js`))
  const { map: refEntries } = extractEntries(path.join(LANG_DIR, `${REF}.js`))
  console.log(`Base (${BASE}) keys=${baseEntries.size}  Ref (${REF}) keys=${refEntries.size}`)

  for (const lang of TARGET_LANGS) {
    try {
      await processLang(lang, baseEntries, refEntries)
    } catch (e) {
      console.error(`[${lang}] FATAL: ${e.message}`)
    }
  }

  console.log('\nDone. Run `node scripts/i18n-diff.js` to verify.')
}

main().catch(e => { console.error(e); process.exit(1) })
