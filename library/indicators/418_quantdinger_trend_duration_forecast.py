# QuantDinger Trend Duration Forecast
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trend Duration Forecast"
my_indicator_description="Classifies Hull-average direction and forecasts the probable duration of the active trend from recent bullish and bearish duration samples."

# @param smoothing_length int 50 HMA smoothing length range=2:300:1
# @param trend_sensitivity int 3 Trend sensitivity range=1:30:1
# @param sample_size int 10 Trend sample size range=2:100:1

length=max(2,min(300,int(params.get("smoothing_length",50)))); sensitivity=max(1,min(30,int(params.get("trend_sensitivity",3)))); sample_size=max(2,min(100,int(params.get("sample_size",10))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _wma(values,window):
    out=np.full(len(values),np.nan); weights=np.arange(1,window+1,dtype=float); divisor=float(np.sum(weights))
    for i in range(window-1,len(values)):
        sample=values[i-window+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.dot(sample,weights)/divisor)
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); hma=_wma(2*_wma(c,max(1,length//2))-_wma(c,length),max(1,int(np.sqrt(length)))); trend=np.zeros(n,dtype=int); duration=np.zeros(n,dtype=int); probable=np.full(n,np.nan); bullish_samples=[]; bearish_samples=[]; switch_up=np.zeros(n,bool); switch_down=np.zeros(n,bool); current=0; count=0; last_switch=-1
for i in range(n):
    new=current
    if i>=sensitivity and np.isfinite(hma[i]):
        recent=hma[i-sensitivity:i]
        if np.all(np.isfinite(recent)) and hma[i]>np.max(recent): new=1
        if np.all(np.isfinite(recent)) and hma[i]<np.min(recent): new=-1
    if new!=current and new!=0:
        if current==1 and count>0: bullish_samples.append(int(count)); bullish_samples=bullish_samples[-sample_size:]
        elif current==-1 and count>0: bearish_samples.append(int(count)); bearish_samples=bearish_samples[-sample_size:]
        switch_up[i]=new==1; switch_down[i]=new==-1; current=new; count=0; last_switch=i
    if current!=0: count+=1
    trend[i]=current; duration[i]=count; sample=bullish_samples if current==1 else bearish_samples if current==-1 else []
    if sample: probable[i]=float(np.mean(sample))
layers=[]
if last_switch>=0 and np.isfinite(probable[-1]) and np.isfinite(hma[last_switch]): layers.append({"type":"line","role":"Probable Trend Length","startIndex":int(last_switch),"endIndex":int(last_switch+round(probable[-1])),"y1":float(hma[last_switch]),"y2":float(hma[last_switch]),"color":"#E0E0E0","lineWidth":1,"lineStyle":"dashed"})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
rows=[]; maximum=max(len(bullish_samples),len(bearish_samples))
for i in range(maximum): rows.append([{"text":str(i+1)},{"text":str(bullish_samples[i]) if i<len(bullish_samples) else "—"},{"text":str(bearish_samples[i]) if i<len(bearish_samples) else "—"}])
rows.insert(0,[{"text":"#"},{"text":"Trend ↑"},{"text":"Trend ↓"}]); rows.append([{"text":"Average"},{"text":"—" if not bullish_samples else f"{np.mean(bullish_samples):.0f}"},{"text":"—" if not bearish_samples else f"{np.mean(bearish_samples):.0f}"}]); tables=[{"title":"Trend Length Samples","position":"bottom-right","compact":True,"rows":rows}] if n else []
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Hull Trend","title":True,"data":_finite(hma),"color":"#12E49E","negativeColor":"#EC5610","type":"line","lineWidth":3,"overlay":True}],"signals":[_events(switch_up,l,"TREND ↑","#12E49E","buy"),_events(switch_down,h,"TREND ↓","#EC5610","sell")],"layers":layers,"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"hma":_finite(hma),"trend":trend.tolist(),"duration":duration.tolist(),"probableLength":_finite(probable),"switchUp":switch_up.tolist(),"switchDown":switch_down.tolist(),"bullishSamples":bullish_samples,"bearishSamples":bearish_samples}}
