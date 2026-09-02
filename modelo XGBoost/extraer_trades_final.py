import os, re
from pathlib import Path
import pandas as pd
import numpy as np

LOG_DIR = Path(r"D:\BinanceApi\logs")
OUTPUT_DIR = Path(r"D:\BinanceApi\modelo XGBoost\dataset")
OUTPUT_DIR.mkdir(exist_ok=True)

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

RE_BALANCE = re.compile(
    r"\[BALANCE\] (?P<sym>\w+/USDT): PnL (?P<pnl>[+-]?[\d.]+)"
)


def extraer_ts(linea):
    m = RE_TS.search(linea)
    return m.group("ts") if m else None


def extraer_trades():
    logs = sorted([
        f for f in os.listdir(LOG_DIR)
        if (f.startswith("bot_trading_") or f.startswith("sintetic_bot_trading_")) and f.endswith(".log")
    ])
    print(f"Procesando {len(logs)} archivos...")

    eventos = {"BTC/USDT": [], "ETH/USDT": []}
    for logfile in logs:
        ruta = LOG_DIR / logfile
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    ts = extraer_ts(linea)
                    if not ts:
                        continue

                    m = RE_SENAL.search(linea)
                    if m:
                        sym = m.group("sym")
                        eventos[sym].append(("senal", ts, {
                            "rsi": float(m.group("rsi")),
                            "rsi_umbral": float(m.group("rsi_umbral")),
                            "vol": float(m.group("vol")),
                            "tp_pct": float(m.group("tp_pct")),
                            "precio": float(m.group("precio")),
                            "monto": float(m.group("monto")),
                        }))
                        continue

                    m = RE_COMPRA_OK.search(linea)
                    if m:
                        sym = m.group("sym")
                        eventos[sym].append(("compra", ts, {
                            "sym": m.group("sym"),
                            "cantidad": float(m.group("cant")),
                            "monto": float(m.group("monto")),
                            "sl": float(m.group("sl")),
                            "tp": float(m.group("tp")),
                        }))
                        continue

                    m = RE_VENTA.search(linea)
                    if m:
                        sym = m.group("sym")
                        eventos[sym].append(("venta", ts, {
                            "motivo": m.group("motivo"),
                        }))
                        continue

                    m = RE_BALANCE.search(linea)
                    if m:
                        sym = m.group("sym")
                        eventos[sym].append(("balance", ts, {
                            "pnl": float(m.group("pnl")),
                        }))
                        continue
        except Exception as e:
            print(f"  Error en {logfile}: {e}")

    trades = []
    for sym in ["BTC/USDT", "ETH/USDT"]:
        evts = eventos[sym]
        i = 0
        while i < len(evts):
            t1, ts1, d1 = evts[i]
            if t1 == "senal":
                senal = d1
                for j in range(i + 1, min(i + 50, len(evts))):
                    t2, ts2, d2 = evts[j]
                    if t2 == "compra":
                        compra = d2
                        for k in range(j + 1, min(j + 50, len(evts))):
                            t3, ts3, d3 = evts[k]
                            if t3 == "venta":
                                venta = d3
                                for l in range(k + 1, min(k + 20, len(evts))):
                                    t4, ts4, d4 = evts[l]
                                    if t4 == "balance":
                                        pnl = d4["pnl"]
                                        trades.append({
                                            "ts_compra": ts2,
                                            "ts_venta": ts3,
                                            "symbol": sym,
                                            "monto": compra["monto"],
                                            "sl": compra["sl"],
                                            "tp": compra["tp"],
                                            "rsi": senal["rsi"],
                                            "rsi_umbral": senal["rsi_umbral"],
                                            "volatilidad": senal["vol"],
                                            "tp_pct": senal["tp_pct"],
                                            "precio_compra": senal["precio"],
                                            "venta_motivo": venta["motivo"],
                                            "ganancia_neta": round(pnl, 4),
                                            "target": 1 if pnl > 0 else 0,
                                        })
                                        i = l + 1
                                        break
                                break
                        break
                else:
                    i += 1
                    continue
            else:
                i += 1

    return trades


