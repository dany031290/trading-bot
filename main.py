import os, time, requests, pandas as pd, threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string
import yfinance as yf
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ─────────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────────
ALPACA_KEY    = os.environ.get("API_KEY", "")
ALPACA_SECRET = os.environ.get("API_SECRET", "")
BASE_URL      = "https://paper-api.alpaca.markets"

CAPITAL_POR_OPERACION = 55
STOP_LOSS    = 0.015
TAKE_PROFIT  = 0.030
EMA_FAST     = 9
EMA_SLOW     = 21
RSI_PERIOD   = 14
RSI_MAX      = 70
INTERVALO    = 60

ACTIVOS = {
    "BTC-USD": "BTC/USD",
    "GLD":  "GLD",
    "SLV":  "SLV",
    "GDX":  "GDX",
    "NEM":  "NEM",
    "LMT":  "LMT",
    "RTX":  "RTX",
    "NOC":  "NOC",
    "GD":   "GD",
    "XOM":  "XOM",
    "CVX":  "CVX",
    "USO":  "USO",
    "XLE":  "XLE",
    "JNJ":  "JNJ",
    "PFE":  "PFE",
    "SPY":  "SPY",
    "IEF":  "IEF",
    "TLT":  "TLT",
}

ACTIVOS_INFO = {
    "BTC-USD": ("🪙", "Bitcoin"),
    "GLD":     ("🥇", "Oro (ETF)"),
    "SLV":     ("🥇", "Plata (ETF)"),
    "GDX":     ("🥇", "Mineras de Oro"),
    "NEM":     ("🥇", "Newmont Corp"),
    "LMT":     ("🛡️", "Lockheed Martin"),
    "RTX":     ("🛡️", "Raytheon Tech"),
    "NOC":     ("🛡️", "Northrop Grumman"),
    "GD":      ("🛡️", "General Dynamics"),
    "XOM":     ("⚡", "Exxon Mobil"),
    "CVX":     ("⚡", "Chevron Corp"),
    "USO":     ("⚡", "ETF Petroleo"),
    "XLE":     ("⚡", "ETF Energia"),
    "JNJ":     ("💊", "Johnson & Johnson"),
    "PFE":     ("💊", "Pfizer Inc"),
    "SPY":     ("🏦", "S&P 500 ETF"),
    "IEF":     ("🏦", "Bonos Tesoro"),
    "TLT":     ("🏦", "Bonos Largo Plazo"),
}

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET
}

estado = {
    "activos": {}, "operaciones": [], "señales": [],
    "capital": 100000.0, "cash": 100000.0, "pnl": 0.0,
    "trades": 0, "log": [], "ultimo_update": "—",
    "mercado_abierto": False
}

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(tipo, msg):
    iconos = {"buy":"🟢","sell":"🔴","wait":"⏳","info":"📊","error":"❌","warn":"⚠️"}
    print(f"{ts()}  {iconos.get(tipo,'•')}  {msg}", flush=True)
    estado["log"].insert(0, {"time": ts(), "tipo": tipo, "msg": msg})
    if len(estado["log"]) > 100:
        estado["log"].pop()

def alpaca_get(path):
    r = requests.get(f"{BASE_URL}/v2/{path}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def alpaca_post(path, data):
    r = requests.post(f"{BASE_URL}/v2/{path}", headers=HEADERS, json=data, timeout=10)
    return r.json()

def get_bars(ticker, limit=60):
    for intento in range(3):
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            })
            ticker_obj = yf.Ticker(ticker, session=session)
            df = ticker_obj.history(period="2d", interval="1m")
            if df is None or len(df) < 22:
                return None
            df = df.tail(limit).copy()
            df["c"] = df["Close"].astype(float)
            return df.reset_index()
        except Exception as e:
            if intento < 2:
                time.sleep(3)
                continue
            log("error", f"{ticker}: {str(e)[:60]}")
            return None

