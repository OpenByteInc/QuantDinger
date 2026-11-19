# QuantDinger TTrades Daily Bias
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger TTrades Daily Bias"
my_indicator_description="Assigns daily and weekly draw-on-liquidity bias from prior-period range interactions, tracks previous-high/low raids and summarizes historical hit and close-through rates."

# @param daily_bias bool true Calculate daily bias
# @param weekly_bias bool false Calculate weekly bias
# @param show_daily_levels bool true Show previous-day levels
# @param show_weekly_levels bool true Show previous-week levels
# @param stop_after_raid bool false Stop extending level after raid
# @param show_statistics bool true Show statistics

daily_enabled=bool(params.get("daily_bias",True)); weekly_enabled=bool(params.get("weekly_bias",False)); show_daily=bool(params.get("show_daily_levels",True)); show_weekly=bool(params.get("show_weekly_levels",True)); stop_after_raid=bool(params.get("stop_after_raid",False)); show_statistics=bool(params.get("show_statistics",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000.0
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); timestamps=pd.to_datetime(_seconds(frame["time"].to_numpy(float)),unit="s",utc=True); daily_keys=np.asarray(timestamps.strftime("%Y-%m-%d")); iso=timestamps.isocalendar(); weekly_keys=np.asarray(iso["year"].astype(str)+"-"+iso["week"].astype(str))
def _bias_engine(keys,enabled):
    bias=np.zeros(n,dtype=int); level_high=np.full(n,np.nan); level_low=np.full(n,np.nan); hit_high=np.zeros(n,bool); hit_low=np.zeros(n,bool); new_period=np.zeros(n,bool); reasons=[None]*n; layers=[]; stats={"biasHigh":0,"biasLow":0,"hitHigh":0,"hitLow":0,"closeHigh":0,"closeLow":0}
    if not enabled or n==0: return bias,level_high,level_low,hit_high,hit_low,new_period,reasons,layers,stats
    groups=[]; start=0
    for i in range(1,n+1):
        if i==n or keys[i]!=keys[start]:
            groups.append({"start":start,"end":i-1,"open":float(o[start]),"high":float(np.max(h[start:i])),"low":float(np.min(l[start:i])),"close":float(c[i-1])}); start=i
    previous_up=False; current_bias=0; active=[]
    for group_index,group in enumerate(groups):
        s=group["start"]; e=group["end"]; new_period[s]=True
        if group_index>=2:
            prior=groups[group_index-1]; older=groups[group_index-2]
            prior_bias=current_bias
            if prior["close"]>older["high"]: stats["closeHigh"]+=1 if prior_bias==1 else 0; current_bias=1; reasons[s]="Close Above Previous High"
            elif prior["close"]<older["low"]: stats["closeLow"]+=1 if prior_bias==-1 else 0; current_bias=-1; reasons[s]="Close Below Previous Low"
            elif older["low"]<prior["close"]<older["high"] and prior["high"]>older["high"] and prior["low"]>older["low"]: current_bias=-1; reasons[s]="Failed Close Above Previous High"
            elif older["low"]<prior["close"]<older["high"] and prior["high"]<older["high"] and prior["low"]<older["low"]: current_bias=1; reasons[s]="Failed Close Below Previous Low"
            elif prior["high"]<=older["high"] and prior["low"]>=older["low"]: current_bias=1 if previous_up else -1; reasons[s]="Inside Period Continuation"
            else: current_bias=0; reasons[s]="Outside Period, No Bias"
            previous_up=prior["close"]>=prior["open"]
        elif group_index==1:
            previous_up=groups[0]["close"]>=groups[0]["open"]
        if group_index>=1:
            previous=groups[group_index-1]; active.append({"startIndex":s,"endIndex":e,"high":previous["high"],"low":previous["low"],"hitHigh":False,"hitLow":False,"bias":current_bias})
            level_high[s:e+1]=previous["high"]; level_low[s:e+1]=previous["low"]
        bias[s:e+1]=current_bias
        if current_bias==1: stats["biasHigh"]+=1
        elif current_bias==-1: stats["biasLow"]+=1
        if active:
            level=active[-1]
            for i in range(s,e+1):
                if not level["hitHigh"] and h[i]>=level["high"]:
                    level["hitHigh"]=True; hit_high[i]=True; stats["hitHigh"]+=1 if current_bias==1 else 0
                    if stop_after_raid: level["endIndex"]=i
                if not level["hitLow"] and l[i]<=level["low"]:
                    level["hitLow"]=True; hit_low[i]=True; stats["hitLow"]+=1 if current_bias==-1 else 0
                    if stop_after_raid: level["endIndex"]=min(level["endIndex"],i)
    for level in active[-30:]:
        layers.extend([{"type":"line","role":"Previous High","startIndex":level["startIndex"],"endIndex":level["endIndex"],"y1":level["high"],"y2":level["high"],"color":"#F44336" if level["hitHigh"] else "#2196F3","lineWidth":1},{"type":"line","role":"Previous Low","startIndex":level["startIndex"],"endIndex":level["endIndex"],"y1":level["low"],"y2":level["low"],"color":"#F44336" if level["hitLow"] else "#2196F3","lineWidth":1}])
    return bias,level_high,level_low,hit_high,hit_low,new_period,reasons,layers,stats
daily=_bias_engine(daily_keys,daily_enabled); weekly=_bias_engine(weekly_keys,weekly_enabled); layers=(daily[7] if show_daily else [])+(weekly[7] if show_weekly else [])
def _thin(mask,limit=4,min_gap=10):
    kept=np.zeros(len(mask),bool); last=None; count=0
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index); count+=1
            if count>=limit: break
    return kept
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
signals=[_events(_thin(daily[3]),h,"PDH RAID","#F44336","event"),_events(_thin(daily[4]),l,"PDL RAID","#F44336","event"),_events(_thin(weekly[3],2,16),h,"PWH RAID","#E91E63","event"),_events(_thin(weekly[4],2,16),l,"PWL RAID","#E91E63","event")]
tables=[]
if n and show_statistics:
    def _pct(hit,total): return round(hit/total*100,1) if total else 0.0
    rows=[]
    for label,result in (("PDH",daily),("PDL",daily),("PWH",weekly),("PWL",weekly)):
        if label.startswith("PD") and not daily_enabled or label.startswith("PW") and not weekly_enabled: continue
        high_side=label.endswith("H"); stats=result[8]; sample=stats["biasHigh"] if high_side else stats["biasLow"]; hit=stats["hitHigh"] if high_side else stats["hitLow"]; close_through=stats["closeHigh"] if high_side else stats["closeLow"]; rows.append([{"text":label},{"text":f"{_pct(hit,sample):.1f}%"},{"text":f"{_pct(close_through,hit):.1f}%"},{"text":sample}])
    tables=[{"title":"Bias / Success / Close Through / Sample","position":"top-right","compact":True,"rows":rows}]
