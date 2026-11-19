# QuantDinger Stochastic Heat Map
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Stochastic Heat Map"
my_indicator_description="Stacks up to 28 smoothed stochastic horizons into a heat map with fast and slow aggregate waves."

# @param ma_type str EMA Smoothing type values=SMA,EMA,WMA
# @param theme str Theme 3 Color theme values=Theme 1,Theme 2,Theme 3
# @param increment int 10 Length increment range=1:100:1
# @param fast_smoothing int 2 Fast smoothing range=1:100:1
# @param slow_smoothing int 21 Slow smoothing range=1:500:1
# @param plot_number int 28 Number of heat rows range=1:28:1
# @param paint_bars bool true Paint bars
# @param waves bool false Use nonlinear wave lengths

ma_type=str(params.get("ma_type","EMA")); ma_type=ma_type if ma_type in ("SMA","EMA","WMA") else "EMA"; theme=str(params.get("theme","Theme 3")); increment=max(1,min(100,int(params.get("increment",10)))); smooth=max(1,min(100,int(params.get("fast_smoothing",2)))); slow_length=max(1,min(500,int(params.get("slow_smoothing",21)))); plot_number=max(1,min(28,int(params.get("plot_number",28)))); paint=bool(params.get("paint_bars",True)); waves=bool(params.get("waves",False))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _ma(v,length,kind):
    out=np.full(len(v),np.nan)
    if kind=="EMA":
        state=np.nan; a=2/(length+1)
        for i,x in enumerate(v):
            if np.isfinite(x): state=float(x) if not np.isfinite(state) else state+a*(float(x)-state); out[i]=state
        return out
    weights=np.arange(1,length+1,dtype=float)
    for i in range(length-1,len(v)):
        w=v[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.mean(w) if kind=="SMA" else np.dot(w,weights)/np.sum(weights))
    return out
def _stoch(close,high,low,length):
    out=np.full(len(close),np.nan)
    for i in range(length-1,len(close)):
        hi=np.max(high[i-length+1:i+1]); lo=np.min(low[i-length+1:i+1]); out[i]=50 if hi==lo else 100*(close[i]-lo)/(hi-lo)
    return out
def _color(value):
    if not np.isfinite(value): return None
    palettes={"Theme 1":["#750202","#910303","#B80606","#D60606","#FF0505","#027502","#039103","#04B504","#05D904","#01FF00"],"Theme 2":["#640275","#7B038F","#9B05B5","#BB04DB","#D800FF","#046775","#047F91","#049CB3","#04BCD9","#00DDFF"],"Theme 3":["#02269E","#0F44F5","#1176F2","#0093C9","#11E7F2","#3AF211","#EEF211","#F29811","#F25811","#CF0000"]}; return palettes.get(theme,palettes["Theme 3"])[max(0,min(9,int(value//10)))]
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame); nonlinear=[1,2,3,4,5,6,7,8,9,10,15,20,25,30,35,40,45,50,55,60,70,80,90,100,110,120,140,160]; rows=[]
for row in range(plot_number):
    factor=nonlinear[row] if waves else row+1; rows.append(_ma(_stoch(close,high,low,factor*increment),smooth+(row+1 if waves else 0),ma_type))
matrix=np.vstack(rows); valid_count=np.sum(np.isfinite(matrix),axis=0); average=np.divide(np.nansum(matrix,axis=0),valid_count,out=np.full(n,np.nan),where=valid_count>0); fast=average/100*plot_number; slow=_ma(fast,slow_length,ma_type); heat=[]
for row,values in enumerate(rows): heat.append([{"value":float(row),"color":_color(values[i])} if np.isfinite(values[i]) else None for i in range(n)])
bar_colors=[_color(average[i]) if paint else None for i in range(n)]
plots=[{"name":f"Heat {row+1}","title":False,"data":heat[row],"color":"#808080","type":"heatmap","overlay":False} for row in range(plot_number)]; plots.extend([{"name":"Fast Wave","title":True,"data":_finite(fast),"color":"#FFFFFF","type":"line","lineWidth":1,"overlay":False},{"name":"Slow Wave","title":True,"data":_finite(slow),"color":"#FFA500","type":"line","lineWidth":1,"overlay":False}])
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[],"layers":[],"tables":[],"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"stochastics":{str(i+1):_finite(rows[i]) for i in range(plot_number)},"average":_finite(average),"fast":_finite(fast),"slow":_finite(slow),"lengthFactors":nonlinear[:plot_number] if waves else list(range(1,plot_number+1))}}
