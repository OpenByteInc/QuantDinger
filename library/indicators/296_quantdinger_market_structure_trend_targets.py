# ============================================================
# QuantDinger Market Structure Trend Targets
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Structure Trend Targets"
my_indicator_description = "Counts repeated structure breaks and derives a median-range trend target with optional trailing-stop markers."

# @param length int 10 Pivot length range=2:30:1
# @param display_percent bool false Display target progress as percent
# @param display_trailing_stop bool true Display trailing stop
# @param bullish_color color #54B6D4 Bullish color
# @param bearish_color color #CF3737 Bearish color

length = min(30, max(2, int(params.get("length", 10))))
display_percent = bool(params.get("display_percent", False))
display_trailing_stop = bool(params.get("display_trailing_stop", True))
bullish_color = str(params.get("bullish_color", "#54B6D4")); bearish_color = str(params.get("bearish_color", "#CF3737"))

def _finite(values): return [float(v) if np.isfinite(v) else None for v in values]

frame=df.copy().reset_index(drop=True)
if "time" not in frame.columns: frame["time"]=np.arange(len(frame),dtype=float)*3600000.0
if "volume" not in frame.columns: frame["volume"]=0.0
for column in ("time","open","high","low","close","volume"): frame[column]=pd.to_numeric(frame[column],errors="coerce")
frame=frame.dropna(subset=["time","open","high","low","close"]).reset_index(drop=True)
times=frame["time"].to_numpy(float)
if len(times) and np.nanmax(np.abs(times))<100000000000.0: times=times*1000.0
open_price=frame["open"].to_numpy(float);high=frame["high"].to_numpy(float);low=frame["low"].to_numpy(float);close=frame["close"].to_numpy(float);n=len(frame)

pivot_high=np.full(n,np.nan);pivot_low=np.full(n,np.nan)
for index in range(2*length,n):
    candidate=index-length
    if high[candidate]>np.max(high[candidate-length:candidate]) and high[candidate]>=np.max(high[candidate+1:candidate+length+1]): pivot_high[index]=high[candidate]
    if low[candidate]<np.min(low[candidate-length:candidate]) and low[candidate]<=np.min(low[candidate+1:candidate+length+1]): pivot_low[index]=low[candidate]
range_source=pd.Series(high-low).rolling(50,min_periods=50).mean().to_numpy()*2.0
rolling_median=pd.Series(close).rolling(40,min_periods=40).median().to_numpy()
center=pd.Series(rolling_median).rolling(10,min_periods=10).mean().to_numpy()
source_high=pd.Series(range_source).rolling(200,min_periods=1).max().to_numpy();source_low=pd.Series(range_source).rolling(200,min_periods=1).min().to_numpy();volatility=(source_high+source_low)*0.5

upper=lower=np.nan;upper_index=lower_index=0;upper_count=lower_count=0;upper_entry=lower_entry=0.0;trend=False
upper_series=np.full(n,np.nan);lower_series=np.full(n,np.nan);upper_break=np.zeros(n,bool);lower_break=np.zeros(n,bool);upper_count_history=np.zeros(n,int);lower_count_history=np.zeros(n,int);trend_history=np.zeros(n,bool);trend_line=np.full(n,np.nan)
structure_layers=[];structure_labels=[];stop_marker_up=np.zeros(n,bool);stop_marker_down=np.zeros(n,bool);trend_up=np.zeros(n,bool);trend_down=np.zeros(n,bool)
for index in range(n):
    previous_upper=upper_series[index-1] if index>0 else np.nan;previous_lower=lower_series[index-1] if index>0 else np.nan
    if np.isfinite(pivot_high[index]): upper=float(pivot_high[index]);upper_index=index-length
    if np.isfinite(pivot_low[index]): lower=float(pivot_low[index]);lower_index=index-length
    crossed_upper=np.isfinite(upper) and high[index]>upper and index>0 and np.isfinite(previous_upper) and high[index-1]<=previous_upper
    crossed_lower=np.isfinite(lower) and low[index]<lower and index>0 and np.isfinite(previous_lower) and low[index-1]>=previous_lower
    previous_trend=trend
    if crossed_upper:
        upper_break[index]=True;upper_count+=1
        if upper_count==1: upper_entry=upper
        percent=(upper-upper_entry)/upper_entry*100.0 if upper_entry else np.nan; text=f"{upper_entry:.1f} △" if upper_count==1 else f"{percent:.2f}%" if display_percent else str(upper_count-1)
        midpoint=int(index-(index-upper_index)/2);structure_layers.append((upper_index,index,upper,bullish_color));structure_labels.append((midpoint,upper,text,bullish_color,"down"));upper=np.nan;lower_count=0;trend=True
    if crossed_lower:
        lower_break[index]=True;lower_count+=1
        if lower_count==1: lower_entry=lower
        percent=(lower-lower_entry)/lower_entry*100.0 if lower_entry else np.nan;text=f"{lower_entry:.1f} ▽" if lower_count==1 else f"{percent:.2f}%" if display_percent else str(lower_count-1)
        midpoint=int(index-(index-lower_index)/2);structure_layers.append((lower_index,index,lower,bearish_color));structure_labels.append((midpoint,lower,text,bearish_color,"up"));lower=np.nan;upper_count=0;trend=False
    upper_series[index]=upper;lower_series[index]=lower;upper_count_history[index]=upper_count;lower_count_history[index]=lower_count;trend_history[index]=trend
    trend_line[index]=center[index]-volatility[index] if trend else center[index]+volatility[index]
    if index>0 and np.all(np.isfinite([trend_line[index],trend_line[index-1]])):
        low_cross_up=low[index]>trend_line[index] and low[index-1]<=trend_line[index-1]
        high_cross_down=high[index]<trend_line[index] and high[index-1]>=trend_line[index-1]
        stop_marker_up[index]=display_trailing_stop and trend and low_cross_up and previous_trend
        stop_marker_down[index]=display_trailing_stop and not trend and high_cross_down and not previous_trend
    trend_up[index]=trend and not previous_trend;trend_down[index]=not trend and previous_trend

