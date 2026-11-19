# QuantDinger Community Algo
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Community Algo"
my_indicator_description="Combines volatility, RSI and multi-timeframe trend context with deviation bands, optional rolling support/resistance, EMA overlays and alternating WaveTrend signals."

# @param show_void_lines bool true Show deviation bands
# @param show_dashboard bool true Show dashboard
# @param color_bars bool true Color bars around basis
# @param show_support_resistance bool false Show rolling support and resistance
# @param show_ema_lines bool false Show EMA 8 and EMA 200
# @param show_signals bool true Show WaveTrend signals
# @param show_fibonacci bool false Show latest swing Fibonacci levels

show_void=bool(params.get("show_void_lines",True)); show_dashboard=bool(params.get("show_dashboard",True)); color_bars=bool(params.get("color_bars",True)); show_sr=bool(params.get("show_support_resistance",False)); show_ema=bool(params.get("show_ema_lines",False)); show_signals=bool(params.get("show_signals",True)); show_fib=bool(params.get("show_fibonacci",False))
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
def _sma(values,length): return pd.Series(values).rolling(length,min_periods=length).mean().to_numpy(float)
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000
    return out
def _aggregate(base,seconds):
    temp=base.copy(); temp["_s"]=_seconds(temp["time"].to_numpy(float)); temp["_b"]=np.floor(temp["_s"]/seconds).astype(np.int64); return temp.groupby("_b",sort=True).agg(close=("close","last")).reset_index(drop=True)
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); hlc3=(h+l+c)/3
previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,14); atr_mean=_sma(atr,20); atr_std=pd.Series(atr).rolling(20,min_periods=20).std(ddof=0).to_numpy(float); bottom=atr_mean-2*atr_std; top=atr_mean+2*atr_std; percent_vol=np.divide(atr-bottom,top-bottom,out=np.full(n,np.nan),where=np.isfinite(top-bottom)&((top-bottom)!=0))*40+30
delta=np.diff(c,prepend=np.nan); gain=_rma(np.where(delta>0,delta,0),14); loss=_rma(np.where(delta<0,-delta,0),14); rs=np.divide(gain,loss,out=np.full(n,np.nan),where=loss!=0); rsi=100-100/(1+rs); rsi[(loss==0)&(gain>0)]=100; rsi[(loss==0)&(gain==0)]=50
ema9=_ema(c,9); basis=_sma(c,20); deviation=pd.Series(c).rolling(20,min_periods=20).std(ddof=0).to_numpy(float); upper2=basis+2*deviation; lower2=basis-2*deviation; upper3=basis+3*deviation; lower3=basis-3*deviation; ema8=_ema(c,8); ema200=_ema(c,200)
esa=_ema(hlc3,14); d=_ema(np.abs(hlc3-esa),14); ci=np.divide(hlc3-esa,0.015*d,out=np.full(n,np.nan),where=d!=0); wt1=_ema(ci,21); wt2=_sma(wt1,4); cross_up=np.zeros(n,bool); cross_down=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(wt1[1:])&np.isfinite(wt2[1:])&np.isfinite(wt1[:-1])&np.isfinite(wt2[:-1]); cross_up[1:]=valid&(wt1[1:]>wt2[1:])&(wt1[:-1]<=wt2[:-1]); cross_down[1:]=valid&(wt1[1:]<wt2[1:])&(wt1[:-1]>=wt2[:-1])
buy=np.zeros(n,bool); sell=np.zeros(n,bool); armed=0
for i in range(n):
    if cross_up[i] and armed!=1: buy[i]=True; armed=1
    elif cross_down[i] and armed!=-1: sell[i]=True; armed=-1
plots=[]
if show_void: plots.extend([{"name":"Void Basis","data":_finite(basis),"color":"#9C27B0","type":"line","lineWidth":1,"overlay":True},{"name":"Upper 200%","data":_finite(upper2),"color":"#009688","type":"line","lineWidth":1,"overlay":True},{"name":"Lower 200%","data":_finite(lower2),"color":"#9C27B0","type":"line","lineWidth":1,"overlay":True},{"name":"Upper 300%","data":_finite(upper3),"color":"rgba(0,150,136,0.55)","type":"line","lineWidth":1,"overlay":True},{"name":"Lower 300%","data":_finite(lower3),"color":"rgba(156,39,176,0.55)","type":"line","lineWidth":1,"overlay":True}])
if show_ema: plots.extend([{"name":"EMA 8","data":_finite(ema8),"color":"#FFFFFF","type":"line","lineWidth":2,"overlay":True},{"name":"EMA 200","data":_finite(ema200),"color":"#4CAF50","type":"line","lineWidth":2,"overlay":True}])
sr_values={}
if show_sr:
    for window in (10,50,100,250,500,750,1000):
        high_line=pd.Series(h).rolling(window,min_periods=1).max().to_numpy(float); low_line=pd.Series(l).rolling(window,min_periods=1).min().to_numpy(float); sr_values[str(window)]={"high":_finite(high_line),"low":_finite(low_line)}; plots.extend([{"name":f"R{window}","data":_finite(high_line),"color":"#EF5350","type":"line","lineWidth":1,"overlay":True},{"name":f"S{window}","data":_finite(low_line),"color":"#66BB6A","type":"line","lineWidth":1,"overlay":True}])
