# QuantDinger Volume-Supported Linear Regression Trend
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Volume-Supported Linear Regression Trend"
my_indicator_description="Compares short- and long-horizon price regression slopes with estimated buy/sell volume regression slopes to grade directional trend strength."

# @param short_length int 20 Short length range=5:500:1
# @param long_length int 50 Long length range=5:1000:1

short_length=max(5,min(500,int(params.get("short_length",20)))); long_length=max(5,min(1000,int(params.get("long_length",50))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _slope(values,length):
    out=np.full(len(values),np.nan); x=np.arange(length,dtype=float); xm=float(x.mean()); divisor=float(np.sum((x-xm)**2))
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.sum((x-xm)*(sample-sample.mean()))/divisor)
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); top_wick=h-np.maximum(o,c); bottom_wick=np.minimum(o,c)-l; body=np.abs(c-o); total=top_wick+bottom_wick+body; buy_rate=np.divide(0.5*(top_wick+bottom_wick+np.where(o<=c,2*body,0)),total,out=np.full(n,0.5),where=total!=0); sell_rate=np.divide(0.5*(top_wick+bottom_wick+np.where(o>c,2*body,0)),total,out=np.full(n,0.5),where=total!=0); buy_volume=v*buy_rate; sell_volume=v*sell_rate
short_price=_slope(c,short_length); long_price=_slope(c,long_length); short_buy=_slope(buy_volume,short_length); short_sell=_slope(sell_volume,short_length); long_buy=_slope(buy_volume,long_length); long_sell=_slope(sell_volume,long_length)
def _grade(price,buy,sell):
    out=np.zeros(n,dtype=int)
    for i in range(n):
        if not np.all(np.isfinite([price[i],buy[i],sell[i]])): continue
        if price[i]>0: out[i]=3 if buy[i]>0 and buy[i]>sell[i] else 2 if buy[i]>0 else 1
        elif price[i]<0: out[i]=-3 if sell[i]>0 and buy[i]<sell[i] else -2 if sell[i]>0 else -1
    return out
short_grade=_grade(short_price,short_buy,short_sell); long_grade=_grade(long_price,long_buy,long_sell); short_plot=short_price*short_length; long_plot=long_price*long_length
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Short Trend","title":True,"data":_finite(short_plot),"color":"#00FF00","negativeColor":"#FF0000","type":"histogram","lineWidth":2,"overlay":False},{"name":"Long Trend","title":True,"data":_finite(long_plot),"color":"#008EFF","negativeColor":"#FD9701","type":"line","lineWidth":3,"overlay":False}],"signals":[],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"buyRate":_finite(buy_rate),"sellRate":_finite(sell_rate),"buyVolume":_finite(buy_volume),"sellVolume":_finite(sell_volume),"shortPriceSlope":_finite(short_price),"longPriceSlope":_finite(long_price),"shortBuyVolumeSlope":_finite(short_buy),"shortSellVolumeSlope":_finite(short_sell),"longBuyVolumeSlope":_finite(long_buy),"longSellVolumeSlope":_finite(long_sell),"shortGrade":short_grade.tolist(),"longGrade":long_grade.tolist()}}
