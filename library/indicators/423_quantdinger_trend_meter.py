# QuantDinger Trend Meter
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trend Meter"
my_indicator_description="Combines three configurable trend meters, two moving-average trend bars, WaveTrend crosses and RSI/WaveTrend pressure signals."

# @param meter_1 str MACD Crossover - Fast - 8, 21, 5 Trend meter 1 values=MACD Crossover - 12, 26, 9,MACD Crossover - Fast - 8, 21, 5,Mom Dad Cross,RSI Signal Line Cross,RSI 13,RSI 5,Trend Candles,N/A
# @param meter_2 str RSI 13 Trend meter 2 values=MACD Crossover - 12, 26, 9,MACD Crossover - Fast - 8, 21, 5,Mom Dad Cross,RSI Signal Line Cross,RSI 13,RSI 5,Trend Candles,N/A
# @param meter_3 str RSI 5 Trend meter 3 values=MACD Crossover - 12, 26, 9,MACD Crossover - Fast - 8, 21, 5,Mom Dad Cross,RSI Signal Line Cross,RSI 13,RSI 5,Trend Candles,N/A
# @param trend_bar_1 str MA Crossover Trend bar 1 values=MA Crossover,Fast MA Direction,Slow MA Direction,MACD Crossover
# @param trend_bar_2 str MA Crossover Trend bar 2 values=MA Crossover,Fast MA Direction,Slow MA Direction,MACD Crossover
# @param show_pressure bool true Show positive and negative pressure
# @param show_alignment_signals bool true Show three-meter alignment signals
# @param show_wave_alignment bool true Show WaveTrend aligned signals

meter1=str(params.get("meter_1","MACD Crossover - Fast - 8, 21, 5")); meter2=str(params.get("meter_2","RSI 13")); meter3=str(params.get("meter_3","RSI 5")); trend_bar1=str(params.get("trend_bar_1","MA Crossover")); trend_bar2=str(params.get("trend_bar_2","MA Crossover")); show_pressure=bool(params.get("show_pressure",True)); show_alignment=bool(params.get("show_alignment_signals",True)); show_wave_alignment=bool(params.get("show_wave_alignment",True))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.mean(sample))
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
    delta=np.r_[np.nan,np.diff(values)]; gain=np.where(np.isfinite(delta),np.maximum(delta,0),np.nan); loss=np.where(np.isfinite(delta),np.maximum(-delta,0),np.nan); ag=_rma(gain,length); al=_rma(loss,length); out=np.full(len(values),np.nan); out[np.isfinite(al)&(al==0)]=100; valid=np.isfinite(ag)&np.isfinite(al)&(al>0); out[valid]=100-100/(1+ag[valid]/al[valid]); return out
def _linreg(values,length):
    out=np.full(len(values),np.nan); x=np.arange(length,dtype=float); xm=float(np.mean(x)); denominator=float(np.sum((x-xm)**2))
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.mean(sample)+np.dot(x-xm,sample-np.mean(sample))/denominator*((length-1)-xm))
    return out
def _cross(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(((a[1:]>b[1:])&(a[:-1]<=b[:-1]))|((a[1:]<b[1:])&(a[:-1]>=b[:-1])))
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); hlc3=(h+l+c)/3; esa=_ema(hlc3,9); deviation=_ema(np.abs(hlc3-esa),9); ci=np.divide(hlc3-esa,0.015*deviation,out=np.full(n,np.nan),where=np.isfinite(deviation)&(deviation>1e-12)); wt1=_ema(ci,12); wt2=_sma(wt1,3); wave_delta=wt1-wt2; wave_cross=_cross(wt1,wt2); wave_up=wave_cross&(wt1>=wt2); wave_down=wave_cross&(wt1<=wt2); rsi14=_rsi(c,14); rsi13=_rsi(c,13); rsi5=_rsi(c,5); positive_pressure=np.zeros(n,bool); negative_pressure=np.zeros(n,bool)
if n>1:
    positive_pressure[1:]=((rsi14[1:]<30)|(rsi14[:-1]<30))&(wt1[1:]<-60)&(wave_delta[1:]>wave_delta[:-1]); negative_pressure[1:]=((rsi14[1:]>70)|(rsi14[:-1]>70))&(wt1[1:]>60)&(wave_delta[1:]<=wave_delta[:-1])
macd=_ema(c,12)-_ema(c,26); macd_hist=macd-_ema(macd,9); fast_macd=_ema(c,8)-_ema(c,21); fast_hist=fast_macd-_ema(fast_macd,5); mom=_ema(c,5)-_ema(c,20); dad=_ema(mom,30); rsi_line=_linreg(rsi13,21); ha_close=(o+h+l+c)/4; ha_open=np.full(n,np.nan)
for i in range(n): ha_open[i]=(o[i]+c[i])/2 if i==0 else (ha_open[i-1]+ha_close[i-1])/2
trend_candle=ha_close>ha_open
for i in range(1,n):
    for lag in range(min(6,i),0,-1):
        if min(ha_open[lag*-1+i],ha_close[lag*-1+i])<=ha_open[i]<=max(ha_open[lag*-1+i],ha_close[lag*-1+i]) and min(ha_open[lag*-1+i],ha_close[lag*-1+i])<=ha_close[i]<=max(ha_open[lag*-1+i],ha_close[lag*-1+i]): trend_candle[i]=trend_candle[i-lag]; break
