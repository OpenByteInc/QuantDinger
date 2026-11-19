# QuantDinger Volatility Adaptive Filtered Trend
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Volatility Adaptive Filtered Trend"
my_indicator_description="Adapts smoothing to ATR and directional-change activity, applies a displacement-weighted trend filter and changes regime only beyond return-volatility bands."

# @param volatility_length int 15 Adaptive volatility length range=2:500:1
# @param normalization_length int 30 Normalization length range=5:1000:1
# @param minimum_smoothing float 0.084 Minimum smoothing range=0.001:1:0.001
# @param maximum_smoothing float 0.33 Maximum smoothing range=0.001:1:0.001
# @param ema_length int 5 EMA smoothing range=1:500:1
# @param filter_length int 9 Filter length range=2:100:1
# @param momentum_length int 2 Momentum length range=1:100:1
# @param band_volatility_length int 22 Band volatility length range=2:500:1
# @param upper_multiplier float 2 Upper multiplier range=0.1:20:0.1
# @param lower_multiplier float 2 Lower multiplier range=0.1:20:0.1
# @param color_bars bool true Color bars

vol_length=max(2,min(500,int(params.get("volatility_length",15)))); normal_length=max(5,min(1000,int(params.get("normalization_length",30)))); min_sc=max(0.001,min(1,float(params.get("minimum_smoothing",0.084)))); max_sc=max(0.001,min(1,float(params.get("maximum_smoothing",0.33)))); ema_length=max(1,min(500,int(params.get("ema_length",5)))); filter_length=max(2,min(100,int(params.get("filter_length",9)))); momentum=max(1,min(100,int(params.get("momentum_length",2)))); band_length=max(2,min(500,int(params.get("band_volatility_length",22)))); upper_mult=max(0.1,min(20,float(params.get("upper_multiplier",2)))); lower_mult=max(0.1,min(20,float(params.get("lower_multiplier",2)))); color_bars=bool(params.get("color_bars",True))
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
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); src=(h+l+c)/3; n=len(frame); previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,vol_length); net=np.full(n,np.nan)
if n>vol_length: net[vol_length:]=np.abs(src[vol_length:]-src[:-vol_length])
atr_base=pd.Series(atr).rolling(normal_length,min_periods=normal_length).mean().to_numpy(float); change_base=pd.Series(net).rolling(normal_length,min_periods=normal_length).mean().to_numpy(float); atr_factor=np.divide(atr,atr_base,out=np.ones(n),where=np.isfinite(atr_base)&(atr_base!=0)); change_factor=np.divide(net,change_base,out=np.ones(n),where=np.isfinite(change_base)&(change_base!=0)); normalized=np.clip((atr_factor+change_factor)/4,0,1); adaptive_sc=min_sc+(max_sc-min_sc)*normalized; adaptive=np.full(n,np.nan); state=np.nan
for i in range(n):
    if not np.isfinite(src[i]): continue
    state=float(src[i]) if not np.isfinite(state) else state+adaptive_sc[i]*(src[i]-state); adaptive[i]=state
ema_trend=_ema(adaptive,ema_length); filtered=np.full(n,np.nan); fallback=pd.Series(ema_trend).rolling(filter_length,min_periods=filter_length).mean().to_numpy(float)
for i in range(n):
    numerator=0.0; coefficient_sum=0.0
    for j in range(filter_length):
        a=i-j; b=a-momentum
        if b>=0 and np.isfinite(ema_trend[a]) and np.isfinite(ema_trend[b]): coefficient=abs(ema_trend[a]-ema_trend[b]); numerator+=coefficient*ema_trend[a]; coefficient_sum+=coefficient
    if coefficient_sum>0: filtered[i]=numerator/coefficient_sum
    elif np.isfinite(fallback[i]): filtered[i]=fallback[i]
returns=np.zeros(n); returns[1:]=np.divide(src[1:]-src[:-1],src[:-1],out=np.zeros(n-1),where=src[:-1]!=0); volatility=np.r_[np.nan,pd.Series(returns).rolling(band_length,min_periods=band_length).std(ddof=0).to_numpy(float)[:-1]]; upper=filtered+upper_mult*volatility*filtered; lower=filtered-lower_mult*volatility*filtered; regime=np.zeros(n,dtype=int); current=0; long_signal=np.zeros(n,bool); short_signal=np.zeros(n,bool)
for i in range(n):
    previous_regime=current
    if np.isfinite(upper[i]) and c[i]>upper[i]: current=1
    elif np.isfinite(lower[i]) and c[i]<lower[i]: current=-1
    regime[i]=current; long_signal[i]=current==1 and previous_regime!=1; short_signal[i]=current==-1 and previous_regime!=-1
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
bar_colors=["rgba(57,255,20,0.75)" if color_bars and regime[i]==1 else "rgba(138,43,226,0.75)" if color_bars and regime[i]==-1 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Filtered Adaptive Trend","title":True,"data":_finite(filtered),"color":"#39FF14","negativeColor":"#8A2BE2","type":"line","lineWidth":2,"overlay":True},{"name":"Upper Volatility Band","data":_finite(upper),"color":"rgba(57,255,20,0.65)","type":"line","lineWidth":1,"overlay":True},{"name":"Lower Volatility Band","data":_finite(lower),"color":"rgba(138,43,226,0.65)","type":"line","lineWidth":1,"overlay":True}],"signals":[_events(long_signal,l,"LONG","#39FF14","buy"),_events(short_signal,h,"SHORT","#8A2BE2","sell")],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"netChange":_finite(net),"normalizedFactor":_finite(normalized),"adaptiveSmoothing":_finite(adaptive_sc),"adaptiveTrend":_finite(adaptive),"emaTrend":_finite(ema_trend),"filteredTrend":_finite(filtered),"returnVolatility":_finite(volatility),"upperBand":_finite(upper),"lowerBand":_finite(lower),"regime":regime.tolist(),"longSignal":long_signal.tolist(),"shortSignal":short_signal.tolist()}}
