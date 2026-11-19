# QuantDinger Strategies

This folder contains QuantDinger Strategy API V2 strategies in a flat, copy-friendly layout.

## Usage

1. Open a strategy `.py` file and copy its complete source.
2. Create a strategy in the QuantDinger strategy editor and paste the source.
3. Review its instrument, product type, timeframe, direction, protection, and parameters.
4. Compile and rerun the backtest. Review orders, fills, positions, closed trades, and the equity curve before considering paper or live deployment.

Each strategy declares its own execution contract. Review the source before reuse instead of assuming that every file has the same instrument scope, product type, direction, or timeframe.

## Validation notes

Every strategy listed in the validation table passed the Strategy API V2 compiler and the same QuantDinger backtest engine used by asynchronous backtest jobs when it was added. Validation covered market-data loading, sandbox execution, matching, commissions, slippage, the order ledger, fills, trades, and the equity curve.

The returns and drawdowns below are historical acceptance results. They show that the strategy executed and produced auditable trades in that test; they do not predict future performance or suitability for any market.

| # | Strategy | Python file | Timeframe | Status | Return | Max drawdown | Trades |
| ---: | --- | --- | ---: | --- | ---: | ---: | ---: |
| 001 | Bollinger Band Breakout | [001_quantdinger_bollinger_breakout.py](./001_quantdinger_bollinger_breakout.py) | 1h | Passed | -3.42% | -20.67% | 45 |
| 002 | Donchain BO | [002_quantdinger_donchian_breakout.py](./002_quantdinger_donchian_breakout.py) | 1h | Passed | -3.05% | -18.82% | 31 |
| 003 | EMA Long Only Strategy | [003_quantdinger_ema_atr_long.py](./003_quantdinger_ema_atr_long.py) | 1h | Passed | -8.99% | -23.00% | 34 |
| 004 | On Balance Volume Oscillator Strategy Quantnomad | [004_quantdinger_obv_oscillator.py](./004_quantdinger_obv_oscillator.py) | 1h | Passed | -5.03% | -21.17% | 104 |
| 005 | Simple Moving Average Crossover | [005_quantdinger_dual_ma_angle.py](./005_quantdinger_dual_ma_angle.py) | 1h | Passed | 2.14% | -26.01% | 42 |
| 006 | Trend Reversal Alerts Strategy 4H 3M | [006_quantdinger_trend_reversal.py](./006_quantdinger_trend_reversal.py) | 1h | Passed | -34.00% | -36.64% | 220 |
| 007 | EVWMA VWAP Cross [QuantNomad] | [007_quantdinger_evwma_vwap_cross.py](./007_quantdinger_evwma_vwap_cross.py) | 1h | Passed | -10.72% | -35.93% | 51 |
| 008 | Highest High and Lowest Low Channel Strategy | [008_quantdinger_high_low_channel.py](./008_quantdinger_high_low_channel.py) | 1h | Passed | -9.64% | -33.07% | 62 |
| 009 | Support Resistance Strategy | [009_quantdinger_support_resistance.py](./009_quantdinger_support_resistance.py) | 1h | Passed | -10.69% | -23.98% | 81 |
| 010 | SSL Channel Cross | [010_quantdinger_ssl_channel.py](./010_quantdinger_ssl_channel.py) | 1h | Passed | -6.00% | -22.05% | 192 |
| 011 | ATR Trailing Stoploss Strategy | [011_quantdinger_atr_trailing_line.py](./011_quantdinger_atr_trailing_line.py) | 1h | Passed | -25.12% | -36.94% | 161 |
| 012 | Uhl MA System - Strategy Analysis | [012_quantdinger_uhl_ma_system.py](./012_quantdinger_uhl_ma_system.py) | 1h | Passed | -7.38% | -35.83% | 33 |
| 013 | Ichimoku Cloud Back Test | [013_quantdinger_ichimoku_cloud.py](./013_quantdinger_ichimoku_cloud.py) | 1h | Passed | -11.93% | -37.99% | 70 |
| 014 | EVWMA VWAP MACD Strategy [QuantNomad] | [014_quantdinger_evwma_vwap_macd.py](./014_quantdinger_evwma_vwap_macd.py) | 1h | Passed | -17.91% | -21.70% | 100 |
| 015 | Turtle System | [015_quantdinger_turtle_system.py](./015_quantdinger_turtle_system.py) | 1h | Passed | 7.07% | -21.22% | 34 |
| 016 | Backtest single EMA cross | [016_quantdinger_dual_ema_cross.py](./016_quantdinger_dual_ema_cross.py) | 1h | Passed | -14.06% | -30.28% | 113 |
| 017 | Fractal Breakout Strategy | [017_quantdinger_fractal_breakout.py](./017_quantdinger_fractal_breakout.py) | 1h | Passed | -15.22% | -31.49% | 109 |
| 018 | Grover Llorens Activator | [018_quantdinger_grover_activator.py](./018_quantdinger_grover_activator.py) | 1h | Passed | 6.74% | -28.41% | 21 |
| 019 | Hhll Strategy | [019_quantdinger_hhll_bollinger.py](./019_quantdinger_hhll_bollinger.py) | 1h | Passed | -7.93% | -33.60% | 29 |
| 020 | Hulltsibot | [020_quantdinger_hull_tsi.py](./020_quantdinger_hull_tsi.py) | 1h | Passed | -26.31% | -33.68% | 158 |
| 021 | KPL Swing Strategy | [021_quantdinger_kpl_swing.py](./021_quantdinger_kpl_swing.py) | 1h | Passed | -10.36% | -34.12% | 61 |
| 022 | MA 10/20 Crossover | [022_quantdinger_sma_10_20_cross.py](./022_quantdinger_sma_10_20_cross.py) | 1h | Passed | -17.03% | -28.79% | 151 |
| 023 | Optimized Rsi Strategy Buy The Dips By Coinrule | [023_quantdinger_rsi_buy_dips.py](./023_quantdinger_rsi_buy_dips.py) | 1h | Passed | -0.96% | -5.42% | 17 |
| 024 | Price Action Bollinger Strategy | [024_quantdinger_price_action_bollinger.py](./024_quantdinger_price_action_bollinger.py) | 1h | Passed | -2.34% | -21.38% | 14 |
| 025 | Ut Bot Strategy | [025_quantdinger_ut_bot.py](./025_quantdinger_ut_bot.py) | 1h | Passed | -22.10% | -36.61% | 270 |
| 026 | Heikin Ashi Price Action Crypto Long Strategy | [026_quantdinger_heikin_ashi_price_action.py](./026_quantdinger_heikin_ashi_price_action.py) | 1h | Passed | -13.81% | -20.27% | 95 |
| 027 | State-aware MA Cross Strategy | [027_quantdinger_state_aware_ma_cross.py](./027_quantdinger_state_aware_ma_cross.py) | 1h | Passed | -9.55% | -28.91% | 140 |
| 028 | Double Exponential Moving Average 8-20-63 Strategy | [028_quantdinger_triple_dema.py](./028_quantdinger_triple_dema.py) | 1h | Passed | -9.05% | -26.07% | 84 |
| 029 | Crypto ZigZag RSI strategy 15min | [029_quantdinger_zigzag_rsi_15m.py](./029_quantdinger_zigzag_rsi_15m.py) | 15m | Passed | 4.76% | -15.57% | 15 |
| 030 | Mov Avg Speed Strategy | [030_quantdinger_three_signal_trend.py](./030_quantdinger_three_signal_trend.py) | 1h | Passed | -20.97% | -31.66% | 64 |
| 031 | Parabolic Sar Strategy On Close Quantnomad | [031_quantdinger_parabolic_sar_close.py](./031_quantdinger_parabolic_sar_close.py) | 1h | Passed | -13.03% | -25.08% | 165 |
| 032 | AlphaTrend Strategy | [032_quantdinger_alpha_trend.py](./032_quantdinger_alpha_trend.py) | 1h | Passed | -7.29% | -26.42% | 63 |
| 033 | LUBE | [033_quantdinger_friction_trend.py](./033_quantdinger_friction_trend.py) | 30m | Passed | -11.16% | -12.14% | 18 |
| 034 | Atr Parabolic Sar Strategy Quantnomad | [034_quantdinger_atr_parabolic_sar.py](./034_quantdinger_atr_parabolic_sar.py) | 1h | Passed | 1.06% | -28.19% | 42 |
| 035 | ATR Trailing Stop Strategy by ceyhun | [035_quantdinger_dual_atr_trails.py](./035_quantdinger_dual_atr_trails.py) | 1h | Passed | -11.19% | -31.18% | 74 |
| 036 | RSI Strategy with alerts via TradingConnector to Forex | [036_quantdinger_rsi_reversal.py](./036_quantdinger_rsi_reversal.py) | 1h | Passed | -13.93% | -26.87% | 22 |
| 037 | scalping against trapped countertrend | [037_quantdinger_trapped_countertrend.py](./037_quantdinger_trapped_countertrend.py) | 1h | Passed | 13.49% | -24.68% | 42 |
| 038 | Crossing Moving Averages with ADX Filter) | [038_quantdinger_ma_adx_filter.py](./038_quantdinger_ma_adx_filter.py) | 1h | Passed | 9.02% | -22.08% | 24 |
| 039 | Moving Average Scalper | [039_quantdinger_moving_average_scalper.py](./039_quantdinger_moving_average_scalper.py) | 1h | Passed | -1.64% | -5.84% | 9 |
| 040 | RSI Trend Reversal | [040_quantdinger_heikin_rsi_threshold.py](./040_quantdinger_heikin_rsi_threshold.py) | 1h | Passed | 4.99% | -26.35% | 28 |
| 041 | Scalping Trading System Bot Crypto And Stocks | [041_quantdinger_scalping_system.py](./041_quantdinger_scalping_system.py) | 1h | Passed | 0.93% | -27.70% | 22 |
| 042 | Turtle trading strategy (Donchian/ATR) | [042_quantdinger_turtle_donchian_atr.py](./042_quantdinger_turtle_donchian_atr.py) | 1h | Passed | 8.69% | -10.72% | 34 |
| 043 | Peri Bollinger Mean Reversion V2 | [043_quantdinger_bollinger_rsi_reversion.py](./043_quantdinger_bollinger_rsi_reversion.py) | 1h | Passed | -10.25% | -12.62% | 35 |
| 044 | Kaufman Adaptive Moving Average (KAMA) Strategy [TradeDots] | [044_quantdinger_kama_slope.py](./044_quantdinger_kama_slope.py) | 1h | Passed | 1.60% | -17.08% | 24 |
| 045 | Ut Bot Strategy With Backtesting Range Quantnomad | [045_quantdinger_ut_bot_range.py](./045_quantdinger_ut_bot_range.py) | 1h | Passed | -22.10% | -36.61% | 270 |
| 046 | Previous Day High and Low Breakout Strategy | [046_quantdinger_previous_day_breakout.py](./046_quantdinger_previous_day_breakout.py) | 1h | Passed | -11.63% | -33.64% | 56 |
| 047 | Double Bottom and Top Hunter | [047_quantdinger_double_extreme_hunter.py](./047_quantdinger_double_extreme_hunter.py) | 1h | Passed | -7.45% | -23.18% | 12 |
| 048 | Super Scalper - 5 Min 15 Min | [048_quantdinger_super_scalper_15m.py](./048_quantdinger_super_scalper_15m.py) | 15m | Passed | -29.50% | -36.03% | 300 |
| 049 | EMA Slope Trend Follower | [049_quantdinger_ema_slope_trend.py](./049_quantdinger_ema_slope_trend.py) | 1h | Passed | 4.11% | -8.22% | 40 |
| 050 | Combo Backtest 123 Reversal Rsi | [050_quantdinger_reversal_rsi_combo.py](./050_quantdinger_reversal_rsi_combo.py) | 1h | Passed | -13.65% | -29.92% | 36 |
| 051 | Combo Backtest 123 Reversal Statistical Volatility | [051_quantdinger_reversal_volatility_combo.py](./051_quantdinger_reversal_volatility_combo.py) | 1h | Passed | -13.40% | -26.06% | 21 |
| 052 | Pivot Point SuperTrend [Backtest] | [052_quantdinger_pivot_supertrend.py](./052_quantdinger_pivot_supertrend.py) | 1h | Passed | -7.58% | -32.48% | 47 |
| 053 | Combo 2 20 Ema Accelerator Oscillator Ac | [053_quantdinger_ema_accelerator_combo.py](./053_quantdinger_ema_accelerator_combo.py) | 1h | Passed | -43.75% | -43.88% | 357 |
| 054 | QuantNomad - Heikin-Ashi PSAR Strategy | [054_quantdinger_heikin_psar.py](./054_quantdinger_heikin_psar.py) | 1h | Passed | -12.30% | -26.84% | 153 |
| 055 | Ut Bot V2 Atr Trailing Stop | [055_quantdinger_ut_bot_v2.py](./055_quantdinger_ut_bot_v2.py) | 1h | Passed | -22.10% | -36.61% | 270 |
| 056 | Accurate BUY & SELL 5 mins TF by RR | [056_quantdinger_accurate_5m_composite.py](./056_quantdinger_accurate_5m_composite.py) | 5m | Passed | -94.96% | -94.97% | 1480 |
| 057 | Adaptive Trend Flow Strategy with Filters for SPX | [057_quantdinger_adaptive_trend_flow.py](./057_quantdinger_adaptive_trend_flow.py) | 1h | Passed | -29.05% | -37.68% | 163 |
| 058 | ADX \| DMI Trend | [058_quantdinger_adx_dmi_trend.py](./058_quantdinger_adx_dmi_trend.py) | 1h | Passed | -16.32% | -21.66% | 86 |
| 059 | AlphaTrend | [059_quantdinger_alpha_trend_mfi.py](./059_quantdinger_alpha_trend_mfi.py) | 1h | Passed | -7.29% | -26.42% | 63 |
| 060 | Backtesting on Non-Standard Charts: Caution! [PineCoders] | [060_quantdinger_standard_bar_reversal.py](./060_quantdinger_standard_bar_reversal.py) | 1h | Passed | -60.93% | -61.27% | 440 |
| 061 | Best Supertrend Cci Strategy | [061_quantdinger_supertrend_cci.py](./061_quantdinger_supertrend_cci.py) | 1h | Passed | -18.44% | -33.12% | 208 |
| 062 | Best Trailing Take Profit Strategy | [062_quantdinger_sma_trailing_profit.py](./062_quantdinger_sma_trailing_profit.py) | 1h | Passed | -13.05% | -24.57% | 68 |
| 063 | Best Trend Direction Helper Strategy Edition | [063_quantdinger_wpr_trend_helper.py](./063_quantdinger_wpr_trend_helper.py) | 1h | Passed | 18.00% | -14.97% | 95 |
| 064 | Chandelier Exit - Strategy | [064_quantdinger_chandelier_risk.py](./064_quantdinger_chandelier_risk.py) | 1h | Passed | -12.17% | -29.50% | 96 |
| 065 | CHOP Zone Entry Strategy + DMI/PSAR Exit | [065_quantdinger_chop_dmi_psar.py](./065_quantdinger_chop_dmi_psar.py) | 1h | Passed | 1.92% | -6.03% | 21 |
| 066 | cRSI + Waves Strategy with VWMA overlay | [066_quantdinger_crsi_vwma_waves.py](./066_quantdinger_crsi_vwma_waves.py) | 1h | Passed | -3.85% | -26.39% | 49 |
| 067 | Davin's 10/200MA Pullback on SPY Strategy v2.0 | [067_quantdinger_ma_pullback.py](./067_quantdinger_ma_pullback.py) | 1h | Passed | -13.57% | -21.76% | 38 |
| 068 | [DS]Entry_Exit_TRADE.V01-Strategy | [068_quantdinger_dema_entry_exit.py](./068_quantdinger_dema_entry_exit.py) | 1h | Passed | -31.69% | -35.65% | 134 |
| 069 | Dynamic Swing Anchored VWAP STRATEGY (Zeiierman X PineIndicators) | [069_quantdinger_dynamic_swing_vwap.py](./069_quantdinger_dynamic_swing_vwap.py) | 1h | Passed | -8.85% | -19.11% | 15 |
| 070 | Easy System 420 | [070_quantdinger_easy_system_score.py](./070_quantdinger_easy_system_score.py) | 1h | Passed | 19.90% | -16.75% | 10 |
| 071 | EMA Cross + RSI + ADX - Strategy V2 | [071_quantdinger_ema_rsi_adx.py](./071_quantdinger_ema_rsi_adx.py) | 1h | Passed | -17.80% | -19.28% | 58 |
| 072 | Ema Crossover Strategy | [072_quantdinger_ema_ratio_cross.py](./072_quantdinger_ema_ratio_cross.py) | 1h | Passed | 5.63% | -26.67% | 39 |
| 073 | FlexiSuperTrend - Strategy [presentTrading] | [073_quantdinger_flexi_supertrend.py](./073_quantdinger_flexi_supertrend.py) | 1h | Passed | -21.96% | -31.60% | 37 |
| 074 | Gaussian Channel Strategy | [074_quantdinger_gaussian_channel.py](./074_quantdinger_gaussian_channel.py) | 1h | Passed | -12.92% | -35.30% | 121 |
| 075 | Golden Vector Trend Orchestrator (GVTO)) | [075_quantdinger_golden_vector.py](./075_quantdinger_golden_vector.py) | 1h | Passed | 0.19% | -0.64% | 39 |
| 076 | Gradient Trend Filter STRATEGY [ChartPrime/PineIndicators] | [076_quantdinger_gradient_trend_filter.py](./076_quantdinger_gradient_trend_filter.py) | 1h | Passed | 5.38% | -17.11% | 17 |
| 077 | How To Set Backtest Time Ranges | [077_quantdinger_timed_sma_cross.py](./077_quantdinger_timed_sma_cross.py) | 1h | Passed | -5.95% | -19.72% | 52 |
| 078 | Hull Fisher | [078_quantdinger_hull_fisher.py](./078_quantdinger_hull_fisher.py) | 1h | Passed | -27.62% | -35.55% | 185 |
| 079 | Ichimoku with MACD/ CMF/ TSI | [079_quantdinger_ichimoku_momentum.py](./079_quantdinger_ichimoku_momentum.py) | 1h | Passed | -3.63% | -28.91% | 49 |
| 080 | Kama Strategy Kaufman S Adaptive Moving Average | [080_quantdinger_kama_atr.py](./080_quantdinger_kama_atr.py) | 1h | Passed | -16.23% | -27.38% | 164 |
| 081 | MACD ReLoaded | [081_quantdinger_macd_reloaded.py](./081_quantdinger_macd_reloaded.py) | 1h | Passed | -18.71% | -29.79% | 196 |
| 082 | MACD+RSI | [082_quantdinger_macd_rsi_momentum.py](./082_quantdinger_macd_rsi_momentum.py) | 1h | Passed | -7.59% | -16.74% | 118 |
| 083 | Moving Average Band Strategy | [083_quantdinger_ma_band.py](./083_quantdinger_ma_band.py) | 1h | Passed | -17.31% | -23.29% | 125 |
| 084 | Moving Average Displaced Envelope Atrts | [084_quantdinger_displaced_envelope.py](./084_quantdinger_displaced_envelope.py) | 1h | Passed | -0.71% | -29.87% | 75 |
| 085 | Moving Regression Band Breakout strategy | [085_quantdinger_regression_band.py](./085_quantdinger_regression_band.py) | 1h | Passed | 7.95% | -12.73% | 8 |
| 086 | Mutanabby_AI \| Algo Pro Strategy | [086_quantdinger_engulfing_stability.py](./086_quantdinger_engulfing_stability.py) | 1h | Passed | 8.91% | -15.67% | 26 |
| 087 | Non-Repainting Renko Emulation Strategy [PineIndicators] | [087_quantdinger_renko_emulation.py](./087_quantdinger_renko_emulation.py) | 1h | Passed | -27.62% | -34.01% | 41 |
| 088 | Optimized Trend Tracker | [088_quantdinger_optimized_trend_tracker.py](./088_quantdinger_optimized_trend_tracker.py) | 1h | Passed | -6.91% | -32.72% | 80 |
| 089 | PPS | [089_quantdinger_pivot_supertrend.py](./089_quantdinger_pivot_supertrend.py) | 1h | Passed | -3.17% | -29.42% | 66 |
| 090 | PMax Explorer | [090_quantdinger_pmax_explorer.py](./090_quantdinger_pmax_explorer.py) | 1h | Passed | 7.64% | -25.44% | 29 |
| 091 | PMax on Rsi w T3 Strategy | [091_quantdinger_rsi_t3_pmax.py](./091_quantdinger_rsi_t3_pmax.py) | 1h | Passed | -17.96% | -25.79% | 90 |
| 092 | PTS: Golden Edge | [092_quantdinger_golden_edge.py](./092_quantdinger_golden_edge.py) | 1m | Passed | -46.94% | -49.22% | 338 |
| 093 | Profit Maximizer Strategy Long-Short | [093_quantdinger_pmax_long.py](./093_quantdinger_pmax_long.py) | 1h | Passed | 6.39% | -15.89% | 15 |
| 094 | Quasimodo Pattern Strategy Back Test [TradingFinder] QM Trading | [094_quantdinger_quasimodo.py](./094_quantdinger_quasimodo.py) | 1h | Passed | 17.94% | -5.06% | 2 |
| 095 | RCI Strategy [PineIndicators] | [095_quantdinger_rci_cross.py](./095_quantdinger_rci_cross.py) | 1h | Passed | -39.95% | -43.42% | 313 |
| 096 | Renko Strategy V2 | [096_quantdinger_atr_renko.py](./096_quantdinger_atr_renko.py) | 1h | Passed | 5.40% | -15.11% | 85 |
| 097 | Rsi Divergence Strategy | [097_quantdinger_rsi_divergence.py](./097_quantdinger_rsi_divergence.py) | 1h | Passed | -8.83% | -12.43% | 1 |
| 098 | SMC Strategy with Premium, Equilibrium, and Discount Zones | [098_quantdinger_smc_zones.py](./098_quantdinger_smc_zones.py) | 1h | Passed | -14.73% | -39.73% | 52 |
| 099 | Stochastic Optimized Trend Tracker Strategy | [099_quantdinger_stochastic_ott.py](./099_quantdinger_stochastic_ott.py) | 1h | Passed | 12.77% | -19.51% | 17 |
| 100 | Strategy: HMA 50 + Supertrend Sniper | [100_quantdinger_hma_supertrend.py](./100_quantdinger_hma_supertrend.py) | 1h | Passed | -10.39% | -28.69% | 101 |
| 101 | Super Guppy Strategy | [101_quantdinger_super_guppy.py](./101_quantdinger_super_guppy.py) | 1h | Passed | -9.44% | -33.33% | 61 |
| 102 | SuperTrend Cloud Strategy | [102_quantdinger_supertrend_cloud.py](./102_quantdinger_supertrend_cloud.py) | 1h | Passed | -4.19% | -25.43% | 64 |
| 103 | SuperTrend STRATEGY | [103_quantdinger_supertrend.py](./103_quantdinger_supertrend.py) | 1h | Passed | -3.27% | -29.49% | 66 |
| 104 | TRAILING STOP LOSS TO LONG AND SHORT | [104_quantdinger_continuous_trailing_long.py](./104_quantdinger_continuous_trailing_long.py) | 1h | Passed | -3.44% | -29.30% | 42 |
| 105 | Trend Surfers - Breakout + Alerts | [105_quantdinger_channel_breakout.py](./105_quantdinger_channel_breakout.py) | 1h | Passed | 20.23% | -15.94% | 17 |
| 106 | Triangular Hull Moving Average [BigBeluga X PineIndicators] | [106_quantdinger_triangular_hull.py](./106_quantdinger_triangular_hull.py) | 1h | Passed | -9.05% | -29.79% | 98 |
| 107 | Twin Optimized Trend Tracker | [107_quantdinger_twin_ott.py](./107_quantdinger_twin_ott.py) | 1h | Passed | 5.55% | -22.39% | 11 |
| 108 | Ut Bot V5 | [108_quantdinger_ut_bot_v5.py](./108_quantdinger_ut_bot_v5.py) | 1h | Passed | -12.09% | -22.03% | 103 |
| 109 | Vegas SuperTrend Enhanced - strategy [presentTrading] | [109_quantdinger_vegas_supertrend.py](./109_quantdinger_vegas_supertrend.py) | 1h | Passed | 9.61% | -21.97% | 26 |
| 110 | Vwma With Knn Machine Learning Mfi Adx | [110_quantdinger_vwma_knn.py](./110_quantdinger_vwma_knn.py) | 1h | Passed | -46.09% | -48.58% | 11 |
| 111 | YinYang RSI Volume Trend Strategy | [111_quantdinger_yinyang_rsi_volume.py](./111_quantdinger_yinyang_rsi_volume.py) | 1h | Passed | -3.26% | -10.56% | 66 |
| 112 | 3 Ema Rsi With Trail Stop Free990 Low Tf | [112_quantdinger_3_ema_rsi_with_trail_stop_free990_low_tf.py](./112_quantdinger_3_ema_rsi_with_trail_stop_free990_low_tf.py) | 1h | Passed | -15.72% | -24.00% | 114 |
| 113 | ABCD Strategy | [113_quantdinger_abcd_strategy.py](./113_quantdinger_abcd_strategy.py) | 1h | Passed | -59.73% | -60.34% | 444 |
| 114 | [BACKTEST]Traders Dynamic Index Indicator Alert by JustUncleL | [114_quantdinger_backtest_traders_dynamic_index_indicator_alert_by_just.py](./114_quantdinger_backtest_traders_dynamic_index_indicator_alert_by_just.py) | 1h | Passed | -28.24% | -29.77% | 128 |
| 115 | BBTrend w SuperTrend decision - Strategy [presentTrading] | [115_quantdinger_bbtrend_w_supertrend_decision_strategy_presenttrading.py](./115_quantdinger_bbtrend_w_supertrend_decision_strategy_presenttrading.py) | 1h | Passed | 1.01% | -25.46% | 116 |
| 116 | BTCUSD Supertrend + EMA Trend Filter (1H) | [116_quantdinger_btcusd_supertrend_ema_trend_filter_1h.py](./116_quantdinger_btcusd_supertrend_ema_trend_filter_1h.py) | 1h | Passed | -18.21% | -28.84% | 99 |
| 117 | Bjorgum Double Tap | [117_quantdinger_bjorgum_double_tap.py](./117_quantdinger_bjorgum_double_tap.py) | 1h | Passed | -48.08% | -49.54% | 429 |
| 118 | Volume-Confirmed Support/Resistance Breakout Strategy | [118_quantdinger_volume_confirmed_support_resistance_breakout_strategy.py](./118_quantdinger_volume_confirmed_support_resistance_breakout_strategy.py) | 1h | Passed | 4.74% | -13.99% | 93 |
| 119 | Breaks and Retests - Free990 | [119_quantdinger_breaks_and_retests_free990.py](./119_quantdinger_breaks_and_retests_free990.py) | 1h | Passed | 0.25% | -28.19% | 47 |
| 120 | BSL/SSL Sweep + FVG Strategy (Fixed Entry Logic) | [120_quantdinger_bsl_ssl_sweep_fvg_strategy_fixed_entry_logic.py](./120_quantdinger_bsl_ssl_sweep_fvg_strategy_fixed_entry_logic.py) | 1h | Passed | -34.01% | -36.88% | 141 |
| 121 | Buy&Sell Strategy depends on AO+Stoch+RSI+ATR by SerdarYILMAZ | [121_quantdinger_buy_sell_strategy_depends_on_ao_stoch_rsi_atr_by_serda.py](./121_quantdinger_buy_sell_strategy_depends_on_ao_stoch_rsi_atr_by_serda.py) | 1h | Passed | 9.50% | -9.83% | 32 |
| 122 | BuyTheDip | [122_quantdinger_buythedip.py](./122_quantdinger_buythedip.py) | 1h | Passed | -8.64% | -12.83% | 10 |
| 123 | Сalculation a position size based on risk | [123_quantdinger_alculation_a_position_size_based_on_risk.py](./123_quantdinger_alculation_a_position_size_based_on_risk.py) | 1h | Passed | -5.01% | -26.68% | 52 |
| 124 | Crypto Crew University | [124_quantdinger_crypto_crew_university.py](./124_quantdinger_crypto_crew_university.py) | 1h | Passed | -19.04% | -25.50% | 64 |
| 125 | Crypto Bot Lower Timeframes | [125_quantdinger_crypto_bot_lower_timeframes.py](./125_quantdinger_crypto_bot_lower_timeframes.py) | 1h | Passed | -17.64% | -29.84% | 197 |
| 126 | Delta Rsi Strategy With Filters | [126_quantdinger_delta_rsi_strategy_with_filters.py](./126_quantdinger_delta_rsi_strategy_with_filters.py) | 1h | Passed | -6.99% | -18.85% | 12 |
| 127 | Divergence for Many Indicators v4 ST | [127_quantdinger_divergence_for_many_indicators_v4_st.py](./127_quantdinger_divergence_for_many_indicators_v4_st.py) | 1h | Passed | -43.07% | -45.27% | 182 |
| 128 | Double&Triple Pattern[TS_Indie] | [128_quantdinger_double_triple_pattern_ts_indie.py](./128_quantdinger_double_triple_pattern_ts_indie.py) | 1h | Passed | -53.11% | -53.70% | 438 |
| 129 | Elder Ray Bull Power Tp And Sl | [129_quantdinger_elder_ray_bull_power_tp_and_sl.py](./129_quantdinger_elder_ray_bull_power_tp_and_sl.py) | 1h | Passed | -22.22% | -25.37% | 161 |
| 130 | Ema Rsi Atr Hidden Div Strat 1 Min | [130_quantdinger_ema_rsi_atr_hidden_div_strat_1_min.py](./130_quantdinger_ema_rsi_atr_hidden_div_strat_1_min.py) | 1m | Passed | -45.32% | -45.64% | 266 |
| 131 | Larry Connors Strategy [JoseMetal] | [131_quantdinger_larry_connors_strategy_josemetal.py](./131_quantdinger_larry_connors_strategy_josemetal.py) | 1h | Passed | -76.48% | -77.06% | 776 |
| 132 | Fib Confluence Scanner | [132_quantdinger_fib_confluence_scanner.py](./132_quantdinger_fib_confluence_scanner.py) | 1h | Passed | -16.03% | -24.92% | 60 |
| 133 | Fibonacci Rsi Strategy | [133_quantdinger_fibonacci_rsi_strategy.py](./133_quantdinger_fibonacci_rsi_strategy.py) | 1h | Passed | -12.21% | -21.35% | 60 |
| 134 | Fixed Percent Stop Loss & Take Profit % | [134_quantdinger_fixed_percent_stop_loss_take_profit.py](./134_quantdinger_fixed_percent_stop_loss_take_profit.py) | 1h | Passed | -20.90% | -35.61% | 46 |
| 135 | Flawless Victory Strategy 15Min Btc Machine Learning Strategy | [135_quantdinger_flawless_victory_strategy_15min_btc_machine_learning_s.py](./135_quantdinger_flawless_victory_strategy_15min_btc_machine_learning_s.py) | 15m | Passed | -31.26% | -34.41% | 222 |
| 136 | Forex Fractal EMA Scalper | [136_quantdinger_forex_fractal_ema_scalper.py](./136_quantdinger_forex_fractal_ema_scalper.py) | 1h | Passed | -13.56% | -31.77% | 67 |
| 137 | Bollinger Bands, RSI and ADX Trading System | [137_quantdinger_bollinger_bands_rsi_and_adx_trading_system.py](./137_quantdinger_bollinger_bands_rsi_and_adx_trading_system.py) | 1m | Passed | -84.39% | -84.56% | 974 |
| 138 | Gap Filling Strategy | [138_quantdinger_gap_filling_strategy.py](./138_quantdinger_gap_filling_strategy.py) | 1h | Passed | -30.94% | -34.83% | 136 |
| 139 | Gold Intraday EMA/BB/VWAP + ATR SL/TP + Session/Weekday Filter | [139_quantdinger_gold_intraday_ema_bb_vwap_atr_sl_tp_session_weekday_fi.py](./139_quantdinger_gold_intraday_ema_bb_vwap_atr_sl_tp_session_weekday_fi.py) | 1h | Passed | -44.78% | -48.92% | 294 |
| 140 | Gold Scalping Strategy with Precise Entries | [140_quantdinger_gold_scalping_strategy_with_precise_entries.py](./140_quantdinger_gold_scalping_strategy_with_precise_entries.py) | 1h | Passed | -32.39% | -36.07% | 134 |
| 141 | Golden Trident \| Swing-Anchored VWAP Trend System | [141_quantdinger_golden_trident_swing_anchored_vwap_trend_system.py](./141_quantdinger_golden_trident_swing_anchored_vwap_trend_system.py) | 1h | Passed | -6.68% | -25.18% | 224 |
| 142 | High Low Channel Multi Averages Crypto Swing Strategy | [142_quantdinger_high_low_channel_multi_averages_crypto_swing_strategy.py](./142_quantdinger_high_low_channel_multi_averages_crypto_swing_strategy.py) | 1h | Passed | -3.98% | -21.67% | 92 |
| 143 | Hybrid: RSI + Breakout + Dashboard | [143_quantdinger_hybrid_rsi_breakout_dashboard.py](./143_quantdinger_hybrid_rsi_breakout_dashboard.py) | 1h | Passed | -6.45% | -15.92% | 17 |
| 144 | Ichimoku Backtester with Optional RSI, TP, and SL | [144_quantdinger_ichimoku_backtester_with_optional_rsi_tp_and_sl.py](./144_quantdinger_ichimoku_backtester_with_optional_rsi_tp_and_sl.py) | 1h | Passed | -19.52% | -22.49% | 41 |
| 145 | Js Techtrading Vwap Momentum Pullback Strategy | [145_quantdinger_js_techtrading_vwap_momentum_pullback_strategy.py](./145_quantdinger_js_techtrading_vwap_momentum_pullback_strategy.py) | 1h | Passed | -30.63% | -31.59% | 144 |
| 146 | LANZ Strategy 2.0 [Backtest] | [146_quantdinger_lanz_strategy_2_0_backtest.py](./146_quantdinger_lanz_strategy_2_0_backtest.py) | 1h | Passed | -19.15% | -35.05% | 69 |
| 147 | LANZ Strategy 4.0 [Backtest] | [147_quantdinger_lanz_strategy_4_0_backtest.py](./147_quantdinger_lanz_strategy_4_0_backtest.py) | 1h | Passed | 10.94% | -18.83% | 16 |
| 148 | Linear Regression Channel Breakout Strategy | [148_quantdinger_linear_regression_channel_breakout_strategy.py](./148_quantdinger_linear_regression_channel_breakout_strategy.py) | 1h | Passed | -2.45% | -28.40% | 27 |
| 149 | Liquidity Sweep Breakout - LSB | [149_quantdinger_liquidity_sweep_breakout_lsb.py](./149_quantdinger_liquidity_sweep_breakout_lsb.py) | 1h | Passed | -13.69% | -24.88% | 89 |
| 150 | MACD + BB + RSI [Alorse] | [150_quantdinger_macd_bb_rsi_alorse.py](./150_quantdinger_macd_bb_rsi_alorse.py) | 1h | Passed | -17.53% | -29.84% | 197 |
| 151 | Macd Liquidity Tracker Strategy Quant Trading | [151_quantdinger_macd_liquidity_tracker_strategy_quant_trading.py](./151_quantdinger_macd_liquidity_tracker_strategy_quant_trading.py) | 1h | Passed | -8.81% | -19.07% | 62 |
| 152 | MACD Pullback Sniper \| Trend + ADX Filtered Strategy | [152_quantdinger_macd_pullback_sniper_trend_adx_filtered_strategy.py](./152_quantdinger_macd_pullback_sniper_trend_adx_filtered_strategy.py) | 1h | Passed | -9.18% | -21.02% | 25 |
| 153 | Momentum Sequence Strategy [Herman] | [153_quantdinger_momentum_sequence_strategy_herman.py](./153_quantdinger_momentum_sequence_strategy_herman.py) | 1h | Passed | -50.90% | -56.27% | 417 |
| 154 | Nononsense Forex High Timeframe Trading Absurd Non Repainting | [154_quantdinger_nononsense_forex_high_timeframe_trading_absurd_non_rep.py](./154_quantdinger_nononsense_forex_high_timeframe_trading_absurd_non_rep.py) | 1h | Passed | -27.96% | -31.37% | 128 |
| 155 | Open Close Cross Strategy Norepaint Version By Justunclel | [155_quantdinger_open_close_cross_strategy_norepaint_version_by_justunc.py](./155_quantdinger_open_close_cross_strategy_norepaint_version_by_justunc.py) | 1h | Passed | -57.76% | -60.76% | 490 |
| 156 | Profit Maximizer Strategy | [156_quantdinger_profit_maximizer_strategy.py](./156_quantdinger_profit_maximizer_strategy.py) | 1h | Passed | -22.68% | -34.50% | 38 |
| 157 | Rob Booker - ADX Breakout | [157_quantdinger_rob_booker_adx_breakout.py](./157_quantdinger_rob_booker_adx_breakout.py) | 1h | Passed | -6.18% | -11.23% | 6 |
| 158 | RSI Divergence Strategy | [158_quantdinger_rsi_divergence_strategy.py](./158_quantdinger_rsi_divergence_strategy.py) | 1h | Passed | -43.59% | -45.42% | 184 |
| 159 | Scalping The Bull Two Ema Strategy | [159_quantdinger_scalping_the_bull_two_ema_strategy.py](./159_quantdinger_scalping_the_bull_two_ema_strategy.py) | 1h | Passed | -22.53% | -32.05% | 46 |
| 160 | Bill Williams Alligator improved | [160_quantdinger_bill_williams_alligator_improved.py](./160_quantdinger_bill_williams_alligator_improved.py) | 1h | Passed | 12.01% | -7.09% | 36 |
| 161 | Sequence Snap \| RSI-Confirmed Reversal Scalper | [161_quantdinger_sequence_snap_rsi_confirmed_reversal_scalper.py](./161_quantdinger_sequence_snap_rsi_confirmed_reversal_scalper.py) | 1h | Passed | -41.12% | -42.65% | 225 |
| 162 | Smart Grid Scalping(Pullback) Strategy[BullByte] | [162_quantdinger_smart_grid_scalping_pullback_strategy_bullbyte.py](./162_quantdinger_smart_grid_scalping_pullback_strategy_bullbyte.py) | 1h | Passed | -5.24% | -19.32% | 24 |
| 163 | Stop loss and Take Profit in $$ example | [163_quantdinger_stop_loss_and_take_profit_in_example.py](./163_quantdinger_stop_loss_and_take_profit_in_example.py) | 1h | Passed | -23.07% | -33.67% | 46 |
| 164 | Strategy Rs Open Session Breakout Trader | [164_quantdinger_strategy_rs_open_session_breakout_trader.py](./164_quantdinger_strategy_rs_open_session_breakout_trader.py) | 1h | Passed | -4.62% | -16.50% | 66 |
| 165 | Strategy Template | [165_quantdinger_strategy_template.py](./165_quantdinger_strategy_template.py) | 1h | Passed | -34.47% | -37.54% | 100 |
| 166 | Strategy Ul Price Divergence Strategy V1 0 | [166_quantdinger_strategy_ul_price_divergence_strategy_v1_0.py](./166_quantdinger_strategy_ul_price_divergence_strategy_v1_0.py) | 1h | Passed | -12.33% | -13.10% | 27 |
| 167 | SuperTrend Backtester | [167_quantdinger_supertrend_backtester.py](./167_quantdinger_supertrend_backtester.py) | 1h | Passed | -27.66% | -30.92% | 165 |
| 168 | SuperTrend Regime Confluence | [168_quantdinger_supertrend_regime_confluence.py](./168_quantdinger_supertrend_regime_confluence.py) | 1h | Passed | -5.79% | -16.93% | 127 |
| 169 | ThinkTech AI Signals | [169_quantdinger_thinktech_ai_signals.py](./169_quantdinger_thinktech_ai_signals.py) | 1h | Passed | -25.26% | -29.45% | 114 |
| 170 | TradeIQ - Crazy Scalping Trading Strategy [Kaspricci] | [170_quantdinger_tradeiq_crazy_scalping_trading_strategy_kaspricci.py](./170_quantdinger_tradeiq_crazy_scalping_trading_strategy_kaspricci.py) | 1h | Passed | -22.31% | -37.64% | 134 |
| 171 | TrendLines with Alerts | [171_quantdinger_trendlines_with_alerts.py](./171_quantdinger_trendlines_with_alerts.py) | 1h | Passed | -3.74% | -25.43% | 49 |
| 172 | Triple EMA + RSI + ATR | [172_quantdinger_triple_ema_rsi_atr.py](./172_quantdinger_triple_ema_rsi_atr.py) | 1h | Passed | -17.51% | -23.80% | 114 |
| 173 | Triple EMA Trend | [173_quantdinger_triple_ema_trend.py](./173_quantdinger_triple_ema_trend.py) | 1h | Passed | -11.46% | -17.96% | 18 |
| 174 | Two-Bar Fib Retrace Strategy [Futures] | [174_quantdinger_two_bar_fib_retrace_strategy_futures.py](./174_quantdinger_two_bar_fib_retrace_strategy_futures.py) | 1h | Passed | -6.65% | -26.40% | 56 |
| 175 | VWAP Breakout Strategy + EMAs + Clean Cycle/TP/SL Plots | [175_quantdinger_vwap_breakout_strategy_emas_clean_cycle_tp_sl_plots.py](./175_quantdinger_vwap_breakout_strategy_emas_clean_cycle_tp_sl_plots.py) | 1h | Passed | -54.34% | -57.78% | 384 |
| 176 | Wyckoff Range Strategy by deperp | [176_quantdinger_wyckoff_range_strategy_by_deperp.py](./176_quantdinger_wyckoff_range_strategy_by_deperp.py) | 1h | Passed | -24.20% | -31.59% | 43 |
| 177 | XAUUSD Scalping V2 AGRESIV | [177_quantdinger_xauusd_scalping_v2_agresiv.py](./177_quantdinger_xauusd_scalping_v2_agresiv.py) | 1h | Passed | -31.24% | -34.28% | 140 |
| 178 | Zero-Lag MA Trend Following | [178_quantdinger_zero_lag_ma_trend_following.py](./178_quantdinger_zero_lag_ma_trend_following.py) | 1h | Passed | -31.22% | -35.16% | 87 |
| 179 | 2xTP, SL to entry | [179_quantdinger_2xtp_sl_to_entry.py](./179_quantdinger_2xtp_sl_to_entry.py) | 1h | Passed | -29.49% | -34.01% | 130 |
| 180 | Advanced Position Management [Mr_Rakun] | [180_quantdinger_advanced_position_management_mr_rakun.py](./180_quantdinger_advanced_position_management_mr_rakun.py) | 1h | Passed | -8.59% | -29.63% | 40 |
| 181 | Advanced Vwap Pullback Strategy Trend Template Qualifier | [181_quantdinger_advanced_vwap_pullback_strategy_trend_template_qualifi.py](./181_quantdinger_advanced_vwap_pullback_strategy_trend_template_qualifi.py) | 1d | Passed | -0.29% | -11.08% | 10 |
| 182 | AI Volume Supertrend - Strategy [presentTrading] | [182_quantdinger_ai_volume_supertrend_strategy_presenttrading.py](./182_quantdinger_ai_volume_supertrend_strategy_presenttrading.py) | 1h | Passed | -8.37% | -20.76% | 128 |
| 183 | Automated Bitcoin (BTC) Investment Strategy | [183_quantdinger_automated_bitcoin_btc_investment_strategy.py](./183_quantdinger_automated_bitcoin_btc_investment_strategy.py) | 1h | Passed | -6.51% | -22.41% | 21 |
| 184 | Backtest Template Backtest Terminal | [184_quantdinger_backtest_template_backtest_terminal.py](./184_quantdinger_backtest_template_backtest_terminal.py) | 1h | Passed | -13.46% | -28.01% | 59 |
| 185 | Double AI Super Trend Trading - Strategy [PresentTrading] | [185_quantdinger_double_ai_super_trend_trading_strategy_presenttrading.py](./185_quantdinger_double_ai_super_trend_trading_strategy_presenttrading.py) | 1h | Passed | -8.37% | -20.76% | 128 |
| 186 | Elliott Wave with Supertrend Exit - Strategy [presentTrading] | [186_quantdinger_elliott_wave_with_supertrend_exit_strategy_presenttrad.py](./186_quantdinger_elliott_wave_with_supertrend_exit_strategy_presenttrad.py) | 1h | Passed | -8.27% | -20.67% | 128 |
| 187 | EMA Crossover Strategy with Take Profit and Candle Highlighting | [187_quantdinger_ema_crossover_strategy_with_take_profit_and_candle_hig.py](./187_quantdinger_ema_crossover_strategy_with_take_profit_and_candle_hig.py) | 1h | Passed | -27.95% | -35.86% | 41 |
| 188 | Fibonacci 5 Candles Retracement VERSION 2 - Close Bar | [188_quantdinger_fibonacci_5_candles_retracement_version_2_close_bar.py](./188_quantdinger_fibonacci_5_candles_retracement_version_2_close_bar.py) | 1h | Passed | -15.67% | -24.56% | 60 |
| 189 | Grab Trading System | [189_quantdinger_grab_trading_system.py](./189_quantdinger_grab_trading_system.py) | 1h | Passed | -0.65% | -20.53% | 51 |
| 190 | Kahlman HullMA / WT Cross Strategy | [190_quantdinger_kahlman_hullma_wt_cross_strategy.py](./190_quantdinger_kahlman_hullma_wt_cross_strategy.py) | 1h | Passed | -47.62% | -52.32% | 417 |
| 191 | Keltner Channel ETH/USDT 1H | [191_quantdinger_keltner_channel_eth_usdt_1h.py](./191_quantdinger_keltner_channel_eth_usdt_1h.py) | 1h | Passed | -2.08% | -17.80% | 21 |
| 192 | LuxAlgo SMC Pro Ultimate | [192_quantdinger_luxalgo_smc_pro_ultimate.py](./192_quantdinger_luxalgo_smc_pro_ultimate.py) | 1h | Passed | -45.90% | -46.80% | 151 |
| 193 | Multi-Step FlexiMA - Strategy [presentTrading] | [193_quantdinger_multi_step_flexima_strategy_presenttrading.py](./193_quantdinger_multi_step_flexima_strategy_presenttrading.py) | 1h | Passed | -11.03% | -36.17% | 37 |
| 194 | Multi-Step FlexiSuperTrend - Strategy [presentTrading] | [194_quantdinger_multi_step_flexisupertrend_strategy_presenttrading.py](./194_quantdinger_multi_step_flexisupertrend_strategy_presenttrading.py) | 1h | Passed | 0.90% | -25.55% | 116 |
| 195 | ORB Strategy [LuciTech] | [195_quantdinger_orb_strategy_lucitech.py](./195_quantdinger_orb_strategy_lucitech.py) | 1d | Passed | 23.82% | -10.08% | 7 |
| 196 | Precision Confluence Trading Strategy [JOAT] | [196_quantdinger_precision_confluence_trading_strategy_joat.py](./196_quantdinger_precision_confluence_trading_strategy_joat.py) | 1h | Passed | -5.09% | -10.07% | 7 |
| 197 | Stepped trailing strategy example | [197_quantdinger_stepped_trailing_strategy_example.py](./197_quantdinger_stepped_trailing_strategy_example.py) | 1h | Passed | -32.15% | -32.94% | 208 |
| 198 | SuperATR 7-Step Profit - Strategy [presentTrading]  | [198_quantdinger_superatr_7_step_profit_strategy_presenttrading.py](./198_quantdinger_superatr_7_step_profit_strategy_presenttrading.py) | 1h | Passed | -48.00% | -52.64% | 423 |
| 199 | Supertrend advance | [199_quantdinger_supertrend_advance.py](./199_quantdinger_supertrend_advance.py) | 1h | Passed | -24.59% | -30.63% | 139 |
| 200 | Supply & Demand Zones + Order Block (Pro Fusion) - Auto Order | [200_quantdinger_supply_demand_zones_order_block_pro_fusion_auto_order.py](./200_quantdinger_supply_demand_zones_order_block_pro_fusion_auto_order.py) | 1h | Passed | -15.63% | -27.50% | 98 |
| 201 | The Flash-Strategy (Momentum-RSI, EMA-crossover, ATR) | [201_quantdinger_the_flash_strategy_momentum_rsi_ema_crossover_atr.py](./201_quantdinger_the_flash_strategy_momentum_rsi_ema_crossover_atr.py) | 1h | Passed | -19.81% | -25.88% | 114 |
| 202 | Tradingview Alerts To Mt4 Mt5 Dynamic Variables Non Repainting | [202_quantdinger_tradingview_alerts_to_mt4_mt5_dynamic_variables_non_re.py](./202_quantdinger_tradingview_alerts_to_mt4_mt5_dynamic_variables_non_re.py) | 1h | Passed | -28.29% | -34.01% | 129 |
| 203 | Tradingview Alerts To Mt4 Mt5 Forex Indices Commodities | [203_quantdinger_tradingview_alerts_to_mt4_mt5_forex_indices_commoditie.py](./203_quantdinger_tradingview_alerts_to_mt4_mt5_forex_indices_commoditie.py) | 1h | Passed | -28.29% | -34.01% | 129 |
| 204 | SL1 Pips after TP1 (MA) | [204_quantdinger_sl1_pips_after_tp1_ma.py](./204_quantdinger_sl1_pips_after_tp1_ma.py) | 1h | Passed | -19.22% | -35.70% | 52 |
| 205 | Y-Profit Maximizer Strategy with Exit Points | [205_quantdinger_y_profit_maximizer_strategy_with_exit_points.py](./205_quantdinger_y_profit_maximizer_strategy_with_exit_points.py) | 1h | Passed | -2.21% | -13.24% | 66 |

## Contributing

Contributions are welcome. Add a uniquely named `.py` file to this folder and include compiler and backtest evidence in the pull request. Follow the shared [library contribution requirements](../README.md#contributing).

## Risk notice

Always rerun a strategy with current market data and your own fee, slippage, capital, product, and risk settings. Backtest results are not guarantees of future performance.
