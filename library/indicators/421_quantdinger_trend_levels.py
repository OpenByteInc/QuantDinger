# QuantDinger Trend Levels
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trend Levels"
my_indicator_description="Switches trend on rolling range extremes, tracks the active trend's high, low and midpoint and colors price from the balance of closes above or below the midpoint."

# @param length int 30 Range length range=2:300:1
# @param projection_bars int 10 Level projection bars range=1:100:1
# @param bullish_color str #00FF00 Bullish color
# @param bearish_color str #FF00FF Bearish color

length=max(2,min(300,int(params.get("length",30)))); projection=max(1,min(100,int(params.get("projection_bars",10)))); bullish_color=str(params.get("bullish_color","#00FF00")); bearish_color=str(params.get("bearish_color","#FF00FF"))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); trend=np.zeros(n,dtype=int); active_high=np.full(n,np.nan); active_low=np.full(n,np.nan); midpoint=np.full(n,np.nan); delta=np.full(n,np.nan); switch_up=np.zeros(n,bool); switch_down=np.zeros(n,bool); current=0; start=0; count_up=count_down=0
for i in range(n):
    high_range=np.max(h[max(0,i-length+1):i+1]); low_range=np.min(l[max(0,i-length+1):i+1]); previous=current
    if h[i]==high_range: current=1
    if l[i]==low_range: current=-1
    if current!=previous and current!=0: switch_up[i]=current==1; switch_down[i]=current==-1; start=i; count_up=count_down=0
    bars=i-start+1
    if bars>1:
        active_high[i]=float(np.max(h[start:i+1])); active_low[i]=float(np.min(l[start:i+1])); midpoint[i]=(active_high[i]+active_low[i])/2
        if c[i]>midpoint[i]: count_up+=1
        elif c[i]<midpoint[i]: count_down+=1
        total=count_up+count_down; delta[i]=(count_up-count_down)/total*100 if total else 0
    trend[i]=current
bar_colors=[]
for value in delta:
    if not np.isfinite(value): bar_colors.append(None); continue
    weight=float(np.clip((value+50)/100,0,1)); red=int(round(255*(1-weight))); green=int(round(255*weight)); blue=int(round(255*(1-weight))); bar_colors.append(f"rgb({red},{green},{blue})")
layers=[]
if n and np.all(np.isfinite([active_high[-1],active_low[-1],midpoint[-1]])):
    for role,value,color,style in (("Upper",active_high[-1],bullish_color,"solid"),("Mid",midpoint[-1],"#808080","dashed"),("Lower",active_low[-1],bearish_color,"solid")): layers.append({"type":"line","role":role,"startIndex":n-1,"endIndex":n-1+projection,"y1":float(value),"y2":float(value),"color":color,"lineWidth":1,"lineStyle":style})
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
tables=[]
if n: tables=[{"title":"Trend Levels","position":"top-right","compact":True,"rows":[[{"text":"Trend"},{"text":"Up" if trend[-1]==1 else "Down" if trend[-1]==-1 else "Waiting"}],[{"text":"Balance"},{"text":"—" if not np.isfinite(delta[-1]) else f"{delta[-1]:+.1f}%"}],[{"text":"Upper"},{"text":"—" if not np.isfinite(active_high[-1]) else f"{active_high[-1]:.6g}"}],[{"text":"Mid"},{"text":"—" if not np.isfinite(midpoint[-1]) else f"{midpoint[-1]:.6g}"}],[{"text":"Lower"},{"text":"—" if not np.isfinite(active_low[-1]) else f"{active_low[-1]:.6g}"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Trend High","title":True,"data":_finite(active_high),"color":bullish_color,"type":"line","lineWidth":1,"overlay":True},{"name":"Trend Mid","title":True,"data":_finite(midpoint),"color":"#808080","type":"line","lineWidth":1,"lineStyle":"dashed","overlay":True},{"name":"Trend Low","title":True,"data":_finite(active_low),"color":bearish_color,"type":"line","lineWidth":1,"overlay":True}],"signals":[_events(switch_up,l,"⇗",bullish_color,"buy"),_events(switch_down,h,"⇘",bearish_color,"sell")],"layers":layers,"tables":tables,"bar_colors":bar_colors,"background_colors":[None]*n,"calculatedVars":{"trend":trend.tolist(),"activeHigh":_finite(active_high),"activeLow":_finite(active_low),"midpoint":_finite(midpoint),"deltaPercent":_finite(delta),"switchUp":switch_up.tolist(),"switchDown":switch_down.tolist()}}
