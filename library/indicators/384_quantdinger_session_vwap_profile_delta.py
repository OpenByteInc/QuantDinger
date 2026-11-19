# ============================================================
# QuantDinger Session VWAP Profile and Candle Delta
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Session VWAP Profile and Candle Delta"
my_indicator_description = "Builds higher-timeframe anchored VWAP deviation bands, distributed volume profiles, candle counts, POC levels and outer-band reversals."
indicator_timeframe_param = "manual_timeframe"
indicator_timeframes = ["15m", "45m", "2H", "200m", "360m", "720m", "1D", "2D", "4D", "1W", "2W", "1M", "3M", "7M", "12M", "18M", "48M", "240M"]

# @param auto_timeframe bool true Automatically select session timeframe
# @param manual_timeframe str 1D Manual session timeframe values=15m,45m,2H,200m,360m,720m,1D,2D,4D,1W,2W,1M,3M,7M,12M,18M,48M,240M
# @param stdev_multiplier float 2.0 Standard-deviation multiplier range=0.1:20:0.1
# @param auto_bins bool true Automatically select bin count
# @param manual_bins int 50 Manual bin count range=10:250:1
# @param max_sessions int 3 Maximum retained sessions range=1:50:1
# @param right_offset int 35 Active profile offset range=5:200:1
# @param show_labels bool true Show bullish and bearish candle counts
# @param show_poc bool true Show point of control
# @param show_signals bool true Show outer-band signals
# @param up_color str #0000FF Rising VWAP profile color
# @param down_color str #FF0000 Falling VWAP profile color
# @param base_color str #787B86 Low-density and band color
# @param poc_color str #FF0000 POC color

auto_timeframe = bool(params.get("auto_timeframe", True)); manual_timeframe = str(params.get("manual_timeframe", "1D"))
stdev_multiplier = min(20.0, max(0.1, float(params.get("stdev_multiplier", 2.0))))
auto_bins = bool(params.get("auto_bins", True)); manual_bins = min(250, max(10, int(params.get("manual_bins", 50))))
max_sessions = min(50, max(1, int(params.get("max_sessions", 3)))); right_offset = min(200, max(5, int(params.get("right_offset", 35))))
show_labels = bool(params.get("show_labels", True)); show_poc = bool(params.get("show_poc", True)); show_signals = bool(params.get("show_signals", True))
up_color = str(params.get("up_color", "#0000FF")); down_color = str(params.get("down_color", "#FF0000")); base_color = str(params.get("base_color", "#787B86")); poc_color = str(params.get("poc_color", "#FF0000"))


def _finite(values): return [float(value) if np.isfinite(value) else None for value in values]


def _rma(values, length):
    result=np.full(len(values),np.nan);state=np.nan;seed=[]
    for index,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==length: state=float(np.mean(seed));result[index]=state
        else: state=(state*(length-1.0)+float(value))/length;result[index]=state
    return result


def _chart_parts(text):
    value=str(text or "1H").strip()
    aliases={"1":"1m","2":"2m","5":"5m","15":"15m","30":"30m","60":"1H","120":"2H","240":"4H","D":"1D","W":"1W","M":"1M"}
    value=aliases.get(value,value)
    if value.endswith("s"): return "seconds",max(1,int(value[:-1] or 1))
    if value.endswith("m"): return "minutes",max(1,int(value[:-1] or 1))
    if value.endswith("H"): return "minutes",max(1,int(value[:-1] or 1))*60
    if value.endswith("D"): return "days",max(1,int(value[:-1] or 1))
    if value.endswith("W"): return "weeks",max(1,int(value[:-1] or 1))
    if value.endswith("M"): return "months",max(1,int(value[:-1] or 1))
    return "minutes",60


def _auto_htf(chart_text):
    kind,multiplier=_chart_parts(chart_text)
    if not auto_timeframe: return manual_timeframe
    if kind=="seconds": return "15m" if multiplier<=5 else "45m" if multiplier<=15 else "2H"
    if kind=="minutes":
        exact={1:"200m",2:"360m",5:"720m",15:"2D",30:"4D",60:"1W",120:"2W"}
        return exact.get(multiplier,"1D" if multiplier<60 else "1M" if multiplier<360 else "3M")
    if kind=="days": return "7M" if multiplier==1 else "12M" if multiplier==2 else "18M"
    if kind=="weeks": return "48M"
    return "240M"


def _period_key(times, timeframe):
    kind,multiplier=_chart_parts(timeframe)
    if kind=="seconds": return np.floor(times/multiplier).astype(np.int64)
    if kind=="minutes": return np.floor(times/(multiplier*60.0)).astype(np.int64)
    if kind=="days": return np.floor(times/(multiplier*86400.0)).astype(np.int64)
    if kind=="weeks": return np.floor((times-4*86400.0)/(multiplier*7*86400.0)).astype(np.int64)
    stamp=pd.to_datetime(times,unit="s",utc=True);months=np.asarray(stamp.year,dtype=np.int64)*12+np.asarray(stamp.month,dtype=np.int64)-1
    return months//multiplier


