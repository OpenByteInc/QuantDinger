# QuantDinger Structure Break Volume Profile
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Structure Break Volume Profile"
my_indicator_description="Labels BOS and CHoCH breaks and builds a bounded volume profile with POC, HVN and optional LVN nodes over the latest completed CHoCH structure."

# @param fractal_length int 12 Fractal length range=3:51:1
# @param bars_to_right int 5 Projection bars range=1:300:1
# @param require_close_break bool true Require close beyond structure
# @param maximum_broken int 250 Maximum broken structures range=100:400:1
# @param show_last_broken int 0 Show last broken structures range=0:400:1
# @param profile_source_bars int 3000 Profile source bars range=100:10000:1
# @param show_profile bool true Show volume profile
# @param profile_architecture str Ask/Bid Profile architecture values=Ask/Bid,Total Volume
# @param profile_rows int 50 Profile rows range=8:100:1
# @param profile_width int 88 Profile width percent range=10:100:1
# @param profile_position str Left Profile position values=Left,Right
# @param show_poc bool true Show POC
# @param show_hvn bool true Show HVN nodes
# @param hvn_count int 3 Maximum HVN count range=1:8:1
# @param hvn_threshold float 55 HVN percent of POC range=10:95:5
# @param show_lvn bool false Show LVN nodes
# @param lvn_count int 3 Maximum LVN count range=1:8:1
# @param lvn_threshold float 45 LVN percent of POC range=5:90:5

fractal=max(3,min(51,int(params.get("fractal_length",12)))); fractal+=1-fractal%2; strength=fractal//2; right=max(1,min(300,int(params.get("bars_to_right",5)))); require_close=bool(params.get("require_close_break",True)); max_broken=max(100,min(400,int(params.get("maximum_broken",250)))); show_last=max(0,min(400,int(params.get("show_last_broken",0)))); source_bars=max(100,min(10000,int(params.get("profile_source_bars",3000)))); show_profile=bool(params.get("show_profile",True)); architecture=str(params.get("profile_architecture","Ask/Bid")); rows=max(8,min(100,int(params.get("profile_rows",50)))); width=max(10,min(100,int(params.get("profile_width",88)))); position=str(params.get("profile_position","Left")); show_poc=bool(params.get("show_poc",True)); show_hvn=bool(params.get("show_hvn",True)); hvn_count=max(1,min(8,int(params.get("hvn_count",3)))); hvn_threshold=max(10,min(95,float(params.get("hvn_threshold",55)))); show_lvn=bool(params.get("show_lvn",False)); lvn_count=max(1,min(8,int(params.get("lvn_count",3)))); lvn_threshold=max(5,min(90,float(params.get("lvn_threshold",45))))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame); pivot_high=np.full(n,np.nan); pivot_low=np.full(n,np.nan)
for confirmed in range(2*strength,n):
    center=confirmed-strength; wh=h[center-strength:center+strength+1]; wl=l[center-strength:center+strength+1]
    if h[center]==np.max(wh): pivot_high[confirmed]=h[center]
    if l[center]==np.min(wl): pivot_low[confirmed]=l[center]
