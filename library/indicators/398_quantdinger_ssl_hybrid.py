# QuantDinger SSL Hybrid
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger SSL Hybrid"
my_indicator_description="Combines a volatility channel baseline, primary and continuation SSL lines, exit crosses and ATR-percentile risk context."

# @param display_mode str Full Display Display mode values=Baseline Only,Baseline + SSL,SSL Only,Entry/Exit Only,Full Display
# @param color_bars bool true Color bars
# @param show_signals bool true Show signal diamonds
# @param show_risk_table bool true Show risk table
# @param baseline_type str HMA Baseline type values=SMA,EMA,DEMA,TEMA,LSMA,WMA,TMA,HMA,JMA,McGinley
# @param baseline_length int 60 Baseline length range=1:500:1
# @param channel_multiplier float 0.2 Channel multiplier range=0:10:0.05
# @param ssl2_type str JMA SSL2 type values=SMA,EMA,DEMA,TEMA,WMA,TMA,HMA,JMA,McGinley
# @param ssl2_length int 5 SSL2 length range=1:500:1
# @param continuation_atr_criteria float 0.9 Continuation ATR criteria range=0:10:0.1
# @param exit_type str HMA Exit type values=DEMA,TEMA,LSMA,TMA,HMA,JMA,McGinley
# @param exit_length int 15 Exit length range=1:500:1
# @param atr_length int 14 ATR length range=1:500:1
# @param atr_multiplier float 1 ATR multiplier range=0:20:0.1
# @param risk_lookback int 100 Risk lookback range=50:500:1
# @param bullish_color str #00C3FF Bullish color
# @param bearish_color str #FF0062 Bearish color

display=str(params.get("display_mode","Full Display")); color_bars=bool(params.get("color_bars",True)); show_signals=bool(params.get("show_signals",True)); show_table=bool(params.get("show_risk_table",True)); baseline_type=str(params.get("baseline_type","HMA")); baseline_length=max(1,min(500,int(params.get("baseline_length",60)))); channel_mult=max(0,min(10,float(params.get("channel_multiplier",0.2)))); ssl2_type=str(params.get("ssl2_type","JMA")); ssl2_length=max(1,min(500,int(params.get("ssl2_length",5)))); atr_crit=max(0,min(10,float(params.get("continuation_atr_criteria",0.9)))); exit_type=str(params.get("exit_type","HMA")); exit_length=max(1,min(500,int(params.get("exit_length",15)))); atr_length=max(1,min(500,int(params.get("atr_length",14)))); atr_mult=max(0,min(20,float(params.get("atr_multiplier",1)))); risk_lookback=max(50,min(500,int(params.get("risk_lookback",100)))); bull=str(params.get("bullish_color","#00C3FF")); bear=str(params.get("bearish_color","#FF0062"))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _ema(v,length):
    out=np.full(len(v),np.nan); state=np.nan; a=2/(length+1)
    for i,x in enumerate(v):
        if np.isfinite(x): state=float(x) if not np.isfinite(state) else state+a*(float(x)-state); out[i]=state
    return out
def _rma(v,length):
    out=np.full(len(v),np.nan); state=np.nan; seed=[]
    for i,x in enumerate(v):
        if not np.isfinite(x): continue
        if not np.isfinite(state):
            seed.append(float(x))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1)+float(x))/length; out[i]=state
    return out
def _sma(v,length):
    out=np.full(len(v),np.nan)
    for i in range(length-1,len(v)):
        w=v[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.mean(w))
    return out
def _wma(v,length):
    out=np.full(len(v),np.nan); weights=np.arange(1,length+1,dtype=float)
    for i in range(length-1,len(v)):
        w=v[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.dot(w,weights)/np.sum(weights))
    return out
