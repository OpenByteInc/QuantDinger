# ============================================================
# QuantDinger Smart Money Concept ML
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Smart Money Concept ML"
my_indicator_description = "Maps confirmed smart-money structure, liquidity and zones, calibrates retest and first-touch odds, and tracks one risk-defined signal at a time."

# @param swing_length int 10 Swing length range=3:50:1
# @param internal_length int 3 Internal length range=2:10:1
# @param show_internal bool true Show internal structure
# @param show_swing_labels bool false Show HH, HL, LH, and LL labels
# @param history_mode str Historical Structure view values=Historical,Recent
# @param swing_history int 120 Swing breaks retained range=10:180:1
# @param internal_history int 160 Internal breaks retained range=10:180:1
# @param equal_history int 40 Equal pairs retained range=5:60:1
# @param show_order_blocks bool true Show order blocks
# @param order_blocks_per_side int 2 Order blocks per side range=1:10:1
# @param show_fvg bool false Show fair value gaps
# @param minimum_fvg_atr float 0.3 Minimum FVG range in ATR range=0:3:0.05
# @param show_equal_levels bool true Show equal highs and lows
# @param equal_tolerance_atr float 0.15 Equal-level tolerance in ATR range=0.02:1:0.01
# @param show_strong_weak bool true Show strong and weak range extremes
# @param show_premium_discount bool false Show premium and discount zones
# @param show_liquidity_odds bool true Show calibrated liquidity odds
# @param retest_window int 20 Retest window range=3:200:1
# @param show_signals bool true Show buy and sell signals
# @param entry_mode str Break_Close Entry mode values=Break_Close,Level_Retest,Adaptive
# @param adaptive_threshold float 0.8 Adaptive retest threshold range=0.5:0.99:0.01
# @param break_filter str All Signal breaks values=All,CHoCH,BOS
# @param stop_buffer_atr float 0.2 Stop buffer range=0:3:0.05
# @param max_risk_atr float 6.0 Maximum risk range=0.5:30:0.5
# @param target_1_r float 1.0 Target 1 R range=0.25:10:0.25
# @param target_2_r float 2.0 Target 2 R range=0.5:20:0.25
# @param break_even_after_tp1 bool true Move stop to entry after TP1
# @param trade_timeout int 150 Trade timeout range=10:2000:1
# @param bullish_color str #246BFF Bullish color
# @param bearish_color str #FF8A18 Bearish color
# @param show_table bool true Show information table
# @param table_position str top-right Table position values=top-right,bottom-right,top-left,bottom-left
# @param zen_mode bool false Hide labels and table


def _int(name, default, low, high): return min(high, max(low, int(params.get(name, default))))
def _float(name, default, low, high): return min(high, max(low, float(params.get(name, default))))


swing_length = _int("swing_length", 10, 3, 50); internal_length = _int("internal_length", 3, 2, 10); show_internal = bool(params.get("show_internal", True)); show_swing_labels = bool(params.get("show_swing_labels", False)); historical = str(params.get("history_mode", "Historical")) == "Historical"; swing_history_limit = _int("swing_history", 120, 10, 180); internal_history_limit = _int("internal_history", 160, 10, 180); equal_history_limit = _int("equal_history", 40, 5, 60)
show_order_blocks = bool(params.get("show_order_blocks", True)); ob_keep = _int("order_blocks_per_side", 2, 1, 10); show_fvg = bool(params.get("show_fvg", False)); minimum_fvg = _float("minimum_fvg_atr", 0.3, 0, 3); show_equal = bool(params.get("show_equal_levels", True)); equal_tolerance = _float("equal_tolerance_atr", 0.15, 0.02, 1); show_strong_weak = bool(params.get("show_strong_weak", True)); show_pd = bool(params.get("show_premium_discount", False)); show_liquidity = bool(params.get("show_liquidity_odds", True)); retest_window = _int("retest_window", 20, 3, 200)
show_signals = bool(params.get("show_signals", True)); entry_mode = str(params.get("entry_mode", "Break_Close")); adaptive_threshold = _float("adaptive_threshold", 0.8, 0.5, 0.99); break_filter = str(params.get("break_filter", "All")); stop_buffer = _float("stop_buffer_atr", 0.2, 0, 3); max_risk = _float("max_risk_atr", 6, 0.5, 30); tp1_r = _float("target_1_r", 1, 0.25, 10); tp2_r = _float("target_2_r", 2, 0.5, 20); tp1_r, tp2_r = min(tp1_r, tp2_r), max(tp1_r, tp2_r); move_be = bool(params.get("break_even_after_tp1", True)); trade_timeout = _int("trade_timeout", 150, 10, 2000)
bullish_color = str(params.get("bullish_color", "#246BFF")); bearish_color = str(params.get("bearish_color", "#FF8A18")); show_table = bool(params.get("show_table", True)); table_position = str(params.get("table_position", "top-right")); zen_mode = bool(params.get("zen_mode", False))

CAL_CAP = 500; CAL_LAMBDA = 50.0; CAL_MIN = 30; FOLLOW_WINDOW = 150; DRAW_WINDOW = 1000; Z_LIMIT = 2.39


def _finite(values): return [float(value) if np.isfinite(value) else None for value in values]


def _rma(values, length):
    result = np.full(len(values), np.nan); state = np.nan; seed = []
    for index, value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed) == length: state = float(np.mean(seed)); result[index] = state
        else: state = (state * (length - 1.0) + float(value)) / length; result[index] = state
    return result


def _rolling_std(values, length):
    result = np.full(len(values), np.nan)
    for index in range(length - 1, len(values)):
        window = values[index-length+1:index+1]
        if np.all(np.isfinite(window)): result[index] = float(np.std(window, ddof=0))
    return result


def _pivots(values, length, high_pivot):
    result = np.full(len(values), np.nan)
    for confirmed in range(length * 2, len(values)):
        center = confirmed - length; window = values[center-length:center+length+1]; value = values[center]
        if value == (np.max(window) if high_pivot else np.min(window)): result[confirmed] = value
    return result