upper=lower=np.nan; upper_index=lower_index=-1; upper_open=lower_open=False; direction=0; breaks=[]; break_up=np.zeros(n,bool); break_down=np.zeros(n,bool); choch=np.zeros(n,bool); bos=np.zeros(n,bool); active_high=active_low=np.nan; active_high_index=active_low_index=-1; profile=None; poc_series=np.full(n,np.nan); poc_retest=np.zeros(n,bool)
def _build_profile(start,end,low_price,high_price,break_direction):
    start=max(start,end-source_bars+1,0); height=(high_price-low_price)/rows
    if height<=0 or end<=start: return None
    total=np.zeros(rows); ask=np.zeros(rows); bid=np.zeros(rows)
    for i in range(start,end+1):
        candle_low=max(l[i],low_price); candle_high=min(h[i],high_price)
        if candle_high<candle_low: continue
        first=max(0,min(rows-1,int(np.floor((candle_low-low_price)/height)))); last=max(0,min(rows-1,int(np.floor((candle_high-low_price)/height)))); count=max(1,last-first+1); allocated=volume[i]/count; candle_range=max(h[i]-l[i],1e-12); buyer=np.clip((c[i]-l[i])/candle_range,0,1)
        for row in range(first,last+1): total[row]+=allocated; ask[row]+=allocated*buyer; bid[row]+=allocated*(1-buyer)
    if np.max(total)<=0: return None
    poc=int(np.argmax(total)); peak=float(total[poc]); hvn=[]; lvn=[]
    for row in range(1,rows-1):
        if total[row]>=total[row-1] and total[row]>=total[row+1] and total[row]>=peak*hvn_threshold/100: hvn.append(row)
        if total[row]<=total[row-1] and total[row]<=total[row+1] and total[row]<=peak*lvn_threshold/100: lvn.append(row)
    hvn=sorted(hvn,key=lambda x:total[x],reverse=True)[:hvn_count]; lvn=sorted(lvn,key=lambda x:total[x])[:lvn_count]
    return {"start":start,"end":end,"low":float(low_price),"high":float(high_price),"rowHeight":float(height),"total":total.tolist(),"ask":ask.tolist(),"bid":bid.tolist(),"pocRow":poc,"poc":float(low_price+(poc+0.5)*height),"hvn":hvn,"lvn":lvn,"direction":int(break_direction)}
for i in range(n):
    if np.isfinite(pivot_high[i]): upper=float(pivot_high[i]); upper_index=i-strength; upper_open=True; active_high=upper; active_high_index=upper_index
    if np.isfinite(pivot_low[i]): lower=float(pivot_low[i]); lower_index=i-strength; lower_open=True; active_low=lower; active_low_index=lower_index
    up=upper_open and np.isfinite(upper) and ((c[i]>upper) if require_close else (h[i]>upper)); down=lower_open and np.isfinite(lower) and ((c[i]<lower) if require_close else (l[i]<lower))
    if up:
        is_choch=direction==-1; break_up[i]=True; choch[i]=is_choch; bos[i]=not is_choch; direction=1; upper_open=False; breaks.append({"start":upper_index,"end":i,"level":upper,"direction":1,"choch":is_choch})
        if is_choch and np.all(np.isfinite([active_high,active_low])): profile=_build_profile(min(active_high_index,active_low_index),i-1,active_low,active_high,1)
    elif down:
        is_choch=direction==1; break_down[i]=True; choch[i]=is_choch; bos[i]=not is_choch; direction=-1; lower_open=False; breaks.append({"start":lower_index,"end":i,"level":lower,"direction":-1,"choch":is_choch})
        if is_choch and np.all(np.isfinite([active_high,active_low])): profile=_build_profile(min(active_high_index,active_low_index),i-1,active_low,active_high,-1)
    breaks=breaks[-max_broken:]
    if profile is not None:
        poc_series[i]=profile["poc"]
        if i>profile["end"] and l[i]<=profile["poc"]<=h[i] and (i==0 or not np.isfinite(poc_series[i-1]) or not (l[i-1]<=profile["poc"]<=h[i-1])): poc_retest[i]=True
limit=show_last if show_last>0 else max_broken; visible=breaks[-limit:]; layers=[]
for item in visible:
    color="#00C896" if item["direction"]==1 else "#FF4D6D"; layers.append({"type":"line","role":"CHoCH" if item["choch"] else "BOS","startIndex":item["start"],"endIndex":item["end"],"y1":item["level"],"y2":item["level"],"color":color,"lineWidth":1})
    layers.append({"type":"label","role":"Structure","index":int((item["start"]+item["end"])/2),"price":item["level"],"text":"CHoCH" if item["choch"] else "BOS","color":"rgba(0,0,0,0)","textColor":color,"size":"small"})
