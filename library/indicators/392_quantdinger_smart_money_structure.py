# QuantDinger Smart Money Structure
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name = "QuantDinger Smart Money Structure"
my_indicator_description = "Tracks confirmed pivots, BOS/CHoCH levels, filtered momentum entries, cumulative volume delta and multi-timeframe trend agreement."
indicator_timeframes = ["1m","5m","15m","30m","1H","4H","1D"]

# @param pivot_length int 5 Pivot length range=1:20:1
# @param momentum_threshold float 0.01 Base momentum threshold percent range=0.001:1:0.001
# @param take_profit_points float 10 Take-profit points range=0:500:1
# @param stop_loss_points float 10 Stop-loss points range=0:500:1
# @param minimum_signal_distance int 5 Minimum signal distance range=1:50:1
# @param use_momentum_filter bool true Use momentum filter
# @param use_trend_filter bool true Use higher-timeframe trend filter
# @param higher_timeframe str 5m Higher timeframe values=1m,5m,15m,30m,1H,4H,1D
# @param use_lower_timeframe_filter bool true Use lower-timeframe filter
# @param lower_timeframe str 5m Lower timeframe values=1m,5m,15m,30m,1H,4H,1D
# @param use_volume_filter bool true Use volume filter
# @param use_breakout_filter bool true Use breakout filter
# @param show_get_ready bool false Show get-ready signals
# @param restrict_repeated_signals bool true Restrict repeated signals
# @param volume_long_period int 50 Long volume period range=10:100:1
# @param volume_short_period int 5 Short volume period range=1:20:1
# @param breakout_period int 5 Breakout period range=1:50:1

pivot_length=max(1,min(20,int(params.get("pivot_length",5)))); momentum_base=max(0.001,min(1.0,float(params.get("momentum_threshold",0.01)))); tp=max(0,min(500,float(params.get("take_profit_points",10)))); sl=max(0,min(500,float(params.get("stop_loss_points",10)))); min_distance=max(1,min(50,int(params.get("minimum_signal_distance",5)))); use_momentum=bool(params.get("use_momentum_filter",True)); use_trend=bool(params.get("use_trend_filter",True)); higher_tf=str(params.get("higher_timeframe","5m")); use_lower=bool(params.get("use_lower_timeframe_filter",True)); lower_tf=str(params.get("lower_timeframe","5m")); use_volume=bool(params.get("use_volume_filter",True)); use_breakout=bool(params.get("use_breakout_filter",True)); show_ready=bool(params.get("show_get_ready",False)); restrict_repeated=bool(params.get("restrict_repeated_signals",True)); volume_long=max(10,min(100,int(params.get("volume_long_period",50)))); volume_short=max(1,min(20,int(params.get("volume_short_period",5)))); breakout_length=max(1,min(50,int(params.get("breakout_period",5))))

def _finite(values): return [float(v) if np.isfinite(v) else None for v in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); state=np.nan; a=2.0/(length+1.0)
    for i,v in enumerate(values):
        if np.isfinite(v): state=float(v) if not np.isfinite(state) else state+a*(float(v)-state); out[i]=state
    return out
def _rma(values,length):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,v in enumerate(values):
        if not np.isfinite(v): continue
        if not np.isfinite(state):
            seed.append(float(v))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1.0)+float(v))/length; out[i]=state
    return out
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        w=values[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.mean(w))
    return out
def _normalize(frame):
    out=frame.copy().reset_index(drop=True)
    for c in ("time","open","high","low","close","volume"):
        if c not in out.columns: out[c]=0.0
        out[c]=pd.to_numeric(out[c],errors="coerce")
    t=out["time"].to_numpy(float)
    if len(t) and np.nanmax(np.abs(t))>1e10: out["time"]=t/1000.0
    return out
def _cross_over(a,b,index): return index>0 and np.isfinite(a[index]) and np.isfinite(b[index]) and np.isfinite(a[index-1]) and np.isfinite(b[index-1]) and a[index]>b[index] and a[index-1]<=b[index-1]
def _cross_under(a,b,index): return index>0 and np.isfinite(a[index]) and np.isfinite(b[index]) and np.isfinite(a[index-1]) and np.isfinite(b[index-1]) and a[index]<b[index] and a[index-1]>=b[index-1]
def _trend(frame):
    c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); ema=_ema(c,20); typical=(frame["high"].to_numpy(float)+frame["low"].to_numpy(float)+c)/3; cum=np.cumsum(np.nan_to_num(v)); vwap=np.divide(np.cumsum(np.nan_to_num(typical*v)),cum,out=np.full(len(c),np.nan),where=cum>0); state=np.where((c>ema)&(c>vwap),1,np.where((c<ema)&(c<vwap),-1,0)); return state,ema,vwap
