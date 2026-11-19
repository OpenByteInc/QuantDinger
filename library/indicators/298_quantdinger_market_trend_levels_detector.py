# ============================================================
# QuantDinger Market Trend Levels Detector
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================
my_indicator_name="QuantDinger Market Trend Levels Detector"
my_indicator_description="Creates support and resistance levels at EMA trend changes and optionally extends them until price invalidation."
# @param fast_length int 12 Fast EMA length range=1:500:1
# @param slow_length int 25 Slow EMA length range=2:1000:1
# @param extend_lines bool false Extend levels until invalidated
# @param display_last int 50 Display last levels range=1:500:1
# @param bullish_color color #26BA9F Bullish color
# @param bearish_color color #6626BA Bearish color
fast_length=min(500,max(1,int(params.get("fast_length",12))));slow_length=min(1000,max(2,int(params.get("slow_length",25))));extend_lines=bool(params.get("extend_lines",False));display_last=min(500,max(1,int(params.get("display_last",50))));bullish_color=str(params.get("bullish_color","#26BA9F"));bearish_color=str(params.get("bearish_color","#6626BA"))
def _finite(v):return [float(x) if np.isfinite(x) else None for x in v]
def _ema(v,p):
 r=np.full(len(v),np.nan);a=2/(p+1);s=np.nan
 for i,x in enumerate(v):s=x if not np.isfinite(s) else a*x+(1-a)*s;r[i]=s
 return r
def _rgb(c):
 t=c.lstrip('#');return np.array([int(t[i:i+2],16) for i in (0,2,4)],float)
def _gradient(x,a,b,c1,c2):
 q=0 if b==a else min(1,max(0,(x-a)/(b-a)));v=np.rint(_rgb(c1)+(_rgb(c2)-_rgb(c1))*q).astype(int);return f"#{v[0]:02X}{v[1]:02X}{v[2]:02X}"
df=df.copy()
frame=df.reset_index(drop=True)
if "time" not in frame.columns:frame["time"]=np.arange(len(frame),dtype=float)*3600000.0
if "volume" not in frame.columns:frame["volume"]=0.0
for c in ("time","open","high","low","close","volume"):frame[c]=pd.to_numeric(frame[c],errors="coerce")
frame=frame.dropna(subset=["time","open","high","low","close"]).reset_index(drop=True);times=frame["time"].to_numpy(float)
if len(times) and np.nanmax(np.abs(times))<100000000000.:times=times*1000
high=frame.high.to_numpy(float);low=frame.low.to_numpy(float);close=frame.close.to_numpy(float);n=len(frame);fast=_ema(close,fast_length);slow=_ema(close,slow_length);difference=fast-slow
cross_up=np.zeros(n,bool);cross_down=np.zeros(n,bool)
for i in range(1,n):cross_up[i]=fast[i]>slow[i] and fast[i-1]<=slow[i-1];cross_down[i]=fast[i]<slow[i] and fast[i-1]>=slow[i-1]
rolling_low=pd.Series(low).rolling(slow_length,min_periods=1).min().to_numpy();rolling_high=pd.Series(high).rolling(slow_length,min_periods=1).max().to_numpy();lines=[];labels=[]
for i in range(n):
 if cross_up[i]:
  for lag in range(0,min(slow_length,i)+1):
   if low[i-lag]==rolling_low[i]:lines.append({'side':'support','start':i-lag,'end':i-lag+25,'level':float(rolling_low[i]),'color':bullish_color,'dotted':False,'active':True,'deleted':False});labels.append((i-lag,float(rolling_low[i]),bullish_color,'up'))
 if cross_down[i]:
  for lag in range(0,min(slow_length,i)+1):
   if high[i-lag]==rolling_high[i]:lines.append({'side':'resistance','start':i-lag,'end':i-lag+25,'level':float(rolling_high[i]),'color':bearish_color,'dotted':False,'active':True,'deleted':False});labels.append((i-lag,float(rolling_high[i]),bearish_color,'down'))
 if extend_lines:
  for line in lines:
   if line['deleted'] or not line['active']:continue
   line['end']=i
   if (line['side']=='resistance' and high[i]>line['level']) or (line['side']=='support' and low[i]<line['level']):line['dotted']=True;line['active']=False
 visible=[line for line in lines if not line['deleted']]
 if len(visible)>display_last:visible[0]['deleted']=True
layers=[]
for line in lines:
 if not line['deleted']:
  start=max(0,min(n-1,line['start']));end_time=times[line['end']] if line['end']<n else times[-1]+(line['end']-(n-1))*(times[-1]-times[-2] if n>1 else 60000)
  layers.append({'type':'line','role':line['side'].title(),'start':float(times[start]),'end':float(end_time),'y1':line['level'],'y2':line['level'],'color':line['color'],'lineWidth':1,'dashed':line['dotted']})
diff_high=pd.Series(difference).rolling(100,min_periods=1).max().to_numpy();diff_low=pd.Series(difference).rolling(100,min_periods=1).min().to_numpy();cloud=[]
for i in range(n):
 color=_gradient(difference[i],0,diff_high[i],"#80DCCD",bullish_color) if difference[i]>=0 else _gradient(difference[i],diff_low[i],0,bearish_color,"#A380D5")
 cloud.append({'upper':float(max(fast[i],slow[i])),'lower':float(min(fast[i],slow[i])),'color':color})
plots=[{'name':'EMA Trend Cloud','data':cloud,'color':bullish_color,'type':'cloud','overlay':True},{'name':'Fast EMA','title':False,'data':_finite(fast),'color':'rgba(0,0,0,0)','type':'line','overlay':True},{'name':'Slow EMA','title':False,'data':_finite(slow),'color':'rgba(0,0,0,0)','type':'line','overlay':True}]
signals=[]
output={'schema_version':2,'name':my_indicator_name,'plots':plots,'signals':signals,'layers':layers,'tables':[],'bar_colors':[None]*n,'background_colors':[None]*n,'calculatedVars':{'fastEMA':_finite(fast),'slowEMA':_finite(slow),'difference':_finite(difference),'crossUp':cross_up.tolist(),'crossDown':cross_down.tolist(),'rollingLow':_finite(rolling_low),'rollingHigh':_finite(rolling_high),'levelLines':lines}}