if profile is not None and show_profile:
    total=np.asarray(profile["total"]); maximum=float(np.max(total)); span=max(2,profile["end"]-profile["start"]); available=max(1,int(span*width/100)); anchor_left=profile["start"] if position=="Left" else profile["end"]-available
    layers.append({"type":"zone","role":"Profile Area","startIndex":profile["start"],"endIndex":profile["end"],"top":profile["high"],"bottom":profile["low"],"fillColor":"#8796A5","borderColor":"#8796A5","opacity":0.06})
    for row,value in enumerate(total):
        if value<=0: continue
        row_width=max(1,int(available*value/maximum)); start=anchor_left if position=="Left" else profile["end"]-row_width; end=start+row_width; bottom=profile["low"]+row*profile["rowHeight"]; top=bottom+profile["rowHeight"]
        if architecture=="Ask/Bid":
            ratio=profile["ask"][row]/value if value else 0.5; split=start+max(1,int(row_width*(1-ratio))); layers.extend([{"type":"zone","role":"Bid Volume","startIndex":start,"endIndex":split,"top":top,"bottom":bottom,"fillColor":"#FF4D6D","borderColor":"#FF4D6D","opacity":0.28},{"type":"zone","role":"Ask Volume","startIndex":split,"endIndex":end,"top":top,"bottom":bottom,"fillColor":"#00C896","borderColor":"#00C896","opacity":0.28}])
        else: layers.append({"type":"zone","role":"Total Volume","startIndex":start,"endIndex":end,"top":top,"bottom":bottom,"fillColor":"#8796A5","borderColor":"#8796A5","opacity":0.30})
    if show_poc: layers.append({"type":"line","role":"POC","startIndex":profile["start"],"endIndex":n+right,"y1":profile["poc"],"y2":profile["poc"],"color":"#FFB627","lineWidth":2,"lineStyle":"dashed"})
    if show_hvn:
        for rank,row in enumerate(profile["hvn"],1):
            price=profile["low"]+(row+0.5)*profile["rowHeight"]; layers.append({"type":"line","role":f"HVN {rank}","startIndex":profile["start"],"endIndex":n+right,"y1":price,"y2":price,"color":"#8B5CF6","lineWidth":1,"lineStyle":"dotted"})
    if show_lvn:
        color="#00C896" if profile["direction"]==1 else "#FF4D6D"
        for rank,row in enumerate(profile["lvn"],1):
            price=profile["low"]+(row+0.5)*profile["rowHeight"]; layers.append({"type":"line","role":f"LVN {rank}","startIndex":profile["start"],"endIndex":n+right,"y1":price,"y2":price,"color":color,"lineWidth":1,"lineStyle":"dotted"})
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Active POC","title":True,"data":_finite(poc_series) if show_poc else [None]*n,"color":"#FFB627","type":"line","lineWidth":2,"lineStyle":"step","overlay":True}],"signals":[{"type":"buy","name":"Bullish Structure Break","text":"BOS","data":[{"active":True,"price":float(l[i]),"text":"CHoCH" if choch[i] else "BOS","color":"#00C896"} if break_up[i] else None for i in range(n)],"color":"#00C896","renderMode":"events","overlay":True},{"type":"sell","name":"Bearish Structure Break","text":"BOS","data":[{"active":True,"price":float(h[i]),"text":"CHoCH" if choch[i] else "BOS","color":"#FF4D6D"} if break_down[i] else None for i in range(n)],"color":"#FF4D6D","renderMode":"events","overlay":True},{"type":"event","name":"POC Retest","text":"POC","data":[{"active":True,"price":float(poc_series[i]),"text":"POC","color":"#FFB627"} if poc_retest[i] else None for i in range(n)],"color":"#FFB627","renderMode":"events","overlay":True}],"layers":layers,"tables":([{"title":"Structure Profile","position":"top-right","compact":True,"rows":[[{"text":"Direction"},{"text":"Bullish" if direction==1 else "Bearish" if direction==-1 else "Waiting"}],[{"text":"Broken structures"},{"text":str(len(breaks))}],[{"text":"POC"},{"text":"—" if profile is None else f"{profile['poc']:.6g}"}],[{"text":"Nodes"},{"text":"—" if profile is None else f"{len(profile['hvn'])} HVN · {len(profile['lvn'])} LVN"}]]}] if n else []),"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"breakUp":break_up.tolist(),"breakDown":break_down.tolist(),"choch":choch.tolist(),"bos":bos.tolist(),"poc":_finite(poc_series),"pocRetest":poc_retest.tolist(),"profile":profile,"brokenStructures":breaks}}
