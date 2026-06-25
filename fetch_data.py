#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Observatorio Económico de Marbella — recolector de datos dinámicos.

Descarga TODAS las fuentes (INE, IECA/BADEA, SEPE) y escribe ficheros JSON en data/.
El HTML del observatorio lee esos JSON (mismo origen → sin problemas de CORS).
Pensado para ejecutarse solo en GitHub Actions (ver .github/workflows/update.yml),
pero funciona igual en local:  python fetch_data.py

No requiere dependencias externas: usa solo la librería estándar de Python.
Marbella = municipio INE 29069 · nodo BADEA 2980.
"""
import json, os, sys, io, csv, urllib.request, urllib.error, datetime

try:                       # consola UTF-8 (Windows usa cp1252 por defecto)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)
MUN = "29069"           # código INE de Marbella
BADEA_MARBELLA = "2980" # nodo interno de Marbella en la jerarquía de territorio de BADEA
UA = {"User-Agent": "Mozilla/5.0 (ObservatorioMarbella; +github-actions)"}

def _get(url, timeout=60):
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

LOG = []
def step(title):
    print(f"\n▶ {title}")
    LOG.append(title)

# ---------------------------------------------------------------- INE (Tempus3)
INE = "https://servicios.ine.es/wstempus/js/ES/DATOS_SERIE/"
INE_TBL = "https://servicios.ine.es/wstempus/js/ES/DATOS_TABLA/"

def ine_serie(cod, nult=300):
    try:
        j = get_json(f"{INE}{cod}?nult={nult}")
        pts = [[d["Anyo"], d.get("FK_Periodo"), d["Fecha"], d["Valor"]]
               for d in j.get("Data", []) if d.get("Valor") is not None]
        return pts
    except Exception as e:
        print(f"    ! INE serie {cod}: {e}")
        return []

def ine_mensual(cod, nult=300):
    """Devuelve serie mensual [{t:'AAAA-MM', v:valor}] usando la fecha en ms."""
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

# ---------------------------------------------------------------- TURISMO (EOH Marbella)
def turismo():
    step("Turismo · INE EOH (Marbella)")
    cods = {                      # códigos validados a nivel Marbella (punto turístico)
        "viajeros":      "EOT42428",
        "pernoctaciones":"EOT42534",
        "adr":           "EOT43542",  # tarifa media diaria (ADR)
        "revpar":        "EOT43946",  # ingreso por habitación disponible
        "ocup_plazas":   "EOT3152",
        "personal":      "EOT3296",
    }
    data = {k: ine_mensual(c, 360) for k, c in cods.items()}
    write("turismo.json", data)
    return data

# ---------------------------------------------------------------- RENTA (Atlas INE, municipal)
def renta():
    step("Renta · INE Atlas (tabla 30824, Marbella)")
    try:
        j = get_json(f"{INE_TBL}30824?tv=19:2822&det=2")
    except Exception as e:
        print(f"    ! {e}"); write("renta.json", {}); return
    def pick(name):
        s = next((x for x in j if name.lower() in x["Nombre"].lower()), None)
        if not s: return []
        pts = [{"y": int(p["Anyo"]), "v": p["Valor"]}
               for p in s["Data"] if p.get("Valor") is not None]
        pts.sort(key=lambda x: x["y"])
        return pts
    data = {
        "neta_persona": pick("Renta neta media por persona"),
        "neta_hogar":   pick("Renta neta media por hogar"),
        "bruta_persona":pick("Renta bruta media por persona"),
    }
    write("renta.json", data)

# ---------------------------------------------------------------- EMPRESAS (DIRCE INE, municipal)
def empresas():
    step("Empresas · INE DIRCE (tabla 4721, Marbella)")
    try:
        j = get_json(f"{INE_TBL}4721?tv=19:2822&det=2")
    except Exception as e:
        print(f"    ! {e}"); write("empresas.json", {}); return
    tot = next((s for s in j if "total cnae" in s["Nombre"].lower()), None)
    serie = []
    if tot:
        serie = [{"y": int(p["Anyo"]), "v": round(p["Valor"])}
                 for p in tot["Data"] if p.get("Valor") is not None]
        serie.sort(key=lambda x: x["y"])
    write("empresas.json", {"total": serie})

# ---------------------------------------------------------------- PARO ANUAL (BADEA, municipal)
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

# ---------------------------------------------------------------- PARO MENSUAL (SEPE, municipal)
def _sepe_csv(url):
    raw = _get(url, timeout=120)
    text = raw.decode("latin-1")
    return list(csv.reader(io.StringIO(text), delimiter=";"))

def paro_sepe():
    step("Paro registrado mensual · SEPE (municipal, CSV)")
    year = datetime.date.today().year
    rows_out = []
    for y in (year, year-1, year-2):
        url = ("https://sede.sepe.gob.es/es/portaltrabaja/resources/sede/"
               f"datos_abiertos/datos/Paro_por_municipios_{y}_csv.csv")
        try:
            rows = _sepe_csv(url)
        except Exception as e:
            print(f"    · {y}: no disponible ({e})"); continue
        n = 0
        for r in rows:
            if len(r) < 18: continue
            cod = (r[6] or "").strip()
            if cod != MUN: continue
            try:
                codmes = (r[0] or "").strip()           # AAAAMM
                t = f"{codmes[:4]}-{codmes[4:6]}"
                total = int(r[8])
                h = int(r[9]) + int(r[10]) + int(r[11])
                m = int(r[12]) + int(r[13]) + int(r[14])
                sectores = {
                    "agricultura":  int(r[15]),
                    "industria":    int(r[16]),
                    "construccion": int(r[17]),
                    "servicios":    int(r[18]) if len(r) > 18 else None,
                    "sin_empleo":   int(r[19]) if len(r) > 19 else None,
                }
                rows_out.append({"t": t, "total": total, "hombres": h,
                                 "mujeres": m, "sectores": sectores})
                n += 1
            except (ValueError, IndexError):
                continue
        print(f"    · {y}: {n} meses de Marbella")
    rows_out.sort(key=lambda x: x["t"])
    # dedup por mes (por si solapan ficheros)
    seen, dedup = set(), []
    for r in rows_out:
        if r["t"] in seen: continue
        seen.add(r["t"]); dedup.append(r)
    write("paro_mensual.json", {"serie": dedup})
    return dedup

# ---------------------------------------------------------------- CONTRATOS (SEPE, municipal)
def contratos_sepe():
    step("Contratos registrados mensual · SEPE (municipal, CSV)")
    year = datetime.date.today().year
    out = []
    for y in (year, year-1, year-2):
        url = ("https://sede.sepe.gob.es/es/portaltrabaja/resources/sede/"
               f"datos_abiertos/datos/Contratos_por_municipios_{y}_csv.csv")
        try:
            rows = _sepe_csv(url)
        except Exception as e:
            print(f"    · {y}: no disponible ({e})"); continue
        n = 0
        for r in rows:
            if len(r) < 9: continue
            if (r[6] or "").strip() != MUN: continue
            try:
                codmes = (r[0] or "").strip()
                out.append({"t": f"{codmes[:4]}-{codmes[4:6]}", "total": int(r[8])})
                n += 1
            except (ValueError, IndexError):
                continue
        print(f"    · {y}: {n} meses")
    out.sort(key=lambda x: x["t"])
    seen, dd = set(), []
    for r in out:
        if r["t"] in seen: continue
        seen.add(r["t"]); dd.append(r)
    write("contratos_mensual.json", {"serie": dd})

# ---------------------------------------------------------------- MAIN
def main():
    print("== Observatorio Económico Marbella · recolección de datos ==")
    errors = 0
    for fn in (turismo, renta, empresas, paro_badea, paro_sepe, contratos_sepe):
        try:
            fn()
        except Exception as e:
            errors += 1
            print(f"    !! fallo en {fn.__name__}: {e}")
    meta = {
        "generado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuentes": ["INE Tempus3", "IECA/BADEA", "SEPE datos abiertos"],
        "municipio": "Marbella (29069)",
    }
    write("meta.json", meta)
    print(f"\n== Completado. Fallos: {errors} ==")
    return 0 if errors == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
