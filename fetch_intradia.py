"""
fetch_intradia.py — Encargo 2026-08-21: feed de datos del SLEEVE INTRADÍA
(paper trading), completamente separado del núcleo/sleeve de eventos ya
existentes. Se ejecuta DENTRO de un GitHub Action (red sin restricciones),
igual que fetch_and_commit.py, pero con velas de 5 minutos, no diarias.

Universo: los 10 tickers pedidos por el usuario. Amplía este universo solo
si hay una razón estadística clara documentada (regla explícita).

Guarda en data_live/intradia.json — archivo NUEVO, no toca
data_live/precios.json (núcleo/sleeve de eventos).

Solo lectura de datos públicos de mercado. No coloca ninguna orden.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time

import requests

TICKERS = ["QQQ", "SPY", "AAPL", "MSFT", "NVDA", "AMD", "AMZN", "TSLA", "AVGO", "META"]

OUT_PATH = os.path.join(os.path.dirname(__file__), "data_live", "intradia.json")


def fetch_one(symbol: str) -> dict | None:
    # range=5d (no solo 1d): necesitamos las sesiones previas para poder
    # calcular RVOL/ATR de referencia en vivo, sin depender del histórico
    # local de investigación (que no viaja a la rutina en la nube).
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": "5d", "interval": "5m"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("chart", {}).get("result")
    if not result:
        return None
    r = result[0]
    quote = r["indicators"]["quote"][0]
    meta = r.get("meta", {})
    timestamps = r["timestamp"]
    closes, highs, lows, opens, vols = quote["close"], quote["high"], quote["low"], quote["open"], quote["volume"]

    precio_en_vivo = meta.get("regularMarketPrice")
    if closes and closes[-1] is None and precio_en_vivo is not None:
        closes = closes[:-1] + [precio_en_vivo]

    # marketState de Yahoo viene poco fiable (a veces None) con range=5d --
    # se comprueba en su lugar si la última vela real es reciente (frescura
    # del dato), no el campo de metadatos. Umbral: 20 min, más ancho que el
    # intervalo de 15 min entre ejecuciones para dar margen a retrasos.
    ultima_ts = next((t for t in reversed(timestamps) if t is not None), None)
    ahora_epoch = dt.datetime.now(dt.timezone.utc).timestamp()
    mercado_abierto = ultima_ts is not None and (ahora_epoch - ultima_ts) <= 20 * 60

    return {
        "timestamp": timestamps, "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols,
        "precio_en_vivo": precio_en_vivo,
        "mercado_abierto": mercado_abierto,
        "market_state_yahoo": meta.get("marketState"),  # se conserva para depuración, no se usa para decidir
        "regular_market_time": meta.get("regularMarketTime"),
    }


def main() -> None:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    datos, errores = {}, []
    for symbol in TICKERS:
        try:
            d = fetch_one(symbol)
            if d is None:
                errores.append(f"{symbol}: sin resultado")
                continue
            datos[symbol] = d
        except Exception as e:  # noqa: BLE001
            errores.append(f"{symbol}: {e}")
        time.sleep(0.2)

    salida = {
        "actualizado_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tickers": datos,
        "errores": errores,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2, default=str)

    print(f"Guardado {len(datos)}/{len(TICKERS)} tickers intradía en {OUT_PATH}")
    if errores:
        print("Errores:", errores)


if __name__ == "__main__":
    main()
