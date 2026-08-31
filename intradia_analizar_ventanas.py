"""
intradia_analizar_ventanas.py — Encargo 2026-08-21: análisis por ventanas
móviles (20/50/100 días) del sleeve intradía. NO se considera "aprendizaje"
mejorar el último día -- se comparan ventanas y el modelo actual contra el
anterior (usando intradia_modelo_version.json).

Solo lectura de intradia_historial_señales.json / intradia_posiciones.json.
No modifica ningún parámetro -- eso requiere una decisión explícita
documentada en intradia_modelo_version.json, con evidencia acumulada, no
tras una sola mala sesión.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

POSICIONES_PATH = os.path.join(os.path.dirname(__file__), "intradia_posiciones.json")
HISTORIAL_SEÑALES_PATH = os.path.join(os.path.dirname(__file__), "intradia_historial_señales.json")
VENTANAS_DIAS = [20, 50, 100]


def cargar_json(path: str, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def metricas(pnls: np.ndarray) -> dict:
    if len(pnls) == 0:
        return {"n": 0}
    ganancias = pnls[pnls > 0].sum()
    perdidas = -pnls[pnls < 0].sum()
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak).min() if len(equity) else 0.0
    return {
        "n": len(pnls), "pnl_neto_eur": round(pnls.sum(), 2), "expectancy_eur": round(pnls.mean(), 3),
        "win_rate": round((pnls > 0).mean() * 100, 1),
        "profit_factor": round(ganancias / perdidas, 2) if perdidas > 0 else float("inf"),
        "max_drawdown_eur": round(dd, 2),
    }


def main() -> None:
    estado = cargar_json(POSICIONES_PATH, {"posiciones": []})
    cerradas = [p for p in estado["posiciones"] if p["estado"] == "cerrada"]
    if not cerradas:
        print("Sin operaciones cerradas todavía -- nada que analizar por ventanas.")
        return

    df = pd.DataFrame(cerradas)
    df["fecha_cierre"] = pd.to_datetime(df["fecha_cierre"])
    df = df.sort_values("fecha_cierre")
    df["fecha_dia"] = df["fecha_cierre"].dt.date

    dias_unicos = sorted(df["fecha_dia"].unique())
    print(f"Días de trading con operaciones cerradas: {len(dias_unicos)}")
    print(f"Operaciones cerradas totales: {len(df)}\n")

    for ventana in VENTANAS_DIAS:
        if len(dias_unicos) < ventana:
            print(f"--- Ventana {ventana} días: solo hay {len(dias_unicos)} días, insuficiente todavía ---\n")
            continue
        dias_ventana = set(dias_unicos[-ventana:])
        sub = df[df["fecha_dia"].isin(dias_ventana)]
        m = metricas(sub["pnl_neto_eur"].values)
        print(f"--- Ventana {ventana} días ({len(dias_ventana)} sesiones reales) ---")
        for k, v in m.items():
            print(f"  {k}: {v}")
        print()

    # comparación por versión de modelo
    print("--- Comparación por versión de modelo ---")
    for version, grupo in df.groupby("modelo_version"):
        m = metricas(grupo["pnl_neto_eur"].values)
        print(f"  {version}: {m}")

    # señales ejecutadas vs rechazadas (para el análisis "qué rechazamos y qué habría pasado")
    historial = cargar_json(HISTORIAL_SEÑALES_PATH, {"señales": []})
    señales = historial["señales"]
    if señales:
        ejecutadas = sum(1 for s in señales if s["decision"] == "ejecutada")
        rechazadas = sum(1 for s in señales if s["decision"] == "rechazada")
        print(f"\n--- Señales totales registradas: {len(señales)} (ejecutadas: {ejecutadas}, rechazadas: {rechazadas}) ---")
        if rechazadas:
            motivos = pd.Series([s["motivo_rechazo"] for s in señales if s["decision"] == "rechazada"]).value_counts()
            print(motivos.to_string())


if __name__ == "__main__":
    main()