def _rgba_mix(base,target,weight):
    def rgb(value):
        text=value.lstrip("#"); return [int(text[index:index+2],16) for index in (0,2,4)] if len(text)==6 else [120,123,134]
    left,right=rgb(base),rgb(target);mixed=[int(round(left[index]+(right[index]-left[index])*weight)) for index in range(3)]
    return f"rgb({mixed[0]},{mixed[1]},{mixed[2]})"


df=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in df.columns: df[column]=np.nan if column=="volume" else 0.0
    df[column]=pd.to_numeric(df[column],errors="coerce")
df=df.dropna(subset=["time","open","high","low","close"]).reset_index(drop=True)
times=df["time"].to_numpy(float)
if len(times) and np.nanmax(np.abs(times))>10_000_000_000: times=times/1000.0
opened,high,low,close,volume=(df[key].to_numpy(float) for key in ("open","high","low","close","volume"));volume=np.where(np.isfinite(volume),volume,0.0);n=len(df)
runtime_ctx=globals().get("ctx");chart_timeframe=str(getattr(runtime_ctx,"timeframe",None) or "1H");selected_timeframe=_auto_htf(chart_timeframe);keys=_period_key(times,selected_timeframe);session_change=np.zeros(n,bool)
if n>1: session_change[1:]=keys[1:]!=keys[:-1]
previous_close=np.r_[np.nan,close[:-1]];true_range=np.maximum(high-low,np.maximum(abs(high-previous_close),abs(low-previous_close)))
if n:true_range[0]=high[0]-low[0]
atr=_rma(true_range,14)
vwap=np.full(n,np.nan);stdev=np.full(n,np.nan);upper=np.full(n,np.nan);lower=np.full(n,np.nan);rising=np.zeros(n,bool);session_start=np.zeros(n,int);bin_count=np.full(n,manual_bins,int)
sum_volume=sum_pv=sum_p2v=0.0;start=0;records=[]
for index in range(n):
    if session_change[index]:
        records.append({"startIndex":start,"endIndex":index-1});start=index;sum_volume=sum_pv=sum_p2v=0.0
    sum_volume+=volume[index];sum_pv+=close[index]*volume[index];sum_p2v+=close[index]*close[index]*volume[index]
    center=sum_pv/sum_volume if sum_volume!=0 else np.nan;variance=max(0.0,sum_p2v/sum_volume-center*center) if sum_volume!=0 else np.nan
    vwap[index]=center;stdev[index]=np.sqrt(variance) if np.isfinite(variance) else np.nan;upper[index]=center+stdev[index]*stdev_multiplier;lower[index]=center-stdev[index]*stdev_multiplier
    rising[index]=index>0 and np.isfinite(vwap[index-1]) and center>=vwap[index-1];session_start[index]=start
    if auto_bins and np.isfinite(atr[index]) and atr[index]>0 and upper[index]>lower[index]: bin_count[index]=min(250,max(10,int(np.floor((upper[index]-lower[index])/(atr[index]*0.5)+0.5))))
if n: records.append({"startIndex":start,"endIndex":n-1})


def _profile(record,is_active):
    start_index,end_index=record["startIndex"],record["endIndex"];bins=int(bin_count[end_index]);low_band,high_band=lower[end_index],upper[end_index]
    if not np.isfinite(low_band) or not np.isfinite(high_band) or high_band<=low_band: return {**record,"binCount":bins,"bins":[],"pocBin":0,"pocPrice":None}
    step=(high_band-low_band)/bins;totals=np.zeros(bins);bull=np.zeros(bins,int);bear=np.zeros(bins,int)
    for index in range(start_index,end_index+1):
        price=close[index]
        if low_band<=price<=high_band:
            bin_index=min(bins-1,int(np.floor((price-low_band)/step)));totals[bin_index]+=volume[index]*0.5
            if close[index]>=opened[index]:bull[bin_index]+=1
            else:bear[bin_index]+=1
            if bin_index>0:totals[bin_index-1]+=volume[index]*0.25
            if bin_index+1<bins:totals[bin_index+1]+=volume[index]*0.25
    maximum=float(np.max(totals)) if len(totals) else 0.0;poc=int(np.argmax(totals)) if maximum>0 else 0;items=[];right=end_index+right_offset if is_active else end_index
    for bin_index,total in enumerate(totals):
        if total>0:
            bottom=low_band+bin_index*step;top=bottom+step;span=max(1,int(np.floor(total/maximum*30+0.5)));left=max(start_index,right-span) if not is_active else right-span
            items.append({"index":bin_index,"bottom":float(bottom),"top":float(top),"mid":float((bottom+top)/2),"volume":float(total),"bullCount":int(bull[bin_index]),"bearCount":int(bear[bin_index]),"leftIndex":int(left),"rightIndex":int(right),"color":_rgba_mix(base_color,up_color if rising[end_index] else down_color,float(total/maximum))})
    return {**record,"binCount":bins,"lowerBand":float(low_band),"upperBand":float(high_band),"bins":items,"pocBin":poc,"pocPrice":float(low_band+(poc+0.5)*step) if maximum>0 else None}

