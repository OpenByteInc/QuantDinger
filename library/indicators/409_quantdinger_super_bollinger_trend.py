# QuantDinger Super Bollinger Trend
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Super Bollinger Trend"
my_indicator_description="Runs a trailing Bollinger state machine, marks direction reversals, connects reversal pivots and projects a median or average move target."

# @param period int 12 Bollinger period range=1:300:1
# @param multiplier float 2 Bollinger multiplier range=0.1:20:0.1
# @param show_zigzag bool true Show reversal zigzag
# @param signal_display str Signal Signal display values=Signal,Peak Distance
# @param distance_source str ZigZag Distance source values=ZigZag,High/Low,Close
# @param show_table bool false Show distance table
# @param show_take_profit bool false Show projected target
# @param target_statistic str Median Target statistic values=Median,Average

period=max(1,min(300,int(params.get("period",12)))); multiplier=max(0.1,min(20,float(params.get("multiplier",2)))); show_zigzag=bool(params.get("show_zigzag",True)); signal_display=str(params.get("signal_display","Signal")); distance_source=str(params.get("distance_source","ZigZag")); show_table=bool(params.get("show_table",False)); show_tp=bool(params.get("show_take_profit",False)); statistic=str(params.get("target_statistic","Median"))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        window=values[i-length+1:i+1]
        if np.all(np.isfinite(window)): out[i]=float(np.mean(window))
    return out
def _stdev(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        window=values[i-length+1:i+1]
        if np.all(np.isfinite(window)): out[i]=float(np.std(window,ddof=0))
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); upper=_sma(h,period)+_stdev(h,period)*multiplier; lower=_sma(l,period)-_stdev(l,period)*multiplier; state=np.full(n,np.nan); direction=np.zeros(n,dtype=int); long_signal=np.zeros(n,bool); short_signal=np.zeros(n,bool); long_tp=np.zeros(n,bool); short_tp=np.zeros(n,bool); target=np.full(n,np.nan); signal_points=[]; distances=[]; running_state=0.0; running_direction=0
for i in range(n):
    if not np.isfinite(upper[i]) or not np.isfinite(lower[i]):
        state[i]=0.0; continue
    previous_state=running_state; preliminary=max(previous_state,lower[i]) if c[i]>previous_state else min(previous_state,upper[i]) if c[i]<previous_state else 0.0; previous_close=c[i-1] if i else np.nan; crossed_up=i>0 and c[i]>preliminary and previous_close<=previous_state; crossed_down=i>0 and c[i]<preliminary and previous_close>=previous_state
    if crossed_up:
        running_state=float(lower[i]); running_direction=1; long_signal[i]=True
    elif crossed_down:
        running_state=float(upper[i]); running_direction=-1; short_signal[i]=True
    else: running_state=float(preliminary)
    state[i]=running_state; direction[i]=running_direction
    if long_signal[i] or short_signal[i]:
        value=running_state if distance_source=="ZigZag" else l[i] if long_signal[i] and distance_source=="High/Low" else h[i] if short_signal[i] and distance_source=="High/Low" else c[i]
        if signal_points: distances.append(abs(float(value)-signal_points[-1][1]))
        signal_points.append((int(i),float(value),1 if long_signal[i] else -1))
    if signal_points and distances:
        distance=float(np.median(distances)) if statistic=="Median" else float(np.mean(distances)); position=c[i]>running_state; projected=signal_points[-1][1]+distance if position else signal_points[-1][1]-distance; target[i]=projected
        if show_tp and i>0:
            long_tp[i]=position and h[i]>=projected and (not np.isfinite(target[i-1]) or h[i-1]<target[i-1]); short_tp[i]=(not position) and l[i]<=projected and (not np.isfinite(target[i-1]) or l[i-1]>target[i-1])
layers=[]
if show_zigzag:
    for index in range(1,len(signal_points)):
        x1,y1,_=signal_points[index-1]; x2,y2,side=signal_points[index]; layers.append({"type":"line","role":"Reversal ZigZag","startIndex":x1,"endIndex":x2,"y1":y1,"y2":y2,"color":"#8A8A8A","lineWidth":1,"lineStyle":"dashed"})
if show_tp and signal_points and distances and n:
    finite_target=np.flatnonzero(np.isfinite(target))
    if len(finite_target):
        start=signal_points[-1][0]; value=float(target[finite_target[-1]]); layers.append({"type":"line","role":"Take Profit","startIndex":start,"endIndex":n+10,"y1":value,"y2":value,"color":"#00C853","lineWidth":1}); layers.append({"type":"label","role":"Take Profit","index":n+10,"price":value,"text":"TP","color":"rgba(0,0,0,0)","textColor":"#E0E0E0","size":"small"})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
distance_value=float(np.median(distances)) if distances and statistic=="Median" else float(np.mean(distances)) if distances else np.nan; tables=[]
if show_table and n: tables=[{"title":"Super Bollinger Trend","position":"top-right","compact":True,"rows":[[{"text":f"{statistic} ZigZag Distance"},{"text":"—" if not np.isfinite(distance_value) else f"{distance_value:.6g}p"}],[{"text":"Reversals"},{"text":str(len(signal_points))}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Super Bollinger Trend","title":True,"data":_finite(state),"color":"#00C853","negativeColor":"#FF1744","type":"line","lineWidth":2,"overlay":True},{"name":"Take Profit","title":show_tp,"data":_finite(target) if show_tp else [None]*n,"color":"#00C853","type":"line","lineWidth":1,"lineStyle":"dashed","overlay":True}],"signals":[_events(long_signal,l,"LONG","#00C853","buy"),_events(short_signal,h,"SHORT","#FF1744","sell"),_events(long_tp,target,"TP","#00C853","event"),_events(short_tp,target,"TP","#FF1744","event")],"layers":layers[-24:],"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"bollingerUpper":_finite(upper),"bollingerLower":_finite(lower),"state":_finite(state),"direction":direction.tolist(),"longSignal":long_signal.tolist(),"shortSignal":short_signal.tolist(),"target":_finite(target),"longTargetHit":long_tp.tolist(),"shortTargetHit":short_tp.tolist(),"signalPoints":[{"index":x,"price":y,"direction":side} for x,y,side in signal_points],"distances":[float(x) for x in distances],"distanceStatistic":None if not np.isfinite(distance_value) else float(distance_value)}}
