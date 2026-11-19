# QuantDinger Oscillator Trend Lines
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Oscillator Trend Lines"
my_indicator_description="Draws validated rising support and falling resistance trend lines across pivots in RSI, CCI, momentum-style CCI or OBV."

# @param indicator str RSI Oscillator values=RSI,CCI,Momentum,OBV
# @param source str close Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param rsi_length int 14 RSI length range=1:200:1
# @param cci_length int 20 CCI length range=1:300:1
# @param momentum_length int 10 Momentum length range=1:300:1
# @param pivot_period int 10 Pivot period range=5:50:1
# @param pivot_count int 3 Pivot points to check range=2:3:1

indicator=str(params.get("indicator","RSI")); source_name=str(params.get("source","close")); rsi_length=max(1,min(200,int(params.get("rsi_length",14)))); cci_length=max(1,min(300,int(params.get("cci_length",20)))); momentum_length=max(1,min(300,int(params.get("momentum_length",10)))); pivot_period=max(5,min(50,int(params.get("pivot_period",10)))); pivot_count=max(2,min(3,int(params.get("pivot_count",3))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
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
def _cci(values,length):
    basis=_sma(values,length); out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)):
            dev=float(np.mean(np.abs(sample-basis[i]))); out[i]=(values[i]-basis[i])/(0.015*dev) if dev>0 else 0
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); n=len(frame); source={"open":o,"high":h,"low":l,"close":c,"hl2":(h+l)/2,"hlc3":(h+l+c)/3,"ohlc4":(o+h+l+c)/4}.get(source_name,c); rsi=_rsi(source,rsi_length); cci=_cci(source,cci_length); momentum=_cci(source,momentum_length); obv=np.zeros(n)
for i in range(1,n): obv[i]=obv[i-1]+(volume[i] if c[i]>c[i-1] else -volume[i] if c[i]<c[i-1] else 0)
oscillator={"RSI":rsi,"CCI":cci,"Momentum":momentum,"OBV":obv}.get(indicator,rsi); pivot_high=[]; pivot_low=[]
for confirmed in range(2*pivot_period,n):
    center=confirmed-pivot_period; sample=oscillator[center-pivot_period:center+pivot_period+1]
    if np.all(np.isfinite(sample)) and oscillator[center]==np.max(sample): pivot_high.append((center,float(oscillator[center])))
    if np.all(np.isfinite(sample)) and oscillator[center]==np.min(sample): pivot_low.append((center,float(oscillator[center])))
layers=[]; support_lines=[]; resistance_lines=[]
for first in range(min(pivot_count-1,max(0,len(pivot_low)-1))):
    newer=pivot_low[-1-first]
    for older_offset in range(pivot_count,first+1,-1):
        if len(pivot_low)<older_offset: continue
        older=pivot_low[-older_offset]
        if newer[1]<=older[1] or newer[0]==older[0]: continue
        slope=(newer[1]-older[1])/(newer[0]-older[0]); valid=True
        for index in range(older[0]+1,n):
            if np.isfinite(oscillator[index]) and oscillator[index]<older[1]+slope*(index-older[0])-1e-10: valid=False; break
        if valid: support_lines.append((older[0],older[1],n-1,older[1]+slope*(n-1-older[0]))); break
for first in range(min(pivot_count-1,max(0,len(pivot_high)-1))):
    newer=pivot_high[-1-first]
    for older_offset in range(pivot_count,first+1,-1):
        if len(pivot_high)<older_offset: continue
        older=pivot_high[-older_offset]
        if newer[1]>=older[1] or newer[0]==older[0]: continue
        slope=(newer[1]-older[1])/(newer[0]-older[0]); valid=True
        for index in range(older[0]+1,n):
            if np.isfinite(oscillator[index]) and oscillator[index]>older[1]+slope*(index-older[0])+1e-10: valid=False; break
        if valid: resistance_lines.append((older[0],older[1],n-1,older[1]+slope*(n-1-older[0]))); break
for item in support_lines: layers.append({"type":"line","role":"Oscillator Support","startIndex":item[0],"endIndex":item[2],"y1":item[1],"y2":item[3],"color":"#00C853","lineWidth":1})
for item in resistance_lines: layers.append({"type":"line","role":"Oscillator Resistance","startIndex":item[0],"endIndex":item[2],"y1":item[1],"y2":item[3],"color":"#FF1744","lineWidth":1})
upper=[70.0 if indicator=="RSI" else 100.0 if indicator=="CCI" else None for _ in range(n)]; lower=[30.0 if indicator=="RSI" else -100.0 if indicator=="CCI" else None for _ in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":indicator,"title":True,"data":_finite(oscillator),"color":"#FFFFFF","type":"line","lineWidth":2,"overlay":False},{"name":"Upper Level","title":True,"data":upper,"color":"#9915FF","type":"line","lineWidth":1,"overlay":False},{"name":"Lower Level","title":True,"data":lower,"color":"#9915FF","type":"line","lineWidth":1,"overlay":False}],"signals":[],"layers":layers,"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"oscillator":_finite(oscillator),"rsi":_finite(rsi),"cci":_finite(cci),"momentum":_finite(momentum),"obv":_finite(obv),"pivotHigh":[{"index":x,"value":y} for x,y in pivot_high],"pivotLow":[{"index":x,"value":y} for x,y in pivot_low],"supportLines":[list(item) for item in support_lines],"resistanceLines":[list(item) for item in resistance_lines]}}
