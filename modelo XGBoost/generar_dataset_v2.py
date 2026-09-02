# -*- coding: utf-8 -*-
"""
Genera dataset limpio para entrenar modelo XGBoost.
Extrae trades de logs reales + sintéticos emparejando COMPRA->VENTA consecutivas.
"""

import os
import re
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

# ──────────────────────── CONFIG ────────────────────────

BASE = Path(r"D:\BinanceApi")
LOG_DIR = BASE / "logs"
OUTPUT_DIR = BASE / "modelo XGBoost" / "dataset"
OUTPUT_DIR.mkdir(exist_ok=True)

# ──────────────────────── PARSER SIMPLIFICADO ────────────────────────

def extraer_trades_de_log(ruta_log):
    """Extrae trades completos de un solo archivo log."""
    trades = []
    
    with open(ruta_log, 'r', encoding='utf-8') as f:
        contenido = f.read()
    
    lineas = contenido.split('\n')
    
    # Estado actual
    compra_actual = None
    
    for i, linea in enumerate(lineas):
        # ── COMPRA OK ──
        if '[COMPRA OK]' in linea:
            # Extraer datos de compra
            m = re.search(
                r'\[COMPRA OK\] (?P<sym>\w+/USDT) \| '
                r'(?P<cantidad>[\de.-]+) unidades \((?P<monto>[\$0-9.]+)\) \| '
                r'SL=\$(?P<sl>[\d.]+) TP=\$(?P<tp>[\d.]+)',
                linea
            )
            if m:
                # Buscar señal previa (en las últimas 20 líneas)
                senal = None
                for j in range(max(0, i-20), i):
                    if '[SENAL COMPRA]' in lineas[j] and m.group('sym') in lineas[j]:
                        senal = lineas[j]
                        break
                
                # Extraer datos de la señal o usar defaults
                if senal:
                    m_senal = re.search(
                        r'\[SENAL COMPRA\] (?P<sym>\w+/USDT) \| '
                        r'RSI=(?P<rsi>[\d.]+) \(umbral (?P<rsi_umbral>[\d.]+)\) \| '
                        r'volatilidad 7d=(?P<vol>[\d.]+)% \| '
                        r'TP=(?P<tp_pct>[\d.]+)% \| precio=\$(?P<precio>[\d.]+)',
                        senal
                    )
                    if m_senal:
                        rsi = float(m_senal.group('rsi'))
                        rsi_umbral = float(m_senal.group('rsi_umbral'))
                        volatilidad = float(m_senal.group('vol'))
                        tp_pct = float(m_senal.group('tp_pct'))
                        precio_compra = float(m_senal.group('precio'))
                    else:
                        rsi, rsi_umbral, volatilidad, tp_pct, precio_compra = 30.0, 30.0, 3.0, 2.0, 0.0
                else:
                    # Sin señal - usar defaults
                    rsi, rsi_umbral, volatilidad, tp_pct = 30.0, 30.0, 3.0, 2.0
                    # Calcular precio de compra desde monto/cantidad
                    cantidad = float(m.group('cantidad'))
                    monto = float(m.group('monto').replace('$', ''))
                    precio_compra = monto / cantidad if cantidad > 0 else 0.0
                
                compra_actual = {
                    'ts_compra': linea.split(']')[0].strip('['),
                    'symbol': m.group('sym'),
                    'cantidad': float(m.group('cantidad')),
                    'monto': float(m.group('monto').replace('$', '')),
                    'sl': float(m.group('sl')),
                    'tp': float(m.group('tp')),
                    'rsi': rsi,
                    'rsi_umbral': rsi_umbral,
                    'volatilidad': volatilidad,
                    'tp_pct': tp_pct,
                    'precio_compra': precio_compra,
                    'log_file': os.path.basename(ruta_log),
                }
        
        # ── VENTA ──
        elif '[VENTA-' in linea and compra_actual:
            m = re.search(
                r'\[VENTA-(?P<motivo>[^\]]+)\] (?P<sym>\w+/USDT): '
                r'(?P<cantidad>[\de.-]+) .+ @ \$(?P<precio_venta>[\d.]+) = \$(?P<total>[\d.]+)',
                linea
            )
            if m and m.group('sym') == compra_actual['symbol']:
                # Verificar que cantidad coincida (±20%)
                cant_compra = compra_actual['cantidad']
                cant_venta = float(m.group('cantidad'))
                if abs(cant_venta - cant_compra) / max(cant_compra, 0.0001) < 0.20:
                    ts_venta = linea.split(']')[0].strip('[')
                    
                    # Calcular ganancia
                    total_venta = float(m.group('total'))
                    fees_est = compra_actual['monto'] * 0.00145  # 0.145%
                    ganancia_neta = total_venta - compra_actual['monto'] - fees_est
                    target = 1 if ganancia_neta > 0 else 0
                    
                    trades.append({
                        **compra_actual,
                        'ts_venta': ts_venta,
                        'venta_motivo': m.group('motivo'),
                        'precio_venta': float(m.group('precio_venta')),
                        'total_venta': total_venta,
                        'ganancia_neta': round(ganancia_neta, 4),
                        'target': target,
                    })
                    
                    compra_actual = None  # Reset
    
    return trades


