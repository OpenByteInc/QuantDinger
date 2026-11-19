# QuantDinger AlgoAlpha Trend Targets
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger AlgoAlpha Trend Targets"
my_indicator_description="Smooths the midpoint of trailing SuperTrend bands, confirms line-direction changes and creates ATR stop-loss plus three R-multiple target plans with rejection signals."

# @param supertrend_factor float 12 SuperTrend factor range=1:50:0.5
# @param supertrend_atr_period int 90 SuperTrend ATR period range=1:500:1
# @param wma_length int 40 WMA length range=1:300:1
# @param ema_length int 14 EMA length range=1:300:1
# @param confirmation_count int 3 Rejection confirmation count range=1:20:1
# @param target_atr_period int 14 Target ATR period range=1:300:1
# @param stop_atr_multiplier float 5 Stop ATR multiplier range=0.1:20:0.1
# @param target_1_multiplier float 0.5 Target 1 multiplier range=0.1:10:0.1
# @param target_2_multiplier float 1 Target 2 multiplier range=0.1:10:0.1
# @param target_3_multiplier float 1.5 Target 3 multiplier range=0.1:10:0.1
# @param show_targets bool true Show target plans

factor=max(1,min(50,float(params.get("supertrend_factor",12)))); st_period=max(1,min(500,int(params.get("supertrend_atr_period",90)))); wma_length=max(1,min(300,int(params.get("wma_length",40)))); ema_length=max(1,min(300,int(params.get("ema_length",14)))); confirmation=max(1,min(20,int(params.get("confirmation_count",3)))); target_atr_period=max(1,min(300,int(params.get("target_atr_period",14)))); stop_mult=max(0.1,min(20,float(params.get("stop_atr_multiplier",5)))); target_multipliers=[max(0.1,min(10,float(params.get("target_1_multiplier",0.5)))),max(0.1,min(10,float(params.get("target_2_multiplier",1)))),max(0.1,min(10,float(params.get("target_3_multiplier",1.5))))]; show_targets=bool(params.get("show_targets",True))
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
def _wma(values,length):
    out=np.full(len(values),np.nan); weights=np.arange(1,length+1,dtype=float); divisor=float(np.sum(weights))
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights)/divisor)
    return out
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
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,c[:-1]]; true_range=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: true_range[0]=h[0]-l[0]
st_atr=_rma(true_range,st_period); volatility=_rma(true_range,target_atr_period); middle=(h+l)/2; lower_raw=middle-factor*st_atr; upper_raw=middle+factor*st_atr; lower_band=np.full(n,np.nan); upper_band=np.full(n,np.nan)
for i in range(n):
    if not np.isfinite(lower_raw[i]): continue
    if i==0 or not np.isfinite(lower_band[i-1]): lower_band[i]=lower_raw[i]; upper_band[i]=upper_raw[i]
    else: lower_band[i]=lower_raw[i] if lower_raw[i]>lower_band[i-1] or c[i-1]<lower_band[i-1] else lower_band[i-1]; upper_band[i]=upper_raw[i] if upper_raw[i]<upper_band[i-1] or c[i-1]>upper_band[i-1] else upper_band[i-1]
baseline=_ema(_wma((lower_band+upper_band)/2,wma_length),ema_length); trend=np.zeros(n,dtype=int); bull_change=np.zeros(n,bool); bear_change=np.zeros(n,bool); rejection_count=np.zeros(n,dtype=int); bullish_rejection=np.zeros(n,bool); bearish_rejection=np.zeros(n,bool); current=0; count=0
for i in range(n):
    previous_trend=current
    if i>=2 and np.all(np.isfinite(baseline[i-2:i+1])):
        if baseline[i]>baseline[i-1] and baseline[i-1]<=baseline[i-2]: current=1
        if baseline[i]<baseline[i-1] and baseline[i-1]>=baseline[i-2]: current=-1
    bull_change[i]=current==1 and previous_trend<=0; bear_change[i]=current==-1 and previous_trend>=0
    rejection=np.isfinite(baseline[i]) and h[i]>baseline[i] and l[i]<baseline[i] and current!=0
    if current!=previous_trend: count=0
    elif rejection: count+=1
    elif count>0: count=0
    rejection_count[i]=count; bullish_rejection[i]=count>confirmation and current==1; bearish_rejection[i]=count>confirmation and current==-1; trend[i]=current
