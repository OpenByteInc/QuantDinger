import json
from typing import Dict, Any, List

class StrategyCompiler:
    def compile(self, config: Dict[str, Any]) -> str:
        """
        Compiles the strategy configuration JSON into executable Python code.
        """
        
        # Extract configurations
        name = config.get('name', 'Generated Strategy')
        entry_rules = config.get('entry_rules', [])
        position_config = config.get('position_config', {})
        pyramiding_rules = config.get('pyramiding_rules', {})
        risk_management = config.get('risk_management', {})
        
        # 1. Imports and Setup
        code = self._get_header(name)
        
        # 2. Parameters (Variables)
        code += self._get_parameters(position_config, pyramiding_rules, risk_management)
        
        # 3. Indicators Calculation
        code += self._get_indicators_calculation(entry_rules)
        
        # 4. Signal Logic (Entry Conditions)
        code += self._get_entry_logic(entry_rules)
        
        # 5. Core Loop (Position Management) - Based on code2.py
        code += self._get_core_loop(position_config, pyramiding_rules, risk_management)
        
        # 6. Output Formatting
        code += self._get_output_section(name, entry_rules)
        
        return code

    def _get_header(self, name):
        return f'''
# Generated Strategy: {name}
import pandas as pd
import numpy as np

# Helper function for checking signals safely
def get_val(arr, i, default=0):
    if i < 0 or i >= len(arr): return default
    return arr[i]
'''

    def _get_parameters(self, pos_config, pyr_rules, risk_mgmt):
        # Default values
        initial_size = pos_config.get('initial_size_pct', 10) / 100.0
        leverage = pos_config.get('leverage', 1)
        max_pyramiding = pos_config.get('max_pyramiding', 0)
        
        pyr_enabled = pyr_rules.get('enabled', False)
        add_size = pyr_rules.get('size_pct', 0) / 100.0 if pyr_enabled else 0
        add_threshold = pyr_rules.get('value', 0) / 100.0
        
        stop_loss = risk_mgmt.get('stop_loss', {})
        sl_enabled = stop_loss.get('enabled', False)
        sl_pct = stop_loss.get('value', 0) / 100.0 if sl_enabled else 0.0
        
        trailing = risk_mgmt.get('trailing_stop', {})
        ts_enabled = trailing.get('enabled', False)
        ts_activation = trailing.get('activation_profit', 0) / 100.0
        ts_callback = trailing.get('callback_pct', 0) / 100.0
        
        return f'''
# ===========================
# 1. Parameters
# ===========================
initial_position_pct = {initial_size}
leverage = {leverage}
max_pyramiding = {max_pyramiding}

# Pyramiding
add_position_pct = {add_size}
add_threshold_pct = {add_threshold} 

# Risk Management
stop_loss_pct = {sl_pct}
take_profit_activation = {ts_activation}
trailing_callback = {ts_callback}
'''

    def _get_indicators_calculation(self, rules):
        code = """
# ===========================
# 2. Indicators Calculation
# ===========================
"""
        calculated = set()
        
        for rule in rules:
            ind = rule.get('indicator')
            params = rule.get('params', {})
            
            if ind == 'supertrend':
                key = f"st_{params.get('period')}_{params.get('multiplier')}"
                if key not in calculated:
                    code += f"""
# SuperTrend ({params.get('period')}, {params.get('multiplier')})
period = {params.get('period', 14)}
multiplier = {params.get('multiplier', 3.0)}
df['hl2'] = (df['high'] + df['low']) / 2
df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
df['basic_upper'] = df['hl2'] + (multiplier * df['atr'])
df['basic_lower'] = df['hl2'] - (multiplier * df['atr'])

final_upper = [0.0] * len(df)
final_lower = [0.0] * len(df)
trend = [1] * len(df)
close_arr = df['close'].values
basic_upper = np.nan_to_num(df['basic_upper'].values)
basic_lower = np.nan_to_num(df['basic_lower'].values)

for i in range(1, len(df)):
    if basic_upper[i] < final_upper[i-1] or close_arr[i-1] > final_upper[i-1]:
        final_upper[i] = basic_upper[i]
    else:
        final_upper[i] = final_upper[i-1]
        
    if basic_lower[i] > final_lower[i-1] or close_arr[i-1] < final_lower[i-1]:
        final_lower[i] = basic_lower[i]
    else:
        final_lower[i] = final_lower[i-1]
        
    prev_trend = trend[i-1]
    if prev_trend == -1 and close_arr[i] > final_upper[i-1]:
        trend[i] = 1
    elif prev_trend == 1 and close_arr[i] < final_lower[i-1]:
        trend[i] = -1
    else:
        trend[i] = prev_trend

df['st_trend'] = trend
df['st_upper'] = final_upper
df['st_lower'] = final_lower
"""
                    calculated.add(key)

            elif ind == 'ema':
                period = params.get('period', 20)
                key = f"ema_{period}"
                if key not in calculated:
                    code += f"\ndf['ema_{period}'] = df['close'].ewm(span={period}, adjust=False).mean()\n"
                    calculated.add(key)

            elif ind == 'rsi':
                period = params.get('period', 14)
                key = f"rsi_{period}"
                if key not in calculated:
                    from app.utils.technical_indicators import rsi_wilder_codegen
                    code += rsi_wilder_codegen(period, f"rsi_{period}")
                    calculated.add(key)

            elif ind == 'macd':
                fast = params.get('fast_period', 12)
                slow = params.get('slow_period', 26)
                signal = params.get('signal_period', 9)
                key = f"macd_{fast}_{slow}_{signal}"
                if key not in calculated:
                    code += f"""
# MACD ({fast}, {slow}, {signal})
exp1 = df['close'].ewm(span={fast}, adjust=False).mean()
exp2 = df['close'].ewm(span={slow}, adjust=False).mean()
df['macd_{fast}_{slow}_{signal}_line'] = exp1 - exp2
df['macd_{fast}_{slow}_{signal}_signal'] = df['macd_{fast}_{slow}_{signal}_line'].ewm(span={signal}, adjust=False).mean()
df['macd_{fast}_{slow}_{signal}_hist'] = df['macd_{fast}_{slow}_{signal}_line'] - df['macd_{fast}_{slow}_{signal}_signal']
"""
                    calculated.add(key)

            elif ind == 'bollinger':
                period = params.get('period', 20)
                std_dev = params.get('std_dev', 2.0)
                key = f"bb_{period}_{std_dev}"
                if key not in calculated:
                    code += f"""
# Bollinger Bands ({period}, {std_dev})
sma = df['close'].rolling(window={period}).mean()
std = df['close'].rolling(window={period}).std()
df['bb_{period}_{std_dev}_upper'] = sma + ({std_dev} * std)
df['bb_{period}_{std_dev}_lower'] = sma - ({std_dev} * std)
df['bb_{period}_{std_dev}_mid'] = sma
"""
                    calculated.add(key)

            elif ind == 'kdj':
                period = params.get('period', 9)
                signal_period = params.get('signal_period', 3)
                k_smooth = params.get('k_smooth', signal_period)
                d_smooth = params.get('d_smooth', signal_period)
                key = f"kdj_{period}_{signal_period}"
                if key not in calculated:
                    from app.utils.technical_indicators import kdj_codegen
                    col_prefix = f"kdj_{period}_{signal_period}"
                    code += kdj_codegen(period, k_smooth, d_smooth, col_prefix)
                    calculated.add(key)

            elif ind == 'ma':
                period = params.get('period', 20)
                ma_type = params.get('ma_type', 'sma')
                key = f"ma_{ma_type}_{period}"
                if key not in calculated:
                    if ma_type == 'ema':
                         code += f"\ndf['ma_{ma_type}_{period}'] = df['close'].ewm(span={period}, adjust=False).mean()\n"
                    else:
                         code += f"\ndf['ma_{ma_type}_{period}'] = df['close'].rolling(window={period}).mean()\n"
                    calculated.add(key)

        return code

    def _get_entry_logic(self, rules):
        code = """
# ===========================
# 3. Entry Signal Logic
# ===========================
# Default False
df['raw_buy'] = False
df['raw_sell'] = False
"""
        conditions_buy = []
        conditions_sell = []
        
        for rule in rules:
            ind = rule.get('indicator')
            params = rule.get('params', {})
            
            if ind == 'supertrend':
                signal = rule.get('signal', 'trend_bullish')
                if signal == 'trend_bullish':
                    conditions_buy.append("(df['st_trend'] == 1) & (df['st_trend'].shift(1) == -1)")
                    conditions_sell.append("(df['st_trend'] == -1) & (df['st_trend'].shift(1) == 1)")
                elif signal == 'is_uptrend':
                    conditions_buy.append("(df['st_trend'] == 1)")
                    conditions_sell.append("(df['st_trend'] == -1)")
            
            elif ind == 'ema':
                period = params.get('period', 20)
                operator = rule.get('operator', 'price_above')
                col = f"df['ema_{period}']"
                if operator == 'price_above':
                    conditions_buy.append(f"(df['close'] > {col})")
                    conditions_sell.append(f"(df['close'] < {col})")
                elif operator == 'price_below':
                    conditions_buy.append(f"(df['close'] < {col})")
                    conditions_sell.append(f"(df['close'] > {col})")
                elif operator == 'cross_up':
                    conditions_buy.append(f"(df['close'] > {col}) & (df['close'].shift(1) <= {col}.shift(1))")
                    conditions_sell.append(f"(df['close'] < {col}) & (df['close'].shift(1) >= {col}.shift(1))")
                elif operator == 'cross_down':
                    conditions_buy.append(f"(df['close'] < {col}) & (df['close'].shift(1) >= {col}.shift(1))")
                    conditions_sell.append(f"(df['close'] > {col}) & (df['close'].shift(1) <= {col}.shift(1))")

            elif ind == 'rsi':
                period = params.get('period', 14)
                operator = rule.get('operator', '<')
                thresh = params.get('threshold', 30)
                col = f"df['rsi_{period}']"
                if operator == '<':
                    conditions_buy.append(f"({col} < {thresh})")
                    conditions_sell.append(f"({col} > {100-thresh})")
                elif operator == '>':
                    conditions_buy.append(f"({col} > {thresh})")
                    conditions_sell.append(f"({col} < {100-thresh})")
                elif operator == 'cross_up':
                    conditions_buy.append(f"({col} > {thresh}) & ({col}.shift(1) <= {thresh})")
                    conditions_sell.append(f"({col} < {100-thresh}) & ({col}.shift(1) >= {100-thresh})")
                elif operator == 'cross_down':
                    conditions_buy.append(f"({col} < {thresh}) & ({col}.shift(1) >= {thresh})")
                    conditions_sell.append(f"({col} > {100-thresh}) & ({col}.shift(1) <= {100-thresh})")

            elif ind == 'macd':
                fast = params.get('fast_period', 12)
                slow = params.get('slow_period', 26)
                signal = params.get('signal_period', 9)
                operator = rule.get('operator', 'diff_gt_dea')
                line_col = f"df['macd_{fast}_{slow}_{signal}_line']"
                sig_col = f"df['macd_{fast}_{slow}_{signal}_signal']"
                
                if operator == 'diff_gt_dea':
                    conditions_buy.append(f"({line_col} > {sig_col})")
                    conditions_sell.append(f"({line_col} < {sig_col})")
                elif operator == 'diff_lt_dea':
                    conditions_buy.append(f"({line_col} < {sig_col})")
                    conditions_sell.append(f"({line_col} > {sig_col})")
                elif operator == 'cross_up':
                    conditions_buy.append(f"({line_col} > {sig_col}) & ({line_col}.shift(1) <= {sig_col}.shift(1))")
                    conditions_sell.append(f"({line_col} < {sig_col}) & ({line_col}.shift(1) >= {sig_col}.shift(1))")
                elif operator == 'cross_down':
                    conditions_buy.append(f"({line_col} < {sig_col}) & ({line_col}.shift(1) >= {sig_col}.shift(1))")
                    conditions_sell.append(f"({line_col} > {sig_col}) & ({line_col}.shift(1) <= {sig_col}.shift(1))")

            elif ind == 'bollinger':
                period = params.get('period', 20)
                std_dev = params.get('std_dev', 2.0)
                operator = rule.get('operator', 'price_above_upper')
                upper = f"df['bb_{period}_{std_dev}_upper']"
                lower = f"df['bb_{period}_{std_dev}_lower']"
                mid = f"df['bb_{period}_{std_dev}_mid']"
                
                if operator == 'price_above_upper':
                    conditions_buy.append(f"(df['close'] > {upper})")
                    conditions_sell.append(f"(df['close'] < {lower})")
                elif operator == 'price_below_lower':
                    conditions_buy.append(f"(df['close'] < {lower})")
                    conditions_sell.append(f"(df['close'] > {upper})")
                elif operator == 'price_above_mid':
                    conditions_buy.append(f"(df['close'] > {mid})")
                    conditions_sell.append(f"(df['close'] < {mid})")
                elif operator == 'price_below_mid':
                    conditions_buy.append(f"(df['close'] < {mid})")
                    conditions_sell.append(f"(df['close'] > {mid})")
                elif operator == 'cross_up_lower':
                    conditions_buy.append(f"(df['close'] > {lower}) & (df['close'].shift(1) <= {lower}.shift(1))")
                    conditions_sell.append(f"(df['close'] < {upper}) & (df['close'].shift(1) >= {upper}.shift(1))")
                elif operator == 'cross_down_upper':
                    conditions_buy.append(f"(df['close'] < {upper}) & (df['close'].shift(1) >= {upper}.shift(1))")
                    conditions_sell.append(f"(df['close'] > {lower}) & (df['close'].shift(1) <= {lower}.shift(1))")

            elif ind == 'kdj':
                period = params.get('period', 9)
                signal_period = params.get('signal_period', 3)
                operator = rule.get('operator', 'k_gt_d')
                k_col = f"df['kdj_{period}_{signal_period}_k']"
                d_col = f"df['kdj_{period}_{signal_period}_d']"
                
                if operator == 'k_gt_d':
                    conditions_buy.append(f"({k_col} > {d_col})")
                    conditions_sell.append(f"({k_col} < {d_col})")
                elif operator == 'k_lt_d':
                    conditions_buy.append(f"({k_col} < {d_col})")
                    conditions_sell.append(f"({k_col} > {d_col})")
                elif operator == 'gold_cross':
                    conditions_buy.append(f"({k_col} > {d_col}) & ({k_col}.shift(1) <= {d_col}.shift(1))")
                    conditions_sell.append(f"({k_col} < {d_col}) & ({k_col}.shift(1) >= {d_col}.shift(1))")
                elif operator == 'death_cross':
                    conditions_buy.append(f"({k_col} < {d_col}) & ({k_col}.shift(1) >= {d_col}.shift(1))")
                    conditions_sell.append(f"({k_col} > {d_col}) & ({k_col}.shift(1) <= {d_col}.shift(1))")

            elif ind == 'ma':
                period = params.get('period', 20)
                ma_type = params.get('ma_type', 'sma')
                operator = rule.get('operator', 'price_above')
                col = f"df['ma_{ma_type}_{period}']"
                
                if operator == 'price_above':
                    conditions_buy.append(f"(df['close'] > {col})")
                    conditions_sell.append(f"(df['close'] < {col})")
                elif operator == 'price_below':
                    conditions_buy.append(f"(df['close'] < {col})")
                    conditions_sell.append(f"(df['close'] > {col})")
                elif operator == 'cross_up':
                    conditions_buy.append(f"(df['close'] > {col}) & (df['close'].shift(1) <= {col}.shift(1))")
                    conditions_sell.append(f"(df['close'] < {col}) & (df['close'].shift(1) >= {col}.shift(1))")
                elif operator == 'cross_down':
                    conditions_buy.append(f"(df['close'] < {col}) & (df['close'].shift(1) >= {col}.shift(1))")
                    conditions_sell.append(f"(df['close'] > {col}) & (df['close'].shift(1) <= {col}.shift(1))")

        if conditions_buy:
            code += f"\ndf['raw_buy'] = {' & '.join(conditions_buy)}\n"
        if conditions_sell:
            code += f"\ndf['raw_sell'] = {' & '.join(conditions_sell)}\n"
            
        return code

    def _get_core_loop(self, pos_config, pyr_rules, risk_mgmt):
        # This mirrors the logic in code2.py loop
        return """
# ===========================
# 4. Core Loop (Backtest)
# ===========================
open_long_signals = [False] * len(df)
add_long_signals = [False] * len(df)
close_long_signals = [False] * len(df)

open_long_text = [None] * len(df)
add_long_text = [None] * len(df)

open_long_price = [0.0] * len(df)
add_long_price = [0.0] * len(df)
close_long_price = [0.0] * len(df)
close_long_text = [None] * len(df)

open_short_signals = [False] * len(df)
add_short_signals = [False] * len(df)
close_short_signals = [False] * len(df)

open_short_price = [0.0] * len(df)
add_short_price = [0.0] * len(df)
close_short_price = [0.0] * len(df)
close_short_text = [None] * len(df)

position = 0 # 0, 1 (Long), -1 (Short)
position_count = 0
avg_entry_price = 0.0
last_add_price = 0.0
highest_price = 0.0 # For Long: Highest High; For Short: Lowest Low

close_arr = df['close'].values
high_arr = df['high'].values
low_arr = df['low'].values
raw_buy_arr = df['raw_buy'].values
raw_sell_arr = df['raw_sell'].values

for i in range(len(df)):
    current_close = close_arr[i]
    current_high = high_arr[i]
    current_low = low_arr[i]
    
    if position == 1:
        # Long Position
        if current_high > highest_price:
            highest_price = current_high
            
        profit_pct = (highest_price - avg_entry_price) / avg_entry_price
        current_profit_pct = (current_close - avg_entry_price) / avg_entry_price
        
        # 1. Trailing Stop
        if take_profit_activation > 0 and profit_pct >= take_profit_activation:
            drawdown = (highest_price - current_close) / avg_entry_price
            if drawdown >= trailing_callback:
                close_long_signals[i] = True
                close_long_price[i] = current_close
                close_long_text[i] = "Trailing Stop"
                position = 0
                position_count = 0
                continue
                
        # 2. Stop Loss
        if stop_loss_pct > 0:
            loss_pct = (avg_entry_price - current_low) / avg_entry_price
            if loss_pct >= stop_loss_pct:
                close_long_signals[i] = True
                close_long_price[i] = avg_entry_price * (1 - stop_loss_pct)
                close_long_text[i] = "Stop Loss"
                position = 0
                position_count = 0
                continue
                
        # 3. Signal Exit (if enabled)
        # Note: Code2 uses raw_sell_arr for exit
        if raw_sell_arr[i]:
             close_long_signals[i] = True
             close_long_price[i] = current_close
             close_long_text[i] = "Signal Exit"
             position = 0
             position_count = 0
             
             # Reverse to Short if trade_direction allows (simplified here)
             # For now we just close.
             continue
             
        # 4. Pyramiding (Add Long)
        if max_pyramiding > 0 and position_count < max_pyramiding + 1 and current_profit_pct > 0:
             # Condition: Price rise by threshold
             if add_threshold_pct > 0:
                 target_price = last_add_price * (1 + add_threshold_pct)
                 if current_high >= target_price:
                     add_long_signals[i] = True
                     add_long_price[i] = target_price
                     add_long_text[i] = "Add Long"
                     position_count += 1
                     last_add_price = target_price
             
    elif position == -1:
        # Short Position
        # For Short, highest_price tracks the LOWEST price (best profit scenario)
        if highest_price == 0: highest_price = avg_entry_price
        if current_low < highest_price:
            highest_price = current_low
            
        # Profit: (Entry - Lowest) / Entry
        profit_pct = (avg_entry_price - highest_price) / avg_entry_price
        current_profit_pct = (avg_entry_price - current_close) / avg_entry_price
        
        # 1. Trailing Stop
        if take_profit_activation > 0 and profit_pct >= take_profit_activation:
            # Drawdown: (Current - Lowest) / Entry
            drawdown = (current_close - highest_price) / avg_entry_price
            if drawdown >= trailing_callback:
                close_short_signals[i] = True
                close_short_price[i] = current_close
                close_short_text[i] = "Trailing Stop"
                position = 0
                position_count = 0
                continue

        # 2. Stop Loss
        if stop_loss_pct > 0:
            # Loss: Price went up. (High - Entry) / Entry
            loss_pct = (current_high - avg_entry_price) / avg_entry_price
            if loss_pct >= stop_loss_pct:
                close_short_signals[i] = True
                close_short_price[i] = avg_entry_price * (1 + stop_loss_pct)
                close_short_text[i] = "Stop Loss"
                position = 0
                position_count = 0
                continue

        # 3. Signal Exit
        if raw_buy_arr[i]:
             close_short_signals[i] = True
             close_short_price[i] = current_close
             close_short_text[i] = "Signal Exit"
             position = 0
             position_count = 0
             continue

        # 4. Pyramiding (Add Short)
        if max_pyramiding > 0 and position_count < max_pyramiding + 1 and current_profit_pct > 0:
             # Condition: Price drop by threshold
             if add_threshold_pct > 0:
                 target_price = last_add_price * (1 - add_threshold_pct)
                 if current_low <= target_price:
                     add_short_signals[i] = True
                     add_short_price[i] = target_price
                     add_short_text[i] = "Add Short"
                     position_count += 1
                     last_add_price = target_price

    else:
        # No Position
        if raw_buy_arr[i]:
            open_long_signals[i] = True
            open_long_price[i] = current_close
            open_long_text[i] = "Open Long"
            position = 1
            position_count = 1
            avg_entry_price = current_close
            last_add_price = current_close
            highest_price = current_close
            
        elif raw_sell_arr[i]:
            open_short_signals[i] = True
            open_short_price[i] = current_close
            open_short_text[i] = "Open Short"
            position = -1
            position_count = 1
            avg_entry_price = current_close
            last_add_price = current_close
            highest_price = current_close # Init with Entry

# Append columns
df['open_long'] = open_long_signals
df['add_long'] = add_long_signals
df['close_long'] = close_long_signals
df['open_long_price'] = [p if s else None for p, s in zip(open_long_price, open_long_signals)]
df['add_long_price'] = [p if s else None for p, s in zip(add_long_price, add_long_signals)]
df['close_long_price'] = [p if s else None for p, s in zip(close_long_price, close_long_signals)]
df['open_long_text'] = open_long_text
df['add_long_text'] = add_long_text
df['close_long_text'] = close_long_text
"""

    def _get_output_section(self, name, rules):
        # Generate plot configs based on indicators
        plots = []
        for rule in rules:
            ind = rule.get('indicator')
            params = rule.get('params', {})
            if ind == 'supertrend':
                plots.append({
                    "name": "SuperTrend Up", "type": "line", "data": "df['st_lower'].tolist()", "color": "#00FF00", "overlay": True
                })
                plots.append({
                    "name": "SuperTrend Down", "type": "line", "data": "df['st_upper'].tolist()", "color": "#FF0000", "overlay": True
                })
            elif ind == 'ema':
                p = params.get('period', 20)
                plots.append({
                    "name": f"EMA {p}", "type": "line", "data": f"df['ema_{p}'].tolist()", "color": "#FFA500", "overlay": True
                })
            elif ind == 'ma':
                p = params.get('period', 20)
                t = params.get('ma_type', 'sma')
                plots.append({
                    "name": f"{t.upper()} {p}", "type": "line", "data": f"df['ma_{t}_{p}'].tolist()", "color": "#FFA500", "overlay": True
                })
            elif ind == 'bollinger':
                p = params.get('period', 20)
                d = params.get('std_dev', 2.0)
                plots.append({
                    "name": "BB Upper", "type": "line", "data": f"df['bb_{p}_{d}_upper'].tolist()", "color": "#0088FE", "overlay": True
                })
                plots.append({
                    "name": "BB Lower", "type": "line", "data": f"df['bb_{p}_{d}_lower'].tolist()", "color": "#0088FE", "overlay": True
                })
            # MACD, RSI, KDJ are typically separate panes, not overlay. The `overlay` param controls this.
            elif ind == 'macd':
                f = params.get('fast_period', 12)
                s = params.get('slow_period', 26)
                si = params.get('signal_period', 9)
                plots.append({
                    "name": "MACD", "type": "line", "data": f"df['macd_{f}_{s}_{si}_line'].tolist()", "color": "#0088FE", "overlay": False
                })
                plots.append({
                    "name": "Signal", "type": "line", "data": f"df['macd_{f}_{s}_{si}_signal'].tolist()", "color": "#FF8042", "overlay": False
                })
            elif ind == 'rsi':
                p = params.get('period', 14)
                plots.append({
                    "name": f"RSI {p}", "type": "line", "data": f"df['rsi_{p}'].tolist()", "color": "#8884d8", "overlay": False
                })
            elif ind == 'kdj':
                p = params.get('period', 9)
                si = params.get('signal_period', 3)
                plots.append({
                    "name": "K", "type": "line", "data": f"df['kdj_{p}_{si}_k'].tolist()", "color": "#8884d8", "overlay": False
                })
                plots.append({
                    "name": "D", "type": "line", "data": f"df['kdj_{p}_{si}_d'].tolist()", "color": "#82ca9d", "overlay": False
                })
                plots.append({
                    "name": "J", "type": "line", "data": f"df['kdj_{p}_{si}_j'].tolist()", "color": "#ffc658", "overlay": False
                })
        
        # Convert plots to string representation valid in Python
        plots_py = "[\n"
        for p in plots:
            plots_py += f"    {{'name': '{p['name']}', 'type': '{p['type']}', 'data': {p['data']}, 'color': '{p['color']}', 'overlay': {p['overlay']}}},\n"
        plots_py += "]"

        return f"""
# ===========================
# 5. Output
# ===========================
output = {{
    "name": "{name}",
    "plots": {plots_py},
    "signals": [
        {{
            "name": "Open Long",
            "type": "buy",
            "data": df['open_long_price'].tolist(),
            "color": "#00FF00",
            "text": "Open Long"
        }},
        {{
            "name": "Add Long",
            "type": "buy",
            "data": df['add_long_price'].tolist(),
            "color": "#00DD00",
            "text": "Add Long"
        }},
        {{
            "name": "Close Long",
            "type": "sell",
            "data": df['close_long_price'].tolist(),
            "color": "#FF6600",
            "text": "Close Long"
        }},
        {{
            "name": "Open Short",
            "type": "sell",
            "data": df['open_short_price'].tolist(),
            "color": "#FF0000",
            "text": "Open Short"
        }},
        {{
            "name": "Add Short",
            "type": "sell",
            "data": df['add_short_price'].tolist(),
            "color": "#DD0000",
            "text": "Add Short"
        }},
        {{
            "name": "Close Short",
            "type": "buy",
            "data": df['close_short_price'].tolist(),
            "color": "#00CCFF",
            "text": "Close Short"
        }}
    ]
}}
"""

