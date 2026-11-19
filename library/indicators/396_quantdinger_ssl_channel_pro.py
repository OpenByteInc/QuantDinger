# QuantDinger SSL Channel Pro
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name = "QuantDinger SSL Channel Pro"
my_indicator_description = "An SSL high/low channel with RSI, DMI, volatility, participation and optional higher-timeframe confirmation scores."
indicator_timeframe_param = "higher_timeframe"
indicator_timeframes = ["5m", "15m", "30m", "1H", "2H", "4H", "1D"]

# @param ma_type str EMA Moving-average type values=SMA,EMA,WMA,HMA,RMA,VWMA
# @param ssl_length int 10 SSL length range=1:500:1
# @param signal_profile str Balanced Signal profile values=Responsive,Balanced,Selective
# @param use_higher_timeframe bool false Enable higher-timeframe confirmation
# @param higher_timeframe str 1H Higher timeframe values=5m,15m,30m,1H,2H,4H,1D
# @param show_signals bool true Show confirmed signals

ma_type=str(params.get("ma_type","EMA")); ma_type=ma_type if ma_type in ("SMA","EMA","WMA","HMA","RMA","VWMA") else "EMA"
ssl_length=max(1,min(500,int(params.get("ssl_length",10)))); profile=str(params.get("signal_profile","Balanced")); profile=profile if profile in ("Responsive","Balanced","Selective") else "Balanced"
use_htf=bool(params.get("use_higher_timeframe",False)); higher_timeframe=str(params.get("higher_timeframe","1H")); show_signals=bool(params.get("show_signals",True))

def _finite(values): return [float(v) if np.isfinite(v) else None for v in values]
def _ema(values,length):
    out=np.full(len(values),np.nan); state=np.nan; a=2.0/(length+1.0)
    for i,v in enumerate(values):
        if np.isfinite(v): state=float(v) if not np.isfinite(state) else state+a*(float(v)-state); out[i]=state
    return out
def _rma(values,length):
    out=np.full(len(values),np.nan); state=np.nan; seed=[]
    for i,v in enumerate(values):
        if not np.isfinite(v): continue
        if not np.isfinite(state):
            seed.append(float(v))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1.0)+float(v))/length; out[i]=state
    return out
def _sma(values,length):
    out=np.full(len(values),np.nan)
    for i in range(length-1,len(values)):
        w=values[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.mean(w))
    return out
def _wma(values,length):
    out=np.full(len(values),np.nan); weights=np.arange(1,length+1,dtype=float); divisor=float(np.sum(weights))
    for i in range(length-1,len(values)):
        w=values[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.dot(w,weights)/divisor)
    return out
