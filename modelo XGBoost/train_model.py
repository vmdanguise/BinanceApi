import os, sys, warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    confusion_matrix, classification_report, roc_auc_score
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

BASE = Path(__file__).parent
DATASET_DIR = BASE / "dataset"
REPORTE_PATH = BASE / "reporte_modelo.md"

FEATURE_COLS = [
    "rsi", "rsi_umbral", "rsi_distancia", "rsi_distancia_rel",
    "volatilidad", "tp_pct", "sl_pct", "tp_sl_ratio",
    "vol_tp_interac", "spread_tp_sl", "log_precio", "monto",
    "es_btc", "es_eth", "hora", "mes", "es_ny_night",
]

def cargar_datos():
    print("[1/4] Cargando dataset...")
    train_path = DATASET_DIR / "train.csv"
    test_path = DATASET_DIR / "test.csv"
    full_path = DATASET_DIR / "dataset_completo.csv"

    if train_path.exists() and test_path.exists():
        train = pd.read_csv(train_path)
        test = pd.read_csv(test_path)
        print(f"  Train: {len(train)} ({train['target'].sum()} POS, {(train['target']==0).sum()} NEG)")
        print(f"  Test:  {len(test)} ({test['target'].sum()} POS, {(test['target']==0).sum()} NEG)")
        return train, test

    if full_path.exists():
        df = pd.read_csv(full_path)
        split = int(len(df) * 0.8)
        train = df.iloc[:split].copy()
        test = df.iloc[split:].copy()
        print(f"  Usando split 80/20: Train={len(train)}, Test={len(test)}")
        return train, test

    print("[ERROR] No se encontraron CSVs en", DATASET_DIR)
    return None, None


def entrenar(train, test):
    print("\n[2/4] Entrenando XGBoost...")
    X_train = train[FEATURE_COLS]
    y_train = train["target"]
    X_test = test[FEATURE_COLS]
    y_test = test["target"]

    n_pos = y_train.sum()
    n_neg = (y_train == 0).sum()
    scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0
    print(f"  Train: {n_pos} POS, {n_neg} NEG, scale_pos_weight={scale_pos_weight:.2f}")

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=3,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        eval_metric="logloss",
        early_stopping_rounds=20,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_train, y_train), (X_test, y_test)],
        verbose=False,
    )

    y_pred_train = model.predict(X_train)
    y_proba_train = model.predict_proba(X_train)[:, 1]
    y_pred_test = model.predict(X_test)
    y_proba_test = model.predict_proba(X_test)[:, 1]

    metrics = {
        "train": {
            "accuracy": accuracy_score(y_train, y_pred_train),
            "precision": precision_score(y_train, y_pred_train, zero_division=0),
            "recall": recall_score(y_train, y_pred_train, zero_division=0),
            "f1": f1_score(y_train, y_pred_train, zero_division=0),
            "roc_auc": roc_auc_score(y_train, y_proba_train) if len(np.unique(y_train)) > 1 else 0,
            "cm": confusion_matrix(y_train, y_pred_train).tolist(),
            "cr": classification_report(y_train, y_pred_train, output_dict=True, zero_division=0),
        },
        "test": {
            "accuracy": accuracy_score(y_test, y_pred_test),
            "precision": precision_score(y_test, y_pred_test, zero_division=0),
            "recall": recall_score(y_test, y_pred_test, zero_division=0),
            "f1": f1_score(y_test, y_pred_test, zero_division=0),
            "roc_auc": roc_auc_score(y_test, y_proba_test) if len(np.unique(y_test)) > 1 else 0,
            "cm": confusion_matrix(y_test, y_pred_test).tolist(),
            "cr": classification_report(y_test, y_pred_test, output_dict=True, zero_division=0),
        },
        "n_train": len(X_train), "n_test": len(X_test),
        "n_pos_train": int(n_pos), "n_neg_train": int(n_neg),
        "n_pos_test": int(y_test.sum()), "n_neg_test": int((y_test == 0).sum()),
        "feature_importance": dict(zip(FEATURE_COLS, model.feature_importances_)),
    }

    n_test_correct = int((y_pred_test == y_test).sum())
    n_test_errors = int((y_pred_test != y_test).sum())
    print(f"  Test Accuracy: {metrics['test']['accuracy']:.4f} ({n_test_correct}/{len(X_test)} correctos, {n_test_errors} errores)")
    print(f"  Precision: {metrics['test']['precision']:.4f}, Recall: {metrics['test']['recall']:.4f}, F1: {metrics['test']['f1']:.4f}")
    if metrics['test']['roc_auc']:
        print(f"  ROC AUC: {metrics['test']['roc_auc']:.4f}")

    return model, metrics


