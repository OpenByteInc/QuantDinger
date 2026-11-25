# QuantDinger Triple EMA MTF SuperTrend Confluence
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Triple EMA MTF SuperTrend Confluence"
my_indicator_description="Combines three close-versus-EMA states with a completed higher-timeframe SuperTrend, emitting individual flips, full EMA confluence changes and a compact status dashboard."

# @param ema_1_length int 20 EMA 1 length range=1:500:1
# @param ema_2_length int 50 EMA 2 length range=1:500:1
# @param ema_3_length int 200 EMA 3 length range=1:1000:1
# @param supertrend_timeframe str 1H SuperTrend timeframe values=5m,15m,30m,1H,2H,4H,1D
# @param supertrend_factor float 3 SuperTrend factor range=0.1:20:0.1
# @param supertrend_atr_period int 10 SuperTrend ATR period range=1:500:1
# @param show_dashboard bool true Show dashboard
# @param show_shading bool true Shade SuperTrend regime

ema_lengths=[max(1,min(1000,int(params.get("ema_1_length",20)))),max(1,min(1000,int(params.get("ema_2_length",50)))),max(1,min(1000,int(params.get("ema_3_length",200))))]; htf=str(params.get("supertrend_timeframe","1H")); factor=max(0.1,min(20,float(params.get("supertrend_factor",3)))); atr_period=max(1,min(500,int(params.get("supertrend_atr_period",10)))); show_dashboard=bool(params.get("show_dashboard",True)); show_shading=bool(params.get("show_shading",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000.0
    return out
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
def _tf_seconds(value):
    key=str(value).strip().upper(); aliases={"5":"5M","15":"15M","30":"30M","60":"1H","120":"2H","240":"4H","D":"1D"}; key=aliases.get(key,key); return {"1M":60,"3M":180,"5M":300,"15M":900,"30M":1800,"1H":3600,"2H":7200,"4H":14400,"1D":86400}.get(key,3600)
def _aggregate(base,value):
    temp=base.copy(); temp["_seconds"]=_seconds(temp["time"].to_numpy(float)); temp["_bucket"]=np.floor(temp["_seconds"]/_tf_seconds(value)).astype(np.int64); return temp.groupby("_bucket",sort=True).agg(time=("_seconds","min"),open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum")).reset_index(drop=True)
def _supertrend(frame_value):
    high=frame_value["high"].to_numpy(float); low=frame_value["low"].to_numpy(float); close=frame_value["close"].to_numpy(float); previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous)))
    if len(tr): tr[0]=high[0]-low[0]
    atr=_rma(tr,atr_period); middle=(high+low)/2; upper_raw=middle+factor*atr; lower_raw=middle-factor*atr; upper=np.full(len(close),np.nan); lower=np.full(len(close),np.nan); direction=np.zeros(len(close),dtype=int); line=np.full(len(close),np.nan)
    for i in range(len(close)):
        if not np.isfinite(atr[i]): continue
        if i==0 or not np.isfinite(upper[i-1]): upper[i]=upper_raw[i]; lower[i]=lower_raw[i]; direction[i]=-1
        else:
            upper[i]=upper_raw[i] if upper_raw[i]<upper[i-1] or close[i-1]>upper[i-1] else upper[i-1]; lower[i]=lower_raw[i] if lower_raw[i]>lower[i-1] or close[i-1]<lower[i-1] else lower[i-1]
            direction[i]=1 if close[i]<lower[i-1] else -1 if close[i]>upper[i-1] else direction[i-1] if direction[i-1] else -1
        line[i]=lower[i] if direction[i]<0 else upper[i]
    return line,direction,atr
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
c=frame["close"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); n=len(frame); emas=[_ema(c,x) for x in ema_lengths]; states=[np.where(c>x,1,np.where(c<x,-1,0)) for x in emas]; flips=[]
for state in states:
    up=np.zeros(n,bool); down=np.zeros(n,bool)
    if n>1: up[1:]=(state[1:]==1)&(state[:-1]!=1); down[1:]=(state[1:]==-1)&(state[:-1]!=-1)
    flips.append((up,down))
source=None; context=ctx
if context is not None and hasattr(context,"raw_series"):
    for key in (htf,htf.replace("m","")):
        try:
            candidate=context.raw_series(key)
            if candidate is not None and len(candidate): source=candidate.copy().reset_index(drop=True); break
        except Exception: pass
if source is None: source=_aggregate(frame,htf)
for column in ("time","open","high","low","close","volume"):
    if column not in source.columns: source[column]=0.0
    source[column]=pd.to_numeric(source[column],errors="coerce")