def calc_ema(series, period):
    k = 2 / (period + 1)
    result = [series.iloc[0]]
    for v in series.iloc[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def calc_rsi(series, period=14):
    deltas = series.diff().dropna()
    ag = deltas.clip(lower=0).rolling(period).mean().iloc[-1]
    al = (-deltas).clip(lower=0).rolling(period).mean().iloc[-1]
    return round(100 if al == 0 else 100 - (100 / (1 + ag / al)), 1)

def get_position(symbol):
    try:
        return alpaca_get(f"positions/{symbol}")
    except:
        return None

def analizar(ticker):
    df = get_bars(ticker)
    if df is None:
        return None
    close     = df["c"]
    e9        = calc_ema(close, EMA_FAST)
    e21       = calc_ema(close, EMA_SLOW)
    rsi       = calc_rsi(close, RSI_PERIOD)
    precio    = round(float(close.iloc[-1]), 4)
    e9v       = round(e9[-1], 4)
    e21v      = round(e21[-1], 4)
    cruce_alc = e9[-2] <= e21[-2] and e9v > e21v
    cruce_baj = e9[-2] >= e21[-2] and e9v < e21v
    señal     = "COMPRAR" if cruce_alc and rsi < RSI_MAX else "VENDER" if cruce_baj else "ESPERAR"
    diff_pct  = round((e9v - e21v) / e21v * 100, 3)
    info      = ACTIVOS_INFO.get(ticker, ("📊", ticker))
    return {
        "ticker": ticker, "precio": precio,
        "ema9": e9v, "ema21": e21v, "rsi": rsi,
        "señal": señal, "diff_pct": diff_pct,
        "en_posicion": False, "pos_pl": 0, "pos_qty": "0",
        "icono": info[0], "nombre": info[1]
    }

def comprar(datos):
    ticker      = datos["ticker"]
    precio      = datos["precio"]
    sym         = ACTIVOS[ticker]
    es_crypto   = "-USD" in ticker
    qty         = round(CAPITAL_POR_OPERACION / precio, 6) if es_crypto else round(CAPITAL_POR_OPERACION / precio, 4)
    tif         = "gtc" if es_crypto else "day"
    stop        = round(precio * (1 - STOP_LOSS), 2)
    tp          = round(precio * (1 + TAKE_PROFIT), 2)
    total       = round(qty * precio, 2)
    es_fraccion = not es_crypto and qty < 1.0

    print(f"\n  {'─'*60}", flush=True)
    log("buy", f"COMPRANDO {qty}x {ticker}")
    log("buy", f"Precio:      ${precio:,}")
    log("buy", f"Total:       ${total}")
    log("buy", f"Stop-Loss:   ${stop} (-{STOP_LOSS*100}%)")
    log("buy", f"Take-Profit: ${tp} (+{TAKE_PROFIT*100}%)")
    if es_fraccion:
        log("buy", "Orden simple (fraccion) — SL/TP gestionado por bot")
    print(f"  {'─'*60}\n", flush=True)

    try:
        if es_crypto or es_fraccion:
            resp = alpaca_post("orders", {
                "symbol": sym, "qty": str(qty),
                "side": "buy", "type": "market",
                "time_in_force": tif
            })
        else:
            resp = alpaca_post("orders", {
                "symbol": sym, "qty": str(qty),
                "side": "buy", "type": "market",
                "time_in_force": tif,
                "order_class": "bracket",
                "stop_loss":   {"stop_price": str(stop)},
                "take_profit": {"limit_price": str(tp)}
            })
        if resp.get("id"):
            log("buy", f"Orden ejecutada - ID: {resp['id'][:8]}...")
            estado["trades"] += 1
            estado["operaciones"].insert(0, {
                "time": ts(), "tipo": "COMPRA",
                "ticker": ticker, "precio": precio,
                "qty": qty, "total": total
            })
        else:
            log("warn", f"Respuesta: {str(resp)[:100]}")
    except Exception as e:
        log("error", f"Error comprando {ticker}: {str(e)[:80]}")

def vender(datos, pos):
    ticker    = datos["ticker"]
    precio    = datos["precio"]
    sym       = ACTIVOS[ticker]
    qty       = pos.get("qty", "0")
    pl        = round(float(pos.get("unrealized_pl", 0)), 2)
    es_crypto = "-USD" in ticker
    tif       = "gtc" if es_crypto else "day"

    print(f"\n  {'─'*60}", flush=True)
    log("sell", f"VENDIENDO {qty}x {ticker}")
    log("sell", f"Precio:  ${precio:,}")
    log("sell", f"P&L:     ${pl}")
    print(f"  {'─'*60}\n", flush=True)

    try:
        resp = alpaca_post("orders", {
            "symbol": sym, "qty": str(qty),
            "side": "sell", "type": "market",
            "time_in_force": tif
        })
        if resp.get("id"):
            log("sell", f"Venta ejecutada - ID: {resp['id'][:8]}...")
            estado["trades"] += 1
            estado["operaciones"].insert(0, {
                "time": ts(), "tipo": "VENTA",
                "ticker": ticker, "precio": precio,
                "qty": qty, "total": round(float(qty)*precio, 2)
            })
        else:
            log("warn", f"Respuesta: {str(resp)[:100]}")
    except Exception as e:
        log("error", f"Error vendiendo {ticker}: {str(e)[:80]}")

DASHBOARD = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Trading Bot</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root{--bg:#050a0e;--s:#0c1318;--b:#1a2530;--g:#00ff9d;--r:#ff3d6b;--y:#ffd166;--bl:#38bdf8;--t:#c8d8e4;--d:#4a6070}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--t);font-family:'Space Mono',monospace;padding:14px;min-height:100vh}
  h1{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--g)}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--b)}
  .pill{display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--g);font-size:9px;color:var(--g)}
  .pill.closed{border-color:var(--r);color:var(--r)}
  .dot{width:6px;height:6px;border-radius:50%;background:var(--g);animation:p 1.4s infinite}
  .dot.closed{background:var(--r);animation:none}
  @keyframes p{0%,100%{opacity:1;box-shadow:0 0 5px var(--g)}50%{opacity:.2}}
  .metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:12px}
  .m{background:var(--s);border:1px solid var(--b);padding:10px}
  .ml{font-size:8px;letter-spacing:.12em;text-transform:uppercase;color:var(--d);margin-bottom:4px}
  .mv{font-family:'Syne',sans-serif;font-size:18px;font-weight:800}
  .mv.g{color:var(--g)} .mv.r{color:var(--r)} .mv.b{color:var(--bl)} .mv.y{color:var(--y)}
  .tabs{display:flex;margin-bottom:12px;border-bottom:1px solid var(--b)}
  .tab{padding:8px 12px;font-size:10px;cursor:pointer;color:var(--d);border-bottom:2px solid transparent;transition:all .2s}
  .tab.active{color:var(--g);border-bottom-color:var(--g)}
  .panel{display:none} .panel.active{display:block}
  .table{width:100%;border-collapse:collapse;font-size:10px}
  .table th{text-align:left;padding:6px 8px;color:var(--d);font-size:8px;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--b)}
  .table td{padding:6px 8px;border-bottom:1px solid rgba(26,37,48,.4)}
  .badge{display:inline-block;padding:2px 7px;font-size:8px;font-weight:700}
  .badge.buy{background:rgba(0,255,157,.15);color:var(--g)}
  .badge.sell{background:rgba(255,61,107,.15);color:var(--r)}
  .badge.wait{background:rgba(255,209,102,.1);color:var(--y)}
  .bar{height:3px;margin-top:2px;max-width:50px;border-radius:2px}
  .log-box{background:var(--s);border:1px solid var(--b);padding:12px;max-height:300px;overflow-y:auto}
  .le{display:flex;gap:8px;font-size:10px;padding:4px 0;border-bottom:1px solid rgba(26,37,48,.4);line-height:1.5}
  .lt{color:var(--d);flex-shrink:0}
  .lbuy{color:var(--g)} .lsell{color:var(--r)} .linfo{color:var(--t)} .lwait{color:var(--y)} .lerror{color:var(--r)}
  .search{width:100%;background:var(--s);border:1px solid var(--b);color:var(--t);padding:8px 12px;font-family:'Space Mono',monospace;font-size:11px;margin-bottom:10px;outline:none}
  .search:focus{border-color:var(--g)}
  .footer{text-align:center;font-size:9px;color:var(--d);margin-top:10px}
