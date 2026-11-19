# QuantDinger VWAP Supply Demand Zones
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger VWAP Supply Demand Zones"
my_indicator_description="Creates volume- and momentum-confirmed supply or demand zones when price crosses a rolling VWAP, then tracks tests, breaks and active-zone bias."

# @param vwap_length int 20 Rolling VWAP length range=1:100:1
# @param zone_width_percent float 0.5 Zone width percent range=0.1:3:0.1
# @param maximum_zones int 5 Maximum zones per side range=1:10:1
# @param minimum_touches int 2 Minimum touches range=1:5:1
# @param zone_extension int 30 Zone extension range=10:100:1
# @param show_zones bool true Show zones
# @param show_dashboard bool true Show dashboard

length=max(1,min(100,int(params.get("vwap_length",20)))); width=max(0.1,min(3,float(params.get("zone_width_percent",0.5)))); max_zones=max(1,min(10,int(params.get("maximum_zones",5)))); min_touches=max(1,min(5,int(params.get("minimum_touches",2)))); extension=max(10,min(100,int(params.get("zone_extension",30)))); show_zones=bool(params.get("show_zones",True)); show_dashboard=bool(params.get("show_dashboard",True))
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
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); v=frame["volume"].to_numpy(float); n=len(frame); typical=(h+l+c)/3; numerator=pd.Series(typical*v).rolling(length,min_periods=length).mean().to_numpy(float); denominator=pd.Series(v).rolling(length,min_periods=length).mean().to_numpy(float); vwap=np.divide(numerator,denominator,out=np.full(n,np.nan),where=denominator!=0); volume_ma=pd.Series(v).rolling(20,min_periods=20).mean().to_numpy(float); previous=np.r_[np.nan,c[:-1]]; tr=np.maximum(h-l,np.maximum(np.abs(h-previous),np.abs(l-previous)))
if n: tr[0]=h[0]-l[0]
atr=_rma(tr,14); cross_up=np.zeros(n,bool); cross_down=np.zeros(n,bool)
if n>1:
    valid=np.isfinite(vwap[1:])&np.isfinite(vwap[:-1]); cross_up[1:]=valid&(c[1:]>vwap[1:])&(c[:-1]<=vwap[:-1]); cross_down[1:]=valid&(c[1:]<vwap[1:])&(c[:-1]>=vwap[:-1])
volume_confirm=v>volume_ma*1.2; momentum=np.abs(c-o)>atr*0.5; create_supply=(np.arange(n)>length*2)&cross_down&volume_confirm&momentum; create_demand=(np.arange(n)>length*2)&cross_up&volume_confirm&momentum; supplies=[]; demands=[]; supply_test=np.zeros(n,bool); demand_test=np.zeros(n,bool); supply_break=np.zeros(n,bool); demand_break=np.zeros(n,bool); in_supply=np.zeros(n,bool); in_demand=np.zeros(n,bool)
for i in range(n):
    if create_supply[i]: supplies.append({"startIndex":int(i),"endIndex":int(i+extension),"top":float(h[i]*(1+width/100)),"bottom":float(vwap[i]),"touches":0,"broken":False}); supplies=supplies[-max_zones:]
    if create_demand[i]: demands.append({"startIndex":int(i),"endIndex":int(i+extension),"top":float(vwap[i]),"bottom":float(l[i]*(1-width/100)),"touches":0,"broken":False}); demands=demands[-max_zones:]
    for zone in supplies:
        if zone["broken"]: continue
        zone["endIndex"]=int(i+extension)
        if h[i]>=zone["bottom"] and h[i]<=zone["top"]: zone["touches"]+=1; supply_test[i]|=zone["touches"]==min_touches
        if c[i]>zone["top"]: zone["broken"]=True; supply_break[i]=True
        elif h[i]>=zone["bottom"] and l[i]<=zone["top"]: in_supply[i]=True
    for zone in demands:
        if zone["broken"]: continue
        zone["endIndex"]=int(i+extension)
        if l[i]<=zone["top"] and l[i]>=zone["bottom"]: zone["touches"]+=1; demand_test[i]|=zone["touches"]==min_touches
        if c[i]<zone["bottom"]: zone["broken"]=True; demand_break[i]=True
        elif h[i]>=zone["bottom"] and l[i]<=zone["top"]: in_demand[i]=True
