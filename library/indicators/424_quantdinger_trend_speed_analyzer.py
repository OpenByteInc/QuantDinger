# QuantDinger Trend Speed Analyzer
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trend Speed Analyzer"
my_indicator_description="Adapts an EMA length and acceleration to price movement, accumulates smoothed candle pressure within trend waves and compares current wave speed with historical bullish and bearish waves."

# @param maximum_length int 50 Maximum dynamic length range=5:300:1
# @param accelerator_multiplier float 5 Accelerator multiplier range=0:30:0.1
# @param collection_period int 100 Speed normalization period range=5:500:1
# @param lookback_period int 100 Wave statistics lookback range=1:500:1
# @param color_bars bool true Color bars by trend speed
# @param show_table bool true Show wave statistics

maximum=max(5,min(300,int(params.get("maximum_length",50)))); accelerator=max(0,min(30,float(params.get("accelerator_multiplier",5)))); collection=max(5,min(500,int(params.get("collection_period",100)))); lookback=max(1,min(500,int(params.get("lookback_period",100)))); color_bars=bool(params.get("color_bars",True)); show_table=bool(params.get("show_table",True))
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
def _hma(values,length): return _wma(2*_wma(values,max(1,length//2))-_wma(values,length),max(1,int(np.sqrt(length))))
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); dynamic_length=np.full(n,np.nan); accel_factor=np.zeros(n); alpha=np.full(n,np.nan); dynamic_ema=np.full(n,np.nan); delta=np.r_[0.0,np.abs(np.diff(c))]
for i in range(n):
    price_window=np.abs(c[max(0,i-199):i+1]); maximum_price=float(np.max(price_window)) if len(price_window) else 1; normalized=(c[i]+maximum_price)/(2*maximum_price) if maximum_price>0 else 0.5; dynamic_length[i]=5+normalized*(maximum-5); max_delta=float(np.max(delta[max(0,i-199):i+1])); accel_factor[i]=delta[i]/max_delta if max_delta>0 else 0; alpha[i]=min(1,2/(dynamic_length[i]+1)*(1+accel_factor[i]*accelerator)); dynamic_ema[i]=c[i] if i==0 else alpha[i]*c[i]+(1-alpha[i])*dynamic_ema[i-1]
smooth_close=_rma(c,10); smooth_open=_rma(o,10); pressure=smooth_close-smooth_open; speed=np.zeros(n); position=np.zeros(n,dtype=int); bull_waves=[]; bear_waves=[]; wave_values=[]; current_position=0
for i in range(n):
    bull_cross=i>0 and c[i]>dynamic_ema[i] and c[i-1]<=dynamic_ema[i-1]; bear_cross=i>0 and c[i]<dynamic_ema[i] and c[i-1]>=dynamic_ema[i-1]
    if bull_cross:
        if current_position==-1 and wave_values: bear_waves.append(float(np.min(wave_values)))
        current_position=1; wave_values=[]; speed[i]=pressure[i] if np.isfinite(pressure[i]) else 0
    elif bear_cross:
        if current_position==1 and wave_values: bull_waves.append(float(np.max(wave_values)))
        current_position=-1; wave_values=[]; speed[i]=pressure[i] if np.isfinite(pressure[i]) else 0
    else: speed[i]=(speed[i-1] if i else 0)+(pressure[i] if np.isfinite(pressure[i]) else 0)
    wave_values.append(float(speed[i])); position[i]=current_position
bull_waves=bull_waves[-lookback:]; bear_waves=bear_waves[-lookback:]; trend_speed=_hma(speed,5); normalized_speed=np.full(n,np.nan)
for i in range(n):
    sample=speed[max(0,i-collection+1):i+1]; low_value=float(np.min(sample)); high_value=float(np.max(sample)); normalized_speed[i]=(speed[i]-low_value)/(high_value-low_value) if high_value>low_value else 0.5
bar_colors=[("#82FFC3" if speed[i]>=0 and normalized_speed[i]<0.75 else "#00FF00" if speed[i]>=0 else "#F78C8C" if normalized_speed[i]>0.25 else "#FF0000") if color_bars else None for i in range(n)]; bull_avg=float(np.mean(bull_waves)) if bull_waves else np.nan; bear_avg=float(np.mean(bear_waves)) if bear_waves else np.nan; bull_max=float(np.max(bull_waves)) if bull_waves else np.nan; bear_max=float(np.min(bear_waves)) if bear_waves else np.nan
def _ratio(value,reference): return value/abs(reference) if np.isfinite(reference) and abs(reference)>1e-12 else np.nan
current=float(speed[-1]) if n else np.nan; ratio_avg=_ratio(current,bull_avg if current>=0 else bear_avg); ratio_max=_ratio(current,bull_max if current>=0 else bear_max); dominance_avg=bull_avg-abs(bear_avg) if np.all(np.isfinite([bull_avg,bear_avg])) else np.nan; dominance_max=bull_max-abs(bear_max) if np.all(np.isfinite([bull_max,bear_max])) else np.nan; tables=[]
if show_table and n: tables=[{"title":"Trend Speed","position":"top-right","compact":True,"rows":[[{"text":""},{"text":"Average Wave"},{"text":"Max Wave"}],[{"text":"Current Ratio"},{"text":"—" if not np.isfinite(ratio_avg) else f"{ratio_avg:.2f}x"},{"text":"—" if not np.isfinite(ratio_max) else f"{ratio_max:.2f}x"}],[{"text":"Dominance"},{"text":"—" if not np.isfinite(dominance_avg) else ("Bullish " if dominance_avg>0 else "Bearish ")+f"{abs(dominance_avg):.2f}"},{"text":"—" if not np.isfinite(dominance_max) else ("Bullish " if dominance_max>0 else "Bearish ")+f"{abs(dominance_max):.2f}"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Dynamic Trend","title":True,"data":_finite(dynamic_ema),"color":"#00FF00","negativeColor":"#FF0000","type":"line","lineWidth":2,"overlay":True},{"name":"Trend Speed","title":True,"data":_finite(trend_speed),"color":"#82FFC3","negativeColor":"#FF0000","type":"histogram","overlay":False}],"signals":[],"layers":[],"tables":tables,"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"dynamicLength":_finite(dynamic_length),"accelerationFactor":_finite(accel_factor),"alpha":_finite(alpha),"dynamicEMA":_finite(dynamic_ema),"pressure":_finite(pressure),"speed":_finite(speed),"trendSpeed":_finite(trend_speed),"normalizedSpeed":_finite(normalized_speed),"position":position.tolist(),"bullishWaves":[float(x) for x in bull_waves],"bearishWaves":[float(x) for x in bear_waves],"bullishAverage":None if not np.isfinite(bull_avg) else bull_avg,"bearishAverage":None if not np.isfinite(bear_avg) else bear_avg,"bullishMaximum":None if not np.isfinite(bull_max) else bull_max,"bearishMaximum":None if not np.isfinite(bear_max) else bear_max}}
