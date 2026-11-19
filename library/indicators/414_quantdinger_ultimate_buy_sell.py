# QuantDinger Ultimate Buy and Sell
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Ultimate Buy and Sell"
my_indicator_description="Combines RSI Bollinger watch events with configurable confirmation crosses, price bands, ATR bands, MACD, ROC filtering and staged moving-average candle colors."

# @param watch_lookback int 35 Watch validity bars range=5:300:1
# @param rsi_length int 32 RSI length range=1:200:1
# @param rsi_ma_length int 32 RSI band basis length range=1:200:1
# @param rsi_band_multiplier float 2 RSI band multiplier range=1:3:0.1
# @param price_basis_length int 20 Price band basis length range=2:300:1
# @param price_inner_multiplier float 2.1 Inner price band multiplier range=1:4:0.1
# @param price_outer_multiplier float 2.4 Outer price band multiplier range=2:6:0.1
# @param use_rsi_watch bool true Use RSI watch events
# @param use_price_watch bool false Use price-band watch events
# @param use_atr_watch bool false Use ATR watch events
# @param use_rsi_basis_signal bool true Use RSI basis crosses
# @param use_price_basis_signal bool false Use price basis crosses
# @param use_macd_signal bool false Use MACD crosses
# @param use_75_signal bool true Use RSI cross under 75
# @param use_50_signal bool false Use RSI 50-zone crosses
# @param use_25_signal bool true Use RSI cross over 25
# @param use_roc_filter bool false Use ROC direction filter
# @param show_all_moving_averages bool false Show all configured moving averages
# @param show_signal_background bool true Show pre-signal background
# @param show_state_background bool false Show bought or sold state background

watch=max(5,min(300,int(params.get("watch_lookback",35)))); rsi_length=max(1,min(200,int(params.get("rsi_length",32)))); rsi_ma_length=max(1,min(200,int(params.get("rsi_ma_length",32)))); rsi_mult=max(1,min(3,float(params.get("rsi_band_multiplier",2)))); price_length=max(2,min(300,int(params.get("price_basis_length",20)))); inner_mult=max(1,min(4,float(params.get("price_inner_multiplier",2.1)))); outer_mult=max(2,min(6,float(params.get("price_outer_multiplier",2.4)))); use_rsi_watch=bool(params.get("use_rsi_watch",True)); use_price_watch=bool(params.get("use_price_watch",False)); use_atr_watch=bool(params.get("use_atr_watch",False)); use_rsi_signal=bool(params.get("use_rsi_basis_signal",True)); use_price_signal=bool(params.get("use_price_basis_signal",False)); use_macd_signal=bool(params.get("use_macd_signal",False)); use_75=bool(params.get("use_75_signal",True)); use_50=bool(params.get("use_50_signal",False)); use_25=bool(params.get("use_25_signal",True)); roc_filter=bool(params.get("use_roc_filter",False)); show_mas=bool(params.get("show_all_moving_averages",False)); show_signal_bg=bool(params.get("show_signal_background",True)); show_state_bg=bool(params.get("show_state_background",False))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.mean(sample))
    return out
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
def _wma(values,length):
    out=np.full(len(values),np.nan); weights=np.arange(1,length+1,dtype=float); divisor=float(np.sum(weights))
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights)/divisor)
    return out
def _ma(values,length,kind): return {"SMA":_sma,"EMA":_ema,"WMA":_wma,"RMA":_rma}.get(str(kind).upper(),_sma)(values,length)
def _stdev(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.std(sample,ddof=0))
    return out
