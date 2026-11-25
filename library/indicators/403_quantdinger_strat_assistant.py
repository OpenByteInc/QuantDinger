# QuantDinger Strat Assistant
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Strat Assistant"
my_indicator_description="Classifies 1/2-up/2-down/3 candles, detects common Strat sequences and summarizes same-symbol full-timeframe continuity."
indicator_timeframes=["15m","30m","1H","4H","1D","1W","1M","3M"]

# @param action_wick_percentage float 0.75 Actionable wick percentage range=0.1:0.95:0.05
# @param show_previous_day_high bool true Show previous-day high
# @param show_previous_day_low bool true Show previous-day low
# @param show_previous_week_high bool false Show previous-week high
# @param show_previous_week_low bool false Show previous-week low
# @param show_full_timeframe_continuity bool true Show full-timeframe continuity
# @param maximum_signal_labels int 16 Maximum visible pattern labels range=4:40:1
# @param minimum_signal_spacing int 3 Minimum bars between labels of one type range=1:20:1

wick_percentage=max(0.1,min(0.95,float(params.get("action_wick_percentage",0.75)))); show_day_high=bool(params.get("show_previous_day_high",True)); show_day_low=bool(params.get("show_previous_day_low",True)); show_week_high=bool(params.get("show_previous_week_high",False)); show_week_low=bool(params.get("show_previous_week_low",False)); show_ftc=bool(params.get("show_full_timeframe_continuity",True)); maximum_labels=max(4,min(40,int(params.get("maximum_signal_labels",16)))); minimum_spacing=max(1,min(20,int(params.get("minimum_signal_spacing",3))))
def _normalize(frame):
    out=frame.copy().reset_index(drop=True)
    for col in ("time","open","high","low","close","volume"):
        if col not in out.columns: out[col]=0.0
        out[col]=pd.to_numeric(out[col],errors="coerce")
    t=out["time"].to_numpy(float)
    if len(t) and np.nanmax(np.abs(t))>1e10: out["time"]=t/1000
    return out
def _classify(high,low):
    out=np.zeros(len(high),dtype=int)
    for i in range(1,len(high)):
        out[i]=1 if high[i]<=high[i-1] and low[i]>=low[i-1] else 3 if high[i]>high[i-1] and low[i]<low[i-1] else 2 if high[i]>high[i-1] else -2 if low[i]<low[i-1] else 0
    return out
runtime_ctx=ctx
def _aligned_state(time,timeframe):
    raw=None
    if hasattr(runtime_ctx,"raw_series"):
        try: raw=runtime_ctx.raw_series(timeframe)
        except Exception: raw=None
    direction=np.zeros(len(time),dtype=int); kind=np.zeros(len(time),dtype=int)
    if raw is None or not len(raw): return direction,kind,False,None
    remote=_normalize(raw); rt=remote["time"].to_numpy(float); ro=remote["open"].to_numpy(float); rc=remote["close"].to_numpy(float); rk=_classify(remote["high"].to_numpy(float),remote["low"].to_numpy(float))
    for i,t in enumerate(time):
        j=int(np.searchsorted(rt,t,side="right")-1)
        if j>=0: direction[i]=1 if rc[j]>=ro[j] else -1; kind[i]=rk[j]
    return direction,kind,True,remote
def _pattern(classification,index,sequence):
    if index-len(sequence)+1<0: return False
    return tuple(classification[index-len(sequence)+1:index+1])==tuple(sequence)

frame=_normalize(df); time=frame["time"].to_numpy(float); opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame); kind=_classify(high,low)
patterns={name:np.zeros(n,bool) for name in ("222Bull","222Bear","212Bull","212Bear","22Bull","22Bear","322Bull","322Bear","32Bull","32Bear","312Bull","312Bear","122Bull","122Bear","2222Bull","2222Bear","3Bull","3Bear")}
for i in range(n):
    tests={"222Bull":(2,2,2),"222Bear":(-2,-2,-2),"212Bull":(2,1,2),"212Bear":(-2,1,-2),"22Bull":(-2,2),"22Bear":(2,-2),"322Bull":(3,-2,2),"322Bear":(3,2,-2),"32Bull":(3,2),"32Bear":(3,-2),"312Bull":(3,1,2),"312Bear":(3,1,-2),"122Bull":(1,-2,2),"122Bear":(1,2,-2),"2222Bull":(2,2,-2,2),"2222Bear":(-2,-2,2,-2)}
    for name,seq in tests.items(): patterns[name][i]=_pattern(kind,i,seq)
    patterns["3Bull"][i]=kind[i]==3 and i>0 and close[i]>high[i-1]; patterns["3Bear"][i]=kind[i]==3 and i>0 and close[i]<low[i-1]
bar_range=high-low; shooter=(opened<high-bar_range*wick_percentage)&(close<high-bar_range*wick_percentage)&(kind!=1); hammer=(opened>low+bar_range*wick_percentage)&(close>low+bar_range*wick_percentage)&(kind!=1); inside=kind==1
timeframes=("15m","30m","1H","4H","1D","1W","1M","3M"); ftc={}; availability={}; remote_frames={}
for tf in timeframes:
    direction,remote_kind,available,remote=_aligned_state(time,tf); ftc[tf]={"direction":direction,"kind":remote_kind}; availability[tf]=available; remote_frames[tf]=remote
