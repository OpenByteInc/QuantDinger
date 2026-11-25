# QuantDinger SuperTrend MTF Heikin Ashi
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger SuperTrend MTF Heikin Ashi"
my_indicator_description="Calculates Heikin-Ashi SuperTrend on the chart timeframe and on the previous completed higher-timeframe candle, aligned without look-ahead."

# @param htf_method str Auto Higher-timeframe method values=Auto,User Defined
# @param higher_timeframe str 5 Higher timeframe values=5m,15m,30m,1H,2H,4H,1D,1W,5W
# @param atr_factor float 2 ATR factor range=0.5:100:0.1
# @param atr_period int 7 ATR period range=1:100:1

method=str(params.get("htf_method","Auto")); requested=str(params.get("higher_timeframe","5m")); factor=max(0.5,min(100,float(params.get("atr_factor",2)))); period=max(1,min(100,int(params.get("atr_period",7))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000.0
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
def _heikin(frame_value):
    opened=frame_value["open"].to_numpy(float); high=frame_value["high"].to_numpy(float); low=frame_value["low"].to_numpy(float); close=frame_value["close"].to_numpy(float); count=len(frame_value); ha_close=(opened+high+low+close)/4.0; ha_open=np.full(count,np.nan)
    for i in range(count): ha_open[i]=(opened[i]+close[i])/2.0 if i==0 else (ha_open[i-1]+ha_close[i-1])/2.0
    return ha_open,np.maximum(high,np.maximum(ha_open,ha_close)),np.minimum(low,np.minimum(ha_open,ha_close)),ha_close
def _ha_atr(ha_high,ha_low,ha_close,length):
    previous=np.r_[np.nan,ha_close[:-1]]; tr=np.maximum(ha_high-ha_low,np.maximum(np.abs(ha_high-previous),np.abs(ha_low-previous)))
    if len(tr): tr[0]=ha_high[0]-ha_low[0]
    return _rma(tr,length)
def _trend_line(ha_high,ha_low,ha_close,atr):
    count=len(ha_close); raw_up=(ha_high+ha_low)/2.0-factor*atr; raw_down=(ha_high+ha_low)/2.0+factor*atr; trailing_up=np.full(count,np.nan); trailing_down=np.full(count,np.nan); trend=np.zeros(count,dtype=int); line=np.full(count,np.nan)
    for i in range(count):
        if not np.isfinite(raw_up[i]) or not np.isfinite(raw_down[i]): continue
        if i==0 or not np.isfinite(trailing_up[i-1]): trailing_up[i]=raw_up[i]; trailing_down[i]=raw_down[i]; trend[i]=1
        else:
            trailing_up[i]=max(raw_up[i],trailing_up[i-1]) if ha_close[i-1]>trailing_up[i-1] else raw_up[i]; trailing_down[i]=min(raw_down[i],trailing_down[i-1]) if ha_close[i-1]<trailing_down[i-1] else raw_down[i]; trend[i]=1 if ha_close[i]>trailing_down[i-1] else -1 if ha_close[i]<trailing_up[i-1] else trend[i-1] if trend[i-1] else 1
        line[i]=trailing_up[i] if trend[i]==1 else trailing_down[i]
    return line,trend,trailing_up,trailing_down
def _canonical(value):
    key=str(value).strip().upper(); aliases={"1":"1m","3":"3m","5":"5m","15":"15m","30":"30m","45":"45m","60":"1H","120":"2H","180":"3H","240":"4H","D":"1D","W":"1W","5W":"5W","1M":"1m","3M":"3m","5M":"5m","15M":"15m","30M":"30m","45M":"45m","1H":"1H","2H":"2H","3H":"3H","4H":"4H","1D":"1D"}; return aliases.get(key,str(value))
def _tf_seconds(value):
    key=_canonical(value); mapping={"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,"45m":2700,"1H":3600,"2H":7200,"3H":10800,"4H":14400,"1D":86400,"1W":604800,"5W":3024000}; return mapping.get(key,300)
def _aggregate(base,value):
    seconds=_tf_seconds(value); temp=base.copy(); temp["_seconds"]=_seconds(temp["time"].to_numpy(float)); temp["_bucket"]=np.floor(temp["_seconds"]/seconds).astype(np.int64); grouped=temp.groupby("_bucket",sort=True)
    result=grouped.agg(time=("_seconds","min"),open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum")).reset_index(drop=True); return result
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
n=len(frame); chart_key=_canonical(ctx.timeframe or "1H"); auto_map={"1m":"5m","3m":"15m","5m":"15m","15m":"1H","30m":"2H","45m":"2H","1H":"4H","2H":"4H","3H":"4H","4H":"1D","1D":"1W","1W":"5W"}; htf=auto_map.get(chart_key,"1D") if method=="Auto" else _canonical(requested)
ha_open,ha_high,ha_low,ha_close=_heikin(frame); atr=_ha_atr(ha_high,ha_low,ha_close,period); current_line,current_trend,current_up,current_down=_trend_line(ha_high,ha_low,ha_close,atr)
source=None; context=ctx
if context is not None and hasattr(context,"raw_series"):
    for key in (htf,str(htf).replace("m","")):
        try:
            candidate=context.raw_series(key)
            if candidate is not None and len(candidate): source=candidate.copy().reset_index(drop=True); break
        except Exception: pass
if source is None: source=_aggregate(frame,htf)
for column in ("time","open","high","low","close","volume"):
    if column not in source.columns: source[column]=0.0
    source[column]=pd.to_numeric(source[column],errors="coerce")
source=source.sort_values("time").reset_index(drop=True); source_ha_open,source_ha_high,source_ha_low,source_ha_close=_heikin(source); source_atr=_ha_atr(source_ha_high,source_ha_low,source_ha_close,period); base_time=_seconds(frame["time"].to_numpy(float)); source_time=_seconds(source["time"].to_numpy(float)); previous_index=np.searchsorted(source_time,base_time,side="right")-2; valid=(previous_index>=0)&(previous_index<len(source)); htf_high=np.full(n,np.nan); htf_low=np.full(n,np.nan); htf_close=np.full(n,np.nan); htf_atr=np.full(n,np.nan); htf_source_index=np.full(n,-1,dtype=int)
if len(source):
    clipped=np.clip(previous_index,0,len(source)-1); htf_high[valid]=source_ha_high[clipped[valid]]; htf_low[valid]=source_ha_low[clipped[valid]]; htf_close[valid]=source_ha_close[clipped[valid]]; htf_atr[valid]=source_atr[clipped[valid]]; htf_source_index[valid]=previous_index[valid]
htf_line,htf_trend,htf_up,htf_down=_trend_line(htf_high,htf_low,htf_close,htf_atr); current_up_signal=np.zeros(n,bool); current_down_signal=np.zeros(n,bool); htf_up_signal=np.zeros(n,bool); htf_down_signal=np.zeros(n,bool)
if n>1:
    current_up_signal[1:]=(current_trend[1:]==1)&(current_trend[:-1]==-1); current_down_signal[1:]=(current_trend[1:]==-1)&(current_trend[:-1]==1); htf_up_signal[1:]=(htf_trend[1:]==1)&(htf_trend[:-1]==-1); htf_down_signal[1:]=(htf_trend[1:]==-1)&(htf_trend[:-1]==1)
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
tables=[]
if n: tables=[{"title":"MTF Heikin-Ashi SuperTrend","position":"top-right","compact":True,"rows":[[{"text":"Chart"},{"text":chart_key}],[{"text":"Higher timeframe"},{"text":htf}],[{"text":"Current trend"},{"text":"Bullish" if current_trend[-1]>0 else "Bearish" if current_trend[-1]<0 else "Waiting"}],[{"text":"HTF trend"},{"text":"Bullish" if htf_trend[-1]>0 else "Bearish" if htf_trend[-1]<0 else "Waiting"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Heikin-Ashi SuperTrend","title":True,"data":_finite(current_line),"color":"#64DD17","negativeColor":"#FF1744","type":"line","lineWidth":2,"overlay":True},{"name":"HTF Heikin-Ashi SuperTrend","title":True,"data":_finite(htf_line),"color":"#2979FF","negativeColor":"#FF1744","type":"line","lineWidth":3,"overlay":True}],"signals":[_events(current_up_signal,current_line,"ST UP","#64DD17","buy"),_events(current_down_signal,current_line,"ST DOWN","#FF1744","sell"),_events(htf_up_signal,htf_line,"HTF UP","#2979FF","buy"),_events(htf_down_signal,htf_line,"HTF DOWN","#FF1744","sell")],"layers":[],"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"chartTimeframe":chart_key,"higherTimeframe":htf,"htfAvailable":bool(np.any(valid)),"heikinOpen":_finite(ha_open),"heikinHigh":_finite(ha_high),"heikinLow":_finite(ha_low),"heikinClose":_finite(ha_close),"atr":_finite(atr),"currentUpper":_finite(current_up),"currentLower":_finite(current_down),"currentTrend":current_trend.tolist(),"currentLine":_finite(current_line),"htfSourceIndex":htf_source_index.tolist(),"htfHigh":_finite(htf_high),"htfLow":_finite(htf_low),"htfClose":_finite(htf_close),"htfATR":_finite(htf_atr),"htfUpper":_finite(htf_up),"htfLower":_finite(htf_down),"htfTrend":htf_trend.tolist(),"htfLine":_finite(htf_line),"currentUpSignal":current_up_signal.tolist(),"currentDownSignal":current_down_signal.tolist(),"htfUpSignal":htf_up_signal.tolist(),"htfDownSignal":htf_down_signal.tolist()}}