retained=records[-max_sessions:];profiles=[_profile(record,index==len(retained)-1 and record["endIndex"]==n-1) for index,record in enumerate(retained)]
layers=[]
for profile in profiles:
    active=profile["endIndex"]==n-1
    label_bin_indexes=set()
    if show_labels and active:
        label_bin_indexes={item["index"] for item in sorted(profile["bins"],key=lambda value:value["volume"],reverse=True)[:6]}
    for item in profile["bins"]:
        layers.append({"type":"line","role":"Active Profile Bin" if active else "Historical Profile Bin","startIndex":item["leftIndex"],"endIndex":item["rightIndex"],"y1":item["mid"],"y2":item["mid"],"color":item["color"],"lineWidth":1,"lineStyle":"solid" if active else "dotted"})
        if item["index"] in label_bin_indexes: layers.append({"type":"label","role":"Candle Delta","index":item["rightIndex"],"price":item["mid"],"text":f"[+{item['bullCount']} | -{item['bearCount']}]","color":"rgba(0,0,0,0)","textColor":"#99A1BD","size":"tiny"})
    if show_poc and profile["pocPrice"] is not None:
        poc_item=next((item for item in profile["bins"] if item["index"]==profile["pocBin"]),None);left=poc_item["leftIndex"] if poc_item else profile["startIndex"]
        layers.append({"type":"line","role":"Point of Control","startIndex":left,"endIndex":profile["startIndex"],"y1":profile["pocPrice"],"y2":profile["pocPrice"],"color":poc_color,"lineWidth":2,"lineStyle":"solid" if active else "dashed"})

upper_signal=np.zeros(n,bool);lower_signal=np.zeros(n,bool);last_signal=-9999
for index in range(n):
    gap=index-last_signal>=10
    upper_signal[index]=show_signals and gap and np.isfinite(upper[index]) and high[index]>upper[index] and close[index]<upper[index]
    lower_signal[index]=show_signals and gap and np.isfinite(lower[index]) and low[index]<lower[index] and close[index]>lower[index]
    if upper_signal[index] or lower_signal[index]:last_signal=index
band_cloud=[{"upper":float(upper[index]),"lower":float(lower[index]),"color":base_color,"opacity":0.05} if np.all(np.isfinite([upper[index],lower[index]])) else None for index in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"VWAP","title":True,"data":_finite(vwap),"color":up_color,"type":"line","lineWidth":2,"overlay":True},{"name":"Upper Band","title":True,"data":_finite(upper),"color":"#808080","type":"line","overlay":True},{"name":"Lower Band","title":True,"data":_finite(lower),"color":"#808080","type":"line","overlay":True},{"name":"Band Fill","title":False,"data":band_cloud,"color":base_color,"type":"cloud","overlay":True}],"signals":[{"type":"sell","name":"Upper Reversal","text":"SELL","data":[{"active":True,"price":float(high[index]+atr[index]),"text":"SELL","color":down_color} if upper_signal[index] and np.isfinite(atr[index]) else None for index in range(n)],"color":down_color,"renderMode":"events","overlay":True},{"type":"buy","name":"Lower Reversal","text":"BUY","data":[{"active":True,"price":float(low[index]-atr[index]),"text":"BUY","color":up_color} if lower_signal[index] and np.isfinite(atr[index]) else None for index in range(n)],"color":up_color,"renderMode":"events","overlay":True}],"layers":layers,"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"selectedTimeframe":selected_timeframe,"sessionChange":session_change.tolist(),"sessionStart":session_start.tolist(),"sumVolumeByBar":_finite(np.asarray([np.sum(volume[session_start[index]:index+1]) for index in range(n)])),"vwap":_finite(vwap),"stdev":_finite(stdev),"upperBand":_finite(upper),"lowerBand":_finite(lower),"isVwapRising":rising.tolist(),"binCount":bin_count.tolist(),"upperSignal":upper_signal.tolist(),"lowerSignal":lower_signal.tolist(),"sessionRecords":records,"profiles":profiles}}