def _cross_over(a,b):
    right=np.full(len(a),float(b)) if np.isscalar(b) else np.asarray(b,float); out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(right[1:])&np.isfinite(a[:-1])&np.isfinite(right[:-1])&(a[1:]>right[1:])&(a[:-1]<=right[:-1])
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); ha_close=(o+h+l+c)/4; ha_open=np.full(n,np.nan)
for i in range(n): ha_open[i]=(o[i]+c[i])/2 if i==0 else (ha_open[i-1]+ha_close[i-1])/2
source=ha_close if bool(params.get("use_heikin_ashi_smoothing",False)) else c; delta=np.r_[np.nan,np.diff(source)]; gain=np.where(np.isfinite(delta),np.maximum(delta,0),np.nan); loss=np.where(np.isfinite(delta),np.maximum(-delta,0),np.nan); average_gain=_rma(gain,rsi_length); average_loss=_rma(loss,rsi_length); rsi=np.full(n,np.nan); rsi[np.isfinite(average_loss)&(average_loss==0)]=100; valid=np.isfinite(average_gain)&np.isfinite(average_loss)&(average_loss>0); rsi[valid]=100-100/(1+average_gain[valid]/average_loss[valid]); rsi_basis=_sma(rsi,rsi_ma_length); rsi_dev=_stdev(rsi,rsi_ma_length); rsi_upper=rsi_basis+rsi_mult*rsi_dev; rsi_lower=rsi_basis-rsi_mult*rsi_dev
price_basis=_sma(c,price_length); price_dev=_stdev(c,price_length); upper_inner=price_basis+inner_mult*price_dev; lower_inner=price_basis-inner_mult*price_dev; upper_outer=price_basis+outer_mult*price_dev; lower_outer=price_basis-outer_mult*price_dev; fast_macd=_ema(c,12); slow_macd=_ema(c,26); macd=fast_macd-slow_macd; macd_signal=_ema(macd,9); roc_length=max(1,min(200,int(params.get("roc_length",10)))); roc=np.full(n,np.nan)
for i in range(roc_length,n):
    if np.isfinite(c[i-roc_length]) and c[i-roc_length]!=0: roc[i]=(c[i]-c[i-roc_length])/c[i-roc_length]*100
previous=np.r_[np.nan,c[:-1]]; true_range=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: true_range[0]=h[0]-l[0]
atr=_rma(true_range,20); atr_basis=_wma(c,20); atr_dev=_stdev(c,20); atr_upper=atr_basis+atr+1.5*atr_dev; atr_lower=atr_basis-atr-1.5*atr_dev
price_cross_up=_cross_over(c,price_basis); price_cross_down=_cross_over(price_basis,c); rsi_lower_cross=_cross_over(rsi,rsi_lower); rsi_upper_cross=_cross_over(rsi_upper,rsi); rsi_basis_up=_cross_over(rsi,rsi_basis); rsi_basis_down=_cross_over(rsi_basis,rsi); cross_70=_cross_over(rsi,70); cross_under_75=_cross_over(np.full(n,75.0),rsi); cross_51=_cross_over(rsi,51); cross_under_49=_cross_over(np.full(n,49.0),rsi); cross_under_30=_cross_over(np.full(n,30.0),rsi); cross_25=_cross_over(rsi,25); macd_up=_cross_over(macd,macd_signal); macd_down=_cross_over(macd_signal,macd); atr_watch_buy=_cross_over(atr_lower,c); atr_watch_sell=_cross_over(c,atr_upper)
buy_watch=np.zeros(n,bool); sell_watch=np.zeros(n,bool); buy_signal=np.zeros(n,bool); sell_signal=np.zeros(n,bool); buy_warning=np.zeros(n,bool); sell_warning=np.zeros(n,bool); bought_state=np.zeros(n,bool); sold_state=np.zeros(n,bool); buy_queue=[]; sell_queue=[]; bought=sold=False
for i in range(n):
    price_buy=i>0 and np.isfinite(lower_outer[i-1]) and l[i]<lower_outer[i-1]; price_sell=i>0 and np.isfinite(upper_outer[i-1]) and h[i]>upper_outer[i-1]; buy_watch[i]=(use_price_watch and price_buy) or (use_rsi_watch and (rsi_lower_cross[i] or cross_under_30[i])) or (use_atr_watch and atr_watch_buy[i]); sell_watch[i]=(use_price_watch and price_sell) or (use_rsi_watch and (rsi_upper_cross[i] or cross_70[i])) or (use_atr_watch and atr_watch_sell[i]); buy_queue.append(bool(buy_watch[i])); sell_queue.append(bool(sell_watch[i])); buy_queue=buy_queue[-watch:]; sell_queue=sell_queue[-watch:]; buy_ready=any(buy_queue); sell_ready=any(sell_queue); combined_buy=(use_rsi_signal and rsi_basis_up[i]) or (use_price_signal and price_cross_up[i]) or (use_50 and cross_51[i]) or (use_25 and cross_25[i]) or (use_macd_signal and macd_up[i]); combined_sell=(use_rsi_signal and rsi_basis_down[i]) or (use_price_signal and price_cross_down[i]) or (use_50 and cross_under_49[i]) or (use_75 and cross_under_75[i]) or (use_macd_signal and macd_down[i]); allow_buy=(not roc_filter) or (np.isfinite(roc[i]) and roc[i]>0); allow_sell=(not roc_filter) or (np.isfinite(roc[i]) and roc[i]<0); buy_warning[i]=combined_buy and buy_ready and allow_buy; sell_warning[i]=(not buy_warning[i]) and combined_sell and sell_ready and allow_sell
    if buy_warning[i]: buy_signal[i]=True; bought=True; sold=False; buy_queue=[]; sell_queue=[]
    elif sell_warning[i]: sell_signal[i]=True; bought=False; sold=True; buy_queue=[]; sell_queue=[]
    bought_state[i]=bought; sold_state[i]=sold