runtime_ctx=globals().get("ctx")
def _align_remote(timeframe):
    raw=None
    if hasattr(runtime_ctx,"raw_series"):
        try: raw=runtime_ctx.raw_series(timeframe)
        except Exception: raw=None
    if raw is None or not len(raw): return local_trend.copy(),False
    remote=_normalize(raw); state,_,_=_trend(remote); rt=remote["time"].to_numpy(float); out=np.zeros(n,dtype=int)
    for i,t in enumerate(time):
        j=int(np.searchsorted(rt,t,side="right")-2)
        if j>=0: out[i]=state[j]
    return out,True

frame=_normalize(df); time=frame["time"].to_numpy(float); opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame)
previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan; atr=_rma(tr,14); volatility=np.divide(atr,close,out=np.zeros(n),where=close!=0); momentum_threshold=momentum_base*(1+volatility*2); pre_threshold=momentum_threshold*(0.5*(1-volatility*0.5)); price_change=np.divide(close-previous,previous,out=np.full(n,np.nan),where=previous!=0)*100
pivot_high=np.full(n,np.nan); pivot_low=np.full(n,np.nan)
for confirmed in range(2*pivot_length,n):
    center=confirmed-pivot_length; w_high=high[center-pivot_length:center+pivot_length+1]; w_low=low[center-pivot_length:center+pivot_length+1]
    if high[center]==np.max(w_high): pivot_high[confirmed]=high[center]
    if low[center]==np.min(w_low): pivot_low[confirmed]=low[center]
last_high=np.full(n,np.nan); last_low=np.full(n,np.nan); lh=ll=np.nan
for i in range(n):
    if np.isfinite(pivot_high[i]): lh=pivot_high[i]
    if np.isfinite(pivot_low[i]): ll=pivot_low[i]
    last_high[i]=lh; last_low[i]=ll
local_trend,local_ema,local_vwap=_trend(frame); higher_trend,higher_available=_align_remote(higher_tf); lower_trend,lower_available=_align_remote(lower_tf)
volume_long_ma=_sma(volume,volume_long); volume_short_ma=_sma(volume,volume_short); volume_condition=(volume>volume_long_ma)&(np.diff(volume_short_ma,prepend=np.nan)>0)
highest=np.full(n,np.nan); lowest=np.full(n,np.nan)
for i in range(breakout_length-1,n): highest[i]=np.max(high[i-breakout_length+1:i+1]); lowest[i]=np.min(low[i-breakout_length+1:i+1])
choch_sell=np.zeros(n,bool); choch_buy=np.zeros(n,bool); bos_sell=np.zeros(n,bool); bos_buy=np.zeros(n,bool); buy=np.zeros(n,bool); sell=np.zeros(n,bool); ready_buy=np.zeros(n,bool); ready_sell=np.zeros(n,bool); last_signal=0; last_bar=-min_distance-1; levels=[]
for i in range(1,n):
    choch_sell[i]=_cross_under(low,last_high,i) and close[i]<opened[i]; choch_buy[i]=_cross_over(high,last_low,i) and close[i]>opened[i]
    if i>1: bos_sell[i]=_cross_under(low,np.r_[np.nan,last_low[:-1]],i) and close[i]<opened[i]; bos_buy[i]=_cross_over(high,np.r_[np.nan,last_high[:-1]],i) and close[i]>opened[i]
    momentum_buy=(not use_momentum) or price_change[i]>momentum_threshold[i]; momentum_sell=(not use_momentum) or price_change[i]<-momentum_threshold[i]; trend_buy=(not use_trend) or higher_trend[i]==1; trend_sell=(not use_trend) or higher_trend[i]==-1; lower_buy=(not use_lower) or lower_trend[i]==1; lower_sell=(not use_lower) or lower_trend[i]==-1; vol_ok=(not use_volume) or volume_condition[i]; breakout_buy=(not use_breakout) or (np.isfinite(highest[i-1]) and close[i]>highest[i-1]); breakout_sell=(not use_breakout) or (np.isfinite(lowest[i-1]) and close[i]<lowest[i-1]); spaced=i-last_bar>=min_distance
    allowed_buy=(not restrict_repeated) or last_signal!=1; allowed_sell=(not restrict_repeated) or last_signal!=-1
    buy[i]=momentum_buy and trend_buy and lower_buy and vol_ok and breakout_buy and spaced and allowed_buy; sell[i]=momentum_sell and trend_sell and lower_sell and vol_ok and breakout_sell and spaced and allowed_sell
    ready_buy[i]=show_ready and price_change[i]>pre_threshold[i] and price_change[i]<momentum_threshold[i] and trend_buy and lower_buy and vol_ok and breakout_buy and spaced; ready_sell[i]=show_ready and price_change[i]<-pre_threshold[i] and price_change[i]>-momentum_threshold[i] and trend_sell and lower_sell and vol_ok and breakout_sell and spaced
    if buy[i]: last_signal=1; last_bar=i; levels.append((i,1,float(high[i]),float(high[i]+tp),float(low[i]-sl)))
    elif sell[i]: last_signal=-1; last_bar=i; levels.append((i,-1,float(low[i]),float(low[i]-tp),float(high[i]+sl)))
