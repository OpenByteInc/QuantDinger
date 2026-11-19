# QuantDinger XAUUSD Trading Suite
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger XAUUSD Trading Suite"
my_indicator_description="Combines alternating range-exhaustion signals with VWAP/EMA momentum, swing order blocks, dynamic support/resistance, fair-value gaps, daily pivots and ATR targets."

# @param minimum_move float 15 Minimum alternating move range=0.1:1000:0.1
# @param major_move float 30 Major move range=0.1:2000:0.1
# @param use_ema_filter bool false Use EMA 200 filter
# @param show_ema_200 bool true Show EMA 200
# @param show_vwap_ema bool true Show VWAP and EMA 9
# @param show_fvg bool true Show fair value gaps
# @param show_targets bool true Show target plans
# @param pivot_length int 15 Order-block pivot length range=2:100:1

minimum=max(0.1,min(1000,float(params.get("minimum_move",15)))); major=max(minimum,min(2000,float(params.get("major_move",30)))); ema_filter=bool(params.get("use_ema_filter",False)); show_ema200=bool(params.get("show_ema_200",True)); show_vwap=bool(params.get("show_vwap_ema",True)); show_fvg=bool(params.get("show_fvg",True)); show_targets=bool(params.get("show_targets",True)); pivot_length=max(2,min(100,int(params.get("pivot_length",15))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
def _rma(values,length):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1)+float(value))/length; out[i]=state
    return out
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); ema200=_ema(c,200); ema9=_ema(c,9); dates=np.asarray(pd.to_datetime(_seconds(frame["time"].to_numpy(float)),unit="s",utc=True).strftime("%Y-%m-%d")); vwap=np.full(n,np.nan); daily_open=np.full(n,np.nan); daily_pivot=np.full(n,np.nan); pv=0.0; vv=0.0; current=None; previous_daily=None; current_daily=None
for i in range(n):
    if current!=dates[i]:
        if current_daily is not None: previous_daily=current_daily
        current=dates[i]; pv=0; vv=0; current_daily={"open":float(o[i]),"high":float(h[i]),"low":float(l[i]),"close":float(c[i])}
    else: current_daily["high"]=max(current_daily["high"],float(h[i])); current_daily["low"]=min(current_daily["low"],float(l[i])); current_daily["close"]=float(c[i])
    pv+=c[i]*v[i]; vv+=v[i]; vwap[i]=pv/vv if vv else np.nan; daily_open[i]=current_daily["open"]
    if previous_daily is not None: daily_pivot[i]=(previous_daily["high"]+previous_daily["low"]+previous_daily["close"])/3
cross_up=np.zeros(n,bool); cross_down=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(vwap[1:])&np.isfinite(vwap[:-1]); cross_up[1:]=valid&(ema9[1:]>vwap[1:])&(ema9[:-1]<=vwap[:-1]); cross_down[1:]=valid&(ema9[1:]<vwap[1:])&(ema9[:-1]>=vwap[:-1])
pivot_high=np.zeros(n,bool); pivot_low=np.zeros(n,bool)
for confirmed in range(pivot_length*2,n):
    p=confirmed-pivot_length; sample_h=h[p-pivot_length:p+pivot_length+1]; sample_l=l[p-pivot_length:p+pivot_length+1]; pivot_high[p]=h[p]>=np.max(sample_h); pivot_low[p]=l[p]<=np.min(sample_l)
resistance=np.full(n,np.nan); support=np.full(n,np.nan); supply=None; demand=None; layers=[]; fvg=[]; active_r=np.nan; active_s=np.nan
for i in range(n):
    if pivot_high[i]: active_r=float(h[i]); supply={"startIndex":int(i),"endIndex":int(n+5),"top":float(h[i]),"bottom":float(l[i])}
    if pivot_low[i]: active_s=float(l[i]); demand={"startIndex":int(i),"endIndex":int(n+5),"top":float(l[i]),"bottom":float(l[i])}
    resistance[i]=active_r; support[i]=active_s
    if i>=2 and show_fvg:
        if l[i]>h[i-2]: fvg.append({"startIndex":int(i-2),"endIndex":int(i+5),"top":float(l[i]),"bottom":float(h[i-2]),"direction":1})
        if h[i]<l[i-2]: fvg.append({"startIndex":int(i-2),"endIndex":int(i+5),"top":float(l[i-2]),"bottom":float(h[i]),"direction":-1})
