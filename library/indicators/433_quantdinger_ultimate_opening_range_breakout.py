# QuantDinger Ultimate Opening Range Breakout
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Ultimate Opening Range Breakout"
my_indicator_description="Builds a configurable opening range, projects three extension targets, records historical target hit rates and manages one-shot breakout signals with an ATR trailing stop."

# @param session_start str 09:30 Opening range start
# @param session_end str 10:00 Opening range end
# @param timezone_offset int -5 Session UTC offset range=-12:12:1
# @param range_source str High/Low Range source values=High/Low,Close
# @param extension_type str Multiples Extension type values=Multiples,Fibonacci
# @param multiplier_1 float 1 Extension multiplier 1 range=0:10:0.1
# @param multiplier_2 float 2 Extension multiplier 2 range=0:10:0.1
# @param multiplier_3 float 3 Extension multiplier 3 range=0:10:0.1
# @param show_extensions bool true Show extensions
# @param show_trailing_stop bool false Show trailing stop
# @param trail_multiplier float 2 Trail ATR multiplier range=0.1:20:0.1
# @param trail_atr_length int 14 Trail ATR length range=1:500:1

start_text=str(params.get("session_start","09:30")); end_text=str(params.get("session_end","10:00")); offset=max(-12,min(12,int(params.get("timezone_offset",-5)))); range_source=str(params.get("range_source","High/Low")); extension_type=str(params.get("extension_type","Multiples")); multipliers=[max(0,min(10,float(params.get("multiplier_1",1)))),max(0,min(10,float(params.get("multiplier_2",2)))),max(0,min(10,float(params.get("multiplier_3",3))))]; show_extensions=bool(params.get("show_extensions",True)); show_trail=bool(params.get("show_trailing_stop",False)); trail_mult=max(0.1,min(20,float(params.get("trail_multiplier",2)))); trail_period=max(1,min(500,int(params.get("trail_atr_length",14))))
if extension_type=="Fibonacci": multipliers=[0.382,0.618,1.0]
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000
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
def _minutes(text):
    pieces=text.replace(".",":").split(":"); return int(pieces[0])*60+int(pieces[1])
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); seconds=_seconds(frame["time"].to_numpy(float)); local=pd.to_datetime(seconds+offset*3600,unit="s",utc=True); minute=np.asarray(local.hour*60+local.minute,dtype=float); dates=np.asarray(local.strftime("%Y-%m-%d")); start_min=_minutes(start_text); end_min=_minutes(end_text); intervals=np.diff(seconds); valid_intervals=intervals[np.isfinite(intervals)&(intervals>0)]; bar_minutes=max(1.0,min(1440.0,float(np.median(valid_intervals))/60.0 if len(valid_intervals) else 60.0)); in_session=(minute<float(end_min))&((minute+bar_minutes)>float(start_min))
previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,trail_period); vol_sma=pd.Series(v).rolling(20,min_periods=20).mean().to_numpy(float); or_high=np.full(n,np.nan); or_low=np.full(n,np.nan); extensions_up=[np.full(n,np.nan) for _ in range(3)]; extensions_down=[np.full(n,np.nan) for _ in range(3)]; breakout_up=np.zeros(n,bool); breakout_down=np.zeros(n,bool); target_up=[np.zeros(n,bool) for _ in range(3)]; target_down=[np.zeros(n,bool) for _ in range(3)]; trail=np.full(n,np.nan); active_direction=np.zeros(n,dtype=int); background=[None]*n; session_profiles=[]; hit_counts_up=[0,0,0]; hit_counts_down=[0,0,0]; total_sessions=0; current_date=None; high_state=np.nan; low_state=np.nan; ended=False; can_up=True; can_down=True; reached_up=[False]*3; reached_down=[False]*3; direction=0; trail_state=np.nan; entry=np.nan; total_profit=0.0; volume_bins={}
for i in range(n):
    new_date=current_date is None or dates[i]!=current_date
    session_start=in_session[i] and (i==0 or not in_session[i-1] or new_date)
    if new_date or session_start:
        current_date=dates[i]; high_state=np.nan; low_state=np.nan; ended=False; can_up=True; can_down=True; reached_up=[False]*3; reached_down=[False]*3; direction=0; trail_state=np.nan; entry=np.nan; volume_bins={}
    if in_session[i]:
        current_high=h[i] if range_source=="High/Low" else max(o[i],c[i]); current_low=l[i] if range_source=="High/Low" else min(o[i],c[i]); high_state=current_high if not np.isfinite(high_state) else max(high_state,current_high); low_state=current_low if not np.isfinite(low_state) else min(low_state,current_low); background[i]="rgba(91,156,246,0.10)"; tick=max((high_state-low_state)/14,1e-9); price=round(c[i]/tick)*tick; volume_bins[price]=volume_bins.get(price,0.0)+float(v[i])
    if i>0 and not in_session[i] and in_session[i-1] and np.isfinite(high_state):
        ended=True; total_sessions+=1; session_profiles.append({"date":str(current_date),"poc":float(max(volume_bins,key=volume_bins.get)) if volume_bins else None,"volume":float(sum(volume_bins.values()))})
    if np.isfinite(high_state):
        or_high[i]=high_state; or_low[i]=low_state; distance=high_state-low_state
        for j,mult in enumerate(multipliers): extensions_up[j][i]=high_state+distance*mult; extensions_down[j][i]=low_state-distance*mult
    if ended and np.isfinite(high_state):
        for j in range(3):
            if not reached_up[j] and h[i]>=extensions_up[j][i]: reached_up[j]=True; target_up[j][i]=True; hit_counts_up[j]+=1
            if not reached_down[j] and l[i]<=extensions_down[j][i]: reached_down[j]=True; target_down[j][i]=True; hit_counts_down[j]+=1
        if i>0 and can_up and c[i]>high_state and c[i-1]<=high_state: breakout_up[i]=True; can_up=False; direction=1; entry=high_state; trail_state=l[i]-atr[i]*trail_mult if np.isfinite(atr[i]) else l[i]
        if i>0 and can_down and c[i]<low_state and c[i-1]>=low_state: breakout_down[i]=True; can_down=False; direction=-1; entry=low_state; trail_state=h[i]+atr[i]*trail_mult if np.isfinite(atr[i]) else h[i]
    if direction==1 and np.isfinite(atr[i]):
        trail_state=max(trail_state,l[i]-atr[i]*trail_mult); trail[i]=trail_state
        if c[i]<trail_state: total_profit+=c[i]-entry; direction=0; trail_state=np.nan
    elif direction==-1 and np.isfinite(atr[i]):
        trail_state=min(trail_state,h[i]+atr[i]*trail_mult); trail[i]=trail_state
        if c[i]>trail_state: total_profit+=entry-c[i]; direction=0; trail_state=np.nan
    active_direction[i]=direction
