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

def _get(url, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

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

def ine_mensual(cod, nult=400):
    out = []
    for anyo, per, fecha, val in ine_serie(cod, nult):
        d = datetime.datetime.fromtimestamp(fecha/1000, datetime.timezone.utc)
        out.append({"t": f"{d.year:04d}-{d.month:02d}", "v": val})
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
    step("Demografía · INE Atlas (tabla 30832, Marbella)")
    try:
        s = tabla_series("30832", "19:2822")
    except Exception as e:
        print(f"    ! demografía 30832: {e}"); write("demografia.json", {}); return
    data = {
        "poblacion":        serie_anual_from(s, "marbella. población."),
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
    ipv  = {"indice": "IPV766", "var_anual": "IPV939",
            "indice_nueva": "IPV765", "indice_segunda": "IPV764"}
    data = {
        "compraventa": {k: ine_mensual(c) for k, c in comp.items()},
        "precio":      {k: ine_mensual(c) for k, c in ipv.items()},
        "ambito": {"compraventa": "provincia de Málaga", "precio": "Andalucía"},
    }
    write("vivienda.json", data)

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
    write("paro_mensual.json", {"serie": paro_mb})

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

# ---------------------------------------------------------------- MAIN
def main():
    print("== Observatorio Económico Marbella · recolección de datos ==")
    errors = 0
    for fn in (turismo, renta, demografia, empresas, vivienda, paro_badea, sepe_laboral):
        try:
            fn()
        except Exception as e:
            errors += 1
            print(f"    !! fallo en {fn.__name__}: {e}")
    meta = {
        "generado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuentes": ["INE Tempus3", "IECA/BADEA", "SEPE datos abiertos"],
        "municipio": "Marbella (29069)",
        "ambito_comparativa": "Marbella · Málaga (29) · Andalucía · España",
    }
    write("meta.json", meta)
    print(f"\n== Completado. Fallos: {errors} ==")
    return 0 if errors == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
