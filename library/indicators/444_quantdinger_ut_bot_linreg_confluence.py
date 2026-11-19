# QuantDinger UT Bot LinReg Confluence
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger UT Bot LinReg Confluence"
my_indicator_description="Runs independent buy and sell UT trails, applies the resulting regime to linear-regression candles and maintains ATR-thick longevity zones plus optional rolling support/resistance."

# @param buy_sensitivity float 2 Buy sensitivity range=0:50:0.1
# @param buy_atr_period int 1 Buy ATR period range=0:500:1
# @param sell_sensitivity float 2 Sell sensitivity range=0:50:0.1
# @param sell_atr_period int 1 Sell ATR period range=0:500:1
# @param linreg_length int 11 LinReg length range=1:200:1
# @param signal_length int 7 Signal smoothing range=1:200:1
# @param signal_method str SMA Signal method values=SMA,EMA
# @param longevity_length int 5 Longevity length range=2:100:1
# @param show_trails bool false Show UT trails
# @param show_zones bool true Show longevity zones
# @param show_support_resistance bool true Show support resistance

buy_sensitivity=max(0,min(50,float(params.get("buy_sensitivity",2)))); buy_period=max(0,min(500,int(params.get("buy_atr_period",1)))); sell_sensitivity=max(0,min(50,float(params.get("sell_sensitivity",2)))); sell_period=max(0,min(500,int(params.get("sell_atr_period",1)))); linreg_length=max(1,min(200,int(params.get("linreg_length",11)))); signal_length=max(1,min(200,int(params.get("signal_length",7)))); signal_method=str(params.get("signal_method","SMA")).upper(); zone_length=max(2,min(100,int(params.get("longevity_length",5)))); show_trails=bool(params.get("show_trails",False)); show_zones=bool(params.get("show_zones",True)); show_sr=bool(params.get("show_support_resistance",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
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
def _linreg(values,length):
    out=np.full(len(values),np.nan); x=np.arange(length,dtype=float); xm=x.mean(); denominator=np.sum((x-xm)**2)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)):
            slope=np.sum((x-xm)*(sample-sample.mean()))/denominator if denominator else 0; out[i]=sample.mean()+slope*(length-1-xm)
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
buy_atr=_rma(tr,buy_period) if buy_period else np.full(n,np.nan); sell_atr=_rma(tr,sell_period) if sell_period else np.full(n,np.nan)
def _trail(atr,sensitivity,initial_side):
    out=np.full(n,np.nan); stop=np.nan
    for i in range(n):
        if not np.isfinite(atr[i]) or sensitivity<=0: continue
        loss=sensitivity*atr[i]
        if not np.isfinite(stop): stop=c[i]-loss if initial_side<0 else c[i]+loss
        elif c[i]>stop and c[i-1]>stop: stop=max(stop,c[i]-loss)
        elif c[i]<stop and c[i-1]<stop: stop=min(stop,c[i]+loss)
        else: stop=c[i]-loss if c[i]>stop else c[i]+loss
        out[i]=stop
    return out
buy_trail=_trail(buy_atr,buy_sensitivity,-1); sell_trail=_trail(sell_atr,sell_sensitivity,1); raw_buy=np.zeros(n,bool); raw_sell=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(buy_trail[1:])&np.isfinite(buy_trail[:-1]); raw_buy[1:]=valid&(c[1:]>buy_trail[1:])&(c[:-1]<=buy_trail[:-1]); valid=np.isfinite(sell_trail[1:])&np.isfinite(sell_trail[:-1]); raw_sell[1:]=valid&(c[1:]<sell_trail[1:])&(c[:-1]>=sell_trail[:-1])
position=np.zeros(n,dtype=int); buy=np.zeros(n,bool); sell=np.zeros(n,bool); state=0
for i in range(n):
    if raw_buy[i] and state<=0: state=1; buy[i]=True
    elif raw_sell[i] and state>=0: state=-1; sell[i]=True
    position[i]=state
lr_open=_linreg(o,linreg_length); lr_high=_linreg(h,linreg_length); lr_low=_linreg(l,linreg_length); lr_close=_linreg(c,linreg_length); signal=pd.Series(lr_close).rolling(signal_length,min_periods=signal_length).mean().to_numpy(float) if signal_method=="SMA" else _ema(lr_close,signal_length); atr20=_rma(tr,20); rolling_high=pd.Series(h).rolling(zone_length,min_periods=zone_length).max().to_numpy(float); rolling_low=pd.Series(l).rolling(zone_length,min_periods=zone_length).min().to_numpy(float); upper=[]; lower=[]
for i in range(1,n):
    if np.isfinite(rolling_high[i-1]) and h[i-1]==rolling_high[i-1] and h[i]<rolling_high[i]: upper.append({"startIndex":int(i-1),"top":float(rolling_high[i]),"bottom":float(rolling_high[i]-atr20[i]*0.5) if np.isfinite(atr20[i]) else float(rolling_high[i])})
    if np.isfinite(rolling_low[i-1]) and l[i-1]==rolling_low[i-1] and l[i]>rolling_low[i]: lower.append({"startIndex":int(i-1),"top":float(rolling_low[i]+atr20[i]*0.5) if np.isfinite(atr20[i]) else float(rolling_low[i]),"bottom":float(rolling_low[i])})
    upper=[z for z in upper if not(h[i]>z["top"] and i>z["startIndex"])][-10:]; lower=[z for z in lower if not(l[i]<z["bottom"] and i>z["startIndex"])][-10:]
