# QuantDinger Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Support and Resistance"
my_indicator_description="Tracks the latest confirmed pivot high and pivot low as stepped resistance and support levels with the original left-shifted visual alignment."

# @param left_bars int 10 Pivot left bars range=1:200:1
# @param right_bars int 10 Pivot right bars range=1:200:1
# @param resistance_color str #00CED1 Resistance color
# @param support_color str #DC143C Support color

left=max(1,min(200,int(params.get("left_bars",10)))); right=max(1,min(200,int(params.get("right_bars",10)))); resistance_color=str(params.get("resistance_color","#00CED1")); support_color=str(params.get("support_color","#DC143C"))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); n=len(frame); pivot_high=np.full(n,np.nan); pivot_low=np.full(n,np.nan); high_confirmed=np.full(n,np.nan); low_confirmed=np.full(n,np.nan)
for confirmed in range(left+right,n):
    center=confirmed-right; high_window=h[center-left:center+right+1]; low_window=l[center-left:center+right+1]
    if np.isfinite(h[center]) and h[center]==np.nanmax(high_window): pivot_high[center]=h[center]; high_confirmed[confirmed]=h[center]
    if np.isfinite(l[center]) and l[center]==np.nanmin(low_window): pivot_low[center]=l[center]; low_confirmed[confirmed]=l[center]
resistance=np.full(n,np.nan); support=np.full(n,np.nan); resistance_change=np.zeros(n,bool); support_change=np.zeros(n,bool); updates=[]
for confirmed in range(n):
    if np.isfinite(high_confirmed[confirmed]): updates.append((max(0,confirmed-left),"resistance",float(high_confirmed[confirmed]),confirmed))
    if np.isfinite(low_confirmed[confirmed]): updates.append((max(0,confirmed-left),"support",float(low_confirmed[confirmed]),confirmed))
updates.sort(key=lambda item:(item[0],item[3],item[1])); current_resistance=current_support=np.nan; update_index=0
for i in range(n):
    while update_index<len(updates) and updates[update_index][0]==i:
        _,kind,value,_=updates[update_index]
        if kind=="resistance": current_resistance=value; resistance_change[i]=True
        else: current_support=value; support_change[i]=True
        update_index+=1
    resistance[i]=current_resistance; support[i]=current_support
resistance_plot=resistance.copy(); support_plot=support.copy(); resistance_plot[resistance_change]=np.nan; support_plot[support_change]=np.nan
layers=[]
for kind,series,color in (("Resistance",resistance,resistance_color),("Support",support,support_color)):
    changes=np.flatnonzero(np.r_[True,~np.isclose(series[1:],series[:-1],equal_nan=True)])
    for position,index in enumerate(changes):
        if not np.isfinite(series[index]): continue
        end=int(changes[position+1]-1) if position+1<len(changes) else n-1; layers.append({"type":"line","role":kind,"startIndex":int(index),"endIndex":end,"y1":float(series[index]),"y2":float(series[index]),"color":color,"lineWidth":1})
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Resistance","title":True,"data":_finite(resistance_plot),"color":resistance_color,"type":"line","lineWidth":1,"lineStyle":"step","overlay":True},{"name":"Support","title":True,"data":_finite(support_plot),"color":support_color,"type":"line","lineWidth":1,"lineStyle":"step","overlay":True}],"signals":[],"layers":layers[-200:],"tables":[],"bar_colors":[None]*n,"background_colors":[None]*n,"calculatedVars":{"pivotHigh":_finite(pivot_high),"pivotLow":_finite(pivot_low),"confirmedHigh":_finite(high_confirmed),"confirmedLow":_finite(low_confirmed),"resistance":_finite(resistance),"support":_finite(support),"resistanceChange":resistance_change.tolist(),"supportChange":support_change.tolist(),"visualOffset":-left}}