stop_loss=trend_line.copy()
for index in range(n):
    if index>0 and trend_history[index]!=trend_history[index-1]: stop_loss[index]=np.nan
    elif trend_history[index] and np.isfinite(stop_loss[index]) and low[index]<stop_loss[index]: stop_loss[index]=np.nan
    elif not trend_history[index] and np.isfinite(stop_loss[index]) and high[index]>stop_loss[index]: stop_loss[index]=np.nan
if not display_trailing_stop: stop_loss[:]=np.nan
layers=[]
for start,end,price,color in structure_layers: layers.append({"type":"line","role":"Structure Target","start":float(times[start]),"end":float(times[end]),"y1":price,"y2":price,"color":color,"lineWidth":1})
visible_label_start=max(0,n-180)
for index,price,text,color,direction in structure_labels:
    if index>=visible_label_start: layers.append({"type":"label","index":index,"price":price,"text":text,"textColor":color,"color":"rgba(0,0,0,0)","direction":direction,"size":"small"})
line_colors=[bullish_color if value else bearish_color for value in trend_history]
cloud=[{"upper":float(max((high[index]+low[index])*0.5,stop_loss[index])),"lower":float(min((high[index]+low[index])*0.5,stop_loss[index])),"color":f"{line_colors[index]}1A"} if np.isfinite(stop_loss[index]) else None for index in range(n)]
plots=[{"name":"Trailing Stop","data":_finite(stop_loss),"color":line_colors,"type":"line","style":"linebr","lineWidth":1,"overlay":True},{"name":"Trailing Stop Glow","title":False,"data":_finite(stop_loss),"color":[f"{color}33" for color in line_colors],"type":"line","style":"linebr","lineWidth":5,"overlay":True},{"name":"Trend Area","title":False,"data":cloud,"color":bullish_color,"type":"cloud","overlay":True},{"name":"State","title":False,"data":_finite(close),"color":"rgba(0,0,0,0)","type":"line","overlay":True}]
signals=[{"type":"buy","name":"Bullish Stop Retest","text":"◆","data":[float(low[i-1]) if stop_marker_up[i] else None for i in range(n)],"color":bullish_color,"renderMode":"events"},{"type":"sell","name":"Bearish Stop Retest","text":"◆","data":[float(high[i-1]) if stop_marker_down[i] else None for i in range(n)],"color":bearish_color,"renderMode":"events"},{"type":"buy","name":"Bullish Trend","text":"△","data":[float(trend_line[i]) if trend_up[i] else None for i in range(n)],"color":bullish_color,"renderMode":"events"},{"type":"sell","name":"Bearish Trend","text":"▽","data":[float(trend_line[i]) if trend_down[i] else None for i in range(n)],"color":bearish_color,"renderMode":"events"}]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":signals,"layers":layers,"tables":[],"bar_colors":[f"{color}80" for color in line_colors],"background_colors":[None]*n,"calculatedVars":{"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"rangeSource":_finite(range_source),"center":_finite(center),"volatility":_finite(volatility),"upper":_finite(upper_series),"lower":_finite(lower_series),"upperBreak":upper_break.tolist(),"lowerBreak":lower_break.tolist(),"upperCount":upper_count_history.tolist(),"lowerCount":lower_count_history.tolist(),"trend":trend_history.tolist(),"trendLine":_finite(trend_line),"stopLoss":_finite(stop_loss),"stopMarkerUp":stop_marker_up.tolist(),"stopMarkerDown":stop_marker_down.tolist(),"trendUp":trend_up.tolist(),"trendDown":trend_down.tolist()}}
