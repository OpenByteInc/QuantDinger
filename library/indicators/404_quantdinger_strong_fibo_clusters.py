# QuantDinger Strong Fibo Clusters
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Strong Fibo Clusters"
my_indicator_description="Clusters Fibonacci retracements from confirmed swings inside completed weekly, bi-weekly or monthly anchors and tracks prior-period liquidity states."

# @param anchor_period str 1W Anchor period values=1W,2W,1M
# @param source_periods int 1 Source periods range=1:8:1
# @param swing_length int 10 Swing length range=3:50:1
# @param maximum_swings int 10 Maximum swings per side range=4:15:1
# @param cluster_tolerance_atr float 0.5 Cluster tolerance range=0.1:3:0.1
# @param minimum_pair_range_atr float 2 Minimum pair range range=0.1:10:0.1
# @param minimum_strength int 2 Minimum cluster strength range=2:20:1
# @param maximum_clusters int 5 Maximum clusters range=1:10:1
# @param minimum_separation_atr float 1.5 Minimum cluster separation range=0.5:5:0.1
# @param show_golden_pocket bool true Show golden pocket
# @param show_equilibrium bool true Show equilibrium
# @param show_extensions bool true Show extensions

anchor=str(params.get("anchor_period","1W")); anchor=anchor if anchor in ("1W","2W","1M") else "1W"; source_periods=max(1,min(8,int(params.get("source_periods",1)))); swing_length=max(3,min(50,int(params.get("swing_length",10)))); max_swings=max(4,min(15,int(params.get("maximum_swings",10)))); tolerance=max(0.1,min(3,float(params.get("cluster_tolerance_atr",0.5)))); minimum_range=max(0.1,min(10,float(params.get("minimum_pair_range_atr",2)))); minimum_strength=max(2,min(20,int(params.get("minimum_strength",2)))); maximum_clusters=max(1,min(10,int(params.get("maximum_clusters",5)))); separation=max(0.5,min(5,float(params.get("minimum_separation_atr",1.5)))); show_gp=bool(params.get("show_golden_pocket",True)); show_eq=bool(params.get("show_equilibrium",True)); show_ext=bool(params.get("show_extensions",True))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _rma(v,l):
    out=np.full(len(v),np.nan); state=np.nan; seed=[]
    for i,x in enumerate(v):
        if not np.isfinite(x): continue
        if not np.isfinite(state):
            seed.append(float(x))
            if len(seed)==l: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(l-1)+float(x))/l; out[i]=state
    return out
def _period_keys(time):
    dt=pd.to_datetime(time,unit='s',utc=True); days=np.floor(time/86400).astype(np.int64)
    if anchor=="1W": return np.floor((days+3)/7).astype(np.int64)
    if anchor=="2W": return np.floor((days+3)/14).astype(np.int64)
    return (dt.year.to_numpy()*12+dt.month.to_numpy()-1).astype(np.int64)
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
time=frame["time"].to_numpy(float); time=np.where(np.abs(time)>1e10,time/1000,time); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan; atr=_rma(tr,14); keys=_period_keys(time) if n else np.asarray([],int)
pivot_high=np.full(n,np.nan); pivot_low=np.full(n,np.nan)
for confirmed in range(2*swing_length,n):
    center=confirmed-swing_length
    if high[center]==np.max(high[center-swing_length:center+swing_length+1]): pivot_high[confirmed]=high[center]
    if low[center]==np.min(low[center-swing_length:center+swing_length+1]): pivot_low[confirmed]=low[center]
period_high=np.full(n,np.nan); period_low=np.full(n,np.nan); cluster_count=np.zeros(n,dtype=int); current_clusters=[]; snapshots=[]; state_high=state_low=0; sweep_high=np.zeros(n,bool); sweep_low=np.zeros(n,bool); accept_high=np.zeros(n,bool); accept_low=np.zeros(n,bool); failed_high=np.zeros(n,bool); failed_low=np.zeros(n,bool); last_high=last_low=np.nan
unique_keys=[]
for k in keys:
    if not unique_keys or unique_keys[-1]!=k: unique_keys.append(int(k))