def extraer_todos_los_trades():
    """Extrae trades de todos los logs."""
    todos_los_trades = []
    
    logs = sorted([
        f for f in os.listdir(LOG_DIR)
        if (f.startswith('bot_trading_') or f.startswith('sintetic_bot_trading_')) and f.endswith('.log')
    ])
    
    print(f"  Procesando {len(logs)} archivos log...")
    
    for logfile in logs:
        ruta = LOG_DIR / logfile
        trades = extraer_trades_de_log(ruta)
        todos_los_trades.extend(trades)
    
    return todos_los_trades


# ──────────────────────── FEATURE ENGINEERING ────────────────────────

def calcular_features(df):
    """Agrega features derivadas."""
    if df.empty:
        return df
    
    # RSI distancia
    df['rsi_distancia'] = df['rsi_umbral'] - df['rsi']
    df['rsi_distancia_rel'] = df['rsi_distancia'] / df['rsi_umbral']
    
    # SL %
    df['sl_pct'] = (1 - df['sl'] / df['precio_compra']) * 100
    
    # TP/SL ratio
    df['tp_sl_ratio'] = df['tp_pct'] / df['sl_pct']
    df['tp_sl_ratio'] = df['tp_sl_ratio'].replace([np.inf, -np.inf], 999)
    
    # Interacción volatilidad * TP
    df['vol_tp_interac'] = df['volatilidad'] * df['tp_pct']
    
    # Spread TP-SL
    df['spread_tp_sl'] = (df['tp'] - df['sl']) / df['precio_compra'] * 100
    
    # Log precio
    df['log_precio'] = np.log(df['precio_compra'])
    
    # One-hot para símbolo
    df['es_btc'] = (df['symbol'] == 'BTC/USDT').astype(int)
    df['es_eth'] = (df['symbol'] == 'ETH/USDT').astype(int)
    
    # Hora y mes
    df['hora'] = pd.to_datetime(df['ts_compra']).dt.hour
    df['mes'] = pd.to_datetime(df['ts_compra']).dt.month
    df['anio'] = pd.to_datetime(df['ts_compra']).dt.year
    
    # NY night (0-5am, 9pm-11pm)
    df['es_ny_night'] = df['hora'].apply(lambda h: 1 if h in [0,1,2,3,4,5,21,22,23] else 0)
    
    return df


