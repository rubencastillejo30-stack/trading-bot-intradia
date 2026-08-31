# trading-bot-intradia

Sleeve de **paper trading intradía** — simulación, no ejecuta ninguna orden
real ni en cuenta real ni demo. No coloca ninguna orden contra Trading 212
ni ningún otro bróker. Todo el "trading" aquí es contabilidad simulada
sobre datos de mercado públicos.

Este repositorio es un extracto público de un proyecto de investigación
más amplio (núcleo validado + sleeve de eventos + investigación histórica),
que permanece en un repositorio privado aparte. Este repo contiene
únicamente el sleeve intradía, separado para poder usar `repository_dispatch`
de GitHub Actions sin el límite de minutos gratis de los repos privados.

## Contenido

- `fetch_intradia.py` — descarga velas de 5 min (Yahoo Finance, datos públicos).
- `intradia_engine.py` — 5 familias de señal independientes y versionadas
  (MOMENTUM, BREAKOUT, REVERSAL, RS_QQQ, PULLBACK), LONG y SHORT.
- `intradia_paper_trader.py` — gestión de posiciones simuladas, tamaño por
  riesgo, seguimiento de MFE/MAE y de 6 objetivos "sombra" (0.5R-2R).
- `intradia_resumen_diario.py` / `intradia_analizar_ventanas.py` — cálculo
  de estadísticas para el informe diario.
- `intradia_modelo_version.json` — changelog obligatorio de cada versión
  del motor de señales (qué cambió, por qué, con qué datos).
- `.github/workflows/actualizar_intradia.yml` — se ejecuta vía
  `repository_dispatch`, disparado por un pinger externo (cron-job.org)
  aproximadamente cada 5-10 minutos durante la sesión NYSE/Nasdaq.

## Estado

Señal v0.2, **provisional, no validada** con walk-forward/bootstrap todavía
— en fase de acumulación de muestra real de paper trading antes de sacar
ninguna conclusión estadística.