delta=np.where(close>previous,volume,np.where(close<previous,-volume,0)); cvd=np.cumsum(np.nan_to_num(delta)); trend_strength=higher_trend+lower_trend+local_trend
layers=[]
for i in np.flatnonzero(choch_buy|choch_sell|bos_buy|bos_sell)[-60:]:
    direction=1 if choch_buy[i] or bos_buy[i] else -1; value=last_low[i] if choch_buy[i] or bos_sell[i] else last_high[i]; role="CHoCH" if choch_buy[i] or choch_sell[i] else "BOS"
    if np.isfinite(value): layers.append({"type":"line","role":role,"startIndex":int(max(0,i-pivot_length)),"endIndex":int(i+5),"y1":float(value),"y2":float(value),"color":"#00D5FF" if direction==1 else "#FF477E","lineWidth":2 if role=="CHoCH" else 1})
for index,direction,entry,target,stop in levels[-20:]:
    color="#00D5FF" if direction==1 else "#FF477E"; end=min(n+2,index+20); layers.extend([{"type":"line","role":"Entry","startIndex":index,"endIndex":end,"y1":entry,"y2":entry,"color":color,"lineWidth":1},{"type":"line","role":"Take Profit","startIndex":index,"endIndex":end,"y1":target,"y2":target,"color":"#00C853","lineWidth":1,"lineStyle":"dashed"},{"type":"line","role":"Stop Loss","startIndex":index,"endIndex":end,"y1":stop,"y2":stop,"color":"#FF1744","lineWidth":1,"lineStyle":"dashed"}])
tables=[]
if n: tables=[{"title":"Smart Money Structure","position":"top-right","compact":True,"rows":[[{"text":"Trend agreement"},{"text":f"{trend_strength[-1]:+d} / 3","color":"#00D5FF" if trend_strength[-1]>0 else "#FF477E" if trend_strength[-1]<0 else "#808080"}],[{"text":"CVD"},{"text":f"{cvd[-1]:.0f}"}],[{"text":"Higher TF"},{"text":"Ready" if higher_available else "Chart fallback"}],[{"text":"Lower TF"},{"text":"Ready" if lower_available else "Chart fallback"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"CHoCH Sell","title":False,"data":_finite(np.where(choch_sell,last_high,np.nan)),"color":"#00E5FF","type":"scatter","overlay":True},{"name":"CHoCH Buy","title":False,"data":_finite(np.where(choch_buy,last_low,np.nan)),"color":"#76FF03","type":"scatter","overlay":True},{"name":"BOS Sell","title":False,"data":_finite(np.where(bos_sell,last_low,np.nan)),"color":"#E040FB","type":"scatter","overlay":True},{"name":"BOS Buy","title":False,"data":_finite(np.where(bos_buy,last_high,np.nan)),"color":"#00BFA5","type":"scatter","overlay":True}],
    "signals":[{"type":"buy","name":"Buy","text":"BUY","data":[{"active":True,"price":float(low[i]),"text":"BUY","color":"#00E676"} if buy[i] else None for i in range(n)],"color":"#00E676","renderMode":"events","overlay":True},{"type":"sell","name":"Sell","text":"SELL","data":[{"active":True,"price":float(high[i]),"text":"SELL","color":"#FF1744"} if sell[i] else None for i in range(n)],"color":"#FF1744","renderMode":"events","overlay":True},{"type":"buy","name":"Ready Buy","text":"READY","data":[{"active":True,"price":float(low[i]),"text":"READY","color":"#FFB627"} if ready_buy[i] else None for i in range(n)],"color":"#FFB627","renderMode":"events","overlay":True},{"type":"sell","name":"Ready Sell","text":"READY","data":[{"active":True,"price":float(high[i]),"text":"READY","color":"#FFB627"} if ready_sell[i] else None for i in range(n)],"color":"#FFB627","renderMode":"events","overlay":True}],"layers":layers,"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,
    "calculatedVars":{"atr":_finite(atr),"momentumThreshold":_finite(momentum_threshold),"priceChange":_finite(price_change),"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"lastHigh":_finite(last_high),"lastLow":_finite(last_low),"localTrend":local_trend.tolist(),"higherTrend":higher_trend.tolist(),"lowerTrend":lower_trend.tolist(),"higherAvailable":bool(higher_available),"lowerAvailable":bool(lower_available),"trendStrength":trend_strength.tolist(),"volumeCondition":volume_condition.tolist(),"chochBuy":choch_buy.tolist(),"chochSell":choch_sell.tolist(),"bosBuy":bos_buy.tolist(),"bosSell":bos_sell.tolist(),"buy":buy.tolist(),"sell":sell.tolist(),"readyBuy":ready_buy.tolist(),"readySell":ready_sell.tolist(),"cvd":_finite(cvd)}}
