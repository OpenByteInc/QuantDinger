# QuantDinger Trendline Breakout Navigator
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger Trendline Breakout Navigator"
my_indicator_description="Tracks long, medium and short confirmed swing trendlines, reports close breakouts and wick probes, and optionally colors price by multi-term alignment."

# @param long_length int 60 Long swing length range=2:500:1
# @param medium_length int 30 Medium swing length range=2:500:1
# @param short_length int 10 Short swing length range=2:500:1
# @param show_long bool true Show long trendline
# @param show_medium bool true Show medium trendline
# @param show_short bool true Show short trendline
# @param background_term str Long Background term values=Long,Medium,Short
# @param color_bars bool false Color bars by trendline alignment
# @param color_background bool false Shade selected trend

lengths=[max(2,min(500,int(params.get("long_length",60)))),max(2,min(500,int(params.get("medium_length",30)))),max(2,min(500,int(params.get("short_length",10))))]
enabled=[bool(params.get("show_long",True)),bool(params.get("show_medium",True)),bool(params.get("show_short",True))]
term=str(params.get("background_term","Long")); color_bars=bool(params.get("color_bars",False)); color_background=bool(params.get("color_background",False))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame)
def _navigate(left):
    right=1; pivot_high=np.zeros(n,bool); pivot_low=np.zeros(n,bool)
    for confirmed in range(left+right,n):
        p=confirmed-right; lo=p-left; hi=p+right+1
        if lo>=0 and np.all(np.isfinite(h[lo:hi])) and h[p]>=np.max(h[lo:hi]): pivot_high[p]=True
        if lo>=0 and np.all(np.isfinite(l[lo:hi])) and l[p]<=np.min(l[lo:hi]): pivot_low[p]=True
    line=np.full(n,np.nan); trend=np.zeros(n,dtype=int); breakout_up=np.zeros(n,bool); breakout_down=np.zeros(n,bool); wick_up=np.zeros(n,bool); wick_down=np.zeros(n,bool); ph=[]; pl=[]; active_kind=None; anchor=None; slope=0.0; segments=[]
    for i in range(n):
        if pivot_high[i]:
            ph.append((i,float(h[i])))
            if len(ph)>2: ph=ph[-2:]
            if len(ph)==2 and ph[-1][1]<ph[-2][1]:
                active_kind="resistance"; anchor=ph[-2]; dx=ph[-1][0]-ph[-2][0]; slope=(ph[-1][1]-ph[-2][1])/dx if dx else 0.0; segments.append({"start":ph[-2][0],"end":ph[-1][0],"y1":ph[-2][1],"y2":ph[-1][1],"kind":active_kind})
            if len(ph)==2 and ph[-1][1]>ph[-2][1] and len(pl): trend[i]=1
        if pivot_low[i]:
            pl.append((i,float(l[i])))
            if len(pl)>2: pl=pl[-2:]
            if len(pl)==2 and pl[-1][1]>pl[-2][1]:
                active_kind="support"; anchor=pl[-2]; dx=pl[-1][0]-pl[-2][0]; slope=(pl[-1][1]-pl[-2][1])/dx if dx else 0.0; segments.append({"start":pl[-2][0],"end":pl[-1][0],"y1":pl[-2][1],"y2":pl[-1][1],"kind":active_kind})
            if len(pl)==2 and pl[-1][1]<pl[-2][1] and len(ph): trend[i]=-1
        if i and trend[i]==0: trend[i]=trend[i-1]
        if anchor is not None:
            value=anchor[1]+slope*(i-anchor[0]); line[i]=value
            previous=anchor[1]+slope*(i-1-anchor[0]) if i else value
            if active_kind=="resistance":
                breakout_up[i]=i>0 and c[i]>value and c[i-1]<=previous
                wick_up[i]=h[i]>value and c[i]<=value
                if breakout_up[i]: trend[i]=1; active_kind=None
            elif active_kind=="support":
                breakout_down[i]=i>0 and c[i]<value and c[i-1]>=previous
                wick_down[i]=l[i]<value and c[i]>=value
                if breakout_down[i]: trend[i]=-1; active_kind=None
        if trend[i]==0 and anchor is not None and np.isfinite(line[i]): trend[i]=1 if c[i]>=line[i] else -1
    return line,trend,pivot_high,pivot_low,breakout_up,breakout_down,wick_up,wick_down,segments
results=[_navigate(x) for x in lengths]
names=["Long","Medium","Short"]; widths=[2,1,1]; plots=[]; signals=[]; layers=[]
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
for index,result in enumerate(results):
    line,trend,pivot_high,pivot_low,up,down,wick_up,wick_down,segments=result
    if enabled[index]: plots.append({"name":names[index]+" Trendline","title":True,"data":_finite(line),"color":"#089981","negativeColor":"#F23645","type":"line","lineWidth":widths[index],"lineStyle":"solid" if index==0 else "dashed" if index==1 else "dotted","overlay":True})
    signals.extend([_events(up,line,names[index]+" BREAK UP","#089981","buy"),_events(down,line,names[index]+" BREAK DOWN","#F23645","sell"),_events(wick_up,line,"WICK","#085DEF","event"),_events(wick_down,line,"WICK","#FF5D00","event")])
    for segment in segments[-12:]: layers.append({"type":"line","role":names[index]+" "+segment["kind"],"startIndex":segment["start"],"endIndex":segment["end"],"y1":segment["y1"],"y2":segment["y2"],"color":"#089981" if segment["kind"]=="support" else "#F23645","lineWidth":widths[index]})
selected={"Long":0,"Medium":1,"Short":2}.get(term,0); selected_line=results[selected][0]
bar_colors=[]; background=[]
for i in range(n):
    comparisons=[c[i]>result[0][i] if np.isfinite(result[0][i]) else None for result in results]
    score=sum(1 if x is True else -1 if x is False else 0 for x in comparisons)
    bar_colors.append("rgba(8,153,129,0.80)" if color_bars and score>=2 else "rgba(242,54,69,0.80)" if color_bars and score<=-2 else None)
    background.append("rgba(8,153,129,0.03)" if color_background and np.isfinite(selected_line[i]) and c[i]>selected_line[i] else "rgba(242,54,69,0.03)" if color_background and np.isfinite(selected_line[i]) else None)
tables=[]
if n: tables=[{"title":"Trendline Breakout Navigator","position":"top-right","compact":True,"rows":[[{"text":names[i]},{"text":"Bullish" if results[i][1][-1]>0 else "Bearish" if results[i][1][-1]<0 else "Waiting"}] for i in range(3)]}]
output={"schema_version":2,"name":my_indicator_name,"plots":plots,"signals":signals,"layers":layers,"tables":tables,"bar_colors":bar_colors,"background_colors":background,"calculatedVars":{"lengths":lengths,"lines":[_finite(x[0]) for x in results],"trends":[x[1].tolist() for x in results],"pivotHigh":[x[2].tolist() for x in results],"pivotLow":[x[3].tolist() for x in results],"breakoutUp":[x[4].tolist() for x in results],"breakoutDown":[x[5].tolist() for x in results],"wickUp":[x[6].tolist() for x in results],"wickDown":[x[7].tolist() for x in results]}}
