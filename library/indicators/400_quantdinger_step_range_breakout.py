# QuantDinger Step Range Breakout
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Step Range Breakout"
my_indicator_description="Confirms stable structural ranges, detects breakouts and manages an ATR trailing stop with rolling trade statistics."

# @param structure_length int 20 Structure length range=1:500:1
# @param consolidation_bars int 5 Consolidation bars range=1:100:1
# @param atr_length int 14 Trailing ATR length range=1:500:1
# @param atr_multiplier float 3 Trailing ATR multiplier range=0.1:20:0.1
# @param win_rate_lookback int 100 Win-rate lookback trades range=1:1000:1
# @param bullish_color str #0AA797 Bullish color
# @param bearish_color str #FF0000 Bearish color
# @param middle_color str #BEC1CC Middle color

length=max(1,min(500,int(params.get("structure_length",20)))); consolidation=max(1,min(100,int(params.get("consolidation_bars",5)))); atr_length=max(1,min(500,int(params.get("atr_length",14)))); multiplier=max(0.1,min(20,float(params.get("atr_multiplier",3)))); win_lookback=max(1,min(1000,int(params.get("win_rate_lookback",100)))); bull=str(params.get("bullish_color","#0AA797")); bear=str(params.get("bearish_color","#FF0000")); middle=str(params.get("middle_color","#BEC1CC"))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _rma(v,length):
    out=np.full(len(v),np.nan); state=np.nan; seed=[]
    for i,x in enumerate(v):
        if not np.isfinite(x): continue
        if not np.isfinite(state):
            seed.append(float(x))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1)+float(x))/length; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame); highest=np.full(n,np.nan); lowest=np.full(n,np.nan)
for i in range(length-1,n): highest[i]=np.max(high[i-length+1:i+1]); lowest[i]=np.min(low[i-length+1:i+1])
average=(highest+lowest)/2; stable=np.zeros(n,bool)
for i in range(consolidation,n): stable[i]=np.isfinite(average[i]) and average[i]==average[i-consolidation]
previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan; atr=_rma(tr,atr_length)
state=np.zeros(n,dtype=int); zone_upper=np.full(n,np.nan); zone_lower=np.full(n,np.nan); zone_middle=np.full(n,np.nan); trailing=np.full(n,np.nan); direction=np.zeros(n,dtype=int); break_up=np.zeros(n,bool); break_down=np.zeros(n,bool); exit_event=np.zeros(n,bool); current="SEARCHING"; upper=lower=mid=stop=entry=np.nan; bullish=True; zone_start=-1; trades=[]; zones=[]
for i in range(n):
    if current=="SEARCHING" and stable[i]: upper=float(highest[i]); lower=float(lowest[i]); mid=float(average[i]); zone_start=max(0,i-consolidation); current="ZONE_ACTIVE"
    elif current=="ZONE_ACTIVE":
        if close[i]>upper: bullish=True; stop=float(low[i]-atr[i]*multiplier); entry=float(close[i]); break_up[i]=True; current="TRAILING"; zones.append({"start":zone_start,"end":i,"upper":upper,"lower":lower,"direction":1})
        elif close[i]<lower: bullish=False; stop=float(high[i]+atr[i]*multiplier); entry=float(close[i]); break_down[i]=True; current="TRAILING"; zones.append({"start":zone_start,"end":i,"upper":upper,"lower":lower,"direction":-1})
    elif current=="TRAILING":
        if bullish:
            stop=max(stop,float(low[i]-atr[i]*multiplier))
            if close[i]<stop: trades.append(1 if close[i]>entry else 0); exit_event[i]=True; current="SEARCHING"
        else:
            stop=min(stop,float(high[i]+atr[i]*multiplier))
            if close[i]>stop: trades.append(1 if close[i]<entry else 0); exit_event[i]=True; current="SEARCHING"
        trades=trades[-win_lookback:]
    state[i]=0 if current=="SEARCHING" else 1 if current=="ZONE_ACTIVE" else 2; direction[i]=1 if bullish else -1; zone_upper[i]=upper if current!="SEARCHING" else np.nan; zone_lower[i]=lower if current!="SEARCHING" else np.nan; zone_middle[i]=mid if current!="SEARCHING" else np.nan; trailing[i]=stop if current=="TRAILING" else np.nan
layers=[]
for z in zones[-30:]: layers.append({"type":"zone","role":"Bullish Range" if z["direction"]==1 else "Bearish Range","startIndex":z["start"],"endIndex":z["end"],"top":z["upper"],"bottom":z["lower"],"fillColor":bull if z["direction"]==1 else bear,"borderColor":middle,"opacity":0.12})
if current=="ZONE_ACTIVE": layers.append({"type":"zone","role":"Active Range","startIndex":zone_start,"endIndex":n+2,"top":upper,"bottom":lower,"fillColor":middle,"borderColor":middle,"opacity":0.10})
wins=sum(trades); total=len(trades); rate=100*wins/total if total else 0
tables=[{"title":"Step Range","position":"top-right","compact":True,"rows":[[{"text":"State"},{"text":current}],[{"text":"Closed trades"},{"text":str(total)}],[{"text":"Win rate"},{"text":f"{wins}/{total} ({rate:.1f}%)","color":bull if rate>=50 else bear}]]}] if n else []
display_zone=state==1
display_middle=np.where(display_zone,zone_middle,np.nan); display_upper=np.where(display_zone,zone_upper,np.nan); display_lower=np.where(display_zone,zone_lower,np.nan)
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Range Middle","title":False,"data":_finite(display_middle),"color":middle,"type":"line","lineWidth":1,"lineStyle":"step","overlay":True},{"name":"Range Upper","title":False,"data":_finite(display_upper),"color":middle,"type":"line","lineWidth":1,"lineStyle":"step","overlay":True},{"name":"Range Lower","title":False,"data":_finite(display_lower),"color":middle,"type":"line","lineWidth":1,"lineStyle":"step","overlay":True},{"name":"Trailing Stop","title":bool(current=="TRAILING"),"data":_finite(trailing),"color":bull,"negativeColor":bear,"type":"line","lineWidth":2,"overlay":True}],
"signals":[{"type":"buy","name":"Bullish Breakout","text":"BO","data":[{"active":True,"price":float(low[i]),"text":"BO","color":bull} if break_up[i] else None for i in range(n)],"color":bull,"renderMode":"events","overlay":True},{"type":"sell","name":"Bearish Breakout","text":"BO","data":[{"active":True,"price":float(high[i]),"text":"BO","color":bear} if break_down[i] else None for i in range(n)],"color":bear,"renderMode":"events","overlay":True}],"layers":layers,"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"highest":_finite(highest),"lowest":_finite(lowest),"rangeAverage":_finite(average),"stable":stable.tolist(),"atr":_finite(atr),"state":state.tolist(),"direction":direction.tolist(),"zoneUpper":_finite(zone_upper),"zoneLower":_finite(zone_lower),"zoneMiddle":_finite(zone_middle),"trailingStop":_finite(trailing),"breakoutUp":break_up.tolist(),"breakoutDown":break_down.tolist(),"exit":exit_event.tolist(),"closedTrades":total,"wins":wins,"winRate":float(rate)}}
