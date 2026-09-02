# -*- coding: utf-8 -*-
"""
Modelo STOP_LOSS con las ultimas 3 VELAS HORARIAS completas antes de la
senal de compra. Cada feature se expande en 3 columnas individuales:
  rsi_t1, rsi_t2, rsi_t3  (1h, 2h, 3h antes)
  precio_t1, precio_t2, precio_t3
  vol_t1, vol_t2, vol_t3
Esto replica la idea de "3 velas antes" adaptado a la resolucion 1h
de los datos sinteticos.
"""
import os, re
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import roc_auc_score, confusion_matrix

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

N_VELAS = 3  # ultimas 3 velas horarias completas

def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

print("=== Modelo STOP_LOSS: 3 velas horarias previas como features ===\n")

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
                    compra = d2
                    for k in range(j+1, min(j+50, len(evts))):
                        t3, ts3, d3 = evts[k]
                        if t3 == "venta":
                            for l in range(k+1, min(k+20, len(evts))):
                                t4, ts4, d4 = evts[l]
                                if t4 == "balance":
                                    ts_entrada = parse_ts(ts2)

                                    # Ultimas N velas horarias COMPLETAS antes de la entrada
                                    # Excluimos la vela actual (la de la senal) porque sus features
                                    # ya estan en los campos base (rsi, volatilidad, etc.)
                                    senal_idx = None
                                    for idx, s in enumerate(senales_horarias):
                                        if parse_ts(s["ts"]) == parse_ts(ts_senal):
                                            senal_idx = idx
                                            break

                                    previas = []
                                    if senal_idx is not None:
                                        inicio = max(0, senal_idx - N_VELAS)
                                        previas = senales_horarias[inicio:senal_idx]  # excluye la actual
                                        previas = previas[-N_VELAS:]  # solo las N mas cercanas

                                    row = {
                                        "ts_compra": ts_senal,
                                        "symbol": sym,
                                        "venta_motivo": d3["motivo"],
                                        "ganancia_neta": round(d4["pnl"], 4),
                                        "target_stop": 1 if "STOP_LOSS" in d3["motivo"] else 0,
                                        "rsi": senal["rsi"],
                                        "rsi_umbral": senal["rsi_umbral"],
                                        "volatilidad": senal["vol"],
                                        "tp_pct": senal["tp_pct"],
                                        "monto": senal["monto"],
                                    }

                                    # Las 3 velas horarias PREVIAS como features separadas
                                    # t1 = 1 hora antes, t2 = 2 horas antes, t3 = 3 horas antes
                                    for vi, s in enumerate(reversed(previas)):
                                        suf = f"t{vi+1}"
                                        row[f"rsi_{suf}"] = s["rsi"]
                                        row[f"vol_{suf}"] = s["vol"]
                                        row[f"precio_{suf}"] = s["precio"]

                                    # Rellenar con 0 si no hay suficientes velas previas
                                    for vi in range(len(previas), N_VELAS):
                                        suf = f"t{vi+1}"
                                        row[f"rsi_{suf}"] = row["rsi"]
                                        row[f"vol_{suf}"] = row["volatilidad"]
                                        row[f"precio_{suf}"] = 0

                                    trades.append(row)
                                    i = l + 1
                                    break
                        break
                break
        i += 1

df = pd.DataFrame(trades).sort_values("ts_compra").reset_index(drop=True)
n_stop = df["target_stop"].sum()
print(f"Trades: {len(df)}, STOP_LOSS: {n_stop} ({n_stop/len(df)*100:.1f}%)")

base_feats = ["rsi", "rsi_umbral", "volatilidad", "tp_pct", "monto"]
vela_feats = []
for suf in [f"t{i+1}" for i in range(N_VELAS)]:
    vela_feats += [f"rsi_{suf}", f"vol_{suf}", f"precio_{suf}"]

FEATURE_COLS = base_feats + vela_feats

# Derivadas
df["rsi_distancia"] = df["rsi_umbral"] - df["rsi"]
df["rsi_distancia_rel"] = df["rsi_distancia"] / df["rsi_umbral"].clip(lower=0.01)
FEATURE_COLS += ["rsi_distancia", "rsi_distancia_rel"]

split = int(len(df) * 0.8)
train = df.iloc[:split]
test = df.iloc[split:]

X_train, y_train = train[FEATURE_COLS].fillna(0), train["target_stop"]
X_test, y_test = test[FEATURE_COLS].fillna(0), test["target_stop"]

scale = (y_train == 0).sum() / y_train.sum() if y_train.sum() > 0 else 1
print(f"Train: {len(train)} STOP={y_train.sum()} NO={(y_train==0).sum()} scale={scale:.1f}")

model = xgb.XGBClassifier(
    n_estimators=300, max_depth=4, learning_rate=0.03,
    scale_pos_weight=scale, subsample=0.8, colsample_bytree=0.8,
    random_state=42, eval_metric="logloss",
)
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]
auc = roc_auc_score(y_test, y_proba)
cm = confusion_matrix(y_test, y_pred)
tn, fp, fn, tp = cm[0][0], cm[0][1], cm[1][0], cm[1][1]

print(f"\n=== RESULTADOS TEST ===")
print(f"ROC AUC: {auc:.4f}")
print(f"Matriz: TN={tn} FP={fp} | FN={fn} TP={tp}")
if tp+fp>0: print(f"Precision STOP: {tp/(tp+fp):.3f}")
if tp+fn>0: print(f"Recall STOP: {tp/(tp+fn):.3f}")

# Ultimas 2 semanas
from datetime import datetime as dt_mod
dos_semanas = dt_mod.now() - timedelta(days=14)
ultimas = df[df["ts_compra"] >= dos_semanas.strftime("%Y-%m-%d")].copy()
ultimas["pred_stop"] = model.predict(ultimas[FEATURE_COLS].fillna(0))
pnl_sin = ultimas["ganancia_neta"].sum()
pnl_con = ultimas[ultimas["pred_stop"]==0]["ganancia_neta"].sum()
print(f"\n=== ULTIMAS 2 SEMANAS ({len(ultimas)} trades) ===")
print(f"SIN filtro: ${pnl_sin:.2f}")
print(f"CON filtro: ${pnl_con:.2f} ({len(ultimas[ultimas['pred_stop']==0])} trades)")

stop_reales = ultimas[ultimas["target_stop"]==1]
stop_evitados = stop_reales[stop_reales["pred_stop"]==1]
stop_no_evitados = stop_reales[stop_reales["pred_stop"]==0]
print(f"STOP evitados: {len(stop_evitados)} (${stop_evitados['ganancia_neta'].sum():+.2f})")
print(f"STOP no evitados: {len(stop_no_evitados)} (${stop_no_evitados['ganancia_neta'].sum():+.2f})")

print(f"\nFeature importances:")
fi = dict(zip(FEATURE_COLS, model.feature_importances_))
for name, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:15]:
    print(f"  {name:<20} {imp:.4f}")
