import os, time, requests, pandas as pd, threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string

API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
BASE_URL   = "https://paper-api.alpaca.markets"
DATA_URL   = "https://data.alpaca.markets"

CAPITAL_REAL = 500    # ← Cambia este número para ajustar cuánto usa el bot
STOP_LOSS    = 0.015
TAKE_PROFIT  = 0.030
EMA_FAST     = 9
EMA_SLOW     = 21
RSI_PERIOD   = 14
RSI_MAX      = 70
INTERVALO    = 120

ACTIVOS_CRYPTO = [
    "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD",
    "DOGE/USD", "AVAX/USD", "LINK/USD", "LTC/USD", "UNI/USD",
]

ACTIVOS_STOCKS = [
    "AAPL", "TSLA", "MSFT", "GOOGL", "AMZN",
    "NVDA", "META", "NFLX", "AMD",   "INTC",
    "KO",   "PEP",  "MCD",  "DIS",   "NKE",
    "JPM",  "BAC",  "V",    "MA",    "GS",
    "PFE",  "JNJ",  "XOM",  "CVX",   "GLD",
    "SLV",  "USO",  "SPY",  "QQQ",   "ARKK",
]

TOTAL_ACTIVOS = len(ACTIVOS_CRYPTO) + len(ACTIVOS_STOCKS)
POR_ACTIVO    = round(CAPITAL_REAL / TOTAL_ACTIVOS, 2)

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

estado = {
    "activos": {}, "señales": [], "operaciones": [],
    "capital": 0.0, "pnl": 0.0, "trades": 0,
    "log": [], "ultimo_update": "—",
    "capital_real": CAPITAL_REAL,
    "por_activo": POR_ACTIVO
}

def log(tipo, msg):
    hora = datetime.now().strftime("%H:%M:%S")
    iconos = {"buy":"🟢","sell":"🔴","wait":"⏳","info":"📊","error":"❌","warn":"⚠️"}
    print(f"{hora}  {iconos.get(tipo,'•')}  {msg}", flush=True)
    estado["log"].insert(0, {"time": hora, "tipo": tipo, "msg": msg})
    if len(estado["log"]) > 100:
        estado["log"].pop()

