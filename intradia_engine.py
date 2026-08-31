"""
intradia_engine.py — REESCRITO 2026-08-21 (v0.2). Motor de features y
señales del SLEEVE INTRADÍA (paper trading), separado por completo del
núcleo/sleeve de eventos existentes.

Cambio de fondo respecto a v0.1 (ver intradia_modelo_version.json para el
changelog completo): el filtro rígido `normalized_speed>=0.30 AND
RVOL>=1.5` queda ELIMINADO. Esas variables pasan a ser FEATURES que se
registran siempre, en cada revisión y cada ticker, generen señal o no.
En su lugar hay 5 familias de señal INDEPENDIENTES y versionadas, cada
una con su propia lógica simple y explicable -- no se combinan todas en
un único gate. Pueden coexistir varias señales de distintas familias el
mismo día.

Lee data_live/intradia.json (actualizado por fetch_intradia.py, GitHub
Action). Calcula features EN VIVO usando solo datos con timestamp <=
ahora.

SOLO SIMULACIÓN. Este módulo nunca coloca ninguna orden real ni toca el
sistema long-term ni el núcleo/sleeve de eventos.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import statistics

MODELO_PATH = os.path.join(os.path.dirname(__file__), "intradia_modelo_version.json")
INTRADIA_PATH = os.path.join(os.path.dirname(__file__), "data_live", "intradia.json")

ATR_PERIOD = 14
VELA_MIN = 5  # minutos por vela


def cargar_parametros() -> dict:
    with open(MODELO_PATH, encoding="utf-8") as f:
        modelo = json.load(f)
    version_actual = modelo["version_actual"]
    entrada = next(h for h in modelo["historial"] if h["version"] == version_actual)
    return entrada["parametros"], version_actual


def cargar_intradia() -> dict:
    with open(INTRADIA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _true_range(highs: list, lows: list, closes: list) -> list[float | None]:
    tr = [None]
    for i in range(1, len(closes)):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        if h is None or l is None or prev_c is None:
            tr.append(None)
            continue
        tr.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return tr


def _atr(highs: list, lows: list, closes: list, period: int = ATR_PERIOD) -> float | None:
    tr = [v for v in _true_range(highs, lows, closes) if v is not None]
    if len(tr) < period:
        return None
    return sum(tr[-period:]) / period


def _sesiones_por_fecha(timestamps: list) -> dict:
    sesiones: dict = {}
    for i, ts in enumerate(timestamps):
        if ts is None:
            continue
        fecha = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()
        sesiones.setdefault(fecha, []).append(i)
    return sesiones


def _return_since_open(idx_hoy: list, opens: list, closes: list, idx_actual: int) -> float | None:
    apertura = opens[idx_hoy[0]]
    precio = closes[idx_actual]
    if apertura is None or precio is None:
        return None
    return precio / apertura - 1


def calcular_features(ticker: str, datos_ticker: dict, datos_qqq: dict | None, datos_spy: dict | None) -> dict | None:
    """Calcula TODAS las features descriptivas para el momento actual.
    Se calculan siempre, generen señal o no -- eso es justo lo que se
    pidió: 'registra las condiciones aunque finalmente no genere entrada'."""
    ts = datos_ticker.get("timestamp", [])
    closes, highs, lows, opens, vols = (datos_ticker.get("close", []), datos_ticker.get("high", []),
                                         datos_ticker.get("low", []), datos_ticker.get("open", []),
                                         datos_ticker.get("volume", []))
    if not ts or closes[-1] is None:
        return None

    sesiones = _sesiones_por_fecha(ts)
    fechas_ordenadas = sorted(sesiones.keys())
    if not fechas_ordenadas:
        return None
    fecha_hoy = fechas_ordenadas[-1]
    idx_hoy = sesiones[fecha_hoy]
    if len(idx_hoy) < 2:
        return None

    idx_actual = idx_hoy[-1]
    precio_actual = datos_ticker.get("precio_en_vivo") or closes[idx_actual]
    apertura_hoy = opens[idx_hoy[0]]
    if apertura_hoy is None or precio_actual is None:
        return None

    minutos_desde_apertura = (len(idx_hoy) - 1) * VELA_MIN
    return_since_open = precio_actual / apertura_hoy - 1

    def _ret_hace_n_velas(n: int) -> float | None:
        pos = len(idx_hoy) - 1 - n
        if pos < 0:
            return None
        precio_pasado = closes[idx_hoy[pos]]
        if precio_pasado is None or precio_pasado == 0:
            return None
        return precio_actual / precio_pasado - 1

    return_5m = _ret_hace_n_velas(1)
    return_10m = _ret_hace_n_velas(2)
    return_15m = _ret_hace_n_velas(3)

    atr = _atr(highs[:idx_actual + 1], lows[:idx_actual + 1], closes[:idx_actual + 1])
    speed = (precio_actual - apertura_hoy) / max(minutos_desde_apertura, 1)
    normalized_speed = speed / atr if atr else None

    # aceleración: diferencia entre el retorno de los últimos 5 min y los 5 min anteriores a esos
    ret_5m_anterior = None
    if len(idx_hoy) >= 3:
        pos_a, pos_b = idx_hoy[-2], idx_hoy[-3]
        if closes[pos_a] and closes[pos_b]:
            ret_5m_anterior = closes[pos_a] / closes[pos_b] - 1
    aceleracion = (return_5m - ret_5m_anterior) if (return_5m is not None and ret_5m_anterior is not None) else None

    # VWAP y distancia
    tpv_acum, vol_acum = 0.0, 0.0
    for i in idx_hoy:
        if highs[i] is None or lows[i] is None or closes[i] is None or vols[i] is None:
            continue
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tpv_acum += tp * vols[i]
        vol_acum += vols[i]
    vwap = tpv_acum / vol_acum if vol_acum > 0 else precio_actual
    distance_vwap_pct = (precio_actual - vwap) / precio_actual

    # rango de sesión / máximos-mínimos recientes
    highs_hoy = [highs[i] for i in idx_hoy if highs[i] is not None]
    lows_hoy = [lows[i] for i in idx_hoy if lows[i] is not None]
    session_high = max(highs_hoy) if highs_hoy else precio_actual
    session_low = min(lows_hoy) if lows_hoy else precio_actual
    rango_sesion_pct = (session_high - session_low) / apertura_hoy if apertura_hoy else None

    # máximo/mínimo de las últimas 12 velas (~1h) para detección de ruptura
    ventana_reciente = idx_hoy[-12:]
    highs_recientes = [highs[i] for i in ventana_reciente[:-1] if highs[i] is not None]
    lows_recientes = [lows[i] for i in ventana_reciente[:-1] if lows[i] is not None]
    max_reciente = max(highs_recientes) if highs_recientes else None
    min_reciente = min(lows_recientes) if lows_recientes else None

    # volatilidad reciente: desviación estándar de los retornos de las últimas 12 velas
    closes_recientes = [closes[i] for i in idx_hoy[-13:] if closes[i] is not None]
    retornos_recientes = [closes_recientes[i] / closes_recientes[i - 1] - 1 for i in range(1, len(closes_recientes)) if closes_recientes[i - 1]]
    volatilidad_reciente = statistics.pstdev(retornos_recientes) if len(retornos_recientes) >= 3 else None

    # RVOL: volumen acumulado hoy vs media de la misma franja en sesiones previas
    vol_hoy_acum = sum(vols[i] for i in idx_hoy if vols[i] is not None)
    idx_relativo_actual = len(idx_hoy) - 1
    vols_previos = []
    for fecha in fechas_ordenadas[:-1]:
        idx_dia = sesiones[fecha]
        if len(idx_dia) > idx_relativo_actual:
            vols_previos.append(sum(vols[i] for i in idx_dia[:idx_relativo_actual + 1] if vols[i] is not None))
    rvol = vol_hoy_acum / (sum(vols_previos) / len(vols_previos)) if vols_previos else None

    def _contexto(datos_bench):
        if datos_bench is None:
            return None
        bts = datos_bench.get("timestamp", [])
        bcloses, bopens = datos_bench.get("close", []), datos_bench.get("open", [])
        b_sesiones = _sesiones_por_fecha(bts)
        if fecha_hoy not in b_sesiones:
            return None
        b_idx_hoy = b_sesiones[fecha_hoy]
        if len(b_idx_hoy) < len(idx_hoy):
            return None
        b_idx_actual = b_idx_hoy[len(idx_hoy) - 1]
        return _return_since_open(b_idx_hoy, bopens, bcloses, b_idx_actual)

    qqq_ret = _contexto(datos_qqq)
    spy_ret = _contexto(datos_spy)
    relative_strength_qqq = (return_since_open - qqq_ret) if qqq_ret is not None else None

    return {
        "ticker": ticker, "precio_actual": precio_actual, "atr": atr,
        "minutos_desde_apertura": minutos_desde_apertura,
        "return_since_open": return_since_open, "return_5m": return_5m, "return_10m": return_10m, "return_15m": return_15m,
        "speed": speed, "normalized_speed": normalized_speed, "aceleracion": aceleracion,
        "distance_vwap_pct": distance_vwap_pct, "rango_sesion_pct": rango_sesion_pct,
        "session_high": session_high, "session_low": session_low,
        "max_reciente_12v": max_reciente, "min_reciente_12v": min_reciente,
        "volatilidad_reciente": volatilidad_reciente, "rvol": rvol,
        "qqq_return_since_open": qqq_ret, "spy_return_since_open": spy_ret,
        "relative_strength_qqq": relative_strength_qqq,
        "idx_hoy": idx_hoy, "idx_actual": idx_actual,  # uso interno de los detectores, no se serializan tal cual
    }


# ---------------------------------------------------------------------------
# Familias de señal v0.2 -- independientes, versionadas, sin combinar en un
# único filtro rígido. Cada una define su propio umbral, documentado aquí y
# en intradia_modelo_version.json. Ninguno de estos umbrales se ha validado
# todavía con walk-forward -- son puntos de partida razonables para empezar
# a generar muestra, sujetos a revisión con evidencia acumulada (nunca tras
# una sola mala sesión).
# ---------------------------------------------------------------------------

def _construir_señal(direccion: str, features: dict, params: dict, estrategia_id: str, motivo: str) -> dict:
    entrada = features["precio_actual"]
    atr = features["atr"]
    stop_mult = params["stop_atr_mult"]
    target_mult = params["target_atr_mult_default"]
    if direccion == "LONG":
        stop = entrada - stop_mult * atr
        target = entrada + target_mult * atr
    else:
        stop = entrada + stop_mult * atr
        target = entrada - target_mult * atr
    return {
        "ticker": features["ticker"], "estrategia_id": estrategia_id, "direccion": direccion,
        "precio_entrada": entrada, "stop": stop, "target": target, "atr": atr, "motivo": motivo,
    }


def _detector_momentum(features: dict, params: dict) -> dict | None:
    """MOMENTUM_V01: velocidad normalizada fuerte y consistente (aceleración
    en la misma dirección), confirmada por fuerza relativa vs QQQ."""
    ns = features["normalized_speed"]
    acc = features["aceleracion"]
    rs = features["relative_strength_qqq"]
    if ns is None or acc is None or rs is None:
        return None
    umbral = params["momentum_umbral_speed"]
    if ns >= umbral and acc > 0 and rs > 0:
        return _construir_señal("LONG", features, params, "MOMENTUM_V01",
                                 f"normalized_speed={ns:.3f}>=+{umbral}, aceleración positiva, RS>0")
    if ns <= -umbral and acc < 0 and rs < 0:
        return _construir_señal("SHORT", features, params, "MOMENTUM_V01",
                                 f"normalized_speed={ns:.3f}<=-{umbral}, aceleración negativa, RS<0")
    return None


def _detector_breakout(features: dict, params: dict) -> dict | None:
    """BREAKOUT_V01: ruptura del máximo/mínimo de las últimas 12 velas (~1h)."""
    precio = features["precio_actual"]
    max_r, min_r = features["max_reciente_12v"], features["min_reciente_12v"]
    if max_r is None or min_r is None:
        return None
    if precio > max_r:
        return _construir_señal("LONG", features, params, "BREAKOUT_V01", f"precio {precio:.2f} rompe máximo reciente {max_r:.2f}")
    if precio < min_r:
        return _construir_señal("SHORT", features, params, "BREAKOUT_V01", f"precio {precio:.2f} rompe mínimo reciente {min_r:.2f}")
    return None


def _detector_reversal(features: dict, params: dict) -> dict | None:
    """REVERSAL_V01: precio muy cerca del extremo de sesión pero el retorno
    de la última vela ya apunta en contra de ese extremo (posible agotamiento)."""
    precio = features["precio_actual"]
    session_high, session_low = features["session_high"], features["session_low"]
    ret_5m = features["return_5m"]
    if ret_5m is None or session_high is None or session_low is None or session_high == session_low:
        return None
    umbral_proximidad = params["reversal_umbral_proximidad_pct"]
    cerca_del_high = (session_high - precio) / (session_high - session_low) <= umbral_proximidad
    cerca_del_low = (precio - session_low) / (session_high - session_low) <= umbral_proximidad
    if cerca_del_high and ret_5m < 0:
        return _construir_señal("SHORT", features, params, "REVERSAL_V01", f"precio cerca del máximo de sesión y última vela negativa ({ret_5m:.3%})")
    if cerca_del_low and ret_5m > 0:
        return _construir_señal("LONG", features, params, "REVERSAL_V01", f"precio cerca del mínimo de sesión y última vela positiva ({ret_5m:.3%})")
    return None


def _detector_rs_qqq(features: dict, params: dict) -> dict | None:
    """RS_QQQ_V01: fuerza relativa frente a QQQ muy divergente, sin exigir
    RVOL ni velocidad -- señal independiente para comparar qué aporta cada
    variable por separado."""
    rs = features["relative_strength_qqq"]
    if rs is None:
        return None
    umbral = params["rs_qqq_umbral"]
    if rs >= umbral:
        return _construir_señal("LONG", features, params, "RS_QQQ_V01", f"fuerza relativa vs QQQ={rs:.3%} >= +{umbral:.3%}")
    if rs <= -umbral:
        return _construir_señal("SHORT", features, params, "RS_QQQ_V01", f"fuerza relativa vs QQQ={rs:.3%} <= -{umbral:.3%}")
    return None


def _detector_pullback(features: dict, params: dict) -> dict | None:
    """PULLBACK_V01: momentum reciente claro (return_15m fuerte) pero la
    última vela (return_5m) es un retroceso parcial y pequeño en contra --
    continuación tras respiro, no persecución del extremo."""
    ret15, ret5 = features["return_15m"], features["return_5m"]
    if ret15 is None or ret5 is None:
        return None
    umbral_tendencia = params["pullback_umbral_tendencia_15m"]
    umbral_retroceso_max = params["pullback_umbral_retroceso_5m"]
    if ret15 >= umbral_tendencia and -umbral_retroceso_max <= ret5 < 0:
        return _construir_señal("LONG", features, params, "PULLBACK_V01",
                                 f"tendencia 15m={ret15:.3%}, retroceso leve 5m={ret5:.3%}")
    if ret15 <= -umbral_tendencia and 0 < ret5 <= umbral_retroceso_max:
        return _construir_señal("SHORT", features, params, "PULLBACK_V01",
                                 f"tendencia 15m={ret15:.3%}, retroceso leve 5m={ret5:.3%}")
    return None


DETECTORES = {
    "MOMENTUM_V01": _detector_momentum,
    "BREAKOUT_V01": _detector_breakout,
    "REVERSAL_V01": _detector_reversal,
    "RS_QQQ_V01": _detector_rs_qqq,
    "PULLBACK_V01": _detector_pullback,
}


def generar_señales(features: dict, params: dict) -> list[dict]:
    """Ejecuta TODAS las familias -- pueden coexistir varias señales del
    mismo ticker/momento si distintas familias coinciden. No se combinan
    en un único filtro."""
    señales = []
    for nombre, detector in DETECTORES.items():
        try:
            s = detector(features, params)
        except Exception:  # noqa: BLE001 -- un detector no debe tumbar a los demás
            s = None
        if s:
            señales.append(s)
    return señales
