# ============================================================
# QuantDinger Signal Moving Average
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Signal Moving Average"
my_indicator_description = "Blends toward a source average when price-time correlation is strong and otherwise extrapolates at the target average's change rate."

# @param length int 50 Correlation and average length range=2:1000:1
# @param source str close Source values=close,open,high,low,hl2,hlc3,ohlc4
# @param bullish_color str #0CB51A Bullish color
# @param bearish_color str #FF1100 Bearish color

length=min(1000,max(2,int(params.get("length",50))));source_name=str(params.get("source","close")).lower();bullish_color=str(params.get("bullish_color","#0CB51A"));bearish_color=str(params.get("bearish_color","#FF1100"))


def _finite(values): return [float(value) if np.isfinite(value) else None for value in values]


df=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in df.columns: df[column]=np.nan if column=="volume" else 0.0
    df[column]=pd.to_numeric(df[column],errors="coerce")
df=df.dropna(subset=["time","open","high","low","close"]).reset_index(drop=True)
opened,high,low,close=(df[key].to_numpy(float) for key in ("open","high","low","close"));n=len(df);sources={"close":close,"open":opened,"high":high,"low":low,"hl2":(high+low)/2.0,"hlc3":(high+low+close)/3.0,"ohlc4":(opened+high+low+close)/4.0};source_name=source_name if source_name in sources else "close";source=sources[source_name]
target=np.full(n,np.nan);correlation=np.full(n,np.nan);r_squared=np.full(n,np.nan);direction=np.zeros(n);signal_average=np.full(n,np.nan)
bar_index=np.arange(n,dtype=float)
for index in range(length-1,n):
    source_window=source[index-length+1:index+1]
    if np.all(np.isfinite(source_window)): target[index]=float(np.mean(source_window))
    price_window=close[index-length+1:index+1];index_window=bar_index[index-length+1:index+1]
    if np.all(np.isfinite(price_window)):
        price_centered=price_window-np.mean(price_window);index_centered=index_window-np.mean(index_window);denominator=np.sqrt(np.sum(price_centered*price_centered)*np.sum(index_centered*index_centered))
        correlation[index]=float(np.sum(price_centered*index_centered)/denominator) if denominator!=0 else np.nan
        r_squared[index]=correlation[index]*correlation[index] if np.isfinite(correlation[index]) else np.nan
for index in range(n):
    previous_direction=direction[index-1] if index else 0.0;direction[index]=previous_direction
    if r_squared[index]>0.5 and index and np.all(np.isfinite([source[index-1],target[index-1]])): direction[index]=float(np.sign(source[index-1]-target[index-1]))
    previous_average=signal_average[index-1] if index else np.nan
    if r_squared[index]>0.5 and np.isfinite(target[index]): signal_average[index]=r_squared[index]*target[index]+(1.0-r_squared[index])*(previous_average if np.isfinite(previous_average) else target[index])
    elif index and np.isfinite(previous_average) and np.all(np.isfinite([target[index],target[index-1]])): signal_average[index]=previous_average-abs(target[index]-target[index-1])*direction[index]
line_colors=[bullish_color if value==1 else bearish_color for value in direction]
fill=[{"upper":float(max(source[index],signal_average[index])),"lower":float(min(source[index],signal_average[index])),"color":bullish_color if source[index]>signal_average[index] else bearish_color,"opacity":0.20} if np.all(np.isfinite([source[index],signal_average[index]])) else None for index in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Signal MA","title":True,"data":_finite(signal_average),"color":line_colors,"type":"line","lineWidth":2,"overlay":True},{"name":"Source Fill","title":False,"data":fill,"color":bullish_color,"type":"cloud","overlay":True},{"name":"Price Guide","title":False,"data":_finite(close),"color":"rgba(0,0,0,0)","type":"line","overlay":True}],"signals":[],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"source":_finite(source),"target":_finite(target),"correlation":_finite(correlation),"rSquared":_finite(r_squared),"direction":_finite(direction),"signalAverage":_finite(signal_average)}}