if supply: layers.append({"type":"zone","role":"Supply OB",**supply,"fillColor":"rgba(244,67,54,0.15)","borderColor":"#F44336"})
if demand: layers.append({"type":"zone","role":"Demand OB",**demand,"fillColor":"rgba(76,175,80,0.15)","borderColor":"#4CAF50"})
for gap in fvg[-12:]: layers.append({"type":"zone","role":"Bullish FVG" if gap["direction"]==1 else "Bearish FVG","startIndex":gap["startIndex"],"endIndex":gap["endIndex"],"top":gap["top"],"bottom":gap["bottom"],"fillColor":"rgba(76,175,80,0.10)" if gap["direction"]==1 else "rgba(244,67,54,0.10)","borderColor":"rgba(0,0,0,0)"})
typical_range=float(np.nanmedian(h-l)) if n else 0.0; adaptive_minimum=max(minimum,typical_range*0.75); adaptive_major=max(major,typical_range*1.5)
buy=np.zeros(n,bool); sell=np.zeros(n,bool); bull_diamond=np.zeros(n,bool); bear_diamond=np.zeros(n,bool); extreme_high=h[0] if n else np.nan; extreme_low=l[0] if n else np.nan; state=0
for i in range(n):
    extreme_high=max(extreme_high,h[i]); extreme_low=min(extreme_low,l[i])
    if state<=0 and extreme_high-l[i]>=adaptive_minimum and c[i]>o[i]:
        raw=True; bull_diamond[i]=extreme_high-l[i]>=adaptive_major; state=1; extreme_low=l[i]; extreme_high=h[i]; buy[i]=raw and (not ema_filter or c[i]>ema200[i])
    elif state>=0 and h[i]-extreme_low>=adaptive_minimum and c[i]<o[i]:
        raw=True; bear_diamond[i]=h[i]-extreme_low>=adaptive_major; state=-1; extreme_high=h[i]; extreme_low=l[i]; sell[i]=raw and (not ema_filter or c[i]<ema200[i])
    bull_diamond[i]&=buy[i]; bear_diamond[i]&=sell[i]
previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,14); plans=[]
if show_targets:
    for i in range(n):
        if (buy[i] or sell[i]) and np.isfinite(atr[i]):
            direction=1 if buy[i] else -1; risk=float(atr[i]*1.5); entry=float(c[i]); plan={"startIndex":int(i),"endIndex":int(i+10),"direction":direction,"entry":entry,"stop":entry-direction*risk,"targets":[entry+direction*risk*x for x in (1.5,2,3)]}; plans.append(plan)
    for plan in plans[-2:]:
        for role,price,color in (("SL",plan["stop"],"#F44336"),("TP1",plan["targets"][0],"#4CAF50"),("TP2",plan["targets"][1],"#4CAF50"),("TP3",plan["targets"][2],"#4CAF50")): layers.append({"type":"line","role":role,"startIndex":plan["startIndex"],"endIndex":plan["endIndex"],"y1":price,"y2":price,"color":color,"lineWidth":2 if role in ("SL","TP3") else 1})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
plots=[]
if show_ema200: plots.append({"name":"EMA 200","data":_finite(ema200),"color":"#9C27B0","type":"line","lineWidth":2,"overlay":True})
if show_vwap: plots.extend([{"name":"VWAP","data":_finite(vwap),"color":"#F44336","type":"line","lineWidth":2,"overlay":True},{"name":"EMA 9","data":_finite(ema9),"color":"#4CAF50","type":"line","lineWidth":2,"overlay":True}])
plots.extend([{"name":"Resistance","data":_finite(resistance),"color":"#F44336","type":"line","lineWidth":1,"overlay":True},{"name":"Support","data":_finite(support),"color":"#4CAF50","type":"line","lineWidth":1,"overlay":True},{"name":"Daily Pivot","data":_finite(daily_pivot),"color":"#2196F3","type":"line","lineWidth":2,"overlay":True},{"name":"Daily Open","data":_finite(daily_open),"color":"#FF9800","type":"line","lineWidth":2,"overlay":True}])
def _recent(mask,limit=1):
    visible=np.zeros(n,bool); indices=np.flatnonzero(mask)
    if len(indices): visible[indices[-limit:]]=True
    return visible
regular_buy=_recent(buy&~bull_diamond); regular_sell=_recent(sell&~bear_diamond); display_bull_diamond=_recent(bull_diamond); display_bear_diamond=_recent(bear_diamond); display_cross_up=_recent(cross_up&~buy&~sell); display_cross_down=_recent(cross_down&~buy&~sell)
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(regular_buy,l,"BUY","#4CAF50","buy"),_events(regular_sell,h,"SELL","#F44336","sell"),_events(display_bull_diamond,l,"2x DIAMOND","#00BCD4","buy"),_events(display_bear_diamond,h,"2x DIAMOND","#FF9800","sell"),_events(display_cross_up,l,"EMA9 > VWAP","#4CAF50","buy"),_events(display_cross_down,h,"EMA9 < VWAP","#F44336","sell")],"layers":layers,"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"ema200":_finite(ema200),"ema9":_finite(ema9),"vwap":_finite(vwap),"vwapCrossUp":cross_up.tolist(),"vwapCrossDown":cross_down.tolist(),"pivotHigh":pivot_high.tolist(),"pivotLow":pivot_low.tolist(),"resistance":_finite(resistance),"support":_finite(support),"dailyOpen":_finite(daily_open),"dailyPivot":_finite(daily_pivot),"buySignal":buy.tolist(),"sellSignal":sell.tolist(),"bullDiamond":bull_diamond.tolist(),"bearDiamond":bear_diamond.tolist(),"adaptiveMinimum":adaptive_minimum,"adaptiveMajor":adaptive_major,"fairValueGaps":fvg,"plans":plans}}
