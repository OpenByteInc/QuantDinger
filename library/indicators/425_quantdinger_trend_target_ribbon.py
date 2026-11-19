# QuantDinger Trend Target Ribbon
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trend Target Ribbon"
my_indicator_description="Confirms ALMA trends with volatility bands and normalized slope, paints a conviction ribbon and manages structure-based stops with two to four R-multiple targets."

# @param alma_length int 34 ALMA length range=5:300:1
# @param alma_offset float 0.85 ALMA offset range=0:1:0.05
# @param alma_sigma float 6 ALMA sigma range=1:15:0.5
# @param deviation_length int 34 Deviation length range=5:300:1
# @param confirmation_multiplier float 0.65 Confirmation multiplier range=0.1:3:0.05
# @param slope_length int 3 Slope length range=1:10:1
# @param minimum_slope float 0.08 Minimum normalized slope range=0:1:0.01
# @param atr_length int 14 ATR length range=5:200:1
# @param stop_lookback int 12 Stop structure lookback range=3:50:1
# @param minimum_stop_atr float 0.75 Minimum stop ATR range=0.25:3:0.05
# @param maximum_stop_atr float 3 Maximum stop ATR range=1:8:0.25
# @param target_count int 4 Profit targets range=2:4:1
# @param target_zone_percent float 0.06 Target zone size range=0.01:0.2:0.01
# @param projection_bars int 30 Projection length range=10:100:1
# @param keep_positions int 4 Positions on chart range=1:5:1

alma_length=max(5,min(300,int(params.get("alma_length",34)))); alma_offset=max(0,min(1,float(params.get("alma_offset",0.85)))); alma_sigma=max(1,min(15,float(params.get("alma_sigma",6)))); dev_length=max(5,min(300,int(params.get("deviation_length",34)))); dev_mult=max(0.1,min(3,float(params.get("confirmation_multiplier",0.65)))); slope_length=max(1,min(10,int(params.get("slope_length",3)))); slope_min=max(0,min(1,float(params.get("minimum_slope",0.08)))); atr_length=max(5,min(200,int(params.get("atr_length",14)))); stop_lookback=max(3,min(50,int(params.get("stop_lookback",12)))); min_stop=max(0.25,min(3,float(params.get("minimum_stop_atr",0.75)))); max_stop=max(1,min(8,float(params.get("maximum_stop_atr",3)))); target_count=max(2,min(4,int(params.get("target_count",4)))); zone_pct=max(0.01,min(0.2,float(params.get("target_zone_percent",0.06)))); projection=max(10,min(100,int(params.get("projection_bars",30)))); keep=max(1,min(5,int(params.get("keep_positions",4))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _alma(values,length,offset,sigma):
    out=np.full(len(values),np.nan); center=offset*(length-1); scale=length/sigma; weights=np.exp(-((np.arange(length)-center)**2)/(2*scale*scale)); weights/=np.sum(weights)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights))
    return out
def _stdev(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.std(sample,ddof=0))
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
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); alma=_alma(c,alma_length,alma_offset,alma_sigma); deviation=_stdev(c,dev_length); previous=np.r_[np.nan,c[:-1]]; true_range=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: true_range[0]=h[0]-l[0]
atr=_rma(true_range,atr_length); slope_score=np.full(n,np.nan); valid=np.arange(n)>=slope_length; slope_score[valid]=np.divide(alma[valid]-alma[np.arange(n)[valid]-slope_length],atr[valid],out=np.full(np.sum(valid),np.nan),where=np.isfinite(atr[valid])&(atr[valid]>0)); upper=alma+deviation*dev_mult; lower=alma-deviation*dev_mult; trend=np.zeros(n,dtype=int); bull_flip=np.zeros(n,bool); bear_flip=np.zeros(n,bool); current=0
for i in range(n):
    bull=np.isfinite(slope_score[i]) and slope_score[i]>slope_min and c[i]>upper[i]; bear=np.isfinite(slope_score[i]) and slope_score[i]<-slope_min and c[i]<lower[i]
    if current!=1 and bull: current=1; bull_flip[i]=True
    elif current!=-1 and bear: current=-1; bear_flip[i]=True
    trend[i]=current