</style>
</head>
<body>
<div class="header">
  <div><h1>TRADING BOT</h1><div style="font-size:9px;color:var(--d)" id="upd">—</div></div>
  <div class="pill" id="mpill"><div class="dot" id="mdot"></div><span id="mtxt">CARGANDO</span></div>
</div>
<div class="metrics">
  <div class="m"><div class="ml">Capital</div><div class="mv g" id="cap">$—</div></div>
  <div class="m"><div class="ml">P&L</div><div class="mv" id="pnl" style="color:var(--g)">$—</div></div>
  <div class="m"><div class="ml">Trades</div><div class="mv b" id="ntrades">0</div></div>
  <div class="m"><div class="ml">Activos</div><div class="mv y" id="nactivos">0</div></div>
</div>
<div class="tabs">
  <div class="tab active" onclick="showTab('mercado')">📊 MERCADO</div>
  <div class="tab" onclick="showTab('ops')">📋 OPERACIONES</div>
  <div class="tab" onclick="showTab('logp')">🔍 LOG</div>
</div>
<div id="mercado" class="panel active">
  <input class="search" id="search" placeholder="Buscar..." oninput="filtrar()">
  <table class="table">
    <thead><tr><th>Activo</th><th>Precio</th><th>EMA9 vs EMA21</th><th>RSI</th><th>Señal</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<div id="ops" class="panel">
  <table class="table">
    <thead><tr><th>Hora</th><th>Tipo</th><th>Activo</th><th>Precio</th><th>Total</th></tr></thead>
    <tbody id="ops-body"></tbody>
  </table>
  <div id="no-ops" style="text-align:center;padding:20px;color:var(--d);font-size:11px">Sin operaciones aun</div>
