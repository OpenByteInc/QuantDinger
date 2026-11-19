# QuantDinger Super Trend V
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Super Trend V"
my_indicator_description="Builds a volume-spread pressure series, converts it to a price shadow, smooths it over a timeframe-scaled window and applies a SuperTrend with regression-band targets."

# @param target_minutes int 720 Smoothing target minutes range=60:10080:1
# @param timeframe str Auto Chart timeframe values=Auto,1m,3m,5m,15m,30m,1H,2H,4H,1D,1W
# @param spread_window int 28 Spread window range=2:300:1
# @param volume_smoothing int 14 Volume smoothing range=1:300:1
# @param supertrend_multiplier float 1 SuperTrend multiplier range=0:100:0.1
# @param supertrend_period int 10 SuperTrend period range=1:300:1
# @param target_multiplier float 2 Target deviation multiplier range=1:20:0.1
# @param target_length int 150 Target regression length range=2:1000:1

target_minutes=max(60,min(10080,int(params.get("target_minutes",720)))); timeframe_param=str(params.get("timeframe","Auto")); spread_window=max(2,min(300,int(params.get("spread_window",28)))); volume_smoothing=max(1,min(300,int(params.get("volume_smoothing",14)))); st_mult=max(0,min(100,float(params.get("supertrend_multiplier",1)))); st_period=max(1,min(300,int(params.get("supertrend_period",10)))); target_mult=max(1,min(20,float(params.get("target_multiplier",2)))); target_length=max(2,min(1000,int(params.get("target_length",150))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2.0/(length+1.0); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
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
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        window=values[i-length+1:i+1]
        if np.all(np.isfinite(window)): out[i]=float(np.mean(window))
    return out
def _stdev(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        window=values[i-length+1:i+1]
        if np.all(np.isfinite(window)): out[i]=float(np.std(window,ddof=0))
    return out
def _cross_over(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(a[1:]>b[1:])&(a[:-1]<=b[:-1])
    return out
def _timeframe_minutes(value):
    key=str(value).strip().upper(); aliases={"1":1,"3":3,"5":5,"15":15,"30":30,"45":45,"60":60,"120":120,"180":180,"240":240,"1M":1,"3M":3,"5M":5,"15M":15,"30M":30,"45M":45,"1H":60,"2H":120,"3H":180,"4H":240,"1D":1440,"D":1440,"1W":10080,"W":10080}
    return aliases.get(key,60)
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame)
chart_timeframe=getattr(globals().get("ctx",None),"timeframe","1H"); timeframe_value=chart_timeframe if timeframe_param=="Auto" else timeframe_param; chart_minutes=_timeframe_minutes(timeframe_value); smoothing_length=max(1,int(round(target_minutes/chart_minutes*7))) if chart_minutes<1440 else 7
range_scaled=(h-l)*100.0; open_close=(c-o)*100.0; volume_per_range=np.divide(volume,range_scaled,out=np.zeros(n),where=np.isfinite(range_scaled)&(np.abs(range_scaled)>1e-12)); spread_volume=open_close*volume_per_range; cumulative=np.cumsum(np.where(np.isfinite(spread_volume),spread_volume,0.0)); pressure=spread_volume+cumulative; price_spread=_stdev(h-l,spread_window); smooth=_sma(pressure,volume_smoothing); pressure_spread=_stdev(pressure-smooth,spread_window); shadow=np.divide(pressure-smooth,pressure_spread,out=np.full(n,np.nan),where=np.isfinite(pressure_spread)&(pressure_spread>1e-12))*price_spread; transformed=np.where(shadow>0,h+shadow,l+shadow); transformed[~np.isfinite(shadow)]=np.nan
pressure_ema=_ema(transformed,smoothing_length); open_ema=_ema(o,smoothing_length); vpt=_ema(transformed,smoothing_length); previous_close=np.r_[np.nan,c[:-1]]; true_range=np.maximum(h-l,np.maximum(np.abs(h-previous_close),np.abs(l-previous_close))); true_range[0]=h[0]-l[0] if n else np.nan; atr=_rma(true_range,st_period); upper_level=vpt-st_mult*atr; lower_level=vpt+st_mult*atr; trailing_up=np.full(n,np.nan); trailing_down=np.full(n,np.nan); trend=np.ones(n,dtype=int); supertrend=np.full(n,np.nan)
for i in range(n):
    if not np.isfinite(upper_level[i]) or not np.isfinite(lower_level[i]): continue
    if i==0 or not np.isfinite(trailing_up[i-1]): trailing_up[i]=upper_level[i]; trailing_down[i]=lower_level[i]; trend[i]=1
    else:
        trailing_up[i]=max(upper_level[i],trailing_up[i-1]) if c[i-1]>trailing_up[i-1] else upper_level[i]; trailing_down[i]=min(lower_level[i],trailing_down[i-1]) if c[i-1]<trailing_down[i-1] else lower_level[i]; trend[i]=1 if c[i]>trailing_down[i-1] else -1 if c[i]<trailing_up[i-1] else trend[i-1]
    supertrend[i]=trailing_up[i] if trend[i]==1 else trailing_down[i]
buy=_cross_over(c,supertrend)&(c>open_ema); sell=_cross_over(supertrend,c)&(c<open_ema); regression=np.full(n,np.nan); deviation=_stdev(c,target_length)*target_mult; x=np.arange(2,target_length+2,dtype=float); sum_x=float(np.sum(x)); sum_x2=float(np.sum(x*x)); denominator=target_length*sum_x2-sum_x*sum_x
for i in range(target_length-1,n):
    window=c[i-target_length+1:i+1]
    if not np.all(np.isfinite(window)) or denominator==0: continue
    sum_y=float(np.sum(window)); slope=(target_length*float(np.dot(window,x))-sum_x*sum_y)/denominator; average=sum_y/target_length; intercept=average-slope*sum_x/target_length+slope; regression[i]=intercept+slope*target_length
target_upper=regression+deviation; target_lower=regression-deviation; target_long=_cross_over(c,target_lower); target_short=_cross_over(target_upper,c)
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Pressure EMA","title":True,"data":_finite(pressure_ema),"color":"#EF5350","type":"line","lineWidth":1,"overlay":True},{"name":"Open EMA","title":True,"data":_finite(open_ema),"color":"#42A5F5","type":"line","lineWidth":2,"overlay":True},{"name":"SuperTrend","title":True,"data":_finite(supertrend),"color":"#00C853","negativeColor":"#FF1744","type":"line","lineWidth":2,"overlay":True},{"name":"Target Upper","title":False,"data":_finite(target_upper),"color":"#66BB6A","type":"line","lineWidth":1,"overlay":True},{"name":"Target Lower","title":False,"data":_finite(target_lower),"color":"#EF5350","type":"line","lineWidth":1,"overlay":True}],"signals":[_events(buy,l,"BUY","#00C853","buy"),_events(sell,h,"SELL","#FF1744","sell"),_events(target_long,l,"TP","#EF5350","event"),_events(target_short,h,"TP","#66BB6A","event")],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"smoothingLength":int(smoothing_length),"spreadVolume":_finite(spread_volume),"pressure":_finite(pressure),"priceSpread":_finite(price_spread),"smoothPressure":_finite(smooth),"pressureSpread":_finite(pressure_spread),"shadow":_finite(shadow),"transformedPrice":_finite(transformed),"pressureEMA":_finite(pressure_ema),"openEMA":_finite(open_ema),"atr":_finite(atr),"upperLevel":_finite(trailing_up),"lowerLevel":_finite(trailing_down),"trend":trend.tolist(),"supertrend":_finite(supertrend),"regression":_finite(regression),"targetUpper":_finite(target_upper),"targetLower":_finite(target_lower),"buy":buy.tolist(),"sell":sell.tolist(),"targetLong":target_long.tolist(),"targetShort":target_short.tolist()}}
