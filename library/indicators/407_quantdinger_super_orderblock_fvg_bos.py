# QuantDinger Super OrderBlock FVG BOS
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Super OrderBlock FVG BOS"
my_indicator_description="Detects order blocks, fair-value gaps, structure breaks, liquidity-sweep order blocks, stacked momentum zones and high-volume candles."

# @param pivot_lookup int 1 Pivot lookup range=1:5:1
# @param show_order_blocks bool true Show order blocks
# @param show_fair_value_gaps bool true Show fair-value gaps
# @param show_structure_breaking_fvg bool true Highlight structure-breaking FVG
# @param show_rejection_blocks bool false Show rejection blocks
# @param show_bos bool false Show break-of-structure zones
# @param show_ppdd bool true Show liquidity-sweep order blocks
# @param show_stacked_ob_fvg bool true Show stacked OB and FVG
# @param show_high_volume_bars bool true Highlight high-volume bars
# @param volume_ema_length int 12 Volume EMA length range=1:200:1
# @param volume_multiplier float 1.5 High-volume multiplier range=1:20:0.1
# @param maximum_zones int 10 Maximum zones of each type range=1:100:1

pivot_lookup=max(1,min(5,int(params.get("pivot_lookup",1)))); show_ob=bool(params.get("show_order_blocks",True)); show_fvg=bool(params.get("show_fair_value_gaps",True)); show_sfvg=bool(params.get("show_structure_breaking_fvg",True)); show_rjb=bool(params.get("show_rejection_blocks",False)); show_bos=bool(params.get("show_bos",False)); show_ppdd=bool(params.get("show_ppdd",True)); show_stack=bool(params.get("show_stacked_ob_fvg",True)); show_hvb=bool(params.get("show_high_volume_bars",True)); volume_length=max(1,min(200,int(params.get("volume_ema_length",12)))); volume_mult=max(1,min(20,float(params.get("volume_multiplier",1.5)))); maximum=max(1,min(100,int(params.get("maximum_zones",10))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2.0/(length+1.0); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame)
pivot_high=np.full(n,np.nan); pivot_low=np.full(n,np.nan); top=np.full(n,np.nan); bottom=np.full(n,np.nan); last_top=last_bottom=np.nan
for confirmed in range(2*pivot_lookup,n):
    center=confirmed-pivot_lookup; high_window=h[center-pivot_lookup:center+pivot_lookup+1]; low_window=l[center-pivot_lookup:center+pivot_lookup+1]
    if np.isfinite(h[center]) and h[center]==np.nanmax(high_window): pivot_high[confirmed]=h[center]; last_top=float(h[center])
    if np.isfinite(l[center]) and l[center]==np.nanmin(low_window): pivot_low[confirmed]=l[center]; last_bottom=float(l[center])
    top[confirmed]=last_top; bottom[confirmed]=last_bottom
for i in range(min(2*pivot_lookup,n)):
    top[i]=np.nan; bottom[i]=np.nan
for i in range(1,n):
    if not np.isfinite(top[i]): top[i]=top[i-1]
    if not np.isfinite(bottom[i]): bottom[i]=bottom[i-1]
is_up=c>o; is_down=c<o; bull_ob=np.zeros(n,bool); bear_ob=np.zeros(n,bool); bull_fvg=np.zeros(n,bool); bear_fvg=np.zeros(n,bool); bull_rjb=np.zeros(n,bool); bear_rjb=np.zeros(n,bool); bull_bos=np.zeros(n,bool); bear_bos=np.zeros(n,bool); bull_ppdd=np.zeros(n,bool); bear_ppdd=np.zeros(n,bool); bull_stack=np.zeros(n,bool); bear_stack=np.zeros(n,bool)
zones={"Bullish OB":[],"Bearish OB":[],"Bullish FVG":[],"Bearish FVG":[],"Bullish RJB":[],"Bearish RJB":[],"Bullish BOS":[],"Bearish BOS":[]}
active=[]
def _zone(kind,start,end,zone_top,zone_bottom,color,text):
    item={"kind":kind,"start":int(start),"end":int(end),"top":float(max(zone_top,zone_bottom)),"bottom":float(min(zone_top,zone_bottom)),"color":color,"text":text,"mitigated":False,"hidden":False}; zones[kind].append(item); active.append(item)
    if len(zones[kind])>maximum:
        old=zones[kind].pop(0); old["hidden"]=True
    return item
for i in range(n):
    if i>=2:
        bull_ob[i]=bool(is_down[i-2] and is_up[i-1] and c[i-1]>h[i-2]); bear_ob[i]=bool(is_up[i-2] and is_down[i-1] and c[i-1]<l[i-2]); bull_fvg[i]=bool(l[i]>h[i-2]); bear_fvg[i]=bool(h[i]<l[i-2])
        if show_ob and bull_ob[i]: _zone("Bullish OB",i-2,i,h[i-2],min(l[i-2],l[i-1]),"#16A085","OB+")
        if show_ob and bear_ob[i]: _zone("Bearish OB",i-2,i,max(h[i-2],h[i-1]),l[i-2],"#E74C3C","OB-")
        if bull_fvg[i] and (show_fvg or show_sfvg):
            structural=show_sfvg and np.isfinite(top[i]) and c[i-1]>top[i] and l[i-1]<top[i] and h[i-2]<top[i] and l[i]>top[i]; _zone("Bullish FVG",i-2,i,l[i],h[i-2],"#2962FF" if structural else "#5C6BC0","FVG+")
        if bear_fvg[i] and (show_fvg or show_sfvg):
            structural=show_sfvg and np.isfinite(bottom[i]) and c[i-1]<bottom[i] and h[i-1]>bottom[i] and l[i-2]>bottom[i] and h[i]<bottom[i]; _zone("Bearish FVG",i-2,i,l[i-2],h[i],"#2962FF" if structural else "#5C6BC0","FVG-")
        if show_rjb and bear_ob[i]:
            if h[i-1] < c[i-2]+0.2*(h[i-2]-c[i-2]): bear_rjb[i]=True; _zone("Bearish RJB",i-2,i,h[i-2],c[i-2],"#D35400","RJB-")
            elif h[i-1]>h[i-2]: bear_rjb[i]=True; _zone("Bearish RJB",i-1,i,h[i-1],o[i-1],"#D35400","RJB-")
        if show_rjb and bull_ob[i]:
            if l[i-1] > c[i-2]-0.2*(c[i-2]-l[i-2]): bull_rjb[i]=True; _zone("Bullish RJB",i-2,i,c[i-2],l[i-2],"#27AE60","RJB+")
            elif l[i-1]<l[i-2]: bull_rjb[i]=True; _zone("Bullish RJB",i-1,i,o[i-1],l[i-1],"#27AE60","RJB+")
        bull_stack[i]=show_stack and bull_fvg[i] and bull_ob[i]; bear_stack[i]=show_stack and bear_fvg[i] and bear_ob[i]
    if i>=1:
        ob_up_now=is_down[i-1] and is_up[i] and c[i]>h[i-1]; ob_down_now=is_up[i-1] and is_down[i] and c[i]<l[i-1]
        previous_top=top[i-1] if i else np.nan; previous_bottom=bottom[i-1] if i else np.nan
        bear_ppdd[i]=show_ppdd and ob_down_now and ((np.isfinite(top[i]) and max(h[i],h[i-1])>top[i] and c[i]<top[i]) or (np.isfinite(previous_top) and max(h[i],h[i-1])>previous_top and c[i]<previous_top))
        bull_ppdd[i]=show_ppdd and ob_up_now and ((np.isfinite(bottom[i]) and min(l[i],l[i-1])<bottom[i] and c[i]>bottom[i]) or (np.isfinite(previous_bottom) and min(l[i],l[i-1])<previous_bottom and c[i]>previous_bottom))
        if show_bos and np.isfinite(top[i]) and np.isfinite(bottom[i]):
            bull_bos[i]=np.isfinite(top[i-1]) and c[i]>top[i] and c[i-1]<=top[i-1]; bear_bos[i]=np.isfinite(bottom[i-1]) and c[i]<bottom[i] and c[i-1]>=bottom[i-1]
            if bull_bos[i]: _zone("Bullish BOS",i,i+1,top[i],bottom[i],"#00A86B","BOS+")
            if bear_bos[i]: _zone("Bearish BOS",i,i+1,top[i],bottom[i],"#C62828","BOS-")
    for item in active:
        if item["hidden"] or item["mitigated"] or item["start"]>=i: continue
        crossed=(h[i]>item["bottom"] and l[i]<item["bottom"]) or (h[i]>item["top"] and l[i]<item["top"])
        if crossed: item["mitigated"]=True; item["end"]=int(i)
        else: item["end"]=int(i)
volume_ema=_ema(volume,volume_length); high_volume=np.isfinite(volume_ema)&(volume>volume_mult*volume_ema); bar_colors=[("#00A86B" if is_up[i] else "#D32F2F") if show_hvb and high_volume[i] else None for i in range(n)]
layers=[]
visible_zones=sorted([item for group in zones.values() for item in group if not item["hidden"]],key=lambda item:item["start"])
active_zones=[item for item in visible_zones if not item["mitigated"]][-8:]
active_ids={id(item) for item in active_zones}
recent_zones=[item for item in visible_zones if id(item) not in active_ids][-4:]
for item in sorted(recent_zones+active_zones,key=lambda value:value["start"]):
    layers.append({"type":"zone","role":item["kind"],"startIndex":item["start"],"endIndex":item["end"],"top":item["top"],"bottom":item["bottom"],"fillColor":"#808080" if item["mitigated"] else item["color"],"borderColor":item["color"],"opacity":0.10 if item["mitigated"] else 0.18,"text":item["text"]})
def _thin(mask,limit=4,min_gap=5):
    selected=[]
    for index in np.flatnonzero(mask)[::-1]:
        if all(abs(int(index)-other)>=min_gap for other in selected): selected.append(int(index))
        if len(selected)>=limit: break
    output_mask=np.zeros(n,bool); output_mask[selected]=True
    return output_mask
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
signals=[_events(_thin(bull_ppdd),l,"PPDD+","#00C853","buy"),_events(_thin(bear_ppdd),h,"PPDD-","#FF1744","sell"),_events(_thin(bull_stack),l,"◆","#00E676","buy"),_events(_thin(bear_stack),h,"◆","#FF5252","sell")]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Pivot Resistance","title":True,"data":_finite(top),"color":"#B0BEC5","type":"line","lineWidth":1,"lineStyle":"step","overlay":True},{"name":"Pivot Support","title":True,"data":_finite(bottom),"color":"#B0BEC5","type":"line","lineWidth":1,"lineStyle":"step","overlay":True}],"signals":signals,"layers":layers,"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"top":_finite(top),"bottom":_finite(bottom),"bullishOrderBlock":bull_ob.tolist(),"bearishOrderBlock":bear_ob.tolist(),"bullishFVG":bull_fvg.tolist(),"bearishFVG":bear_fvg.tolist(),"bullishBOS":bull_bos.tolist(),"bearishBOS":bear_bos.tolist(),"bullishPPDD":bull_ppdd.tolist(),"bearishPPDD":bear_ppdd.tolist(),"bullishStack":bull_stack.tolist(),"bearishStack":bear_stack.tolist(),"volumeEMA":_finite(volume_ema),"highVolume":high_volume.tolist(),"zones":[item for group in zones.values() for item in group if not item["hidden"]]}}
