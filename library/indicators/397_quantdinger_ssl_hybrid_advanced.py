# QuantDinger SSL Hybrid Advanced
# Independent Python implementation for the QuantDinger indicator runtime.

my_indicator_name="QuantDinger SSL Hybrid Advanced"
my_indicator_description="A composite SSL baseline with QQE, Waddah Attar, ADX, squeeze and SuperTrend filters for signal-strength scoring."

# @param signal_mode str SSL-HYBRID Signal mode values=SSL-HYBRID,SSL+QQE+WAE,SSL-HYBRID+SuperTrend
# @param trend_filter str NO-FILTER Trend filter values=NO-FILTER,SUPERTREND,HULL
# @param remove_squeeze_signals bool false Remove squeeze signals
# @param remove_low_adx_signals bool false Remove low-ADX signals
# @param show_table bool true Show strength table
# @param baseline_length int 55 Baseline length range=1:500:1
# @param continuation_length int 5 Continuation length range=1:500:1
# @param exit_length int 15 Exit length range=1:500:1
# @param atr_length int 14 ATR length range=1:500:1
# @param atr_multiplier float 1.5 ATR multiplier range=0:20:0.1
# @param adx_threshold float 20 ADX threshold range=1:100:1

signal_mode=str(params.get("signal_mode","SSL-HYBRID")); trend_filter=str(params.get("trend_filter","NO-FILTER")); remove_squeeze=bool(params.get("remove_squeeze_signals",False)); remove_low_adx=bool(params.get("remove_low_adx_signals",False)); show_table=bool(params.get("show_table",True)); base_length=max(1,min(500,int(params.get("baseline_length",55)))); cont_length=max(1,min(500,int(params.get("continuation_length",5)))); exit_length=max(1,min(500,int(params.get("exit_length",15)))); atr_length=max(1,min(500,int(params.get("atr_length",14)))); atr_mult=max(0,min(20,float(params.get("atr_multiplier",1.5)))); adx_threshold=max(1,min(100,float(params.get("adx_threshold",20))))
def _finite(v): return [float(x) if np.isfinite(x) else None for x in v]
def _ema(v,length):
    out=np.full(len(v),np.nan); state=np.nan; a=2/(length+1)
    for i,x in enumerate(v):
        if np.isfinite(x): state=float(x) if not np.isfinite(state) else state+a*(float(x)-state); out[i]=state
    return out
def _rma(v,length):
    out=np.full(len(v),np.nan); state=np.nan; seed=[]
    for i,x in enumerate(v):
        if not np.isfinite(x): continue
        if not np.isfinite(state):
            seed.append(float(x))
            if len(seed)==length: state=float(np.mean(seed)); out[i]=state
        else: state=(state*(length-1)+float(x))/length; out[i]=state
    return out
def _sma(v,length):
    out=np.full(len(v),np.nan)
    for i in range(length-1,len(v)):
        w=v[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.mean(w))
    return out
def _wma(v,length):
    out=np.full(len(v),np.nan); weights=np.arange(1,length+1,dtype=float)
    for i in range(length-1,len(v)):
        w=v[i-length+1:i+1]
        if np.all(np.isfinite(w)): out[i]=float(np.dot(w,weights)/np.sum(weights))
    return out
