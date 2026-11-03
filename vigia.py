"""Vigía de fuentes del Observatorio Económico de Marbella.

Pregunta a cada fuente, con una consulta mínima, cuál es el último mes que tiene
publicado y lo compara con el que ya está en data/. Si alguna va por delante,
devuelve los grupos a refrescar para que el Action lance
`python fetch_data.py --solo <grupos>` en ese mismo momento.

Así cada dato entra cuando la fuente lo publica (el paro de Argos a las 9:00 del
día de la rueda de prensa, el INE a las 9:00 de su calendario, la afiliación del
IECA cuando la cuelga…) y no cuando toque la siguiente pasada completa.

Uso:  python vigia.py            → imprime los grupos con datos nuevos
      (en GitHub Actions también los deja en $GITHUB_OUTPUT como `grupos=`)
"""
import datetime, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as f

HOY = datetime.datetime.now(datetime.timezone.utc)


def _ultimo(fichero, *ruta):
    """Último periodo 't' de la serie data/<fichero> en la ruta dada."""
    x = f._leer(fichero) or {}
    for k in ruta:
        x = (x or {}).get(k) if isinstance(x, dict) else None
    ts = [p["t"] for p in (x or []) if isinstance(p, dict) and p.get("t")]
    return max(ts) if ts else None


def _ine(cod):
    s = f.ine_mensual(cod, nult=1)
    return s[-1]["t"] if s else None


def _tmov(tabla):
    ts = []
    for s in f.get_json(f"{f.INE_TBL}{tabla}?{f.TMOV_FILTRO}&nult=1"):
        for d in s.get("Data", []):
            if d.get("Valor") is not None:
                y, m = f.ine_periodo(d["Anyo"], d.get("FK_Periodo"), d["Fecha"])
                ts.append(f"{y:04d}-{m:02d}")
    return max(ts) if ts else None


def _argos():
    paro, _ = f.argos_marbella(HOY.year, HOY.year)
    return max(paro) if paro else None


def _sepe_xls():
    """¿Ha colgado ya el SEPE el .xls municipal del mes siguiente al último con desglose?"""
    ult = f._ultimo_mes_con_desglose("paro_mensual.json")
    if not ult:
        return None
    y, m = int(ult[:4]), int(ult[5:7]) + 1
    if m > 12:
        y, m = y + 1, 1
    pag = ("https://www.sepe.es/HomeSepe/que-es-el-sepe/estadisticas/"
           f"datos-estadisticos/municipios/{y}/{f._MESES_ES[m-1]}.html")
    try:
        html = f._get(pag, timeout=30, retries=1).decode("utf-8", "ignore")
    except Exception:
        return ult                       # sin página aún (404) o el SEPE no contesta
    return f"{y:04d}-{m:02d}" if f"MUNI_MALAGA_{m:02d}{y % 100:02d}.xls" in html else ult


def _afiliacion():
    p = f._afi_periodos()
    return p[-1][1] if p else None


def _bce():
    s = f._ecb_mensual("FM/M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA")
    return s[-1]["t"] if s else None


def _bce_publicado():
    d = (f._leer("series_ia.json") or {}).get("series", {}).get("euribor_12m") or {}
    p = d.get("puntos") or []
    return p[-1][0] if p else None


def _mivau():
    s = f._mivau_transacciones(f.MIVAU_TRANS["total"])
    return s[-1]["t"] if s else None


# (grupo, nombre legible, sonda de la fuente, último publicado aquí, ¿solo 1 vez por hora?)
SONDAS = [
    ("laboral", "Paro de Marbella · Argos (SAE)", _argos,
     lambda: _ultimo("paro_mensual.json", "serie"), False),
    ("laboral", "Paro por edad/sector · .xls municipal del SEPE", _sepe_xls,
     lambda: f._ultimo_mes_con_desglose("paro_mensual.json"), False),
    ("afiliacion", "Afiliación municipal · IECA/BADEA", _afiliacion,
     lambda: _ultimo("afiliacion.json", "marbella", "total"), False),
    ("turismo", "Hoteles Marbella · INE EOH", lambda: _ine("EOT42428"),
     lambda: _ultimo("turismo.json", "hoteles", "viajeros"), False),
    ("turismo", "Apartamentos Marbella · INE EOAP", lambda: _ine("EOT41395"),
     lambda: _ultimo("turismo.json", "apartamentos", "viajeros"), False),
    ("coyuntura", "IPC Andalucía · INE", lambda: _ine("IPC293660"),
     lambda: _ultimo("coyuntura.json", "ipc", "indice"), False),
    ("coyuntura", "IPC España (adelantado) · INE", lambda: _ine("IPC290750"),
     lambda: _ultimo("coyuntura.json", "ipc", "var_anual_es"), False),
    ("coyuntura", "Comercio minorista Andalucía · INE", lambda: _ine("ICM4441"),
     lambda: _ultimo("coyuntura.json", "icm", "indice"), False),
    ("vivienda", "Compraventas Málaga · INE ETDP", lambda: _ine("ETDP1696"),
     lambda: _ultimo("vivienda.json", "compraventa", "general"), False),
    ("vivienda", "Hipotecas Málaga · INE", lambda: _ine("HPT34587"),
     lambda: _ultimo("vivienda.json", "hipotecas", "numero"), False),
    ("vivienda", "Precio vivienda Andalucía · INE IPV", lambda: _ine("IPV1623"),
     lambda: _ultimo("vivienda.json", "precio", "indice"), False),
    ("sociedades", "Sociedades mercantiles Málaga · INE", lambda: _ine("SM25051"),
     lambda: _ultimo("sociedades.json", "constituidas"), False),
    ("turismo_moviles", "Turistas nacionales (móviles) · INE", lambda: _tmov("53464"),
     lambda: _ultimo("turismo_moviles.json", "nacional", "total"), True),
    ("turismo_moviles", "Turistas extranjeros (móviles) · INE", lambda: _tmov("52048"),
     lambda: _ultimo("turismo_moviles.json", "internacional", "total"), True),
    ("vivienda_marbella", "Compraventas Marbella · MIVAU", _mivau,
     lambda: _ultimo("vivienda_marbella.json", "compraventas", "total"), True),
    ("bce", "Euríbor · BCE", _bce, _bce_publicado, False),
]


def main():
    forzar_todo = "--todo" in sys.argv
    hora_en_punto = HOY.minute % 30 < 10     # las sondas pesadas, solo cada media hora
    grupos = []
    print(f"== Vigía de fuentes · {HOY:%Y-%m-%d %H:%M} UTC ==")
    for grupo, nombre, sonda, local, pesada in SONDAS:
        if pesada and not (hora_en_punto or forzar_todo):
            continue
        if grupo in grupos:
            continue
        try:
            fuente, aqui = sonda(), local()
        except Exception as e:
            print(f"  ?  {nombre}: sin respuesta ({str(e)[:80]})")
            continue
        nuevo = bool(fuente and (not aqui or fuente > aqui))
        print(f"  {'★' if nuevo else '·'}  {nombre}: fuente {fuente or '—'} · publicado {aqui or '—'}")
        if nuevo:
            grupos.append(grupo)
    print(f"== Grupos con datos nuevos: {', '.join(grupos) or 'ninguno'} ==")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"grupos={','.join(grupos)}\n")


if __name__ == "__main__":
    main()