def _phi(value):
    z = abs(float(value)); t = 1.0 / (1.0 + 0.2316419 * z); density = 0.3989422804014327 * np.exp(-0.5 * z * z); probability = 1.0 - density * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    return probability if value >= 0 else 1.0 - probability


def _clamp_probability(value): return max(0.005, min(0.995, float(value)))
def _logit(value):
    probability = _clamp_probability(value); return float(np.log(probability / (1.0 - probability)))
def _touch_probability(distance, deviation, window):
    return np.nan if not np.isfinite(deviation) or deviation <= 0 or not np.isfinite(distance) else _clamp_probability(2.0 * (1.0 - _phi(distance / (deviation * np.sqrt(window)))))


def _new_calibration(fixed_intercept=False, cut1=0.70, cut2=0.90):
    return {"x":[],"y":[],"fixed":fixed_intercept,"a":0.0,"b":1.0,"n":0,"hit":0,"sumProbability":0.0,"brierModel":0.0,"brierFormula":0.0,"brierBase":0.0,"cut1":cut1,"cut2":cut2,"bands":[[0.0,0.0,0.0],[0.0,0.0,0.0],[0.0,0.0,0.0]]}


def _cal_probability(calibration, formula):
    return np.nan if not np.isfinite(formula) else float(1.0 / (1.0 + np.exp(-(calibration["a"] + calibration["b"] * _logit(formula)))))


def _cal_fit(calibration):
    if not calibration["x"]: return
    a,b=calibration["a"],calibration["b"]
    for _ in range(4):
        ga=CAL_LAMBDA*a; gb=CAL_LAMBDA*(b-1.0); haa=CAL_LAMBDA; hab=0.0; hbb=CAL_LAMBDA
        for x,outcome in zip(calibration["x"],calibration["y"]):
            probability=1.0/(1.0+np.exp(-(a+b*x))); error=probability-outcome; weight=probability*(1.0-probability); ga+=error; gb+=error*x; haa+=weight; hab+=weight*x; hbb+=weight*x*x
        determinant=haa*hbb-hab*hab
        if calibration["fixed"]: b-=gb/hbb
        elif determinant>0: a-=(hbb*ga-hab*gb)/determinant; b-=(haa*gb-hab*ga)/determinant
    calibration["a"],calibration["b"]=float(a),float(b)


def _cal_score(calibration, shown, formula, outcome):
    base=calibration["hit"]/calibration["n"] if calibration["n"] else 0.5; calibration["brierBase"]+=(base-outcome)**2; calibration["brierModel"]+=(shown-outcome)**2; calibration["brierFormula"]+=(formula-outcome)**2; calibration["sumProbability"]+=shown; calibration["n"]+=1; calibration["hit"]+=outcome; band=0 if shown<calibration["cut1"] else 1 if shown<calibration["cut2"] else 2; calibration["bands"][band][0]+=1; calibration["bands"][band][1]+=outcome; calibration["bands"][band][2]+=shown; calibration["x"].append(_logit(formula)); calibration["y"].append(int(outcome)); calibration["x"]=calibration["x"][-CAL_CAP:]; calibration["y"]=calibration["y"][-CAL_CAP:]; _cal_fit(calibration)


def _band_ok(sample, hits, shown, z):
    if sample<20: return True
    probability=shown/sample; return abs(probability-hits/sample)<=z*np.sqrt(max(probability*(1-probability),0.0025)/sample)


def _calibrated(calibration):
    return calibration["n"]>=CAL_MIN and _band_ok(calibration["n"],calibration["hit"],calibration["sumProbability"],1.96) and all(_band_ok(band[0],band[1],band[2],2.39) for band in calibration["bands"])


def _new_structure(length):
    return {"len":length,"hi":np.nan,"hiIndex":-1,"hiOpen":False,"lo":np.nan,"loIndex":-1,"loOpen":False,"prevHi":np.nan,"prevHiIndex":-1,"prevLo":np.nan,"prevLoIndex":-1,"legLo":np.nan,"legLoIndex":-1,"legHi":np.nan,"legHiIndex":-1,"trend":0,"newHi":False,"newLo":False,"event":0,"choch":False,"level":np.nan,"levelIndex":-1,"protected":np.nan,"protectedIndex":-1,"sweep":False}


def _step_structure(state, index, pivot_high, pivot_low):
    state["event"]=0; state["newHi"]=False; state["newLo"]=False
    if np.isfinite(state["legLo"]) and low[index]<state["legLo"]: state["legLo"]=float(low[index]); state["legLoIndex"]=index
    if np.isfinite(state["legHi"]) and high[index]>state["legHi"]: state["legHi"]=float(high[index]); state["legHiIndex"]=index
    if state["hiOpen"] and close[index]>state["hi"]:
        state["event"]=1; state["choch"]=state["trend"]==-1; state["trend"]=1; state["level"]=state["hi"]; state["levelIndex"]=state["hiIndex"]; state["protected"]=state["legLo"] if np.isfinite(state["legLo"]) else float(low[index]); state["protectedIndex"]=state["legLoIndex"] if np.isfinite(state["legLo"]) else index; reference=state["lo"] if state["loIndex"]<state["protectedIndex"] else state["prevLo"] if state["prevLoIndex"]<state["protectedIndex"] else np.nan; state["sweep"]=np.isfinite(reference) and state["protected"]<reference; state["hiOpen"]=False
    elif state["loOpen"] and close[index]<state["lo"]:
        state["event"]=-1; state["choch"]=state["trend"]==1; state["trend"]=-1; state["level"]=state["lo"]; state["levelIndex"]=state["loIndex"]; state["protected"]=state["legHi"] if np.isfinite(state["legHi"]) else float(high[index]); state["protectedIndex"]=state["legHiIndex"] if np.isfinite(state["legHi"]) else index; reference=state["hi"] if state["hiIndex"]<state["protectedIndex"] else state["prevHi"] if state["prevHiIndex"]<state["protectedIndex"] else np.nan; state["sweep"]=np.isfinite(reference) and state["protected"]>reference; state["loOpen"]=False
    length=state["len"]
    if np.isfinite(pivot_high):
        state["prevHi"],state["prevHiIndex"]=state["hi"],state["hiIndex"]; state["hi"],state["hiIndex"],state["hiOpen"],state["newHi"]=float(pivot_high),index-length,True,True; segment=low[max(0,index-length+1):index+1]; offset=int(np.argmin(segment)); state["legLo"]=float(segment[offset]); state["legLoIndex"]=max(0,index-length+1)+offset
    if np.isfinite(pivot_low):
        state["prevLo"],state["prevLoIndex"]=state["lo"],state["loIndex"]; state["lo"],state["loIndex"],state["loOpen"],state["newLo"]=float(pivot_low),index-length,True,True; segment=high[max(0,index-length+1):index+1]; offset=int(np.argmax(segment)); state["legHi"]=float(segment[offset]); state["legHiIndex"]=max(0,index-length+1)+offset