for i in range(n):
    if i and keys[i]!=keys[i-1]:
        completed=[key for key in unique_keys if key<keys[i]][-source_periods:]; mask=np.isin(keys[:i],completed); swings=[]
        for j in range(i):
            if mask[j] and np.isfinite(pivot_high[j]): swings.append((float(pivot_high[j]),True,j-swing_length))
            if mask[j] and np.isfinite(pivot_low[j]): swings.append((float(pivot_low[j]),False,j-swing_length))
        highs=[x for x in swings if x[1]][-max_swings:]; lows=[x for x in swings if not x[1]][-max_swings:]; levels=[]; local_atr=float(atr[i-1]) if np.isfinite(atr[i-1]) else float(np.nanmean(tr[max(0,i-50):i])); fibs=(0.236,0.382,0.5,0.618,0.786,1.0)
        pair_id=0
        for hp in highs:
            for lp in lows:
                span=hp[0]-lp[0]
                if span>minimum_range*local_atr:
                    for ratio in fibs: levels.append((lp[0]+span*ratio,pair_id))
                    pair_id+=1
        raw=[]
        for value,pair in sorted(levels):
            match=next((item for item in raw if abs(item["center"]-value)<=tolerance*local_atr),None)
            if match is None: raw.append({"center":value,"pairs":{pair}})
            else: match["center"]=(match["center"]*len(match["pairs"])+value)/(len(match["pairs"])+1); match["pairs"].add(pair)
        candidates=sorted([{"center":x["center"],"strength":len(x["pairs"])} for x in raw if len(x["pairs"])>=minimum_strength],key=lambda x:x["strength"],reverse=True); selected=[]
        for item in candidates:
            if all(abs(item["center"]-other["center"])>=separation*local_atr for other in selected): selected.append(item)
            if len(selected)>=maximum_clusters: break
        last_mask=keys[:i]==keys[i-1]; last_high=float(np.max(high[:i][last_mask])); last_low=float(np.min(low[:i][last_mask])); current_clusters=selected; snapshots.append({"start":i,"clusters":[dict(x) for x in selected],"high":last_high,"low":last_low,"atr":local_atr}); state_high=state_low=0
    period_high[i]=last_high; period_low[i]=last_low; cluster_count[i]=len(current_clusters)
    if np.isfinite(last_high) and np.isfinite(atr[i]):
        if high[i]>last_high+0.08*atr[i] and close[i]<=last_high: sweep_high[i]=True; state_high=1
        if close[i]>last_high+0.30*atr[i]: accept_high[i]=True; state_high=2
        if state_high==2 and close[i]<last_high: failed_high[i]=True; state_high=3
    if np.isfinite(last_low) and np.isfinite(atr[i]):
        if low[i]<last_low-0.08*atr[i] and close[i]>=last_low: sweep_low[i]=True; state_low=1
        if close[i]<last_low-0.30*atr[i]: accept_low[i]=True; state_low=2
        if state_low==2 and close[i]>last_low: failed_low[i]=True; state_low=3
layers=[]
if snapshots:
    snap=snapshots[-1]; end=n+30
    for item in snap["clusters"]:
        strength=item["strength"]; color="#21C997" if strength>=6 else "#26A69A" if strength>=4 else "#00897B"; half=0.2*snap["atr"]; layers.append({"type":"zone","role":f"Fib Cluster {strength}","startIndex":snap["start"],"endIndex":end,"top":item["center"]+half,"bottom":item["center"]-half,"fillColor":color,"borderColor":color,"opacity":0.22})
    if show_gp:
        span=snap["high"]-snap["low"]; layers.append({"type":"zone","role":"Golden Pocket","startIndex":snap["start"],"endIndex":end,"top":snap["low"]+span*0.618,"bottom":snap["low"]+span*0.5,"fillColor":"#787878","borderColor":"#787878","opacity":0.18})
    if show_eq: layers.append({"type":"line","role":"Equilibrium","startIndex":snap["start"],"endIndex":end,"y1":(snap["high"]+snap["low"])/2,"y2":(snap["high"]+snap["low"])/2,"color":"#8E8E8E","lineWidth":1,"lineStyle":"dashed"})
    if show_ext:
        span=snap["high"]-snap["low"]
        for ratio,color in ((1.13,"#CC24E2"),(1.27,"#880E4F"),(-0.13,"#CC24E2"),(-0.27,"#880E4F")): layers.append({"type":"line","role":f"Extension {ratio}","startIndex":snap["start"],"endIndex":end,"y1":snap["low"]+span*ratio,"y2":snap["low"]+span*ratio,"color":color,"lineWidth":1})
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Prior High","title":True,"data":_finite(period_high),"color":"#21C997","type":"line","lineWidth":2,"lineStyle":"step","overlay":True},{"name":"Prior Low","title":True,"data":_finite(period_low),"color":"#CC24E2","type":"line","lineWidth":2,"lineStyle":"step","overlay":True}],"signals":[{"type":"sell","name":"High Sweep","text":"SWEEP","data":[{"active":True,"price":float(high[i]),"text":"SWEEP","color":"#CC24E2"} if sweep_high[i] else None for i in range(n)],"color":"#CC24E2","renderMode":"events","overlay":True},{"type":"buy","name":"Low Sweep","text":"SWEEP","data":[{"active":True,"price":float(low[i]),"text":"SWEEP","color":"#21C997"} if sweep_low[i] else None for i in range(n)],"color":"#21C997","renderMode":"events","overlay":True}],"layers":layers,"tables":([{"title":"Fibo Clusters","position":"top-right","compact":True,"rows":[[{"text":"Anchor"},{"text":anchor}],[{"text":"Clusters"},{"text":str(len(current_clusters))}],[{"text":"Prior high"},{"text":"—" if not np.isfinite(last_high) else f"{last_high:.6g}"}],[{"text":"Prior low"},{"text":"—" if not np.isfinite(last_low) else f"{last_low:.6g}"}]]}] if n else []),"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"periodKey":keys.astype(int).tolist(),"priorHigh":_finite(period_high),"priorLow":_finite(period_low),"clusterCount":cluster_count.tolist(),"activeClusters":current_clusters,"sweepHigh":sweep_high.tolist(),"sweepLow":sweep_low.tolist(),"acceptHigh":accept_high.tolist(),"acceptLow":accept_low.tolist(),"failedHigh":failed_high.tolist(),"failedLow":failed_low.tolist()}}
