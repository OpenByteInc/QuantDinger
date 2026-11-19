# QuantDinger Trinity Reversal Pattern
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trinity Reversal Pattern"
my_indicator_description="Finds three-candle bullish and bearish reversal structures, scores the signal candle against recent body strength and manages touch-or-expiry reversal levels."

# @param strength_lookback int 100 Body-strength lookback range=2:5000:1
# @param minimum_strength float 0 Minimum strength percent range=0:100:1
# @param level_expiry int 100 Level expiry bars range=1:5000:1
# @param use_trend_filter bool false Use EMA trend filter
# @param fast_ema int 21 Fast EMA range=1:500:1
# @param slow_ema int 50 Slow EMA range=2:1000:1
# @param show_levels bool true Show reversal levels
# @param max_levels int 500 Maximum stored levels range=10:500:1

lookback=max(2,min(5000,int(params.get("strength_lookback",100)))); minimum=max(0,min(100,float(params.get("minimum_strength",0)))); expiry=max(1,min(5000,int(params.get("level_expiry",100)))); use_trend=bool(params.get("use_trend_filter",False)); fast_length=max(1,min(500,int(params.get("fast_ema",21)))); slow_length=max(fast_length+1,min(1000,int(params.get("slow_ema",50)))); show_levels=bool(params.get("show_levels",True)); max_levels=max(10,min(500,int(params.get("max_levels",500))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); bodies=np.abs(c-o); largest=pd.Series(bodies).rolling(lookback,min_periods=1).max().to_numpy(float); strength=np.divide(bodies,largest,out=np.zeros(n),where=largest>0)*100
fast=_ema(c,fast_length); slow=_ema(c,slow_length); bull_cross=np.zeros(n,bool); bear_cross=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(fast[1:])&np.isfinite(slow[1:])&np.isfinite(fast[:-1])&np.isfinite(slow[:-1]); bull_cross[1:]=valid&(fast[1:]>slow[1:])&(fast[:-1]<=slow[:-1]); bear_cross[1:]=valid&(fast[1:]<slow[1:])&(fast[:-1]>=slow[:-1])
trend=np.zeros(n,dtype=int); current=0
for i in range(n):
    if bull_cross[i]: current=1
    elif bear_cross[i]: current=-1
    elif current==0 and np.isfinite(fast[i]) and np.isfinite(slow[i]): current=1 if fast[i]>slow[i] else -1 if fast[i]<slow[i] else 0
    trend[i]=current
bull=np.zeros(n,bool); bear=np.zeros(n,bool)
for i in range(2,n):
    bull[i]=c[i]>o[i] and h[i]>h[i-2] and l[i-1]<l[i-2] and h[i-1]<h[i-2] and c[i-1]<o[i-1] and c[i-2]<o[i-2]
    bear[i]=c[i]<o[i] and l[i]<l[i-2] and h[i-1]>h[i-2] and l[i-1]>l[i-2] and c[i-1]>o[i-1] and c[i-2]>o[i-2]
trend_allows_bull=(trend==1) if use_trend else np.ones(n,bool); trend_allows_bear=(trend==-1) if use_trend else np.ones(n,bool); bull_signal=bull&(strength>=minimum)&trend_allows_bull; bear_signal=bear&(strength>=minimum)&trend_allows_bear; touched_bull=np.zeros(n,bool); touched_bear=np.zeros(n,bool); expired_mask=np.zeros(n,bool); active=[]; history=[]
for i in range(n):
    remaining=[]
    for level in active:
        age=i-level["signalIndex"]; touched=age>0 and (l[i]<=level["price"] if level["direction"]==1 else h[i]>=level["price"]); expired=age>=expiry
        level["endIndex"]=i if touched or expired else i+5
        if touched: touched_bull[i]|=level["direction"]==1; touched_bear[i]|=level["direction"]==-1
        if expired and not touched: expired_mask[i]=True
        if touched or expired: level["active"]=False; history.append(level)
        else: remaining.append(level)
    active=remaining
    if bull_signal[i] or bear_signal[i]:
        direction=1 if bull_signal[i] else -1; sample_l=l[i-2:i+1]; sample_h=h[i-2:i+1]; price=float(np.min(sample_l)) if direction==1 else float(np.max(sample_h)); offset=int(np.argmin(sample_l)) if direction==1 else int(np.argmax(sample_h)); start=i-2+offset; level={"startIndex":int(start),"signalIndex":int(i),"endIndex":int(i+5),"price":price,"direction":direction,"strength":float(strength[i]),"active":True}; active.append(level)
    while len(active)+len(history)>max_levels:
        if history: history.pop(0)
        elif active: active.pop(0)
levels=(history+active)[-max_levels:]; layers=[]
if show_levels:
    for level in levels: layers.append({"type":"line","role":"Bullish reversal" if level["direction"]==1 else "Bearish reversal","startIndex":level["startIndex"],"endIndex":level["endIndex"],"y1":level["price"],"y2":level["price"],"color":"#00FFBB" if level["direction"]==1 else "#FF1100","lineWidth":3 if level["active"] else 1,"lineStyle":"solid" if level["active"] else "dotted","text":f'{level["strength"]:.1f}% Strength'})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
plots=[]
if use_trend: plots=[{"name":"Fast Trend EMA","data":_finite(fast),"color":"#00FFBB","negativeColor":"#FF1100","type":"line","lineWidth":2,"overlay":True},{"name":"Slow Trend EMA","data":_finite(slow),"color":"rgba(0,255,187,0.55)","negativeColor":"rgba(255,17,0,0.55)","type":"line","lineWidth":2,"overlay":True}]
else: plots=[{"name":"Price Guide","title":False,"data":_finite(c),"color":"rgba(0,0,0,0)","type":"line","lineWidth":1,"overlay":True}]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(bull_signal,l,"TRINITY UP","#00FFBB","buy"),_events(bear_signal,h,"TRINITY DOWN","#FF1100","sell"),_events(touched_bull,c,"BULL LEVEL TOUCHED","#00FFBB","event"),_events(touched_bear,c,"BEAR LEVEL TOUCHED","#FF1100","event"),_events(expired_mask,c,"LEVEL EXPIRED","#9E9E9E","event"),_events(use_trend&bull_cross,slow,"EMA CROSS UP","#00FFBB","buy"),_events(use_trend&bear_cross,slow,"EMA CROSS DOWN","#FF1100","sell")],"layers":layers,"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"strength":_finite(strength),"fastEMA":_finite(fast),"slowEMA":_finite(slow),"trend":trend.tolist(),"bullCross":bull_cross.tolist(),"bearCross":bear_cross.tolist(),"bullSignal":bull_signal.tolist(),"bearSignal":bear_signal.tolist(),"bullLevelTouched":touched_bull.tolist(),"bearLevelTouched":touched_bear.tolist(),"levelExpired":expired_mask.tolist(),"levels":levels}}