def _append_capped(items, value, limit): items.append(value); del items[:-limit]
def _add_liquidity(items, side, level, kind, first_index, label_index):
    position=len(items)
    for index,item in enumerate(items):
        if (side==1 and level<item["level"]) or (side==-1 and level>item["level"]): position=index; break
    items.insert(position,{"level":float(level),"kind":int(kind),"firstIndex":int(first_index),"labelIndex":int(label_index)}); del items[20:]
def _sweep(items, side, index):
    equal=False
    while items and ((side==1 and high[index]>items[0]["level"]) or (side==-1 and low[index]<items[0]["level"])): equal=equal or items[0]["kind"]==1; items.pop(0)
    return equal
def _add_zone(zones, zone, current_atr):
    hit=-1
    for index,item in enumerate(zones):
        fits=zone["kind"]==1 or not np.isfinite(current_atr) or max(item["top"],zone["top"])-min(item["bottom"],zone["bottom"])<=3.0*current_atr
        if hit<0 and item["kind"]==zone["kind"] and item["direction"]==zone["direction"] and zone["bottom"]<=item["top"] and zone["top"]>=item["bottom"] and fits: hit=index
    if hit>=0:
        item=zones.pop(hit); item["top"]=max(item["top"],zone["top"]); item["bottom"]=min(item["bottom"],zone["bottom"]); item["startIndex"]=min(item["startIndex"],zone["startIndex"]); zones.append(item)
    else: zones.append(zone)
def _cap_zones(zones, kind, direction, keep):
    seen=0
    for index in range(len(zones)-1,-1,-1):
        if zones[index]["kind"]==kind and zones[index]["direction"]==direction:
            seen+=1
            if seen>keep: zones.pop(index)


df=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in df.columns: df[column]=0.0
    df[column]=pd.to_numeric(df[column],errors="coerce")
df=df.dropna(subset=["time","open","high","low","close"]).reset_index(drop=True)
times=df["time"].to_numpy(float); time_ms=np.where(np.abs(times)<100000000000.0,times*1000.0,times); opened,high,low,close,volume=(df[key].to_numpy(float) for key in ("open","high","low","close","volume")); volume=np.nan_to_num(volume,nan=0.0); n=len(df)
previous_close=np.r_[np.nan,close[:-1]]; true_range=np.maximum(high-low,np.maximum(np.abs(high-previous_close),np.abs(low-previous_close))) if n else np.asarray([],float)
if n:true_range[0]=high[0]-low[0]
atr=_rma(true_range,14); change=np.diff(close,prepend=np.nan); deviation=_rolling_std(change,100); swing_high=_pivots(high,swing_length,True); swing_low=_pivots(low,swing_length,False); internal_high=_pivots(high,internal_length,True); internal_low=_pivots(low,internal_length,False)
fvg_bull=np.zeros(n,bool); fvg_bear=np.zeros(n,bool)
for index in range(2,n):
    if np.isfinite(atr[index]) and atr[index]>0: fvg_bull[index]=low[index]>high[index-2] and close[index-1]>high[index-2] and low[index]-high[index-2]>=minimum_fvg*atr[index]; fvg_bear[index]=high[index]<low[index-2] and close[index-1]<low[index-2] and low[index-2]-high[index]>=minimum_fvg*atr[index]

swing=_new_structure(swing_length); internal=_new_structure(internal_length); retest_cal=_new_calibration(False,0.70,0.90); liquidity_cal=_new_calibration(True,0.65,0.80); zones=[]; liquidity_up=[]; liquidity_down=[]; swing_marks=[]; swing_history=[]; internal_marks=[]; equal_history=[]; swing_points=[]; pending_breaks=[]; pending_draws=[]; edge=[0]*12; trades=[]; closed_stats={"count":0,"tp1":0,"fullStop":0,"sumR":0.0,"sumR2":0.0}; waiting=None; last_fvg_bull=last_fvg_bear=-1
event_bull_choch=np.zeros(n,bool); event_bear_choch=np.zeros(n,bool); event_bull_bos=np.zeros(n,bool); event_bear_bos=np.zeros(n,bool); event_buy=np.zeros(n,bool); event_sell=np.zeros(n,bool); event_exit=np.zeros(n,bool); event_sweep=np.zeros(n,bool); swing_trend=np.zeros(n,int); range_high=np.full(n,np.nan); range_low=np.full(n,np.nan); range_position=np.full(n,np.nan); retest_probability=np.full(n,np.nan); open_trade_state=np.zeros(n,int); entry_event=np.full(n,np.nan); exit_event=np.full(n,np.nan)