def alpaca_get(path):
    r = requests.get(f"{BASE_URL}/v2/{path}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def alpaca_post(path, data):
    r = requests.post(f"{BASE_URL}/v2/{path}", headers=HEADERS, json=data, timeout=10)
    return r.json()

def get_bars_crypto(symbol, limit=60):
    try:
        url = f"{DATA_URL}/v1beta3/crypto/us/bars"
        params = {"symbols": symbol, "timeframe": "5Min", "limit": limit}
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        bars = r.json().get("bars", {}).get(symbol, [])
        if not bars or len(bars) < 22:
            return None
        df = pd.DataFrame(bars)
        df["c"] = df["c"].astype(float)
        return df
    except Exception as e:
        log("error", f"Crypto bars {symbol}: {str(e)[:50]}")
        return None

def get_bars_stock(symbol, limit=60):
    try:
        url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
        params = {"timeframe": "5Min", "limit": limit, "feed": "iex"}
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        bars = r.json().get("bars", [])
        if not bars or len(bars) < 22:
            return None
        df = pd.DataFrame(bars)
        df["c"] = df["c"].astype(float)
        return df
    except Exception as e:
        log("error", f"Stock bars {symbol}: {str(e)[:50]}")
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
        sym = symbol.replace("/", "")
        return alpaca_get(f"positions/{sym}")
    except:
        return None

def analizar(symbol, es_crypto):
    df = get_bars_crypto(symbol) if es_crypto else get_bars_stock(symbol)
    if df is None:
        return None
    close  = df["c"]
    e9     = calc_ema(close, EMA_FAST)
    e21    = calc_ema(close, EMA_SLOW)
    rsi    = calc_rsi(close, RSI_PERIOD)
    precio = round(float(close.iloc[-1]), 4)
    e9v    = round(e9[-1], 4)
    e21v   = round(e21[-1], 4)
    e9p, e21p = e9[-2], e21[-2]
    cruce_alc = e9p <= e21p and e9v > e21v
    cruce_baj = e9p >= e21p and e9v < e21v
    señal = "COMPRAR" if cruce_alc and rsi < RSI_MAX else "VENDER" if cruce_baj else "ESPERAR"
    return {
        "ticker": symbol, "precio": precio,
        "ema9": e9v, "ema21": e21v, "rsi": rsi,
        "señal": señal, "update": datetime.now().strftime("%H:%M:%S")
    }

def run_bot():
    log("info", f"🤖 Bot iniciado — {TOTAL_ACTIVOS} activos")
    log("info", f"💰 Capital limitado: ${CAPITAL_REAL} | Por activo: ${POR_ACTIVO}")

    while True:
        try:
            cuenta  = alpaca_get("account")
            equity  = round(float(cuenta.get("equity", 0)), 2)
            estado["capital"]       = equity
            estado["pnl"]           = round(equity - 100000.0, 2)
            estado["ultimo_update"] = datetime.now().strftime("%H:%M:%S")

            log("info", f"💰 Capital total: ${equity:,} | Usando: ${CAPITAL_REAL}")

            señales_activas = []

            # ── CRYPTO (24/7) ─────────────────────────
            for symbol in ACTIVOS_CRYPTO:
                try:
                    datos = analizar(symbol, es_crypto=True)
                    if datos is None:
                        continue
                    estado["activos"][symbol] = datos
                    pos    = get_position(symbol)
                    precio = datos["precio"]
                    señal  = datos["señal"]
                    qty    = round(POR_ACTIVO / precio, 6)

                    if señal == "COMPRAR" and pos is None and qty > 0:
                        try:
                            alpaca_post("orders", {
                                "symbol": symbol, "qty": str(qty),
                                "side": "buy", "type": "market",
                                "time_in_force": "gtc"
                            })
                            estado["trades"] += 1
                            log("buy", f"COMPRA {symbol} @ ${precio:,} | qty:{qty} | ${round(qty*precio,2)}")
                            estado["operaciones"].insert(0, {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "tipo": "COMPRA", "ticker": symbol,
                                "precio": precio, "qty": qty,
                                "total": round(qty * precio, 2)
                            })
                        except Exception as e:
                            log("warn", f"{symbol}: {str(e)[:50]}")

                    elif señal == "VENDER" and pos is not None:
                        try:
                            alpaca_post("orders", {
                                "symbol": symbol, "qty": pos.get("qty"),
                                "side": "sell", "type": "market",
                                "time_in_force": "gtc"
                            })
                            estado["trades"] += 1
                            log("sell", f"VENTA {symbol} @ ${precio:,}")
                            estado["operaciones"].insert(0, {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "tipo": "VENTA", "ticker": symbol,
                                "precio": precio, "qty": pos.get("qty"),
                                "total": round(float(pos.get("qty", 0)) * precio, 2)
                            })
                        except Exception as e:
                            log("warn", f"{symbol}: {str(e)[:50]}")

                    if señal == "COMPRAR":
                        señales_activas.append(datos)

                except Exception as e:
                    log("error", f"{symbol}: {str(e)[:50]}")

            # ── ACCIONES (solo mercado abierto) ───────
            try:
                clock = alpaca_get("clock")
                mercado_abierto = clock.get("is_open", False)
            except:
                mercado_abierto = False

            for symbol in ACTIVOS_STOCKS:
                try:
                    datos = analizar(symbol, es_crypto=False)
                    if datos is None:
                        continue
                    estado["activos"][symbol] = datos

                    if not mercado_abierto:
                        continue

                    pos    = get_position(symbol)
                    precio = datos["precio"]
                    señal  = datos["señal"]
                    qty    = max(1, int(POR_ACTIVO / precio))

                    if señal == "COMPRAR" and pos is None and qty >= 1:
                        try:
                            alpaca_post("orders", {
                                "symbol": symbol, "qty": str(qty),
                                "side": "buy", "type": "market",
                                "time_in_force": "day"
                            })
                            estado["trades"] += 1
                            log("buy", f"COMPRA {symbol} @ ${precio} | qty:{qty} | ${round(qty*precio,2)}")
                            estado["operaciones"].insert(0, {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "tipo": "COMPRA", "ticker": symbol,
                                "precio": precio, "qty": qty,
                                "total": round(qty * precio, 2)
                            })
                        except Exception as e:
                            log("warn", f"{symbol}: {str(e)[:50]}")

                    elif señal == "VENDER" and pos is not None:
                        try:
                            alpaca_post("orders", {
                                "symbol": symbol, "qty": pos.get("qty"),
                                "side": "sell", "type": "market",
                                "time_in_force": "day"
                            })
                            estado["trades"] += 1
                            log("sell", f"VENTA {symbol} @ ${precio}")
                            estado["operaciones"].insert(0, {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "tipo": "VENTA", "ticker": symbol,
                                "precio": precio, "qty": pos.get("qty"),
                                "total": round(float(pos.get("qty", 0)) * precio, 2)
                            })
                        except Exception as e:
                            log("warn", f"{symbol}: {str(e)[:50]}")

                    if señal == "COMPRAR":
                        señales_activas.append(datos)

                except Exception as e:
                    log("error", f"{symbol}: {str(e)[:50]}")

            estado["señales"] = señales_activas
            log("info", f"✅ Ciclo completo — {len(estado['activos'])} activos | {len(señales_activas)} señales")

        except Exception as e:
            log("error", f"Error general: {str(e)[:80]}")

        time.sleep(INTERVALO)

DASHBOARD = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Trading Bot Pro</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root{--bg:#050a0e;--s:#0c1318;--b:#1a2530;--g:#00ff9d;--r:#ff3d6b;--y:#ffd166;--bl:#38bdf8;--t:#c8d8e4;--d:#4a6070}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--t);font-family:'Space Mono',monospace;padding:14px;min-height:100vh}
  body::before{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.07) 2px,rgba(0,0,0,.07) 4px);pointer-events:none;z-index:999}
  h1{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;color:var(--g)}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--b)}
  .pill{display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--g);font-size:9px;color:var(--g)}
  .dot{width:6px;height:6px;border-radius:50%;background:var(--g);animation:p 1.4s infinite}
  @keyframes p{0%,100%{opacity:1;box-shadow:0 0 5px var(--g)}50%{opacity:.2}}
  .metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:12px}
  .m{background:var(--s);border:1px solid var(--b);padding:10px}
  .ml{font-size:8px;letter-spacing:.12em;text-transform:uppercase;color:var(--d);margin-bottom:4px}
  .mv{font-family:'Syne',sans-serif;font-size:18px;font-weight:800}
  .mv.g{color:var(--g)} .mv.r{color:var(--r)} .mv.b{color:var(--bl)} .mv.y{color:var(--y)}
  .ms{font-size:8px;color:var(--d);margin-top:2px}
  .tabs{display:flex;gap:0;margin-bottom:12px;border-bottom:1px solid var(--b)}
  .tab{padding:8px 12px;font-size:10px;cursor:pointer;color:var(--d);letter-spacing:.06em;border-bottom:2px solid transparent;transition:all .2s}
  .tab.active{color:var(--g);border-bottom-color:var(--g)}
  .panel{display:none} .panel.active{display:block}
  .table{width:100%;border-collapse:collapse;font-size:10px}
  .table th{text-align:left;padding:6px 8px;color:var(--d);font-size:8px;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--b)}
  .table td{padding:6px 8px;border-bottom:1px solid rgba(26,37,48,.4)}
  .table tr:hover td{background:rgba(255,255,255,.02)}
  .badge{display:inline-block;padding:2px 7px;font-size:8px;font-weight:700}
  .badge.buy{background:rgba(0,255,157,.15);color:var(--g)}
  .badge.sell{background:rgba(255,61,107,.15);color:var(--r)}
  .badge.wait{background:rgba(255,209,102,.1);color:var(--y)}
  .signals-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .sig-card{background:var(--s);border:1px solid var(--g);padding:10px}
  .sig-ticker{font-family:'Syne',sans-serif;font-size:16px;font-weight:800;color:var(--g)}
  .sig-price{font-size:11px;color:var(--bl);margin-top:2px}
  .sig-info{font-size:9px;color:var(--d);margin-top:4px}
  .log-box{background:var(--s);border:1px solid var(--b);padding:12px;max-height:300px;overflow-y:auto}
  .log-box::-webkit-scrollbar{width:2px}
  .log-box::-webkit-scrollbar-thumb{background:var(--b)}
  .le{display:flex;gap:8px;font-size:10px;padding:4px 0;border-bottom:1px solid rgba(26,37,48,.4);line-height:1.5}
  .lt{color:var(--d);flex-shrink:0}
  .lbuy{color:var(--g)} .lsell{color:var(--r)} .linfo{color:var(--t)} .lwait{color:var(--y)} .lerror{color:var(--r)}
  .search{width:100%;background:var(--s);border:1px solid var(--b);color:var(--t);padding:8px 12px;font-family:'Space Mono',monospace;font-size:11px;margin-bottom:10px;outline:none}
  .search:focus{border-color:var(--g)}
  .info-bar{background:var(--s);border:1px solid var(--b);padding:8px 12px;margin-bottom:12px;font-size:10px;display:flex;justify-content:space-between}
  .footer{text-align:center;font-size:9px;color:var(--d);margin-top:10px}
