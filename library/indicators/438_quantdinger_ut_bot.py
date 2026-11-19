# QuantDinger UT Bot
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger UT Bot"
my_indicator_description="Maintains a direction-aware ATR trailing stop and emits signals whenever price crosses the stop."

# @param sensitivity float 3 ATR sensitivity range=0.1:50:0.1
# @param atr_period int 10 ATR period range=1:500:1
# @param color_bars bool true Color bars by trailing stop

sensitivity=max(0.1,min(50,float(params.get("sensitivity",3)))); period=max(1,min(500,int(params.get("atr_period",10)))); color_bars=bool(params.get("color_bars",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _rma(values,length):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1)+float(value))/length; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,period); loss=sensitivity*atr; trail=np.full(n,np.nan); position=np.zeros(n,dtype=int); buy=np.zeros(n,bool); sell=np.zeros(n,bool); stop=0.0; state=0
for i in range(n):
    if not np.isfinite(loss[i]): continue
    old=stop; old_price=c[i-1] if i else c[i]
    if c[i]>old and old_price>old: stop=max(old,c[i]-loss[i])
    elif c[i]<old and old_price<old: stop=min(old,c[i]+loss[i])
    else: stop=c[i]-loss[i] if c[i]>old else c[i]+loss[i]
    if i and old_price<old and c[i]>old: state=1
    elif i and old_price>old and c[i]<old: state=-1
    trail[i]=stop; position[i]=state; buy[i]=i>0 and c[i]>stop and c[i-1]<=trail[i-1] if np.isfinite(trail[i-1]) else False; sell[i]=i>0 and c[i]<stop and c[i-1]>=trail[i-1] if np.isfinite(trail[i-1]) else False
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"ATR Trailing Stop","title":True,"data":_finite(trail),"color":"#4CAF50","negativeColor":"#F44336","type":"line","lineWidth":2,"overlay":True}],"signals":[_events(buy,l,"BUY","#4CAF50","buy"),_events(sell,h,"SELL","#F44336","sell")],"layers":[],"tables":[],"bar_colors":["rgba(76,175,80,0.80)" if color_bars and np.isfinite(trail[i]) and c[i]>trail[i] else "rgba(244,67,54,0.80)" if color_bars and np.isfinite(trail[i]) else None for i in range(n)],"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"loss":_finite(loss),"trailingStop":_finite(trail),"position":position.tolist(),"buy":buy.tolist(),"sell":sell.tolist()}}
