# QuantDinger TKP T3 Reversal
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger TKP T3 Reversal"
my_indicator_description="Combines TSI momentum curls, Parabolic SAR, fast and slow Tillson T3 averages, Heikin-Ashi state and reversal candle coloring."

# @param tsi_long int 25 TSI long length range=1:200:1
# @param tsi_short int 5 TSI short length range=1:100:1
# @param tsi_signal int 14 TSI signal length range=1:100:1
# @param psar_start float 0.043 PSAR start range=0.001:0.5:0.001
# @param psar_increment float 0.043 PSAR increment range=0.001:0.5:0.001
# @param psar_maximum float 0.34 PSAR maximum range=0.01:1:0.01
# @param fast_t3_length int 5 Fast T3 length range=1:100:1
# @param slow_t3_length int 8 Slow T3 length range=1:100:1
# @param t3_factor float 0.7 T3 factor range=0.1:1:0.05
# @param heikin_ashi_colors bool false Use Heikin-Ashi state colors

tsi_long=max(1,min(200,int(params.get("tsi_long",25)))); tsi_short=max(1,min(100,int(params.get("tsi_short",5)))); tsi_signal=max(1,min(100,int(params.get("tsi_signal",14)))); psar_start=max(0.001,min(0.5,float(params.get("psar_start",0.043)))); psar_increment=max(0.001,min(0.5,float(params.get("psar_increment",0.043)))); psar_max=max(psar_start,min(1,float(params.get("psar_maximum",0.34)))); fast_length=max(1,min(100,int(params.get("fast_t3_length",5)))); slow_length=max(1,min(100,int(params.get("slow_t3_length",8)))); factor=max(0.1,min(1,float(params.get("t3_factor",0.7)))); ha_colors=bool(params.get("heikin_ashi_colors",False))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
def _t3(values,length,b):
    e1=_ema(values,length); e2=_ema(e1,length); e3=_ema(e2,length); e4=_ema(e3,length); e5=_ema(e4,length); e6=_ema(e5,length); c1=-b**3; c2=3*b*b+3*b**3; c3=-6*b*b-3*b-3*b**3; c4=1+3*b+b**3+3*b*b; return c1*e6+c2*e5+c3*e4+c4*e3
def _cross(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(((a[1:]>b[1:])&(a[:-1]<=b[:-1]))|((a[1:]<b[1:])&(a[:-1]>=b[:-1])))
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); change=np.r_[np.nan,np.diff(c)]; smoothed_change=_ema(_ema(change,tsi_long),tsi_short); smoothed_absolute=_ema(_ema(np.abs(change),tsi_long),tsi_short); tsi=np.divide(100*smoothed_change,smoothed_absolute,out=np.full(n,np.nan),where=np.isfinite(smoothed_absolute)&(smoothed_absolute>1e-12)); tsi_average=_ema(tsi,tsi_signal); curl_up=np.zeros(n,bool); curl_down=np.zeros(n,bool)
if n>1:
    curl_up[1:]=np.isfinite(tsi[1:])&np.isfinite(tsi[:-1])&np.isfinite(tsi_average[1:])&(tsi[1:]>tsi[:-1])&(tsi[1:]<tsi_average[1:]); curl_down[1:]=np.isfinite(tsi[1:])&np.isfinite(tsi[:-1])&np.isfinite(tsi_average[1:])&(tsi[1:]<tsi[:-1])&(tsi[1:]>tsi_average[1:])