def _ma(v,length,kind):
    if kind=="SMA": return _sma(v,length)
    if kind=="WMA": return _wma(v,length)
    if kind=="HMA": return _wma(2*_wma(v,max(1,length//2))-_wma(v,length),max(1,int(np.sqrt(length))))
    first=_ema(v,length)
    if kind=="DEMA": return 2*first-_ema(first,length)
    if kind=="TEMA":
        second=_ema(first,length); return 3*(first-second)+_ema(second,length)
    if kind=="TMA": return _sma(_sma(v,max(1,(length+1)//2)),max(1,(length+1)//2))
    if kind=="LSMA":
        out=np.full(len(v),np.nan); x=np.arange(length,dtype=float)
        for i in range(length-1,len(v)):
            w=v[i-length+1:i+1]
            if np.all(np.isfinite(w)): out[i]=np.polyfit(x,w,1)[0]*(length-1)+np.polyfit(x,w,1)[1]
        return out
    if kind=="McGinley":
        out=np.full(len(v),np.nan)
        for i,x in enumerate(v): out[i]=x if i==0 or not np.isfinite(out[i-1]) else out[i-1]+(x-out[i-1])/(length*max((x/out[i-1])**4,0.1))
        return out
    if kind=="JMA": return _ema(_ema(v,max(1,length//2)),max(1,length//2))
    return first
def _state(close,high_ma,low_ma):
    out=np.ones(len(close),dtype=int)
    for i in range(len(close)): out[i]=1 if close[i]>high_ma[i] else -1 if close[i]<low_ma[i] else out[i-1] if i else 1
    return out
def _cross_over(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(a[1:]>b[1:])&(a[:-1]<=b[:-1])
    return out
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan; atr=_wma(tr,atr_length); upper_atr=close+atr*atr_mult; lower_atr=close-atr*atr_mult
baseline=_ma(close,baseline_length,baseline_type); range_ma=_ema(tr,baseline_length); upper_channel=baseline+range_ma*channel_mult; lower_channel=baseline-range_ma*channel_mult; primary_high=_ma(high,baseline_length,baseline_type); primary_low=_ma(low,baseline_length,baseline_type); primary_state=_state(close,primary_high,primary_low); ssl1=np.where(primary_state<0,primary_high,primary_low)
continuation_high=_ma(high,ssl2_length,ssl2_type); continuation_low=_ma(low,ssl2_length,ssl2_type); continuation_state=_state(close,continuation_high,continuation_low); ssl2=np.where(continuation_state<0,continuation_high,continuation_low); exit_high=_ma(high,exit_length,exit_type); exit_low=_ma(low,exit_length,exit_type); exit_state=_state(close,exit_high,exit_low); exit_line=np.where(exit_state<0,exit_high,exit_low)
buy_cont=(lower_atr<ssl2)&(close>baseline)&(close>ssl2); sell_cont=(upper_atr>ssl2)&(close<baseline)&(close<ssl2); buy_signal=buy_cont&~np.r_[False,buy_cont[:-1]]; sell_signal=sell_cont&~np.r_[False,sell_cont[:-1]]; exit_long=_cross_over(exit_line,close); exit_short=_cross_over(close,exit_line); violation=(np.abs(close-opened)>atr)&(upper_atr>baseline)&(lower_atr<baseline)
risk_percentile=np.full(n,np.nan)
for i in range(risk_lookback-1,n):
    w=atr[i-risk_lookback+1:i+1]
    if np.all(np.isfinite(w)): risk_percentile[i]=100*np.count_nonzero(w[:-1]<=w[-1])/max(1,len(w)-1)
distance=np.divide(np.abs(close-baseline),atr,out=np.full(n,np.nan),where=atr>0); baseline_bull=_cross_over(close,upper_channel); baseline_bear=_cross_over(lower_channel,close)
show_baseline=display in ("Baseline Only","Baseline + SSL","Full Display"); show_ssl1=display in ("SSL Only","Baseline + SSL","Full Display"); show_ssl2=display in ("SSL Only","Full Display"); show_exit=display in ("Entry/Exit Only","Full Display"); colors=[bull if close[i]>upper_channel[i] else bear if close[i]<lower_channel[i] else "#666666" for i in range(n)]
display_buy=np.zeros(n,bool); display_sell=np.zeros(n,bool); display_violation=np.zeros(n,bool); shown=0; last_index=n+100
if show_signals:
    for i in range(n-1,max(-1,n-161),-1):
        if not (buy_signal[i] or sell_signal[i] or violation[i]) or last_index-i<6: continue
        if buy_signal[i]: display_buy[i]=True
        elif sell_signal[i]: display_sell[i]=True
        else: display_violation[i]=True
        shown+=1; last_index=i
        if shown>=18: break
cloud=[{"upper":float(upper_channel[i]),"lower":float(lower_channel[i]),"color":colors[i],"opacity":0.10} if show_baseline and np.all(np.isfinite([upper_channel[i],lower_channel[i]])) else None for i in range(n)]
tables=[]
if n and show_table:
    risk="High" if np.isfinite(risk_percentile[-1]) and risk_percentile[-1]>75 else "Low" if np.isfinite(risk_percentile[-1]) and risk_percentile[-1]<25 else "Normal"; entry="Near" if np.isfinite(distance[-1]) and distance[-1]<1 else "Extended" if np.isfinite(distance[-1]) and distance[-1]<2 else "Far"
    tables=[{"title":"SSL Hybrid Risk","position":"top-right","compact":True,"rows":[[{"text":"Baseline"},{"text":"Bullish" if close[-1]>baseline[-1] else "Bearish","color":bull if close[-1]>baseline[-1] else bear}],[{"text":"ATR risk"},{"text":risk}],[{"text":"Entry distance"},{"text":entry}],[{"text":"Continuation"},{"text":"BUY" if buy_cont[-1] else "SELL" if sell_cont[-1] else "Neutral"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Baseline Channel","title":False,"data":cloud,"color":bull,"type":"cloud","overlay":True},{"name":"Baseline","title":False,"data":_finite(baseline) if show_baseline else [None]*n,"color":bull,"negativeColor":bear,"type":"line","lineWidth":3,"overlay":True},{"name":"SSL1","title":False,"data":_finite(ssl1) if show_ssl1 else [None]*n,"color":bull,"negativeColor":bear,"type":"line","lineWidth":2,"overlay":True},{"name":"SSL2","title":False,"data":_finite(ssl2) if show_ssl2 else [None]*n,"color":bull,"negativeColor":bear,"type":"scatter","overlay":True},{"name":"Exit","title":False,"data":_finite(exit_line) if show_exit else [None]*n,"color":"#FFFFFF","type":"line","lineWidth":1,"overlay":True}],
"signals":[{"type":"buy","name":"SSL2 BUY","text":"BUY","data":[{"active":True,"price":float(low[i]),"text":"BUY","color":bull} if display_buy[i] else None for i in range(n)],"color":bull,"renderMode":"events","overlay":True},{"type":"sell","name":"SSL2 SELL","text":"SELL","data":[{"active":True,"price":float(high[i]),"text":"SELL","color":bear} if display_sell[i] else None for i in range(n)],"color":bear,"renderMode":"events","overlay":True},{"type":"warning","name":"ATR Violation","text":"ATR","data":[{"active":True,"price":float(high[i]),"text":"ATR","color":"#FFB627"} if display_violation[i] else None for i in range(n)],"color":"#FFB627","renderMode":"events","overlay":True}],"layers":[],"tables":tables,"bar_colors":colors if color_bars else [None]*n,"background_colors":[None]*n,
"calculatedVars":{"atr":_finite(atr),"riskPercentile":_finite(risk_percentile),"baseline":_finite(baseline),"upperChannel":_finite(upper_channel),"lowerChannel":_finite(lower_channel),"ssl1":_finite(ssl1),"ssl2":_finite(ssl2),"exitLine":_finite(exit_line),"distanceFromBaseline":_finite(distance),"buyContinuation":buy_cont.tolist(),"sellContinuation":sell_cont.tolist(),"buySignal":buy_signal.tolist(),"sellSignal":sell_signal.tolist(),"exitLong":exit_long.tolist(),"exitShort":exit_short.tolist(),"baselineBullish":baseline_bull.tolist(),"baselineBearish":baseline_bear.tolist(),"atrViolation":violation.tolist()}}
