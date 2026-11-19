# QuantDinger TMA Overlay
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger TMA Overlay"
my_indicator_description="Overlays four smoothed moving averages, trend state, three-line-strike and engulfing patterns, plus an optional weekday trading-session background."

# @param show_100_line bool true Show 100-period line
# @param show_trend_fill bool true Show long-term trend background
# @param show_bullish_strike bool true Show bullish three-line strike
# @param show_bearish_strike bool true Show bearish three-line strike
# @param show_bullish_engulfing bool true Show bullish engulfing candle
# @param show_bearish_engulfing bool true Show bearish engulfing candle
# @param show_trade_session bool true Show trade session
# @param timezone str America/Chicago Session timezone values=America/Chicago,America/New_York,UTC,Europe/London,Europe/Frankfurt,Asia/Tokyo,Asia/Sydney
# @param analysis_start_hour int 7 Analysis session start hour range=0:23:1
# @param session_start_hour int 8 Main session start hour range=0:23:1
# @param session_start_minute int 30 Main session start minute range=0:59:1
# @param session_end_hour int 12 Session end hour range=0:23:1
# @param session_end_minute int 0 Session end minute range=0:59:1

show_100=bool(params.get("show_100_line",True)); trend_fill=bool(params.get("show_trend_fill",True)); show_bull_strike=bool(params.get("show_bullish_strike",True)); show_bear_strike=bool(params.get("show_bearish_strike",True)); show_bull_engulf=bool(params.get("show_bullish_engulfing",True)); show_bear_engulf=bool(params.get("show_bearish_engulfing",True)); show_session=bool(params.get("show_trade_session",True)); timezone=str(params.get("timezone","America/Chicago")); analysis_hour=max(0,min(23,int(params.get("analysis_start_hour",7)))); session_hour=max(0,min(23,int(params.get("session_start_hour",8)))); session_minute=max(0,min(59,int(params.get("session_start_minute",30)))); end_hour=max(0,min(23,int(params.get("session_end_hour",12)))); end_minute=max(0,min(59,int(params.get("session_end_minute",0))))
def _finite(values): return [float(x) if np.isfinite(x) else None for x in values]
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        sample=values[i-length+1:i+1]
        if np.all(np.isfinite(sample)): out[i]=float(np.mean(sample))
    return out
def _smma(values,length):
    seed=_sma(values,length); out=np.full(len(values),np.nan); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(state):
            if np.isfinite(seed[i]): state=float(seed[i]); out[i]=state
        elif np.isfinite(value): state=(state*(length-1)+float(value))/length; out[i]=state
    return out
def _ema(values,length):
    out=np.full(len(values),np.nan); alpha=2/(length+1); state=np.nan
    for i,value in enumerate(values):
        if not np.isfinite(value): continue
        state=float(value) if not np.isfinite(state) else alpha*float(value)+(1-alpha)*state; out[i]=state
    return out
frame=df.copy().reset_index(drop=True)
for column in ("time","open","high","low","close","volume"):
    if column not in frame.columns: frame[column]=0.0
    frame[column]=pd.to_numeric(frame[column],errors="coerce")
time=frame["time"].to_numpy(float); o=frame["open"].to_numpy(float); h=frame["high"].to_numpy(float); l=frame["low"].to_numpy(float); c=frame["close"].to_numpy(float); n=len(frame); smma21=_smma(c,21); smma50=_smma(c,50); smma100=_smma(c,100); smma200=_smma(c,200); ema2=_ema(c,2); bullish_strike=np.zeros(n,bool); bearish_strike=np.zeros(n,bool); bullish_engulf=np.zeros(n,bool); bearish_engulf=np.zeros(n,bool)
for i in range(n):
    if i>=3: bearish_strike[i]=c[i-3]>o[i-3] and c[i-2]>o[i-2] and c[i-1]>o[i-1] and c[i]<o[i-1]; bullish_strike[i]=c[i-3]<o[i-3] and c[i-2]<o[i-2] and c[i-1]<o[i-1] and c[i]>o[i-1]
    if i>=1: bullish_engulf[i]=o[i]<=c[i-1] and o[i]<o[i-1] and c[i]>o[i-1]; bearish_engulf[i]=o[i]>=c[i-1] and o[i]>o[i-1] and c[i]<o[i-1]