layers=[]; fib_pivots=[]
if show_fib:
    depth=5
    for i in range(depth,n-depth):
        if h[i]>=np.max(h[i-depth:i+depth+1]): fib_pivots.append((i,float(h[i]),1))
        if l[i]<=np.min(l[i-depth:i+depth+1]): fib_pivots.append((i,float(l[i]),-1))
    fib_pivots.sort(); alternating=[]
    for p in fib_pivots:
        if not alternating or p[2]!=alternating[-1][2]: alternating.append(p)
        elif p[2]==1 and p[1]>alternating[-1][1] or p[2]==-1 and p[1]<alternating[-1][1]: alternating[-1]=p
    if len(alternating)>=2:
        a,b=alternating[-2:]; diff=b[1]-a[1]
        for ratio,color in ((0,"#787B86"),(0.236,"#F44336"),(0.382,"#81C784"),(0.5,"#4CAF50"),(0.618,"#009688"),(0.786,"#64B5F6"),(1,"#787B86")):
            price=a[1]+diff*ratio; layers.append({"type":"line","role":f"Fib {ratio}","startIndex":a[0],"endIndex":n+30,"y1":price,"y2":price,"color":color,"lineWidth":1})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
tables=[]; mtf={}
if n and show_dashboard:
    for label,seconds in (("1m",60),("5m",300),("15m",900),("30m",1800),("1H",3600),("2H",7200),("4H",14400),("1D",86400),("1W",604800),("1M",2592000)):
        source=_aggregate(frame,seconds)["close"].to_numpy(float); ma=_sma(source,50); mtf[label]="Up" if len(ma)>1 and np.isfinite(ma[-1]) and np.isfinite(ma[-2]) and ma[-1]>ma[-2] else "Down" if len(ma)>1 and np.isfinite(ma[-1]) and np.isfinite(ma[-2]) else "Waiting"
    sentiment="Bullish" if n>2 and ema9[-1]>ema9[-3] else "Bearish" if n>2 and ema9[-1]<ema9[-3] else "Flat"; rows=[[{"text":"Volatility"},{"text":None if not np.isfinite(percent_vol[-1]) else round(float(percent_vol[-1]),2)}],[{"text":"Volume"},{"text":round(float(v[-1]),2)}],[{"text":"RSI"},{"text":None if not np.isfinite(rsi[-1]) else round(float(rsi[-1]),2)}],[{"text":"Sentiment"},{"text":sentiment}]]+[[{"text":key},{"text":value}] for key,value in mtf.items()]; tables=[{"title":"Community Algo Dashboard","position":"top-right","compact":True,"rows":rows}]
bar_colors=["rgba(0,150,136,0.75)" if color_bars and np.isfinite(basis[i]) and c[i]>basis[i] else "rgba(156,39,176,0.75)" if color_bars and np.isfinite(basis[i]) and c[i]<basis[i] else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(buy if show_signals else np.zeros(n,bool),l,"BUY","#4CAF50","buy"),_events(sell if show_signals else np.zeros(n,bool),h,"SELL","#F44336","sell")],"layers":layers,"tables":tables,"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"percentVolatility":_finite(percent_vol),"rsi":_finite(rsi),"ema9":_finite(ema9),"basis":_finite(basis),"upper2":_finite(upper2),"lower2":_finite(lower2),"upper3":_finite(upper3),"lower3":_finite(lower3),"ema8":_finite(ema8),"ema200":_finite(ema200),"waveTrend1":_finite(wt1),"waveTrend2":_finite(wt2),"buySignal":buy.tolist(),"sellSignal":sell.tolist(),"multiTimeframeTrend":mtf,"supportResistance":sr_values}}
