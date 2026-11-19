# QuantDinger Target Trend
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Target Trend"
my_indicator_description="Detects ATR-buffered moving-average trend changes and projects entry, stop and three directional targets with target-hit tracking."

# @param trend_length int 10 Trend length range=2:300:1
# @param target_offset int 0 Target spacing adjustment range=-4:20:1
# @param atr_length int 200 ATR length range=5:500:1
# @param projection_bars int 20 Projection bars range=5:200:1

length=max(2,min(300,int(params.get("trend_length",10)))); target_offset=max(-4,min(20,int(params.get("target_offset",0)))); atr_length=max(5,min(500,int(params.get("atr_length",200)))); projection=max(5,min(200,int(params.get("projection_bars",20))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _sma(values,window):
    out=np.full(len(values),np.nan)
    for i in range(window-1,len(values)):
        sample=values[i-window+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.mean(sample))
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
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,c[:-1]]; true_range=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: true_range[0]=h[0]-l[0]
atr_raw=_rma(true_range,atr_length); atr_value=_sma(atr_raw,atr_length)*0.8; sma_high=_sma(h,length)+atr_value; sma_low=_sma(l,length)-atr_value; trend=np.zeros(n,dtype=int); trend_value=np.full(n,np.nan); signal_up=np.zeros(n,bool); signal_down=np.zeros(n,bool)
for i in range(n):
    previous_trend=trend[i-1] if i else 0; current=previous_trend
    if i>0 and np.isfinite(sma_high[i]) and np.isfinite(sma_high[i-1]) and c[i]>sma_high[i] and c[i-1]<=sma_high[i-1]: current=1
    if i>0 and np.isfinite(sma_low[i]) and np.isfinite(sma_low[i-1]) and c[i]<sma_low[i] and c[i-1]>=sma_low[i-1]: current=-1
    trend[i]=current; signal_up[i]=current==1 and previous_trend==-1; signal_down[i]=current==-1 and previous_trend==1; trend_value[i]=sma_low[i] if current==1 else sma_high[i] if current==-1 else np.nan
plans=[]; active=None; target_hit=np.zeros((3,n),bool); stop_hit=np.zeros(n,bool)
for i in range(n):
    if active is not None and i>active["start"]:
        direction=active["direction"]
        if not active["stopped"] and l[i]<=active["stop"]<=h[i]: active["stopped"]=True; stop_hit[i]=True
        if not active["stopped"]:
            for level in range(3):
                if not active["hits"][level] and ((direction==1 and h[i]>=active["targets"][level]) or (direction==-1 and l[i]<=active["targets"][level])): active["hits"][level]=True; target_hit[level,i]=True
        active["end"]=i
    if signal_up[i] or signal_down[i]:
        if active is not None: active["end"]=i
        direction=1 if signal_up[i] else -1; base=float(sma_low[i] if direction==1 else sma_high[i]); step=float(atr_value[i])*(1 if direction==1 else -1); targets=[float(c[i]+step*(5+target_offset)),float(c[i]+step*(10+2*target_offset)),float(c[i]+step*(15+3*target_offset))]; active={"start":int(i),"end":int(i),"direction":direction,"entry":float(c[i]),"stop":base,"targets":targets,"hits":[False,False,False],"stopped":False}; plans.append(active)
layers=[]
for plan in plans[-8:]:
    end=max(plan["end"],min(n-1,plan["start"]+projection)); color="#06B690" if plan["direction"]==1 else "#B67006"
    for role,value,line_color,style in (("Stop",plan["stop"],"#DB1E1E","solid"),("Entry",plan["entry"],color,"solid"),("Target 1",plan["targets"][0],color,"dashed"),("Target 2",plan["targets"][1],color,"dashed"),("Target 3",plan["targets"][2],color,"dashed")): layers.append({"type":"line","role":role,"startIndex":plan["start"],"endIndex":end,"y1":float(value),"y2":float(value),"color":line_color,"lineWidth":1,"lineStyle":style})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
bar_colors=["#06B690" if trend[i]==1 else "#B67006" if trend[i]==-1 else None for i in range(n)]; background=["rgba(6,182,144,0.06)" if trend[i]==1 else "rgba(182,112,6,0.06)" if trend[i]==-1 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Trend Stop","title":True,"data":_finite(trend_value),"color":"#06B690","negativeColor":"#B67006","type":"line","lineWidth":2,"overlay":True}],"signals":[_events(signal_up,l-2*atr_value,"TREND UP","#06B690","buy"),_events(signal_down,h+2*atr_value,"TREND DOWN","#B67006","sell"),_events(target_hit[0],c,"TP1","#16AC09","event"),_events(target_hit[1],c,"TP2","#16AC09","event"),_events(target_hit[2],c,"TP3","#16AC09","event"),_events(stop_hit,c,"STOP","#DB1E1E","event")],"layers":layers,"tables":[],"bar_colors":bar_colors,"background_colors":background,"calculatedVars":{"atr":_finite(atr_value),"smaHigh":_finite(sma_high),"smaLow":_finite(sma_low),"trend":trend.tolist(),"trendValue":_finite(trend_value),"signalUp":signal_up.tolist(),"signalDown":signal_down.tolist(),"target1Hit":target_hit[0].tolist(),"target2Hit":target_hit[1].tolist(),"target3Hit":target_hit[2].tolist(),"stopHit":stop_hit.tolist(),"plans":plans}}