layers=[]
if show_zones:
    for kind,zones,color in (("Supply",supplies,"rgba(244,67,54,0.12)"),("Demand",demands,"rgba(76,175,80,0.12)")):
        for zone in zones: layers.append({"type":"zone","role":kind,"startIndex":zone["startIndex"],"endIndex":zone["endIndex"],"top":zone["top"],"bottom":zone["bottom"],"fillColor":"rgba(128,128,128,0.08)" if zone["broken"] else color,"borderColor":"#808080" if zone["broken"] else "#F44336" if kind=="Supply" else "#4CAF50","text":f'{kind} {zone["touches"]} touches'})
active_supply=sum(not x["broken"] for x in supplies); active_demand=sum(not x["broken"] for x in demands); tables=[]
if n and show_dashboard: tables=[{"title":"VWAP Supply & Demand","position":"bottom-right","compact":True,"rows":[[{"text":"Supply"},{"text":active_supply}],[{"text":"Demand"},{"text":active_demand}],[{"text":"VWAP"},{"text":None if not np.isfinite(vwap[-1]) else round(float(vwap[-1]),6)}],[{"text":"Bias"},{"text":"BEAR" if active_supply>active_demand else "BULL" if active_demand>active_supply else "NEUTRAL"}]]}]
def _thin(mask,limit=2,min_gap=24):
    kept=np.zeros(len(mask),bool); last=None; count=0
    for index in np.flatnonzero(mask)[::-1]:
        if last is None or last-index>=min_gap:
            kept[index]=True; last=int(index); count+=1
            if count>=limit: break
    return kept
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
background=["rgba(244,67,54,0.05)" if in_supply[i] else "rgba(76,175,80,0.05)" if in_demand[i] else None for i in range(n)]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Rolling VWAP","title":True,"data":_finite(vwap),"color":"#2196F3","type":"line","lineWidth":2,"overlay":True},{"name":"Upper VWAP Band","data":_finite(vwap*(1+width/1000)),"color":"rgba(33,150,243,0.35)","type":"line","lineWidth":1,"overlay":True},{"name":"Lower VWAP Band","data":_finite(vwap*(1-width/1000)),"color":"rgba(33,150,243,0.35)","type":"line","lineWidth":1,"overlay":True}],"signals":[_events(_thin(create_supply),h,"NEW SUPPLY","#F44336","sell"),_events(_thin(create_demand),l,"NEW DEMAND","#4CAF50","buy"),_events(_thin(supply_test),c,"SUPPLY TEST","#F44336","event"),_events(_thin(demand_test),c,"DEMAND TEST","#4CAF50","event"),_events(_thin(supply_break),c,"SUPPLY BREAK","#2196F3","buy"),_events(_thin(demand_break),c,"DEMAND BREAK","#FF9800","sell")],"layers":layers,"tables":tables,"bar_colors":["rgba(76,175,80,0.30)" if np.isfinite(vwap[i]) and c[i]>vwap[i] else "rgba(244,67,54,0.30)" if np.isfinite(vwap[i]) else None for i in range(n)],"background_colors":background,"calculatedVars":{"vwap":_finite(vwap),"atr":_finite(atr),"crossUp":cross_up.tolist(),"crossDown":cross_down.tolist(),"createSupply":create_supply.tolist(),"createDemand":create_demand.tolist(),"supplyTest":supply_test.tolist(),"demandTest":demand_test.tolist(),"supplyBreak":supply_break.tolist(),"demandBreak":demand_break.tolist(),"inSupply":in_supply.tolist(),"inDemand":in_demand.tolist(),"supplyZones":supplies,"demandZones":demands}}
