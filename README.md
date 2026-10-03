# Observatorio Económico de Marbella

Panel web de indicadores socioeconómicos de Marbella (municipio INE **29069**) en el que
**todos los datos son dinámicos**: un proceso automático descarga cada día las fuentes
oficiales y las deja servidas junto al panel. Al abrirlo —hoy o dentro de un año— siempre
muestra el último dato publicado, sin que nadie tenga que tocar nada.

## ¿Cómo funciona? (arquitectura)

```
GitHub Actions (cron diario)
   └─ fetch_data.py  ── descarga ──►  INE Tempus3 · IECA/BADEA · Argos (SAE) · SEPE · MIVAU
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
| Mercado laboral | Paro registrado mensual: total y sexo | Observatorio Argos (SAE) · respaldo SEPE | Mensual (día 2) |
| Mercado laboral | Paro registrado mensual: edad y sector | SEPE datos abiertos | Mensual |
| Mercado laboral | Paro registrado (media anual) | Calculada con los 12 meses | Anual |
| Contratación | Contratos registrados (tipo, sexo, sector) | Observatorio Argos (SAE) · respaldo SEPE | Mensual (día 2) |
| Comparativa | Marbella · Málaga · Andalucía · España | SEPE datos abiertos | Mensual |
| Mercado laboral | Paro, contratos y afiliación por nacionalidad (españoles / extranjeros) | IECA · SIMA | Anual |
| Turismo | Viajeros y pernoctaciones hoteleras por residencia (España / extranjero) | INE · EOH | Mensual |
| Demografía | Población por nacionalidad, lugar de nacimiento y edad | INE · Estadística Continua de Población | Anual |
| Demografía | Migraciones con el extranjero y con otros municipios | INE · EMCR | Anual |
| Demografía | Nacimientos, defunciones y crecimiento vegetativo | IECA · SIMA | Anual |
| Vivienda | Compraventas (nueva / segunda mano) y valor tasado €/m² **de Marbella** | Ministerio de Vivienda (MIVAU) | Trimestral |
| Vivienda | Índice de precios del alquiler **de Marbella** | INE · IPVA | Anual |
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

## Por qué Argos para el paro y los contratos de Marbella

El Observatorio Argos del Servicio Andaluz de Empleo publica el paro y los contratos **por
municipio** el mismo día en que se anuncia el paro (2.º día hábil del mes), que es de donde sale
el dato que da el Ayuntamiento en sus notas de prensa. El SEPE publica su fichero municipal días
después. Cotejado de enero de 2024 a agosto de 2026: el paro coincide en todos los meses y los
contratos en el total (alguna diferencia de 1-2 entre categorías por revisiones). Argos no da el
paro municipal por edad ni sector ni cubre España: eso sigue viniendo del SEPE, que además hace de
respaldo si Argos no contesta.
