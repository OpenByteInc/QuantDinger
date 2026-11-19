# QuantDinger Smoothed Heikin-Ashi Cloud
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Smoothed Heikin-Ashi Cloud"
my_indicator_description="Smooths Heikin-Ashi open, high, low and close with a selectable average and visualizes body direction plus optional wick clouds."

# @param ma_type str EMA Moving average type values=ALMA,HMA,SMA,SWMA,VWMA,WMA,ZLEMA,EMA
# @param ma_period int 9 Moving average length range=1:300:1
# @param alma_offset float 0.85 ALMA offset range=0:1:0.05
# @param alma_sigma int 6 ALMA sigma range=1:20:1
# @param show_close_line bool true Show close line
# @param show_high_low_lines bool false Show high and low lines
# @param show_fill bool true Show cloud background

ma_type=str(params.get("ma_type","EMA")).upper(); period=max(1,min(300,int(params.get("ma_period",9)))); alma_offset=max(0,min(1,float(params.get("alma_offset",0.85)))); alma_sigma=max(1,min(20,int(params.get("alma_sigma",6)))); show_close=bool(params.get("show_close_line",True)); show_hl=bool(params.get("show_high_low_lines",False)); show_fill=bool(params.get("show_fill",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.mean(sample))
    return out
def _wma(values,length):
    out=np.full(len(values),np.nan); weights=np.arange(1,length+1,dtype=float); divisor=float(np.sum(weights))
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights)/divisor)
    return out
def _vwma(values,volume,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]; weights=volume[i-length+1:i+1]; total=float(np.sum(weights))
        if np.all(np.isfinite(sample)) and np.all(np.isfinite(weights)) and total>0: out[i]=float(np.dot(sample,weights)/total)
    return out
def _alma(values,length,offset,sigma):
    out=np.full(len(values),np.nan); center=offset*(length-1); scale=length/sigma; weights=np.exp(-((np.arange(length)-center)**2)/(2*scale*scale)); weights/=np.sum(weights)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights))
    return out
def _average(values,volume):
    if ma_type=="ALMA": return _alma(values,period,alma_offset,alma_sigma)
    if ma_type=="HMA": return _wma(2*_wma(values,max(1,period//2))-_wma(values,period),max(1,int(np.sqrt(period))))
    if ma_type=="SMA": return _sma(values,period)
    if ma_type=="SWMA":
        out=np.full(len(values),np.nan)
        for i in range(3,len(values)):
            sample=values[i-3:i+1]
            if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,np.array([1,2,2,1]))/6)
        return out
    if ma_type in ("VWMA","WMA"): return _vwma(values,volume,period)
    if ma_type=="ZLEMA":
        lag=(period-1)//2; adjusted=values.copy()
        if lag>0: adjusted[lag:]=2*values[lag:]-values[:-lag]; adjusted[:lag]=np.nan
        return _ema(adjusted,period)
    return _ema(values,period)
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame); ha_close=(o+h+l+c)/4; ha_open=np.full(n,np.nan)
for i in range(n): ha_open[i]=(o[i]+c[i])/2 if i==0 else (ha_open[i-1]+ha_close[i-1])/2
ha_high=np.maximum(h,np.maximum(ha_open,ha_close)); ha_low=np.minimum(l,np.minimum(ha_open,ha_close)); smooth_open=_average(ha_open,volume); smooth_close=_average(ha_close,volume); smooth_high=_average(ha_high,volume); smooth_low=_average(ha_low,volume); denominator=smooth_high-smooth_low; trend=np.divide(100*(smooth_close-smooth_open),denominator,out=np.full(n,np.nan),where=np.isfinite(denominator)&(np.abs(denominator)>1e-12)); body_high=np.maximum(smooth_open,smooth_close); body_low=np.minimum(smooth_open,smooth_close); background=["rgba(38,166,154,0.12)" if show_fill and np.isfinite(trend[i]) and trend[i]>0 else "rgba(239,83,80,0.12)" if show_fill and np.isfinite(trend[i]) else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Smoothed HA Open","title":False,"data":_finite(smooth_open),"color":"rgba(128,128,128,0)","type":"line","lineWidth":1,"overlay":True},{"name":"Smoothed HA Close","title":show_close,"data":_finite(smooth_close) if show_close else [None]*n,"color":"#26A69A","negativeColor":"#EF5350","type":"line","lineWidth":2,"overlay":True},{"name":"Smoothed HA High","title":show_hl,"data":_finite(smooth_high) if show_hl else [None]*n,"color":"#808080","type":"line","lineWidth":1,"overlay":True},{"name":"Smoothed HA Low","title":show_hl,"data":_finite(smooth_low) if show_hl else [None]*n,"color":"#808080","type":"line","lineWidth":1,"overlay":True}],"signals":[],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":background,"calculatedVars":{"heikinOpen":_finite(ha_open),"heikinHigh":_finite(ha_high),"heikinLow":_finite(ha_low),"heikinClose":_finite(ha_close),"smoothedOpen":_finite(smooth_open),"smoothedHigh":_finite(smooth_high),"smoothedLow":_finite(smooth_low),"smoothedClose":_finite(smooth_close),"bodyHigh":_finite(body_high),"bodyLow":_finite(body_low),"trendPercent":_finite(trend),"bullish":(trend>0).tolist(),"bearish":(trend<=0).tolist()}}