plans=[]; active=None; stop_hit=np.zeros(n,bool); target_hits=[np.zeros(n,bool) for _ in range(3)]
for i in range(n):
    if active is not None and i>active["start"]:
        direction=active["direction"]
        if not active["ended"] and ((direction==1 and l[i]<=active["stop"]) or (direction==-1 and h[i]>=active["stop"])): active["ended"]=True; active["end"]=i; stop_hit[i]=True
        elif not active["ended"]:
            for target_index,target in enumerate(active["targets"]):
                if not active["hits"][target_index] and ((direction==1 and h[i]>=target) or (direction==-1 and l[i]<=target)): active["hits"][target_index]=True; target_hits[target_index][i]=True
            active["end"]=i
    if (bull_change[i] or bear_change[i]) and show_targets and np.isfinite(volatility[i]):
        direction=1 if bull_change[i] else -1; stop=float(l[i]-volatility[i]*stop_mult) if direction==1 else float(h[i]+volatility[i]*stop_mult); risk=abs(float(c[i])-stop); targets=[float(c[i]+direction*risk*value) for value in target_multipliers]; active={"start":int(i),"end":int(i),"direction":direction,"entry":float(c[i]),"stop":stop,"targets":targets,"hits":[False,False,False],"ended":False}; plans.append(active)
layers=[]
for plan in plans[-5:]:
    end=max(plan["end"],n+40 if plan is active and not plan["ended"] else plan["end"]); color="#00FFBB" if plan["direction"]==1 else "#FF1100"
    for role,value,line_color in (("Entry",plan["entry"],color),("SL",plan["stop"],"#FF1100"),("TP1",plan["targets"][0],"#00FFBB"),("TP2",plan["targets"][1],"#00FFBB"),("TP3",plan["targets"][2],"#00FFBB")): layers.append({"type":"line","role":role,"startIndex":plan["start"],"endIndex":int(end),"y1":float(value),"y2":float(value),"color":line_color,"lineWidth":2})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
signals=[_events(bull_change,baseline,"TREND UP","#00FFBB","buy"),_events(bear_change,baseline,"TREND DOWN","#FF1100","sell"),_events(bullish_rejection,baseline,"▲","#00FFBB","buy"),_events(bearish_rejection,baseline,"▼","#FF1100","sell"),_events(stop_hit,c,"SL","#FF1100","event")]
for i in range(3): signals.append(_events(target_hits[i],c,f"TP{i+1}","#00FFBB","event"))
bar_colors=["rgba(0,255,187,0.50)" if trend[i]==1 else "rgba(255,17,0,0.50)" if trend[i]==-1 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Trend Baseline","title":True,"data":_finite(baseline),"color":"#00FFBB","negativeColor":"#FF1100","type":"line","lineWidth":2,"overlay":True}],"signals":signals,"layers":layers,"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"supertrendATR":_finite(st_atr),"lowerBand":_finite(lower_band),"upperBand":_finite(upper_band),"baseline":_finite(baseline),"trend":trend.tolist(),"bullishTrendChange":bull_change.tolist(),"bearishTrendChange":bear_change.tolist(),"rejectionCount":rejection_count.tolist(),"bullishRejection":bullish_rejection.tolist(),"bearishRejection":bearish_rejection.tolist(),"volatility":_finite(volatility),"stopHit":stop_hit.tolist(),"targetHits":[x.tolist() for x in target_hits],"plans":plans}}
