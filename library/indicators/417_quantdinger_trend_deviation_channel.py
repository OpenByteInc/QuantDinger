# QuantDinger Trend Deviation Channel
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trend Deviation Channel"
my_indicator_description="Fits a rolling linear trend, learns asymmetric ATR-capped residual widths, classifies trend strength and tracks expansion or re-entry across deviation rails."

# @param trend_length int 200 Trend length range=20:400:1
# @param enter_strength float 1 Trend enter strength range=0.1:10:0.1
# @param exit_strength float 0.25 Trend exit strength range=0:5:0.05
# @param deviation_shape str Asymmetric Deviation shape values=Asymmetric,Symmetric
# @param residual_cap_atr float 4 Residual cap ATR range=1:20:0.25
# @param minimum_width_atr float 0.05 Minimum width ATR range=0.01:1:0.01
# @param minimum_side_samples int 12 Minimum side samples range=3:100:1
# @param deviation_1 float 1 First rail range=0.25:5:0.25
# @param deviation_2 float 2 Second rail range=0.5:6:0.25
# @param deviation_3 float 3 Third rail range=0.75:8:0.25
# @param stretch_memory float 0.9 Stretch memory range=0.5:0.99:0.01
# @param show_profile bool true Show deviation profile
# @param profile_bins int 20 Profile bins range=11:61:1

length=max(20,min(400,int(params.get("trend_length",200)))); enter=max(0.1,min(10,float(params.get("enter_strength",1)))); exit_strength=max(0,min(5,float(params.get("exit_strength",0.25)))); shape=str(params.get("deviation_shape","Asymmetric")); cap_atr=max(1,min(20,float(params.get("residual_cap_atr",4)))); min_width=max(0.01,min(1,float(params.get("minimum_width_atr",0.05)))); minimum_side=max(3,min(100,int(params.get("minimum_side_samples",12)))); d1=max(0.25,float(params.get("deviation_1",1))); d2=max(d1+0.1,float(params.get("deviation_2",2))); d3=max(d2+0.1,float(params.get("deviation_3",3))); memory=max(0.5,min(0.99,float(params.get("stretch_memory",0.9)))); show_profile=bool(params.get("show_profile",True)); bins=max(11,min(61,int(params.get("profile_bins",20))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _rma(values,window):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed)==window: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(window-1)+float(value))/window; out[i]=state
    return out
def _cross_over(a,level):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(a[:-1])&(a[1:]>level)&(a[:-1]<=level)
    return out
def _cross_under(a,level):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(a[:-1])&(a[1:]<level)&(a[:-1]>=level)
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,100); regression=np.full(n,np.nan); slope=np.full(n,np.nan); upper_width=np.full(n,np.nan); lower_width=np.full(n,np.nan); baseline=np.full(n,np.nan); zscore=np.full(n,np.nan); efficiency=np.full(n,np.nan); trend_strength=np.full(n,np.nan); required=np.full(n,np.nan); trend=np.zeros(n,dtype=int); stretch=np.zeros(n); x=np.arange(length,dtype=float); x_mean=float(np.mean(x)); denominator=float(np.sum((x-x_mean)**2)); current_trend=0; current_stretch=0.0
for i in range(length,n):
    sample=c[i-length:i]
    if not np.all(np.isfinite(sample)) or denominator==0 or not np.isfinite(atr[i-1]) or atr[i-1]<=0: trend[i]=current_trend; stretch[i]=current_stretch; continue
    y_mean=float(np.mean(sample)); sl=float(np.dot(x-x_mean,sample-y_mean)/denominator); re=float(y_mean+sl*((length-1)-x_mean)); regression[i]=re; slope[i]=sl; capped=atr[i-1]*cap_atr; residuals=[]; positive=[]; negative=[]
    for lag in range(1,length+1):
        residual=float(c[i-lag]-(re-sl*(lag-1))); value=float(np.clip(residual,-capped,capped)); residuals.append(value); (positive if value>=0 else negative).append(value)
    shared=float(np.sqrt(np.mean(np.square(residuals)))); up=float(np.sqrt(np.mean(np.square(positive)))) if len(positive)>=minimum_side else shared; down=float(np.sqrt(np.mean(np.square(negative)))) if len(negative)>=minimum_side else shared; floor=atr[i-1]*min_width; up=max(up,floor); down=max(down,floor)
    if shape=="Symmetric": up=down=max(shared,floor)
    upper_width[i]=up; lower_width[i]=down; base=re+sl; baseline[i]=base; residual=c[i]-base; zscore[i]=residual/up if residual>=0 else residual/down; path=float(np.sum(np.abs(np.diff(sample)))); displacement=abs(sample[-1]-sample[0]); ef=displacement/path if path>0 else 0.0; efficiency[i]=ef; strength=sl*(length-1)/atr[i-1]; trend_strength[i]=strength; req=enter*(1.5-0.5*min(1,max(0,ef/0.18))); required[i]=req
    if current_trend==0: current_trend=1 if strength>req else -1 if strength<-req else 0
    elif current_trend==1: current_trend=-1 if strength<-req else 0 if strength<exit_strength else 1
    else: current_trend=1 if strength>req else 0 if strength>-exit_strength else -1
    current_stretch=min(3.0,current_stretch*memory+max(abs(zscore[i])-d1,0)*(1-memory)); trend[i]=current_trend; stretch[i]=current_stretch
