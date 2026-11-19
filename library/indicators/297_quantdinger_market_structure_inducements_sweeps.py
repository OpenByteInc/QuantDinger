# ============================================================
# QuantDinger Market Structure with Inducements and Sweeps
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Structure with Inducements and Sweeps"
my_indicator_description = "Combines long-horizon CHoCH swings with short-horizon inducements, BOS confirmation, sweeps, and live extensions."

# @param choch_period int 50 CHoCH detection period range=2:500:1
# @param inducement_period int 3 Inducement detection period range=1:100:1
# @param bullish_color color #089981 Bullish elements
# @param bearish_color color #FF5252 Bearish elements
# @param show_choch bool true Show CHoCH
# @param show_bos bool true Show BOS
# @param show_inducements bool true Show inducements
# @param inducement_color color #808080 Inducement color
# @param show_sweeps bool true Show sweeps
# @param sweep_color color #808080 Sweep color
# @param show_swings bool true Show swings

choch_period=min(500,max(2,int(params.get("choch_period",50))));inducement_period=min(100,max(1,int(params.get("inducement_period",3))))
bullish_color=str(params.get("bullish_color","#089981"));bearish_color=str(params.get("bearish_color","#FF5252"));show_choch=bool(params.get("show_choch",True));show_bos=bool(params.get("show_bos",True));show_inducements=bool(params.get("show_inducements",True));inducement_color=str(params.get("inducement_color","#808080"));show_sweeps=bool(params.get("show_sweeps",True));sweep_color=str(params.get("sweep_color","#808080"));show_swings=bool(params.get("show_swings",True))

def _finite(values):return [float(v) if np.isfinite(v) else None for v in values]
def _swings(high,low,period):
    n=len(high);top=np.full(n,np.nan);bottom=np.full(n,np.nan);top_index=np.full(n,-1,int);bottom_index=np.full(n,-1,int);state=np.full(n,-1,int);current=None;last_top=-1;last_bottom=-1
    for index in range(n):
        previous=current
        if index>=period:
            upper=np.max(high[max(0,index-period+1):index+1]);lower=np.min(low[max(0,index-period+1):index+1]);candidate=index-period
            if high[candidate]>upper:current=0
            elif low[candidate]<lower:current=1
        if current is not None:
            state[index]=current
            if previous is not None and current==0 and previous!=0:top[index]=high[index-period];last_top=index-period
            if previous is not None and current==1 and previous!=1:bottom[index]=low[index-period];last_bottom=index-period
        top_index[index]=last_top;bottom_index[index]=last_bottom
    return top,top_index,bottom,bottom_index,state

frame=df.copy().reset_index(drop=True)
if "time" not in frame.columns:frame["time"]=np.arange(len(frame),dtype=float)*3600000.0
if "volume" not in frame.columns:frame["volume"]=0.0
for column in ("time","open","high","low","close","volume"):frame[column]=pd.to_numeric(frame[column],errors="coerce")
frame=frame.dropna(subset=["time","open","high","low","close"]).reset_index(drop=True)
times=frame["time"].to_numpy(float)
if len(times) and np.nanmax(np.abs(times))<100000000000.0:times=times*1000.0
high=frame["high"].to_numpy(float);low=frame["low"].to_numpy(float);close=frame["close"].to_numpy(float);n=len(frame)
top,top_index,bottom,bottom_index,long_swing_state=_swings(high,low,choch_period)
short_top,short_top_index,short_bottom,short_bottom_index,short_swing_state=_swings(high,low,inducement_period)

