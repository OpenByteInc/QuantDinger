# Broker, data, and AI integrations

This page defines a consistent onboarding workflow for external services. Availability depends on the build, account permissions, region, and provider policy; the options shown by your deployed instance are authoritative.

## Common onboarding workflow

1. Create a dedicated provider account or application; do not reuse a personal master key.
2. Grant only required permissions and disable withdrawals, transfers, and unnecessary trading scopes.
3. Save credentials in the corresponding Settings page, never in chat, screenshots, logs, or Git.
4. Run the connection check and verify clock, network, proxy, and allowlists.
5. Query accounts and market data before completing a paper or otherwise risk-free test.
6. Assign an owner and define expiration, rotation, and revocation.

## Brokers and exchanges

| Asset class | Common integrations | Prerequisites | First acceptance test |
| --- | --- | --- | --- |
| US equities | Alpaca, IBKR, Futu/Moomoo OpenD, Webull OpenAPI, Tiger, depending on build | Region and account permissions; some use a local gateway | Account, cash, positions, orders, paper order |
| Hong Kong equities | Futu/Moomoo OpenD, depending on build | Running and unlocked OpenD | Quotes, positions, orders, currency |
| Crypto | Exchange adapters available in the current build | API key, secret, IP allowlist, market permissions | Clock, balances, symbol, paper or minimum-size order |

Local desktop gateways such as IBKR and Futu/Moomoo must be reachable from the backend. From a container, `127.0.0.1` is the container itself, not the host; use the platform's host address and restrict exposed ports.

Read [Live-trading safety](../trading/LIVE_TRADING_SAFETY.md) before enabling production execution.

## Data and research providers

QuantDinger can combine public market data, macro data, universes, and professional report providers. Validate in this order:

1. Configure a provider under Settings → Professional report data.
2. Inspect connectivity, synchronization time, coverage, and errors under Data status & sync.
3. Confirm symbols, frequency, timezone, and adjustment policy in Market.
4. Generate a small AI Research report and inspect data-quality and risk fields.
5. Add usage budgets, caching, and failure fallback for paid or rate-limited providers.

Public sources may be delayed, incomplete, or license restricted. Production decisions should record source, timestamp, and quality state rather than treating one public source as exchange-grade realtime data.

## AI / LLM

Settings → AI / LLM selects the provider, model, API key, and advanced parameters. Depending on the build, the UI may offer OpenAI, Google, DeepSeek, Grok, AtlasCloud, and custom OpenAI-compatible services.

- Select the provider before entering its credential and model name.
- Use a trusted HTTPS endpoint for custom services and verify the compatible API path.
- Set bounded timeouts, retries, concurrency, and daily budget.
- Do not send unredacted account data, secrets, personal data, or restricted market data to a third-party model.
- Run a minimal research job to verify text, structured fields, error handling, and cost accounting.

## Notifications and sign-in

- [SMTP email](NOTIFICATION_EMAIL_CONFIG_EN.md)
- [Twilio SMS](NOTIFICATION_SMS_CONFIG_EN.md)
- [Telegram](NOTIFICATION_TELEGRAM_CONFIG_EN.md)
- [OAuth](OAUTH_CONFIG_EN.md)

## Go-live checklist

- Credentials use minimum privileges and have a rotation record.
- Test and production credentials are isolated.
- Accounts, positions, orders, currencies, and timezones render correctly.
- Create, pause, resume, and incident handling are tested in paper mode.
- Disconnect, rate-limit, and expired-key failures produce actionable messages.
- Logs contain no secrets, tokens, complete account numbers, or personal data.

Use the [Error knowledge base](ERROR_KNOWLEDGE_BASE.md) when collecting diagnostics.
