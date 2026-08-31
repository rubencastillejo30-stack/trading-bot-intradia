"""
intradia_paper_trader.py — REESCRITO 2026-08-21 (v0.2). Gestor del SLEEVE
INTRADÍA (paper trading), separado por completo del sistema long-term y
del núcleo/sleeve de eventos existentes.

Cambios de fondo respecto a v0.1 (ver intradia_modelo_version.json):
  - Tamaño de posición por RIESGO (% de un capital teórico), no notional fijo.
  - Múltiples familias de señal pueden coexistir (ver intradia_engine.py).
  - Cada posición registra MFE/MAE reales y el resultado de varios
    "targets sombra" (0.5R a 2R) sobre la MISMA operación, para poder
    comparar estructuras de salida sin multiplicar el capital comprometido.
  - Se registran las FEATURES de cada ticker en CADA revisión, generen
    señal o no (intradia_features_log.json) -- eso es lo que permite
    diagnosticar después si el problema es el filtro, la frecuencia, o el
    universo, en vez de asumirlo.
  - Costes reportados a 1/2/3 ticks.

REGLA DE SEGURIDAD ABSOLUTA: SOLO SIMULACIÓN. Nunca llama a ningún
endpoint de creación/modificación/cancelación de órdenes de Trading 212,
real ni demo. Nunca toca las posiciones long-term ni el núcleo/sleeve de
eventos -- archivos de estado completamente separados.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

from intradia_engine import calcular_features, cargar_intradia, cargar_parametros, generar_señales

POSICIONES_PATH = os.path.join(os.path.dirname(__file__), "intradia_posiciones.json")
HISTORIAL_SEÑALES_PATH = os.path.join(os.path.dirname(__file__), "intradia_historial_señales.json")
FEATURES_LOG_PATH = os.path.join(os.path.dirname(__file__), "intradia_features_log.json")
TICK = 0.01
MAX_FILAS_FEATURES_LOG = 20000  # recorta el log si crece demasiado, conserva lo más reciente


def cargar_json(path: str, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def guardar_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def coste_pct(precio: float, ticks: int) -> float:
    return (2 * ticks * TICK) / precio


def _bars_desde_entrada(datos_ticker: dict, fecha_entrada_iso: str) -> tuple[list, list, list]:
    """Devuelve (highs, lows, closes) de todas las velas de HOY con
    timestamp >= al instante de entrada (recalculado cada vez desde los
    datos completos del día, no de forma incremental)."""
    entrada_dt = dt.datetime.fromisoformat(fecha_entrada_iso)
    entrada_epoch = entrada_dt.timestamp()
    ts = datos_ticker.get("timestamp", [])
    highs, lows, closes = datos_ticker.get("high", []), datos_ticker.get("low", []), datos_ticker.get("close", [])
    idx_validos = [i for i, t in enumerate(ts) if t is not None and t >= entrada_epoch]
    return [highs[i] for i in idx_validos], [lows[i] for i in idx_validos], [closes[i] for i in idx_validos]


def evaluar_posicion(pos: dict, datos_ticker: dict, exit_targets_shadow_r: list[float]) -> dict:
    """Recalcula desde cero (no incremental) si la posición real se ha
    cerrado, MFE/MAE, y el resultado de cada target sombra."""
    highs, lows, closes = _bars_desde_entrada(datos_ticker, pos["fecha_entrada"])
    entrada, riesgo_precio = pos["precio_entrada"], pos["riesgo_precio"]

    if not highs:
        precio_actual = datos_ticker.get("precio_en_vivo") or entrada
        return {"cerrar_real": False, "mfe_r": 0.0, "mae_r": 0.0, "shadows": {}, "precio_actual": precio_actual}

    precio_actual = closes[-1] if closes and closes[-1] is not None else entrada
    stop_precio = pos["stop"]
    target_precio_real = pos["target"]

    # cálculo explícito MFE/MAE en R, vela a vela, hasta el cierre real (si lo hay)
    mfe_r, mae_r = 0.0, 0.0
    idx_cierre_real, precio_cierre_real, motivo_cierre_real = None, None, None
    for i in range(len(highs)):
        h, l = highs[i], lows[i]
        if h is None or l is None:
            continue
        if pos["direccion"] == "LONG":
            fav_r = (h - entrada) / riesgo_precio
            adv_r = (l - entrada) / riesgo_precio
        else:
            fav_r = (entrada - l) / riesgo_precio
            adv_r = (entrada - h) / riesgo_precio
        mfe_r = max(mfe_r, fav_r)
        mae_r = min(mae_r, adv_r)

        if idx_cierre_real is None:
            if pos["direccion"] == "LONG":
                if l <= stop_precio:
                    idx_cierre_real, precio_cierre_real, motivo_cierre_real = i, stop_precio, "stop"
                elif h >= target_precio_real:
                    idx_cierre_real, precio_cierre_real, motivo_cierre_real = i, target_precio_real, "target"
            else:
                if h >= stop_precio:
                    idx_cierre_real, precio_cierre_real, motivo_cierre_real = i, stop_precio, "stop"
                elif l <= target_precio_real:
                    idx_cierre_real, precio_cierre_real, motivo_cierre_real = i, target_precio_real, "target"

    # targets sombra: mismo recorrido, distinto múltiplo de R, resueltos de forma independiente
    shadows = {}
    for target_r in exit_targets_shadow_r:
        resuelto, resultado_r, motivo = False, None, None
        for i in range(len(highs)):
            h, l = highs[i], lows[i]
            if h is None or l is None:
                continue
            if pos["direccion"] == "LONG":
                target_precio_sombra = entrada + target_r * riesgo_precio
                if l <= stop_precio:
                    resuelto, resultado_r, motivo = True, -1.0, "stop"
                    break
                if h >= target_precio_sombra:
                    resuelto, resultado_r, motivo = True, target_r, "target"
                    break
            else:
                target_precio_sombra = entrada - target_r * riesgo_precio
                if h >= stop_precio:
                    resuelto, resultado_r, motivo = True, -1.0, "stop"
                    break
                if l <= target_precio_sombra:
                    resuelto, resultado_r, motivo = True, target_r, "target"
                    break
        shadows[str(target_r)] = {"resuelto": resuelto, "resultado_r": resultado_r, "motivo": motivo}

    return {
        "cerrar_real": idx_cierre_real is not None, "precio_cierre_real": precio_cierre_real,
        "motivo_cierre_real": motivo_cierre_real, "mfe_r": round(mfe_r, 3), "mae_r": round(mae_r, 3),
        "shadows": shadows, "precio_actual": precio_actual,
    }


def main() -> None:
    params, version = cargar_parametros()
    intradia = cargar_intradia()
    tickers_datos = intradia.get("tickers", {})
    datos_qqq, datos_spy = tickers_datos.get("QQQ"), tickers_datos.get("SPY")

    estado = cargar_json(POSICIONES_PATH, {"posiciones": []})
    posiciones = estado["posiciones"]
    historial = cargar_json(HISTORIAL_SEÑALES_PATH, {"señales": []})
    features_log = cargar_json(FEATURES_LOG_PATH, {"registros": []})

    ahora = dt.datetime.now(dt.timezone.utc)
    mercado_abierto = any(d.get("mercado_abierto") for d in tickers_datos.values())
    riesgo_eur_por_operacion = params["capital_teorico_eur"] * params["risk_pct_capital"]
    riesgo_maximo_simultaneo_eur = riesgo_eur_por_operacion * params["max_riesgo_simultaneo_multiplo"]

    cerradas_ahora = []

    # 1) Revisar posiciones abiertas: MFE/MAE, targets sombra, cierre real
    for pos in posiciones:
        if pos["estado"] != "abierta":
            continue
        d = tickers_datos.get(pos["ticker"])
        if not d:
            continue
        ev = evaluar_posicion(pos, d, params["exit_targets_shadow_r"])
        pos["mfe_r"], pos["mae_r"] = ev["mfe_r"], ev["mae_r"]
        pos["shadows"] = ev["shadows"]

        cierre, motivo = None, None
        if ev["cerrar_real"]:
            cierre, motivo = ev["precio_cierre_real"], ev["motivo_cierre_real"]
        elif not mercado_abierto:
            cierre, motivo = ev["precio_actual"], "cierre_sesion"

        if cierre is not None:
            resultado_r = (cierre - pos["precio_entrada"]) / pos["riesgo_precio"] * (1 if pos["direccion"] == "LONG" else -1)
            costes = {f"{t}_ticks": round(coste_pct(pos["precio_entrada"], t) * pos["capital_nocional_eur"], 3) for t in params["coste_ticks_reportados"]}
            pnl_bruto_eur = resultado_r * pos["riesgo_eur"]
            pnl_neto_por_coste = {k: round(pnl_bruto_eur - v, 2) for k, v in costes.items()}
            pos.update({
                "estado": "cerrada", "fecha_cierre": ahora.isoformat(), "precio_cierre": cierre,
                "motivo_cierre": motivo, "resultado_r": round(resultado_r, 3),
                "pnl_bruto_eur": round(pnl_bruto_eur, 2), "costes_eur": costes, "pnl_neto_eur": pnl_neto_por_coste,
            })
            cerradas_ahora.append(pos)

    def riesgo_ocupado() -> float:
        return sum(p["riesgo_eur"] for p in posiciones if p["estado"] == "abierta")

    # 2) Registrar features + generar candidatas SOLO si el mercado está abierto
    abiertas_ahora = []
    if mercado_abierto:
        for ticker, d in tickers_datos.items():
            if ticker in ("QQQ", "SPY"):
                continue  # benchmarks, no candidatos de entrada por ahora
            features = calcular_features(ticker, d, datos_qqq, datos_spy)
            if features is None:
                continue

            registro_features = {k: v for k, v in features.items() if k not in ("idx_hoy", "idx_actual")}
            registro_features["timestamp"] = ahora.isoformat()
            features_log["registros"].append(registro_features)

            señales = generar_señales(features, params)
            for señal in señales:
                registro_base = {
                    "id": str(uuid.uuid4())[:8], "timestamp": ahora.isoformat(), "modelo_version": version,
                    "ticker": ticker, "señal": señal,
                    "features_en_entrada": registro_features,
                }
                ya_abierta = any(p["ticker"] == ticker and p["estado"] == "abierta" for p in posiciones)
                if ya_abierta:
                    registro_base["decision"] = "rechazada"
                    registro_base["motivo_rechazo"] = "ya_hay_posicion_abierta_en_este_ticker"
                elif riesgo_ocupado() + riesgo_eur_por_operacion > riesgo_maximo_simultaneo_eur:
                    registro_base["decision"] = "rechazada"
                    registro_base["motivo_rechazo"] = "sin_hueco_de_riesgo_simultaneo"
                else:
                    riesgo_precio = abs(señal["precio_entrada"] - señal["stop"])
                    if riesgo_precio <= 0:
                        registro_base["decision"] = "rechazada"
                        registro_base["motivo_rechazo"] = "riesgo_precio_invalido"
                    else:
                        cantidad = riesgo_eur_por_operacion / riesgo_precio
                        nueva = {
                            "id": registro_base["id"], "ticker": ticker, "estrategia_id": señal["estrategia_id"],
                            "direccion": señal["direccion"], "fecha_entrada": ahora.isoformat(),
                            "precio_entrada": señal["precio_entrada"], "stop": señal["stop"], "target": señal["target"],
                            "riesgo_precio": riesgo_precio, "cantidad_teorica": cantidad,
                            "riesgo_eur": round(riesgo_eur_por_operacion, 2),
                            "capital_nocional_eur": round(cantidad * señal["precio_entrada"], 2),
                            "estado": "abierta", "modelo_version": version, "mfe_r": 0.0, "mae_r": 0.0,
                            "features_en_entrada": registro_features,
                        }
                        posiciones.append(nueva)
                        abiertas_ahora.append(nueva)
                        registro_base["decision"] = "ejecutada"

                historial["señales"].append(registro_base)

    if len(features_log["registros"]) > MAX_FILAS_FEATURES_LOG:
        features_log["registros"] = features_log["registros"][-MAX_FILAS_FEATURES_LOG:]

    guardar_json(POSICIONES_PATH, {"posiciones": posiciones})
    guardar_json(HISTORIAL_SEÑALES_PATH, historial)
    guardar_json(FEATURES_LOG_PATH, features_log)

    abiertas_totales = [p for p in posiciones if p["estado"] == "abierta"]
    resumen = {
        "actualizado_utc": ahora.isoformat(), "mercado_abierto": mercado_abierto, "modelo_version": version,
        "abiertas_esta_ejecucion": abiertas_ahora, "cerradas_esta_ejecucion": cerradas_ahora,
        "n_abiertas_total": len(abiertas_totales), "riesgo_ocupado_eur": round(riesgo_ocupado(), 2),
        "riesgo_maximo_simultaneo_eur": round(riesgo_maximo_simultaneo_eur, 2),
    }
    with open("intradia_resumen.json", "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(resumen, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