def _open_direction(): return trades[-1]["direction"] if trades and trades[-1]["open"] else 0
def _close_trade(trade,result,price,index):
    trade["open"]=False; trade["result"]=result; trade["endIndex"]=index; trade["exitPrice"]=float(price); risk=abs(trade["entry"]-trade["initialStop"]); remainder=trade["direction"]*(price-trade["entry"])/risk if risk>0 else 0.0; total_r=0.5*tp1_r+0.5*remainder if trade["tp1Hit"] else remainder; closed_stats["count"]+=1; closed_stats["tp1"]+=int(trade["tp1Hit"]); closed_stats["fullStop"]+=int(result=="SL"); closed_stats["sumR"]+=total_r; closed_stats["sumR2"]+=total_r*total_r
    while len(trades)>1: trades.pop(0)

for index in range(n):
    _step_structure(swing,index,swing_high[index],swing_low[index]); _step_structure(internal,index,internal_high[index],internal_low[index]); swing_trend[index]=swing["trend"]
    current_range_high=max([value for value in (swing["legHi"],swing["hi"]) if np.isfinite(value)],default=np.nan); current_range_low=min([value for value in (swing["legLo"],swing["lo"]) if np.isfinite(value)],default=np.nan); range_high[index]=current_range_high; range_low[index]=current_range_low
    if np.all(np.isfinite([current_range_high,current_range_low])) and current_range_high>current_range_low: range_position[index]=(close[index]-current_range_low)/(current_range_high-current_range_low)
    # Signal exits are evaluated before all new entries.
    if trades and trades[-1]["open"] and index>trades[-1]["entryIndex"]:
        trade=trades[-1]; long=trade["direction"]==1; hit_stop=low[index]<=trade["stop"] if long else high[index]>=trade["stop"]; hit_target2=high[index]>=trade["target2"] if long else low[index]<=trade["target2"]; hit_target1=(not trade["tp1Hit"]) and (high[index]>=trade["target1"] if long else low[index]<=trade["target1"])
        if hit_stop:
            price=min(opened[index],trade["stop"]) if long else max(opened[index],trade["stop"]); result="SL" if not trade["tp1Hit"] else "TP1, then stop at entry" if trade["breakEvenIndex"]>=0 else "TP1, then SL"; _close_trade(trade,result,price,index); event_exit[index]=True; exit_event[index]=price
        elif hit_target2:
            price=max(opened[index],trade["target2"]) if long else min(opened[index],trade["target2"]); trade["tp1Hit"]=True; _close_trade(trade,"TP2",price,index); event_exit[index]=True; exit_event[index]=price
        else:
            if hit_target1:
                trade["tp1Hit"]=True
                if move_be: trade["stop"]=trade["entry"]; trade["breakEvenIndex"]=index
            if index-trade["entryIndex"]>=trade_timeout: _close_trade(trade,"timeout",close[index],index); event_exit[index]=True; exit_event[index]=close[index]
    # Resolve pending swing-break outcomes.
    for item in pending_breaks[:][::-1]:
        if index<=item["bar0"]: continue
        long=item["direction"]==1
        if not item["retestDone"]:
            touched=low[index]<=item["level"] if long else high[index]>=item["level"]
            if touched or index-item["bar0"]>=retest_window:
                item["retestDone"]=True; item["mark"]["retest"]=1 if touched else -1
                if np.isfinite(item["formula"]): _cal_score(retest_cal,item["shown"],item["formula"],1 if touched else 0)
        if not item["followDone"]:
            failed=close[index]<item["protected"] if long else close[index]>item["protected"]; won=close[index]>=item["entry"]+item["risk"] if long else close[index]<=item["entry"]-item["risk"]
            if failed or won or index-item["bar0"]>=FOLLOW_WINDOW:
                item["followDone"]=True
                if failed or won:
                    outcome=0 if failed else 1
                    for offset in ((2 if item["choch"] else 0),(4 if item["fvg"] else 6),(8 if item["sweep"] else 10)): edge[offset]+=1; edge[offset+1]+=outcome
        if item["retestDone"] and item["followDone"]: pending_breaks.remove(item)
    for item in pending_draws[:][::-1]:
        if index<=item["bar0"]: continue
        hit_up=high[index]>item["up"]; hit_down=low[index]<item["down"]
        if hit_up or hit_down or index-item["bar0"]>=DRAW_WINDOW:
            if hit_up!=hit_down: _cal_score(liquidity_cal,item["shown"],item["formula"],1 if hit_up==item["nearUp"] else 0)
            pending_draws.remove(item)
    # Mitigate zones and register completed fair-value gaps.
    for zone in zones[:][::-1]:
        gone=(close[index]<zone["bottom"] if zone["direction"]==1 else close[index]>zone["top"]) if zone["kind"]==0 else (low[index]<=zone["bottom"] if zone["direction"]==1 else high[index]>=zone["top"])
        if gone: zones.remove(zone)
    if fvg_bull[index]: last_fvg_bull=index
    if fvg_bear[index]: last_fvg_bear=index
    if show_fvg and (fvg_bull[index] or fvg_bear[index]):
        direction=1 if fvg_bull[index] else -1; zone={"direction":direction,"top":float(low[index] if direction==1 else low[index-2]),"bottom":float(high[index-2] if direction==1 else high[index]),"startIndex":index-2,"kind":1}; _add_zone(zones,zone,atr[index]); _cap_zones(zones,1,direction,4)
    event_sweep[index]=_sweep(liquidity_up,1,index) or _sweep(liquidity_down,-1,index)
    if np.isfinite(atr[index]) and atr[index]>0:
        if swing["newHi"]: _add_liquidity(liquidity_up,1,swing["hi"],0,swing["hiIndex"],swing["hiIndex"])
        if swing["newLo"]: _add_liquidity(liquidity_down,-1,swing["lo"],0,swing["loIndex"],swing["loIndex"])
        if internal["newHi"] and np.isfinite(internal["prevHi"]) and abs(internal["hi"]-internal["prevHi"])<=equal_tolerance*atr[index]:
            level=max(internal["hi"],internal["prevHi"]); span=index-internal["prevHiIndex"]-1; clean=index-internal["prevHiIndex"]<=300 and all(high[index-k]<=level for k in range(max(0,min(span,299))+1))
            if clean: _add_liquidity(liquidity_up,1,level,1,internal["prevHiIndex"],internal["hiIndex"]); _append_capped(equal_history,{"firstIndex":internal["prevHiIndex"],"secondIndex":internal["hiIndex"],"level":float(level),"side":1,"confirmedIndex":index},equal_history_limit)
        if internal["newLo"] and np.isfinite(internal["prevLo"]) and abs(internal["lo"]-internal["prevLo"])<=equal_tolerance*atr[index]:
            level=min(internal["lo"],internal["prevLo"]); span=index-internal["prevLoIndex"]-1; clean=index-internal["prevLoIndex"]<=300 and all(low[index-k]>=level for k in range(max(0,min(span,299))+1))
            if clean: _add_liquidity(liquidity_down,-1,level,1,internal["prevLoIndex"],internal["loIndex"]); _append_capped(equal_history,{"firstIndex":internal["prevLoIndex"],"secondIndex":internal["loIndex"],"level":float(level),"side":-1,"confirmedIndex":index},equal_history_limit)
    if show_swing_labels:
        if swing["newHi"]: _append_capped(swing_points,{"index":swing["hiIndex"],"price":swing["hi"],"text":"H" if not np.isfinite(swing["prevHi"]) else "HH" if swing["hi"]>swing["prevHi"] else "LH","direction":1},40 if historical else 30)
        if swing["newLo"]: _append_capped(swing_points,{"index":swing["loIndex"],"price":swing["lo"],"text":"L" if not np.isfinite(swing["prevLo"]) else "LL" if swing["lo"]<swing["prevLo"] else "HL","direction":-1},40 if historical else 30)
    if show_internal and internal["event"]!=0 and not (swing["event"]==internal["event"] and swing["level"]==internal["level"]): _append_capped(internal_marks,{"startIndex":internal["levelIndex"],"endIndex":index,"level":internal["level"],"direction":internal["event"],"choch":bool(internal["choch"])},internal_history_limit if historical else 20)
    signal_direction=0; signal_entry=signal_stop=np.nan; signal_how=""
    if np.isfinite(atr[index]) and atr[index]>0 and swing["event"]!=0:
        direction=swing["event"]; long=direction==1; risk=abs(close[index]-swing["protected"]); formula=_touch_probability(abs(close[index]-swing["level"]),deviation[index],retest_window); shown=_cal_probability(retest_cal,formula); retest_probability[index]=shown; fvg_index=last_fvg_bull if long else last_fvg_bear; has_fvg=fvg_index>=swing["protectedIndex"]
        event_bull_choch[index]=long and swing["choch"]; event_bear_choch[index]=(not long) and swing["choch"]; event_bull_bos[index]=long and not swing["choch"]; event_bear_bos[index]=(not long) and not swing["choch"]
        mark={"startIndex":swing["levelIndex"],"endIndex":index,"level":float(swing["level"]),"direction":direction,"choch":bool(swing["choch"]),"probability":float(shown) if np.isfinite(shown) else None,"retest":0}; _append_capped(swing_marks,mark,6); _append_capped(swing_history,mark,swing_history_limit)
        if risk>0: pending_breaks.append({"direction":direction,"level":float(swing["level"]),"entry":float(close[index]),"protected":float(swing["protected"]),"risk":float(risk),"bar0":index,"choch":bool(swing["choch"]),"fvg":bool(has_fvg),"sweep":bool(swing["sweep"]),"formula":float(formula) if np.isfinite(formula) else np.nan,"shown":float(shown) if np.isfinite(shown) else np.nan,"retestDone":False,"followDone":False,"mark":mark})
        up=liquidity_up[0]["level"] if liquidity_up else np.nan; down=liquidity_down[0]["level"] if liquidity_down else np.nan
        if np.isfinite(up) and np.isfinite(down) and up>close[index]>down and np.isfinite(deviation[index]) and deviation[index]>0:
            distance_up=up-close[index]; distance_down=close[index]-down
            if distance_up+distance_down<=deviation[index]*np.sqrt(DRAW_WINDOW): near_up=distance_up<=distance_down; base=_clamp_probability((distance_down if near_up else distance_up)/(distance_up+distance_down)); pending_draws.append({"up":float(up),"down":float(down),"nearUp":near_up,"bar0":index,"formula":base,"shown":_cal_probability(liquidity_cal,base)})
        back=min(max(index-swing["protectedIndex"],0),400); selected=back
        for offset in range(back,back+4):
            candle=index-offset
            if candle>=0 and ((long and close[candle]<opened[candle]) or ((not long) and close[candle]>opened[candle])): selected=offset; break
        candle=max(0,index-selected)
        if show_order_blocks and high[candle]-low[candle]<=3.0*atr[index]: zone={"direction":direction,"top":float(high[candle]),"bottom":float(low[candle]),"startIndex":candle,"kind":0}; _add_zone(zones,zone,atr[index]); _cap_zones(zones,0,direction,ob_keep)
        if waiting is not None and direction!=waiting["direction"]: waiting=None
        qualifies=break_filter=="All" or (break_filter=="CHoCH" and swing["choch"]) or (break_filter=="BOS" and not swing["choch"]); wait=entry_mode=="Level_Retest" or (entry_mode=="Adaptive" and np.isfinite(shown) and shown>=adaptive_threshold); stop_level=swing["protected"]-stop_buffer*atr[index] if long else swing["protected"]+stop_buffer*atr[index]
        if qualifies:
            if wait: waiting={"direction":direction,"bar0":index,"level":float(swing["level"]),"protected":float(swing["protected"]),"probability":float(shown) if np.isfinite(shown) else None}
            else:
                waiting=None
                if _open_direction()!=direction and 0<abs(close[index]-stop_level)<=max_risk*atr[index]: signal_direction=direction; signal_entry=close[index]; signal_stop=stop_level; signal_how="break close"
    if np.isfinite(atr[index]) and atr[index]>0 and waiting is not None and index>waiting["bar0"] and signal_direction==0:
        long=waiting["direction"]==1
        if index-waiting["bar0"]>retest_window or (close[index]<waiting["protected"] if long else close[index]>waiting["protected"]): waiting=None
        elif (low[index]<=waiting["level"] and close[index]>waiting["level"] if long else high[index]>=waiting["level"] and close[index]<waiting["level"]) and _open_direction()!=waiting["direction"]:
            stop_level=waiting["protected"]-stop_buffer*atr[index] if long else waiting["protected"]+stop_buffer*atr[index]
            if 0<abs(close[index]-stop_level)<=max_risk*atr[index]: signal_direction=waiting["direction"]; signal_entry=close[index]; signal_stop=stop_level; signal_how="level retest"
            waiting=None
    if signal_direction!=0:
        current=_open_direction()
        if current!=0 and current!=signal_direction: trade=trades[-1]; _close_trade(trade,"reversed",close[index],index); event_exit[index]=True; exit_event[index]=close[index]
        risk=abs(signal_entry-signal_stop); trade={"direction":signal_direction,"entry":float(signal_entry),"initialStop":float(signal_stop),"stop":float(signal_stop),"target1":float(signal_entry+signal_direction*tp1_r*risk),"target2":float(signal_entry+signal_direction*tp2_r*risk),"entryIndex":index,"breakEvenIndex":-1,"endIndex":None,"exitPrice":None,"tp1Hit":False,"open":True,"result":"","how":signal_how}; trades.append(trade); event_buy[index]=signal_direction==1; event_sell[index]=signal_direction==-1; entry_event[index]=signal_entry
    open_trade_state[index]=_open_direction()