layers=[]
if show_zones:
    for zone in upper[-4:]: layers.append({"type":"zone","role":"Upper Longevity","startIndex":zone["startIndex"],"endIndex":n+10,"top":zone["top"],"bottom":zone["bottom"],"fillColor":"rgba(244,67,54,0.25)","borderColor":"rgba(244,67,54,0.65)"})
    for zone in lower[-4:]: layers.append({"type":"zone","role":"Lower Longevity","startIndex":zone["startIndex"],"endIndex":n+10,"top":zone["top"],"bottom":zone["bottom"],"fillColor":"rgba(76,175,80,0.25)","borderColor":"rgba(76,175,80,0.65)"})
plots=[{"name":"LinReg Signal","title":True,"data":_finite(signal),"color":"#2196F3","type":"line","lineWidth":2,"overlay":True}]
if show_trails: plots.extend([{"name":"UT Buy Trail","data":_finite(buy_trail),"color":"#4CAF50","type":"line","lineWidth":1,"overlay":True},{"name":"UT Sell Trail","data":_finite(sell_trail),"color":"#F44336","type":"line","lineWidth":1,"overlay":True}])
sr={}
if show_sr:
    for window in (10,50,100,250,500,750,1000):
        hi=pd.Series(h).rolling(window,min_periods=window).max().to_numpy(float); lo=pd.Series(l).rolling(window,min_periods=window).min().to_numpy(float); sr[str(window)]={"high":_finite(hi),"low":_finite(lo)}
        if window<=250 and n>=window and np.isfinite(c[-1]) and c[-1]>0:
            if np.isfinite(hi[-1]) and abs(hi[-1]-c[-1])/c[-1]<=0.15: plots.append({"name":f"R{window}","data":_finite(hi),"color":"rgba(244,67,54,0.35)","type":"line","lineWidth":1,"overlay":True})
            if np.isfinite(lo[-1]) and abs(lo[-1]-c[-1])/c[-1]<=0.15: plots.append({"name":f"S{window}","data":_finite(lo),"color":"rgba(76,175,80,0.35)","type":"line","lineWidth":1,"overlay":True})
def _thin(mask,limit=4,min_gap=16):
    kept=np.zeros(len(mask),bool); last=None; count=0
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index); count+=1
            if count>=limit: break
    return kept
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
volume_average=pd.Series(v).rolling(20,min_periods=20).mean().to_numpy(float); tables=[]
if n: tables=[{"title":"UT Bot LinReg Status","position":"top-right","compact":True,"rows":[[{"text":"Regime"},{"text":"Long" if position[-1]>0 else "Short" if position[-1]<0 else "Flat"}],[{"text":"Volume"},{"text":"Above avg" if np.isfinite(volume_average[-1]) and v[-1]>volume_average[-1] else "Below avg"}],[{"text":"LinReg"},{"text":"Up" if np.isfinite(lr_close[-1]) and np.isfinite(signal[-1]) and lr_close[-1]>signal[-1] else "Down"}],[{"text":"Upper zones"},{"text":len(upper)}],[{"text":"Lower zones"},{"text":len(lower)}]]}]
bar_colors=["rgba(76,175,80,0.35)" if position[i]>0 else "rgba(244,67,54,0.35)" if position[i]<0 else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(_thin(buy),l,"UT BUY","#4CAF50","buy"),_events(_thin(sell),h,"UT SELL","#F44336","sell")],"layers":layers,"tables":tables,"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"buyATR":_finite(buy_atr),"sellATR":_finite(sell_atr),"buyTrail":_finite(buy_trail),"sellTrail":_finite(sell_trail),"rawBuy":raw_buy.tolist(),"rawSell":raw_sell.tolist(),"buySignal":buy.tolist(),"sellSignal":sell.tolist(),"position":position.tolist(),"linregOpen":_finite(lr_open),"linregHigh":_finite(lr_high),"linregLow":_finite(lr_low),"linregClose":_finite(lr_close),"signal":_finite(signal),"upperZones":upper,"lowerZones":lower,"supportResistance":sr}}
