# QuantDinger Isotropic Trend Lines
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name = "QuantDinger Isotropic Trend Lines"
my_indicator_description = "Builds Yang-Zhang-volatility-normalized log-price channels and six-scale trend consensus."

# @param calculation_bar str Live Calculation bar values=Live,Closed
# @param trend_block_period int 13 Trend block period range=2:200:1
# @param trend_block_groups int 5 Trend block groups range=2:20:1
# @param range_threshold float 0.5 Range threshold in degrees range=0:45:0.1
# @param sigma_length int 20 Yang-Zhang sigma length range=2:500:1
# @param projection_offset int 7 Projection offset range=0:100:1
# @param show_channel bool true Show channel
# @param show_fill bool true Show channel fill
# @param show_dashboard bool true Show dashboard
# @param up_color str #26A69A Up color
# @param down_color str #EF5350 Down color
# @param range_color str #888888 Range color

closed_bar=str(params.get("calculation_bar","Live"))=="Closed"; period=max(2,min(200,int(params.get("trend_block_period",13)))); groups=max(2,min(20,int(params.get("trend_block_groups",5)))); threshold=max(0,min(45,float(params.get("range_threshold",0.5)))); sigma_length=max(2,min(500,int(params.get("sigma_length",20)))); projection=max(0,min(100,int(params.get("projection_offset",7)))); show_channel=bool(params.get("show_channel",True)); show_fill=bool(params.get("show_fill",True)); show_dashboard=bool(params.get("show_dashboard",True)); up_color=str(params.get("up_color","#26A69A")); down_color=str(params.get("down_color","#EF5350")); range_color=str(params.get("range_color","#888888"))

def _finite(values): return [float(v) if np.isfinite(v) else None for v in values]
def _rolling(values,length,kind):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        w=values[i-length+1:i+1]
        if not np.all(np.isfinite(w)): continue
        out[i]=float(np.var(w,ddof=0) if kind=="variance" else np.mean(w))
    return out
def _line(p1,x1,p2,x2,target):
    if x1==x2 or p1<=0 or p2<=0: return p1
    return float(np.exp(np.log(p1)+(np.log(p2)-np.log(p1))*(target-x1)/(x2-x1)))