</div>
<div id="logp" class="panel">
  <div class="log-box"><div id="log"></div></div>
</div>
<div class="footer">Actualiza cada 10s · Paper Trading · 24/7</div>
<script>
let todos={};
function showTab(id){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['mercado','ops','logp'][i]===id));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
function filtrar(){
  const q=document.getElementById('search').value.toLowerCase();
  render(Object.values(todos).filter(a=>a.ticker.toLowerCase().includes(q)||a.nombre.toLowerCase().includes(q)));
}
function render(activos){
  document.getElementById('tbody').innerHTML=activos.map(a=>{
    const diff=a.diff_pct||0;
    const col=diff>0.05?'var(--g)':diff<-0.05?'var(--r)':'var(--y)';
    const bar=Math.min(Math.abs(diff)*20,50);
    const str=(diff>=0?'+':'')+diff+'%';
    const icon=diff>0.05?'▲':diff<-0.05?'▼':'▶';
    const pos=a.en_posicion?'<div style="font-size:8px;color:'+(a.pos_pl>=0?'var(--g)':'var(--r)')+';margin-top:2px">● '+a.pos_qty+' uds | P&L '+(a.pos_pl>=0?'+':'')+'$'+a.pos_pl+'</div>':'';
    return '<tr style="'+(a.en_posicion?'background:rgba(0,255,157,0.04)':'')+'">'
      +'<td>'
        +'<div style="display:flex;align-items:center;gap:6px">'
          +'<span style="font-size:16px">'+(a.icono||'📊')+'</span>'
          +'<div>'
            +'<div style="font-weight:700;font-size:10px">'+a.ticker+'</div>'
            +'<div style="font-size:8px;color:var(--d)">'+(a.nombre||'')+'</div>'
          +'</div>'
        +'</div>'
        +pos
      +'</td>'
      +'<td style="color:var(--bl)">$'+Number(a.precio).toLocaleString()+'</td>'
      +'<td><span style="color:'+col+'">'+icon+' '+str+'</span><div class="bar" style="width:'+bar+'px;background:'+col+'"></div></td>'
      +'<td style="color:'+(a.rsi>70?'var(--r)':a.rsi<30?'var(--g)':'var(--y)')+'">'+a.rsi+'</td>'
      +'<td><span class="badge '+(a.señal==='COMPRAR'?'buy':a.señal==='VENDER'?'sell':'wait')+'">'+a.señal+'</span></td>'
      +'</tr>';
  }).join('');
}
async function update(){
  try{
    const d=await(await fetch('/api/estado')).json();
    document.getElementById('cap').textContent='$'+Number(d.capital).toLocaleString();
    const pnl=d.pnl,pe=document.getElementById('pnl');
    pe.textContent=(pnl>=0?'+':'')+' $'+Math.abs(pnl).toLocaleString();
    pe.style.color=pnl>=0?'var(--g)':'var(--r)';
    document.getElementById('ntrades').textContent=d.trades;
    document.getElementById('nactivos').textContent=Object.keys(d.activos).length;
    document.getElementById('upd').textContent='Update: '+new Date().toLocaleTimeString('es-ES');
    const pill=document.getElementById('mpill'),dot=document.getElementById('mdot'),txt=document.getElementById('mtxt');
    if(d.mercado_abierto){pill.className='pill';dot.className='dot';txt.textContent='MERCADO ABIERTO';}
    else{pill.className='pill closed';dot.className='dot closed';txt.textContent='MERCADO CERRADO';}
    todos=d.activos;filtrar();
    const ob=document.getElementById('ops-body'),no=document.getElementById('no-ops');
    if(d.operaciones&&d.operaciones.length>0){
      no.style.display='none';
      ob.innerHTML=d.operaciones.map(o=>'<tr>'
        +'<td style="color:var(--d)">'+o.time+'</td>'
        +'<td><span class="badge '+(o.tipo==='COMPRA'?'buy':'sell')+'">'+o.tipo+'</span></td>'
        +'<td style="font-weight:700">'+o.ticker+'</td>'
        +'<td style="color:var(--bl)">$'+Number(o.precio).toLocaleString()+'</td>'
        +'<td style="color:var(--g)">$'+o.total+'</td>'
        +'</tr>').join('');
    }else{no.style.display='block';}
    document.getElementById('log').innerHTML=d.log.map(e=>'<div class="le"><span class="lt">'+e.time+'</span><span class="l'+e.tipo+'">'+e.msg+'</span></div>').join('');
  }catch(e){console.error(e);}
}
update();setInterval(update,10000);
</script>
</body>
</html>"""

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(DASHBOARD)

@app.route("/api/estado")
def api_estado():
    return jsonify(estado)

def run_bot():
    print("\n" + "="*75, flush=True)
    print("  TRADING BOT - PAPER TRADING", flush=True)
    print(f"  Activos: {len(ACTIVOS)} | Capital/op: ${CAPITAL_POR_OPERACION} | SL:{STOP_LOSS*100}% TP:{TAKE_PROFIT*100}%", flush=True)
    print("="*75 + "\n", flush=True)

    try:
        orders = alpaca_get("orders?status=filled&limit=20")
        for o in orders:
            if o.get("filled_at"):
                estado["operaciones"].append({
                    "time": o["filled_at"][11:19],
                    "tipo": "COMPRA" if o["side"] == "buy" else "VENTA",
                    "ticker": o["symbol"],
                    "precio": round(float(o.get("filled_avg_price", 0)), 2),
                    "qty": o.get("filled_qty", "0"),
                    "total": round(float(o.get("filled_avg_price", 0)) * float(o.get("filled_qty", 0)), 2)
                })
        log("info", f"Cargadas {len(orders)} operaciones previas de Alpaca")
    except Exception as e:
        log("warn", f"Operaciones no cargadas: {str(e)[:50]}")

    ciclo = 0
    while True:
        ciclo += 1
        try:
            cuenta  = alpaca_get("account")
            equity  = float(cuenta.get("equity", 0))
            cash    = float(cuenta.get("cash", 0))
            pnl     = round(equity - 100000, 2)
            estado.update({"capital": equity, "cash": cash, "pnl": pnl, "ultimo_update": ts()})

            clock = alpaca_get("clock")
            estado["mercado_abierto"] = clock.get("is_open", False)
            mercado_str = "ABIERTO" if estado["mercado_abierto"] else "CERRADO"

            print(f"\n{'='*75}", flush=True)
            print(f"  CICLO #{ciclo} — {ts()} | Capital: ${equity:,.2f} | P&L: {'+' if pnl>=0 else ''}${pnl:,.2f} | Mercado: {mercado_str}", flush=True)
            print(f"  {'─'*72}", flush=True)
            print(f"  {'':2} {'NOMBRE':<20} {'TICKER':<8} {'PRECIO':>11} | {'EMA9vsEMA21':>12} | {'RSI':>5} | {'SEÑAL':<8} POSICION", flush=True)
            print(f"  {'─'*72}", flush=True)

            señales = []
            for ticker in ACTIVOS:
                datos = analizar(ticker)
                if datos is None:
                    print(f"  ❌ {ticker}: sin datos", flush=True)
                    continue

                estado["activos"][ticker] = datos
                pos          = get_position(ACTIVOS[ticker].replace("/", ""))
                señal        = datos["señal"]
                diff         = datos["diff_pct"]
                es_crypto    = "-USD" in ticker
                puede_operar = es_crypto or estado["mercado_abierto"]

                if pos:
                    estado["activos"][ticker].update({
                        "en_posicion": True,
                        "pos_pl":  round(float(pos.get("unrealized_pl", 0)), 2),
                        "pos_qty": pos.get("qty", "0")
                    })
                    pl_val = round(float(pos.get("unrealized_pl", 0)), 2)
                    pos_str = f"[POS P&L:{'+' if pl_val>=0 else ''}${pl_val}]"
                else:
                    estado["activos"][ticker].update({"en_posicion": False, "pos_pl": 0, "pos_qty": "0"})
                    pos_str = "[SIN POSICION]"

                info   = ACTIVOS_INFO.get(ticker, ("📊", ticker))
                icono  = info[0]
                nombre = info[1]
                diff_str = f"{'+' if diff>=0 else ''}{diff:>7}%"
                señal_color = "🟢" if señal == "COMPRAR" else "🔴" if señal == "VENDER" else "⚪"

                print(f"  {icono} {nombre:<20} {ticker:<8} ${datos['precio']:>10,.2f} | {diff_str:>12} | {datos['rsi']:>5} | {señal_color}{señal:<7} {pos_str}", flush=True)

                if señal == "COMPRAR" and pos is None and puede_operar:
                    comprar(datos)
                    señales.append(datos)
                elif señal == "VENDER" and pos is not None and puede_operar:
                    if es_crypto:
                        vender(datos, pos)
                    else:
                        try:
                            open_orders = alpaca_get(f"orders?status=open&symbols={ACTIVOS[ticker]}")
                            for o in open_orders:
                                requests.delete(f"{BASE_URL}/v2/orders/{o['id']}", headers=HEADERS, timeout=10)
                                log("info", f"Bracket cancelado: {o['id'][:8]}")
                            time.sleep(0.5)
                            vender(datos, pos)
                        except Exception as e:
                            log("error", f"Error venta {ticker}: {str(e)[:60]}")

            estado["señales"] = señales
            print(f"\n  Esperando {INTERVALO}s hasta el proximo ciclo...", flush=True)

        except Exception as e:
            log("error", f"Error general: {str(e)[:80]}")

        time.sleep(INTERVALO)

def keep_alive():
    import urllib.request
    url = os.environ.get("RENDER_EXTERNAL_URL", "")
    while True:
        time.sleep(600)
        try:
            if url:
                urllib.request.urlopen(url + "/api/estado")
                print("Keep-alive ping OK", flush=True)
        except:
            pass

threading.Thread(target=run_bot, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