# Live liquidity probabilities.
live_up=next((item for item in liquidity_up if not n or high[-1]<=item["level"]),None); live_down=next((item for item in liquidity_down if not n or low[-1]>=item["level"]),None); live_probability_up=np.nan; live_formula_up=np.nan
if n and live_up is not None and live_down is not None and live_up["level"]>close[-1]>live_down["level"]:
    distance_up=live_up["level"]-close[-1]; distance_down=close[-1]-live_down["level"]; near_up=distance_up<=distance_down; base=_clamp_probability((distance_down if near_up else distance_up)/(distance_up+distance_down)); calibrated=_cal_probability(liquidity_cal,base); live_probability_up=calibrated if near_up else 1.0-calibrated; live_formula_up=base if near_up else 1.0-base

layers=[]; right_index=n+2
for zone in zones:
    if (zone["kind"]==0 and show_order_blocks) or (zone["kind"]==1 and show_fvg): layers.append({"type":"zone","role":"Order Block" if zone["kind"]==0 else "Fair Value Gap","startIndex":zone["startIndex"],"endIndex":right_index,"top":zone["top"],"bottom":zone["bottom"],"fillColor":bullish_color if zone["direction"]==1 else bearish_color,"borderColor":bullish_color if zone["direction"]==1 else bearish_color,"opacity":0.15 if zone["kind"]==0 else 0.12})
