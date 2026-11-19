# QuantDinger Standardized MACD Heikin-Ashi
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Standardized MACD Heikin-Ashi"
my_indicator_description="Standardizes MACD by the slow EMA of price range and transforms the oscillator into Heikin-Ashi candles."

# @param source str close MACD source values=open,high,low,close,hl2,hlc3,ohlc4,hlcc4
# @param fast_length int 12 Fast length range=1:500:1
# @param slow_length int 26 Slow length range=2:500:1
# @param show_signal bool true Show signal
# @param signal_source str close Signal source values=open,high,low,close,hl2,hlc3,ohlc4,hlcc4
# @param signal_length int 9 Signal length range=1:500:1
# @param display_mode str Hybrid Display mode values=Hybrid,MACD,Histogram
# @param show_reversions bool true Show reversion markers
# @param reversion_threshold int 100 Reversion threshold values=100,150
# @param bar_coloring str None Bar coloring values=None,MidLine,Candles,Sig Cross,Extremities,Reversions

source_name=str(params.get("source","close")); fast=max(1,min(500,int(params.get("fast_length",12)))); slow=max(fast+1,min(500,int(params.get("slow_length",26)))); show_signal=bool(params.get("show_signal",True)); signal_source=str(params.get("signal_source","close")); signal_length=max(1,min(500,int(params.get("signal_length",9)))); mode=str(params.get("display_mode","Hybrid")); mode=mode if mode in ("Hybrid","MACD","Histogram") else "Hybrid"; show_reversions=bool(params.get("show_reversions",True)); reversion_threshold=150 if int(params.get("reversion_threshold",100))==150 else 100; coloring=str(params.get("bar_coloring","None"))

def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _ema(v,length):
    out=np.full(len(v),np.nan); state=np.nan; a=2/(length+1)
    for i,x in enumerate(v):
        if np.isfinite(x): state=float(x) if not np.isfinite(state) else state+a*(float(x)-state); out[i]=state
    return out
def _pick(name,o,h,l,c):
    return {"open":o,"high":h,"low":l,"close":c,"hl2":(h+l)/2,"hlc3":(h+l+c)/3,"ohlc4":(o+h+l+c)/4,"hlcc4":(h+l+2*c)/4}.get(name,c)
def _cross_over(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(a[1:]>b[1:])&(a[:-1]<=b[:-1])
    return out

frame=df.copy().reset_index(drop=True)
for c in ("time","open","high","low","close","volume"):
    if c not in frame.columns: frame[c]=0.0
    frame[c]=pd.to_numeric(frame[c],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); src=_pick(source_name,o,h,l,c)
denominator=_ema(h-l,slow); raw=np.divide(_ema(src,fast)-_ema(src,slow),denominator,out=np.full(n,np.nan),where=np.isfinite(denominator)&(denominator!=0))*100
macd_open=np.r_[np.nan,raw[:-1]]; macd_high=np.fmax(raw,macd_open); macd_low=np.fmin(raw,macd_open); macd_close=raw.copy(); ha_close=(macd_open+macd_high+macd_low+macd_close)/4; ha_open=np.full(n,np.nan)
for i in range(n): ha_open[i]=(macd_open[i]+macd_close[i])/2 if i==0 or not np.isfinite(ha_open[i-1]) else (ha_open[i-1]+ha_close[i-1])/2
ha_high=np.fmax(macd_high,np.fmax(ha_open,ha_close)); ha_low=np.fmin(macd_low,np.fmin(ha_open,ha_close)); signal=_ema(_pick(signal_source,ha_open,ha_high,ha_low,ha_close),signal_length); histogram=ha_close-signal
overbought=np.zeros(n,bool); oversold=np.zeros(n,bool); mid_up=_cross_over(ha_close,np.zeros(n)); mid_down=_cross_over(np.zeros(n),ha_close); bull_cross=_cross_over(ha_close,signal); bear_cross=_cross_over(signal,ha_close); ha_up=np.zeros(n,bool); ha_down=np.zeros(n,bool)
if n>1:
    overbought[1:]=(ha_high[1:]>reversion_threshold)&(ha_close[1:]<ha_open[1:])&(ha_close[:-1]>=ha_open[:-1]); oversold[1:]=(ha_low[1:]<-reversion_threshold)&(ha_close[1:]>ha_open[1:])&(ha_close[:-1]<=ha_open[:-1]); ha_up[1:]=(ha_close[1:]>ha_open[1:])&(ha_close[:-1]<=ha_open[:-1]); ha_down[1:]=(ha_close[1:]<ha_open[1:])&(ha_close[:-1]>=ha_open[:-1])
bar_colors=[]
for i in range(n):
    color=None
    if coloring=="MidLine": color="#00BCD4" if ha_close[i]>=0 else "#FC1F1F"
    elif coloring=="Candles": color="#00BCD4" if ha_close[i]>=ha_open[i] else "#FC1F1F"
    elif coloring=="Sig Cross": color="#00BCD4" if ha_close[i]>=signal[i] else "#FC1F1F"
    elif coloring=="Extremities": color="#FC1F1F" if ha_high[i]>=reversion_threshold else "#00BCD4" if ha_low[i]<=-reversion_threshold else None
    elif coloring=="Reversions": color="#FC1F1F" if overbought[i] else "#00BCD4" if oversold[i] else None
    bar_colors.append(color)
candles=[{"open":float(ha_open[i]),"high":float(ha_high[i]),"low":float(ha_low[i]),"close":float(ha_close[i]),"color":"#00BCD4" if ha_close[i]>=ha_open[i] else "#FC1F1F"} if mode!="Histogram" and np.all(np.isfinite([ha_open[i],ha_high[i],ha_low[i],ha_close[i]])) else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"HA MACD","title":True,"data":candles,"color":"#00BCD4","type":"candles","overlay":False},{"name":"Signal","title":True,"data":_finite(signal) if show_signal and mode!="Histogram" else [None]*n,"color":"#C0C0C0","type":"line","lineWidth":1,"overlay":False},{"name":"Histogram","title":True,"data":_finite(histogram) if mode!="MACD" else [None]*n,"color":"#8AC3F5","negativeColor":"#FFA7B6","type":"histogram","overlay":False},{"name":"Zero","title":True,"data":[0.0]*n,"color":"#808080","type":"line","overlay":False}],
"signals":[{"type":"sell","name":"Overbought","text":"OB","data":[{"active":True,"price":float(ha_high[i]+40),"text":"OB","color":"#FC1F1F"} if show_reversions and overbought[i] else None for i in range(n)],"color":"#FC1F1F","renderMode":"events","overlay":False},{"type":"buy","name":"Oversold","text":"OS","data":[{"active":True,"price":float(ha_low[i]-40),"text":"OS","color":"#00BCD4"} if show_reversions and oversold[i] else None for i in range(n)],"color":"#00BCD4","renderMode":"events","overlay":False}],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,
"calculatedVars":{"rawMacd":_finite(raw),"haOpen":_finite(ha_open),"haHigh":_finite(ha_high),"haLow":_finite(ha_low),"haClose":_finite(ha_close),"signal":_finite(signal),"histogram":_finite(histogram),"overbought":overbought.tolist(),"oversold":oversold.tolist(),"midlineUp":mid_up.tolist(),"midlineDown":mid_down.tolist(),"haUp":ha_up.tolist(),"haDown":ha_down.tolist(),"bullCross":bull_cross.tolist(),"bearCross":bear_cross.tolist()}}