fast_ma=_wma(c,max(1,min(300,int(params.get("fast_ma_length",10))))); slow_ma=_wma(c,max(1,min(300,int(params.get("slow_ma_length",20))))); bar_colors=["#00A000" if c[i]>=fast_ma[i] and c[i]>=slow_ma[i] else "#FF0000" if c[i]<fast_ma[i] and c[i]<slow_ma[i] else "#FFD700" if np.isfinite(fast_ma[i]) and np.isfinite(slow_ma[i]) else None for i in range(n)]; background=[]
for i in range(n):
    color="rgba(255,165,0,0.20)" if show_signal_bg and buy_warning[i] else "rgba(255,0,255,0.20)" if show_signal_bg and sell_warning[i] else "rgba(0,128,0,0.10)" if show_state_bg and bought_state[i] else "rgba(255,0,0,0.10)" if show_state_bg and sold_state[i] else None; background.append(color)
ma_lengths=[10,20,50,100,200,300,400,500]; ma_colors=["#FFFF00","#00A000","#FF0000","#800080","#0000FF","#808080","#FFA500","#FFFFFF"]; moving_averages=[_wma(c,value) for value in ma_lengths]; plots=[{"name":"Price Basis","title":True,"data":_finite(price_basis),"color":"#FFA500","type":"line","lineWidth":2,"overlay":True},{"name":"Upper Inner Band","title":True,"data":_finite(upper_inner),"color":"#66BB6A","type":"line","lineWidth":1,"overlay":True},{"name":"Upper Outer Band","title":True,"data":_finite(upper_outer),"color":"#2E7D32","type":"line","lineWidth":1,"overlay":True},{"name":"Lower Inner Band","title":True,"data":_finite(lower_inner),"color":"#EF5350","type":"line","lineWidth":1,"overlay":True},{"name":"Lower Outer Band","title":True,"data":_finite(lower_outer),"color":"#C62828","type":"line","lineWidth":1,"overlay":True}]
for index,series in enumerate(moving_averages): plots.append({"name":f"MA {index+1} ({ma_lengths[index]})","title":show_mas,"data":_finite(series) if show_mas else [None]*n,"color":ma_colors[index],"type":"line","lineWidth":1,"overlay":True})
def _thin(mask,limit=4,min_gap=8):
    selected=[]
    for value in np.flatnonzero(mask)[::-1]:
        index=int(value)
        if all(abs(index-other)>=min_gap for other in selected): selected.append(index)
        if len(selected)>=limit: break
    result=np.zeros(n,bool); result[selected]=True
    return result
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(buy_signal,l,"BUY","#FF9800","buy"),_events(sell_signal,h,"SELL","#FF00FF","sell"),_events(_thin(buy_watch),l,"WATCH","#FF9800","event"),_events(_thin(sell_watch),h,"WATCH","#FF00FF","event")],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":background,"calculatedVars":{"rsi":_finite(rsi),"rsiBasis":_finite(rsi_basis),"rsiUpper":_finite(rsi_upper),"rsiLower":_finite(rsi_lower),"priceBasis":_finite(price_basis),"upperInner":_finite(upper_inner),"lowerInner":_finite(lower_inner),"upperOuter":_finite(upper_outer),"lowerOuter":_finite(lower_outer),"macd":_finite(macd),"macdSignal":_finite(macd_signal),"roc":_finite(roc),"atrUpper":_finite(atr_upper),"atrLower":_finite(atr_lower),"fastMA":_finite(fast_ma),"slowMA":_finite(slow_ma),"buyWatch":buy_watch.tolist(),"sellWatch":sell_watch.tolist(),"buyWarning":buy_warning.tolist(),"sellWarning":sell_warning.tolist(),"buySignal":buy_signal.tolist(),"sellSignal":sell_signal.tolist(),"boughtState":bought_state.tolist(),"soldState":sold_state.tolist()}}