if show_equal:
    equal_items=equal_history if historical else [dict(firstIndex=item["firstIndex"],secondIndex=right_index,level=item["level"],side=1) for item in liquidity_up if item["kind"]==1][:2]+[dict(firstIndex=item["firstIndex"],secondIndex=right_index,level=item["level"],side=-1) for item in liquidity_down if item["kind"]==1][:2]
    for item in equal_items: layers.append({"type":"line","role":"EQH" if item["side"]==1 else "EQL","startIndex":item["firstIndex"],"endIndex":item.get("secondIndex",right_index),"y1":item["level"],"y2":item["level"],"color":bearish_color if item["side"]==1 else bullish_color,"lineWidth":1,"lineStyle":"dotted"})
if show_liquidity:
    for item,side,probability in ((live_up,1,live_probability_up),(live_down,-1,1.0-live_probability_up if np.isfinite(live_probability_up) else np.nan)):
        if item is not None: layers.append({"type":"line","role":"Buy-side Liquidity" if side==1 else "Sell-side Liquidity","startIndex":item["firstIndex"],"endIndex":right_index,"y1":item["level"],"y2":item["level"],"color":bearish_color if side==1 else bullish_color,"lineWidth":1,"lineStyle":"dashed"});
        if item is not None and not zen_mode: layers.append({"type":"label","role":"Liquidity Odds","index":right_index,"price":item["level"],"text":("BSL " if side==1 else "SSL ")+f"{item['level']:.6g}"+(f" {probability*100:.0f}%" if np.isfinite(probability) else ""),"color":"rgba(0,0,0,0)","textColor":bearish_color if side==1 else bullish_color,"size":"small"})
if show_internal:
    for mark in internal_marks: layers.append({"type":"line","role":"Internal CHoCH" if mark["choch"] else "Internal BOS","startIndex":mark["startIndex"],"endIndex":mark["endIndex"],"y1":mark["level"],"y2":mark["level"],"color":bullish_color if mark["direction"]==1 else bearish_color,"lineWidth":1,"lineStyle":"dashed"})
swing_display_marks=swing_history if historical else swing_marks
structure_label_marks=[]
for mark in reversed(swing_display_marks):
    label_index=int((mark["startIndex"]+mark["endIndex"])/2)
    if label_index<max(0,n-160) or any(abs(label_index-existing)<6 for existing in structure_label_marks): continue
    structure_label_marks.append(label_index)
    if len(structure_label_marks)>=12: break