recent_dates=set(np.unique(dates)[-10:]) if n else set(); visible=np.asarray([value in recent_dates for value in dates],dtype=bool)
def _recent(values): return [float(values[i]) if visible[i] and np.isfinite(values[i]) else None for i in range(n)]
def _thin(mask,limit=4,min_gap=12):
    kept=np.zeros(len(mask),bool); last=None; count=0
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index); count+=1
            if count>=limit: break
    return kept
plots=[{"name":"OR High","data":_recent(or_high),"color":"rgba(91,156,246,0.80)","type":"line","lineWidth":2,"overlay":True},{"name":"OR Low","data":_recent(or_low),"color":"rgba(91,156,246,0.80)","type":"line","lineWidth":2,"overlay":True}]
if show_extensions:
    for j in range(3): plots.extend([{"name":f"Upper Target {j+1}","data":_recent(extensions_up[j]),"color":"rgba(8,153,129,0.60)","type":"line","lineWidth":1,"overlay":True},{"name":f"Lower Target {j+1}","data":_recent(extensions_down[j]),"color":"rgba(242,54,69,0.60)","type":"line","lineWidth":1,"overlay":True}])
if show_trail: plots.append({"name":"Trailing Stop","data":_finite(trail),"color":"#089981","negativeColor":"#F23645","type":"line","lineWidth":2,"overlay":True})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text+(" HV" if np.isfinite(vol_sma[i]) and v[i]>vol_sma[i] else " LV"),"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
signals=[_events(_thin(breakout_up),h,"BULL BREAK","#089981","buy"),_events(_thin(breakout_down),l,"BEAR BREAK","#F23645","sell")]
tables=[]
if n:
    rows=[]
    for j in range(3): rows.extend([[{"text":f"Bull T{j+1}"},{"text":hit_counts_up[j]},{"text":total_sessions},{"text":round(hit_counts_up[j]/max(1,total_sessions)*100,1)}],[{"text":f"Bear T{j+1}"},{"text":hit_counts_down[j]},{"text":total_sessions},{"text":round(hit_counts_down[j]/max(1,total_sessions)*100,1)}]])
    rows.append([{"text":"Trail P/L"},{"text":round(total_profit,6)},{"text":"Sessions"},{"text":total_sessions}]); tables=[{"title":"Opening Range Hit Rate","position":"top-right","compact":True,"rows":rows}]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":signals,"layers":[],"tables":tables,"bar_colors":[None]*n,"background_colors":background,"calculatedVars":{"inSession":in_session.tolist(),"openingHigh":_finite(or_high),"openingLow":_finite(or_low),"upperTargets":[_finite(x) for x in extensions_up],"lowerTargets":[_finite(x) for x in extensions_down],"breakoutUp":breakout_up.tolist(),"breakoutDown":breakout_down.tolist(),"targetUp":[x.tolist() for x in target_up],"targetDown":[x.tolist() for x in target_down],"hitCountsUp":hit_counts_up,"hitCountsDown":hit_counts_down,"totalSessions":total_sessions,"trail":_finite(trail),"activeDirection":active_direction.tolist(),"totalProfit":float(total_profit),"sessionProfiles":session_profiles[-30:]}}