</style>
</head>
<body>
<div class="header">
  <div><h1>TRADING BOT PRO</h1><div style="font-size:9px;color:var(--d)" id="upd">—</div></div>
  <div class="pill"><div class="dot"></div>PAPER · 40 ACTIVOS</div>
</div>

<div class="info-bar">
  <span>💰 Capital limitado: <span id="cap-real" style="color:var(--g)">$—</span></span>
  <span>📊 Por operación: <span id="por-op" style="color:var(--y)">$—</span></span>
</div>

<div class="metrics">
  <div class="m"><div class="ml">Capital Total</div><div class="mv g" id="cap">$—</div><div class="ms">Cuenta Alpaca</div></div>
  <div class="m"><div class="ml">P&L</div><div class="mv" id="pnl" style="color:var(--g)">$—</div><div class="ms">vs $100k inicial</div></div>
  <div class="m"><div class="ml">Señales</div><div class="mv y" id="nsig">0</div><div class="ms">activas ahora</div></div>
  <div class="m"><div class="ml">Trades</div><div class="mv b" id="ntrades">0</div><div class="ms">ejecutados</div></div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('senales')">🟢 SEÑALES</div>
  <div class="tab" onclick="showTab('mercado')">📊 MERCADO</div>
  <div class="tab" onclick="showTab('ops')">📋 OPERACIONES</div>
  <div class="tab" onclick="showTab('logp')">🔍 LOG</div>