for mark in swing_display_marks:
    layers.append({"type":"line","role":"Swing CHoCH" if mark["choch"] else "Swing BOS","startIndex":mark["startIndex"],"endIndex":mark["endIndex"],"y1":mark["level"],"y2":mark["level"],"color":bullish_color if mark["direction"]==1 else bearish_color,"lineWidth":2 if mark["choch"] else 1})
    label_index=int((mark["startIndex"]+mark["endIndex"])/2)
    if not zen_mode and label_index in structure_label_marks: layers.append({"type":"label","role":"Swing Structure","index":label_index,"price":mark["level"],"text":"CHoCH" if mark["choch"] else "BOS","color":"rgba(0,0,0,0)","textColor":bullish_color if mark["direction"]==1 else bearish_color,"size":"small"})
if show_swing_labels and not zen_mode:
    for point in swing_points: layers.append({"type":"label","role":"Swing Point","index":point["index"],"price":point["price"],"text":point["text"],"color":"rgba(0,0,0,0)","textColor":bearish_color if point["direction"]==1 else bullish_color,"size":"tiny"})
if show_signals:
    for trade in trades:
        end=right_index if trade["open"] else trade["endIndex"]
        for role,price,color,width,style in (("Entry",trade["entry"],"#808080",1,"dashed"),("Stop",trade["initialStop"],bearish_color if trade["direction"]==1 else bullish_color,2,"solid"),("TP1",trade["target1"],bullish_color if trade["direction"]==1 else bearish_color,1,"solid"),("TP2",trade["target2"],bullish_color if trade["direction"]==1 else bearish_color,2,"solid")): layers.append({"type":"line","role":role,"startIndex":trade["entryIndex"],"endIndex":end,"y1":price,"y2":price,"color":color,"lineWidth":width,"lineStyle":style})
        if trade["breakEvenIndex"]>=0: layers.append({"type":"line","role":"Break Even Stop","startIndex":trade["breakEvenIndex"],"endIndex":end,"y1":trade["entry"],"y2":trade["entry"],"color":bearish_color if trade["direction"]==1 else bullish_color,"lineWidth":2,"lineStyle":"dotted"})
if n and np.all(np.isfinite([range_high[-1],range_low[-1]])):
    start_high=swing["legHiIndex"] if np.isfinite(swing["legHi"]) and (not np.isfinite(swing["hi"]) or swing["legHi"]>=swing["hi"]) else swing["hiIndex"]; start_low=swing["legLoIndex"] if np.isfinite(swing["legLo"]) and (not np.isfinite(swing["lo"]) or swing["legLo"]<=swing["lo"]) else swing["loIndex"]
    if show_strong_weak: layers.extend([{"type":"line","role":"Strong High" if swing["trend"]==-1 else "Weak High","startIndex":start_high,"endIndex":right_index,"y1":range_high[-1],"y2":range_high[-1],"color":bearish_color,"lineWidth":1},{"type":"line","role":"Strong Low" if swing["trend"]==1 else "Weak Low","startIndex":start_low,"endIndex":right_index,"y1":range_low[-1],"y2":range_low[-1],"color":bullish_color,"lineWidth":1}])
    if show_pd:
        midpoint=(range_high[-1]+range_low[-1])/2.0; first=min(start_high,start_low); layers.extend([{"type":"zone","role":"Premium","startIndex":first,"endIndex":right_index,"top":range_high[-1],"bottom":midpoint,"fillColor":bearish_color,"borderColor":bearish_color,"opacity":0.08},{"type":"zone","role":"Discount","startIndex":first,"endIndex":right_index,"top":midpoint,"bottom":range_low[-1],"fillColor":bullish_color,"borderColor":bullish_color,"opacity":0.08}])

tables=[]
if n and show_table and not zen_mode:
    latest_mark=swing_marks[-1] if swing_marks else None; retest_text="—" if latest_mark is None else ("measuring" if latest_mark["probability"] is None else f"{latest_mark['probability']*100:.0f}% @ {latest_mark['level']:.6g}" if latest_mark["retest"]==0 else ("retested" if latest_mark["retest"]==1 else "not retested")); bias="BULLISH" if swing["trend"]==1 else "BEARISH" if swing["trend"]==-1 else "NO STRUCTURE"; position_text="" if not np.isfinite(range_position[-1]) else f" · {'PREMIUM' if range_position[-1]>0.5 else 'DISCOUNT'} {range_position[-1]*100:.0f}%"; current_trade=trades[-1] if trades and trades[-1]["open"] else None; now=(f"{'LONG' if current_trade['direction']==1 else 'SHORT'} {current_trade['entry']:.6g} · SL {current_trade['stop']:.6g} · TP1 {current_trade['target1']:.6g} · TP2 {current_trade['target2']:.6g}" if current_trade else f"Wait retest {waiting['level']:.6g}" if waiting else "Waiting for swing structure")
    live_text="LEARNING" if retest_cal["n"]<CAL_MIN else "LIVE · calibrated" if _calibrated(retest_cal) else "LIVE · check calibration"; liquidity_text="needs pools" if live_up is None or live_down is None else f"BSL {live_up['level']:.6g} {live_probability_up*100:.0f}% · SSL {live_down['level']:.6g} {(1-live_probability_up)*100:.0f}%" if np.isfinite(live_probability_up) else "price at pool"; record="—" if closed_stats["count"]==0 else f"{closed_stats['count']} hypothetical · avg {closed_stats['sumR']/closed_stats['count']:+.2f}R"; calibration="—" if retest_cal["n"]==0 else f"retest {retest_cal['sumProbability']/retest_cal['n']*100:.0f}→{retest_cal['hit']/retest_cal['n']*100:.0f}% (n {retest_cal['n']})"
    rows=[[{"text":"Engine"},{"text":f"{live_text} · {retest_cal['n']}/{CAL_MIN}"}],[{"text":"Bias"},{"text":bias+position_text,"color":bullish_color if swing["trend"]==1 else bearish_color if swing["trend"]==-1 else "#808080"}],[{"text":"Now"},{"text":now}],[{"text":"Retest"},{"text":retest_text}]]
    if show_liquidity: rows.append([{"text":"Liquidity"},{"text":liquidity_text}])
    rows.extend([[{"text":"Record"},{"text":record}],[{"text":"Calibration"},{"text":calibration}],[{"text":"Structure view"},{"text":f"{'loaded history' if historical else 'recent'} · {len(swing_history if historical else swing_marks)} swing · {len(internal_marks)} internal · {len(equal_history)} EQ"}]])
    tables.append({"title":"SMC ML","position":table_position,"compact":True,"rows":rows})