def limpiar_y_balancear(df):
    """Limpia outliers y balancea por mes."""
    if df.empty:
        return df
    
    print(f"  Trades antes de limpiar: {len(df)}")
    
    # Eliminar duplicados
    df = df.drop_duplicates(subset=['ts_compra', 'symbol'], keep='first')
    print(f"  Tras eliminar duplicados: {len(df)}")
    
    # Eliminar outliers en ganancia (3σ)
    if len(df) > 20:
        mean_gan = df['ganancia_neta'].mean()
        std_gan = df['ganancia_neta'].std()
        if std_gan > 0:
            df = df[(df['ganancia_neta'] >= mean_gan - 3*std_gan) & 
                    (df['ganancia_neta'] <= mean_gan + 3*std_gan)]
            print(f"  Tras eliminar outliers (3σ): {len(df)}")
    
    # Submuestreo por mes (max 50 trades por mes)
    df['year_month'] = df['anio'].astype(str) + '-' + df['mes'].astype(str).str.zfill(2)
    
    meses = df['year_month'].unique()
    trades_por_mes = {}
    for mes in sorted(meses):
        sub = df[df['year_month'] == mes]
        if len(sub) > 50:
            sub = sub.sample(n=50, random_state=42)
        trades_por_mes[mes] = sub
        print(f"    {mes}: {len(sub)} trades")
    
    df_balanceado = pd.concat(trades_por_mes.values(), ignore_index=True)
    print(f"  Tras submuestreo por mes: {len(df_balanceado)}")
    
    # Distribución de target
    pos = (df_balanceado['target'] == 1).sum()
    neg = (df_balanceado['target'] == 0).sum()
    pct_pos = pos / len(df_balanceado) * 100 if len(df_balanceado) > 0 else 0
    print(f"  Distribución: {pos} POS ({pct_pos:.1f}%), {neg} NEG ({100-pct_pos:.1f}%)")
    
    return df_balanceado


def guardar_train_test_temporal(df):
    """Guarda train/test split temporal (80/20)."""
    if len(df) < 20:
        print("[ERROR] Dataset muy pequeño.")
        return None, None
    
    # Ordenar por tiempo
    df = df.sort_values('ts_compra').reset_index(drop=True)
    
    # 80% train, 20% test
    split_idx = int(len(df) * 0.8)
    train = df.iloc[:split_idx].copy()
    test = df.iloc[split_idx:].copy()
    
    print(f"\nTrain: {len(train)} trades ({train['target'].sum()} POS, {(train['target']==0).sum()} NEG)")
    print(f"Test:  {len(test)} trades ({test['target'].sum()} POS, {(test['target']==0).sum()} NEG)")
    
    # Columnas finales
    feature_cols = [
        'rsi', 'rsi_umbral', 'rsi_distancia', 'rsi_distancia_rel',
        'volatilidad', 'tp_pct', 'sl_pct', 'tp_sl_ratio',
        'vol_tp_interac', 'spread_tp_sl', 'log_precio', 'monto',
        'es_btc', 'es_eth', 'hora', 'mes', 'es_ny_night',
        'ganancia_neta', 'target'
    ]
    
    train_path = OUTPUT_DIR / 'train.csv'
    test_path = OUTPUT_DIR / 'test.csv'
    full_path = OUTPUT_DIR / 'dataset_completo.csv'
    
    train[feature_cols].to_csv(train_path, index=False, encoding='utf-8-sig')
    test[feature_cols].to_csv(test_path, index=False, encoding='utf-8-sig')
    df[feature_cols].to_csv(full_path, index=False, encoding='utf-8-sig')
    
    print(f"\nGuardado:")
    print(f"  {train_path}")
    print(f"  {test_path}")
    print(f"  {full_path}")
    
    return train, test


def main():
    print("=== Generación de Dataset para XGBoost ===\n")
    
    print("[1/4] Extrayendo trades de logs...")
    trades = extraer_todos_los_trades()
    print(f"  ==> {len(trades)} trades completos extraídos.\n")
    
    if not trades:
        print("[ERROR] No se encontraron trades. Verificar logs.")
        return
    
    print("[2/4] Feature engineering...")
    df = pd.DataFrame(trades)
    df = calcular_features(df)
    print(f"  ==> {len(df)} filas, {len(df.columns)} columnas.\n")
    
    print("[3/4] Limpiando y balanceando por mes...")
    df_limpio = limpiar_y_balancear(df)
    print()
    
    if len(df_limpio) < 20:
        print("[ERROR] Dataset muy pequeño después de limpiar.")
        return
    
    print("[4/4] Guardando train/test split...")
    train, test = guardar_train_test_temporal(df_limpio)
    
    print("\n=== Listo ===")
    print(f"Dataset balanceado temporalmente.")
    print(f"Recomendación: Usar TimeSeriesSplit con 5 folds para validación.")


if __name__ == "__main__":
    main()