def generar_reporte(model, metrics, train, test):
    print("\n[3/4] Generando reporte...")
    fi = metrics["feature_importance"]
    sorted_fi = sorted(fi.items(), key=lambda x: x[1], reverse=True)

    lines = []
    lines.append("# Reporte del Modelo XGBoost\n")
    lines.append(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    lines.append("## Resumen del Dataset\n")
    lines.append(f"- Train: **{metrics['n_train']}** ({metrics['n_pos_train']} POS, {metrics['n_neg_train']} NEG)")
    lines.append(f"- Test:  **{metrics['n_test']}** ({metrics['n_pos_test']} POS, {metrics['n_neg_test']} NEG)")
    lines.append(f"- Features: {len(FEATURE_COLS)}\n")

    for subset in ["train", "test"]:
        m = metrics[subset]
        cm = m["cm"]
        lines.append(f"## Rendimiento en {subset.upper()}\n")
        lines.append("| Metrica       | Valor    |")
        lines.append("|--------------|----------|")
        lines.append(f"| Accuracy      | {m['accuracy']:.4f} |")
        lines.append(f"| Precision     | {m['precision']:.4f} |")
        lines.append(f"| Recall        | {m['recall']:.4f} |")
        lines.append(f"| F1-score      | {m['f1']:.4f} |")
        lines.append(f"| ROC AUC       | {m['roc_auc']:.4f} |\n")
        lines.append("### Matriz de Confusion\n")
        lines.append("|                | Predicho Neg | Predicho Pos |")
        lines.append("|----------------|--------------|--------------|")
        lines.append(f"| Real Neg       | {cm[0][0]}            | {cm[0][1]}            |")
        lines.append(f"| Real Pos       | {cm[1][0]}            | {cm[1][1]}            |\n")

        cr = m["cr"]
        lines.append("### Classification Report\n")
        lines.append("| Clase | Precision | Recall | F1 | Support |")
        lines.append("|-------|-----------|--------|----|---------|")
        for cls_name in ("0", "1"):
            c = cr.get(cls_name, {})
            lines.append(f"| {cls_name}     | {c.get('precision', 0):.4f}    | {c.get('recall', 0):.4f} | {c.get('f1', 0):.4f} | {c.get('support', 0):.0f}       |")
        lines.append(f"| macro avg | {cr.get('macro avg', {}).get('precision', 0):.4f} | {cr.get('macro avg', {}).get('recall', 0):.4f} | {cr.get('macro avg', {}).get('f1', 0):.4f} | {cr.get('macro avg', {}).get('support', 0):.0f} |")
        lines.append(f"| weighted avg | {cr.get('weighted avg', {}).get('precision', 0):.4f} | {cr.get('weighted avg', {}).get('recall', 0):.4f} | {cr.get('weighted avg', {}).get('f1', 0):.4f} | {cr.get('weighted avg', {}).get('support', 0):.0f} |\n")

    lines.append("## Feature Importance\n")
    lines.append("| Feature           | Importance |")
    lines.append("|-------------------|-----------|")
    for name, imp in sorted_fi:
        lines.append(f"| {name:<17} | {imp:.4f}     |")

    if sum(fi.values()) > 0:
        top5 = sorted_fi[:5]
        lines.append("\n### Top 5 Features\n")
        for rank, (name, imp) in enumerate(top5, 1):
            lines.append(f"{rank}. **{name}** ({imp:.4f})")
        lines.append("")

    lines.append("\n## Muestras Mal Clasificadas en TEST\n")
    test = test.copy()
    test["pred"] = model.predict(test[FEATURE_COLS])
    errors = test[test["pred"] != test["target"]]
    if len(errors):
        lines.append(f"Total: {len(errors)} errores de {len(test)} muestras\n")
        for _, row in errors.iterrows():
            lines.append(f"- RSI={row['rsi']:.1f} Vol={row['volatilidad']:.1f}% TP={row['tp_pct']:.1f}% "
                         f"Real={'POS' if row['target'] else 'NEG'} Pred={'POS' if row['pred'] else 'NEG'} "
                         f"Ganancia=${row['ganancia_neta']:+.2f}")
    else:
        lines.append("(ninguna)\n")

    texto = "\n".join(lines)
    with open(REPORTE_PATH, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"  Reporte guardado en: {REPORTE_PATH}")


def guardar_modelo(model):
    import pickle
    MODEL_PATH = BASE / "model_rsi40btc_rsi50eth.pkl"
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    print(f"\nModelo guardado: {MODEL_PATH}")


def main():
    print("=== Pipeline XGBoost ===\n")

    train, test = cargar_datos()
    if train is None:
        return

    model, metrics = entrenar(train, test)
    generar_reporte(model, metrics, train, test)
    guardar_modelo(model)

    print("\n[4/4] Feature Importance Top 5:")
    fi = metrics["feature_importance"]
    for name, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {name:<20} {imp:.4f}")

    print(f"\nReporte completo: {REPORTE_PATH}")


if __name__ == "__main__":
    main()