def _ma(values,volume,length,kind):
    if kind=="SMA": return _sma(values,length)
    if kind=="EMA": return _ema(values,length)
    if kind=="WMA": return _wma(values,length)
    if kind=="RMA": return _rma(values,length)
    if kind=="HMA": return _wma(2.0*_wma(values,max(1,length//2))-_wma(values,length),max(1,int(np.sqrt(length))))
    numerator=_sma(values*volume,length); denominator=_sma(volume,length)
    return np.divide(numerator,denominator,out=np.full(len(values),np.nan),where=np.isfinite(denominator)&(denominator!=0))
def _rsi(values,length):
    delta=np.diff(values,prepend=np.nan); gain=_rma(np.where(np.isfinite(delta),np.maximum(delta,0),np.nan),length); loss=_rma(np.where(np.isfinite(delta),np.maximum(-delta,0),np.nan),length); out=np.full(len(values),np.nan); valid=np.isfinite(gain)&np.isfinite(loss); out[valid&(loss==0)]=100; out[valid&(gain==0)&(loss==0)]=50; normal=valid&(loss>0); out[normal]=100-100/(1+gain[normal]/loss[normal]); return out
def _dmi(high,low,close,length):
    n=len(close); previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan
    up=np.diff(high,prepend=np.nan); down=-np.diff(low,prepend=np.nan); plus=np.where((up>down)&(up>0),up,0.0); minus=np.where((down>up)&(down>0),down,0.0)
    atr=_rma(tr,length); plus_di=np.divide(100*_rma(plus,length),atr,out=np.full(n,np.nan),where=atr>0); minus_di=np.divide(100*_rma(minus,length),atr,out=np.full(n,np.nan),where=atr>0); dx=np.divide(100*np.abs(plus_di-minus_di),plus_di+minus_di,out=np.full(n,np.nan),where=(plus_di+minus_di)>0); return plus_di,minus_di,_rma(dx,length),atr
def _scale(values,minimum,maximum): return np.clip((values-minimum)/(maximum-minimum),0.0,1.0) if maximum>minimum else np.zeros(len(values))
def _normalize(frame):
    result=frame.copy().reset_index(drop=True)
    for c in ("time","open","high","low","close","volume"):
        if c not in result.columns: result[c]=0.0
        result[c]=pd.to_numeric(result[c],errors="coerce")
    times=result["time"].to_numpy(float)
    if len(times) and np.nanmax(np.abs(times))>1e10: result["time"]=times/1000.0
    return result
def _state(high_ma,low_ma,close):
    out=np.ones(len(close),dtype=int)
    for i in range(len(close)):
        out[i]=1 if close[i]>high_ma[i] else -1 if close[i]<low_ma[i] else out[i-1] if i else 1
    return out

frame=_normalize(df); opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); volume=frame["volume"].to_numpy(float); time=frame["time"].to_numpy(float); n=len(frame)
high_ma=_ma(high,volume,ssl_length,ma_type); low_ma=_ma(low,volume,ssl_length,ma_type); state=_state(high_ma,low_ma,close); ssl_line=np.where(state==1,low_ma,high_ma)
flip_up=np.zeros(n,bool); flip_down=np.zeros(n,bool)
if n>1: flip_up[1:]=(state[1:]==1)&(state[:-1]==-1); flip_down[1:]=(state[1:]==-1)&(state[:-1]==1)
rsi=_rsi(close,14); plus_di,minus_di,adx,atr=_dmi(high,low,close,14); volume_ma=_sma(volume,20); width=np.abs(high_ma-low_ma); normalized=np.divide(width,atr,out=np.zeros(n),where=np.isfinite(atr)&(atr>0))
adx_threshold={"Responsive":18.0,"Balanced":20.0,"Selective":25.0}[profile]; channel_threshold={"Responsive":0.14,"Balanced":0.20,"Selective":0.28}[profile]; signal_threshold={"Responsive":65,"Balanced":70,"Selective":80}[profile]
buy_confirm=(rsi>50)&(adx>adx_threshold)&(plus_di>minus_di)&(normalized>=channel_threshold); sell_confirm=(rsi<50)&(adx>adx_threshold)&(minus_di>plus_di)&(normalized>=channel_threshold)

htf_state=np.zeros(n,dtype=int); htf_available=not use_htf; source=None
if use_htf and hasattr(ctx,"raw_series"):
    try: source=ctx.raw_series(higher_timeframe)
    except Exception: source=None
if use_htf and source is not None and len(source):
    remote=_normalize(source); rv=remote["volume"].to_numpy(float); rh=_ma(remote["high"].to_numpy(float),rv,ssl_length,ma_type); rl=_ma(remote["low"].to_numpy(float),rv,ssl_length,ma_type); rs=_state(rh,rl,remote["close"].to_numpy(float)); rt=remote["time"].to_numpy(float); htf_available=True
    for i,t in enumerate(time):
        j=int(np.searchsorted(rt,t,side="right")-2)
        if j>=0: htf_state[i]=rs[j]
buy_htf=(not use_htf) | (htf_available & (htf_state==1)); sell_htf=(not use_htf) | (htf_available & (htf_state==-1))
candle_range=np.maximum(high-low,1e-12); body=np.abs(close-opened)/candle_range; buy_location=np.clip((close-low)/candle_range,0,1); sell_location=np.clip((high-close)/candle_range,0,1); buy_break=np.divide(np.maximum(close-high_ma,0),atr,out=np.zeros(n),where=atr>0); sell_break=np.divide(np.maximum(low_ma-close,0),atr,out=np.zeros(n),where=atr>0)
buy_transition=np.where(flip_up,12+_scale(buy_break,0,0.4)*6+body*4+buy_location*3,0); sell_transition=np.where(flip_down,12+_scale(sell_break,0,0.4)*6+body*4+sell_location*3,0)
previous_rsi=np.r_[np.nan,rsi[:-1]]; buy_momentum=_scale(rsi-50,-10,15)*16+np.where(rsi>previous_rsi,4,0); sell_momentum=_scale(50-rsi,-10,15)*16+np.where(rsi<previous_rsi,4,0)
di_total=np.maximum(plus_di+minus_di,0.0001); buy_dom=np.clip((plus_di-minus_di)/di_total,0,1); sell_dom=np.clip((minus_di-plus_di)/di_total,0,1); previous_adx=np.r_[np.nan,adx[:-1]]
buy_trend=np.where(plus_di>minus_di,6+_scale(adx,adx_threshold,adx_threshold+25)*8+buy_dom*4+np.where(adx>previous_adx,2,0),0); sell_trend=np.where(minus_di>plus_di,6+_scale(adx,adx_threshold,adx_threshold+25)*8+sell_dom*4+np.where(adx>previous_adx,2,0),0)
previous_width=np.r_[np.nan,normalized[:-1]]; regime=np.where(normalized>=channel_threshold,8+_scale(normalized,channel_threshold,channel_threshold+max(channel_threshold*1.5,0.25))*4+np.where(normalized>previous_width,3,0),_scale(normalized,0,channel_threshold)*8)
relative=np.divide(volume,volume_ma,out=np.full(n,np.nan),where=volume_ma>0); participation=np.where(np.isfinite(relative),_scale(relative,0.7,1.7)*10,5); buy_wick=(high-np.maximum(opened,close))/candle_range; sell_wick=(np.minimum(opened,close)-low)/candle_range; extension=np.divide(np.abs(close-ssl_line),atr,out=np.zeros(n),where=atr>0); buy_penalty=buy_wick*4+_scale(extension,1.75,3.5)*6; sell_penalty=sell_wick*4+_scale(extension,1.75,3.5)*6
previous_ssl=np.r_[np.nan,ssl_line[:-1]]; buy_local=np.where(ssl_line>previous_ssl,5,0)+_scale(np.divide(np.maximum(close-ssl_line,0),atr,out=np.zeros(n),where=atr>0),0,1)*5; sell_local=np.where(ssl_line<previous_ssl,5,0)+_scale(np.divide(np.maximum(ssl_line-close,0),atr,out=np.zeros(n),where=atr>0),0,1)*5
buy_context=np.where(use_htf,np.where(htf_state==1,10,0),buy_local); sell_context=np.where(use_htf,np.where(htf_state==-1,10,0),sell_local)
buy_raw=np.nan_to_num(buy_transition+buy_momentum+buy_trend+regime+participation+buy_context-buy_penalty,nan=0.0); sell_raw=np.nan_to_num(sell_transition+sell_momentum+sell_trend+regime+participation+sell_context-sell_penalty,nan=0.0); buy_score=np.rint(np.clip(buy_raw,0,100)).astype(int); sell_score=np.rint(np.clip(sell_raw,0,100)).astype(int)
buy_signal=flip_up&buy_confirm&buy_htf&(buy_score>=signal_threshold); sell_signal=flip_down&sell_confirm&sell_htf&(sell_score>=signal_threshold); visual=np.ones(n,bool); visual[1:]=state[1:]==state[:-1]
quality_raw=np.nan_to_num(np.where(state==1,buy_momentum+buy_trend+regime+participation+buy_context-buy_penalty,sell_momentum+sell_trend+regime+participation+sell_context-sell_penalty),nan=0.0); quality=np.rint(np.clip(quality_raw/75*100,0,100)).astype(int)
output={"schema_version":2,"name":my_indicator_name,"plots":[
    {"name":"SSL Line","title":True,"data":_finite(np.where(visual,ssl_line,np.nan)),"color":"#00D5FF","negativeColor":"#FF477E","type":"line","lineWidth":2,"overlay":True},
    {"name":"High Boundary","title":True,"data":_finite(np.where(visual,high_ma,np.nan)),"color":"#765CF6","type":"line","lineWidth":1,"overlay":True},
    {"name":"Low Boundary","title":True,"data":_finite(np.where(visual,low_ma,np.nan)),"color":"#765CF6","type":"line","lineWidth":1,"overlay":True}],
    "signals":[
        {"type":"buy","name":"Confirmed BUY","text":"BUY","data":[{"active":True,"price":float(low[i]-atr[i]*1.05),"text":f"BUY {buy_score[i]}%","color":"#00D5FF"} if show_signals and buy_signal[i] else None for i in range(n)],"color":"#00D5FF","renderMode":"events","overlay":True},
        {"type":"sell","name":"Confirmed SELL","text":"SELL","data":[{"active":True,"price":float(high[i]+atr[i]*1.05),"text":f"SELL {sell_score[i]}%","color":"#FF477E"} if show_signals and sell_signal[i] else None for i in range(n)],"color":"#FF477E","renderMode":"events","overlay":True}],"layers":[],
    "tables":([{"title":"SSL Pro","position":"top-right","compact":True,"rows":[[{"text":"State"},{"text":"BULLISH" if state[-1]==1 else "BEARISH","color":"#00D5FF" if state[-1]==1 else "#FF477E"}],[{"text":"Quality"},{"text":f"{quality[-1]}%"}],[{"text":"HTF"},{"text":("Off" if not use_htf else "Aligned" if htf_available and htf_state[-1]==state[-1] else "Unavailable" if not htf_available else "Opposed")}]]}] if n else []),
    "bar_colors":[None]*n,"background_colors":[None]*n,
    "calculatedVars":{"highMa":_finite(high_ma),"lowMa":_finite(low_ma),"sslState":state.tolist(),"sslLine":_finite(ssl_line),"flipUp":flip_up.tolist(),"flipDown":flip_down.tolist(),"rsi":_finite(rsi),"plusDi":_finite(plus_di),"minusDi":_finite(minus_di),"adx":_finite(adx),"atr":_finite(atr),"normalizedWidth":_finite(normalized),"htfState":htf_state.tolist(),"htfAvailable":bool(htf_available),"buyScore":buy_score.tolist(),"sellScore":sell_score.tolist(),"quality":quality.tolist(),"buySignal":buy_signal.tolist(),"sellSignal":sell_signal.tolist()}}