def _analyze(index,scale,sig):
    anchor=1 if closed_bar else 0
    if index < groups*scale+anchor: return 0,0.0,0,np.nan,np.nan
    gm=[]; highs=[]; lows=[]; centers=[]
    for block in range(groups):
        end=index-anchor-block*scale; start=end-scale+1
        if start<0: return 0,0.0,0,np.nan,np.nan
        hi=float(np.max(high[start:end+1])); lo=float(np.min(low[start:end+1])); gm.append(float(np.sqrt(hi*lo)) if hi>0 and lo>0 else np.nan); highs.append(hi); lows.append(lo); centers.append(end-scale//2)
    if not np.isfinite(gm[0]) or not np.isfinite(gm[1]) or gm[0]==gm[1]: return 0,0.0,0,np.nan,np.nan
    direction=1 if gm[0]>gm[1] else -1; segment=1
    for block in range(1,groups-1):
        relation=1 if gm[block]>gm[block+1] else -1 if gm[block]<gm[block+1] else 0
        if relation==direction: segment=block+1
        else: break
    old=gm[segment]; new=gm[0]; dx=centers[0]-centers[segment]; angle=float(np.degrees(np.arctan((np.log(new)-np.log(old))/max(sig,1e-10)/dx))) if dx and old>0 and new>0 else 0.0
    final_direction=0 if abs(angle)<=threshold else direction
    use=slice(0,segment+1); hs=np.asarray(highs[use],float); ls=np.asarray(lows[use],float); xs=np.asarray(centers[use],int); hh=int(np.argmax(hs)); lh=int(np.argmin(hs)); hl=int(np.argmax(ls)); ll=int(np.argmin(ls)); now=index-anchor
    if final_direction==1: upper=_line(hs[lh],xs[lh],hs[hh],xs[hh],now); lower=_line(ls[ll],xs[ll],ls[hl],xs[hl],now)
    elif final_direction==-1: upper=_line(hs[hh],xs[hh],hs[lh],xs[lh],now); lower=_line(ls[hl],xs[hl],ls[ll],xs[ll],now)
    else: upper=float(hs[hh]); lower=float(ls[ll])
    return final_direction,angle,segment,upper,lower

frame=df.copy().reset_index(drop=True)
for c in ("time","open","high","low","close","volume"):
    if c not in frame.columns: frame[c]=0.0
    frame[c]=pd.to_numeric(frame[c],errors="coerce")
opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame)
previous=np.r_[np.nan,close[:-1]]; overnight=np.log(np.divide(opened,previous,out=np.ones(n),where=(opened>0)&(previous>0))); intraday=np.log(np.divide(close,opened,out=np.ones(n),where=(close>0)&(opened>0))); ho=np.log(np.divide(high,opened,out=np.ones(n),where=(high>0)&(opened>0))); hc=np.log(np.divide(high,close,out=np.ones(n),where=(high>0)&(close>0))); lo=np.log(np.divide(low,opened,out=np.ones(n),where=(low>0)&(opened>0))); lc=np.log(np.divide(low,close,out=np.ones(n),where=(low>0)&(close>0)))
var_or=_rolling(overnight,sigma_length,"variance"); var_co=_rolling(intraday,sigma_length,"variance"); rs=_rolling(ho*hc+lo*lc,sigma_length,"mean"); k=0.34/(1.34+(sigma_length+1.0)/max(sigma_length-1.0,1.0)); sigma=np.sqrt(np.maximum(np.nan_to_num(var_or)+k*np.nan_to_num(var_co)+(1-k)*np.nan_to_num(rs),0)); sigma=np.maximum(sigma,1e-10)
scales=(3,7,13,19,29,47); directions={s:np.zeros(n,dtype=int) for s in scales}; angles={s:np.zeros(n) for s in scales}; primary_direction=np.zeros(n,dtype=int); primary_angle=np.zeros(n); segment=np.zeros(n,dtype=int); channel_up=np.full(n,np.nan); channel_down=np.full(n,np.nan)
for i in range(n):
    for scale in scales:
        d,a,se,up,dn=_analyze(i,scale,sigma[i]); directions[scale][i]=d; angles[scale][i]=a
    d,a,se,up,dn=_analyze(i,period,sigma[i]); primary_direction[i]=d; primary_angle[i]=a; segment[i]=se; channel_up[i]=up; channel_down[i]=dn
up_count=np.sum(np.vstack([directions[s]==1 for s in scales]),axis=0).astype(int); down_count=np.sum(np.vstack([directions[s]==-1 for s in scales]),axis=0).astype(int); flat_count=6-up_count-down_count
channel_position=np.full(n,np.nan); valid=(close>0)&(channel_up>channel_down)&(channel_down>0); channel_position[valid]=(np.log(close[valid])-np.log(channel_down[valid]))/(np.log(channel_up[valid])-np.log(channel_down[valid]))
break_up=valid&(channel_position>1); break_down=valid&(channel_position<0); enter_up=np.zeros(n,bool); enter_down=np.zeros(n,bool); direction_up=np.zeros(n,bool); direction_down=np.zeros(n,bool); direction_range=np.zeros(n,bool)
if n>1:
    enter_up[1:]=break_up[1:]&~break_up[:-1]; enter_down[1:]=break_down[1:]&~break_down[:-1]; direction_up[1:]=(primary_direction[1:]==1)&(primary_direction[:-1]!=1); direction_down[1:]=(primary_direction[1:]==-1)&(primary_direction[:-1]!=-1); direction_range[1:]=(primary_direction[1:]==0)&(primary_direction[:-1]!=0)