def _hma(v,length): return _wma(2*_wma(v,max(1,length//2))-_wma(v,length),max(1,int(np.sqrt(length))))
def _rsi(v,length):
    d=np.diff(v,prepend=np.nan); g=_rma(np.where(np.isfinite(d),np.maximum(d,0),np.nan),length); loss=_rma(np.where(np.isfinite(d),np.maximum(-d,0),np.nan),length); out=np.full(len(v),np.nan); valid=np.isfinite(g)&np.isfinite(loss); out[valid&(loss==0)]=100; out[valid&(g==0)&(loss==0)]=50; normal=valid&(loss>0); out[normal]=100-100/(1+g[normal]/loss[normal]); return out
def _state(c,hm,lm):
    out=np.ones(len(c),dtype=int)
    for i in range(len(c)): out[i]=1 if c[i]>hm[i] else -1 if c[i]<lm[i] else out[i-1] if i else 1
    return out
def _cross_over(a,b):
    out=np.zeros(len(a),bool)
    if len(a)>1: out[1:]=np.isfinite(a[1:])&np.isfinite(b[1:])&np.isfinite(a[:-1])&np.isfinite(b[:-1])&(a[1:]>b[1:])&(a[:-1]<=b[:-1])
    return out
frame=df.copy().reset_index(drop=True)
for col in ("time","open","high","low","close","volume"):
    if col not in frame.columns: frame[col]=0.0
    frame[col]=pd.to_numeric(frame[col],errors="coerce")
opened=frame["open"].to_numpy(float); high=frame["high"].to_numpy(float); low=frame["low"].to_numpy(float); close=frame["close"].to_numpy(float); n=len(frame); previous=np.r_[np.nan,close[:-1]]; tr=np.maximum(high-low,np.maximum(np.abs(high-previous),np.abs(low-previous))); tr[0]=high[0]-low[0] if n else np.nan; atr=_wma(tr,atr_length)
baseline=_hma(close,base_length); upper_channel=baseline+_ema(tr,base_length)*0.2; lower_channel=baseline-_ema(tr,base_length)*0.2; base_high=_hma(high,base_length); base_low=_hma(low,base_length); ssl1=np.where(_state(close,base_high,base_low)<0,base_high,base_low); cont_high=_ema(_ema(high,max(1,cont_length//2)),max(1,cont_length//2)); cont_low=_ema(_ema(low,max(1,cont_length//2)),max(1,cont_length//2)); ssl2=np.where(_state(close,cont_high,cont_low)<0,cont_high,cont_low); exit_high=_hma(high,exit_length); exit_low=_hma(low,exit_length); exit_line=np.where(_state(close,exit_high,exit_low)<0,exit_high,exit_low)
upper_atr=close+atr*atr_mult; lower_atr=close-atr*atr_mult; buy_cont=(close>baseline)&(close>ssl2)&(lower_atr<ssl2); sell_cont=(close<baseline)&(close<ssl2)&(upper_atr>ssl2); exit_long=_cross_over(exit_line,close); exit_short=_cross_over(close,exit_line)
rsi=_rsi(close,6); rsi_smooth=_ema(rsi,5); qqe_buy=(rsi_smooth>53)&(np.r_[False,rsi_smooth[:-1]]<=53); qqe_sell=(rsi_smooth<47)&(np.r_[False,rsi_smooth[:-1]]>=47)
fast_ema=_ema(close,20); slow_ema=_ema(close,40); macd=fast_ema-slow_ema; impulse=np.diff(macd,prepend=np.nan)*150; basis=_sma(close,20); deviation=np.full(n,np.nan)
for i in range(19,n): deviation[i]=2*np.std(close[i-19:i+1],ddof=0)
explosion=2*deviation; wae_buy=(impulse>0)&(impulse>explosion); wae_sell=(impulse<0)&(-impulse>explosion)
up=np.diff(high,prepend=np.nan); down=-np.diff(low,prepend=np.nan); plus_dm=np.where((up>down)&(up>0),up,0); minus_dm=np.where((down>up)&(down>0),down,0); atr_rma=_rma(tr,14); plus=np.divide(100*_rma(plus_dm,14),atr_rma,out=np.full(n,np.nan),where=atr_rma>0); minus=np.divide(100*_rma(minus_dm,14),atr_rma,out=np.full(n,np.nan),where=atr_rma>0); dx=np.divide(100*np.abs(plus-minus),plus+minus,out=np.full(n,np.nan),where=(plus+minus)>0); adx=_rma(dx,14)
bb_basis=_sma(close,20); bb_dev=np.full(n,np.nan)
for i in range(19,n): bb_dev[i]=2*np.std(close[i-19:i+1],ddof=0)
bb_up=bb_basis+bb_dev; bb_dn=bb_basis-bb_dev; k_up=bb_basis+2*atr_rma; k_dn=bb_basis-2*atr_rma; squeeze=(bb_up<=k_up)&(bb_dn>=k_dn); hull=_hma(close,110); hull_up=close>hull
supertrend_direction=np.ones(n,dtype=int); supertrend=np.full(n,np.nan); upper=(high+low)/2+3*atr_rma; lower=(high+low)/2-3*atr_rma
for i in range(1,n):
    if close[i]>upper[i-1]: supertrend_direction[i]=1
    elif close[i]<lower[i-1]: supertrend_direction[i]=-1
    else: supertrend_direction[i]=supertrend_direction[i-1]
    supertrend[i]=lower[i] if supertrend_direction[i]==1 else upper[i]
base_buy=buy_cont&~np.r_[False,buy_cont[:-1]]; base_sell=sell_cont&~np.r_[False,sell_cont[:-1]]
if signal_mode=="SSL+QQE+WAE": raw_buy=base_buy&qqe_buy&wae_buy; raw_sell=base_sell&qqe_sell&wae_sell
elif signal_mode=="SSL-HYBRID+SuperTrend": raw_buy=base_buy&(supertrend_direction==1); raw_sell=base_sell&(supertrend_direction==-1)
else: raw_buy=base_buy; raw_sell=base_sell
trend_buy=np.ones(n,bool) if trend_filter=="NO-FILTER" else (supertrend_direction==1 if trend_filter=="SUPERTREND" else hull_up); trend_sell=np.ones(n,bool) if trend_filter=="NO-FILTER" else (supertrend_direction==-1 if trend_filter=="SUPERTREND" else ~hull_up); buy=raw_buy&trend_buy&(~squeeze if remove_squeeze else True)&((adx>=adx_threshold) if remove_low_adx else True); sell=raw_sell&trend_sell&(~squeeze if remove_squeeze else True)&((adx>=adx_threshold) if remove_low_adx else True)
score=(np.where(close>baseline,1,-1)+np.where(rsi_smooth>50,1,-1)+np.where(plus>minus,1,-1)+np.where(wae_buy,1,np.where(wae_sell,-1,0))+supertrend_direction+np.where(hull_up,1,-1)); colors=["#00C3FF" if close[i]>upper_channel[i] else "#FF0062" if close[i]<lower_channel[i] else "#808080" for i in range(n)]
display_buy=np.zeros(n,bool); display_sell=np.zeros(n,bool); shown=0; last_index=n+100
for i in range(n-1,max(-1,n-161),-1):
    if not (buy[i] or sell[i]) or last_index-i<6: continue
    display_buy[i]=bool(buy[i]); display_sell[i]=bool(sell[i]); shown+=1; last_index=i
    if shown>=16: break
tables=[]
if n and show_table: tables=[{"title":"SSL Advanced","position":"top-right","compact":True,"rows":[[{"text":"Composite"},{"text":f"{score[-1]:+d} / 6","color":"#00C3FF" if score[-1]>0 else "#FF0062" if score[-1]<0 else "#808080"}],[{"text":"ADX"},{"text":"—" if not np.isfinite(adx[-1]) else f"{adx[-1]:.1f}"}],[{"text":"QQE"},{"text":"Bull" if rsi_smooth[-1]>53 else "Bear" if rsi_smooth[-1]<47 else "Neutral"}],[{"text":"Squeeze"},{"text":"ON" if squeeze[-1] else "Off"}]]}]
output={"schema_version":2,"name":my_indicator_name,"plots":[{"name":"Baseline","title":True,"data":_finite(baseline),"color":"#00C3FF","negativeColor":"#FF0062","type":"line","lineWidth":4,"overlay":True},{"name":"SSL1","title":True,"data":_finite(ssl1),"color":"#00C3FF","negativeColor":"#FF0062","type":"line","lineWidth":3,"overlay":True},{"name":"SSL2","title":True,"data":_finite(ssl2),"color":"#00C3FF","negativeColor":"#FF0062","type":"scatter","overlay":True},{"name":"Exit","title":True,"data":_finite(exit_line),"color":"#FFFFFF","type":"line","lineWidth":1,"overlay":True},{"name":"SuperTrend","title":True,"data":_finite(supertrend),"color":"#00C3FF","negativeColor":"#FF0062","type":"line","lineWidth":2,"overlay":True}],
"signals":[{"type":"buy","name":"Composite BUY","text":"BUY","data":[{"active":True,"price":float(low[i]),"text":"BUY","color":"#00C3FF"} if display_buy[i] else None for i in range(n)],"color":"#00C3FF","renderMode":"events","overlay":True},{"type":"sell","name":"Composite SELL","text":"SELL","data":[{"active":True,"price":float(high[i]),"text":"SELL","color":"#FF0062"} if display_sell[i] else None for i in range(n)],"color":"#FF0062","renderMode":"events","overlay":True}],"layers":[],"tables":tables,"bar_colors":colors,"background_colors":["rgba(128,0,128,0.10)" if np.isfinite(adx[i]) and adx[i]<adx_threshold else None for i in range(n)],
"calculatedVars":{"atr":_finite(atr),"baseline":_finite(baseline),"ssl1":_finite(ssl1),"ssl2":_finite(ssl2),"exitLine":_finite(exit_line),"rsi":_finite(rsi),"qqe":_finite(rsi_smooth),"waeImpulse":_finite(impulse),"waeExplosion":_finite(explosion),"plusDi":_finite(plus),"minusDi":_finite(minus),"adx":_finite(adx),"squeeze":squeeze.tolist(),"supertrend":_finite(supertrend),"supertrendDirection":supertrend_direction.tolist(),"hull":_finite(hull),"score":score.astype(int).tolist(),"buy":buy.tolist(),"sell":sell.tolist(),"exitLong":exit_long.tolist(),"exitShort":exit_short.tolist()}}