</div>

<div id="senales" class="panel active">
  <div id="sig-grid" class="signals-grid"></div>
  <div id="no-sig" style="text-align:center;padding:30px;color:var(--d);font-size:11px">⏳ Esperando señales de compra...</div>
</div>

<div id="mercado" class="panel">
  <input class="search" id="search" placeholder="Buscar activo..." oninput="filtrar()">
  <table class="table">
    <thead><tr><th>Activo</th><th>Precio</th><th>EMA9</th><th>RSI</th><th>Señal</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<div id="ops" class="panel">
  <table class="table">
    <thead><tr><th>Hora</th><th>Tipo</th><th>Activo</th><th>Precio</th><th>Total $</th></tr></thead>
    <tbody id="ops-body"></tbody>
  </table>
  <div id="no-ops" style="text-align:center;padding:30px;color:var(--d);font-size:11px">Sin operaciones aún</div>
</div>

<div id="logp" class="panel">
  <div class="log-box"><div id="log"></div></div>
</div>

<div class="footer">↻ Actualiza cada 10s · Paper Trading · 24/7 en Railway</div>

<script>
let todosActivos={};
function showTab(id){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['senales','mercado','ops','logp'][i]===id));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
function filtrar(){
  const q=document.getElementById('search').value.toLowerCase();
  renderTabla(Object.values(todosActivos).filter(a=>a.ticker.toLowerCase().includes(q)));
}
function renderTabla(activos){
  document.getElementById('tbody').innerHTML=activos.map(a=>`
    <tr>
      <td style="font-weight:700">${a.ticker}</td>
      <td style="color:var(--bl)">$${Number(a.precio).toLocaleString()}</td>
      <td style="color:var(--d)">${a.ema9}</td>
      <td style="color:${a.rsi>70?'var(--r)':a.rsi<30?'var(--g)':'var(--y)'}">${a.rsi}</td>
      <td><span class="badge ${a.señal==='COMPRAR'?'buy':a.señal==='VENDER'?'sell':'wait'}">${a.señal}</span></td>
    </tr>`).join('');
}
async function update(){
  try{
    const d=await(await fetch('/api/estado')).json();
    document.getElementById('cap').textContent='$'+Number(d.capital).toLocaleString();
    document.getElementById('cap-real').textContent='$'+Number(d.capital_real).toLocaleString();
    document.getElementById('por-op').textContent='$'+Number(d.por_activo).toLocaleString();
    const pnl=d.pnl,pe=document.getElementById('pnl');
    pe.textContent=(pnl>=0?'+':'')+' $'+Math.abs(pnl).toLocaleString();
    pe.style.color=pnl>=0?'var(--g)':'var(--r)';
    document.getElementById('nsig').textContent=d.señales.length;
    document.getElementById('ntrades').textContent=d.trades;
    document.getElementById('upd').textContent='Update: '+new Date().toLocaleTimeString('es-ES');
    const grid=document.getElementById('sig-grid'),noSig=document.getElementById('no-sig');
    if(d.señales.length>0){
      noSig.style.display='none';
      grid.innerHTML=d.señales.map(s=>`
        <div class="sig-card">
          <div class="sig-ticker">▲ ${s.ticker}</div>
          <div class="sig-price">$${Number(s.precio).toLocaleString()}</div>
          <div class="sig-info">RSI: ${s.rsi} · EMA9: ${s.ema9}</div>
          <div class="sig-info" style="color:var(--g)">EMA cruzó al alza ✓</div>
        </div>`).join('');
    }else{noSig.style.display='block';grid.innerHTML='';}
    todosActivos=d.activos;
    filtrar();
    const ob=document.getElementById('ops-body'),no=document.getElementById('no-ops');
    if(d.operaciones&&d.operaciones.length>0){
      no.style.display='none';
      ob.innerHTML=d.operaciones.map(o=>`
        <tr>
          <td style="color:var(--d)">${o.time}</td>
          <td><span class="badge ${o.tipo==='COMPRA'?'buy':'sell'}">${o.tipo}</span></td>
          <td style="font-weight:700">${o.ticker}</td>
          <td style="color:var(--bl)">$${Number(o.precio).toLocaleString()}</td>
          <td style="color:var(--g)">$${o.total}</td>
        </tr>`).join('');
    }else{no.style.display='block';}
    document.getElementById('log').innerHTML=d.log.map(e=>`
      <div class="le"><span class="lt">${e.time}</span><span class="l${e.tipo}">${e.msg}</span></div>`).join('');
  }catch(e){console.error(e);}
}
update();setInterval(update,10000);
</script>
</body>
</html>
"""

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(DASHBOARD)

@app.route("/api/estado")
def api_estado():
    return jsonify(estado)

@app.route("/api/test")
def test():
    resultados = {}
    # Test crypto
    try:
        r = requests.get(f"{DATA_URL}/v1beta3/crypto/us/bars",
            headers=HEADERS,
            params={"symbols": "BTC/USD", "timeframe": "5Min", "limit": 3},
            timeout=10)
        bars = r.json().get("bars", {}).get("BTC/USD", [])
        resultados["crypto"] = {"ok": True, "bars": len(bars)}
    except Exception as e:
        resultados["crypto"] = {"ok": False, "error": str(e)}

    # Test acciones
    try:
        r = requests.get(f"{DATA_URL}/v2/stocks/AAPL/bars",
            headers=HEADERS,
            params={"timeframe": "5Min", "limit": 3, "feed": "iex"},
            timeout=10)
        resultados["stocks_iex"] = {"ok": True, "status": r.status_code, "data": r.json()}
    except Exception as e:
        resultados["stocks_iex"] = {"ok": False, "error": str(e)}

    # Test sin feed
    try:
        r = requests.get(f"{DATA_URL}/v2/stocks/AAPL/bars",
            headers=HEADERS,
            params={"timeframe": "5Min", "limit": 3},
            timeout=10)
        resultados["stocks_sin_feed"] = {"ok": True, "status": r.status_code, "data": r.json()}
    except Exception as e:
        resultados["stocks_sin_feed"] = {"ok": False, "error": str(e)}

    return jsonify(resultados)

# Arrancar bot al importar (necesario para gunicorn)
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