def _select(name):
    if name=="MACD Crossover - 12, 26, 9": return macd_hist>0
    if name=="MACD Crossover - Fast - 8, 21, 5": return fast_hist>0
    if name=="Mom Dad Cross": return mom>dad
    if name=="RSI Signal Line Cross": return rsi13>rsi_line
    if name=="RSI 13": return rsi13>50
    if name=="RSI 5": return rsi5>50
    if name=="Trend Candles": return trend_candle
    return np.zeros(n,bool)
m1=_select(meter1); m2=_select(meter2); m3=_select(meter3); all_positive=m1&m2&m3; all_negative=(~m1)&(~m2)&(~m3); align_up=np.zeros(n,bool); align_down=np.zeros(n,bool)
if n>1: align_up[1:]=all_positive[1:]&(~all_positive[:-1]); align_down[1:]=all_negative[1:]&(~all_negative[:-1])
ma1=_ema(c,5); ma2=_ema(c,11); ma3=_ema(c,13); ma4=_sma(c,36); tb1={"Fast MA Direction":np.r_[False,np.diff(ma1)>0],"Slow MA Direction":np.r_[False,np.diff(ma2)>0],"MACD Crossover":macd_hist>0}.get(trend_bar1,ma1>ma2); tb2={"Fast MA Direction":np.r_[False,np.diff(ma3)>0],"Slow MA Direction":np.r_[False,np.diff(ma4)>0],"MACD Crossover":macd_hist>0}.get(trend_bar2,ma3>ma4); wave_aligned=(wave_up&all_positive)|(wave_down&all_negative)
plots=[]
for index,state in enumerate((m1,m2,m3)):
    level=128.5-index*6; plots.extend([{"name":f"Meter {index+1} Bull","title":True,"data":[level if x else None for x in state],"color":"#288A75","type":"line","lineWidth":3,"overlay":False},{"name":f"Meter {index+1} Bear","title":False,"data":[level if not x else None for x in state],"color":"#FF0000","type":"line","lineWidth":3,"overlay":False}])
plots.extend([{"name":"Trend Bar 1 Bull","title":True,"data":[110.0 if x else None for x in tb1],"color":"#00A000","type":"line","lineWidth":4,"overlay":False},{"name":"Trend Bar 1 Bear","title":False,"data":[110.0 if not x else None for x in tb1],"color":"#FF0000","type":"line","lineWidth":4,"overlay":False},{"name":"Trend Bar 2 Bull","title":True,"data":[104.5 if x else None for x in tb2],"color":"#00A000","type":"line","lineWidth":5,"overlay":False},{"name":"Trend Bar 2 Bear","title":False,"data":[104.5 if not x else None for x in tb2],"color":"#FF0000","type":"line","lineWidth":5,"overlay":False}])
def _thin(mask,limit=4,min_gap=20):
    kept=np.zeros(len(mask),bool); last=None
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index)
            if int(np.sum(kept))>=limit: break
    return kept
def _events(mask,level,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(level),"text":text,"color":color} if mask[i] else None for i in range(n)],"color":color,"renderMode":"events","overlay":False}
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(_thin(positive_pressure) if show_pressure else np.zeros(n,bool),138.5,"+PRESSURE","#288A75","buy"),_events(_thin(negative_pressure) if show_pressure else np.zeros(n,bool),138.5,"-PRESSURE","#DC143C","sell"),_events(_thin(align_up) if show_alignment else np.zeros(n,bool),134.5,"3 UP","#288A75","buy"),_events(_thin(align_down) if show_alignment else np.zeros(n,bool),134.5,"3 DOWN","#FF0000","sell"),_events(_thin(wave_aligned) if show_wave_alignment else np.zeros(n,bool),134.5,"WT ALIGN","#00B7FF","event")],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"waveTrend":_finite(wt1),"waveSignal":_finite(wt2),"rsi14":_finite(rsi14),"rsi13":_finite(rsi13),"rsi5":_finite(rsi5),"meter1":m1.tolist(),"meter2":m2.tolist(),"meter3":m3.tolist(),"allPositive":all_positive.tolist(),"allNegative":all_negative.tolist(),"alignUp":align_up.tolist(),"alignDown":align_down.tolist(),"positivePressure":positive_pressure.tolist(),"negativePressure":negative_pressure.tolist(),"trendBar1":tb1.tolist(),"trendBar2":tb2.tolist(),"waveAligned":wave_aligned.tolist()}}
