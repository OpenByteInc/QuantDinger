# QuantDinger Structure-Anchored VWAP
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Structure-Anchored VWAP"
my_indicator_description="Re-anchors a volume-weighted mean and deviation bands on confirmed swing structure, structure breaks, fast extremes or a manual time."

# @param pivot_left int 55 Pivot left strength range=1:200:1
# @param pivot_right int 55 Pivot right strength range=1:200:1
# @param minimum_swing_atr float 1.5 Minimum swing size range=0:10:0.25
# @param atr_length int 13 ATR length range=2:100:1
# @param anchor_mode str Swing Anchor mode values=Swing,Structure break,Fast,Manual
# @param fast_lookback int 30 Fast extreme lookback range=2:500:1
# @param source str hl2 Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param weighting str Cumulative Weighting values=Cumulative,Half-life
# @param half_life int 21 Half-life range=1:500:1
# @param clamp_volume bool true Clamp volume spikes
# @param clamp_multiplier float 4 Volume clamp multiple range=1:50:0.5
# @param band_1_sigma float 0.5 Band 1 sigma range=0.1:10:0.1
# @param show_band_2 bool false Show band 2
# @param band_2_sigma float 2 Band 2 sigma range=0.1:10:0.1
# @param retest_minimum_bars int 5 Retest minimum bars range=1:100:1
# @param retest_tolerance_sigma float 0.25 Retest tolerance range=0:3:0.05
# @param risk_preset str Balanced Risk preset values=Conservative,Balanced,Aggressive,Custom

left=max(1,min(200,int(params.get("pivot_left",55)))); right=max(1,min(200,int(params.get("pivot_right",55)))); min_swing=max(0,min(10,float(params.get("minimum_swing_atr",1.5)))); atr_length=max(2,min(100,int(params.get("atr_length",13)))); mode=str(params.get("anchor_mode","Swing")); fast=max(2,min(500,int(params.get("fast_lookback",30)))); source_name=str(params.get("source","hl2")); weighting=str(params.get("weighting","Cumulative")); half_life=max(1,min(500,int(params.get("half_life",21)))); clamp_volume=bool(params.get("clamp_volume",True)); clamp_mult=max(1,min(50,float(params.get("clamp_multiplier",4)))); band1=max(0.1,min(10,float(params.get("band_1_sigma",0.5)))); show_band2=bool(params.get("show_band_2",False)); band2=max(0.1,min(10,float(params.get("band_2_sigma",2)))); retest_bars=max(1,min(100,int(params.get("retest_minimum_bars",5)))); retest_tol=max(0,min(3,float(params.get("retest_tolerance_sigma",0.25)))); risk=str(params.get("risk_preset","Balanced"))
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
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
time=frame["time"].to_numpy(float); o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame); source={"open":o,"high":h,"low":l,"close":c,"hl2":(h+l)/2,"hlc3":(h+l+c)/3,"ohlc4":(o+h+l+c)/4}.get(source_name,(h+l)/2); previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous))); tr[0]=h[0]-l[0] if n else np.nan; atr=_rma(tr,atr_length)
pivot_high=np.full(n,np.nan); pivot_low=np.full(n,np.nan)
for confirmed in range(left+right,n):
    center=confirmed-right
    if h[center]==np.max(h[center-left:center+right+1]): pivot_high[confirmed]=h[center]
    if l[center]==np.min(l[center-left:center+right+1]): pivot_low[confirmed]=l[center]
anchor_event=np.zeros(n,bool); anchor_direction=np.zeros(n,dtype=int); anchor_index=np.full(n,-1,dtype=int); last_high=last_low=np.nan; last_high_index=last_low_index=-1; current_anchor=-1; current_direction=0
for i in range(n):
    candidate=-1; direction=0
    if np.isfinite(pivot_high[i]) and (not np.isfinite(last_low) or pivot_high[i]-last_low>=min_swing*atr[i]): last_high=float(pivot_high[i]); last_high_index=i-right; candidate=last_high_index; direction=-1
    if np.isfinite(pivot_low[i]) and (not np.isfinite(last_high) or last_high-pivot_low[i]>=min_swing*atr[i]): last_low=float(pivot_low[i]); last_low_index=i-right; candidate=last_low_index; direction=1
    if mode=="Structure break":
        candidate=last_low_index if np.isfinite(last_high) and c[i]>last_high else last_high_index if np.isfinite(last_low) and c[i]<last_low else -1; direction=1 if candidate==last_low_index and candidate>=0 else -1 if candidate==last_high_index and candidate>=0 else 0
    elif mode=="Fast" and i>=fast:
        if h[i]>=np.max(h[i-fast+1:i+1]): candidate=i; direction=-1
        elif l[i]<=np.min(l[i-fast+1:i+1]): candidate=i; direction=1
    elif mode=="Manual":
        manual=float(params.get("manual_time",time[0] if n else 0)); candidate=i if current_anchor<0 and time[i]>=manual else -1; direction=1
    if candidate>=0 and candidate!=current_anchor: current_anchor=candidate; current_direction=direction; anchor_event[i]=True
    anchor_direction[i]=current_direction; anchor_index[i]=current_anchor
median_volume=np.full(n,np.nan)
for i in range(n):
    w=volume[max(0,i-99):i+1]; median_volume[i]=float(np.nanmedian(w)) if len(w) else volume[i]
