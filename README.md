# Observatorio Económico de Marbella

Panel web de indicadores socioeconómicos de Marbella (municipio INE **29069**) en el que
**todos los datos son dinámicos**: un proceso automático descarga cada día las fuentes
oficiales y las deja servidas junto al panel. Al abrirlo —hoy o dentro de un año— siempre
muestra el último dato publicado, sin que nadie tenga que tocar nada.

## ¿Cómo funciona? (arquitectura)

```
GitHub Actions (cron diario)
   └─ fetch_data.py  ── descarga ──►  INE Tempus3 · IECA/BADEA · SEPE datos abiertos
        └─ escribe  data/*.json
GitHub Pages sirve  index.html + data/*.json   (mismo origen → sin problemas de CORS)
```

- **`fetch_data.py`** — recolector. Solo usa la librería estándar de Python (sin dependencias).
  Genera los ficheros de `data/`.
- **`.github/workflows/update.yml`** — ejecuta el recolector cada día a las 05:00 UTC y, si hay
  datos nuevos, los publica. También se puede lanzar a mano desde la pestaña *Actions*.
- **`index.html`** — el panel. Lee `data/*.json` y dibuja las gráficas. No depende de que las
  fuentes originales tengan CORS ni de que ningún ordenador esté encendido.

## Indicadores y fuentes

| Familia | Indicadores | Fuente | Frecuencia |
|---|---|---|---|
| Mercado laboral | Paro registrado (mensual, por sexo y sector) | SEPE datos abiertos | Mensual |
| Mercado laboral | Paro registrado (media anual oficial) | IECA / BADEA | Anual |
| Contratación | Contratos registrados | SEPE datos abiertos | Mensual |
| Turismo | Viajeros, pernoctaciones, ADR, RevPAR | INE · EOH | Mensual |
| Tejido empresarial | Empresas activas (DIRCE) | INE | Anual |
| Renta | Renta media por persona y hogar | INE · Atlas de renta | Anual |

## Puesta en marcha (5 minutos)

1. Crea un repositorio en GitHub (p. ej. `observatorio-economico-marbella`) y sube estos archivos
   (o haz `git push` de esta carpeta, que ya es un repo git con un primer commit).
2. En **Settings → Pages**, en *Build and deployment*, elige **Deploy from a branch** y la rama
   `main` / carpeta `/ (root)`. La web quedará publicada en
   `https://TU-USUARIO.github.io/observatorio-economico-marbella/`.
3. En **Settings → Actions → General**, asegúrate de que *Workflow permissions* está en
   **Read and write permissions** (para que el robot pueda guardar los datos).
4. (Opcional) En la pestaña **Actions**, abre *Actualizar datos del Observatorio* y pulsa
   **Run workflow** para forzar la primera actualización. A partir de ahí se ejecuta solo cada día.

> Los ficheros de `data/` ya vienen con una primera foto de datos, así que el panel funciona desde
> el minuto cero, antes incluso de la primera ejecución programada.

## Probar en local

```bash
python fetch_data.py          # actualiza data/*.json
python -m http.server 8000    # y abre http://localhost:8000
```

## Añadir más indicadores

Cada indicador es una función en `fetch_data.py` que escribe un `data/<algo>.json`, y un bloque de
render en `index.html`. La cabecera del script documenta los códigos usados (INE, nodo BADEA de
Marbella = `2980`, CSV municipal del SEPE). Fuentes verificadas con CORS/consulta "lo último":
INE Tempus3 (`?nult=N`) e IECA/BADEA REST.
