# QuantDinger Triangular Hull Moving Average Volatility
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Triangular Hull Moving Average Volatility"
my_indicator_description="Applies a triangular Hull-style weighted average to price, wraps it in Hull-smoothed range volatility and flags two-bar direction reversals."

# @param length int 40 THMA length range=3:500:1
# @param volatility_length int 15 Volatility Hull length range=2:500:1
# @param show_volatility bool true Show volatility envelope

length=max(3,min(500,int(params.get("length",40)))); volatility_length=max(2,min(500,int(params.get("volatility_length",15)))); show_volatility=bool(params.get("show_volatility",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _wma(values,window):
    window=max(1,int(window)); out=np.full(len(values),np.nan); weights=np.arange(1,window+1,dtype=float); divisor=float(weights.sum())
    for i in range(window-1,len(values)):
        sample=values[i-window+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights)/divisor)
    return out
def _hma(values,window): return _wma(2*_wma(values,max(1,window//2))-_wma(values,window),max(1,int(np.sqrt(window))))
def _rma(values,window):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==window: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(window-1)+float(value))/window; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame)
volatility=_hma(h-l,volatility_length); first=_wma(c,max(1,length//3)); second=_wma(c,max(1,length//2)); third=_wma(c,length); thma=_wma(first*3-second-third,length); delayed=np.r_[np.full(2,np.nan),thma[:-2]] if n>=2 else np.full(n,np.nan)
upper=thma+volatility; lower=delayed-volatility; signal_up=np.zeros(n,bool); signal_down=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(thma[1:])&np.isfinite(delayed[1:])&np.isfinite(thma[:-1])&np.isfinite(delayed[:-1]); signal_up[1:]=valid&(thma[1:]>delayed[1:])&(thma[:-1]<=delayed[:-1]); signal_down[1:]=valid&(thma[1:]<delayed[1:])&(thma[:-1]>=delayed[:-1])
previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,200); trend=np.where(thma>delayed,1,np.where(thma<delayed,-1,0)); rolling_max=pd.Series(volatility).rolling(1000,min_periods=1).max().to_numpy(float); normalized=np.divide(volatility,rolling_max,out=np.full(n,np.nan),where=np.isfinite(rolling_max)&(rolling_max!=0))
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
plots=[{"name":"THMA","title":True,"data":_finite(thma),"color":"#16E5A0","negativeColor":"#741DDD","type":"line","lineWidth":2,"overlay":True},{"name":"THMA Delayed","data":_finite(delayed),"color":"rgba(255,255,255,0.35)","type":"line","lineWidth":1,"overlay":True}]
if show_volatility: plots.extend([{"name":"Volatility High","data":_finite(upper),"color":"rgba(22,229,160,0.35)","negativeColor":"rgba(116,29,221,0.35)","type":"line","lineWidth":1,"overlay":True},{"name":"Volatility Low","data":_finite(lower),"color":"rgba(22,229,160,0.35)","negativeColor":"rgba(116,29,221,0.35)","type":"line","lineWidth":1,"overlay":True}])
tables=[]
if n: tables=[{"title":"THMA + Volatility","position":"bottom-right","compact":True,"rows":[[{"text":"Trend"},{"text":"Up" if trend[-1]>0 else "Down" if trend[-1]<0 else "Waiting"}],[{"text":"Normalized volatility"},{"text":None if not np.isfinite(normalized[-1]) else round(float(normalized[-1])*100,2)}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(signal_up,delayed-atr,"UP","#16E5A0","buy"),_events(signal_down,delayed+atr,"DOWN","#741DDD","sell")],"layers":[],"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"thma":_finite(thma),"delayedTHMA":_finite(delayed),"volatility":_finite(volatility),"normalizedVolatility":_finite(normalized),"upper":_finite(upper),"lower":_finite(lower),"atr200":_finite(atr),"trend":trend.tolist(),"signalUp":signal_up.tolist(),"signalDown":signal_down.tolist()}}
