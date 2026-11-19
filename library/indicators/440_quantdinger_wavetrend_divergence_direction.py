# QuantDinger WaveTrend Divergence Direction
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger WaveTrend Divergence Direction"
my_indicator_description="Tracks WaveTrend direction, overbought/oversold crosses, histogram pressure and regular or hidden price divergences confirmed by five-bar oscillator fractals."

# @param channel_length int 10 Channel length range=1:500:1
# @param average_length int 21 Average length range=1:500:1
# @param reaction int 1 Direction reaction range=1:20:1
# @param overbought float 53 Overbought range=0:200:1
# @param oversold float -53 Oversold range=-200:0:1
# @param restrict_to_extremes bool true Restrict crosses to OB/OS
# @param show_regular_divergence bool true Show regular divergences
# @param show_hidden_divergence bool false Show hidden divergences

channel=max(1,min(500,int(params.get("channel_length",10)))); average=max(1,min(500,int(params.get("average_length",21)))); reaction=max(1,min(20,int(params.get("reaction",1)))); overbought=max(0,min(200,float(params.get("overbought",53)))); oversold=max(-200,min(0,float(params.get("oversold",-53)))); restrict=bool(params.get("restrict_to_extremes",True)); show_regular=bool(params.get("show_regular_divergence",True)); show_hidden=bool(params.get("show_hidden_divergence",False))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); ap=(h+l+c)/3; esa=_ema(ap,channel); d=_ema(np.abs(ap-esa),channel); ci=np.divide(ap-esa,0.015*d,out=np.full(n,np.nan),where=d!=0); wt1=_ema(ci,average); wt2=pd.Series(wt1).rolling(4,min_periods=4).mean().to_numpy(float); histogram=wt1-wt2; direction=np.zeros(n,dtype=int); buy=np.zeros(n,bool); sell=np.zeros(n,bool); bull_regular=np.zeros(n,bool); bear_regular=np.zeros(n,bool); bull_hidden=np.zeros(n,bool); bear_hidden=np.zeros(n,bool)
for i in range(n):
    if i>=reaction and np.isfinite(wt1[i]) and np.isfinite(wt1[i-reaction]): direction[i]=1 if wt1[i]>wt1[i-reaction] else -1 if wt1[i]<wt1[i-reaction] else direction[i-1] if i else 0
    elif i: direction[i]=direction[i-1]
    if i and np.all(np.isfinite([wt1[i],wt2[i],wt1[i-1],wt2[i-1]])):
        cross_up=wt1[i]>wt2[i] and wt1[i-1]<=wt2[i-1]; cross_down=wt1[i]<wt2[i] and wt1[i-1]>=wt2[i-1]; buy[i]=cross_up and (not restrict or wt1[i]<=oversold); sell[i]=cross_down and (not restrict or wt1[i]>=overbought)
top_pivots=[]; bottom_pivots=[]
for i in range(2,n-2):
    if not np.all(np.isfinite(wt1[i-2:i+3])): continue
    if wt1[i]>wt1[i-2] and wt1[i]>wt1[i-1] and wt1[i]>wt1[i+1] and wt1[i]>wt1[i+2]:
        if top_pivots:
            p=top_pivots[-1]; bear_regular[i]=show_regular and h[i]>h[p] and wt1[i]<wt1[p]; bear_hidden[i]=show_hidden and h[i]<h[p] and wt1[i]>wt1[p]
        top_pivots.append(i)
    if wt1[i]<wt1[i-2] and wt1[i]<wt1[i-1] and wt1[i]<wt1[i+1] and wt1[i]<wt1[i+2]:
        if bottom_pivots:
            p=bottom_pivots[-1]; bull_regular[i]=show_regular and l[i]<l[p] and wt1[i]>wt1[p]; bull_hidden[i]=show_hidden and l[i]>l[p] and wt1[i]<wt1[p]
        bottom_pivots.append(i)
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":False}
bar_colors=["rgba(0,255,0,0.80)" if buy[i] else "rgba(255,0,0,0.80)" if sell[i] else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"WaveTrend","title":True,"data":_finite(wt1),"color":"#0AAC00","negativeColor":"#FF0000","type":"line","lineWidth":2,"overlay":False},{"name":"Signal","data":_finite(wt2),"color":"#FF8B00","type":"line","lineWidth":1,"overlay":False},{"name":"Histogram","data":_finite(histogram),"color":"rgba(0,155,186,0.55)","type":"area","lineWidth":1,"overlay":False},{"name":"Overbought","data":[overbought]*n,"color":"rgba(255,0,0,0.45)","type":"line","lineWidth":1,"overlay":False},{"name":"Oversold","data":[oversold]*n,"color":"rgba(0,255,19,0.45)","type":"line","lineWidth":1,"overlay":False}],"signals":[_events(buy,wt2,"BUY","#0AAC00","buy"),_events(sell,wt2,"SELL","#FF0000","sell"),_events(bull_regular|bull_hidden,wt1,"BULL DIV","#0AAC00","buy"),_events(bear_regular|bear_hidden,wt1,"BEAR DIV","#FF0000","sell")],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"waveTrend":_finite(wt1),"signal":_finite(wt2),"histogram":_finite(histogram),"direction":direction.tolist(),"buy":buy.tolist(),"sell":sell.tolist(),"regularBullish":bull_regular.tolist(),"regularBearish":bear_regular.tolist(),"hiddenBullish":bull_hidden.tolist(),"hiddenBearish":bear_hidden.tolist(),"topPivots":top_pivots,"bottomPivots":bottom_pivots}}
