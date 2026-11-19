# QuantDinger Ultimate RSI
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Ultimate RSI"
my_indicator_description="Augments RSI by substituting full lookback range when price creates a new extreme, then smooths directional and absolute movement with a selectable average."

# @param length int 14 RSI length range=2:500:1
# @param smoothing_method str RMA RSI smoothing values=EMA,SMA,RMA,TMA
# @param signal_length int 14 Signal length range=1:500:1
# @param signal_method str EMA Signal smoothing values=EMA,SMA,RMA,TMA
# @param overbought float 80 Overbought range=50:100:1
# @param oversold float 20 Oversold range=0:50:1

length=max(2,min(500,int(params.get("length",14)))); method=str(params.get("smoothing_method","RMA")).upper(); signal_length=max(1,min(500,int(params.get("signal_length",14)))); signal_method=str(params.get("signal_method","EMA")).upper(); overbought=max(50,min(100,float(params.get("overbought",80)))); oversold=max(0,min(50,float(params.get("oversold",20))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,window):
    out=np.full(len(values),np.nan); alpha=2/(window+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
def _rma(values,window):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==window: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(window-1)+float(value))/window; out[i]=state
    return out
def _ma(values,window,kind):
    if kind=="EMA": return _ema(values,window)
    if kind=="RMA": return _rma(values,window)
    first=pd.Series(values).rolling(window,min_periods=window).mean().to_numpy(float)
    return pd.Series(first).rolling(window,min_periods=window).mean().to_numpy(float) if kind=="TMA" else first
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
c=frame["close"].to_numpy(float); n=len(frame); upper=pd.Series(c).rolling(length,min_periods=length).max().to_numpy(float); lower=pd.Series(c).rolling(length,min_periods=length).min().to_numpy(float); price_range=upper-lower; delta=np.diff(c,prepend=np.nan); diff=delta.copy()
for i in range(1,n):
    if np.isfinite(upper[i]) and np.isfinite(upper[i-1]) and upper[i]>upper[i-1]: diff[i]=price_range[i]
    elif np.isfinite(lower[i]) and np.isfinite(lower[i-1]) and lower[i]<lower[i-1]: diff[i]=-price_range[i]
numerator=_ma(diff,length,method); denominator=_ma(np.abs(diff),length,method); ultimate=np.divide(numerator,denominator,out=np.full(n,np.nan),where=denominator!=0)*50+50; signal=_ma(ultimate,signal_length,signal_method); cross_up=np.zeros(n,bool); cross_down=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(ultimate[1:])&np.isfinite(signal[1:])&np.isfinite(ultimate[:-1])&np.isfinite(signal[:-1]); cross_up[1:]=valid&(ultimate[1:]>signal[1:])&(ultimate[:-1]<=signal[:-1]); cross_down[1:]=valid&(ultimate[1:]<signal[1:])&(ultimate[:-1]>=signal[:-1])
background=["rgba(8,153,129,0.20)" if np.isfinite(ultimate[i]) and ultimate[i]>overbought else "rgba(242,54,69,0.20)" if np.isfinite(ultimate[i]) and ultimate[i]<oversold else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Ultimate RSI","title":True,"data":_finite(ultimate),"color":"#BDBDBD","positiveColor":"#089981","negativeColor":"#F23645","type":"line","lineWidth":2,"overlay":False},{"name":"Signal Line","data":_finite(signal),"color":"#FF5D00","type":"line","lineWidth":1,"overlay":False},{"name":"Overbought","data":[overbought]*n,"color":"rgba(8,153,129,0.55)","type":"line","lineWidth":1,"overlay":False},{"name":"Midline","data":[50.0]*n,"color":"rgba(158,158,158,0.45)","type":"line","lineWidth":1,"overlay":False},{"name":"Oversold","data":[oversold]*n,"color":"rgba(242,54,69,0.55)","type":"line","lineWidth":1,"overlay":False}],"signals":[],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":background,"calculatedVars":{"upper":_finite(upper),"lower":_finite(lower),"range":_finite(price_range),"difference":_finite(diff),"numerator":_finite(numerator),"denominator":_finite(denominator),"ultimateRSI":_finite(ultimate),"signal":_finite(signal),"crossUp":cross_up.tolist(),"crossDown":cross_down.tolist()}}