# Read-only attribute views keep the final serialization compact without using
# Python classes, which the indicator sandbox intentionally disables.
retest_calibrated=_calibrated(retest_cal); liquidity_calibrated=_calibrated(liquidity_cal)
retest_cal=pd.Series({"a":retest_cal["a"],"b":retest_cal["b"],"n":retest_cal["n"],"hit":retest_cal["hit"],"sum_probability":retest_cal["sumProbability"],"brier_model":retest_cal["brierModel"],"brier_formula":retest_cal["brierFormula"],"brier_base":retest_cal["brierBase"],"bands":retest_cal["bands"],"calibrated":(lambda value=retest_calibrated: value)})
liquidity_cal=pd.Series({"a":liquidity_cal["a"],"b":liquidity_cal["b"],"n":liquidity_cal["n"],"hit":liquidity_cal["hit"],"sum_probability":liquidity_cal["sumProbability"],"brier_model":liquidity_cal["brierModel"],"brier_formula":liquidity_cal["brierFormula"],"brier_base":liquidity_cal["brierBase"],"bands":liquidity_cal["bands"],"calibrated":(lambda value=liquidity_calibrated: value)})

output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Dealing Range High","title":False,"data":_finite(range_high),"color":bearish_color,"type":"line","lineWidth":1,"overlay":True,"visible":False},{"name":"Dealing Range Low","title":False,"data":_finite(range_low),"color":bullish_color,"type":"line","lineWidth":1,"overlay":True,"visible":False},{"name":"Price Guide","title":False,"data":_finite(close),"color":"rgba(0,0,0,0)","type":"line","overlay":True}],"signals":[{"type":"buy","name":"BUY","text":"BUY","data":[{"active":True,"price":float(low[i]),"text":"BUY","color":bullish_color} if show_signals and event_buy[i] else None for i in range(n)],"color":bullish_color,"renderMode":"events","overlay":True},{"type":"sell","name":"SELL","text":"SELL","data":[{"active":True,"price":float(high[i]),"text":"SELL","color":bearish_color} if show_signals and event_sell[i] else None for i in range(n)],"color":bearish_color,"renderMode":"events","overlay":True},{"type":"exit","name":"Exit","text":"EXIT","data":[{"active":True,"price":float(exit_event[i]),"text":"EXIT","color":"#808080"} if show_signals and event_exit[i] and np.isfinite(exit_event[i]) else None for i in range(n)],"color":"#808080","renderMode":"events","overlay":True}],"layers":layers,"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"deviation":_finite(deviation),"swingPivotHigh":_finite(swing_high),"swingPivotLow":_finite(swing_low),"internalPivotHigh":_finite(internal_high),"internalPivotLow":_finite(internal_low),"fvgBull":fvg_bull.tolist(),"fvgBear":fvg_bear.tolist(),"swingTrend":swing_trend.tolist(),"rangeHigh":_finite(range_high),"rangeLow":_finite(range_low),"rangePosition":_finite(range_position),"bullishChoch":event_bull_choch.tolist(),"bearishChoch":event_bear_choch.tolist(),"bullishBos":event_bull_bos.tolist(),"bearishBos":event_bear_bos.tolist(),"buy":event_buy.tolist(),"sell":event_sell.tolist(),"exit":event_exit.tolist(),"liquiditySweep":event_sweep.tolist(),"retestProbabilityAtBreak":_finite(retest_probability),"openTradeState":open_trade_state.tolist(),"entryPrice":_finite(entry_event),"exitPrice":_finite(exit_event),"retestCalibration":{"a":float(retest_cal.a),"b":float(retest_cal.b),"n":int(retest_cal.n),"hits":int(retest_cal.hit),"sumProbability":float(retest_cal.sum_probability),"brierModel":float(retest_cal.brier_model),"brierFormula":float(retest_cal.brier_formula),"brierBase":float(retest_cal.brier_base),"calibrated":bool(retest_cal.calibrated()),"bands":retest_cal.bands},"liquidityCalibration":{"a":float(liquidity_cal.a),"b":float(liquidity_cal.b),"n":int(liquidity_cal.n),"hits":int(liquidity_cal.hit),"sumProbability":float(liquidity_cal.sum_probability),"brierModel":float(liquidity_cal.brier_model),"brierFormula":float(liquidity_cal.brier_formula),"brierBase":float(liquidity_cal.brier_base),"calibrated":bool(liquidity_cal.calibrated()),"bands":liquidity_cal.bands},"liveLiquidityUp":float(live_up["level"]) if live_up else None,"liveLiquidityDown":float(live_down["level"]) if live_down else None,"liveLiquidityProbabilityUp":float(live_probability_up) if np.isfinite(live_probability_up) else None,"liveLiquidityFormulaUp":float(live_formula_up) if np.isfinite(live_formula_up) else None,"closedTrades":int(closed_stats["count"]),"tp1Trades":int(closed_stats["tp1"]),"fullStops":int(closed_stats["fullStop"]),"sumR":float(closed_stats["sumR"]),"sumR2":float(closed_stats["sumR2"]),"edgeCounters":edge,"activeZones":zones,"activeLiquidityUp":liquidity_up,"activeLiquidityDown":liquidity_down,"swingMarks":swing_marks,"internalMarks":internal_marks,"equalHistory":equal_history,"trades":trades}}
