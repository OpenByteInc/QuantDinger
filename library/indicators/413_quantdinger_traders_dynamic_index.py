# QuantDinger Traders Dynamic Index
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Traders Dynamic Index"
my_indicator_description="Combines RSI volatility bands, fast and slow RSI averages, directional signals and regular or hidden RSI-price divergences."

# @param rsi_period int 14 RSI period range=1:200:1
# @param band_length int 34 Volatility band length range=1:300:1
# @param fast_rsi_ma int 7 Fast RSI average range=1:100:1
# @param slow_rsi_ma int 2 Slow RSI average range=1:100:1
# @param pivot_left int 5 Divergence pivot left range=1:50:1
# @param pivot_right int 5 Divergence pivot right range=1:50:1
# @param range_min int 5 Minimum divergence separation range=1:200:1
# @param range_max int 60 Maximum divergence separation range=2:500:1
# @param show_hidden_bullish bool false Show hidden bullish divergence
# @param show_hidden_bearish bool false Show hidden bearish divergence

rsi_period=max(1,min(200,int(params.get("rsi_period",14)))); band_length=max(1,min(300,int(params.get("band_length",34)))); fast_length=max(1,min(100,int(params.get("fast_rsi_ma",7)))); slow_length=max(1,min(100,int(params.get("slow_rsi_ma",2)))); left=max(1,min(50,int(params.get("pivot_left",5)))); right=max(1,min(50,int(params.get("pivot_right",5)))); range_min=max(1,min(200,int(params.get("range_min",5)))); range_max=max(range_min+1,min(500,int(params.get("range_max",60)))); show_hidden_bull=bool(params.get("show_hidden_bullish",False)); show_hidden_bear=bool(params.get("show_hidden_bearish",False))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        window=values[i-length+1:i+1]
        if np.all(np.isfinite(window)): out[i]=float(np.mean(window))
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
def _rsi(values,length):
    delta=np.r_[np.nan,np.diff(values)]; gain=np.where(np.isfinite(delta),np.maximum(delta,0),np.nan); loss=np.where(np.isfinite(delta),np.maximum(-delta,0),np.nan); avg_gain=_rma(gain,length); avg_loss=_rma(loss,length); out=np.full(len(values),np.nan); zero_loss=np.isfinite(avg_loss)&(avg_loss==0); out[zero_loss]=100; valid=np.isfinite(avg_gain)&np.isfinite(avg_loss)&(avg_loss>0); out[valid]=100-100/(1+avg_gain[valid]/avg_loss[valid]); return out
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
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); rsi=_rsi(c,rsi_period); middle=_sma(rsi,band_length); offset=1.6185*_stdev(rsi,band_length); upper=middle+offset; lower=middle-offset; fast=_sma(rsi,fast_length); slow=_sma(rsi,slow_length); long_signal=_cross_over(slow,fast)&(fast>middle)&(fast>50); short_signal=_cross_over(fast,slow)&(fast<middle)&(fast<50); best_setup=_cross_over(slow,middle); scalp=slow>middle
pivot_low=np.full(n,np.nan); pivot_high=np.full(n,np.nan); regular_bull=np.zeros(n,bool); hidden_bull=np.zeros(n,bool); regular_bear=np.zeros(n,bool); hidden_bear=np.zeros(n,bool); low_points=[]; high_points=[]
for confirmed in range(left+right,n):
    center=confirmed-right; osc_window=rsi[center-left:center+right+1]
    if np.all(np.isfinite(osc_window)) and rsi[center]==np.min(osc_window):
        pivot_low[center]=rsi[center]
        if low_points:
            previous_index,previous_osc,previous_price=low_points[-1]; separation=center-previous_index
            if range_min<=separation<=range_max: regular_bull[center]=l[center]<previous_price and rsi[center]>previous_osc; hidden_bull[center]=show_hidden_bull and l[center]>previous_price and rsi[center]<previous_osc
        low_points.append((center,float(rsi[center]),float(l[center])))
    if np.all(np.isfinite(osc_window)) and rsi[center]==np.max(osc_window):
        pivot_high[center]=rsi[center]
        if high_points:
            previous_index,previous_osc,previous_price=high_points[-1]; separation=center-previous_index
            if range_min<=separation<=range_max: regular_bear[center]=h[center]>previous_price and rsi[center]<previous_osc; hidden_bear[center]=show_hidden_bear and h[center]<previous_price and rsi[center]>previous_osc
        high_points.append((center,float(rsi[center]),float(h[center])))
def _events(mask,price,text,color,kind,overlay): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":overlay}
background=["rgba(0,255,0,0.05)" if scalp[i] else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"VB Channel High","title":True,"data":_finite(upper),"color":"#12BCC9","type":"line","lineWidth":2,"overlay":False},{"name":"VB Channel Low","title":True,"data":_finite(lower),"color":"#12BCC9","type":"line","lineWidth":2,"overlay":False},{"name":"Market Base Line","title":True,"data":_finite(middle),"color":"#FFA500","type":"line","lineWidth":2,"overlay":False},{"name":"Slow RSI Price Line","title":True,"data":_finite(slow),"color":"#00FF00","type":"line","lineWidth":2,"overlay":False},{"name":"Fast RSI Signal","title":True,"data":_finite(fast),"color":"#FF0000","type":"line","lineWidth":2,"overlay":False},{"name":"Oversold","title":True,"data":[30.0]*n,"color":"#FF0000","type":"line","lineStyle":"dotted","overlay":False},{"name":"Midline","title":True,"data":[50.0]*n,"color":"#FFA500","type":"line","lineStyle":"dotted","overlay":False},{"name":"Overbought","title":True,"data":[70.0]*n,"color":"#00AA00","type":"line","lineStyle":"dotted","overlay":False}],"signals":[_events(long_signal,slow,"LONG","#00FF00","buy",False),_events(short_signal,slow,"SHORT","#FF0000","sell",False),_events(regular_bull,rsi,"BULL","#00A000","buy",False),_events(hidden_bull,rsi,"H BULL","#66BB6A","buy",False),_events(regular_bear,rsi,"BEAR","#D00000","sell",False),_events(hidden_bear,rsi,"H BEAR","#EF5350","sell",False)],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":background,"calculatedVars":{"rsi":_finite(rsi),"upperBand":_finite(upper),"lowerBand":_finite(lower),"middleBand":_finite(middle),"fastRSI":_finite(fast),"slowRSI":_finite(slow),"longSignal":long_signal.tolist(),"shortSignal":short_signal.tolist(),"bestSetup":best_setup.tolist(),"scalp":scalp.tolist(),"pivotLow":_finite(pivot_low),"pivotHigh":_finite(pivot_high),"regularBullish":regular_bull.tolist(),"hiddenBullish":hidden_bull.tolist(),"regularBearish":regular_bear.tolist(),"hiddenBearish":hidden_bear.tolist()}}
