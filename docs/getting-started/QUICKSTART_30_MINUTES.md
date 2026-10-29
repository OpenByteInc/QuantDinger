# Run QuantDinger in 30 minutes

This path is for a newly installed instance. The goal is a safe end-to-end loop through authentication, AI, data, research, strategy authoring, and backtesting without real capital.

## Before you start

Confirm the web app and `http://127.0.0.1:5000/api/health` work, prepare one model API key, and keep all secrets out of screenshots and support messages. This guide uses research, backtest, paper, and signal modes only.

## Minutes 0–5: security and product mode

In **Settings**, review **Product features**, **Security & Authentication**, and **Registration & Security**. Personal deployments normally do not need billing or public registration. Save, sign in again, and verify the effective state. Public and SaaS systems must complete [post-installation setup](POST_INSTALL_SETUP.md) first.

## Minutes 5–10: AI

Open **Settings → AI / LLM**, select one provider, enter only its key, model, and required Base URL, keep advanced tuning at defaults, save, and run the connection test. Provider switching does not require deleting other provider settings. Keys remain server-side.

## Minutes 10–15: data

Open **Professional report data** and **Data status & sync**. Enable only the capabilities needed for the target market, inspect status and freshness, and do not validate performance against stale or incomplete data.

## Minutes 15–20: first research

Open **AI Research**, select market and instrument, verify timestamp, currency, and source, then run a report. Review quality, risk warnings, evidence, and scenarios rather than the conclusion alone. See [AI Research](../product/AI_RESEARCH.md).

## Minutes 20–27: strategy and backtest

Start from a Strategy API V2 template, match the research market, validate source and manifest, save a version, then open **Backtest**. Set the range, capital, commission, and slippage. Review benchmark, drawdown, trades, fees, and data warnings. See [Backtest Center](../product/BACKTEST_CENTER.md).

## Minutes 27–30: safe next step

Save the report, strategy version, and result. Test one notification channel. Connect only a paper or read-only account, and begin with `signal`, never immediate `live` execution.

## Completion criteria

One model and data source work, the report has evidence and quality signals, one strategy validates and backtests, real execution remains disabled, and the operator knows where to find logs, backups, and the error knowledge base.

Continue with the [integration guide](../deployment/INTEGRATION_GUIDE.md) or [live-trading safety](../trading/LIVE_TRADING_SAFETY.md).