prev_day_high=np.full(n,np.nan); prev_day_low=np.full(n,np.nan); prev_week_high=np.full(n,np.nan); prev_week_low=np.full(n,np.nan)
for tf,target_high,target_low in (("1D",prev_day_high,prev_day_low),("1W",prev_week_high,prev_week_low)):
    remote=remote_frames.get(tf)
    if remote is not None:
        rt=remote["time"].to_numpy(float); rh=remote["high"].to_numpy(float); rl=remote["low"].to_numpy(float)
        for i,t in enumerate(time):
            j=int(np.searchsorted(rt,t,side="right")-2)
            if j>=0: target_high[i]=rh[j]; target_low[i]=rl[j]
layers=[]
for name,values,color,visible in (("Previous Day High",prev_day_high,"#00FF00",show_day_high),("Previous Day Low",prev_day_low,"#800000",show_day_low),("Previous Week High",prev_week_high,"#FFA500",show_week_high),("Previous Week Low",prev_week_low,"#800080",show_week_low)):
    if visible and n and np.isfinite(values[-1]): layers.append({"type":"line","role":name,"startIndex":max(0,n-200),"endIndex":n+2,"y1":float(values[-1]),"y2":float(values[-1]),"color":color,"lineWidth":1,"lineStyle":"dashed"})
bullish=np.zeros(n,bool); bearish=np.zeros(n,bool)
for name,values in patterns.items():
    if name.endswith("Bull"): bullish|=values
    elif name.endswith("Bear"): bearish|=values
def _display_mask(values,limit,spacing):
    selected=[]
    for index in np.flatnonzero(values):
        if not selected or index-selected[-1]>=spacing: selected.append(int(index))
    visible=np.zeros(n,bool)
    for index in selected[-limit:]: visible[index]=True
    return visible
labels_per_type=max(1,maximum_labels//4); bullish_display=_display_mask(bullish&~hammer,labels_per_type,minimum_spacing); bearish_display=_display_mask(bearish&~shooter,labels_per_type,minimum_spacing); hammer_display=_display_mask(hammer,labels_per_type,minimum_spacing); shooter_display=_display_mask(shooter,labels_per_type,minimum_spacing)
bar_colors=["#F6BE00" if kind[i]==1 else "#FF00FF" if kind[i]==3 else "#008000" if kind[i]==2 else "#FF0000" if kind[i]==-2 else None for i in range(n)]
tables=[]
if n and show_ftc:
    rows=[]
    for tf in timeframes:
        direction=ftc[tf]["direction"][-1]; value_kind=ftc[tf]["kind"][-1]; text="Unavailable" if not availability[tf] else ("IN" if value_kind==1 else "OUT" if value_kind==3 else "Green" if direction==1 else "Red"); rows.append([{"text":tf},{"text":text,"color":"#F6BE00" if value_kind==1 else "#FF00FF" if value_kind==3 else "#008000" if direction==1 else "#FF0000"}])
    tables=[{"title":"Full Timeframe Continuity","position":"top-right","compact":True,"rows":rows}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Price Guide","title":False,"data":[float(value) if np.isfinite(value) else None for value in close],"color":"rgba(0,0,0,0)","type":"line","overlay":True}],"signals":[{"type":"buy","name":"Bullish Strat","text":"STRAT","data":[{"active":True,"price":float(low[i]),"text":"STRAT","color":"#008000"} if bullish_display[i] else None for i in range(n)],"color":"#008000","renderMode":"events","overlay":True},{"type":"sell","name":"Bearish Strat","text":"STRAT","data":[{"active":True,"price":float(high[i]),"text":"STRAT","color":"#FF0000"} if bearish_display[i] else None for i in range(n)],"color":"#FF0000","renderMode":"events","overlay":True},{"type":"buy","name":"Hammer","text":"Hammer","data":[{"active":True,"price":float(low[i]),"text":"Hammer","color":"#008000"} if hammer_display[i] else None for i in range(n)],"color":"#008000","renderMode":"events","overlay":True},{"type":"sell","name":"Shooter","text":"Shooter","data":[{"active":True,"price":float(high[i]),"text":"Shooter","color":"#FF0000"} if shooter_display[i] else None for i in range(n)],"color":"#FF0000","renderMode":"events","overlay":True}],"layers":layers,"tables":tables,"bar_colors":bar_colors,"background_colors":[None]*n,
"calculatedVars":{"barType":kind.tolist(),"inside":inside.tolist(),"twoUp":(kind==2).tolist(),"twoDown":(kind==-2).tolist(),"outside":(kind==3).tolist(),"hammer":hammer.tolist(),"shooter":shooter.tolist(),"bullishDisplay":bullish_display.tolist(),"bearishDisplay":bearish_display.tolist(),"hammerDisplay":hammer_display.tolist(),"shooterDisplay":shooter_display.tolist(),"patterns":{name:values.tolist() for name,values in patterns.items()},"ftc":{tf:{"direction":ftc[tf]["direction"].tolist(),"kind":ftc[tf]["kind"].tolist(),"available":availability[tf]} for tf in timeframes},"previousDayHigh":[float(x) if np.isfinite(x) else None for x in prev_day_high],"previousDayLow":[float(x) if np.isfinite(x) else None for x in prev_day_low],"previousWeekHigh":[float(x) if np.isfinite(x) else None for x in prev_week_high],"previousWeekLow":[float(x) if np.isfinite(x) else None for x in prev_week_low]}}
