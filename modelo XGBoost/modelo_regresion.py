# -*- coding: utf-8 -*-
"""
Modelo REGRESION: predice PnL esperado (en vez de clasificar STOP/no-STOP).
Solo opera trades con PnL predicho > umbral.
Usa ventana interpolada 5min para features de tendencia.
"""
import os, re
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, r2_score
from scipy import stats as sp_stats

LOG_DIR = Path(r"D:\BinanceApi\logs")
BASE = Path(r"D:\BinanceApi\modelo XGBoost")

RE_TS = re.compile(r"\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
RE_SENAL = re.compile(
    r"\[SENAL COMPRA\] (?P<sym>\w+/USDT) \| "
    r"RSI=(?P<rsi>[\d.]+) \(umbral (?P<rsi_umbral>[\d.]+)\) \| "
    r"volatilidad 7d=(?P<vol>[\d.]+)% \| "
    r"TP=(?P<tp_pct>[\d.]+)% \| precio=[$](?P<precio>[\d.]+) \| "
    r"invirtiendo [$](?P<monto>[\d.]+)"
)
RE_COMPRA_OK = re.compile(
    r"\[COMPRA OK\] (?P<sym>\w+/USDT) \| "
    r"(?P<cant>[\de.-]+) unidades \([$](?P<monto>[\d.]+)\) \| "
    r"SL=[$](?P<sl>[\d.]+) TP=[$](?P<tp>[\d.]+)"
)
RE_VENTA = re.compile(r"\[VENTA-(?P<motivo>[^\]]+)\] (?P<sym>\w+/USDT)")
RE_BALANCE = re.compile(r"\[BALANCE\] (?P<sym>\w+/USDT): PnL (?P<pnl>[+-]?[\d.]+)")

INTERVALO_MIN = 5
EVALS_EN_VENTANA = 12

def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

def interpolar_evaluaciones(senales_horarias, ts_hasta, n=EVALS_EN_VENTANA):
    if len(senales_horarias) < 2:
        return []
    virtuales = []
    for i in range(len(senales_horarias) - 1):
        s1 = senales_horarias[i]
        s2 = senales_horarias[i+1]
        t1 = parse_ts(s1["ts"])
        t2 = parse_ts(s2["ts"])
        diff_min = (t2 - t1).total_seconds() / 60
        pasos = int(diff_min / INTERVALO_MIN)
        for p in range(pasos):
            frac = p / pasos if pasos > 0 else 0
            vt = t1 + timedelta(minutes=p * INTERVALO_MIN)
            if vt > ts_hasta:
                break
            rsi_interp = s1["rsi"] + (s2["rsi"] - s1["rsi"]) * frac
            vol_interp = s1["vol"] + (s2["vol"] - s1["vol"]) * frac
            tp_interp = s1["tp_pct"] + (s2["tp_pct"] - s1["tp_pct"]) * frac
            precio_interp = s1["precio"] + (s2["precio"] - s1["precio"]) * frac
            virtuales.append({
                "rsi": rsi_interp, "vol": vol_interp,
                "tp_pct": tp_interp, "precio": precio_interp,
                "rsi_umbral": s1["rsi_umbral"],
                "ts": vt,
            })
    return virtuales[-n:]

print("=== Modelo REGRESION (predice PnL) ===\n")

logs = sorted(f for f in os.listdir(LOG_DIR) if f.startswith("sintetic_bot_trading_") and f.endswith(".log"))
eventos = {"BTC/USDT": [], "ETH/USDT": []}
for logfile in logs:
    ruta = LOG_DIR / logfile
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                ts_m = RE_TS.search(linea)
                if not ts_m: continue
                ts = ts_m.group("ts")
                m = RE_SENAL.search(linea)
                if m:
                    s = m.group("sym"); eventos[s].append(("senal", ts, {
                        "rsi": float(m.group("rsi")), "rsi_umbral": float(m.group("rsi_umbral")),
                        "vol": float(m.group("vol")), "tp_pct": float(m.group("tp_pct")),
                        "precio": float(m.group("precio")), "monto": float(m.group("monto")),
                    })); continue
                m = RE_COMPRA_OK.search(linea)
                if m:
                    s = m.group("sym"); eventos[s].append(("compra", ts, {
                        "cantidad": float(m.group("cant")), "monto": float(m.group("monto")),
                        "sl": float(m.group("sl")), "tp": float(m.group("tp")),
                    })); continue
                m = RE_VENTA.search(linea)
                if m:
                    s = m.group("sym"); eventos[s].append(("venta", ts, {
                        "motivo": m.group("motivo"),
                    })); continue
                m = RE_BALANCE.search(linea)
                if m:
                    s = m.group("sym"); eventos[s].append(("balance", ts, {
                        "pnl": float(m.group("pnl")),
                    })); continue
    except: pass

trades = []
for sym in ["BTC/USDT", "ETH/USDT"]:
    evts = eventos[sym]
    senales_horarias = [{**d, "ts": ts} for t, ts, d in evts if t == "senal"]
    i = 0
    while i < len(evts):
        t1, ts1, d1 = evts[i]
        if t1 == "senal":
            senal = d1; ts_senal = ts1
            for j in range(i+1, min(i+50, len(evts))):
                t2, ts2, d2 = evts[j]
                if t2 == "compra":
                    ts_entrada = parse_ts(ts2)
                    compra = d2
                    senal_hasta = [s for s in senales_horarias if parse_ts(s["ts"]) <= ts_entrada]
                    virtuales = interpolar_evaluaciones(senal_hasta, ts_entrada)
                    for k in range(j+1, min(j+50, len(evts))):
                        t3, ts3, d3 = evts[k]
                        if t3 == "venta":
                            for l in range(k+1, min(k+20, len(evts))):
                                t4, ts4, d4 = evts[l]
                                if t4 == "balance":
                                    win_rsi = [v["rsi"] for v in virtuales]
                                    win_vol = [v["vol"] for v in virtuales]
                                    win_precio = [v["precio"] for v in virtuales]

                                    if len(win_rsi) >= 3:
                                        slope_rsi = sp_stats.linregress(range(len(win_rsi)), win_rsi).slope
                                        slope_vol = sp_stats.linregress(range(len(win_vol)), win_vol).slope
                                        slope_precio = sp_stats.linregress(range(len(win_precio)), win_precio).slope
                                    else:
                                        slope_rsi = slope_vol = slope_precio = 0.0

                                    row = {
                                        "ts_compra": ts_senal,
                                        "symbol": sym,
                                        "venta_motivo": d3["motivo"],
                                        "ganancia_neta": round(d4["pnl"], 4),
                                        "rsi": senal["rsi"],
                                        "rsi_umbral": senal["rsi_umbral"],
                                        "volatilidad": senal["vol"],
                                        "tp_pct": senal["tp_pct"],
                                        "monto": senal["monto"],
                                        "rsi_slope": slope_rsi,
                                        "rsi_mean": np.mean(win_rsi) if win_rsi else 0,
                                        "rsi_std": np.std(win_rsi) if win_rsi else 0,
                                        "rsi_min": min(win_rsi) if win_rsi else 0,
                                        "rsi_max": max(win_rsi) if win_rsi else 0,
                                        "rsi_ult_prim": win_rsi[-1] - win_rsi[0] if len(win_rsi) >= 2 else 0,
                                        "vol_slope": slope_vol,
                                        "vol_mean": np.mean(win_vol) if win_vol else 0,
                                        "vol_std": np.std(win_vol) if win_vol else 0,
                                        "precio_slope": slope_precio,
                                        "n_ventana": len(virtuales),
                                    }
                                    trades.append(row)
                                    i = l + 1
                                    break
                        break
                break
        i += 1

df = pd.DataFrame(trades).sort_values("ts_compra").reset_index(drop=True)
print(f"Trades totales: {len(df)}")

# Features
df["rsi_distancia"] = df["rsi_umbral"] - df["rsi"]
df["rsi_distancia_rel"] = df["rsi_distancia"] / df["rsi_umbral"].clip(lower=0.01)
df["rsi_vs_mean"] = df["rsi"] - df["rsi_mean"]
df["rsi_vs_min"] = df["rsi"] - df["rsi_min"]

FEATURE_COLS = [
    "rsi", "rsi_umbral", "rsi_distancia", "rsi_distancia_rel",
    "volatilidad", "tp_pct", "monto",
    "rsi_slope", "rsi_mean", "rsi_std", "rsi_min", "rsi_max",
    "rsi_ult_prim", "rsi_vs_mean", "rsi_vs_min",
    "vol_slope", "vol_mean", "vol_std",
    "precio_slope", "n_ventana",
]

split = int(len(df) * 0.8)
train = df.iloc[:split]
test = df.iloc[split:]

X_train, y_train = train[FEATURE_COLS], train["ganancia_neta"]
X_test, y_test = test[FEATURE_COLS], test["ganancia_neta"]

model = xgb.XGBRegressor(
    n_estimators=500, max_depth=5, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.8,
    random_state=42,
)
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

y_pred = model.predict(X_test)
mae = mean_absolute_error(y_test, y_pred)
r2 = r2_score(y_test, y_pred)

print(f"\n=== RESULTADOS TEST ===")
print(f"MAE: ${mae:.4f}")
print(f"R2: {r2:.4f}")
print(f"Precio Promedio Real: ${y_test.mean():+.4f}")
print(f"Precio Promedio Pred: ${np.mean(y_pred):+.4f}")

# Correlacion direccional
from sklearn.metrics import accuracy_score
y_sign = np.sign(y_test)
p_sign = np.sign(y_pred)
dir_acc = accuracy_score(y_sign, p_sign)
print(f"Direccional Accuracy: {dir_acc:.4f}")

# Simulacion: filtrar con umbrales
resultados = test.copy()
resultados["pred_pnl"] = y_pred

print(f"\n=== SIMULACION FILTRO POR UMBRAL ===")
for umbral in [0.0, 0.02, 0.05, 0.08, 0.10, 0.15]:
    selec = resultados[resultados["pred_pnl"] >= umbral]
    pnl_sel = selec["ganancia_neta"].sum()
    print(f"  Umbral ${umbral:+.2f}: {len(selec)} trades, pnl=${pnl_sel:.2f}, "
          f"win%={(selec['ganancia_neta']>0).mean()*100:.0f}%")

print(f"\n  Sin filtro: {len(resultados)} trades, pnl=${resultados['ganancia_neta'].sum():.2f}, "
      f"win%={(resultados['ganancia_neta']>0).mean()*100:.0f}%")

# Ultimas 2 semanas
dos_semanas = datetime.now() - timedelta(days=14)
ultimas = df[df["ts_compra"] >= dos_semanas.strftime("%Y-%m-%d")].copy()
ultimas["pred_pnl"] = model.predict(ultimas[FEATURE_COLS])
pnl_sin = ultimas["ganancia_neta"].sum()
print(f"\n=== ULTIMAS 2 SEMANAS ({len(ultimas)} trades) ===")
print(f"SIN filtro: ${pnl_sin:.2f}")
for umbral in [0.02, 0.05, 0.08, 0.10]:
    selec = ultimas[ultimas["pred_pnl"] >= umbral]
    pnl_sel = selec["ganancia_neta"].sum()
    print(f"  Umbral ${umbral:+.2f}: {len(selec)} trades, pnl=${pnl_sel:.2f}, "
          f"win%={(selec['ganancia_neta']>0).mean()*100:.0f}%")

# Top features
print(f"\nTop 10 features:")
fi = dict(zip(FEATURE_COLS, model.feature_importances_))
for name, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:10]:
    print(f"  {name:<20} {imp:.4f}")
