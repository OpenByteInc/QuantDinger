# QuantDinger Stochastic Z-Score
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Stochastic Z-Score"
my_indicator_description="Normalizes price to a rolling Z-score, applies stochastic scaling and Hull smoothing, and confirms reversals with long-term ALMA momentum."

# @param source str hlc3 Source values=open,high,low,close,hl2,hlc3,ohlc4,hlcc4
# @param length int 21 Length range=2:500:1
# @param show_histogram bool false Show raw Z-score histogram
# @param bullish_color str #00FFBB Bullish color
# @param bearish_color str #FF1100 Bearish color

source_name=str(params.get("source","hlc3")); length=max(2,min(500,int(params.get("length",21)))); show_hist=bool(params.get("show_histogram",False)); bull=str(params.get("bullish_color","#00FFBB")); bear=str(params.get("bearish_color","#FF1100"))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _sma(v,l):
    out=np.full(len(v),np.nan)
    for i in range(l-1,len(v)):
        w=v[i-l+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.mean(w))
    return out
def _wma(v,l):
    out=np.full(len(v),np.nan); weights=np.arange(1,l+1,dtype=float)
    for i in range(l-1,len(v)):
        w=v[i-l+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.dot(w,weights)/np.sum(weights))
    return out
def _hma(v,l): return _wma(2*_wma(v,max(1,l//2))-_wma(v,l),max(1,int(np.sqrt(l))))
def _alma(v,l,offset,sigma):
    out=np.full(len(v),np.nan); m=offset*(l-1); s=l/sigma; weights=np.exp(-((np.arange(l)-m)**2)/(2*s*s)); weights/=np.sum(weights)
    for i in range(l-1,len(v)):
        w=v[i-l+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.dot(w,weights))
    return out
def _cross_over(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(a[1:]>b[1:])&(a[:-1]<=b[:-1])
    return out
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); source={"open":o,"high":h,"low":l,"close":c,"hl2":(h+l)/2,"hlc3":(h+l+c)/3,"ohlc4":(o+h+l+c)/4,"hlcc4":(h+l+2*c)/4}.get(source_name,(h+l+c)/3)
basis=_sma(source,length); deviation=np.full(n,np.nan)
for i in range(length-1,n):
    w=source[i-length+1:i+1]
    if np.all(np.isfinite(w)): deviation[i]=float(np.std(w,ddof=0))
zscore=np.divide(source-basis,deviation,out=np.full(n,np.nan),where=deviation>0); stochastic=np.full(n,np.nan)
for i in range(length-1,n):
    w=zscore[i-length+1:i+1]; finite=w[np.isfinite(w)]
    if len(finite)==length:
        lo=float(np.min(finite)); hi=float(np.max(finite)); stochastic[i]=50 if hi==lo else 100*(zscore[i]-lo)/(hi-lo)
scaled=stochastic/25-2; oscillator=_hma(scaled,length); previous=np.r_[np.nan,oscillator[:-1]]; long_term=_alma(zscore,length,0,0.1); cross_up=_cross_over(oscillator,previous); cross_down=_cross_over(previous,oscillator); momentum_shift=cross_up|cross_down; bullish=cross_up&(long_term>0)&(previous<-2); bearish=cross_down&(long_term<0)&(previous>2); zero_up=_cross_over(oscillator,np.zeros(n)); zero_down=_cross_over(np.zeros(n),oscillator)
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Z-Score","title":show_hist,"data":_finite(zscore) if show_hist else [None]*n,"color":bull,"negativeColor":bear,"type":"histogram","overlay":False},{"name":"Smoothed Oscillator","title":True,"data":_finite(oscillator),"color":"#A0A0A0","type":"line","lineWidth":3,"overlay":False},{"name":"Upper","title":True,"data":[2.0]*n,"color":bear,"type":"line","lineWidth":2,"overlay":False},{"name":"Upper Outer","title":True,"data":[2.5]*n,"color":bear,"type":"line","lineWidth":2,"overlay":False},{"name":"Lower","title":True,"data":[-2.0]*n,"color":bull,"type":"line","lineWidth":2,"overlay":False},{"name":"Lower Outer","title":True,"data":[-2.5]*n,"color":bull,"type":"line","lineWidth":2,"overlay":False},{"name":"Long-Term Momentum","title":False,"data":_finite(long_term),"color":bull,"negativeColor":bear,"type":"line","overlay":False}],
"signals":[{"type":"buy","name":"Bullish Reversal","text":"▲","data":[{"active":True,"price":float(l[i]),"text":"▲","color":bull} if bullish[i] else None for i in range(n)],"color":bull,"renderMode":"events","overlay":True},{"type":"sell","name":"Bearish Reversal","text":"▼","data":[{"active":True,"price":float(h[i]),"text":"▼","color":bear} if bearish[i] else None for i in range(n)],"color":bear,"renderMode":"events","overlay":True}],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,
"calculatedVars":{"basis":_finite(basis),"deviation":_finite(deviation),"zscore":_finite(zscore),"stochasticZ":_finite(stochastic),"scaled":_finite(scaled),"oscillator":_finite(oscillator),"previousOscillator":_finite(previous),"longTermMomentum":_finite(long_term),"momentumShift":momentum_shift.tolist(),"bullishReversal":bullish.tolist(),"bearishReversal":bearish.tolist(),"zeroCrossUp":zero_up.tolist(),"zeroCrossDown":zero_down.tolist(),"overbought":(oscillator>2.5).tolist(),"oversold":(oscillator<-2.5).tolist()}}
