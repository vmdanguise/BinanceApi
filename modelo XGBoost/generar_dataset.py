# -*- coding: utf-8 -*-
"""
Genera dataset limpio para entrenar modelo XGBoost.
- Extrae trades de logs reales + sintéticos
- Limpia outliers y duplicados
- Submuestrea por mes para balancear distribución temporal
- Guarda train/test split con TimeSeriesSplit (no shuffle para evitar lookahead bias)
"""

import os
import re
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

# ──────────────────────── CONFIG ────────────────────────

BASE = Path(__file__).parent.parent
LOG_DIR = BASE / "logs"
OUTPUT_DIR = BASE / "modelo XGBoost" / "dataset"
OUTPUT_DIR.mkdir(exist_ok=True)

# Patrones de parseo
RE_SENAL = re.compile(
    r"\[(?P<ts>[^\]]+)\] \[SENAL COMPRA\] (?P<symbol>\w+/USDT) \| "
    r"RSI=(?P<rsi>[\d.]+) \(umbral (?P<rsi_umbral>[\d.]+)\) \| "
    r"volatilidad 7d=(?P<volatilidad>[\d.]+)% \| "
    r"TP=(?P<tp_pct>[\d.]+)% \| precio=\$(?P<precio>[\d.]+) \| "
    r"invirtiendo \$(?P<monto>[\d.]+)"
)
RE_COMPRA_OK = re.compile(
    r"\[(?P<ts>[^\]]+)\] \[COMPRA OK\] (?P<symbol>\w+/USDT) \| "
    r"(?P<cantidad>[\de.-]+) unidades \((?P<monto>[\$0-9.]+)\) \| "
    r"SL=\$(?P<sl>[\d.]+) TP=\$(?P<tp>[\d.]+)"
)
RE_VENTA = re.compile(
    r"\[(?P<ts>[^\]]+)\] \[VENTA-(?P<motivo>[^\]]+)\] (?P<symbol>\w+/USDT): "
    r"(?P<cantidad>[\de.-]+) .+ @ \$(?P<precio>[\d.]+) = \$(?P<total>[\d.]+)"
)


def parsear_todos_los_logs():
    """Extrae todos los trades completos de logs reales y sintéticos."""
    logs = sorted(
        f for f in os.listdir(LOG_DIR)
        if (f.startswith("bot_trading_") or f.startswith("sintetic_bot_trading_")) and f.endswith(".log")
    )
    
    eventos = {"BTC/USDT": [], "ETH/USDT": []}
    
    for logfile in logs:
        ruta = LOG_DIR / logfile
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    
                    # Señal
                    m = RE_SENAL.search(linea)
                    if m:
                        sym = m.group("symbol")
                        eventos[sym].append({
                            "tipo": "senal",
                            "ts": m.group("ts"),
                            "rsi": float(m.group("rsi")),
                            "rsi_umbral": float(m.group("rsi_umbral")),
                            "volatilidad": float(m.group("volatilidad")),
                            "tp_pct": float(m.group("tp_pct")),
                            "precio": float(m.group("precio")),
                            "monto": float(m.group("monto")),
                            "log_file": logfile,
                        })
                        continue
                    
                    # Compra OK
                    m = RE_COMPRA_OK.search(linea)
                    if m:
                        sym = m.group("symbol")
                        eventos[sym].append({
                            "tipo": "compra",
                            "symbol": sym,
                            "ts": m.group("ts"),
                            "cantidad": float(m.group("cantidad")),
                            "monto": float(m.group("monto").replace("$", "")),
                            "sl": float(m.group("sl")),
                            "tp": float(m.group("tp")),
                            "log_file": logfile,
                        })
                        continue
                    
                    # Venta
                    m = RE_VENTA.search(linea)
                    if m:
                        sym = m.group("symbol")
                        eventos[sym].append({
                            "tipo": "venta",
                            "ts": m.group("ts"),
                            "motivo": m.group("motivo"),
                            "cantidad": float(m.group("cantidad")),
                            "precio": float(m.group("precio")),
                            "total": float(m.group("total")),
                            "log_file": logfile,
                        })
        except (OSError, UnicodeDecodeError) as e:
            print(f"[AVISO] No se pudo leer {logfile}: {e}")
    
    # Emparejar compra → venta (la señal es informativa)
    trades = []
    for sym in ["BTC/USDT", "ETH/USDT"]:
        evts = sorted(eventos[sym], key=lambda e: e["ts"])
        compra_pendiente = None
        
        for e in evts:
            if e["tipo"] == "compra":
                # Guardar compra pendiente
                compra_pendiente = e
            elif e["tipo"] == "venta" and compra_pendiente:
                # Emparejar venta con compra pendiente (misma cantidad ±10%)
                cant_compra = compra_pendiente["cantidad"]
                cant_venta = e["cantidad"]
                diff_cant = abs(cant_venta - cant_compra) / max(cant_compra, 0.0001)
                
                if diff_cant < 0.10:  # 10% tolerancia
                    # Buscar señal más cercana antes de la compra
                    ts_comp = datetime.strptime(compra_pendiente["ts"], "%Y-%m-%d %H:%M:%S")
                    senal_mas_cercana = None
                    min_diff = float('inf')
                    
                    for evt in evts:
                        if evt["tipo"] == "senal" and evt["ts"] <= compra_pendiente["ts"]:
                            ts_seal = datetime.strptime(evt["ts"], "%Y-%m-%d %H:%M:%S")
                            diff = (ts_comp - ts_seal).total_seconds()
                            if 0 <= diff < 600 and diff < min_diff:  # Dentro de 10 min
                                min_diff = diff
                                senal_mas_cercana = evt
                    
                    if senal_mas_cercana:
                        trades.append({
                            **senal_mas_cercana,
                            **compra_pendiente,
                            "venta_ts": e["ts"],
                            "venta_motivo": e["motivo"],
                            "venta_precio": e["precio"],
                            "venta_total": e["total"],
                        })
                    else:
                        # Trade sin señal (usar datos de compra)
                        trades.append({
                            "ts": compra_pendiente["ts"],
                            "symbol": sym,
                            "rsi": 30.0,  # Default
                            "rsi_umbral": 30.0,
                            "volatilidad": 3.0,
                            "tp_pct": compra_pendiente["tp"] / compra_pendiente["precio"] * 100 if compra_pendiente["precio"] > 0 else 2.0,
                            "precio": compra_pendiente["monto"] / compra_pendiente["cantidad"] if compra_pendiente["cantidad"] > 0 else compra_pendiente["precio"],
                            "monto": compra_pendiente["monto"],
                            "sl": compra_pendiente["sl"],
                            "tp": compra_pendiente["tp"],
                            "cantidad": compra_pendiente["cantidad"],
                            "venta_ts": e["ts"],
                            "venta_motivo": e["motivo"],
                            "venta_precio": e["precio"],
                            "venta_total": e["total"],
                            "log_file": compra_pendiente.get("log_file", ""),
                        })
                    
                compra_pendiente = None  # Reset incluso si no emparejó
    
    return trades


