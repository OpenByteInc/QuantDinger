# QuantDinger SPMA Trend
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name = "QuantDinger SPMA Trend"
my_indicator_description = "A shock-percentile gated moving average with symmetric, asymmetric or empirical residual-volatility bands."

# @param baseline_length int 30 Baseline length range=1:500:1
# @param percentrank_lookback int 30 Percentrank lookback range=2:500:1
# @param percentile_gate int 50 Percentile gate range=0:99:1
# @param asymmetric_bands bool false Use asymmetric bands
# @param volatility_method str MeanAD Volatility method values=SD,ATR,MeanAD,MedianAD,Quantile
# @param volatility_length int 14 Volatility length range=2:500:1
# @param volatility_multiple float 0.75 Volatility multiple range=0:20:0.05
# @param quantile_percentile float 90 Quantile percentile range=50:99:1
# @param use_slope_gate bool true Use slope gate
# @param slope_lookback int 10 Slope lookback range=1:500:1
# @param slope_gate float 0.5 Slope gate percent range=-20:20:0.1

baseline_length=max(1,min(500,int(params.get("baseline_length",30)))); lookback=max(2,min(500,int(params.get("percentrank_lookback",30)))); gate=max(0,min(99,int(params.get("percentile_gate",50))))
asymmetric=bool(params.get("asymmetric_bands",False)); method=str(params.get("volatility_method","MeanAD")); method=method if method in ("SD","ATR","MeanAD","MedianAD","Quantile") else "MeanAD"
vol_length=max(2,min(500,int(params.get("volatility_length",14)))); vol_multiple=max(0.0,min(20.0,float(params.get("volatility_multiple",0.75)))); quantile=max(50.0,min(99.0,float(params.get("quantile_percentile",90.0))))
use_slope=bool(params.get("use_slope_gate",True)); slope_length=max(1,min(500,int(params.get("slope_lookback",10)))); slope_gate=max(-20.0,min(20.0,float(params.get("slope_gate",0.5))))

def _finite(values): return [float(v) if np.isfinite(v) else None for v in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); state=np.nan; alpha=2.0/(length+1.0)
    for i,v in enumerate(values):
        if np.isfinite(v): state=float(v) if not np.isfinite(state) else state+alpha*(float(v)-state); out[i]=state
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
def _window(values,length,kind):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        w=values[i-length+1:i+1]
        if not np.all(np.isfinite(w)): continue
        if kind=="std": out[i]=float(np.std(w,ddof=0))
        elif kind=="meanad": out[i]=float(np.mean(np.abs(w-np.mean(w))))
        elif kind=="medianad": out[i]=float(np.median(np.abs(w-np.median(w))))
    return out
def _percentile(values,length,percent,positive):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        w=values[i-length+1:i+1]; w=w[np.isfinite(w) & ((w>=0) if positive else (w<0))]
        if len(w): out[i]=float(np.percentile(np.abs(w),percent))
    return out

frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame)
returns=np.diff(close,prepend=np.nan); rank=np.full(n,np.nan)
for i in range(lookback-1,n):
    w=returns[i-lookback+1:i+1]
    if np.all(np.isfinite(w)): rank[i]=100.0*np.count_nonzero(w[:-1]<=w[-1])/max(1,len(w)-1)
ema_base=_ema(close,baseline_length); spma=np.full(n,np.nan); update=np.zeros(n,bool)
for i in range(n):
    if i==0 or not np.isfinite(spma[i-1]): spma[i]=ema_base[i]
    elif np.isfinite(rank[i]) and rank[i]>gate: spma[i]=ema_base[i]; update[i]=True
    else: spma[i]=spma[i-1]
previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan
raw={"SD":_window(close,vol_length,"std"),"ATR":_rma(tr,vol_length),"MeanAD":_window(close,vol_length,"meanad"),"MedianAD":_window(close,vol_length,"medianad")}.get(method,np.full(n,np.nan))
volatility=_ema(raw,vol_length); residual=close-spma; quantile_upper=_percentile(residual,vol_length,quantile,True); quantile_lower=_percentile(residual,vol_length,quantile,False)
log_return=np.full(n,0.0); valid=(close>0)&(np.r_[np.nan,close[:-1]]>0); log_return[valid]=np.log(close[valid]/np.r_[np.nan,close[:-1]][valid])
bull_var=_ema(np.maximum(log_return,0.0)**2,vol_length); bear_var=_ema(np.maximum(-log_return,0.0)**2,vol_length); total_var=_ema(log_return**2,vol_length)
bull_ratio=np.sqrt(np.maximum(np.divide(2*bull_var,total_var,out=np.ones(n),where=total_var>0),0)); bear_ratio=np.sqrt(np.maximum(np.divide(2*bear_var,total_var,out=np.ones(n),where=total_var>0),0))
upper_mult=1+3*(bull_ratio-1); lower_mult=1+3*(bear_ratio-1); average_mult=np.full(n,np.nan)
for i in range(n):
    w=(upper_mult+lower_mult)[max(0,i-179):i+1]/2.0; average_mult[i]=float(np.nanmean(w)) if len(w) else 1.0
upper_adj=np.clip(upper_mult,average_mult-1,average_mult+1); lower_adj=np.clip(lower_mult,average_mult-1,average_mult+1)
asym_upper=np.maximum(volatility*upper_adj,1e-12); asym_lower=np.maximum(volatility*lower_adj,1e-12)
final_upper=quantile_upper if method=="Quantile" else asym_upper if asymmetric else volatility; final_lower=quantile_lower if method=="Quantile" else asym_lower if asymmetric else volatility
upper_band=spma+final_upper*vol_multiple; lower_band=spma-final_lower*vol_multiple
slope=np.zeros(n)
for i in range(slope_length,n):
    if np.isfinite(spma[i-slope_length]) and spma[i-slope_length]!=0: slope[i]=(spma[i]-spma[i-slope_length])/spma[i-slope_length]*100.0
state=np.zeros(n,dtype=int)
for i in range(1,n):
    state[i]=state[i-1]
    if spma[i]>spma[i-1] and close[i]>upper_band[i] and ((not use_slope) or slope[i]>slope_gate): state[i]=1
    if close[i]<lower_band[i]: state[i]=-1
up_color="#00D0A0"; down_color="#FF4D6D"; neutral_color="#808080"
output={"schema_version":2,"name":my_indicator_name,"plots":[
    {"name":"SPMA","title":True,"data":_finite(spma),"color":"#B0B0B0","type":"line","lineWidth":2,"overlay":True},
    {"name":"Upper Band","title":True,"data":_finite(upper_band),"color":up_color,"type":"line","lineWidth":2,"overlay":True},
    {"name":"Lower Band","title":True,"data":_finite(lower_band),"color":down_color,"type":"line","lineWidth":2,"overlay":True}],"signals":[],"layers":[],"tables":[],
    "bar_colors":[up_color if s==1 else down_color if s==-1 else neutral_color for s in state],"background_colors":[None]*n,
    "calculatedVars":{"return":_finite(returns),"percentRank":_finite(rank),"update":update.tolist(),"ema":_finite(ema_base),"spma":_finite(spma),"volatilityRaw":_finite(raw),"volatility":_finite(volatility),"quantileUpper":_finite(quantile_upper),"quantileLower":_finite(quantile_lower),"asymmetricUpper":_finite(asym_upper),"asymmetricLower":_finite(asym_lower),"upperBand":_finite(upper_band),"lowerBand":_finite(lower_band),"slopePercent":_finite(slope),"state":state.tolist()}}
