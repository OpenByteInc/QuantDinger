# QuantDinger VWAP Volume Profile
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger VWAP Volume Profile"
my_indicator_description="Plots session VWAP and bins the latest VWAP path into a signed-volume profile with positive and negative VWAP control zones."

# @param period int 250 Profile period range=20:5000:1
# @param bins int 50 Profile bins range=5:200:1
# @param profile_offset int 10 Profile offset range=0:100:1
# @param poc_type str +/-VWAP POC type values=+VWAP,-VWAP,+/-VWAP

period=max(20,min(5000,int(params.get("period",250)))); bins=max(5,min(200,int(params.get("bins",50)))); offset=max(0,min(100,int(params.get("profile_offset",10)))); poc_type=str(params.get("poc_type","+/-VWAP"))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _seconds(values):
    out=np.asarray(values,dtype=float).copy(); finite=out[np.isfinite(out)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: out/=1000
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); dates=np.asarray(pd.to_datetime(_seconds(frame["time"].to_numpy(float)),unit="s",utc=True).strftime("%Y-%m-%d")); vwap=np.full(n,np.nan); cumulative_pv=0.0; cumulative_v=0.0; current=None
for i in range(n):
    if current!=dates[i]: current=dates[i]; cumulative_pv=0.0; cumulative_v=0.0
    cumulative_pv+=c[i]*v[i]; cumulative_v+=v[i]; vwap[i]=cumulative_pv/cumulative_v if cumulative_v else np.nan
signed=np.zeros(n); max_volume=pd.Series(v).rolling(1000,min_periods=1).max().to_numpy(float)
for i in range(n): signed[i]=(v[i] if i>=2 and vwap[i]>vwap[i-2] else -v[i])/max(max_volume[i],1e-12)*20
start=max(0,n-period); recent_vwap=vwap[start:]; recent_volume=v[start:]; recent_signed=signed[start:]; layers=[]; profile=[]; positive_poc=None; negative_poc=None
if len(recent_vwap) and np.any(np.isfinite(recent_vwap)):
    high_bound=float(np.nanmax(h[start:])); low_bound=float(np.nanmin(l[start:])); step=(high_bound-low_bound)/bins if high_bound>low_bound else 1.0; signed_bins=np.zeros(bins); volume_bins=np.zeros(bins)
    for i,value in enumerate(recent_vwap):
        if not np.isfinite(value): continue
        index=int(np.clip((value-low_bound)/step,0,bins-1)); signed_bins[index]+=recent_signed[i]; volume_bins[index]+=recent_volume[i]
    max_abs=max(float(np.max(np.abs(signed_bins))),1e-12); positive_index=int(np.argmax(signed_bins)); negative_index=int(np.argmin(signed_bins)); positive_poc=low_bound+(positive_index+0.5)*step; negative_poc=low_bound+(negative_index+0.5)*step
    for index in range(bins):
        bottom=low_bound+index*step; top=bottom+step; width=int(round(abs(signed_bins[index])/max_abs*20)); direction=1 if signed_bins[index]>0 else -1 if signed_bins[index]<0 else 0; profile.append({"bin":index,"bottom":bottom,"top":top,"signedVolume":float(signed_bins[index]),"volume":float(volume_bins[index]),"direction":direction}); layers.append({"type":"zone","role":"VWAP Profile","startIndex":n+offset,"endIndex":n+offset+direction*width,"top":top,"bottom":bottom,"fillColor":"rgba(66,189,168,0.55)" if direction>0 else "rgba(255,185,80,0.55)","borderColor":"rgba(0,0,0,0)"})
    if poc_type in ("+VWAP","+/-VWAP"): layers.append({"type":"zone","role":"Positive VWAP POC","startIndex":start,"endIndex":n+offset,"top":positive_poc+step/2,"bottom":positive_poc-step/2,"fillColor":"rgba(66,189,168,0.12)","borderColor":"rgba(66,189,168,0.50)"})
    if poc_type in ("-VWAP","+/-VWAP"): layers.append({"type":"zone","role":"Negative VWAP POC","startIndex":start,"endIndex":n+offset,"top":negative_poc+step/2,"bottom":negative_poc-step/2,"fillColor":"rgba(255,185,80,0.12)","borderColor":"rgba(255,185,80,0.50)"})
trend=np.zeros(n,dtype=int)
if n>5: trend[5:]=np.where(vwap[5:]>vwap[:-5],1,-1)
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"VWAP","title":True,"data":_finite(np.where(np.arange(n)>=start,vwap,np.nan)),"color":"#42BDA8","negativeColor":"#FFB950","type":"line","lineWidth":3,"overlay":True}],"signals":[],"layers":layers,"tables":[],"bar_colors":["rgba(66,189,168,0.70)" if trend[i]>0 else "rgba(255,185,80,0.70)" if trend[i]<0 else None for i in range(n)],"background_colors":[None]*n,"calculatedVars":{"vwap":_finite(vwap),"signedVolume":_finite(signed),"trend":trend.tolist(),"profile":profile,"positivePOC":positive_poc,"negativePOC":negative_poc,"profileStart":start}}