for i in range(1,n):
    if trend[i]==0 and trend[i-1]!=0 and not np.isfinite(baseline[i]): trend[i]=trend[i-1]
rails={"Upper 1":baseline+d1*upper_width,"Upper 2":baseline+d2*upper_width,"Upper 3":baseline+d3*upper_width,"Lower 1":baseline-d1*lower_width,"Lower 2":baseline-d2*lower_width,"Lower 3":baseline-d3*lower_width}; upper_expansion=_cross_over(zscore,d2); lower_expansion=_cross_under(zscore,-d2); upper_reentry=_cross_under(zscore,d2); lower_reentry=_cross_over(zscore,-d2); upper_expansion3=_cross_over(zscore,d3); lower_expansion3=_cross_under(zscore,-d3); upper_reentry3=_cross_under(zscore,d3); lower_reentry3=_cross_over(zscore,-d3)
layers=[]; profile_counts=[0]*bins; profile_range=max(3.1,d3)
if show_profile:
    values=zscore[max(0,n-length):n]; finite_values=values[np.isfinite(values)&(values>=-profile_range)&(values<=profile_range)]
    if len(finite_values):
        counts,edges=np.histogram(finite_values,bins=bins,range=(-profile_range,profile_range)); profile_counts=counts.astype(int).tolist(); maximum=max(profile_counts) if profile_counts else 0
        if maximum and n and np.isfinite(baseline[-1]):
            for index,count in enumerate(profile_counts):
                if count<=0: continue
                low_z=float(edges[index]); high_z=float(edges[index+1]); low_price=float(baseline[-1]+(low_z*upper_width[-1] if low_z>=0 else low_z*lower_width[-1])); high_price=float(baseline[-1]+(high_z*upper_width[-1] if high_z>=0 else high_z*lower_width[-1])); width=max(1,int(round(24*count/maximum))); layers.append({"type":"zone","role":"Deviation Profile","startIndex":n+12,"endIndex":n+12+width,"top":max(low_price,high_price),"bottom":min(low_price,high_price),"fillColor":"#FF7043" if (low_z+high_z)/2>0 else "#00BCD4","borderColor":"rgba(0,0,0,0)","opacity":0.30})
def _thin(mask,limit=2,min_gap=15):
    selected=[]
    for value in np.flatnonzero(mask)[::-1]:
        index=int(value)
        if all(abs(index-other)>=min_gap for other in selected): selected.append(index)
        if len(selected)>=limit: break
    result=np.zeros(n,bool); result[selected]=True
    return result
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
plots=[{"name":"Trend","title":True,"data":_finite(baseline),"color":"#12D1EB","negativeColor":"#FA2856","type":"line","lineWidth":3,"overlay":True}]
for name,series,color,style in (("Upper 1",rails["Upper 1"],"#FF7043","solid"),("Upper 2",rails["Upper 2"],"#FF7043","dashed"),("Upper 3",rails["Upper 3"],"#FF7043","dotted"),("Lower 1",rails["Lower 1"],"#00BCD4","solid"),("Lower 2",rails["Lower 2"],"#00BCD4","dashed"),("Lower 3",rails["Lower 3"],"#00BCD4","dotted")): plots.append({"name":name,"title":True,"data":_finite(series),"color":color,"type":"line","lineWidth":1 if "1" in name or "3" in name else 2,"lineStyle":style,"overlay":True})
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":[_events(_thin(upper_expansion),h,"2D","#FF7043","sell"),_events(_thin(lower_expansion),l,"2D","#00BCD4","buy"),_events(_thin(upper_reentry),h,"2D IN","#FF7043","event"),_events(_thin(lower_reentry),l,"2D IN","#00BCD4","event"),_events(_thin(upper_expansion3),h,"3D","#FF7043","sell"),_events(_thin(lower_expansion3),l,"3D","#00BCD4","buy"),_events(_thin(upper_reentry3),h,"3D IN","#FF7043","event"),_events(_thin(lower_reentry3),l,"3D IN","#00BCD4","event")],"layers":layers,"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"atr":_finite(atr),"regression":_finite(regression),"slope":_finite(slope),"baseline":_finite(baseline),"upperWidth":_finite(upper_width),"lowerWidth":_finite(lower_width),"zscore":_finite(zscore),"efficiency":_finite(efficiency),"trendStrength":_finite(trend_strength),"requiredStrength":_finite(required),"trend":trend.tolist(),"stretchMemory":_finite(stretch),"upperExpansion":upper_expansion.tolist(),"lowerExpansion":lower_expansion.tolist(),"upperReentry":upper_reentry.tolist(),"lowerReentry":lower_reentry.tolist(),"profileCounts":profile_counts}}