def feature_engineering(trades):
    """Convierte trades crudos en DataFrame con features y target."""
    if not trades:
        return pd.DataFrame()
    
    rows = []
    for t in trades:
        rsi = t["rsi"]
        rsi_umbral = t["rsi_umbral"]
        volatilidad = t["volatilidad"]
        tp_pct = t["tp_pct"]
        precio_entrada = t["precio"]
        monto = t["monto"]
        sl_price = t["sl"]
        tp_price = t["tp"]
        
        # Features derivadas
        rsi_distancia = rsi_umbral - rsi
        rsi_distancia_rel = rsi_distancia / rsi_umbral if rsi_umbral > 0 else 0
        sl_pct = (1 - sl_price / precio_entrada) * 100
        tp_sl_ratio = tp_pct / sl_pct if sl_pct > 0 else 999
        vol_tp_interac = volatilidad * tp_pct
        spread_tp_sl = (tp_price - sl_price) / precio_entrada * 100
        log_precio = math.log(precio_entrada)
        es_btc = 1 if t.get("symbol") == "BTC/USDT" else 0
        es_eth = 1 if t.get("symbol") == "ETH/USDT" else 0
        
        # Hora del día (para detectar patrones intradía)
        try:
            fecha = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
            hora = fecha.hour
            mes = fecha.month
            anio = fecha.year
            es_ny_night = 1 if hora in [0,1,2,3,4,5,21,22,23] else 0  # NY night
        except:
            hora = 12
            mes = 1
            anio = 2026
            es_ny_night = 0
        
        # Target: ganancia neta positiva
        total_venta = t["venta_total"]
        total_compra = t["cantidad"] * precio_entrada
        ganancia = total_venta - total_compra
        fee_est = total_compra * 0.00145  # 0.145% total fees
        ganancia_neta = ganancia - fee_est
        target = 1 if ganancia_neta > 0 else 0
        
        rows.append({
            # Features
            "rsi": rsi,
            "rsi_umbral": rsi_umbral,
            "rsi_distancia": rsi_distancia,
            "rsi_distancia_rel": rsi_distancia_rel,
            "volatilidad": volatilidad,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "tp_sl_ratio": tp_sl_ratio,
            "vol_tp_interac": vol_tp_interac,
            "spread_tp_sl": spread_tp_sl,
            "log_precio": log_precio,
            "monto": monto,
            "es_btc": es_btc,
            "es_eth": es_eth,
            "hora": hora,
            "mes": mes,
            "anio": anio,
            "es_ny_night": es_ny_night,
            # Target y metadata
            "ganancia_neta": round(ganancia_neta, 4),
            "target": target,
            "venta_motivo": t["venta_motivo"],
            "ts_entrada": t["ts"],
            "ts_venta": t["venta_ts"],
            "symbol": t.get("symbol"),
            "log_file": t.get("log_file", ""),
        })
    
    return pd.DataFrame(rows)


