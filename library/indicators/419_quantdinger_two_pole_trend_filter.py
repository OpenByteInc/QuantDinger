# QuantDinger Two-Pole Trend Filter
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Two-Pole Trend Filter"
my_indicator_description="Applies a damped second-order price filter, counts persistent rises or falls and marks confirmed trend transitions at ATR-spaced levels."

# @param length int 20 Filter length range=2:300:1
# @param damping float 0.9 Damping range=0.1:1:0.01
# @param persistence int 5 Rising and falling confirmation range=1:30:1
# @param band_multiplier float 1 ATR band multiplier range=0.5:10:0.1
# @param color_bars bool false Color bars
# @param show_signals bool false Show transition signals

length=max(2,min(300,int(params.get("length",20)))); damping=max(0.1,min(1,float(params.get("damping",0.9)))); persistence=max(1,min(30,int(params.get("persistence",5)))); band=max(0.5,min(10,float(params.get("band_multiplier",1)))); color_bars=bool(params.get("color_bars",False)); show_signals=bool(params.get("show_signals",False))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _rma(values,window):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==window: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(window-1)+float(value))/window; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,c[:-1]]; true_range=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: true_range[0]=h[0]-l[0]
atr=_rma(true_range,200)*band; omega=2*np.pi/length; alpha=damping*omega; beta=omega**2; first=np.full(n,np.nan); filtered=np.full(n,np.nan); first_state=0.0; second_state=0.0
for i in range(n): first_state=first_state+alpha*(c[i]-first_state); second_state=second_state+beta*(first_state-second_state); first[i]=first_state; filtered[i]=second_state
rising=np.zeros(n,dtype=int); falling=np.zeros(n,dtype=int); up=np.zeros(n,bool); down=np.zeros(n,bool)
for i in range(2,n):
    up[i]=filtered[i]>filtered[i-2]; down[i]=filtered[i]<filtered[i-2]
    if up[i]: rising[i]=rising[i-1]+1; falling[i]=0
    elif down[i]: rising[i]=0; falling[i]=falling[i-1]+1
    else: rising[i]=rising[i-1]; falling[i]=falling[i-1]
signal_up=np.zeros(n,bool); signal_down=np.zeros(n,bool)
if n>1:
    signal_up[1:]=(rising[1:]>=persistence)&(rising[:-1]<persistence); signal_down[1:]=(falling[1:]>=persistence)&(falling[:-1]<persistence)
up_level=filtered-atr; down_level=filtered+atr; bar_colors=[]
for i in range(n):
    if not color_bars: bar_colors.append(None)
    elif up[i]: bar_colors.append("#00FF00")
    elif down[i]: bar_colors.append("#FF0000")
    else: bar_colors.append("#FFFF00")
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if show_signals and mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Two-Pole Filter","title":True,"data":_finite(filtered),"color":"#FFFF00","type":"line","lineWidth":3,"overlay":True},{"name":"Rising Level","title":False,"data":_finite(np.where(rising>=persistence,up_level,np.nan)),"color":"#00FF00","type":"line","lineWidth":2,"overlay":True},{"name":"Falling Level","title":False,"data":_finite(np.where(falling>=persistence,down_level,np.nan)),"color":"#FF0000","type":"line","lineWidth":2,"overlay":True}],"signals":[_events(signal_up,up_level,"RISING","#00FF00","buy"),_events(signal_down,down_level,"FALLING","#FF0000","sell")],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"firstPole":_finite(first),"filter":_finite(filtered),"atrBand":_finite(atr),"rising":rising.tolist(),"falling":falling.tolist(),"up":up.tolist(),"down":down.tolist(),"signalUp":signal_up.tolist(),"signalDown":signal_down.tolist(),"upLevel":_finite(up_level),"downLevel":_finite(down_level)}}
