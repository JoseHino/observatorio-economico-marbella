#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Observatorio Económico de Marbella — recolector de datos dinámicos.

Descarga las fuentes oficiales (INE Tempus3, IECA/BADEA, SEPE datos abiertos) y
escribe ficheros JSON en data/. El panel (index.html) los lee desde el mismo
origen, por lo que no depende de CORS ni de ningún PC encendido.

Pensado para GitHub Actions (.github/workflows/update.yml); funciona igual en
local:  python fetch_data.py   ·   solo usa la librería estándar.

Marbella = municipio INE 29069 · provincia Málaga 29 · CCAA Andalucía 01 ·
nodo BADEA 2980.
"""
import json, os, sys, io, csv, urllib.request, urllib.error, datetime

try:                       # consola UTF-8 (Windows usa cp1252 por defecto)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)
MUN   = "29069"   # código INE de Marbella
PROV  = "29"      # provincia Málaga
CCAA  = "1"       # comunidad autónoma Andalucía
BADEA_MARBELLA = "2980"
UA = {"User-Agent": "Mozilla/5.0 (ObservatorioMarbella; +github-actions)"}

def _get(url, timeout=120, retries=3, backoff=2.0):
    """GET con reintentos: tolera cortes de red transitorios (DNS, timeouts)."""
    last = None
    for intento in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            if intento < retries - 1:
                import time
                time.sleep(backoff * (intento + 1))
    raise last

def get_json(url):
    return json.loads(_get(url).decode("utf-8"))

def write(name, obj):
    path = os.path.join(OUT, name)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓ {name}  ({os.path.getsize(path)//1024 or 1} KB)")

def step(title):
    print(f"\n▶ {title}")

def iv(x):
    """Entero robusto: '<5' (enmascarado por privacidad) y vacíos → 0."""
    s = (x or "").strip()
    if not s or s.startswith("<"):
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return int(round(float(s.replace(",", "."))))
        except ValueError:
            return 0

# ---------------------------------------------------------------- INE (Tempus3)
INE     = "https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE/"
INE_TBL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/"

def ine_serie(cod, nult=400):
    try:
        j = get_json(f"{INE}{cod}?nult={nult}")
        return [[d["Anyo"], d.get("FK_Periodo"), d["Fecha"], d["Valor"]]
                for d in j.get("Data", []) if d.get("Valor") is not None]
    except Exception as e:
        print(f"    ! INE serie {cod}: {e}")
        return []

def ine_periodo(anyo, fk, fecha):
    """Devuelve (anyo, mes) del periodo de referencia del dato.

    NO usar 'Fecha' en UTC: el INE la envía como medianoche de Madrid (UTC+1/+2),
    que convertida a UTC cae en el último día del mes ANTERIOR. Eso etiquetaba
    toda la serie mensual un mes por detrás (el dato de junio salía como mayo).
    Compensando ese huso, 'Fecha' sí es fiable: marca el primer día del periodo.

    Para las MENSUALES manda 'FK_Periodo' (1-12 = mes).

    Para las TRIMESTRALES el trimestre se deduce de la fecha compensada y se
    etiqueta con su último mes (03/06/09/12), como espera el front. NO se traduce
    el número de 'FK_Periodo': el INE lo renumera. El IPV usa hoy 19-22 para
    1T-4T —verificado: 2026 FK=19 trae Fecha 2026-01-01— y el código anterior
    daba por hecho 20-23, con lo que TODA serie trimestral salía un trimestre por
    delante: el dato del 4T de 2025 aparecía etiquetado como septiembre.
    """
    if fk and 1 <= fk <= 12:
        return int(anyo), int(fk)
    d = datetime.datetime.fromtimestamp(fecha/1000 + 7200, datetime.timezone.utc)
    if fk and fk > 12:
        return d.year, ((d.month - 1) // 3 + 1) * 3
    return d.year, d.month

def ine_mensual(cod, nult=400):
    out = []
    for anyo, per, fecha, val in ine_serie(cod, nult):
        y, m = ine_periodo(anyo, per, fecha)
        out.append({"t": f"{y:04d}-{m:02d}", "v": val})
    out.sort(key=lambda x: x["t"])
    return out

def ine_anual(cod, nult=60):
    out = {}
    for anyo, per, fecha, val in ine_serie(cod, nult):
        out[int(anyo)] = val
    return [{"y": y, "v": out[y]} for y in sorted(out)]

def tabla_series(cod, tv, det=2):
    """Devuelve la lista de series (cada una con Nombre y Data) de una tabla."""
    return get_json(f"{INE_TBL}{cod}?tv={tv}&det={det}")

def serie_anual_from(series, *needles):
    """Busca en la lista la serie cuyo Nombre contiene TODOS los needles y la
    devuelve como [{y,v}] ordenada por año."""
    nd = [n.lower() for n in needles]
    s = next((x for x in series if all(n in x["Nombre"].lower() for n in nd)), None)
    if not s:
        return []
    pts = [{"y": int(p["Anyo"]), "v": p["Valor"]}
           for p in s["Data"] if p.get("Valor") is not None]
    pts.sort(key=lambda x: x["y"])
    return pts

# ---------------------------------------------------------------- TURISMO
def turismo():
    step("Turismo · INE (EOH hoteles + EOAP apartamentos + VUT + comparativa Málaga)")
    eoh = {                       # Encuesta de Ocupación Hotelera — Marbella
        "viajeros":       "EOT42428",
        "pernoctaciones": "EOT42534",
        "adr":            "EOT43542",  # tarifa media diaria
        "revpar":         "EOT43946",  # ingreso por habitación disponible
        "ocup_plazas":    "EOT3152",
        "ocup_habit":     "EOT3224",
        "estancia_media": "EOT2936",
        "personal":       "EOT3296",
        "establecimientos":"EOT3008",
        "plazas":         "EOT3080",
    }
    apart = {                     # Apartamentos turísticos (EOAP) — Marbella
        "viajeros":       "EOT41395",
        "pernoctaciones": "EOT41394",
        "ocup_plazas":    "EOT9851",
        "estancia_media": "EOT9705",
        "plazas":         "EOT9848",
    }
    vut = {                       # Viviendas de uso turístico (experimental) — Marbella
        "viviendas":      "VTE3889",
        "plazas":         "VTE15629",
        "pct_viviendas":  "VTE28303",
    }
    comp = {                      # Málaga capital (punto turístico) para comparar
        "viajeros":       "EOT42429",
        "pernoctaciones": "EOT42535",
        "adr":            "EOT43543",
        "revpar":         "EOT43947",
    }
    data = {
        "hoteles":      {k: ine_mensual(c) for k, c in eoh.items()},
        "apartamentos": {k: ine_mensual(c) for k, c in apart.items()},
        "vut":          {k: ine_mensual(c) for k, c in vut.items()},
        "malaga":       {k: ine_mensual(c) for k, c in comp.items()},
    }
    write("turismo.json", data)

# ---------------------------------------------------------------- RENTA
def renta():
    step("Renta · INE Atlas (tabla 30824 + distribución 30831)")
    out = {}
    try:
        s = tabla_series("30824", "19:2822")
        out.update({
            "neta_persona":  serie_anual_from(s, "renta neta media por persona"),
            "neta_hogar":    serie_anual_from(s, "renta neta media por hogar"),
            "bruta_persona": serie_anual_from(s, "renta bruta media por persona"),
            "bruta_hogar":   serie_anual_from(s, "renta bruta media por hogar"),
            "media_uc":      serie_anual_from(s, "media de la renta por unidad"),
            "mediana_uc":    serie_anual_from(s, "mediana de la renta por unidad"),
        })
    except Exception as e:
        print(f"    ! renta 30824: {e}")
    try:
        d = tabla_series("30831", "19:2822")
        # riesgo de pobreza relativa = % población por debajo del 60% de la mediana
        rp = serie_anual_from(d, "total. total", "debajo 60")
        if not rp:
            rp = serie_anual_from(d, "debajo 60")
        out["riesgo_pobreza"] = rp
    except Exception as e:
        print(f"    ! renta 30831: {e}")
    write("renta.json", out)

# ---------------------------------------------------------------- DEMOGRAFÍA
def demografia():
    step("Demografía · INE (población: Padrón DPOP; estructura: Atlas 30832)")
    # Población: Cifras Oficiales del Padrón (op. DPOP, tabla 2882) — a 1 de enero,
    # se publica a finales de año (~6 meses de desfase) en vez de los ~2 años del Atlas.
    poblacion = ine_anual("DPOP13669")          # Marbella. Total habitantes.
    pob_h     = ine_anual("DPOP13670")          # Hombres
    pob_m     = ine_anual("DPOP13671")          # Mujeres
    # Estructura demográfica (edad, nacionalidad, hogares): solo el Atlas la da a nivel
    # municipal, y es anual con ~2 años de desfase (dato fiscal/censal definitivo).
    try:
        s = tabla_series("30832", "19:2822")
    except Exception as e:
        print(f"    ! demografía Atlas 30832: {e}"); s = []
    data = {
        "poblacion":        poblacion,
        "poblacion_h":      pob_h,
        "poblacion_m":      pob_m,
        "edad_media":       serie_anual_from(s, "edad media"),
        "pct_menor18":      serie_anual_from(s, "menor de 18"),
        "pct_mayor65":      serie_anual_from(s, "65 y más"),
        "pct_espanola":     serie_anual_from(s, "población española"),
        "tamano_hogar":     serie_anual_from(s, "tamaño medio del hogar"),
        "pct_unipersonales":serie_anual_from(s, "hogares unipersonales"),
    }
    write("demografia.json", data)

# ---------------------------------------------------------------- EMPRESAS
def empresas():
    step("Empresas · INE DIRCE (tabla 4721, total + ramas CNAE)")
    try:
        j = tabla_series("4721", "19:2822")
    except Exception as e:
        print(f"    ! {e}"); write("empresas.json", {}); return
    total = serie_anual_from(j, "total cnae")
    # composición por rama CNAE: serie anual COMPLETA de cada rama (no solo el último año)
    sectores = []
    anios = set()
    for s in j:
        nom = s["Nombre"]
        low = nom.lower()
        if "total cnae" in low:
            continue
        pts = [{"y": int(p["Anyo"]), "v": round(p["Valor"])}
               for p in s["Data"] if p.get("Valor") is not None]
        if not pts:
            continue
        pts.sort(key=lambda x: x["y"])
        anios.update(p["y"] for p in pts)
        # nombre legible de la rama: trozo entre "Total de empresas." y "Empresas."
        rama = nom
        if "total de empresas." in low:
            rama = nom.split("Total de empresas.", 1)[1]
        rama = rama.replace("Empresas.", "").strip(" .")
        if rama:
            sectores.append({"rama": rama, "serie": pts})
    write("empresas.json", {"total": total, "sectores": sectores, "anios": sorted(anios)})

# ---------------------------------------------------------------- VIVIENDA (INE ETDP + IPV)
def vivienda():
    step("Vivienda · INE (compraventa ETDP Málaga + precio IPV Andalucía)")
    comp = {"general": "ETDP1696", "nueva": "ETDP1695", "segunda_mano": "ETDP1694"}
    # OJO: el INE rebasa el IPV y crea tabla nueva, dejando la anterior congelada,
    # igual que hace con el IPC. Los códigos IPV766/939/765/764 (tabla 76201) MURIERON
    # tras el 4T de 2025 y tenían el precio de la vivienda parado once meses sin que
    # nada fallara. Estos son los vigentes (tabla 80270, base nueva: el índice general
    # de Andalucía pasa de 185,9 a 103,8 en el mismo trimestre, es un cambio de base,
    # no una caída de precios). Si vuelve a congelarse, buscar la tabla de Id mayor en
    # TABLAS_OPERACION/IPV y sacar allí los COD de Andalucía.
    ipv  = {"indice": "IPV1623", "var_anual": "IPV1625",
            "indice_nueva": "IPV1628", "indice_segunda": "IPV1633"}
    # Hipotecas constituidas sobre viviendas (INE tabla 76317, base nueva, mensual)
    hipo = {"numero": "HPT34587", "importe": "HPT34534"}   # provincia de Málaga
    data = {
        "compraventa": {k: ine_mensual(c) for k, c in comp.items()},
        "precio":      {k: ine_mensual(c) for k, c in ipv.items()},
        "hipotecas":   {k: ine_mensual(c) for k, c in hipo.items()},
        "ambito": {"compraventa": "provincia de Málaga", "precio": "Andalucía",
                   "hipotecas": "provincia de Málaga"},
    }
    # importe medio por hipoteca (miles € -> €), alineado por mes
    num = {p["t"]: p["v"] for p in data["hipotecas"]["numero"]}
    imp = {p["t"]: p["v"] for p in data["hipotecas"]["importe"]}
    data["hipotecas"]["importe_medio"] = [
        {"t": t, "v": round(imp[t] * 1000.0 / num[t])}
        for t in sorted(num) if num.get(t) and imp.get(t) is not None
    ]
    write("vivienda.json", data)

# ---------------------------------------------------------------- COYUNTURA (INE: IPC + comercio minorista)
def coyuntura():
    step("Coyuntura · INE (IPC Andalucía/España + Índice de Comercio Minorista Andalucía)")
    # OJO: el INE rebasa el IPC y crea tabla nueva cada pocos años; los códigos
    # antiguos quedan congelados. Estos salen de la tabla vigente 79182 (CCAA,
    # ECOICOP ver.2) y 79181 (nacional). Si el IPC se congela, buscar la tabla de
    # Id mayor en TABLAS_OPERACION/IPC y volver a extraer "Índice general".
    data = {
        "ipc": {
            "indice":         ine_mensual("IPC293660"),   # Andalucía · índice general
            "var_anual":      ine_mensual("IPC293659"),   # Andalucía · variación anual
            "indice_es":      ine_mensual("IPC290751"),   # España · índice general
            "var_anual_es":   ine_mensual("IPC290750"),   # España · variación anual
        },
        # Índice de Comercio al por Menor, cifra de negocio a precios constantes,
        # Andalucía, general (tabla 75808) — pulso del consumo real
        "icm": {"indice": ine_mensual("ICM4441"), "var_anual": ine_mensual("ICM4554")},
        "ambito": {"ipc": "Andalucía y España", "icm": "Andalucía"},
    }
    write("coyuntura.json", data)

# ---------------------------------------------------------------- SOCIEDADES MERCANTILES (INE SM, provincial)
def sociedades():
    step("Sociedades mercantiles · INE SM (provincia de Málaga)")
    # OJO: el INE renumera estas series al rebasarlas; los códigos SM180xx quedaron
    # congelados en 2025-03. Estos son los vigentes (Málaga, mensual), verificados.
    cods = {
        "constituidas":         "SM25051",  # nº sociedades creadas
        "disueltas":            "SM8835",   # nº sociedades disueltas
        "aumento_capital":      "SM25912",  # nº que amplían capital
        "capital_constituidas": "SM25522",  # capital suscrito (miles €)
    }
    data = {k: ine_mensual(c) for k, c in cods.items()}
    # saldo neto mensual (creadas - disueltas) alineado por mes
    cre = {p["t"]: p["v"] for p in data["constituidas"]}
    dis = {p["t"]: p["v"] for p in data["disueltas"]}
    data["saldo_neto"] = [{"t": t, "v": cre[t] - dis.get(t, 0)} for t in sorted(cre)]
    write("sociedades.json", data)

# ---------------------------------------------------------------- PARO ANUAL (BADEA)
def paro_badea():
    step("Paro registrado · IECA/BADEA (media anual municipal)")
    B = ("https://www.juntadeandalucia.es/institutodeestadisticaycartografia/"
         "intranet/admin/rest/v1.0/consulta/37016?D_TERRITORIO_0=" + BADEA_MARBELLA)
    try:
        j = get_json(B)
    except Exception as e:
        print(f"    ! {e}"); write("paro_anual.json", {}); return
    data = j.get("data", [])
    def grab(sexo):
        for row in data:
            des = [c.get("des") for c in row if isinstance(c, dict)]
            if sexo in des and "TOTAL" in des and any((d or "").startswith("Parados") for d in des):
                m = next((c for c in row if c.get("val") is not None), None)
                yr = next((c.get("des") for c in row if (c.get("des") or "").isdigit()), "")
                if m: return {"y": yr, "v": round(float(m["val"]))}
        return None
    write("paro_anual.json", {
        "total":   grab("Ambos sexos"),
        "hombres": grab("Hombres"),
        "mujeres": grab("Mujeres"),
    })

# ---------------------------------------------------------------- SEPE (paro+contratos mensual + comparativa)
def _sepe_csv(url):
    raw = _get(url, timeout=180).decode("latin-1")
    return csv.reader(io.StringIO(raw), delimiter=";")

def _dedup_sorted(rows):
    rows.sort(key=lambda x: x["t"])
    seen, out = set(), []
    for r in rows:
        if r["t"] in seen: continue
        seen.add(r["t"]); out.append(r)
    return out

# ---- Parche mensual del SEPE (fichero .xls por provincia) ----------------------
# El CSV anual (Paro/Contratos_por_municipios_AAAA_csv.csv) se refunde con ~1 mes de
# retraso, pero el SEPE publica cada mes primero un .xls por provincia
# (MUNI_MALAGA_MMAA.xls) que SÍ trae el último mes. Aquí se rellenan los meses que
# aún no están en el CSV anual leyendo ese .xls (solo el detalle de Marbella).
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_XLS_CACHE = {}

def _repara_ole(datos):
    """Corrige el marcador de orden de bytes del .xls mensual del SEPE.

    El fichero que publica el SEPE es un documento OLE2 válido salvo por dos
    bytes: en el desplazamiento 28 escribe ``FF FF`` donde el formato exige
    ``FE FF`` (little-endian). xlrd es estricto y lo rechaza con
    ``CompDocError: Expected "little-endian" marker``, de modo que el parche
    mensual fallaba SIEMPRE y el observatorio se quedaba esperando a que el SEPE
    refundiera el CSV anual, un mes más tarde. Corregidos esos dos bytes, el
    libro abre y trae sus hojas PARO y CONTRATOS intactas.

    Se toca solo la cabecera del contenedor, nunca los datos.
    """
    if len(datos) > 30 and datos[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" \
            and datos[28:30] == b"\xff\xff":
        arreglado = bytearray(datos)
        arreglado[28:30] = b"\xfe\xff"
        return bytes(arreglado)
    return datos


def _sepe_muni_xls(year, month):
    """Devuelve el workbook xlrd del fichero mensual de Málaga, o None."""
    key = (year, month)
    if key in _XLS_CACHE:
        return _XLS_CACHE[key]
    _XLS_CACHE[key] = None
    try:
        import xlrd
    except ImportError:
        print("    · xlrd no disponible: se omite el parche mensual del SEPE")
        return None
    page = ("https://www.sepe.es/HomeSepe/que-es-el-sepe/estadisticas/"
            f"datos-estadisticos/municipios/{year}/{_MESES_ES[month-1]}.html")
    import re
    fn = f"MUNI_MALAGA_{month:02d}{year % 100:02d}.xls"
    try:
        html = _get(page, timeout=90).decode("utf-8", "ignore")
        m = re.search(r'href="([^"]*%s)"' % re.escape(fn), html)
        if not m:
            return None
        href = m.group(1)
        url = href if href.startswith("http") else "https://www.sepe.es" + href
        wb = xlrd.open_workbook(file_contents=_repara_ole(_get(url, timeout=120)))
        _XLS_CACHE[key] = wb
        return wb
    except Exception as e:
        print(f"    · {year}-{month:02d}: xls mensual no disponible ({e})")
        return None

def _xls_marbella_row(wb, sheet):
    if wb is None or sheet not in wb.sheet_names():
        return None
    sh = wb.sheet_by_name(sheet)
    for r in range(sh.nrows):
        if str(sh.cell_value(r, 0)).split(".")[0].strip() == MUN:
            return [sh.cell_value(r, c) for c in range(sh.ncols)]
    return None

def _xv(row, i):
    """Valor entero de una celda del .xls (num o texto)."""
    if row is None or i >= len(row):
        return 0
    v = row[i]
    if isinstance(v, (int, float)):
        return int(round(v))
    return iv(str(v))

def _months_after(t, upto_y, upto_m):
    y, m = int(t[:4]), int(t[5:7])
    out = []
    while True:
        m += 1
        if m > 12:
            m = 1; y += 1
        if y > upto_y or (y == upto_y and m > upto_m):
            break
        out.append((y, m))
    return out

def _sepe_patch_meses(paro_mb, contr_mb):
    """Añade a paro_mb / contr_mb los meses de Marbella que falten respecto a hoy,
    leídos del .xls mensual del SEPE. Devuelve la lista de meses añadidos."""
    today = datetime.date.today()
    ult_paro = paro_mb[-1]["t"] if paro_mb else "2020-12"
    ult_contr = contr_mb[-1]["t"] if contr_mb else "2020-12"
    faltan = _months_after(min(ult_paro, ult_contr), today.year, today.month)
    tiene_paro = {r["t"] for r in paro_mb}
    tiene_contr = {r["t"] for r in contr_mb}
    add = []
    for y, mth in faltan:
        t = f"{y:04d}-{mth:02d}"
        wb = _sepe_muni_xls(y, mth)
        if wb is None:
            continue
        p = _xls_marbella_row(wb, "PARO")      # 0cod 1nom 2tot 3H<25 4H25-44 5H>=45 6M<25 7M25-44 8M>=45 9agri 10ind 11constr 12serv 13sin
        if p and t not in tiene_paro:
            paro_mb.append({"t": t, "total": _xv(p, 2),
                "hombres": _xv(p, 3) + _xv(p, 4) + _xv(p, 5),
                "mujeres": _xv(p, 6) + _xv(p, 7) + _xv(p, 8),
                "edad": {"menor25": _xv(p, 3) + _xv(p, 6),
                         "de25a44": _xv(p, 4) + _xv(p, 7),
                         "mayor45": _xv(p, 5) + _xv(p, 8)},
                "sectores": {"agricultura": _xv(p, 9), "industria": _xv(p, 10),
                             "construccion": _xv(p, 11), "servicios": _xv(p, 12),
                             "sin_empleo": _xv(p, 13)}})
        c = _xls_marbella_row(wb, "CONTRATOS")  # 3H_indef 4H_temp 5H_conv 6M_indef 7M_temp 8M_conv 9agri 10ind 11constr 12serv
        if c and t not in tiene_contr:
            indef = _xv(c, 3) + _xv(c, 5) + _xv(c, 6) + _xv(c, 8)
            temp = _xv(c, 4) + _xv(c, 7)
            contr_mb.append({"t": t, "total": _xv(c, 2),
                "indefinidos": indef, "temporales": temp,
                "indef_h": _xv(c, 3) + _xv(c, 5), "temp_h": _xv(c, 4),
                "indef_m": _xv(c, 6) + _xv(c, 8), "temp_m": _xv(c, 7),
                "sectores": {"agricultura": _xv(c, 9), "industria": _xv(c, 10),
                             "construccion": _xv(c, 11), "servicios": _xv(c, 12)}})
        if p or c:
            add.append(t)
    if add:
        print(f"    + parche .xls mensual del SEPE: {', '.join(add)}")
    return add

def sepe_laboral():
    """Descarga los CSV nacionales del SEPE (paro y contratos) y en una sola
    pasada extrae el detalle de Marbella y agrega España / Andalucía / Málaga
    para la comparativa territorial (misma metodología → totalmente comparable)."""
    year = datetime.date.today().year
    years = (year, year-1, year-2)

    # ----- PARO -----
    step("Paro registrado mensual · SEPE (Marbella + comparativa territorial)")
    paro_mb, agg_paro = [], {}   # agg[t] = {esp,and,mal}
    for y in years:
        url = ("https://sede.sepe.gob.es/es/portaltrabaja/resources/sede/"
               f"datos_abiertos/datos/Paro_por_municipios_{y}_csv.csv")
        try:
            rows = _sepe_csv(url)
        except Exception as e:
            print(f"    · paro {y}: no disponible ({e})"); continue
        n = 0
        for r in rows:
            if len(r) < 19: continue
            t = f"{(r[0] or '').strip()[:4]}-{(r[0] or '').strip()[4:6]}"
            if not t[:4].isdigit(): continue
            tot = iv(r[8])
            a = agg_paro.setdefault(t, {"esp":0,"and":0,"mal":0})
            a["esp"] += tot
            if (r[2] or "").strip() == CCAA: a["and"] += tot
            if (r[4] or "").strip() == PROV: a["mal"] += tot
            if (r[6] or "").strip() == MUN:
                paro_mb.append({"t": t, "total": tot,
                    "hombres": iv(r[9])+iv(r[10])+iv(r[11]),
                    "mujeres": iv(r[12])+iv(r[13])+iv(r[14]),
                    "edad": {"menor25": iv(r[9])+iv(r[12]),
                             "de25a44": iv(r[10])+iv(r[13]),
                             "mayor45": iv(r[11])+iv(r[14])},
                    "sectores": {"agricultura": iv(r[15]), "industria": iv(r[16]),
                                 "construccion": iv(r[17]), "servicios": iv(r[18]),
                                 "sin_empleo": iv(r[19]) if len(r) > 19 else 0}})
                n += 1
        print(f"    · paro {y}: {n} meses de Marbella")
    paro_mb = _dedup_sorted(paro_mb)

    # ----- CONTRATOS -----
    step("Contratos registrados mensual · SEPE (Marbella + comparativa territorial)")
    contr_mb, agg_contr = [], {}
    for y in years:
        url = ("https://sede.sepe.gob.es/es/portaltrabaja/resources/sede/"
               f"datos_abiertos/datos/Contratos_por_municipios_{y}_csv.csv")
        try:
            rows = _sepe_csv(url)
        except Exception as e:
            print(f"    · contratos {y}: no disponible ({e})"); continue
        n = 0
        for r in rows:
            if len(r) < 19: continue
            t = f"{(r[0] or '').strip()[:4]}-{(r[0] or '').strip()[4:6]}"
            if not t[:4].isdigit(): continue
            tot  = iv(r[8])
            # indef = iniciales indef (H+M) + convertidos a indef (H+M)
            indef = iv(r[9]) + iv(r[12]) + iv(r[11]) + iv(r[14])
            temp  = iv(r[10]) + iv(r[13])
            a = agg_contr.setdefault(t, {"esp":[0,0,0],"and":[0,0,0],"mal":[0,0,0]})
            a["esp"][0]+=tot; a["esp"][1]+=indef; a["esp"][2]+=temp
            if (r[2] or "").strip()==CCAA: a["and"][0]+=tot; a["and"][1]+=indef; a["and"][2]+=temp
            if (r[4] or "").strip()==PROV: a["mal"][0]+=tot; a["mal"][1]+=indef; a["mal"][2]+=temp
            if (r[6] or "").strip()==MUN:
                contr_mb.append({"t": t, "total": tot,
                    "indefinidos": indef, "temporales": temp,
                    "indef_h": iv(r[9])+iv(r[11]), "temp_h": iv(r[10]),
                    "indef_m": iv(r[12])+iv(r[14]), "temp_m": iv(r[13]),
                    "sectores": {"agricultura": iv(r[15]), "industria": iv(r[16]),
                                 "construccion": iv(r[17]), "servicios": iv(r[18])}})
                n += 1
        print(f"    · contratos {y}: {n} meses de Marbella")
    contr_mb = _dedup_sorted(contr_mb)

    # ----- PARCHE: meses recientes aún no refundidos en el CSV anual -----
    # Lee el .xls mensual del SEPE (sale antes) para completar Marbella hasta hoy.
    _sepe_patch_meses(paro_mb, contr_mb)
    paro_mb = _dedup_sorted(paro_mb)
    contr_mb = _dedup_sorted(contr_mb)
    write("paro_mensual.json", {"serie": paro_mb})
    write("contratos_mensual.json", {"serie": contr_mb})

    # ----- COMPARATIVA TERRITORIAL -----
    step("Comparativa territorial · agregados SEPE (España/Andalucía/Málaga/Marbella)")
    meses = sorted(set(agg_paro) | set(agg_contr))
    mb_paro = {r["t"]: r["total"] for r in paro_mb}
    mb_contr = {r["t"]: r for r in contr_mb}
    def tasa_temp(total, temp):
        return round(temp/total*100, 1) if total else None
    comp = []
    for t in meses:
        ap = agg_paro.get(t); ac = agg_contr.get(t)
        row = {"t": t}
        if ap:
            row["paro"] = {"marbella": mb_paro.get(t), "malaga": ap["mal"],
                           "andalucia": ap["and"], "espana": ap["esp"]}
        if ac:
            mb = mb_contr.get(t, {})
            row["temporalidad"] = {
                "marbella":  tasa_temp(mb.get("total"), mb.get("temporales")) if mb else None,
                "malaga":    tasa_temp(ac["mal"][0], ac["mal"][2]),
                "andalucia": tasa_temp(ac["and"][0], ac["and"][2]),
                "espana":    tasa_temp(ac["esp"][0], ac["esp"][2]),
            }
        comp.append(row)
    write("comparativa_laboral.json", {"serie": comp})

# ------------------------------------- AFILIACIÓN SEG. SOCIAL (IECA/BADEA b3_291)
# "Afiliados a la Seguridad Social en alta laboral que trabajan en Andalucía".
# Consulta 876 = Afiliaciones por municipio de RESIDENCIA, por régimen (ambos sexos).
# Mensual (último día del mes) desde jul-2021; trimestral antes (desde 2012).
BADEA_REST = ("https://www.juntadeandalucia.es/institutodeestadisticaycartografia/"
              "intranet/admin/rest/v1.0")
AFI_CONSULTA = "876"
AFI_TERR = {"marbella": "2980", "malaga": "3023", "andalucia": "3143"}  # nodos jerarquía 163
AFI_REGS = ("total", "general", "autonomos", "agrario", "mar", "hogar")

def _afi_reg_key(des):
    d = (des or "").lower()
    if "total" in d:                       return "total"
    if "agrario" in d:                     return "agrario"
    if "aut" in d and "nomo" in d:         return "autonomos"
    if "del mar" in d or d.strip().endswith("mar"): return "mar"
    if "hogar" in d:                       return "hogar"
    if "general" in d:                     return "general"
    return None

def _afi_periodos():
    """[(idNodo, 'YYYY-MM')] de periodos disponibles, en orden cronológico."""
    j = get_json(f"{BADEA_REST}/jerarquia/3153?consultaId={AFI_CONSULTA}&alias=D_TEMPORAL_0")
    out = []
    def flat(n):
        for x in (n if isinstance(n, list) else [n]):
            cod = str(x.get("cod") or "")
            if cod.isdigit() and len(cod) == 6 and 2010 <= int(cod[:4]) <= 2035:
                out.append((x.get("id"), f"{cod[:4]}-{cod[4:6]}"))
            for c in (x.get("children") or []):
                flat(c)
    flat(j.get("data") or j)
    seen, res = set(), []
    for i, t in sorted(out, key=lambda z: z[1]):
        if t in seen:
            continue
        seen.add(t); res.append((i, t))
    return res

def _afi_val(cell):
    try:
        return round(float(cell.get("val")))
    except (TypeError, ValueError, AttributeError):
        return 0

def _afi_periodo(pid, t):
    """Un solo request (todos los municipios de ese periodo). Extrae Marbella y
    agrega la provincia de Málaga (cód. prov. '29') y Andalucía (suma de todos los
    municipios; el producto es 'residencia en Andalucía', así que la suma municipal
    es el total autonómico — la fila '00' es el TOTAL e incluye 'Resto de España')."""
    j = get_json(f"{BADEA_REST}/consulta/{AFI_CONSULTA}?D_TEMPORAL_0={pid}")
    out = {ter: {k: 0 for k in AFI_REGS} for ter in AFI_TERR}
    mb_seen = False
    for r in j.get("data", []):
        cod = r[0].get("cod") or []
        if len(cod) != 5:                                   # solo filas municipales
            continue
        k = _afi_reg_key(r[1].get("des", ""))
        if not k:
            continue
        v = _afi_val(r[4])
        out["andalucia"][k] += v                            # todos los municipios = Andalucía
        if cod[3] == PROV:                                  # provincia de Málaga
            out["malaga"][k] += v
        if cod[4] == MUN:                                   # Marbella
            out["marbella"][k] = v; mb_seen = True
    return t, out, (mb_seen and out["andalucia"]["total"] > 0)

def afiliacion():
    step("Afiliación a la Seguridad Social · IECA/BADEA (b3_291, municipal por régimen)")
    periodos = _afi_periodos()
    if not periodos:
        print("    ! no se pudieron obtener periodos"); write("afiliacion.json", {}); return
    print(f"    · {len(periodos)} periodos ({periodos[0][1]} → {periodos[-1][1]}) · descargando en paralelo…")
    from concurrent.futures import ThreadPoolExecutor
    res = {}
    def task(pt):
        pid, t = pt
        try:
            return _afi_periodo(pid, t)
        except Exception as e:
            print(f"      · {t}: {e}"); return t, None, False
    with ThreadPoolExecutor(max_workers=8) as ex:
        for t, out, ok in ex.map(task, periodos):
            if out and ok:
                res[t] = out
    periodos_ok = [t for _, t in periodos if t in res]
    data = {ter: {k: [{"t": t, "v": res[t][ter][k]} for t in periodos_ok] for k in AFI_REGS}
            for ter in AFI_TERR}
    for ter in AFI_TERR:
        tot = data[ter]["total"]
        print(f"    · {ter}: {len(tot)} puntos · último total = {tot[-1]['v'] if tot else '—'}")
    data["periodos"] = periodos_ok
    data["ambito"] = ("Afiliados por municipio de residencia (Marbella); "
                      "agregados de la provincia de Málaga y de Andalucía para comparar")
    write("afiliacion.json", data)

# ---------------------------------------------------------------- VIGILANTE DE FRESCURA
# Desfase máximo tolerado (en meses) antes de avisar de que un indicador se ha quedado
# obsoleto. Sirve para cazar "series muertas" del INE (que renumera y congela códigos)
# sin que nadie lo descubra por casualidad. Los anuales llevan una tolerancia alta.
_FRESCURA_MAX = {
    "paro_mensual.json": 2, "contratos_mensual.json": 2, "comparativa_laboral.json": 3,
    "afiliacion.json": 4, "sociedades.json": 4, "turismo.json": 3, "vivienda.json": 7,
    "coyuntura.json": 3,
    "paro_anual.json": 16, "empresas.json": 20, "renta.json": 32, "demografia.json": 32,
}

def _normaliza(v):
    """'2026-06' se compara tal cual; un año suelto ordena tras sus meses."""
    if v is None: return None
    s = str(v)
    return s + "-13" if len(s) == 4 and s.isdigit() else s


def _series_del_fichero(obj):
    """Devuelve {ruta: último periodo} para CADA serie del JSON, no para el fichero.

    Auditar el fichero entero como una sola cosa dejaba pasar justo lo que este
    vigilante existe para cazar: en vivienda.json conviven la compraventa mensual
    —viva— y el precio de la vivienda —congelado once meses porque el INE renumeró
    la tabla—. Al quedarse con el periodo MAYOR del fichero, la serie muerta era
    invisible. Se mira serie a serie.
    """
    out = {}

    def es_punto(x):
        return isinstance(x, dict) and ("t" in x or "y" in x)

    def anota(ruta, periodo):
        periodo = _normaliza(periodo)
        if not periodo:
            return
        clave = ruta or "(raíz)"
        if clave not in out or periodo > out[clave]:
            out[clave] = periodo

    def walk(x, ruta):
        if isinstance(x, list):
            puntos = [p for p in x if es_punto(p)]
            if puntos:
                for p in puntos:
                    anota(ruta, p.get("t") or p.get("y"))
                return
            for i, v in enumerate(x):
                walk(v, f"{ruta}[{i}]" if ruta else f"[{i}]")
        elif es_punto(x):
            # paro_anual guarda listas de dicts de puntos: {total:{y,v}, hombres:{y,v}}
            anota(ruta, x.get("t") or x.get("y"))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{ruta}.{k}" if ruta else k)
    walk(obj, "")
    return {k: (v[:4] if v.endswith("-13") else v[:7]) for k, v in out.items()}


def _ultimo_periodo(obj):
    """Mayor periodo ('YYYY' o 'YYYY-MM') hallado recursivamente en un JSON."""
    series = _series_del_fichero(obj)
    if not series:
        return None
    return max(series.values(), key=lambda s: _normaliza(s))

def _meses_desde(periodo, hoy):
    if not periodo: return 999
    y = int(periodo[:4]); m = int(periodo[5:7]) if len(periodo) >= 7 else 12
    return (hoy.year - y) * 12 + (hoy.month - m)

def auditar_frescura():
    """Revisa la antigüedad de cada fichero de datos y avisa de los obsoletos.
    Devuelve un dict {fichero: {ultimo, desfase_meses, obsoleto}} para meta.json."""
    step("Auditoría de frescura de los indicadores")
    hoy = datetime.date.today()
    rep, alertas = {}, []
    for name in sorted(os.listdir(OUT)):
        if not name.endswith(".json") or name in ("meta.json",):
            continue
        try:
            obj = json.load(io.open(os.path.join(OUT, name), encoding="utf-8"))
        except Exception:
            continue
        ult = _ultimo_periodo(obj)
        desf = _meses_desde(ult, hoy)
        tope = _FRESCURA_MAX.get(name, 6)
        obsoleto = desf > tope

        # Y ahora serie a serie: una serie muerta dentro de un fichero por lo demás
        # fresco es el caso que hay que cazar, y el que el fichero entero disimula.
        rezagadas = {}
        for ruta, periodo in sorted(_series_del_fichero(obj).items()):
            atraso = _meses_desde(periodo, hoy)
            # Se compara con la serie más fresca del propio fichero: si una va muy
            # por detrás de sus hermanas, es que su código de origen ha muerto.
            if atraso > tope and atraso - desf >= 3:
                rezagadas[ruta] = {"ultimo": periodo, "desfase_meses": atraso}

        rep[name] = {"ultimo": ult, "desfase_meses": desf, "obsoleto": obsoleto}
        if rezagadas:
            rep[name]["series_rezagadas"] = rezagadas
        flag = "  ⚠ OBSOLETO" if obsoleto else ("  ⚠ CON SERIES REZAGADAS" if rezagadas else "")
        print(f"    · {name:28s} último={ult} ({desf} meses){flag}")
        for ruta, info in rezagadas.items():
            print(f"        ↳ {ruta}: último {info['ultimo']} ({info['desfase_meses']} meses)")
        if obsoleto:
            alertas.append(f"{name} (último {ult}, {desf} meses)")
        for ruta, info in rezagadas.items():
            alertas.append(f"{name} · {ruta} (último {info['ultimo']}, "
                           f"{info['desfase_meses']} meses)")
    if alertas:
        print("    !! INDICADORES POSIBLEMENTE OBSOLETOS (revisar códigos de fuente):")
        for a in alertas: print(f"       - {a}")
    return rep

# ---------------------------------------------------------------- MAIN
def main():
    print("== Observatorio Económico Marbella · recolección de datos ==")
    errors = 0
    for fn in (turismo, renta, demografia, empresas, vivienda, coyuntura, sociedades,
               paro_badea, afiliacion, sepe_laboral):
        try:
            fn()
        except Exception as e:
            errors += 1
            print(f"    !! fallo en {fn.__name__}: {e}")
    try:
        frescura = auditar_frescura()
    except Exception as e:
        frescura = {}; print(f"    !! fallo en auditar_frescura: {e}")
    meta = {
        "generado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuentes": ["INE Tempus3", "IECA/BADEA (paro + afiliación SS)", "SEPE datos abiertos"],
        "municipio": "Marbella (29069)",
        "ambito_comparativa": "Marbella · Málaga (29) · Andalucía · España",
        "frescura": frescura,
    }
    write("meta.json", meta)
    print(f"\n== Completado. Fallos: {errors} ==")
    return 0 if errors == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
