# AQ-QD-AUTH-001 implementation result

## Scope and source basis

- Task: isolated QuantDinger AI Collaborate strategy generation through a local ChatGPT account.
- QuantDinger base: `462cdf9e9c3131296865678f4f7c782e56cc8672` (`origin/main`).
- QuantDinger-Vue base: `622b7d0b085dbcd721978a7f1c03b06dbc04edfb` (`origin/main`).
- AdaptiveQuant governance base: `d4bb3dfb6636a3ec51b416e939649df591baf492` (`origin/main`).
- Approved design: `AQ_QD_CHATGPT_LOCAL_AI_INTEGRATION_ADR_20261025_CN.md` (isolated development only).

No deployed QuantDinger container, production database, broker configuration, trading permission, RiskGate policy, Hermes installation, or TradingAgents installation was changed.

## Implementation

The existing `LLMService`, Strategy API V2 AI workspace, compilation and native backtest engine are reused.

- `api_key` remains the explicit legacy mode.
- `chatgpt_account` is an explicit, fail-closed mode and never falls back to an API key.
- Authentication and refresh remain owned by the official local Codex application. QuantDinger does not read, copy or persist the OAuth credential.
- The official Codex app-server JSON-RPC interface supplies the account model catalog and inference stream.
- A loopback bearer-protected bridge is included only for the isolated Docker development topology, because a Linux container cannot safely inherit the Windows Codex credential store.
- The Vue AI Collaborate workspace exposes the two modes and an account-model selector.

## Runtime evidence

Account discovery on the test machine returned seven account models. A bounded `gpt-6-luna` inference returned the expected fixed marker and did not use an API key fallback.

The automated development-only pipeline performed:

1. model discussion;
2. model revision of an existing Strategy API V2 source (`fast_period` 10 to 12);
3. native Strategy V2 compilation;
4. an ephemeral `--network none` historical backtest with a deterministic synthetic OHLCV fixture;
5. evidence serialization outside production storage.

Evidence identity:

- SPEC SHA-256: `c351251ab22e35c0dced754bfc6fbb944099eec07f1fd2ac7ce2d961e057033f`
- base code SHA-256: `ae4c4ae314240a96360251ee85586c284403559caa2e23632ffe179a614ca272`
- revised candidate SHA-256: `61351905357839e9390e8fdbc6f6148bee621b6e232d7b13ac5f4e9244c9e767`
- isolated evidence Run ID: `AQ-QD-AUTH-001-613519053578`
- engine: `quantdinger-strategy-api-v2`
- result: 13 closed trades, 420 equity points
- explicit commission: `0.001`
- explicit slippage: `0.0005`
- persistence: `false`

This Run ID is a content-derived isolated-proof identifier, not a production QuantDinger database run ID. The synthetic result is not strategy-performance evidence and is not eligible for paper or live promotion.

The browser UI was verified at `http://127.0.0.1:8126/#/strategy-ide?tab=script&draft=1&assetType=script` for authenticated access, explicit mode selection and account model discovery. The first end-to-end UI submission exposed a route wiring defect (`LLMService()` ignored the selected mode); that defect was corrected. The post-fix authenticated browser request then completed at `/api/strategies/ai-workspace/turn` with HTTP 200. The temporary stack was stopped after validation; the URL is therefore the tested development URL, not a permanently deployed service.

## Tests

- Focused account-transport checks: four assertions passed under the repository venv when `TEMP`/`TMP` used the writable `D:\QuanT\.tmp` path.
- Python compile check: passed for the account transport, `LLMService`, route, bridge and proof script.
- Vue focused test: the new explicit-auth-mode test passed.
- Vue full unit command (`node --test tests/unit/*.test.mjs`): 514 passed, 1 unrelated environment failure. The existing `factorLibraryLocalization.test.mjs` hard-codes a missing `D:\QuanT\.worktrees\quantdinger_backend` checkout.
- Vue production build: passed, 2,397 modules transformed; existing bundle-size warning remains.
- Repository `npm run test:unit` is incompatible with Node 24 on Windows because it passes a directory instead of a file glob.

## Security boundary and limitations

- No secret, token, account identifier or generated browser JWT is stored in Git or this report.
- The isolated backtest had no network namespace and no production persistence.
- The browser development stack is not a production deployment.
- Starting the complete QD application also started credentialless background loops and generated repeated failed local Kafka connection attempts. No strategy was running and no order was placed. The two task-scoped containers and their temporary network were removed after validation.
- The account model list proves local account capability, not a universal guarantee that every model invocation is included in every ChatGPT plan. The UI deliberately does not perform API-key fallback.
- A separate, post-fix browser rerun is still required for full UI acceptance.
- A production deployment would require an approved credential-boundary design, service lifecycle, audit policy and explicit acceptance; this change does not authorize it.

## Acceptance status

`CONDITIONAL PASS`: native API-mode behavior is preserved, the corrected browser submission returned HTTP 200, and real account model discovery/inference plus the automated no-copy Strategy V2 compile/backtest pipeline are verified. Full production adoption remains blocked on an approved credential-boundary deployment design and a clean background-service profile for the isolated web stack.
