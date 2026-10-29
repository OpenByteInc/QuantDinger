# First run with QuantDinger

This guide connects installation, configuration, and the first validated workflow. Complete research, backtesting, and signal mode before using real capital.

## 1. Start and sign in

Start with the [installation overview](INSTALLATION.md) and choose Windows, Linux/macOS, cloud, SaaS, or mobile delivery. Open the web app, sign in with the administrator created during deployment, and complete [post-installation setup](POST_INSTALL_SETUP.md) immediately.

If the page, database, or containers fail to start, use [installation troubleshooting](../deployment/INSTALL_TROUBLESHOOTING.md). Complete [production hardening](../deployment/PRODUCTION_HARDENING.md) before exposing the service publicly.

## 2. Complete the base configuration

Verify these items in administration settings:

- Site URL, time zone, and default language are correct.
- At least one AI model provider works; provider secrets remain server-side.
- A data provider returns history for the intended market and frequency.
- Required email, SMS, or Telegram channels can send a test notification.
- Administrator and regular-user roles are separated on multi-user deployments.

See [administrator troubleshooting](../deployment/ADMIN_AND_SETTINGS_TROUBLESHOOTING_EN.md) and [multi-user operation](../deployment/MULTI_USER_SETUP.md).

## 3. Run the first AI research task

Open AI Research, choose a market and instrument, verify the quote time and data source, and run one analysis. Check freshness and evidence before relying on model output. See the [AI Research guide](../product/AI_RESEARCH.md).

## 4. Run the first strategy backtest

Start with a Strategy API V2 template:

1. Select a template and define the market, instruments, and frequency.
2. Validate source and manifest until all compile errors are resolved.
3. Choose the range, initial capital, and cost assumptions in Backtest Center.
4. Review the benchmark, drawdown, order ledger, and execution assumptions instead of return alone.
5. Save the source and result for later comparison.

Follow the [strategy workflow](../product/STRATEGY_WORKFLOW.md) and [Backtest Center guide](../product/BACKTEST_CENTER.md). The complete programming contract is in the [Strategy API V2 guide](../trading/STRATEGY_DEV_GUIDE.md).

## 5. Connect an account and validate signals

Test a newly added trading account and grant only the required permissions. Create the first deployment in `signal` mode. Confirm notification frequency, state restoration, and intended direction before live execution. A successful backtest does not prove that credentials, balances, order limits, or network health are ready.

Complete the [live-trading safety checklist](../trading/LIVE_TRADING_SAFETY.md) before using real funds.

## 6. Optional: connect an Agent

To use QuantDinger from Codex, Cursor, or Claude Code, issue an Agent Token with the minimum scopes and follow [MCP setup](../agent/MCP_SETUP.md). Never give an Agent an administrator password or a human JWT.

## Completion criteria

- Sign-in is stable and administration settings show no blocking error.
- AI Research can load current data for the target instrument.
- At least one strategy validates and completes a backtest.
- A person has reviewed the result range, costs, and limitations.
- Notification tests pass while live execution remains stopped or in signal mode.