weights=np.minimum(volume,median_volume*clamp_mult) if clamp_volume else volume; lam=1.0 if weighting=="Cumulative" else float(np.exp(np.log(0.5)/half_life)); vwap=np.full(n,np.nan); sigma=np.full(n,np.nan); sw=sp=sp2=0.0; active_anchor=-1
for i in range(n):
    if anchor_index[i]<0: continue
    if anchor_index[i]!=active_anchor:
        active_anchor=anchor_index[i]; sw=sp=sp2=0.0
        start=max(0,active_anchor)
        for j in range(start,i): sw=lam*sw+weights[j]; sp=lam*sp+source[j]*weights[j]; sp2=lam*sp2+source[j]*source[j]*weights[j]
    sw=lam*sw+weights[i]; sp=lam*sp+source[i]*weights[i]; sp2=lam*sp2+source[i]*source[i]*weights[i]
    if sw>0: vwap[i]=sp/sw; sigma[i]=np.sqrt(max(sp2/sw-vwap[i]*vwap[i],0))
up1=vwap+sigma*band1; down1=vwap-sigma*band1; up2=vwap+sigma*band2; down2=vwap-sigma*band2; cross_up=np.zeros(n,bool); cross_down=np.zeros(n,bool); retest=np.zeros(n,bool)
for i in range(1,n):
    cross_up[i]=np.isfinite(vwap[i]) and c[i]>vwap[i] and c[i-1]<=vwap[i-1] and not anchor_event[i]; cross_down[i]=np.isfinite(vwap[i]) and c[i]<vwap[i] and c[i-1]>=vwap[i-1] and not anchor_event[i]
    age=i-anchor_index[i] if anchor_index[i]>=0 else 0; retest[i]=age>=retest_bars and np.isfinite(sigma[i]) and l[i]<=vwap[i]+sigma[i]*retest_tol and h[i]>=vwap[i]-sigma[i]*retest_tol
buy=(anchor_event&(anchor_direction==1))|(cross_up&retest); sell=(anchor_event&(anchor_direction==-1))|(cross_down&retest); sl_mult,tp1,tp2,tp3={"Conservative":(2,1,2,3),"Balanced":(1.5,1,2,3),"Aggressive":(1,1,1.5,2),"Custom":(float(params.get("stop_atr",1.5)),float(params.get("target_1_r",1)),float(params.get("target_2_r",2)),float(params.get("target_3_r",3)))}.get(risk,(1.5,1,2,3)); layers=[]
for i in np.flatnonzero(buy|sell)[-4:]:
    direction=1 if buy[i] else -1; entry=float(c[i]); stop=entry-direction*sl_mult*atr[i]; r=abs(entry-stop)
    for role,price,color,style in (("Entry",entry,"#5C8AAE","dotted"),("Stop",stop,"#E57373","solid"),("TP1",entry+direction*tp1*r,"#66BB6A","dashed"),("TP2",entry+direction*tp2*r,"#66BB6A","dashed"),("TP3",entry+direction*tp3*r,"#388E3C","solid")): layers.append({"type":"line","role":role,"startIndex":int(i),"endIndex":int(i+20),"y1":float(price),"y2":float(price),"color":color,"lineWidth":1,"lineStyle":style})
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Anchored VWAP","title":True,"data":_finite(vwap),"color":"#5C8AAE","type":"line","lineWidth":2,"overlay":True},{"name":"Upper Band 1","title":True,"data":_finite(up1),"color":"#00E676","type":"line","lineWidth":1,"overlay":True},{"name":"Lower Band 1","title":True,"data":_finite(down1),"color":"#FF5252","type":"line","lineWidth":1,"overlay":True},{"name":"Upper Band 2","title":show_band2,"data":_finite(up2) if show_band2 else [None]*n,"color":"#00E676","type":"line","lineWidth":1,"overlay":True},{"name":"Lower Band 2","title":show_band2,"data":_finite(down2) if show_band2 else [None]*n,"color":"#FF5252","type":"line","lineWidth":1,"overlay":True}],"signals":[{"type":"buy","name":"Buy","text":"BUY","data":[{"active":True,"price":float(l[i]),"text":"BUY","color":"#00E676"} if buy[i] else None for i in range(n)],"color":"#00E676","renderMode":"events","overlay":True},{"type":"sell","name":"Sell","text":"SELL","data":[{"active":True,"price":float(h[i]),"text":"SELL","color":"#FF5252"} if sell[i] else None for i in range(n)],"color":"#FF5252","renderMode":"events","overlay":True}],"layers":layers,"tables":([{"title":"Structure VWAP","position":"top-right","compact":True,"rows":[[{"text":"Mode"},{"text":mode}],[{"text":"Direction"},{"text":"Bullish" if anchor_direction[-1]>0 else "Bearish" if anchor_direction[-1]<0 else "Waiting"}],[{"text":"VWAP"},{"text":"—" if not np.isfinite(vwap[-1]) else f"{vwap[-1]:.6g}"}],[{"text":"Distance"},{"text":"—" if not np.isfinite(sigma[-1]) or sigma[-1]==0 else f"{(c[-1]-vwap[-1])/sigma[-1]:+.2f}σ"}]]}] if n else []),"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"anchorEvent":anchor_event.tolist(),"anchorIndex":anchor_index.tolist(),"anchorDirection":anchor_direction.tolist(),"weight":_finite(weights),"vwap":_finite(vwap),"sigma":_finite(sigma),"upper1":_finite(up1),"lower1":_finite(down1),"upper2":_finite(up2),"lower2":_finite(down2),"crossUp":cross_up.tolist(),"crossDown":cross_down.tolist(),"retest":retest.tolist(),"buy":buy.tolist(),"sell":sell.tolist()}}