session_active=np.zeros(n,bool)
if show_session and n:
    seconds=time.copy(); finite=seconds[np.isfinite(seconds)]
    if len(finite) and float(np.nanmedian(np.abs(finite)))>1e11: seconds/=1000.0
    try: local=pd.to_datetime(seconds,unit="s",utc=True).tz_convert(timezone)
    except Exception: local=pd.to_datetime(seconds,unit="s",utc=True)
    weekday=np.asarray(local.weekday); minutes=np.asarray(local.hour)*60+np.asarray(local.minute); enabled=weekday<5; analysis_start=analysis_hour*60; main_start=session_hour*60+session_minute; end=end_hour*60+end_minute; session_active=enabled&(((minutes>=analysis_start)&(minutes<=end))|((minutes>=main_start)&(minutes<=end)))
trend_up=ema2>smma200; trend_down=ema2<smma200; background=["rgba(25,118,210,0.12)" if session_active[i] else "rgba(0,128,0,0.08)" if trend_fill and trend_up[i] else "rgba(255,0,0,0.08)" if trend_fill and trend_down[i] else None for i in range(n)]
def _thin(mask,limit=3,min_gap=10):
    selected=[]
    for value in np.flatnonzero(mask)[::-1]:
        index=int(value)
        if all(abs(index-other)>=min_gap for other in selected): selected.append(index)
        if len(selected)>=limit: break
    result=np.zeros(n,bool); result[selected]=True
    return result
def _events(mask,price,text,color,kind): return {"type":kind,"name":text,"text":text,"data":[{"active":True,"price":float(price[i]),"text":text,"color":color} if mask[i] and np.isfinite(price[i]) else None for i in range(n)],"color":color,"renderMode":"events","overlay":True}
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"21 SMMA","title":True,"data":_finite(smma21),"color":"#FFFFFF","type":"line","lineWidth":2,"overlay":True},{"name":"50 SMMA","title":True,"data":_finite(smma50),"color":"#6AFF00","type":"line","lineWidth":2,"overlay":True},{"name":"100 SMMA","title":show_100,"data":_finite(smma100) if show_100 else [None]*n,"color":"#FFFF00","type":"line","lineWidth":2,"overlay":True},{"name":"200 SMMA","title":True,"data":_finite(smma200),"color":"#FF0500","type":"line","lineWidth":2,"overlay":True}],"signals":[_events(_thin(bullish_strike) if show_bull_strike else np.zeros(n,bool),l,"3S-BULL","#00A000","buy"),_events(_thin(bearish_strike) if show_bear_strike else np.zeros(n,bool),h,"3S-BEAR","#FF0000","sell"),_events(_thin(bullish_engulf) if show_bull_engulf else np.zeros(n,bool),l,"ENGULF","#00A000","buy"),_events(_thin(bearish_engulf) if show_bear_engulf else np.zeros(n,bool),h,"ENGULF","#FF0000","sell")],"layers":[],"tables":[],"bar_colors":[None]*n,"background_colors":background,"calculatedVars":{"smma21":_finite(smma21),"smma50":_finite(smma50),"smma100":_finite(smma100),"smma200":_finite(smma200),"ema2":_finite(ema2),"trendUp":trend_up.tolist(),"trendDown":trend_down.tolist(),"bullishStrike":bullish_strike.tolist(),"bearishStrike":bearish_strike.tolist(),"bullishEngulfing":bullish_engulf.tolist(),"bearishEngulfing":bearish_engulf.tolist(),"sessionActive":session_active.tolist(),"timezone":timezone}}