structure_state=0;top_level=bottom_level=np.nan;top_x=bottom_x=-1;top_crossed=bottom_crossed=False;short_top_level=short_bottom_level=np.nan;short_top_x=short_bottom_x=-1;short_top_crossed=short_bottom_crossed=False;running_max=running_min=np.nan;max_index=min_index=-1
state_history=np.zeros(n,int);choch_up=np.zeros(n,bool);choch_down=np.zeros(n,bool);bull_idm=np.zeros(n,bool);bear_idm=np.zeros(n,bool);bull_bos=np.zeros(n,bool);bear_bos=np.zeros(n,bool);bull_sweep=np.zeros(n,bool);bear_sweep=np.zeros(n,bool);max_history=np.full(n,np.nan);min_history=np.full(n,np.nan);short_top_carried=np.full(n,np.nan);short_bottom_carried=np.full(n,np.nan)
history=[]
for index in range(n):
    if np.isfinite(top[index]):top_level=top[index];top_x=top_index[index];top_crossed=False
    if np.isfinite(bottom[index]):bottom_level=bottom[index];bottom_x=bottom_index[index];bottom_crossed=False
    previous_state=structure_state
    if np.isfinite(top_level) and close[index]>top_level and not top_crossed:structure_state=1;top_crossed=True
    if np.isfinite(bottom_level) and close[index]<bottom_level and not bottom_crossed:structure_state=0;bottom_crossed=True
    if structure_state!=previous_state:
        running_max=high[index];running_min=low[index];max_index=index;min_index=index;short_top_crossed=False;short_bottom_crossed=False
        if structure_state==1:choch_up[index]=True
        else:choch_down[index]=True
        if show_choch:
            start=top_x if structure_state==1 else bottom_x;price=top_level if structure_state==1 else bottom_level;color=bullish_color if structure_state==1 else bearish_color
            history.append(("CHoCH",start,index,price,color,"dashed","down" if structure_state==1 else "up"))
    if np.isfinite(short_top[index]):short_top_level=short_top[index];short_top_x=short_top_index[index]
    if np.isfinite(short_bottom[index]):short_bottom_level=short_bottom[index];short_bottom_x=short_bottom_index[index]
    short_top_carried[index]=short_top_level;short_bottom_carried[index]=short_bottom_level
    if np.isfinite(short_bottom_level) and low[index]<short_bottom_level and not short_bottom_crossed and structure_state==1 and short_bottom_level!=bottom_level:
        bull_idm[index]=True;short_bottom_crossed=True
        if show_inducements:history.append(("IDM",short_bottom_x,index,short_bottom_level,inducement_color,"dotted","up"))
    if np.isfinite(running_max) and close[index]>running_max and short_bottom_crossed and structure_state==1:
        bull_bos[index]=True;short_bottom_crossed=False
        if show_bos:history.append(("BOS",max_index,index,running_max,bullish_color,"solid","down"))
    if np.isfinite(short_top_level) and high[index]>short_top_level and not short_top_crossed and structure_state==0 and short_top_level!=top_level:
        bear_idm[index]=True;short_top_crossed=True
        if show_inducements:history.append(("IDM",short_top_x,index,short_top_level,inducement_color,"dotted","down"))
    if np.isfinite(running_min) and close[index]<running_min and short_top_crossed and structure_state==0:
        bear_bos[index]=True;short_top_crossed=False
        if show_bos:history.append(("BOS",min_index,index,running_min,bearish_color,"solid","up"))
    if show_sweeps and np.isfinite(running_max) and high[index]>running_max and close[index]<running_max and structure_state==1 and index-max_index>1:
        bull_sweep[index]=True;history.append(("x",max_index,index,running_max,sweep_color,"dotted","down"))
    if show_sweeps and np.isfinite(running_min) and low[index]<running_min and close[index]>running_min and structure_state==0 and index-min_index>1:
        bear_sweep[index]=True;history.append(("x",min_index,index,running_min,sweep_color,"dotted","up"))
    previous_max=running_max;previous_min=running_min
    if np.isfinite(running_max):running_max=max(high[index],running_max)
    if np.isfinite(running_min):running_min=min(low[index],running_min)
    if np.isfinite(previous_max) and running_max>previous_max:max_index=index
    if np.isfinite(previous_min) and running_min<previous_min:min_index=index
    max_history[index]=running_max;min_history[index]=running_min;state_history[index]=structure_state

