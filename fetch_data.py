#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Observatorio Económico de Marbella — recolector de datos dinámicos.

Descarga las fuentes oficiales (INE Tempus3, IECA/BADEA, Observatorio Argos del
SAE, SEPE datos abiertos) y
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

# Servidores que en esta pasada no han llegado a contestar. El 3-10-2026 el SEPE dejó
# de aceptar conexiones desde los runners de GitHub (en EE. UU.; desde España
# respondía en 2 s): cada uno de sus ficheros agotaba 3 reintentos de hasta 180 s y la
# ejecución se fue a 54 minutos para no traer nada. Ahora el primer fallo de conexión
# marca el servidor como caído y el resto de peticiones a él se saltan al momento; lo
# publicado se conserva (write/write_serie) y se vuelve a probar en la siguiente pasada.
_CAIDOS = set()

def _es_fallo_conexion(e):
    """Timeouts y errores de red, no respuestas HTTP (un 404 es una respuesta)."""
    import socket
    if isinstance(e, urllib.error.HTTPError):
        return False
    return isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError))

def _get(url, timeout=120, retries=3, backoff=2.0):
    """GET con reintentos: tolera cortes de red transitorios (DNS, timeouts)."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    if host in _CAIDOS:
        raise ConnectionError(f"{host} no contesta en esta pasada (se omite)")
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
    if _es_fallo_conexion(last):
        _CAIDOS.add(host)
        print(f"    ✗ {host} no contesta ({last}): se omite el resto de sus peticiones")
    raise last

def get_json(url):
    return json.loads(_get(url).decode("utf-8"))

# ---------------------------------------------------------------- ESCRITURA SEGURA
# Las fuentes se caen. El SEPE devolvió 503 en TODO su sitio durante dos ventanas de
# varios días (12-13 y 19-20 de septiembre de 2026) y, tal como estaba escrito esto,
# una caída pasajera de la fuente BORRABA el indicador: cada descarga atrapa su error
# y sigue, así que al final se escribía {"serie":[]} —12 bytes— encima de un fichero
# bueno, el Action lo commiteaba y el panel se quedaba en blanco hasta la siguiente
# pasada con suerte (la del 19 dejó el bloque laboral vacío 37 horas). Encima el
# vigilante de frescura leía "último = None" y marcaba el indicador como obsoleto, que
# es el aviso reservado a los códigos de origen MUERTOS: correos rojos de fallo por
# una avería ajena y pasajera.
#
# Regla ahora: un dato publicado no se sustituye NUNCA por nada. Si la fuente no
# contesta, se conserva lo último bueno y se dice en el log.
_SIN_GUARDIA = {"meta.json", "contexto_ia.json"}   # sin periodos propios: se reescriben siempre
CONSERVADOS = []               # ficheros que esta pasada NO ha podido refrescar

def _leer(name):
    """El JSON ya publicado en data/, o None si no hay o no se puede leer."""
    try:
        return json.load(io.open(os.path.join(OUT, name), encoding="utf-8"))
    except Exception:
        return None

def _sin_datos(obj):
    """True si el objeto no contiene ni un solo punto con periodo."""
    try:
        return _ultimo_periodo(obj) is None
    except Exception:
        return not obj

def write(name, obj):
    path = os.path.join(OUT, name)
    if name not in _SIN_GUARDIA and _sin_datos(obj):
        prev = _leer(name)
        if prev is not None and not _sin_datos(prev):
            print(f"  ⟲ {name}: la fuente no ha devuelto nada; se CONSERVA lo publicado")
            if name not in CONSERVADOS:
                CONSERVADOS.append(name)
            return
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓ {name}  ({os.path.getsize(path)//1024 or 1} KB)")

def write_serie(name, serie, clave="serie"):
    """Escribe una serie temporal FUNDIÉNDOLA con la ya publicada.

    Lo descargado manda (el SEPE revisa cifras de meses anteriores), pero los meses
    que la descarga no ha traído se conservan. Así una caída PARCIAL de la fuente
    —que un año responda y otro no— tampoco amputa el histórico.
    """
    prev = (_leer(name) or {}).get(clave) or []
    fus = {r["t"]: r for r in prev if isinstance(r, dict) and r.get("t")}
    bajados = 0
    for r in serie:
        if isinstance(r, dict) and r.get("t"):
            fus[r["t"]] = r
            bajados += 1
    # Que la descarga traiga MENOS periodos que los publicados es lo normal: el CSV
    # anual del SEPE cubre tres años y el último mes entra por el parche .xls, que no
    # vuelve a bajar lo que ya está. Solo hay avería cuando no viene NADA.
    if not bajados and prev:
        print(f"    ⟲ {name}: la fuente no ha devuelto ningún periodo; "
              f"se conservan los {len(prev)} publicados")
        if name not in CONSERVADOS:
            CONSERVADOS.append(name)
    write(name, {clave: [fus[t] for t in sorted(fus)]})

def _ultimo_mes_publicado(name, clave="serie"):
    """Último periodo de la serie ya publicada en data/, o None."""
    s = (_leer(name) or {}).get(clave) or []
    ts = [r["t"] for r in s if isinstance(r, dict) and r.get("t")]
    return max(ts) if ts else None

def _ultimo_mes_con_desglose(name, clave="serie"):
    """Último periodo publicado que trae el desglose por sector, o None."""
    s = (_leer(name) or {}).get(clave) or []
    ts = [r["t"] for r in s if isinstance(r, dict) and r.get("t") and r.get("sectores")]
    return max(ts) if ts else None

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
        # por lugar de residencia del viajero (tabla 2078): mercado nacional / internacional
        "viajeros_esp":   "EOT2759",
        "viajeros_ext":   "EOT2760",
        "pernoct_esp":    "EOT2761",
        "pernoct_ext":    "EOT2762",
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
    # Estadística Continua de Población (op. ECP, tablas 79543-79545): población
    # residente a 1 de enero por nacionalidad, lugar de nacimiento y grupo de edad.
    # Municipal y con solo ~9 meses de desfase: da el dato de extranjeros dos años
    # antes que el Atlas (que se queda para la serie larga del % de españoles).
    ecp = {
        "nac_espanola":   "ECP357741", "nac_extranjera":  "ECP357740",
        "ext_hombres":    "ECP357737", "ext_mujeres":     "ECP357734",
        "nacidos_espana": "ECP358497", "nacidos_extranjero": "ECP358496",
        "edad_0_15":  "ECP356535", "edad_16_24": "ECP356534", "edad_25_44": "ECP356533",
        "edad_45_64": "ECP356532", "edad_65":    "ECP356531",
    }
    data["residentes"] = {k: ine_anual(c) for k, c in ecp.items()}
    # Estadística de Migraciones y Cambios de Residencia (op. EMCR): anual, municipal.
    emcr = {
        "inmig_extranjero": "EM1827059",   # llegadas desde el extranjero (69696)
        "emig_extranjero":  "EM1905260",   # salidas al extranjero (69711)
        "saldo_total":      "EM2363872",   # saldos (69767)
        "saldo_exterior":   "EM2363873",
        "saldo_interior":   "EM2363874",
        "inter_in_esp":     "EM1970514",   # llegadas desde otros municipios (69743)
        "inter_in_ext":     "EM1970517",
        "inter_out_esp":    "EM2117826",   # salidas a otros municipios (69745)
        "inter_out_ext":    "EM2117829",
    }
    data["migraciones"] = {k: ine_anual(c) for k, c in emcr.items()}
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

# ------------------------------------------------- PARO ANUAL (media de los meses del SEPE)
def paro_anual():
    """Media anual del paro registrado, calculada con los meses que ya trae el SEPE.

    Antes se pedía a IECA/BADEA. El 21-09-2026 la Junta dejó de aceptar tráfico de
    fuera de Europa y los runners de GitHub están en Azure East US (Virginia): la
    conexión ni siquiera llega a abrirse -timeout en el TCP, no un 403-, así que no
    hay cabecera ni ruta que lo arregle. Como BADEA publicaba exactamente la media
    de los doce meses que el SEPE ya nos da, se calcula aquí y se deja de depender
    de una fuente inalcanzable. Comprobado contra el último valor que llegó a
    publicar BADEA (2025: total 7294, hombres 2799, mujeres 4496): coincide.

    Necesita paro_mensual.json ya escrito, así que en main() va DESPUÉS de sepe_laboral().
    """
    step("Paro registrado · media anual (calculada con los meses del SEPE)")
    serie = (_leer("paro_mensual.json") or {}).get("serie") or []
    if not serie:
        print("    ! no hay paro mensual del que sacar la media")
        write("paro_anual.json", {})
        return

    anios = {}
    for p in serie:
        t = str(p.get("t", ""))
        if len(t) == 7 and t[:4].isdigit():
            anios.setdefault(t[:4], []).append(p)

    completos = sorted(a for a, ms in anios.items() if len(ms) == 12)
    if not completos:
        print("    ! aún no hay ningún año completo (12 meses) en la serie mensual")
        write("paro_anual.json", {})
        return

    ultimo = completos[-1]
    meses = anios[ultimo]

    def media(clave):
        vals = [m[clave] for m in meses if isinstance(m.get(clave), (int, float))]
        if len(vals) != 12:
            return None
        return {"y": ultimo, "v": round(sum(vals) / 12)}

    datos = {k: media(k) for k in ("total", "hombres", "mujeres")}
    hecho = datos.get("total")
    print(f"    · {ultimo}: media de 12 meses · total = {hecho['v'] if hecho else '?'}")
    write("paro_anual.json", datos)

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
_XLS_VENTANA = 6          # meses hacia atrás como mucho: más es histórico ya publicado
_XLS_FALLOS_SEGUIDOS = 3  # 3 meses seguidos sin respuesta = la fuente está caída

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
    # El punto de partida es el último mes que YA está publicado, no solo el del CSV
    # recién bajado: si el CSV anual no responde, paro_mb llega vacío y el parche se
    # ponía a pedir uno por uno TODOS los meses desde 2021 (66 ficheros × 2 peticiones
    # × 3 reintentos) contra una web que ya estaba dando 503. Trece minutos de Action
    # martilleando al SEPE para no traer nada.
    # Para el paro cuenta el último mes publicado CON desglose (edad y sector): los
    # meses que llegan antes por Argos solo traen total y sexo, y el .xls del SEPE
    # sigue haciendo falta para completarlos.
    ult_paro = max(filter(None, [paro_mb[-1]["t"] if paro_mb else None,
                                 _ultimo_mes_con_desglose("paro_mensual.json")]),
                   default="2020-12")
    ult_contr = max(filter(None, [contr_mb[-1]["t"] if contr_mb else None,
                                  _ultimo_mes_publicado("contratos_mensual.json")]),
                    default="2020-12")
    faltan = _months_after(min(ult_paro, ult_contr), today.year, today.month)
    faltan = faltan[-_XLS_VENTANA:]      # el CSV anual se refunde con ~1 mes de retraso
    tiene_paro = {r["t"] for r in paro_mb}
    tiene_contr = {r["t"] for r in contr_mb}
    add, fallos = [], 0
    for y, mth in faltan:
        t = f"{y:04d}-{mth:02d}"
        wb = _sepe_muni_xls(y, mth)
        if wb is None:
            fallos += 1
            if fallos >= _XLS_FALLOS_SEGUIDOS:
                print("    · el SEPE no responde: se abandona el parche mensual "
                      "(los datos publicados se conservan)")
                break
            continue
        fallos = 0
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

# ---- Observatorio Argos (Servicio Andaluz de Empleo) -------------------------
# El paro registrado en Andalucía lo gestiona el SAE, y su Observatorio Argos
# publica el dato MUNICIPAL el mismo día de la rueda de prensa del paro (el 2 de
# octubre de 2026 a las 09:00 ya tenía septiembre), mientras que el .xls municipal
# del SEPE tarda varios días más y el CSV anual, un mes. Es la fuente que usa el
# Ayuntamiento en sus notas de prensa.
#
# Cotejado contra el SEPE de ene-2024 a ago-2026: el paro (total, hombres,
# mujeres) coincide en los 32 meses; los contratos coinciden en el total de todos
# los meses y difieren en 1-2 contratos entre categorías en algunos (Argos lleva
# revisiones más recientes). Por eso Argos MANDA para Marbella y el SEPE queda:
#   · para el paro por edad y por sector, que Argos no da por municipio;
#   · para la comparativa con España (Argos solo cubre Andalucía);
#   · como respaldo si Argos no contesta.
ARGOS = "https://www.juntadeandalucia.es/servicioandaluzdeempleo/web/argos/"
_MESES_CAP = [m.capitalize() for m in _MESES_ES]

def _argos_filas(accion, y0, y1):
    """Filas de la tabla de resultados de una consulta municipal de Argos.

    El formulario es un POST con sesión (jsessionid en cookie): primero se abre la
    página para obtenerla y luego se envía la consulta para Marbella, de enero de
    y0 hasta diciembre de y1 (Argos devuelve solo los meses ya publicados).
    """
    import re, html as _html, http.cookiejar, urllib.parse, time
    datos = urllib.parse.urlencode({
        "provincia": PROV, "municipio": MUN,
        "mesInicio": "Enero", "anyoInicio": str(y0),
        "mesFin": "Diciembre", "anyoFin": str(y1), "accion": "Buscar"}).encode()
    last = None
    for intento in range(3):
        try:
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            op.addheaders = list(UA.items())
            op.open(ARGOS + accion, timeout=90).read()
            t = op.open(ARGOS + accion, datos, timeout=120).read().decode("latin-1")
            filas = []
            for tr in re.findall(r"<tr.*?</tr>", t, re.S):
                celdas = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                          for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
                if celdas:
                    filas.append(celdas)
            return filas
        except Exception as e:
            last = e
            if intento < 2:
                time.sleep(2.0 * (intento + 1))
    raise last

def _argos_n(s):
    """'6.246' → 6246 (Argos usa punto de millares)."""
    return iv((s or "").replace(".", ""))

def argos_marbella(y0, y1):
    """Paro (total/sexo) y contratos de Marbella según Argos: dos dicts {t: registro}."""
    paro, contr = {}, {}
    # Demanda: "Septiembre-2026" | demandantes H M T | DENOs H M T | PARADOS H M T | TEAS | otros
    for f in _argos_filas("demandaEmpleo.do", y0, y1):
        if len(f) >= 10 and "-" in f[0] and f[0].split("-")[0] in _MESES_CAP:
            mes, anyo = f[0].split("-")
            t = f"{anyo}-{_MESES_CAP.index(mes) + 1:02d}"
            paro[t] = {"total": _argos_n(f[9]),
                       "hombres": _argos_n(f[7]), "mujeres": _argos_n(f[8])}
    # Contratos: Mes | Año | IH TH IM TM | IA TA | II TI | IC TC | IS TS | TotIndef TotTemp Total
    for f in _argos_filas("buscarContratos.do", y0, y1):
        if len(f) >= 17 and f[0] in _MESES_CAP and f[1].isdigit():
            t = f"{f[1]}-{_MESES_CAP.index(f[0]) + 1:02d}"
            v = [_argos_n(x) for x in f[2:17]]
            contr[t] = {"t": t, "total": v[14],
                "indefinidos": v[12], "temporales": v[13],
                "indef_h": v[0], "temp_h": v[1], "indef_m": v[2], "temp_m": v[3],
                "sectores": {"agricultura": v[4] + v[5], "industria": v[6] + v[7],
                             "construccion": v[8] + v[9], "servicios": v[10] + v[11]},
                "fuente": "argos"}
    return paro, contr

def _argos_aplica(paro_mb, contr_mb, years):
    """Superpone Argos sobre lo bajado del SEPE. Devuelve (paro_mb, contr_mb).

    Paro: Argos fija total/hombres/mujeres; la edad y el sector se conservan del
    SEPE (de esta pasada o de lo ya publicado) y, si el SEPE aún no ha sacado ese
    mes, el registro va sin ellos hasta que lo saque.
    Contratos: el registro de Argos sustituye entero al del SEPE.
    Si Argos no contesta, no se toca nada: queda el SEPE como hasta ahora.
    """
    step("Paro y contratos de Marbella · Observatorio Argos (SAE, Junta de Andalucía)")
    try:
        a_paro, a_contr = argos_marbella(min(years), max(years))
    except Exception as e:
        print(f"    ! Argos no contesta ({e}): se queda el SEPE")
        return paro_mb, contr_mb
    if not a_paro and not a_contr:
        print("    ! Argos no ha devuelto filas: se queda el SEPE")
        return paro_mb, contr_mb

    publicados = {r["t"]: r for r in (_leer("paro_mensual.json") or {}).get("serie") or []
                  if isinstance(r, dict) and r.get("t")}
    sepe = {r["t"]: r for r in paro_mb}
    for t, a in a_paro.items():
        base = dict(publicados.get(t) or {})
        base.update(sepe.get(t) or {})
        base.update(a, t=t)
        base["fuente"] = "argos"
        sepe[t] = base
    paro_mb = list(sepe.values())

    cm = {r["t"]: r for r in contr_mb}
    cm.update(a_contr)
    contr_mb = list(cm.values())

    ult = lambda d: max(d) if d else "—"
    print(f"    · paro: {len(a_paro)} meses (último {ult(a_paro)}) · "
          f"contratos: {len(a_contr)} meses (último {ult(a_contr)})")
    return paro_mb, contr_mb

def sepe_laboral():
    """Descarga los CSV nacionales del SEPE (paro y contratos) y en una sola
    pasada extrae el detalle de Marbella y agrega España / Andalucía / Málaga
    para la comparativa territorial (misma metodología → totalmente comparable).
    El dato de Marbella se corrige después con Argos (ver _argos_aplica)."""
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
    # ----- ARGOS: fuente principal para Marbella (sale el mismo día que el dato) -----
    paro_mb, contr_mb = _argos_aplica(_dedup_sorted(paro_mb), _dedup_sorted(contr_mb), years)
    paro_mb = _dedup_sorted(paro_mb)
    contr_mb = _dedup_sorted(contr_mb)
    write_serie("paro_mensual.json", paro_mb)
    write_serie("contratos_mensual.json", contr_mb)

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
    write_serie("comparativa_laboral.json", comp)

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
    """Envoltorio: una fuente inalcanzable NO debe tumbar la recolección.

    Desde el 21-09-2026 la Junta descarta el tráfico que no viene de Europa y los
    runners de GitHub corren en Azure East US, así que estas llamadas mueren por
    timeout. Antes la excepción subía hasta main(), contaba como error y dejaba la
    pasada en rojo cada seis horas, pese a que los datos se publicaban igual y BADEA
    no tenía ningún mes nuevo que ofrecer.

    Ahora se hace lo mismo que con cualquier otra caída de fuente: conservar el
    último dato bueno y seguir. El aviso no se pierde, solo se aplaza a quien
    corresponde: si el bloqueo dura y la serie se queda de verdad atrás, el vigilante
    de frescura la marcará como obsoleta al pasar de 4 meses (_FRESCURA_MAX) y
    entonces sí saldrá en rojo.
    """
    try:
        _afiliacion_badea()
    except Exception as e:
        print(f"    ! {e}")
        print("    ⟲ afiliacion.json: la fuente no ha contestado; se CONSERVA lo publicado")
        write("afiliacion.json", {})


def _afiliacion_badea():
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

# ------------------------------------- SIMA (IECA): empleo por nacionalidad + natalidad
# Fichas municipales del IECA (operación b3_151, "Andalucía pueblo a pueblo"). Dan para
# Marbella lo que ninguna otra fuente oficial desagrega por municipio: paro, contratos
# y afiliación separando españoles y extranjeros, y el movimiento natural.
#
# La API solo filtra por periodo, no por territorio: cada petición trae TODOS los
# municipios andaluces de un año (hasta ~5 MB en el paro). Por eso se descarga el
# histórico una vez y después solo se piden los años que aún no están publicados.
SIMA_CONSULTAS = {
    "paro":      "37028",   # Paro registrado por nacionalidad y sexo (media anual)
    "contratos": "37138",   # Contratos registrados por nacionalidad y sexo (anual)
    "afiliados": "49401",   # Afiliaciones según municipio de TRABAJO, por nacionalidad (media anual)
    "mnp":       "22084",   # Nacimientos, defunciones y crecimiento vegetativo
}
# Indicadores de actividad local, todos con la misma forma (una categoría por fila):
SIMA_GENERICAS = {
    "matriculaciones": "1274",     # Vehículos matriculados por mes
    "vehiculos":       "1231",     # Parque de vehículos por tipo
    "electricidad":    "39837",    # Consumo de energía eléctrica por sectores (MWh)
    "establecimientos":"22591",    # Establecimientos por actividad (CNAE 09)
    "plazas_turisticas":"117646",  # Plazas en alojamientos turísticos por tipo (Registro de Turismo)
    "vft":             "117653",   # Viviendas con fines turísticos: viviendas y plazas
}
SIMA_CONSULTAS.update(SIMA_GENERICAS)
SIMA_DESDE = 2015

def _sima_categorias(filas):
    """{categoría: valor} (o lista si hay varias medidas) para una consulta genérica.

    Se localiza la columna del año (código de 4 cifras); la categoría es la
    dimensión inmediatamente anterior ('Hotel', 'Enero', 'Sector residencial'…) y
    se ignora el estado Provisional/Definitivo. Los valores van detrás del año."""
    rec = {}
    for r in filas:
        k = next((i for i, c in enumerate(r)
                  if len(c.get("cod") or []) == 1 and str(c["cod"][0]).isdigit()
                  and len(str(c["cod"][0])) == 4), None)
        if not k or k < 2:
            continue
        cat = r[k - 1].get("des")
        vals = [_sima_v(c) for c in r[k + 1:]]
        rec[cat] = vals[0] if len(vals) == 1 else vals
    return rec
_SIMA_GRUPOS = {"UE (15)": "ue15", "Resto UE": "resto_ue", "Resto Europa": "resto_europa",
                "África": "africa", "América latina": "america_latina",
                "Resto América": "resto_america", "Resto del mundo": "resto_mundo"}

def _sima_anios(cid):
    """{año: idNodo} de la dimensión temporal de una consulta SIMA."""
    j = get_json(f"{BADEA_REST}/jerarquia/2?consultaId={cid}&alias=D_TEMPORAL_0")
    out = {}
    def flat(n):
        for x in (n if isinstance(n, list) else [n]):
            c = str(x.get("cod") or "")
            if c.isdigit() and len(c) == 4:
                out[int(c)] = x.get("id")
            for ch in (x.get("children") or []):
                flat(ch)
    flat(j.get("data") or j)
    return out

def _sima_filas_marbella(cid, nodo):
    """Filas de Marbella de una consulta SIMA para un año (lista vacía si no hay)."""
    j = get_json(f"{BADEA_REST}/consulta/{cid}?D_TEMPORAL_0={nodo}")
    return [r for r in j.get("data", [])
            if (r[0].get("cod") or [""])[-1] == MUN]

def _sima_v(cell):
    try:
        return round(float(cell.get("val")))
    except (TypeError, ValueError, AttributeError):
        return None

def _sima_anio(clave, filas):
    """Convierte las filas de Marbella de un año en un registro {y, ...}."""
    if clave == "paro":          # territorio | sexo | nacionalidad | año | clase | valor
        rec = {"grupos": {}}
        for r in filas:
            if r[1].get("des") != "Ambos sexos":
                continue
            nac, v = r[2].get("des"), _sima_v(r[5])
            if nac == "TOTAL":      rec["total"] = v
            elif nac == "Española": rec["espanola"] = v
            elif nac in _SIMA_GRUPOS: rec["grupos"][_SIMA_GRUPOS[nac]] = v
        if rec.get("total") is not None and rec.get("espanola") is not None:
            rec["extranjera"] = rec["total"] - rec["espanola"]
        return rec
    if clave == "contratos":     # territorio | nacionalidad | sexo | año | ámbito | valor
        rec = {}
        for r in filas:
            if r[2].get("des") != "Ambos sexos":
                continue
            k = {"TOTAL": "total", "Española": "espanola", "Extranjera": "extranjera"}.get(r[1].get("des"))
            if k:
                rec[k] = _sima_v(r[5])
        return rec
    if clave == "afiliados":     # territorio | nacionalidad | año | valor
        rec = {}
        for r in filas:
            k = {"TOTAL": "total", "España": "espanola", "Extranjero": "extranjera"}.get(r[1].get("des"))
            if k:
                rec[k] = _sima_v(r[3])
        return rec
    if clave in SIMA_GENERICAS:  # territorio | [estado] | categoría | año | valor(es)
        return _sima_categorias(filas)
    if clave == "mnp":           # territorio | sexo | año | nacimientos | defunciones | crec. vegetativo
        for r in filas:
            if r[1].get("des") == "Ambos sexos":
                return {"nacimientos": _sima_v(r[3]), "defunciones": _sima_v(r[4]),
                        "crecimiento": _sima_v(r[5])}
        return {}
    return {}

def sima_marbella():
    step("SIMA · IECA (paro, contratos y afiliación por nacionalidad; nacimientos y defunciones)")
    prev = _leer("sima.json") or {}
    data = {}
    hoy = datetime.date.today().year
    for clave, cid in SIMA_CONSULTAS.items():
        serie = {r["y"]: r for r in (prev.get(clave) or []) if isinstance(r, dict) and r.get("y")}
        try:
            anios = _sima_anios(cid)
        except Exception as e:
            print(f"    ! {clave}: sin calendario ({e}); se conserva lo publicado")
            data[clave] = [serie[y] for y in sorted(serie)]
            continue
        # el último año publicado se vuelve a pedir (el IECA lo revisa al cerrar el
        # siguiente); los anteriores ya no cambian y no se vuelven a descargar
        ultimo = max(serie) if serie else SIMA_DESDE - 1
        pedir = [y for y in sorted(anios) if max(ultimo, SIMA_DESDE) <= y <= hoy]
        nuevos = []
        for y in pedir:
            try:
                rec = _sima_anio(clave, _sima_filas_marbella(cid, anios[y]))
            except Exception as e:
                print(f"    · {clave} {y}: {e}")
                continue
            if any(v is not None for k, v in rec.items() if k != "grupos"):
                rec["y"] = y
                serie[y] = rec
                nuevos.append(y)
        data[clave] = [serie[y] for y in sorted(serie)]
        ult = data[clave][-1] if data[clave] else {}
        print(f"    · {clave}: {len(data[clave])} años (último {ult.get('y', '—')})"
              + (f" · descargados {nuevos[0]}–{nuevos[-1]}" if nuevos else ""))
    data["ambito"] = {"afiliados": "Afiliaciones con lugar de TRABAJO en Marbella (media anual)",
                      "paro": "Paro registrado, media de los doce meses",
                      "contratos": "Contratos registrados en el año"}
    write("sima.json", data)

# ------------------------------------- VIVIENDA EN MARBELLA (MIVAU + INE alquiler)
# El Ministerio de Vivienda publica para los municipios grandes (>25.000 hab.) lo que
# el INE solo da por provincia: el número de compraventas (notarios) y el valor
# tasado del m2. Son ficheros .xls del boletín estadístico, trimestrales.
MIVAU = "https://apps.fomento.gob.es/BoletinOnline2/sedal/"
MIVAU_TRANS = {"total": "34010210", "nueva": "34010240", "segunda_mano": "34010250"}
MIVAU_TASADO = "35103500"

def _mivau_libro(codigo):
    import xlrd
    return xlrd.open_workbook(file_contents=_get(MIVAU + codigo + ".XLS", timeout=180))

def _mivau_transacciones(codigo):
    """Serie trimestral [{t: 'AAAA-MM', v}] de Marbella.

    Una sola hoja: filas = municipios, columnas = trimestres consecutivos desde el
    1T de 2004 (cabecera de años en la fila con 'Año 2004')."""
    import re
    sh = _mivau_libro(codigo).sheet_by_index(0)
    fila_anios = col0 = None
    for r in range(min(sh.nrows, 30)):
        for c, v in enumerate(sh.row_values(r)):
            m = re.match(r"\s*Año\s+(\d{4})", str(v))
            if m:
                fila_anios, col0, y0 = r, c, int(m.group(1))
                break
        if fila_anios is not None:
            break
    if fila_anios is None:
        raise ValueError("cabecera de trimestres no encontrada")
    for r in range(sh.nrows):
        if str(sh.cell_value(r, 1)).strip() == "Marbella" or str(sh.cell_value(r, 2)).strip() == "Marbella":
            out = []
            for i, v in enumerate(sh.row_values(r)[col0:]):
                if isinstance(v, (int, float)) and v != "":
                    y, q = y0 + i // 4, i % 4 + 1
                    out.append({"t": f"{y:04d}-{q*3:02d}", "v": int(v)})
            return out
    raise ValueError("Marbella no aparece")

def _mivau_tasado():
    """Valor tasado (€/m2) trimestral de Marbella: total, hasta 5 años y más de 5 años.

    Una hoja por trimestre ('T2A2026'); las columnas se localizan por la cabecera
    porque el diseño ha cambiado con los años."""
    import re
    out = {"total": [], "hasta5": [], "mas5": []}
    wb = _mivau_libro(MIVAU_TASADO)
    for sh in wb.sheets():
        m = re.match(r"\s*T(\d)A(\d{4})", sh.name)
        if not m:
            continue
        q, y = int(m.group(1)), int(m.group(2))
        if y < 2012:
            continue
        cols = None
        for r in range(min(sh.nrows, 30)):
            fila = [str(x).strip().lower() for x in sh.row_values(r)]
            if "total" in fila and any(x.startswith("hasta cinco") for x in fila):
                cols = {"total": fila.index("total"),
                        "hasta5": next(i for i, x in enumerate(fila) if x.startswith("hasta cinco")),
                        "mas5": next(i for i, x in enumerate(fila) if x.startswith("con más de cinco"))}
                break
        if not cols:
            continue
        for r in range(sh.nrows):
            if "Marbella" in [str(x).strip() for x in sh.row_values(r)[:4]]:
                fila = sh.row_values(r)
                for k, c in cols.items():
                    if isinstance(fila[c], (int, float)) and fila[c] != "":
                        out[k].append({"t": f"{y:04d}-{q*3:02d}", "v": round(float(fila[c]), 1)})
                break
    for k in out:
        out[k].sort(key=lambda x: x["t"])
    return out

def vivienda_marbella():
    step("Vivienda en Marbella · MIVAU (compraventas y valor tasado) + INE (alquiler)")
    prev = _leer("vivienda_marbella.json") or {}
    data = {}
    trans = {}
    for k, cod in MIVAU_TRANS.items():
        try:
            trans[k] = _mivau_transacciones(cod)
        except Exception as e:
            print(f"    ! compraventas {k}: {e}")
            trans[k] = (prev.get("compraventas") or {}).get(k) or []
    data["compraventas"] = trans
    try:
        data["valor_tasado"] = _mivau_tasado()
    except Exception as e:
        print(f"    ! valor tasado: {e}")
        data["valor_tasado"] = prev.get("valor_tasado") or {}
    # Índice de Precios de Vivienda en Alquiler (INE IPVA, tabla 59060): anual, municipios >10.000 hab.
    data["alquiler"] = {"indice": ine_anual("IPVA8735"), "var_anual": ine_anual("IPVA7172")}
    if not data["alquiler"]["indice"]:
        data["alquiler"] = prev.get("alquiler") or data["alquiler"]
    tt = trans.get("total") or []
    vt = (data["valor_tasado"] or {}).get("total") or []
    print(f"    · compraventas: {len(tt)} trimestres (último {tt[-1]['t'] if tt else '—'}) · "
          f"valor tasado: {len(vt)} (último {vt[-1]['t'] if vt else '—'})")
    write("vivienda_marbella.json", data)

# ------------------------------------- TURISMO MEDIDO CON MÓVILES (INE, op. TMOV)
# Estadística experimental del INE a partir de la posición de los teléfonos móviles:
# cuenta TODOS los turistas que pernoctan en Marbella (hotel, apartamento, vivienda
# turística, casa propia o de amigos), no solo los de hotel como la EOH.
#   · 52048: turistas extranjeros por municipio de destino y país de residencia
#   · 53464: turistas españoles de otras provincias por CCAA de origen
# Las tablas enteras superan el límite de la API ("restricciones de volumen"): hay
# que filtrar por la variable municipio (19) = Marbella (valor 2822).
TMOV_FILTRO = "tv=19:2822"
TMOV_TOP = 20     # países con serie propia; el resto se suma en "Otros"

def _tmov_series(tabla, idx_nombre, nult):
    """{nombre de la categoría: [{t, v}]} para Marbella en una tabla TMOV.

    La tabla de extranjeros (92 países) vuelve a chocar con el límite de volumen
    por encima de ~36 meses aunque se filtre el municipio: se piden los últimos
    meses y se funden con lo ya publicado (_tmov_funde) para no perder historia."""
    out = {}
    for s in get_json(f"{INE_TBL}{tabla}?{TMOV_FILTRO}&nult={nult}"):
        partes = [p.strip() for p in s.get("Nombre", "").split(".") if p.strip()]
        if len(partes) <= idx_nombre:
            continue
        pts = []
        for d in s.get("Data", []):
            if d.get("Valor") is None:
                continue
            y, m = ine_periodo(d["Anyo"], d.get("FK_Periodo"), d["Fecha"])
            pts.append({"t": f"{y:04d}-{m:02d}", "v": round(d["Valor"])})
        if pts:
            out[partes[idx_nombre]] = sorted(pts, key=lambda x: x["t"])
    return out

def _tmov_funde(nuevo, viejo):
    """Mezcla dos series [{t, v}]: manda lo recién bajado, se conserva lo antiguo."""
    m = {p["t"]: p for p in (viejo or [])}
    m.update({p["t"]: p for p in (nuevo or [])})
    return [m[t] for t in sorted(m)]

def turismo_moviles():
    step("Turistas en Marbella medidos con móviles · INE TMOV (52048 extranjeros, 53464 nacionales)")
    prev = _leer("turismo_moviles.json") or {}
    data = {}
    try:
        ser = _tmov_series("52048", 1, 36)      # "Turistas. Reino Unido. Marbella. Dato base."
        pi = prev.get("internacional") or {}
        for n, pts in (pi.get("paises") or {}).items():
            ser[n] = _tmov_funde(ser.get(n), pts)
        total = _tmov_funde(ser.pop("Total", []), pi.get("total"))
        def ult12(nombre):
            return sum(p["v"] for p in ser[nombre][-12:])
        top = sorted(ser, key=ult12, reverse=True)[:TMOV_TOP]
        otros = {}
        for nombre, pts in ser.items():
            if nombre in top:
                continue
            for p in pts:
                otros[p["t"]] = otros.get(p["t"], 0) + p["v"]
        data["internacional"] = {
            "total": total,
            "paises": {n: ser[n] for n in top},
            "otros": [{"t": t, "v": otros[t]} for t in sorted(otros)],
        }
    except Exception as e:
        print(f"    ! internacional: {e}")
        data["internacional"] = prev.get("internacional") or {}
    try:
        ser = _tmov_series("53464", 2, 120)     # "Dato base. Marbella. Andalucía. Turistas."
        data["nacional"] = {"total": ser.pop("Total Nacional", []), "origen": ser}
    except Exception as e:
        print(f"    ! nacional: {e}")
        data["nacional"] = prev.get("nacional") or {}
    it, nt = (data["internacional"] or {}).get("total") or [], (data["nacional"] or {}).get("total") or []
    print(f"    · internacional: {len(it)} meses (último {it[-1]['t'] if it else '—'}) · "
          f"nacional: {len(nt)} meses (último {nt[-1]['t'] if nt else '—'})")
    write("turismo_moviles.json", data)

# ------------------------------------- DEUDA VIVA DEL AYUNTAMIENTO (Ministerio de Hacienda)
# Excel anual por ayuntamiento a 31 de diciembre (miles de euros), desde 2021 en
# este formato. Las URL cambian de patrón de un año a otro, así que se leen de la
# página del Ministerio en vez de construirlas.
HACIENDA = "https://www.hacienda.gob.es"
DEUDA_PAGINA = HACIENDA + "/es-ES/CDI/Paginas/SistemasFinanciacionDeuda/InformacionEELLs/DeudaViva.aspx"

def deuda_viva():
    step("Deuda viva del Ayuntamiento · Ministerio de Hacienda")
    import re, openpyxl
    from urllib.parse import quote
    prev = {r["y"]: r for r in ((_leer("deuda.json") or {}).get("serie") or []) if r.get("y")}
    try:
        html = _get(DEUDA_PAGINA, timeout=90).decode("utf-8", "ignore")
    except Exception as e:
        print(f"    ! página de Hacienda: {e}")
        write("deuda.json", {"serie": [prev[y] for y in sorted(prev)]})
        return
    enlaces = {}
    for href in re.findall(r'href="([^"]*deuda-viva-ayuntamientos-(\d{4})[^"]*\.xlsx)"', html, re.I):
        enlaces[int(href[1])] = href[0]
    serie = dict(prev)
    for y, href in sorted(enlaces.items()):
        if y in prev and y < max(enlaces):       # los años cerrados no cambian
            continue
        url = href if href.startswith("http") else HACIENDA + quote(href, safe="/%")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(_get(url, timeout=120)), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            for fila in ws.iter_rows(values_only=True):
                txt = [str(x).strip() if x is not None else "" for x in fila]
                # hay otro "Marbella" fuera de Málaga? no, pero se exige la provincia
                if "Marbella" in txt and (PROV in txt or "29" in txt or "MALAGA" in txt):
                    # el importe es la primera cifra DETRÁS del nombre (los códigos
                    # de provincia y municipio van delante y en algún año son números)
                    i = txt.index("Marbella")
                    nums = [x for x in fila[i + 1:] if isinstance(x, (int, float))]
                    if nums:
                        serie[y] = {"y": y, "v": round(nums[0] * 1000)}   # miles € → €
                    break
        except Exception as e:
            print(f"    · {y}: {e}")
    s = [serie[y] for y in sorted(serie)]
    print(f"    · {len(s)} años (último {s[-1]['y'] if s else '—'}: "
          f"{round(s[-1]['v']/1e6, 1) if s else '—'} M€)")
    write("deuda.json", {"serie": s})

# ------------------------------------- HISTÓRICO LARGO DE ARGOS (para récords)
# Las notas de prensa del Ayuntamiento comparan con "el mismo mes desde 2007". El
# panel solo guarda tres años, pero Argos da el paro de Marbella desde 2006 y los
# contratos desde 2009 en una sola consulta: se guardan aparte para calcular récords.
def argos_historico():
    step("Histórico largo de paro y contratos · Argos (para comparar con todos los años)")
    prev = _leer("argos_historico.json") or {}
    hoy = datetime.date.today().year
    data = {}
    for clave, desde, idx in (("paro", 2006, 0), ("contratos", 2009, 1)):
        try:
            res = argos_marbella(desde, hoy)[idx]
            serie = [{"t": t, "v": r["total"]} for t, r in sorted(res.items()) if r.get("total")]
            data[clave] = _tmov_funde(serie, prev.get(clave))
        except Exception as e:
            print(f"    ! {clave}: {e}")
            data[clave] = prev.get(clave) or []
        s = data[clave]
        print(f"    · {clave}: {len(s)} meses ({s[0]['t'] if s else '—'} → {s[-1]['t'] if s else '—'})")
    write("argos_historico.json", data)

# ------------------------------------- FICHA DE DATOS PARA EL ASISTENTE DE IA
# El asistente del panel (Cloudflare Worker + Claude) NO calcula nada: recibe esta
# ficha con las cifras ya calculadas aquí, en Python, y se le prohíbe usar números
# que no estén en ella. Así una variación o un récord salen siempre de los datos y
# nunca de la memoria del modelo.
_MESES_NOM = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
              "septiembre", "octubre", "noviembre", "diciembre"]

def _n(v, d=0):
    """Número en formato español: 6246 → '6.246'; 10.18 → '10,2'."""
    if v is None:
        return "—"
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

def _pct(a, b):
    return (a - b) / b * 100 if a is not None and b else None

def _var(a, b, unidad=""):
    """'+383 (+6,5 %)' frente a un valor anterior."""
    if a is None or b is None:
        return "sin dato de comparación"
    d, p = a - b, _pct(a, b)
    return f"{'+' if d >= 0 else '−'}{_n(abs(d))}{unidad} ({'+' if p >= 0 else '−'}{_n(abs(p), 1)} %)"

def _mes(t):
    return f"{_MESES_NOM[int(t[5:7]) - 1]} de {t[:4]}"

def _por_t(serie):
    return {p["t"]: p for p in serie or [] if isinstance(p, dict) and p.get("t")}

def _ultimo(serie):
    s = [p for p in serie or [] if isinstance(p, dict)]
    return s[-1] if s else None

def _hace_un_anio(t):
    return f"{int(t[:4]) - 1:04d}{t[4:]}"

def _mes_anterior(t):
    y, m = int(t[:4]), int(t[5:7]) - 1
    return f"{y - (m == 0):04d}-{(m or 12):02d}"

def _record_mismo_mes(serie, t, mejor="min"):
    """Frase de récord del mes t frente al mismo mes de todos los años de la serie."""
    m = t[5:7]
    mismos = {p["t"][:4]: p["v"] for p in serie if p["t"][5:7] == m and p.get("v") is not None}
    y = t[:4]
    if y not in mismos or len(mismos) < 3:
        return None
    v = mismos[y]
    anteriores = sorted((a for a in mismos if a < y), reverse=True)
    mejor_que = (lambda x: x < v) if mejor == "min" else (lambda x: x > v)
    for a in anteriores:
        if mejor_que(mismos[a]):
            if a == anteriores[0]:
                return None          # el año pasado ya fue mejor: no hay récord
            return (f"el {'menor' if mejor == 'min' else 'mayor'} dato de un mes de "
                    f"{_MESES_NOM[int(m) - 1]} desde {a}, cuando hubo {_n(mismos[a])}")
    return (f"el {'menor' if mejor == 'min' else 'mayor'} dato de un mes de "
            f"{_MESES_NOM[int(m) - 1]} de toda la serie disponible (desde {min(mismos)})")

def contexto_ia():
    step("Ficha de datos para el asistente de IA")
    L = lambda n: _leer(n) or {}
    H = []           # hechos: {"tema", "periodo", "texto", "fuente"}

    def hecho(tema, periodo, texto, fuente):
        H.append({"tema": tema, "periodo": periodo, "texto": texto, "fuente": fuente})

    # --- Paro y contratos (Argos/SEPE) ---
    pm, hist = (L("paro_mensual.json").get("serie") or []), L("argos_historico.json")
    up = _ultimo(pm)
    if up:
        t, P = up["t"], _por_t(pm)
        ant, ia = P.get(_mes_anterior(t)), P.get(_hace_un_anio(t))
        txt = (f"Paro registrado en Marbella en {_mes(t)}: {_n(up['total'])} personas "
               f"({_n(up.get('hombres'))} hombres y {_n(up.get('mujeres'))} mujeres). "
               f"Frente al mes anterior: {_var(up['total'], ant and ant['total'])}. "
               f"Frente al mismo mes del año anterior: {_var(up['total'], ia and ia['total'])}.")
        rec = _record_mismo_mes(hist.get("paro") or [], t, "min")
        if rec:
            txt += f" Es {rec}."
        if up.get("sectores"):
            s = up["sectores"]
            txt += (f" Por sector: servicios {_n(s.get('servicios'))}, construcción "
                    f"{_n(s.get('construccion'))}, industria {_n(s.get('industria'))}, "
                    f"agricultura {_n(s.get('agricultura'))}, sin empleo anterior {_n(s.get('sin_empleo'))}.")
        hecho("Paro registrado", t, txt, "Observatorio Argos (SAE) y SEPE")
    cm = L("contratos_mensual.json").get("serie") or []
    uc = _ultimo(cm)
    if uc:
        t, C = uc["t"], _por_t(cm)
        ant, ia = C.get(_mes_anterior(t)), C.get(_hace_un_anio(t))
        pi = uc["indefinidos"] / uc["total"] * 100 if uc.get("total") else None
        txt = (f"Contratos registrados en Marbella en {_mes(t)}: {_n(uc['total'])}, de ellos "
               f"{_n(uc.get('indefinidos'))} indefinidos ({_n(pi, 1)} %) y {_n(uc.get('temporales'))} temporales. "
               f"Frente al mes anterior: {_var(uc['total'], ant and ant['total'])}. "
               f"Frente al mismo mes del año anterior: {_var(uc['total'], ia and ia['total'])}.")
        rec = _record_mismo_mes(hist.get("contratos") or [], t, "max")
        if rec:
            txt += f" Es {rec}."
        se = uc.get("sectores") or {}
        txt += f" Servicios concentra {_n(se.get('servicios'))} contratos y construcción {_n(se.get('construccion'))}."
        hecho("Contratación", t, txt, "Observatorio Argos (SAE)")
    pa = L("paro_anual.json").get("total")
    if pa:
        hecho("Paro medio anual", pa["y"], f"Paro registrado medio en Marbella en {pa['y']}: {_n(pa['v'])} personas.", "SEPE")

    # --- Afiliación ---
    af = ((L("afiliacion.json").get("marbella") or {}).get("total")) or []
    af = [p for p in af if p.get("v") is not None]
    if af:
        u, A = af[-1], _por_t(af)
        ia = A.get(_hace_un_anio(u["t"]))
        hecho("Afiliación a la Seguridad Social", u["t"],
              f"Afiliados a la Seguridad Social residentes en Marbella en {_mes(u['t'])}: {_n(u['v'])}. "
              f"Frente al año anterior: {_var(u['v'], ia and ia['v'])}.", "IECA a partir de la TGSS")

    # --- Españoles y extranjeros (SIMA, ECP) ---
    S = L("sima.json")
    for clave, nombre, extra in (("paro", "Paro medio anual por nacionalidad", ""),
                                 ("contratos", "Contratos anuales por nacionalidad", ""),
                                 ("afiliados", "Afiliación media anual por nacionalidad (lugar de trabajo en Marbella)", "")):
        u = _ultimo(S.get(clave))
        if u and u.get("total"):
            hecho(nombre, u["y"],
                  f"{nombre} en {u['y']}: total {_n(u['total'])}; españoles {_n(u.get('espanola'))}; "
                  f"extranjeros {_n(u.get('extranjera'))} ({_n((u.get('extranjera') or 0) / u['total'] * 100, 1)} %).",
                  "IECA · SIMA")
    D = L("demografia.json")
    R = D.get("residentes") or {}
    ne, nx = _ultimo(R.get("nac_espanola")), _ultimo(R.get("nac_extranjera"))
    if ne and nx:
        tot = ne["v"] + nx["v"]
        nb = _ultimo(R.get("nacidos_extranjero"))
        hecho("Población", ne["y"],
              f"Población residente en Marbella a 1 de enero de {ne['y']}: {_n(tot)} habitantes; "
              f"{_n(nx['v'])} de nacionalidad extranjera ({_n(nx['v'] / tot * 100, 1)} %)"
              + (f" y {_n(nb['v'])} nacidos fuera de España ({_n(nb['v'] / tot * 100, 1)} %)." if nb else "."),
              "INE · Estadística Continua de Población")
    M = D.get("migraciones") or {}
    se, si = _ultimo(M.get("saldo_exterior")), _ultimo(M.get("saldo_interior"))
    if se:
        hecho("Migraciones", se["y"],
              f"Saldo migratorio de Marbella en {se['y']}: {_n(se['v'])} con el extranjero"
              + (f" y {_n(si['v'])} con el resto de España." if si else "."), "INE · Estadística de Migraciones")

    # --- Turismo ---
    T = L("turismo.json").get("hoteles") or {}
    uv = _ultimo(T.get("viajeros"))
    if uv:
        t, V = uv["t"], _por_t(T.get("viajeros"))
        ia = V.get(_hace_un_anio(t))
        ex, es = _por_t(T.get("viajeros_ext")).get(t), _por_t(T.get("viajeros_esp")).get(t)
        per, adr = _por_t(T.get("pernoctaciones")).get(t), _por_t(T.get("adr")).get(t)
        oc = _por_t(T.get("ocup_plazas")).get(t)
        txt = (f"Hoteles de Marbella en {_mes(t)}: {_n(uv['v'])} viajeros ({_var(uv['v'], ia and ia['v'])} interanual)")
        if per: txt += f", {_n(per['v'])} pernoctaciones"
        if ex and es: txt += f"; el {_n(ex['v'] / (ex['v'] + es['v']) * 100, 1)} % residentes en el extranjero"
        if adr: txt += f"; tarifa media diaria (ADR) {_n(adr['v'], 1)} €"
        if oc: txt += f"; ocupación por plazas {_n(oc['v'], 1)} %"
        hecho("Turismo hotelero", t, txt + ".", "INE · Encuesta de Ocupación Hotelera")
    TM = L("turismo_moviles.json")
    it = (TM.get("internacional") or {}).get("total") or []
    if len(it) >= 12:
        paises = sorted(((n, sum(p["v"] for p in a[-12:])) for n, a in ((TM.get("internacional") or {}).get("paises") or {}).items()),
                        key=lambda x: -x[1])[:5]
        hecho("Turistas internacionales (todos los alojamientos)", it[-1]["t"],
              f"Turistas extranjeros que pernoctaron en Marbella en los 12 meses hasta {_mes(it[-1]['t'])}: "
              f"{_n(sum(p['v'] for p in it[-12:]))}. Principales países: "
              + ", ".join(f"{n} ({_n(v)})" for n, v in paises) + ".",
              "INE · turismo medido con teléfonos móviles (experimental)")
    vf = _ultimo(S.get("vft"))
    if vf and isinstance(vf.get("Viviendas con fines turísticos"), list):
        v = vf["Viviendas con fines turísticos"]
        hecho("Viviendas con fines turísticos", vf["y"],
              f"Viviendas con fines turísticos inscritas en Marbella en {vf['y']}: {_n(v[0])}, con {_n(v[1])} plazas.",
              "IECA · Registro de Turismo de Andalucía")

    # --- Empresas y actividad ---
    E = L("empresas.json")
    ue = _ultimo(E.get("total"))
    if ue:
        prev = [p for p in E["total"] if p["y"] == ue["y"] - 1]
        hecho("Empresas", ue["y"], f"Empresas activas en Marbella en {ue['y']}: {_n(ue['v'])} "
              f"({_var(ue['v'], prev[0]['v'] if prev else None)} frente al año anterior).", "INE · DIRCE")
    es_ = _ultimo(S.get("establecimientos"))
    if es_ and es_.get("TOTAL"):
        hecho("Establecimientos", es_["y"], f"Establecimientos con actividad económica en Marbella en {es_['y']}: {_n(es_['TOTAL'])}.",
              "IECA · Directorio de establecimientos")
    mt = _ultimo(S.get("matriculaciones"))
    if mt:
        tot = sum(mt.get(m.capitalize()) or 0 for m in _MESES_NOM)
        hecho("Matriculaciones", mt["y"], f"Vehículos matriculados en Marbella en {mt['y']}: {_n(tot)}.", "IECA a partir de la DGT")

    # --- Vivienda ---
    VM = L("vivienda_marbella.json")
    cv = (VM.get("compraventas") or {}).get("total") or []
    if cv:
        u, ia = cv[-1], (cv[-5] if len(cv) > 4 else None)
        tr = f"{int(u['t'][5:7]) // 3}.º trimestre de {u['t'][:4]}"
        hecho("Compraventa de vivienda", u["t"], f"Compraventas de vivienda en Marbella en el {tr}: {_n(u['v'])} "
              f"({_var(u['v'], ia and ia['v'])} interanual).", "Ministerio de Vivienda (notarios)")
    vt = (VM.get("valor_tasado") or {}).get("total") or []
    if vt:
        u, ia = vt[-1], (vt[-5] if len(vt) > 4 else None)
        tr = f"{int(u['t'][5:7]) // 3}.º trimestre de {u['t'][:4]}"
        hecho("Precio de la vivienda", u["t"], f"Valor tasado medio de la vivienda libre en Marbella en el {tr}: "
              f"{_n(u['v'])} €/m² ({_var(u['v'], ia and ia['v'], ' €/m²')} interanual).", "Ministerio de Vivienda")
    al = _ultimo((VM.get("alquiler") or {}).get("var_anual"))
    if al:
        hecho("Alquiler", al["y"], f"El precio del alquiler de vivienda en Marbella subió un {_n(al['v'], 1)} % en {al['y']}.",
              "INE · Índice de Precios de Vivienda en Alquiler")

    # --- Renta, precios y hacienda local ---
    rn = _ultimo(L("renta.json").get("neta_persona"))
    if rn:
        hecho("Renta", rn["y"], f"Renta neta media por persona en Marbella en {rn['y']}: {_n(rn['v'])} €.", "INE · Atlas de distribución de renta")
    ipc = _ultimo(((L("coyuntura.json").get("ipc")) or {}).get("var_anual"))
    if ipc:
        hecho("Precios", ipc["t"], f"Inflación anual en Andalucía en {_mes(ipc['t'])}: {_n(ipc['v'], 1)} %.", "INE · IPC")
    dv = _ultimo(L("deuda.json").get("serie"))
    if dv:
        hecho("Deuda municipal", dv["y"], f"Deuda viva del Ayuntamiento de Marbella a 31 de diciembre de {dv['y']}: "
              f"{_n(dv['v'] / 1e6, 1)} millones de euros.", "Ministerio de Hacienda")

    data = {"generado": datetime.date.today().isoformat(), "municipio": "Marbella", "hechos": H}
    print(f"    · {len(H)} hechos")
    write("contexto_ia.json", data)

# ---------------------------------------------------------------- VIGILANTE DE FRESCURA
# Desfase máximo tolerado (en meses) antes de avisar de que un indicador se ha quedado
# obsoleto. Sirve para cazar "series muertas" del INE (que renumera y congela códigos)
# sin que nadie lo descubra por casualidad. Los anuales llevan una tolerancia alta.
#
# El tope tiene que caber el CICLO ENTERO de publicación de su fuente, no el desfase
# del día que se escribió. Si se ajusta a lo que hay hoy, el vigilante se convierte en
# un despertador: pita todos los días de los últimos meses de cada ciclo, sin que pase
# nada, y a base de avisos falsos deja de mirarse. Entonces, el día que algo se rompe
# de verdad, el aviso ya no lo lee nadie.
#
# Atlas de Distribución de Renta del INE (tablas 30824, 30831 y 30832): publica el año
# N alrededor de DICIEMBRE DE N+2. El ejercicio 2023 salió en diciembre de 2025 y sigue
# siendo el último publicado —comprobado contra la tabla 30824, que para Marbella da
# 13.384 € de renta neta por persona en 2023 y nada posterior—; el 2024 no llegará
# hasta diciembre de 2026. El desfase de estas series, por tanto, CRECE hasta unos 36
# meses justo antes de cada entrega y vuelve a caer a 24 el día que sale. Con el tope
# en 32 la ejecución se ponía en rojo cada seis horas durante el último tercio del
# ciclo aunque el recolector funcionase perfectamente.
_ATLAS_INE = 40   # 36 del ciclo + margen por si el INE retrasa la entrega

_FRESCURA_MAX = {
    "paro_mensual.json": 2, "contratos_mensual.json": 2, "comparativa_laboral.json": 3,
    "afiliacion.json": 4, "sociedades.json": 4, "turismo.json": 3, "vivienda.json": 7,
    "coyuntura.json": 3,
    "paro_anual.json": 16, "empresas.json": 20,
    "renta.json": _ATLAS_INE, "demografia.json": 14,
    # sima.json: medias anuales del IECA; el año N completo sale en el 1T de N+1
    "sima.json": 16,
    # vivienda_marbella.json: MIVAU trimestral, ~3 meses tras cerrar el trimestre
    "vivienda_marbella.json": 7,
    # turismo_moviles.json: el INE publica el receptor (extranjeros) con ~10 meses de
    # retraso y el interno con ~3; el tope del fichero cubre el más lento
    "turismo_moviles.json": 14,
    # deuda.json: Hacienda publica el 31-12 del año N a mediados de N+1
    "deuda.json": 20,
}

# Excepciones POR SERIE dentro de un fichero. Hacen falta cuando en el mismo JSON
# conviven fuentes con calendarios distintos: demografia.json mezcla el Padrón —anual
# con ~9 meses de desfase— con la estructura por edad y hogares del Atlas, que va dos
# años por detrás. Un tope único obliga a elegir entre tolerar el Atlas (y no enterarse
# si el Padrón se congela) o vigilar el Padrón (y pitar por el Atlas todos los días).
# Viviendas turísticas (INE, op. VTE): el INE la publica con MUCHO más retraso que
# la encuesta hotelera -en septiembre de 2026 el último dato es mayo-, y las tres
# series conviven en turismo.json con la EOH, que va a un mes. Sin esta excepción,
# cada vez que entra un mes nuevo de hoteles las VUT quedan 3 meses por detrás de
# sus hermanas y el vigilante las declara muertas sin estarlo: pasó el 22-09-2026,
# al recogerse agosto. El tope propio se mide contra el calendario REAL de la fuente.
_VUT_INE = 7      # ~4 meses de desfase habitual + margen

# Migraciones (INE, op. EMCR): el año N se publica a mediados de N+2 (en octubre de
# 2026 el último es 2024), así que el desfase sube a ~30 meses antes de cada entrega.
_EMCR_INE = 32
# Movimiento natural del IECA: mismo ciclo, el año N llega a finales de N+1 o en N+2.
_MNP_IECA = 30
# Índice de alquiler del INE (IPVA): fuente fiscal, el año N sale a finales de N+2.
_IPVA_INE = 38

_FRESCURA_SERIE = {
    # el directorio de establecimientos del IECA va un año por detrás del resto de SIMA
    "sima.json": {"mnp": _MNP_IECA, "establecimientos": _MNP_IECA},
    "vivienda_marbella.json": {"alquiler.indice": _IPVA_INE, "alquiler.var_anual": _IPVA_INE},
    "turismo.json": {
        "vut.viviendas": _VUT_INE, "vut.plazas": _VUT_INE, "vut.pct_viviendas": _VUT_INE,
    },
    "demografia.json": {
        "edad_media": _ATLAS_INE, "pct_menor18": _ATLAS_INE, "pct_mayor65": _ATLAS_INE,
        "pct_espanola": _ATLAS_INE, "tamano_hogar": _ATLAS_INE,
        "pct_unipersonales": _ATLAS_INE,
        **{f"migraciones.{k}": _EMCR_INE for k in (
            "inmig_extranjero", "emig_extranjero", "saldo_total", "saldo_exterior",
            "saldo_interior", "inter_in_esp", "inter_in_ext", "inter_out_esp", "inter_out_ext")},
    },
}


def _tope_de(name, ruta=None):
    """Meses tolerados para un fichero, o para una serie concreta dentro de él."""
    base = _FRESCURA_MAX.get(name, 6)
    if ruta is None:
        return base
    return _FRESCURA_SERIE.get(name, {}).get(ruta, base)

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
        # contexto_ia.json es un derivado (resumen para el asistente), no una fuente
        if not name.endswith(".json") or name in ("meta.json", "contexto_ia.json"):
            continue
        try:
            obj = json.load(io.open(os.path.join(OUT, name), encoding="utf-8"))
        except Exception:
            continue
        ult = _ultimo_periodo(obj)
        desf = _meses_desde(ult, hoy)
        tope = _tope_de(name)
        obsoleto = desf > tope

        # Y ahora serie a serie: una serie muerta dentro de un fichero por lo demás
        # fresco es el caso que hay que cazar, y el que el fichero entero disimula.
        rezagadas = {}
        for ruta, periodo in sorted(_series_del_fichero(obj).items()):
            atraso = _meses_desde(periodo, hoy)
            # Se compara con la serie más fresca del propio fichero: si una va muy
            # por detrás de sus hermanas, es que su código de origen ha muerto. El
            # tope es el de la serie, que puede no ser el del fichero cuando dentro
            # conviven fuentes con calendarios distintos.
            if atraso > _tope_de(name, ruta) and atraso - desf >= 3:
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
    return rep, alertas

# ---------------------------------------------------------------- MAIN
def main():
    print("== Observatorio Económico Marbella · recolección de datos ==")
    errors = 0
    # paro_anual va detrás de sepe_laboral: se calcula con los meses que aquél escribe.
    for fn in (turismo, renta, demografia, empresas, vivienda, coyuntura, sociedades,
               afiliacion, sepe_laboral, paro_anual, sima_marbella, vivienda_marbella,
               turismo_moviles, deuda_viva, argos_historico,
               contexto_ia):          # contexto_ia, el último: resume todo lo anterior
        try:
            fn()
        except Exception as e:
            errors += 1
            print(f"    !! fallo en {fn.__name__}: {e}")
    try:
        frescura, alertas = auditar_frescura()
    except Exception as e:
        frescura, alertas = {}, []
        print(f"    !! fallo en auditar_frescura: {e}")
    meta = {
        "generado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuentes": ["INE Tempus3", "IECA/BADEA (afiliación SS y SIMA)",
                    "Observatorio Argos (SAE)", "SEPE datos abiertos",
                    "MIVAU (compraventas y valor tasado municipal)",
                    "INE TMOV (turismo medido con móviles)", "Ministerio de Hacienda (deuda viva)"],
        "municipio": "Marbella (29069)",
        "ambito_comparativa": "Marbella · Málaga (29) · Andalucía · España",
        "frescura": frescura,
        # Fuentes que no han contestado en esta pasada: el dato que se ve es el
        # último bueno, no uno nuevo. Se informa, pero NO se marca la ejecución en
        # rojo: una caída pasajera del SEPE no es un indicador muerto.
        "conservados": list(CONSERVADOS),
    }
    write("meta.json", meta)
    if CONSERVADOS:
        print("\n== Fuentes caídas en esta pasada (se conserva el último dato bueno): "
              + ", ".join(CONSERVADOS) + " ==")
    print(f"\n== Completado. Fallos: {errors} ==")
    if errors:
        return 1
    if alertas:
        # Los datos ya están escritos y se publican igualmente: lo que se ha
        # quedado atrás es una serie suelta, no la recolección. Pero se sale en
        # rojo a propósito, porque un código de origen muerto NO se manifiesta
        # de ninguna otra forma -el panel sigue pintando la última cifra buena-
        # y así fue como el precio de la vivienda pasó once meses congelado.
        # El Action publica los datos y DESPUÉS marca la ejecución como fallida,
        # para que GitHub avise por correo.
        print("== Series congeladas: se marca la ejecución en rojo para avisar ==")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
