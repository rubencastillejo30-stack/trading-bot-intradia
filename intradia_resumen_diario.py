"""
intradia_resumen_diario.py — Encargo 2026-08-21 (v0.2): calcula, en un solo
JSON, todos los datos que pide el informe diario del sleeve intradía --
revisiones reales, oportunidades por ticker/estrategia, rechazadas y qué
pasó después, P&L del día, y evolución acumulada del modelo. Pensado para
que la rutina de Claude solo tenga que leer este JSON y redactar el email,
no calcular las cifras ella misma.

Solo lectura. No modifica ningún estado.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np

FEATURES_LOG_PATH = os.path.join(os.path.dirname(__file__), "intradia_features_log.json")
HISTORIAL_SEÑALES_PATH = os.path.join(os.path.dirname(__file__), "intradia_historial_señales.json")
POSICIONES_PATH = os.path.join(os.path.dirname(__file__), "intradia_posiciones.json")
INTRADIA_PATH = os.path.join(os.path.dirname(__file__), "data_live", "intradia.json")
MODELO_PATH = os.path.join(os.path.dirname(__file__), "intradia_modelo_version.json")


def cargar_json(path: str, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _fecha_de(iso: str) -> dt.date:
    return dt.datetime.fromisoformat(iso).date()


def main() -> None:
    hoy = dt.datetime.now(dt.timezone.utc).date()
    features_log = cargar_json(FEATURES_LOG_PATH, {"registros": []})["registros"]
    historial = cargar_json(HISTORIAL_SEÑALES_PATH, {"señales": []})["señales"]
    posiciones = cargar_json(POSICIONES_PATH, {"posiciones": []})["posiciones"]
    intradia = cargar_json(INTRADIA_PATH, {"tickers": {}})
    modelo = cargar_json(MODELO_PATH, {"version_actual": "?", "historial": []})

    # --- revisiones reales de hoy ---
    ts_hoy = sorted({r["timestamp"] for r in features_log if _fecha_de(r["timestamp"]) == hoy})
    n_revisiones = len(ts_hoy)
    if n_revisiones >= 2:
        deltas_min = [(dt.datetime.fromisoformat(ts_hoy[i]) - dt.datetime.fromisoformat(ts_hoy[i - 1])).total_seconds() / 60
                      for i in range(1, n_revisiones)]
        retraso_medio = round(sum(deltas_min) / len(deltas_min), 1)
        retraso_max = round(max(deltas_min), 1)
    else:
        retraso_medio = retraso_max = None

    # --- señales de hoy ---
    señales_hoy = [s for s in historial if _fecha_de(s["timestamp"]) == hoy]
    ejecutadas = [s for s in señales_hoy if s["decision"] == "ejecutada"]
    rechazadas = [s for s in señales_hoy if s["decision"] == "rechazada"]

    por_ticker: dict = {}
    por_estrategia: dict = {}
    for s in señales_hoy:
        por_ticker[s["ticker"]] = por_ticker.get(s["ticker"], 0) + 1
        eid = s["señal"]["estrategia_id"]
        por_estrategia[eid] = por_estrategia.get(eid, 0) + 1

    motivos_rechazo: dict = {}
    for s in rechazadas:
        m = s.get("motivo_rechazo", "desconocido")
        motivos_rechazo[m] = motivos_rechazo.get(m, 0) + 1

    # --- top 5 rechazadas + qué pasó después (comparado con el precio actual del ticker) ---
    top_rechazadas = []
    for s in sorted(rechazadas, key=lambda x: x["timestamp"], reverse=True)[:5]:
        ticker = s["ticker"]
        precio_entrada_hipotetico = s["señal"]["precio_entrada"]
        d = intradia.get("tickers", {}).get(ticker, {})
        precio_actual = d.get("precio_en_vivo")
        movimiento_pct = None
        if precio_actual and precio_entrada_hipotetico:
            direccion = s["señal"]["direccion"]
            signo = 1 if direccion == "LONG" else -1
            movimiento_pct = signo * (precio_actual / precio_entrada_hipotetico - 1) * 100
        top_rechazadas.append({
            "ticker": ticker, "hora": s["timestamp"], "estrategia": s["señal"]["estrategia_id"],
            "direccion": s["señal"]["direccion"], "motivo_rechazo": s.get("motivo_rechazo"),
            "precio_en_señal": precio_entrada_hipotetico, "precio_ahora": precio_actual,
            "movimiento_a_favor_pct": round(movimiento_pct, 2) if movimiento_pct is not None else None,
        })

    # --- operaciones de hoy ---
    posiciones_hoy = [p for p in posiciones if _fecha_de(p["fecha_entrada"]) == hoy]
    cerradas_hoy = [p for p in posiciones_hoy if p["estado"] == "cerrada"]
    ganadoras = [p for p in cerradas_hoy if p.get("resultado_r", 0) > 0]
    perdedoras = [p for p in cerradas_hoy if p.get("resultado_r", 0) <= 0]
    pnl_bruto_hoy = sum(p.get("pnl_bruto_eur", 0) for p in cerradas_hoy)
    costes_hoy = {f"{t}_ticks": sum(p.get("costes_eur", {}).get(f"{t}_ticks", 0) for p in cerradas_hoy) for t in [1, 2, 3]}
    pnl_neto_hoy = {k: round(pnl_bruto_hoy - v, 2) for k, v in costes_hoy.items()}

    # --- evolución acumulada (todo el histórico, no solo hoy) ---
    todas_cerradas = [p for p in posiciones if p["estado"] == "cerrada"]
    resultados_r = np.array([p.get("resultado_r", 0.0) for p in todas_cerradas])
    evolucion = {"n_operaciones_acumuladas": len(todas_cerradas)}
    if len(resultados_r) > 0:
        ganancias = resultados_r[resultados_r > 0].sum()
        perdidas = -resultados_r[resultados_r < 0].sum()
        equity = np.cumsum(resultados_r)
        peak = np.maximum.accumulate(equity)
        evolucion.update({
            "expectancy_r": round(resultados_r.mean(), 3),
            "win_rate_pct": round((resultados_r > 0).mean() * 100, 1),
            "profit_factor": round(ganancias / perdidas, 2) if perdidas > 0 else None,
            "max_drawdown_r": round((equity - peak).min(), 2),
            "p_hit_0_5R": round((resultados_r >= 0.5).mean() * 100, 1),
            "p_hit_1R": round((resultados_r >= 1.0).mean() * 100, 1),
            "p_hit_1_5R": round((resultados_r >= 1.5).mean() * 100, 1),
            "p_hit_2R": round((resultados_r >= 2.0).mean() * 100, 1),
        })
        por_estrategia_evol = {}
        for eid in set(p["estrategia_id"] for p in todas_cerradas):
            sub = np.array([p["resultado_r"] for p in todas_cerradas if p["estrategia_id"] == eid])
            por_estrategia_evol[eid] = {"n": len(sub), "expectancy_r": round(sub.mean(), 3), "win_rate_pct": round((sub > 0).mean() * 100, 1)}
        evolucion["por_estrategia"] = por_estrategia_evol

    resumen = {
        "fecha": str(hoy), "modelo_version": modelo.get("version_actual"),
        "revisiones": {
            "n_revisiones_hoy": n_revisiones,
            "primera_revision": ts_hoy[0] if ts_hoy else None, "ultima_revision": ts_hoy[-1] if ts_hoy else None,
            "retraso_medio_min": retraso_medio, "retraso_max_min": retraso_max,
        },
        "oportunidades": {
            "total_candidatas": len(señales_hoy), "por_ticker": por_ticker, "por_estrategia": por_estrategia,
        },
        "rechazadas": {"total": len(rechazadas), "por_motivo": motivos_rechazo, "top_5_recientes": top_rechazadas},
        "operaciones_hoy": {
            "ejecutadas": len(ejecutadas), "cerradas": len(cerradas_hoy),
            "ganadoras": len(ganadoras), "perdedoras": len(perdedoras),
            "pnl_bruto_eur": round(pnl_bruto_hoy, 2), "costes_eur": {k: round(v, 2) for k, v in costes_hoy.items()},
            "pnl_neto_eur": pnl_neto_hoy,
        },
        "evolucion_acumulada": evolucion,
    }
    with open("intradia_resumen_diario.json", "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
