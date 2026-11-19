# QuantDinger Volumatic Support Resistance
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Volumatic Support Resistance"
my_indicator_description="Freezes confirmed rolling price extremes into support and resistance, scales their envelopes by normalized volume and highlights exceptionally active bars."

# @param length int 25 Extreme lookback range=2:500:1
# @param resistance_volume_threshold float 80 Resistance volume threshold range=50:100:1
# @param support_volume_threshold float 80 Support volume threshold range=50:100:1
# @param bar_volume_threshold float 50 Bar volume threshold range=50:100:1

length=max(2,min(500,int(params.get("length",25)))); resistance_threshold=max(50,min(100,float(params.get("resistance_volume_threshold",80)))); support_threshold=max(50,min(100,float(params.get("support_volume_threshold",80)))); bar_threshold=max(50,min(100,float(params.get("bar_volume_threshold",50))))
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
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); rolling_high=pd.Series(h).rolling(length,min_periods=length).max().to_numpy(float); rolling_low=pd.Series(l).rolling(length,min_periods=length).min().to_numpy(float); volume_max=pd.Series(v).rolling(500,min_periods=1).max().to_numpy(float); normalized=np.divide(v,volume_max,out=np.zeros(n),where=volume_max!=0)*100; resistance=np.full(n,np.nan); support=np.full(n,np.nan); resistance_state=np.nan; support_state=np.nan; resistance_new=np.zeros(n,bool); support_new=np.zeros(n,bool)
for i in range(1,n):
    if np.isfinite(rolling_high[i-1]) and h[i-1]==rolling_high[i-1] and h[i]<rolling_high[i]: resistance_state=rolling_high[i]; resistance_new[i]=True
    if np.isfinite(rolling_low[i-1]) and l[i-1]==rolling_low[i-1] and l[i]>rolling_low[i]: support_state=rolling_low[i]; support_new[i]=True
    resistance[i]=resistance_state; support[i]=support_state
previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,200)/100; resistance_upper=resistance+atr*normalized; resistance_lower=resistance-atr*normalized; support_upper=support+atr*normalized; support_lower=support-atr*normalized; max_resistance=normalized>resistance_threshold; max_support=normalized>support_threshold
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Resistance","title":True,"data":_finite(resistance),"color":"#CE2525","type":"line","lineWidth":2,"overlay":True},{"name":"Resistance Upper","data":_finite(resistance_upper),"color":"rgba(206,37,37,0.35)","type":"line","lineWidth":1,"overlay":True},{"name":"Resistance Lower","data":_finite(resistance_lower),"color":"rgba(206,37,37,0.35)","type":"line","lineWidth":1,"overlay":True},{"name":"Support","title":True,"data":_finite(support),"color":"#1CC272","type":"line","lineWidth":2,"overlay":True},{"name":"Support Upper","data":_finite(support_upper),"color":"rgba(28,194,114,0.35)","type":"line","lineWidth":1,"overlay":True},{"name":"Support Lower","data":_finite(support_lower),"color":"rgba(28,194,114,0.35)","type":"line","lineWidth":1,"overlay":True}],"signals":[_events(max_resistance,resistance_upper,"MAX VOL R","#CE2525","event"),_events(max_support,support_lower,"MAX VOL S","#1CC272","event")],"layers":[],"tables":[],"bar_colors":["rgba(255,145,55,0.85)" if normalized[i]>bar_threshold else None for i in range(n)],"background_colors":[None]*n,"calculatedVars":{"rollingHigh":_finite(rolling_high),"rollingLow":_finite(rolling_low),"normalizedVolume":_finite(normalized),"resistance":_finite(resistance),"support":_finite(support),"resistanceNew":resistance_new.tolist(),"supportNew":support_new.tolist(),"resistanceUpper":_finite(resistance_upper),"resistanceLower":_finite(resistance_lower),"supportUpper":_finite(support_upper),"supportLower":_finite(support_lower),"maxVolumeResistance":max_resistance.tolist(),"maxVolumeSupport":max_support.tolist()}}