layers=[]
for role,start,end,price,color,style,direction in history:
    layers.append({"type":"line","role":role,"start":float(times[start]),"end":float(times[end]),"y1":float(price),"y2":float(price),"color":color,"lineWidth":1,"dashed":style!="solid","lineStyle":style})
    layers.append({"type":"label","index":int((start+end)/2),"price":float(price),"text":role,"textColor":color,"color":"rgba(0,0,0,0)","direction":direction,"size":"tiny"})
if n:
    if structure_state==1:
        if show_choch and np.isfinite(bottom_level):layers.append({"type":"line","role":"CHoCH Extension","start":float(times[bottom_x]),"end":float(times[-1]),"y1":float(bottom_level),"y2":float(bottom_level),"color":bearish_color,"dashed":True})
        if show_bos and np.isfinite(running_max):layers.append({"type":"line","role":"BOS Extension","start":float(times[max_index]),"end":float(times[-1]),"y1":float(running_max),"y2":float(running_max),"color":bullish_color})
        if show_inducements and not short_bottom_crossed and np.isfinite(short_bottom_level):layers.append({"type":"line","role":"IDM Extension","start":float(times[short_bottom_x]),"end":float(times[-1]+15*(times[-1]-times[-2] if n>1 else 60000)),"y1":float(short_bottom_level),"y2":float(short_bottom_level),"color":inducement_color,"dashed":True})
    else:
        if show_choch and np.isfinite(top_level):layers.append({"type":"line","role":"CHoCH Extension","start":float(times[top_x]),"end":float(times[-1]),"y1":float(top_level),"y2":float(top_level),"color":bullish_color,"dashed":True})
        if show_bos and np.isfinite(running_min):layers.append({"type":"line","role":"BOS Extension","start":float(times[min_index]),"end":float(times[-1]),"y1":float(running_min),"y2":float(running_min),"color":bearish_color})
        if show_inducements and not short_top_crossed and np.isfinite(short_top_level):layers.append({"type":"line","role":"IDM Extension","start":float(times[short_top_x]),"end":float(times[-1]+15*(times[-1]-times[-2] if n>1 else 60000)),"y1":float(short_top_level),"y2":float(short_top_level),"color":inducement_color,"dashed":True})
top_mark=[None]*n;bottom_mark=[None]*n
if show_swings:
    for i in range(n):
        if np.isfinite(top[i]):top_mark[top_index[i]]=float(top[i])
        if np.isfinite(bottom[i]):bottom_mark[bottom_index[i]]=float(bottom[i])
plots=[{"name":"Swing High","data":top_mark,"color":f"{bearish_color}80","type":"scatter","size":5,"overlay":True},{"name":"Swing Low","data":bottom_mark,"color":f"{bullish_color}80","type":"scatter","size":5,"overlay":True},{"name":"State","title":False,"data":_finite(close),"color":"rgba(0,0,0,0)","type":"line","overlay":True}]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[],"layers":layers,"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"top":_finite(top),"bottom":_finite(bottom),"shortTop":_finite(short_top),"shortBottom":_finite(short_bottom),"longSwingState":long_swing_state.tolist(),"shortSwingState":short_swing_state.tolist(),"structureState":state_history.tolist(),"chochUp":choch_up.tolist(),"chochDown":choch_down.tolist(),"bullIDM":bull_idm.tolist(),"bearIDM":bear_idm.tolist(),"bullBOS":bull_bos.tolist(),"bearBOS":bear_bos.tolist(),"bullSweep":bull_sweep.tolist(),"bearSweep":bear_sweep.tolist(),"runningMax":_finite(max_history),"runningMin":_finite(min_history),"shortTopCarried":_finite(short_top_carried),"shortBottomCarried":_finite(short_bottom_carried)}}