source=source.sort_values("time").reset_index(drop=True); source_line,source_direction,source_atr=_supertrend(source); base_time=_seconds(frame["time"].to_numpy(float)); source_time=_seconds(source["time"].to_numpy(float)); source_index=np.searchsorted(source_time,base_time,side="right")-2; valid=(source_index>=0)&(source_index<len(source)); line=np.full(n,np.nan); direction=np.zeros(n,dtype=int); aligned_atr=np.full(n,np.nan)
if len(source):
    clipped=np.clip(source_index,0,len(source)-1); line[valid]=source_line[clipped[valid]]; direction[valid]=source_direction[clipped[valid]]; aligned_atr[valid]=source_atr[clipped[valid]]
st_up=np.zeros(n,bool); st_down=np.zeros(n,bool)
if n>1: st_up[1:]=(direction[1:]<0)&(direction[:-1]>=0); st_down[1:]=(direction[1:]>0)&(direction[:-1]<=0)
confluence=np.where((states[0]==1)&(states[1]==1)&(states[2]==1),1,np.where((states[0]==-1)&(states[1]==-1)&(states[2]==-1),-1,0)); bull_confluence=np.zeros(n,bool); bear_confluence=np.zeros(n,bool); neutral=np.zeros(n,bool)
if n>1: bull_confluence[1:]=(confluence[1:]==1)&(confluence[:-1]!=1); bear_confluence[1:]=(confluence[1:]==-1)&(confluence[:-1]!=-1); neutral[1:]=(confluence[1:]==0)&(confluence[:-1]!=0)
def _thin(mask,limit=1,min_gap=24):
    kept=np.zeros(len(mask),bool); last=None; count=0
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index); count+=1
            if count>=limit: break
    return kept
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
signals=[]
for i,(up,down) in enumerate(flips): signals.extend([_events(_thin(up),l,f"EMA {ema_lengths[i]} BULL","#2196F3" if i==0 else "#FDD835" if i==1 else "#FB8C00","buy"),_events(_thin(down),h,f"EMA {ema_lengths[i]} BEAR","#2196F3" if i==0 else "#FDD835" if i==1 else "#FB8C00","sell")])
signals.extend([_events(_thin(st_up),l,"ST BULL","#00C853","buy"),_events(_thin(st_down),h,"ST BEAR","#D50000","sell"),_events(_thin(bull_confluence,3,28),l,"EMA ALL BULL","#00C853","buy"),_events(_thin(bear_confluence,3,28),h,"EMA ALL BEAR","#D50000","sell")])
colors=["#2196F3","#FDD835","#FB8C00"]; plots=[{"name":f"EMA {ema_lengths[i]}","title":True,"data":_finite(emas[i]),"color":colors[i],"type":"line","lineWidth":2,"overlay":True} for i in range(3)]; plots.append({"name":"MTF SuperTrend","title":True,"data":_finite(line),"color":"#00C853","negativeColor":"#D50000","type":"line","lineWidth":2,"overlay":True})
tables=[]
if n and show_dashboard:
    rows=[]
    for i in range(3): rows.append([{"text":f"EMA {ema_lengths[i]}"},{"text":None if not np.isfinite(emas[i][-1]) else round(float(emas[i][-1]),6)},{"text":"Above" if states[i][-1]>0 else "Below" if states[i][-1]<0 else "Equal"}])
    rows.extend([[{"text":"EMA overall"},{"text":"Bullish" if confluence[-1]>0 else "Bearish" if confluence[-1]<0 else "Mixed"},{"text":""}],[{"text":"ST "+htf},{"text":"Bullish" if direction[-1]<0 else "Bearish" if direction[-1]>0 else "Waiting"},{"text":None if not np.isfinite(line[-1]) else round(float(line[-1]),6)}]])
    tables=[{"title":"Triple EMA + MTF SuperTrend","position":"top-right","compact":True,"rows":rows}]
background=["rgba(0,200,83,0.12)" if show_shading and direction[i]<0 else "rgba(213,0,0,0.12)" if show_shading and direction[i]>0 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":signals,"layers":[],"tables":tables,"bar_colors":[None]*n,"background_colors":background,"calculatedVars":{"emaLengths":ema_lengths,"emas":[_finite(x) for x in emas],"emaStates":[x.tolist() for x in states],"emaBullFlips":[x[0].tolist() for x in flips],"emaBearFlips":[x[1].tolist() for x in flips],"higherTimeframe":htf,"htfSourceIndex":source_index.tolist(),"supertrend":_finite(line),"supertrendDirection":direction.tolist(),"supertrendATR":_finite(aligned_atr),"supertrendBullFlip":st_up.tolist(),"supertrendBearFlip":st_down.tolist(),"confluence":confluence.tolist(),"bullishConfluence":bull_confluence.tolist(),"bearishConfluence":bear_confluence.tolist(),"neutralConfluence":neutral.tolist()}}