def limpiar_y_submuestrear(df):
    """Limpia outliers y hace submuestreo balanceado por mes."""
    if df.empty:
        return df
    
    print(f"  Trades antes de limpiar: {len(df)}")
    
    # 1. Eliminar duplicados exactos
    df = df.drop_duplicates(subset=["ts_entrada", "symbol"], keep="first")
    print(f"  Tras eliminar duplicados: {len(df)}")
    
    # 2. Eliminar outliers en ganancia_neta (más allá de 3 std)
    if len(df) > 10:
        mean_gan = df["ganancia_neta"].mean()
        std_gan = df["ganancia_neta"].std()
        if std_gan > 0:
            df = df[(df["ganancia_neta"] >= mean_gan - 3*std_gan) & 
                    (df["ganancia_neta"] <= mean_gan + 3*std_gan)]
            print(f"  Tras eliminar outliers (3σ): {len(df)}")
    
    # 3. Submuestreo por mes para balancear distribución temporal
    #    (evitar que un mes domine el dataset)
    df["year_month"] = df["anio"].astype(str) + "-" + df["mes"].astype(str).str.zfill(2)
    
    meses = df["year_month"].unique()
    trades_por_mes = {}
    for mes in meses:
        sub = df[df["year_month"] == mes]
        # Máximo 50 trades por mes para balancear
        if len(sub) > 50:
            sub = sub.sample(n=50, random_state=42)
        trades_por_mes[mes] = sub
        print(f"    {mes}: {len(sub)} trades")
    
    df_balanceado = pd.concat(trades_por_mes.values(), ignore_index=True)
    print(f"  Tras submuestreo por mes: {len(df_balanceado)}")
    
    # 4. Verificar distribución de target
    pos = (df_balanceado["target"] == 1).sum()
    neg = (df_balanceado["target"] == 0).sum()
    print(f"  Distribución: {pos} POS ({pos/len(df_balanceado)*100:.1f}%), "
          f"{neg} NEG ({neg/len(df_balanceado)*100:.1f}%)")
    
    return df_balanceado


def guardar_train_test_split(df):
    """Guarda train/test split usando TimeSeriesSplit (no shuffle)."""
    from sklearn.model_selection import TimeSeriesSplit
    
    if len(df) < 20:
        print("[ERROR] Dataset muy pequeño para split.")
        return
    
    # Ordenar por tiempo
    df = df.sort_values("ts_entrada").reset_index(drop=True)
    
    # 80% train, 20% test (temporal, no shuffle)
    split_idx = int(len(df) * 0.8)
    train = df.iloc[:split_idx].copy()
    test = df.iloc[split_idx:].copy()
    
    print(f"\nTrain: {len(train)} trades ({train['target'].sum()} POS, {(train['target']==0).sum()} NEG)")
    print(f"Test:  {len(test)} trades ({test['target'].sum()} POS, {(test['target']==0).sum()} NEG)")
    
    # Guardar CSVs
    train_path = OUTPUT_DIR / "train.csv"
    test_path = OUTPUT_DIR / "test.csv"
    full_path = OUTPUT_DIR / "dataset_completo.csv"
    
    # Columnas para el modelo (sin metadata)
    feature_cols = [
        "rsi", "rsi_umbral", "rsi_distancia", "rsi_distancia_rel",
        "volatilidad", "tp_pct", "sl_pct", "tp_sl_ratio",
        "vol_tp_interac", "spread_tp_sl", "log_precio", "monto",
        "es_btc", "es_eth", "hora", "mes", "es_ny_night",
        "ganancia_neta", "target"
    ]
    
    train[feature_cols].to_csv(train_path, index=False, encoding="utf-8")
    test[feature_cols].to_csv(test_path, index=False, encoding="utf-8")
    df[feature_cols].to_csv(full_path, index=False, encoding="utf-8")
    
    print(f"\nGuardado:")
    print(f"  {train_path}")
    print(f"  {test_path}")
    print(f"  {full_path}")
    
    return train, test


def main():
    print("=== Generación de Dataset para XGBoost ===\n")
    
    print("[1/4] Extrayendo trades de logs...")
    trades = parsear_todos_los_logs()
    print(f"  ==> {len(trades)} trades completos extraídos.\n")
    
    if not trades:
        print("[ERROR] No se encontraron trades. Verificar logs.")
        return
    
    print("[2/4] Feature engineering...")
    df = feature_engineering(trades)
    print(f"  ==> {len(df)} filas, {len(df.columns)} columnas.\n")
    
    print("[3/4] Limpiando y submuestreando por mes...")
    df_limpio = limpiar_y_submuestrear(df)
    print()
    
    if len(df_limpio) < 20:
        print("[ERROR] Dataset muy pequeño después de limpiar.")
        return
    
    print("[4/4] Guardando train/test split (TimeSeriesSplit)...")
    train, test = guardar_train_test_split(df_limpio)
    
    print("\n=== Listo ===")
    print(f"Dataset balanceado temporalmente, sin lookahead bias.")
    print(f"Usar TimeSeriesSplit con 5 folds para validación cruzada.")


if __name__ == "__main__":
    main()