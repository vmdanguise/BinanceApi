# -*- coding: utf-8 -*-
"""
Entrena modelo POS/NEG con features completas y busca threshold optimo
que maximice PnL neto en validacion.
"""
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import roc_auc_score, confusion_matrix
from pathlib import Path

BASE = Path(r"D:\BinanceApi\modelo XGBoost")
DATASET_DIR = BASE / "dataset"

FEATURE_COLS = [
    "rsi", "rsi_umbral", "rsi_distancia", "rsi_distancia_rel",
    "volatilidad", "tp_pct", "sl_pct", "tp_sl_ratio",
    "vol_tp_interac", "spread_tp_sl", "log_precio", "monto",
    "es_btc", "es_eth", "hora", "mes", "es_ny_night",
]

df = pd.read_csv(DATASET_DIR / "dataset_completo.csv")

print(f"Trades: {len(df)} | POS: {(df['target']==1).sum()} | NEG: {(df['target']==0).sum()}")

split1 = int(len(df) * 0.7)
split2 = int(len(df) * 0.85)
train = df.iloc[:split1]
val = df.iloc[split1:split2]
test = df.iloc[split2:]

X_train, y_train = train[FEATURE_COLS], train["target"]
X_val, y_val = val[FEATURE_COLS], val["target"]
X_test, y_test = test[FEATURE_COLS], test["target"]

print(f"Train: {len(train)} | Val: {len(val)} | Test: {len(test)}")
print(f"Train POS: {y_train.sum()} NEG: {(y_train==0).sum()}")

scale_pos_weight_v = (y_train == 0).sum() / y_train.sum() if y_train.sum() > 0 else 1
print(f"scale_pos_weight: {scale_pos_weight_v:.2f}")

model = xgb.XGBClassifier(
    n_estimators=500, max_depth=4, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.8, random_state=42,
    eval_metric="logloss", min_child_weight=1, max_delta_step=10,
    scale_pos_weight=scale_pos_weight_v,
)

sample_weight = np.where(y_train == 0, 5.0, 1.0)

model.fit(X_train, y_train, sample_weight=sample_weight,
          eval_set=[(X_val, y_val)], verbose=False)

# Buscar threshold optimo por PnL en validacion
val_proba = model.predict_proba(X_val)[:, 1]
val_pnl = val["ganancia_neta"].values

print(f"\n=== Optimizando threshold en validacion ===")
mejor_th = 0.5
mejor_pnl = -999
for th in np.arange(0.05, 0.95, 0.025):
    pnl = val_pnl[val_proba >= th].sum()
    if pnl > mejor_pnl:
        mejor_pnl = pnl
        mejor_th = th

print(f"Threshold optimo: {mejor_th:.3f} (PnL=${mejor_pnl:.2f})")

# Test con threshold optimo
test_proba = model.predict_proba(X_test)[:, 1]
test_pred = (test_proba >= mejor_th).astype(int)
auc = roc_auc_score(y_test, test_proba)
cm = confusion_matrix(y_test, test_pred)
tn, fp, fn, tp = cm[0][0], cm[0][1], cm[1][0], cm[1][1]

print(f"\n=== TEST (threshold={mejor_th:.3f}) ===")
print(f"ROC AUC: {auc:.4f}")
print(f"Matriz: TN={tn} FP={fp} | FN={fn} TP={tp}")
print(f"Precision: {tp/(tp+fp):.3f}" if tp+fp>0 else f"Recall: {tp/(tp+fn):.3f}" if tp+fn>0 else "")

# Simular todos los thresholds en test
test_pnl = test["ganancia_neta"].values
print(f"\n=== FILTROS EN TEST ===")
for th in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]:
    mask = test_proba >= th
    if mask.sum() == 0: continue
    pnl = test_pnl[mask].sum()
    win = (test_pnl[mask] > 0).mean() * 100
    neg = (test_pnl[mask] < 0).sum()
    print(f"  >={th:.2f}: {mask.sum():3d} trades  PnL=${pnl:+.2f}  win%={win:.0f}%  NEG={neg}")

# Feature importance
fi = dict(zip(FEATURE_COLS, model.feature_importances_))
print(f"\nTop features (primer modelo):")
for name, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:10]:
    print(f"  {name:<20} {imp:.4f}")

# Guardar
model.save_model(str(BASE / "model_optimized.json"))
print(f"\nGuardado: model_optimized.json (threshold optimo={mejor_th:.3f})")
