# QuantDinger Wick Sequence Breakout
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Wick Sequence Breakout"
my_indicator_description="Builds concurrent formations from wick or absorption sequences, confirms breakouts only when direction agrees with accumulated buy/sell volume and projects ATR risk targets."

# @param minimum_wick_candles int 3 Minimum wick candles range=1:20:1
# @param window_size int 5 Window size range=1:50:1
# @param minimum_upper_wick float 45 Bearish upper wick percent range=0:100:1
# @param maximum_lower_wick float 25 Bearish lower wick percent range=0:100:1
# @param minimum_lower_wick float 45 Bullish lower wick percent range=0:100:1
# @param maximum_upper_wick float 25 Bullish upper wick percent range=0:100:1
# @param maximum_wait_bars int 100 Maximum wait bars range=0:5000:1
# @param breakout_by_close bool true Use close for breakout
# @param use_absorption bool true Count absorption candles
# @param maximum_concurrent int 2 Maximum concurrent formations range=1:30:1
# @param show_targets bool true Show TP and SL

minimum=max(1,min(20,int(params.get("minimum_wick_candles",3)))); window=max(1,min(50,int(params.get("window_size",5)))); upper_min=max(0,min(100,float(params.get("minimum_upper_wick",45)))); lower_max=max(0,min(100,float(params.get("maximum_lower_wick",25)))); lower_min=max(0,min(100,float(params.get("minimum_lower_wick",45)))); upper_max=max(0,min(100,float(params.get("maximum_upper_wick",25)))); wait=max(0,min(5000,int(params.get("maximum_wait_bars",100)))); use_close=bool(params.get("breakout_by_close",True)); absorption=bool(params.get("use_absorption",True)); concurrent=max(1,min(30,int(params.get("maximum_concurrent",2)))); show_targets=bool(params.get("show_targets",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _rma(values,length):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1)+float(value))/length; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); candle_range=h-l; upper_pct=np.divide(h-np.maximum(o,c),candle_range,out=np.zeros(n),where=candle_range>0)*100; lower_pct=np.divide(np.minimum(o,c)-l,candle_range,out=np.zeros(n),where=candle_range>0)*100; previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(candle_range,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=candle_range[0]
atr=_rma(tr,14); volume_average=pd.Series(v).rolling(20,min_periods=20).mean().to_numpy(float); bear_shape=(upper_pct>=upper_min)&(lower_pct<=lower_max); bull_shape=(lower_pct>=lower_min)&(upper_pct<=upper_max); bear_wick=bear_shape; bull_wick=bull_shape; high_volume=absorption&(v>=volume_average*1.1); close_position=np.divide(c-l,candle_range,out=np.full(n,50.0),where=candle_range>0)*100; is_absorption=high_volume&(candle_range<=atr*0.6)&(~bear_wick)&(~bull_wick); bull_abs=is_absorption&(bull_shape|((~bear_shape)&(close_position>=60))); bear_abs=is_absorption&(bear_shape|((~bull_shape)&(close_position<=40))); bull_candle=bull_wick|bull_abs; bear_candle=bear_wick|bear_abs; bull_count=pd.Series(bull_candle.astype(int)).rolling(window,min_periods=window).sum().fillna(0).to_numpy(int); bear_count=pd.Series(bear_candle.astype(int)).rolling(window,min_periods=window).sum().fillna(0).to_numpy(int); any_count=pd.Series((bull_candle|bear_candle).astype(int)).rolling(window,min_periods=window).sum().fillna(0).to_numpy(int); bull_pattern=(bull_count>=minimum)&~(bear_count>=minimum); bear_pattern=(bear_count>=minimum)&~(bull_count>=minimum); mixed_pattern=(any_count>=minimum)&~bull_pattern&~bear_pattern; bull_formed=np.zeros(n,bool); bear_formed=np.zeros(n,bool); mixed_formed=np.zeros(n,bool)
if n: bull_formed[0]=bull_pattern[0]; bear_formed[0]=bear_pattern[0]; mixed_formed[0]=mixed_pattern[0]
if n>1: bull_formed[1:]=bull_pattern[1:]&~bull_pattern[:-1]; bear_formed[1:]=bear_pattern[1:]&~bear_pattern[:-1]; mixed_formed[1:]=mixed_pattern[1:]&~mixed_pattern[:-1]
active=[]; finished=[]; bull_signal=np.zeros(n,bool); bear_signal=np.zeros(n,bool); expired=np.zeros(n,bool); invalid=np.zeros(n,bool); plans=[]
for i in range(n):
    for index in range(len(active)-1,-1,-1):
        form=active[index]; up=(c[i] if use_close else h[i])>form["top"]; down=(c[i] if use_close else l[i])<form["bottom"]
        if i>form["formIndex"] and not up and not down and h[i]>form["bottom"] and l[i]<form["top"]:
            divisor=max(candle_range[i],1e-12); form["bullVolume"]+=float(v[i]*(c[i]-l[i])/divisor); form["bearVolume"]+=float(v[i]*(h[i]-c[i])/divisor)
        if up or down:
            valid_up=up and form["bullVolume"]>=form["bearVolume"]; valid_down=down and form["bearVolume"]>form["bullVolume"]; form["endIndex"]=i; form["status"]="bull" if valid_up else "bear" if valid_down else "invalid"; invalid[i]|=not(valid_up or valid_down)
            if valid_up: bull_signal[i]=True
            if valid_down: bear_signal[i]=True
            finished.append(form); active.pop(index)
        elif wait and i-form["formIndex"]>wait:
            form["endIndex"]=i; form["status"]="expired"; expired[i]=True; finished.append(form); active.pop(index)
        else: form["endIndex"]=i+1
    recent=any(form["formIndex"]>i-window for form in active); create=(bull_formed[i] or bear_formed[i] or (mixed_formed[i] and not recent)) and len(active)<concurrent and i>=window-1
    if create:
        start=i-window+1; top=float(np.max(h[start:i+1])); bottom=float(np.min(l[start:i+1])); bull_volume=0.0; bear_volume=0.0
        for j in range(start,i+1):
            divisor=max(candle_range[j],1e-12); bull_volume+=float(v[j]*(c[j]-l[j])/divisor); bear_volume+=float(v[j]*(h[j]-c[j])/divisor)
        active.append({"startIndex":int(start),"formIndex":int(i),"endIndex":int(i+1),"top":top,"bottom":bottom,"bullVolume":bull_volume,"bearVolume":bear_volume,"mixed":bool(mixed_formed[i]),"status":"active"})
    if show_targets and (bull_signal[i]^bear_signal[i]) and np.isfinite(atr[i]):
        direction=1 if bull_signal[i] else -1; risk=float(atr[i]*1.5); entry=float(c[i]); plans.append({"startIndex":int(i),"endIndex":int(i+100),"direction":direction,"entry":entry,"stop":entry-direction*risk,"targets":[entry+direction*risk*x for x in (1,2,3)]})
layers=[]
for form in (finished[-8:]+active)[-12:]:
    color="rgba(6,162,47,0.14)" if form["status"]=="bull" else "rgba(207,23,23,0.14)" if form["status"]=="bear" else "rgba(128,128,128,0.10)"; total=form["bullVolume"]+form["bearVolume"]; bull_share=form["bullVolume"]/total*100 if total else 50; layers.append({"type":"zone","role":"Wick Formation","startIndex":form["startIndex"],"endIndex":form["endIndex"],"top":form["top"],"bottom":form["bottom"],"fillColor":color,"borderColor":"#06A22F" if form["status"]=="bull" else "#CF1717" if form["status"]=="bear" else "#808080","text":f'Buy {bull_share:.1f}% / Sell {100-bull_share:.1f}%'})
for plan in plans[-3:]:
    color="#06A22F" if plan["direction"]==1 else "#CF1717"
    for role,price,line_color in (("Entry",plan["entry"],color),("SL",plan["stop"],"#F44336"),("TP1",plan["targets"][0],"#4CAF50"),("TP2",plan["targets"][1],"#4CAF50"),("TP3",plan["targets"][2],"#4CAF50")): layers.append({"type":"line","role":role,"startIndex":plan["startIndex"],"endIndex":plan["endIndex"],"y1":price,"y2":price,"color":line_color,"lineWidth":2 if role in ("Entry","TP3") else 1})
def _thin(mask,limit=4,min_gap=16):
    kept=np.zeros(len(mask),bool); last=None; count=0
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index); count+=1
            if count>=limit: break
    return kept
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
bar_colors=["rgba(207,23,23,0.45)" if bear_wick[i] else "rgba(6,162,47,0.45)" if bull_wick[i] else "rgba(207,23,23,0.72)" if bear_abs[i] else "rgba(6,162,47,0.72)" if bull_abs[i] else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Price Guide","title":False,"data":_finite(c),"color":"rgba(0,0,0,0)","type":"line","lineWidth":1,"overlay":True}],"signals":[_events(_thin(bull_signal),l,"WICK BREAK UP","#06A22F","buy"),_events(_thin(bear_signal),h,"WICK BREAK DOWN","#CF1717","sell"),_events(_thin(expired,3,20),c,"EXPIRED","#808080","event"),_events(_thin(invalid,3,20),c,"VOLUME MISMATCH","#808080","event")],"layers":layers,"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"upperWickPercent":_finite(upper_pct),"lowerWickPercent":_finite(lower_pct),"bullWick":bull_wick.tolist(),"bearWick":bear_wick.tolist(),"bullAbsorption":bull_abs.tolist(),"bearAbsorption":bear_abs.tolist(),"bullCount":bull_count.tolist(),"bearCount":bear_count.tolist(),"bullFormed":bull_formed.tolist(),"bearFormed":bear_formed.tolist(),"mixedFormed":mixed_formed.tolist(),"bullSignal":bull_signal.tolist(),"bearSignal":bear_signal.tolist(),"expired":expired.tolist(),"invalid":invalid.tolist(),"formations":finished+active,"plans":plans}}