distance=np.divide(np.abs(c-alma),atr,out=np.zeros(n),where=np.isfinite(atr)&(atr>0)); conviction=np.clip(np.abs(np.nan_to_num(slope_score))*2+distance*0.35,0,1); ribbon_edge=np.where(trend==1,alma-deviation*0.55,np.where(trend==-1,alma+deviation*0.55,alma)); ribbon_mid=(alma+ribbon_edge)/2; plans=[]; active=None; target_hits=[np.zeros(n,bool) for _ in range(4)]; stop_hit=np.zeros(n,bool)
for i in range(n):
    if active is not None and not active["ended"] and i>active["start"]:
        direction=active["direction"]; stopped=(direction==1 and l[i]<=active["stop"]) or (direction==-1 and h[i]>=active["stop"])
        if stopped: active["ended"]=True; active["end"]=i; active["stopped"]=True; stop_hit[i]=True
        else:
            for target_index,target in enumerate(active["targets"]):
                if not active["hits"][target_index] and ((direction==1 and h[i]>=target) or (direction==-1 and l[i]<=target)): active["hits"][target_index]=True; target_hits[target_index][i]=True
            active["end"]=i
    if bull_flip[i] or bear_flip[i]:
        if active is not None and not active["ended"]: active["ended"]=True; active["end"]=i
        direction=1 if bull_flip[i] else -1; start=max(0,i-stop_lookback); structure=float(np.min(l[start:i])) if direction==1 and i>start else float(l[i]); structure=float(np.max(h[start:i])) if direction==-1 and i>start else structure; raw=c[i]-structure if direction==1 else structure-c[i]; minimum=atr[i]*min_stop if np.isfinite(atr[i]) else max(h[i]-l[i],1e-9); maximum=atr[i]*max_stop if np.isfinite(atr[i]) else minimum*4; risk=float(np.clip(raw if np.isfinite(raw) else minimum,minimum,maximum)); stop=float(c[i]-direction*risk); targets=[float(c[i]+direction*risk*(j+1)) for j in range(target_count)]; active={"start":int(i),"end":int(i),"direction":direction,"entry":float(c[i]),"stop":stop,"risk":risk,"targets":targets,"hits":[False]*target_count,"stopped":False,"ended":False}; plans.append(active)
layers=[]
for plan in plans[-keep:]:
    end=max(plan["end"],min(n-1,plan["start"]+projection)); color="#00FF00" if plan["direction"]==1 else "#FF0066"; layers.append({"type":"zone","role":"Risk","startIndex":plan["start"],"endIndex":end,"top":max(plan["entry"],plan["stop"]),"bottom":min(plan["entry"],plan["stop"]),"fillColor":"#FF0066","borderColor":"#FF0066","opacity":0.12}); layers.append({"type":"line","role":"Entry","startIndex":plan["start"],"endIndex":end,"y1":plan["entry"],"y2":plan["entry"],"color":color,"lineWidth":2}); layers.append({"type":"line","role":"Stop","startIndex":plan["start"],"endIndex":end,"y1":plan["stop"],"y2":plan["stop"],"color":"#FF0066","lineWidth":2})
    previous_level=plan["entry"]
    for target_index,target in enumerate(plan["targets"]): zone_half=plan["risk"]*zone_pct; layers.append({"type":"zone","role":f"Target {target_index+1}","startIndex":plan["start"],"endIndex":end,"top":target+zone_half,"bottom":target-zone_half,"fillColor":"#00FF00","borderColor":"#00FF00","opacity":0.08}); layers.append({"type":"line","role":f"Target {target_index+1}","startIndex":plan["start"],"endIndex":end,"y1":target,"y2":target,"color":"#00FF00","lineWidth":1,"lineStyle":"dashed"}); previous_level=target
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
signals=[_events(bull_flip,l,"LONG","#00FF00","buy"),_events(bear_flip,h,"SHORT","#FF0066","sell"),_events(stop_hit,c,"STOP","#FF0066","event")]
for i in range(target_count): signals.append(_events(target_hits[i],c,f"T{i+1}","#00FF00","event"))
bar_colors=[("rgba(0,255,0,0.85)" if conviction[i]>0.5 else "rgba(0,255,0,0.45)") if trend[i]==1 else ("rgba(255,0,102,0.85)" if conviction[i]>0.5 else "rgba(255,0,102,0.45)") if trend[i]==-1 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"ALMA","title":True,"data":_finite(alma),"color":"#FFFFFF","type":"line","lineWidth":1,"overlay":True},{"name":"Ribbon Mid","title":False,"data":_finite(ribbon_mid),"color":"#808080","type":"line","lineWidth":1,"overlay":True},{"name":"Ribbon Edge","title":True,"data":_finite(ribbon_edge),"color":"#00FF00","negativeColor":"#FF0066","type":"line","lineWidth":3,"overlay":True}],"signals":signals,"layers":layers,"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"alma":_finite(alma),"deviation":_finite(deviation),"atr":_finite(atr),"slopeScore":_finite(slope_score),"upperConfirmation":_finite(upper),"lowerConfirmation":_finite(lower),"trend":trend.tolist(),"bullFlip":bull_flip.tolist(),"bearFlip":bear_flip.tolist(),"distanceScore":_finite(distance),"conviction":_finite(conviction),"ribbonEdge":_finite(ribbon_edge),"ribbonMid":_finite(ribbon_mid),"stopHit":stop_hit.tolist(),"targetHits":[x.tolist() for x in target_hits[:target_count]],"plans":plans}}