sar=np.full(n,np.nan); sar_direction=np.zeros(n,dtype=int)
if n==1: sar[0]=l[0]; sar_direction[0]=1
elif n>1:
    bullish=c[1]>c[0]; extreme=h[1] if bullish else l[1]; current=l[0] if bullish else h[0]; acceleration=psar_start; sar[0]=current; sar_direction[0]=1 if bullish else -1
    for i in range(1,n):
        candidate=current+acceleration*(extreme-current)
        if bullish:
            if candidate>l[i]: bullish=False; candidate=extreme; extreme=l[i]; acceleration=psar_start
            else:
                if h[i]>extreme: extreme=h[i]; acceleration=min(psar_max,acceleration+psar_increment)
                candidate=min(candidate,l[i-1],l[i-2] if i>1 else l[i-1])
        else:
            if candidate<h[i]: bullish=True; candidate=extreme; extreme=h[i]; acceleration=psar_start
            else:
                if l[i]<extreme: extreme=l[i]; acceleration=min(psar_max,acceleration+psar_increment)
                candidate=max(candidate,h[i-1],h[i-2] if i>1 else h[i-1])
        current=float(candidate); sar[i]=current; sar_direction[i]=1 if current<c[i] else -1
fast_t3=_t3(c,fast_length,factor); slow_t3=_t3(c,slow_length,factor); ha_close=(o+h+l+c)/4; ha_open=np.full(n,np.nan)
for i in range(n): ha_open[i]=(o[i]+c[i])/2 if i==0 else (ha_open[i-1]+ha_close[i-1])/2
ha_high=np.maximum(h,np.maximum(ha_open,ha_close)); ha_low=np.minimum(l,np.minimum(ha_open,ha_close)); color_source=ha_close if ha_colors else c; bull=(color_source>slow_t3)&(fast_t3>=slow_t3); bear=(color_source<slow_t3)&(fast_t3<=slow_t3); bull_reversal=(color_source>slow_t3)&(fast_t3<=slow_t3); bear_reversal=(color_source<slow_t3)&(fast_t3>=slow_t3); bar_colors=["#64B5F6" if bull[i] else "#EF5350" if bear[i] else "#FFF176" if bull_reversal[i] or bear_reversal[i] else None for i in range(n)]; close_t3_cross=_cross(c,slow_t3); t3_cross=_cross(slow_t3,fast_t3)
def _thin(mask,limit,min_gap):
    selected=[]
    for value in np.flatnonzero(mask)[::-1]:
        index=int(value)
        if all(abs(index-other)>=min_gap for other in selected): selected.append(index)
        if len(selected)>=limit: break
    result=np.zeros(n,bool); result[selected]=True
    return result
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Parabolic SAR","title":True,"data":_finite(sar),"color":"#64B5F6","negativeColor":"#EF5350","type":"line","lineWidth":1,"overlay":True},{"name":"T3 Fast","title":True,"data":_finite(fast_t3),"color":"#64B5F6","negativeColor":"#EF5350","type":"line","lineWidth":2,"overlay":True},{"name":"T3 Slow","title":True,"data":_finite(slow_t3),"color":"#64B5F6","negativeColor":"#EF5350","type":"line","lineWidth":2,"overlay":True}],"signals":[_events(_thin(curl_up,4,12),l,"CURL UP","#17FF00","buy"),_events(_thin(curl_down,4,12),h,"CURL DOWN","#FFEB3B","sell"),_events(_thin(close_t3_cross,2,15),c,"PRICE/T3","#FFF176","event"),_events(_thin(t3_cross,2,15),c,"T3 CROSS","#42A5F5","event")],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"tsi":_finite(tsi),"tsiSignal":_finite(tsi_average),"curlUp":curl_up.tolist(),"curlDown":curl_down.tolist(),"psar":_finite(sar),"psarDirection":sar_direction.tolist(),"fastT3":_finite(fast_t3),"slowT3":_finite(slow_t3),"heikinOpen":_finite(ha_open),"heikinHigh":_finite(ha_high),"heikinLow":_finite(ha_low),"heikinClose":_finite(ha_close),"bullTrend":bull.tolist(),"bearTrend":bear.tolist(),"bullReversal":bull_reversal.tolist(),"bearReversal":bear_reversal.tolist(),"closeT3Cross":close_t3_cross.tolist(),"t3Cross":t3_cross.tolist()}}
