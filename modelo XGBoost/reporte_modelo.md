# Reporte del Modelo XGBoost

Generado: 2026-06-24 00:19:12

## Resumen del Dataset

- Train: **337** (291 POS, 46 NEG)
- Test:  **85** (69 POS, 16 NEG)
- Features: 17

## Rendimiento en TRAIN

| Metrica       | Valor    |
|--------------|----------|
| Accuracy      | 0.8724 |
| Precision     | 0.9921 |
| Recall        | 0.8591 |
| F1-score      | 0.9208 |
| ROC AUC       | 0.9753 |

### Matriz de Confusion

|                | Predicho Neg | Predicho Pos |
|----------------|--------------|--------------|
| Real Neg       | 44            | 2            |
| Real Pos       | 41            | 250            |

### Classification Report

| Clase | Precision | Recall | F1 | Support |
|-------|-----------|--------|----|---------|
| 0     | 0.5176    | 0.9565 | 0.0000 | 46       |
| 1     | 0.9921    | 0.8591 | 0.0000 | 291       |
| macro avg | 0.7549 | 0.9078 | 0.0000 | 337 |
| weighted avg | 0.9273 | 0.8724 | 0.0000 | 337 |

## Rendimiento en TEST

| Metrica       | Valor    |
|--------------|----------|
| Accuracy      | 0.7647 |
| Precision     | 0.8025 |
| Recall        | 0.9420 |
| F1-score      | 0.8667 |
| ROC AUC       | 0.6929 |

### Matriz de Confusion

|                | Predicho Neg | Predicho Pos |
|----------------|--------------|--------------|
| Real Neg       | 0            | 16            |
| Real Pos       | 4            | 65            |

### Classification Report

| Clase | Precision | Recall | F1 | Support |
|-------|-----------|--------|----|---------|
| 0     | 0.0000    | 0.0000 | 0.0000 | 16       |
| 1     | 0.8025    | 0.9420 | 0.0000 | 69       |
| macro avg | 0.4012 | 0.4710 | 0.0000 | 85 |
| weighted avg | 0.6514 | 0.7647 | 0.0000 | 85 |

## Feature Importance

| Feature           | Importance |
|-------------------|-----------|
| log_precio        | 0.0942     |
| rsi_distancia     | 0.0915     |
| rsi               | 0.0887     |
| rsi_distancia_rel | 0.0843     |
| tp_sl_ratio       | 0.0822     |
| mes               | 0.0802     |
| tp_pct            | 0.0704     |
| hora              | 0.0704     |
| spread_tp_sl      | 0.0700     |
| volatilidad       | 0.0686     |
| vol_tp_interac    | 0.0673     |
| sl_pct            | 0.0662     |
| es_ny_night       | 0.0661     |
| rsi_umbral        | 0.0000     |
| monto             | 0.0000     |
| es_btc            | 0.0000     |
| es_eth            | 0.0000     |

### Top 5 Features

1. **log_precio** (0.0942)
2. **rsi_distancia** (0.0915)
3. **rsi** (0.0887)
4. **rsi_distancia_rel** (0.0843)
5. **tp_sl_ratio** (0.0822)


## Muestras Mal Clasificadas en TEST

Total: 20 errores de 85 muestras

- RSI=25.0 Vol=2.9% TP=2.3% Real=NEG Pred=POS Ganancia=$-0.02
- RSI=15.3 Vol=3.2% TP=2.6% Real=NEG Pred=POS Ganancia=$-0.01
- RSI=19.6 Vol=2.1% TP=1.7% Real=POS Pred=NEG Ganancia=$+0.02
- RSI=18.8 Vol=2.2% TP=1.7% Real=POS Pred=NEG Ganancia=$+0.01
- RSI=21.2 Vol=3.1% TP=2.5% Real=NEG Pred=POS Ganancia=$-0.05
- RSI=13.5 Vol=3.0% TP=2.4% Real=NEG Pred=POS Ganancia=$-0.01
- RSI=15.7 Vol=3.6% TP=2.9% Real=NEG Pred=POS Ganancia=$-0.03
- RSI=38.9 Vol=3.2% TP=2.6% Real=NEG Pred=POS Ganancia=$-0.05
- RSI=39.2 Vol=3.7% TP=3.0% Real=NEG Pred=POS Ganancia=$-0.03
- RSI=17.1 Vol=2.3% TP=1.8% Real=POS Pred=NEG Ganancia=$+0.03
- RSI=30.1 Vol=4.0% TP=3.2% Real=POS Pred=NEG Ganancia=$+0.03
- RSI=28.0 Vol=3.8% TP=3.0% Real=NEG Pred=POS Ganancia=$-0.03
- RSI=28.1 Vol=4.6% TP=3.7% Real=NEG Pred=POS Ganancia=$-0.02
- RSI=27.0 Vol=4.5% TP=3.6% Real=NEG Pred=POS Ganancia=$-0.01
- RSI=31.7 Vol=4.3% TP=3.4% Real=NEG Pred=POS Ganancia=$-0.03
- RSI=32.9 Vol=8.3% TP=5.0% Real=NEG Pred=POS Ganancia=$-0.08
- RSI=32.3 Vol=8.1% TP=5.0% Real=NEG Pred=POS Ganancia=$-0.01
- RSI=36.6 Vol=7.5% TP=5.0% Real=NEG Pred=POS Ganancia=$-0.03
- RSI=33.8 Vol=7.8% TP=5.0% Real=NEG Pred=POS Ganancia=$-0.03
- RSI=34.6 Vol=4.6% TP=3.7% Real=NEG Pred=POS Ganancia=$-0.04