cloud=[{"upper":float(channel_up[i]),"lower":float(channel_down[i]),"color":up_color if primary_direction[i]==1 else down_color if primary_direction[i]==-1 else range_color,"opacity":0.15} if show_fill and np.all(np.isfinite([channel_up[i],channel_down[i]])) else None for i in range(n)]
layers=[]
if n and show_channel and np.all(np.isfinite([channel_up[-1],channel_down[-1]])):
    color=up_color if primary_direction[-1]==1 else down_color if primary_direction[-1]==-1 else range_color; start=max(0,n-1-period*groups)
    layers=[{"type":"line","role":"Upper Projection","startIndex":start,"endIndex":n-1+projection,"y1":float(channel_up[-1]),"y2":float(channel_up[-1]),"color":color,"lineWidth":2,"lineStyle":"dotted"},{"type":"line","role":"Lower Projection","startIndex":start,"endIndex":n-1+projection,"y1":float(channel_down[-1]),"y2":float(channel_down[-1]),"color":color,"lineWidth":2,"lineStyle":"dotted"}]
tables=[]
if n and show_dashboard:
    winner="UP" if up_count[-1]>down_count[-1] and up_count[-1]>flat_count[-1] else "DOWN" if down_count[-1]>up_count[-1] and down_count[-1]>flat_count[-1] else "RANGE"; count=max(up_count[-1],down_count[-1],flat_count[-1]); color=up_color if winner=="UP" else down_color if winner=="DOWN" else range_color
    tables=[{"title":"Isotropic Trend","position":"top-right","compact":True,"rows":[[{"text":"Primary"},{"text":"UP" if primary_direction[-1]==1 else "DOWN" if primary_direction[-1]==-1 else "RANGE","color":up_color if primary_direction[-1]==1 else down_color if primary_direction[-1]==-1 else range_color}],[{"text":"Angle"},{"text":f"{primary_angle[-1]:+.2f}°"}],[{"text":"Consensus"},{"text":f"{winner} {count}/6","color":color}],[{"text":"Channel position"},{"text":"—" if not np.isfinite(channel_position[-1]) else f"{channel_position[-1]*100:.0f}%"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Isotropic Channel","title":False,"data":cloud,"color":up_color,"type":"cloud","overlay":True},{"name":"Upper Channel","title":True,"data":_finite(channel_up) if show_channel else [None]*n,"color":up_color,"negativeColor":down_color,"type":"line","lineWidth":2,"overlay":True},{"name":"Lower Channel","title":True,"data":_finite(channel_down) if show_channel else [None]*n,"color":up_color,"negativeColor":down_color,"type":"line","lineWidth":2,"overlay":True},{"name":"Angle","title":False,"data":_finite(primary_angle),"color":range_color,"type":"line","overlay":False}],
    "signals":[{"type":"buy","name":"Uptrend Entry","text":"▲","data":[{"active":True,"price":float(low[i]),"text":"▲","color":up_color} if direction_up[i] else None for i in range(n)],"color":up_color,"renderMode":"events","overlay":True},{"type":"sell","name":"Downtrend Entry","text":"▼","data":[{"active":True,"price":float(high[i]),"text":"▼","color":down_color} if direction_down[i] else None for i in range(n)],"color":down_color,"renderMode":"events","overlay":True},{"type":"buy","name":"Breakout Up","text":"BO","data":[{"active":True,"price":float(high[i]),"text":"BO","color":up_color} if enter_up[i] else None for i in range(n)],"color":up_color,"renderMode":"events","overlay":True},{"type":"sell","name":"Breakout Down","text":"BO","data":[{"active":True,"price":float(low[i]),"text":"BO","color":down_color} if enter_down[i] else None for i in range(n)],"color":down_color,"renderMode":"events","overlay":True}],"layers":layers,"tables":tables,"bar_colors":[None]*n,"background_colors":[None]*n,
    "calculatedVars":{"sigma":_finite(sigma),"direction":primary_direction.tolist(),"angle":_finite(primary_angle),"segment":segment.tolist(),"channelUp":_finite(channel_up),"channelDown":_finite(channel_down),"channelPosition":_finite(channel_position),"upCount":up_count.tolist(),"downCount":down_count.tolist(),"flatCount":flat_count.tolist(),"scaleDirections":{str(s):directions[s].tolist() for s in scales},"scaleAngles":{str(s):_finite(angles[s]) for s in scales},"breakoutUp":enter_up.tolist(),"breakoutDown":enter_down.tolist()}}
