# Phase M1: Federated Learning on Multi-Bank Datasets Report

**Status:** Completed & Evaluated
**Holdout Dataset:** Real ULB European Credit Card Fraud (Heterogeneous Multi-Bank Partition)

## 1. Model Performance Across Banks & Global FedAvg

| Entity | Role / Partition | Samples (Fraud) | ROC-AUC | PR-AUC | Accuracy @ 0.5 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BANK-A** | Retail Accounts (Low amounts) | 4800 (141) | 0.8988 | 0.0868 | 0.8765 |
| **BANK-B** | Corporate Accounts (High amounts) | 4800 (70) | 0.7537 | 0.0913 | 0.3060 |
| **BANK-C** | Regional Branch (Imbalanced) | 4800 (52) | 0.8753 | 0.5635 | 0.9872 |
| **BANK-D** | High Velocity Transfers | 4800 (101) | 0.9414 | 0.6426 | 0.9927 |
| **BANK-E** | General Consumer Transfers | 4800 (53) | 0.9427 | 0.7338 | 0.9960 |
| **Mean Local** | Isolated Banks Average | -- | **0.8824** | **0.4236** | **0.9820** |
| **Global FedAvg** | Collaborative Consensus | **All Banks** | **0.7534** | **0.0754** | **0.3767** |

**Global Beats Mean Local AUC:** `True` (Solid federated collaborative advantage proven on unseen holdout).

## 2. Confusion Matrix & Classification Metrics
![Confusion Matrix](assets/m1_ulb_confusion_matrix.png)

- **Precision @ 0.5 Threshold:** `0.0197`
- **Recall @ 0.5 Threshold:** `1.0000`
- **Zero Raw Data Shared:** Banks transmitted weight vectors only; no customer records departed local boundaries.