def main():
    print("=== Extrayendo trades finales ===\n")
    trades = extraer_trades()
    print(f"\nTrades extraídos: {len(trades)}")
    if not trades:
        print("ERROR: No se encontraron trades.")
        return

    df = pd.DataFrame(trades)
    pos = (df["target"] == 1).sum()
    neg = (df["target"] == 0).sum()
    print(f"Distribución: {pos} POS ({pos / len(df) * 100:.1f}%), {neg} NEG ({neg / len(df) * 100:.1f}%)")

    df["rsi_distancia"] = df["rsi_umbral"] - df["rsi"]
    df["rsi_distancia_rel"] = df["rsi_distancia"] / df["rsi_umbral"].clip(lower=0.01)
    sl_pct_raw = (1 - df["sl"] / df["tp"]).abs() * 100
    df["sl_pct"] = sl_pct_raw
    df["tp_sl_ratio"] = df["tp_pct"] / df["sl_pct"].clip(lower=0.01)
    df["vol_tp_interac"] = df["volatilidad"] * df["tp_pct"]
    df["spread_tp_sl"] = (df["tp"] - df["sl"]) / df["precio_compra"] * 100
    df["log_precio"] = np.log(df["precio_compra"].clip(lower=1))
    df["es_btc"] = (df["symbol"] == "BTC/USDT").astype(int)
    df["es_eth"] = (df["symbol"] == "ETH/USDT").astype(int)
    df["hora"] = pd.to_datetime(df["ts_compra"]).dt.hour
    df["mes"] = pd.to_datetime(df["ts_compra"]).dt.month
    df["es_ny_night"] = df["hora"].apply(lambda h: 1 if h in [0, 1, 2, 3, 4, 5, 21, 22, 23] else 0)

    if len(df) > 20:
        q1, q3 = df["ganancia_neta"].quantile([0.25, 0.75])
        iqr = q3 - q1
        df = df[(df["ganancia_neta"] >= q1 - 1.5 * iqr) & (df["ganancia_neta"] <= q3 + 1.5 * iqr)]
        print(f"Tras eliminar outliers: {len(df)} trades")

    df["year_month"] = pd.to_datetime(df["ts_compra"]).dt.to_period("M")
    mes_count = df["year_month"].value_counts()
    print(f"Meses con trades: {len(mes_count)} (min={mes_count.min()}, max={mes_count.max()})")

    df = df.sort_values("ts_compra").reset_index(drop=True)
    total = len(df)
    print(f"\nTotal trades finales: {total}")

    split_idx = int(total * 0.8)
    train = df.iloc[:split_idx]
    test = df.iloc[split_idx:]

    print(f"Train: {len(train)} ({(train['target'] == 1).sum()} POS, {(train['target'] == 0).sum()} NEG)")
    print(f"Test:  {len(test)} ({(test['target'] == 1).sum()} POS, {(test['target'] == 0).sum()} NEG)")

    feature_cols = [
        "rsi", "rsi_umbral", "rsi_distancia", "rsi_distancia_rel",
        "volatilidad", "tp_pct", "sl_pct", "tp_sl_ratio",
        "vol_tp_interac", "spread_tp_sl", "log_precio", "monto",
        "es_btc", "es_eth", "hora", "mes", "es_ny_night",
        "ganancia_neta", "target",
    ]

    train[feature_cols].to_csv(OUTPUT_DIR / "train.csv", index=False)
    test[feature_cols].to_csv(OUTPUT_DIR / "test.csv", index=False)
    df[feature_cols].to_csv(OUTPUT_DIR / "dataset_completo.csv", index=False)

    print(f"\nGuardado en {OUTPUT_DIR}")
    print(f"  train.csv ({len(train)} filas)")
    print(f"  test.csv ({len(test)} filas)")
    print(f"  dataset_completo.csv ({len(df)} filas)")

    print("\n=== Resumen de trades ===")
    for _, row in df.iterrows():
        signo = "+" if row["ganancia_neta"] >= 0 else ""
        print(f"  {row['ts_compra']} {row['symbol']} | "
              f"RSI={row['rsi']:.1f} Vol={row['volatilidad']:.2f}% TP={row['tp_pct']:.2f}% | "
              f"${signo}{row['ganancia_neta']:.2f} ({'OK' if row['target'] else 'NO'}) | "
              f"{row['venta_motivo']}")


if __name__ == "__main__":
    main()