bar_colors=["rgba(0,150,136,0.38)" if daily[0][i]>0 else "rgba(244,67,54,0.38)" if daily[0][i]<0 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Daily Bias","data":daily[0].astype(float).tolist(),"color":"#009688","negativeColor":"#F44336","type":"histogram","lineWidth":1,"overlay":False},{"name":"Weekly Bias","data":weekly[0].astype(float).tolist(),"color":"#26A69A","negativeColor":"#EF5350","type":"histogram","lineWidth":1,"overlay":False}],"signals":signals,"layers":layers,"tables":tables,"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"dailyBias":daily[0].tolist(),"weeklyBias":weekly[0].tolist(),"previousDayHigh":_finite(daily[1]),"previousDayLow":_finite(daily[2]),"previousWeekHigh":_finite(weekly[1]),"previousWeekLow":_finite(weekly[2]),"dailyHitHigh":daily[3].tolist(),"dailyHitLow":daily[4].tolist(),"weeklyHitHigh":weekly[3].tolist(),"weeklyHitLow":weekly[4].tolist(),"newDay":daily[5].tolist(),"newWeek":weekly[5].tolist(),"dailyReasons":daily[6],"weeklyReasons":weekly[6],"dailyStats":daily[8],"weeklyStats":weekly[8]}}
