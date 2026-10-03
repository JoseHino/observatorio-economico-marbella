/**
 * Asistente de IA del Observatorio Económico de Marbella (Cloudflare Worker).
 *
 * Dos formas de trabajar:
 *  - Botones (nota de prensa, resumen…): una sola llamada a Claude con la ficha de
 *    hechos ya calculados (data/contexto_ia.json). Rápido y predecible.
 *  - Pregunta libre: un AGENTE con herramientas que consulta las 232 series del
 *    observatorio (data/series_ia.json): busca indicadores, lee series y hace los
 *    cálculos en código (variaciones, récords, medias). El modelo no calcula ni
 *    usa datos de fuera: solo redacta con lo que le devuelven las herramientas.
 *
 * Respuesta al navegador: NDJSON en streaming, una línea por evento:
 *   {"tipo":"estado","texto":"Consultando…"}   lo que está haciendo el agente
 *   {"tipo":"texto","texto":"…"}               trozo del texto final
 *   {"tipo":"fin","datos":"2026-10-03"}        fecha de los datos usados
 *   {"tipo":"error","texto":"…"}
 *
 * La clave de Anthropic es un secreto del Worker (ANTHROPIC_API_KEY).
 */
import Anthropic from "@anthropic-ai/sdk";

const MODELO = "claude-opus-5-5";
const ESFUERZO_BOTONES = "low";    // redactar con datos ya calculados: que empiece a escribir enseguida
const ESFUERZO_AGENTE = "medium";  // decidir qué consultar y cómo combinarlo pide algo más de juicio
const MAX_PREGUNTA = 600;
const MAX_VUELTAS = 8;             // consultas encadenadas como máximo por pregunta
const MAX_PUNTOS = 120;            // puntos por consulta de serie

const ORIGENES = ["https://josehino.github.io", "http://localhost:8765", "http://127.0.0.1:8765"];
const BASE_DATOS = "https://josehino.github.io/observatorio-economico-marbella/data/";

const REGLAS = `Reglas que no puedes saltarte:
- Usa EXCLUSIVAMENTE datos del observatorio. No añadas cifras de tu memoria, de otros municipios ni estimaciones propias. Si algo no está en los datos, dilo con claridad ("el observatorio no dispone de ese dato").
- Cita siempre el periodo de cada dato (mes, trimestre o año) y, cuando aporte credibilidad, la fuente.
- Copia las cifras tal y como te llegan, en formato español (punto de miles, coma decimal). No redondees ni recalcules lo que ya viene calculado.
- No inventes declaraciones ni citas. Si el formato pide una cita, deja el hueco: [Declaraciones del concejal de Fomento Económico y Pymes].
- Los datos tienen periodos distintos; no los presentes como simultáneos.
- Respeta el ámbito de cada dato: si es de la provincia de Málaga o de Andalucía, dilo; no lo atribuyas a Marbella.
- Español de España, tono institucional, claro y riguroso: positivo cuando los datos lo son, sin ocultar lo desfavorable.
- Formato: texto plano; **negrita** para cifras o titulares clave y guiones para listas. Sin tablas ni encabezados con #.`;

const SISTEMA_BOTONES = `Eres el analista del Observatorio Económico de Marbella y redactas para la Delegación de Fomento Económico y Pymes del Ayuntamiento de Marbella.\n\n${REGLAS}`;

const SISTEMA_AGENTE = `Eres el analista del Observatorio Económico de Marbella y respondes a la Delegación de Fomento Económico y Pymes del Ayuntamiento de Marbella. Tienes herramientas para consultar las series oficiales del observatorio.

Cómo trabajar:
- Antes de responder, consulta los datos: localiza las series con buscar_indicadores y léelas con consultar_serie.
- Cualquier variación, comparación, récord, media, suma, máximo o mínimo la obtienes con la herramienta calcular; no hagas cuentas tú.
- Lee la ficha de cada serie (unidad, ámbito, frecuencia y nota) y tenla en cuenta: por ejemplo, el paro registrado no es la tasa de paro de la EPA, y la afiliación por residencia no es la afiliación por lugar de trabajo.
- Haz solo las consultas necesarias. Cuando tengas los datos, responde de forma directa en un máximo de 300 palabras, salvo que te pidan otra extensión o formato.

${REGLAS}`;

const ENCARGOS = {
  nota_paro:
    "Redacta una nota de prensa del Ayuntamiento sobre los últimos datos de paro y contratación de Marbella: titular, entradilla con el dato principal, dos o tres párrafos de desarrollo (variación mensual e interanual, récords frente al mismo mes de años anteriores si la ficha los indica, contratos indefinidos, sectores) y un hueco para la cita del concejal. Unas 300 palabras.",
  resumen_concejal:
    "Prepara un resumen ejecutivo de la situación económica de Marbella para el concejal de Fomento Económico y Pymes: cinco o seis puntos clave con su cifra y periodo (empleo, empresas, turismo, vivienda, población) y, al final, dos o tres aspectos a vigilar. Máximo 250 palabras.",
  pymes:
    "Analiza la situación del tejido empresarial y el empleo de Marbella desde la óptica de las pymes y los autónomos: empresas y establecimientos, afiliación, contratación, consumo local y lo que indican los datos de turismo para el comercio y la hostelería. Máximo 300 palabras.",
  turismo:
    "Resume la situación turística de Marbella: hoteles (viajeros, pernoctaciones, tarifa, ocupación), peso del turista extranjero y principales mercados de origen, y la oferta de viviendas con fines turísticos. Máximo 250 palabras.",
  extranjeros:
    "Describe el papel de la población y los trabajadores extranjeros en Marbella: peso en la población, migraciones, afiliación, contratos y paro por nacionalidad. Máximo 250 palabras.",
};

// ---------------------------------------------------------------- utilidades
const fmt = (v, d) => {
  if (v == null || !isFinite(v)) return "—";
  const dec = d ?? (Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : 1);
  return new Intl.NumberFormat("es-ES", { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: "always" }).format(v);
};
const sinTildes = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const nombrePeriodo = (p, frec) => {
  if (p.length === 4) return p;
  const [y, m] = p.split("-");
  if (frec.startsWith("trimestral")) return `${+m / 3}.º trimestre de ${y}`;
  return `${MESES[+m - 1]} de ${y}`;
};

async function leerJson(nombre) {
  // La caché del borde evita descargarlo en cada pregunta; se renueva cada 10 min.
  const r = await fetch(BASE_DATOS + nombre, { cf: { cacheTtl: 600, cacheEverything: true } });
  if (!r.ok) throw new Error(`${nombre}: HTTP ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------- herramientas del agente
const HERRAMIENTAS = [
  {
    name: "buscar_indicadores",
    description: "Busca series del observatorio por palabras clave (por ejemplo: 'paro jóvenes', 'turistas británicos', 'alquiler', 'autónomos'). Devuelve id, nombre, unidad, frecuencia, ámbito, primer y último periodo y último valor. Con texto vacío lista todas las series.",
    input_schema: {
      type: "object",
      properties: { texto: { type: "string", description: "Palabras clave; vacío para listarlas todas." } },
      required: ["texto"],
    },
    eager_input_streaming: true,
  },
  {
    name: "consultar_serie",
    description: "Devuelve la ficha completa (unidad, frecuencia, ámbito, fuente y nota metodológica) y los datos de una serie entre dos periodos. Periodos: 'AAAA' para anuales y 'AAAA-MM' para mensuales y trimestrales (el trimestre se identifica por su último mes: 03, 06, 09, 12).",
    input_schema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Id de la serie, tal como lo da buscar_indicadores." },
        desde: { type: "string", description: "Periodo inicial (opcional)." },
        hasta: { type: "string", description: "Periodo final (opcional)." },
      },
      required: ["id"],
    },
    eager_input_streaming: true,
  },
  {
    name: "calcular",
    description: "Hace cálculos exactos sobre una serie. Operaciones: 'variacion' (periodo frente a periodo_referencia; por defecto el último dato frente al mismo periodo del año anterior), 'variacion_periodo_anterior' (frente al dato inmediatamente anterior), 'ranking_mismo_mes' (compara un mes con el mismo mes de todos los años: posición y récords), 'maximo_minimo', 'media' y 'suma' (entre desde y hasta).",
    input_schema: {
      type: "object",
      properties: {
        id: { type: "string" },
        operacion: { type: "string", enum: ["variacion", "variacion_periodo_anterior", "ranking_mismo_mes", "maximo_minimo", "media", "suma"] },
        periodo: { type: "string", description: "Periodo a analizar (por defecto, el último)." },
        periodo_referencia: { type: "string", description: "Periodo con el que comparar en 'variacion'." },
        desde: { type: "string" },
        hasta: { type: "string" },
      },
      required: ["id", "operacion"],
    },
    eager_input_streaming: true,
  },
];

const esTexto = (v) => v === undefined || v === null || typeof v === "string";

function ejecutarHerramienta(nombre, ent, SERIES) {
  const ficha = (id) => {
    const s = SERIES[id];
    if (!s) throw new Error(`No existe la serie '${id}'. Usa buscar_indicadores para ver los ids disponibles.`);
    return s;
  };
  const enRango = (s, desde, hasta) => s.puntos.filter(([p]) => (!desde || p >= desde) && (!hasta || p <= hasta));

  if (nombre === "buscar_indicadores") {
    if (!esTexto(ent.texto)) throw new Error("'texto' debe ser una cadena.");
    const palabras = sinTildes(ent.texto || "").split(/\s+/).filter((w) => w.length > 2);
    const filas = Object.entries(SERIES).map(([id, s]) => {
      const hay = sinTildes(`${id} ${s.nombre} ${s.ambito} ${s.claves || ""}`);
      const puntos = palabras.length ? palabras.filter((w) => hay.includes(w.slice(0, Math.max(4, w.length - 2)))).length : 1;
      return { id, s, puntos };
    }).filter((f) => f.puntos > 0).sort((a, b) => b.puntos - a.puntos).slice(0, palabras.length ? 25 : 300);
    if (!filas.length) return "No hay series que coincidan. Prueba con otras palabras o con texto vacío para verlas todas.";
    return filas.map(({ id, s }) => {
      const u = s.puntos[s.puntos.length - 1];
      return `${id} | ${s.nombre} | ${s.unidad} | ${s.frecuencia} | ${s.ambito} | ${s.puntos[0][0]} → ${u[0]} | último: ${fmt(u[1])}`;
    }).join("\n");
  }

  if (nombre === "consultar_serie") {
    if (typeof ent.id !== "string" || !esTexto(ent.desde) || !esTexto(ent.hasta)) throw new Error("Parámetros no válidos.");
    const s = ficha(ent.id);
    let pts = enRango(s, ent.desde, ent.hasta);
    let aviso = "";
    if (pts.length > MAX_PUNTOS) {
      aviso = `\n(Se muestran los últimos ${MAX_PUNTOS} de ${pts.length} puntos; acota con desde/hasta si necesitas otros.)`;
      pts = pts.slice(-MAX_PUNTOS);
    }
    return `Serie: ${s.nombre}\nUnidad: ${s.unidad} · Frecuencia: ${s.frecuencia} · Ámbito: ${s.ambito} · Fuente: ${s.fuente}` +
      (s.nota ? `\nNota: ${s.nota}` : "") +
      `\nDisponible: ${s.puntos[0][0]} → ${s.puntos[s.puntos.length - 1][0]}\nDatos:\n` +
      (pts.length ? pts.map(([p, v]) => `${p}: ${fmt(v)}`).join("\n") : "(sin datos en ese intervalo)") + aviso;
  }

  if (nombre === "calcular") {
    const ok = typeof ent.id === "string" && typeof ent.operacion === "string" &&
      ["periodo", "periodo_referencia", "desde", "hasta"].every((k) => esTexto(ent[k]));
    if (!ok) throw new Error("Parámetros no válidos.");
    const s = ficha(ent.id);
    const P = Object.fromEntries(s.puntos);
    const ultimo = s.puntos[s.puntos.length - 1][0];
    const per = ent.periodo || ultimo;
    const np = (p) => nombrePeriodo(p, s.frecuencia);
    const cab = `${s.nombre} (${s.unidad}; ${s.ambito}; fuente: ${s.fuente})`;
    const varTxt = (a, b, pa, pb) => {
      if (a == null) return `No hay dato para ${np(pa)}.`;
      if (b == null) return `No hay dato para ${np(pb)}.`;
      const d = a - b, pc = b ? (d / b) * 100 : null;
      return `${cab}\n${np(pa)}: ${fmt(a)} · ${np(pb)}: ${fmt(b)}\nDiferencia: ${d >= 0 ? "+" : "−"}${fmt(Math.abs(d))}` +
        (pc != null ? ` (${pc >= 0 ? "+" : "−"}${fmt(Math.abs(pc), 1)} %)` : "");
    };
    switch (ent.operacion) {
      case "variacion": {
        const ref = ent.periodo_referencia || (per.length === 4 ? String(+per - 1) : `${+per.slice(0, 4) - 1}${per.slice(4)}`);
        return varTxt(P[per], P[ref], per, ref);
      }
      case "variacion_periodo_anterior": {
        const i = s.puntos.findIndex(([p]) => p === per);
        if (i <= 0) return `No hay periodo anterior a ${np(per)} en la serie.`;
        return varTxt(P[per], s.puntos[i - 1][1], per, s.puntos[i - 1][0]);
      }
      case "ranking_mismo_mes": {
        if (per.length !== 7) return "Esta operación solo tiene sentido en series mensuales o trimestrales.";
        const mes = per.slice(5);
        const mismos = s.puntos.filter(([p]) => p.slice(5) === mes);
        const v = P[per];
        if (v == null) return `No hay dato para ${np(per)}.`;
        const orden = [...mismos].sort((a, b) => b[1] - a[1]);
        const pos = orden.findIndex(([p]) => p === per) + 1;
        const anteriores = mismos.filter(([p]) => p < per).reverse();
        const record = (mejor) => {
          const sup = anteriores.find(([, x]) => (mejor === "max" ? x > v : x < v));
          if (!sup) return `el ${mejor === "max" ? "más alto" : "más bajo"} de toda la serie (desde ${mismos[0][0].slice(0, 4)})`;
          if (sup === anteriores[0]) return null;
          return `el ${mejor === "max" ? "más alto" : "más bajo"} desde ${sup[0].slice(0, 4)}, cuando fue de ${fmt(sup[1])}`;
        };
        const rmax = record("max"), rmin = record("min");
        return `${cab}\n${np(per)}: ${fmt(v)}. Comparado con el mismo periodo de ${mismos.length} años (${mismos[0][0].slice(0, 4)}–${mismos[mismos.length - 1][0].slice(0, 4)}): ` +
          `posición ${pos} de ${mismos.length} de mayor a menor.` +
          (rmax ? `\nEs ${rmax}.` : "") + (rmin ? `\nEs ${rmin}.` : "") +
          `\nValores del mismo periodo: ` + mismos.map(([p, x]) => `${p.slice(0, 4)}: ${fmt(x)}`).join(" · ");
      }
      case "maximo_minimo":
      case "media":
      case "suma": {
        const pts = enRango(s, ent.desde, ent.hasta);
        if (!pts.length) return "No hay datos en ese intervalo.";
        const rango = `entre ${np(pts[0][0])} y ${np(pts[pts.length - 1][0])} (${pts.length} datos)`;
        if (ent.operacion === "suma") return `${cab}\nSuma ${rango}: ${fmt(pts.reduce((a, [, x]) => a + x, 0))}`;
        if (ent.operacion === "media") return `${cab}\nMedia ${rango}: ${fmt(pts.reduce((a, [, x]) => a + x, 0) / pts.length, 1)}`;
        const mx = pts.reduce((a, b) => (b[1] > a[1] ? b : a)), mn = pts.reduce((a, b) => (b[1] < a[1] ? b : a));
        return `${cab}\n${rango[0].toUpperCase() + rango.slice(1)}: máximo ${fmt(mx[1])} en ${np(mx[0])}; mínimo ${fmt(mn[1])} en ${np(mn[0])}.`;
      }
      default:
        throw new Error(`Operación desconocida: ${ent.operacion}`);
    }
  }
  throw new Error(`Herramienta desconocida: ${nombre}`);
}

// Frase que ve el usuario mientras el agente trabaja.
function frase(nombre, ent, SERIES) {
  const n = (id) => (SERIES[id] ? SERIES[id].nombre.toLowerCase() : id);
  if (nombre === "buscar_indicadores") return ent.texto ? `Buscando indicadores sobre «${ent.texto}»` : "Revisando el catálogo de indicadores";
  if (nombre === "consultar_serie") return `Consultando ${n(ent.id)}` + (ent.desde || ent.hasta ? ` (${ent.desde || "inicio"} → ${ent.hasta || "último dato"})` : "");
  if (nombre === "calcular") {
    const op = { variacion: "variación interanual", variacion_periodo_anterior: "variación frente al periodo anterior",
      ranking_mismo_mes: "comparación con el mismo mes de otros años", maximo_minimo: "máximos y mínimos", media: "media", suma: "total" }[ent.operacion] || ent.operacion;
    return `Calculando ${op}: ${n(ent.id)}`;
  }
  return "Trabajando";
}

// ---------------------------------------------------------------- HTTP
function cabecerasCors(origen) {
  return {
    "Access-Control-Allow-Origin": ORIGENES.includes(origen) ? origen : ORIGENES[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    Vary: "Origin",
  };
}
const error = (estado, mensaje, cors) =>
  new Response(mensaje, { status: estado, headers: { ...cors, "Content-Type": "text/plain; charset=utf-8" } });

const PARAMS_COMUNES = {
  model: MODELO,
  max_tokens: 8000,
  // Si un clasificador de seguridad rechazara la petición, la API la reintenta sola
  // en el modelo de respaldo que Anthropic recomienda para ese caso.
  betas: ["server-side-fallback-2026-07-01"],
  fallbacks: "default",
};

async function responderBoton(client, encargo, emitir) {
  const ficha = await leerJson("contexto_ia.json");
  const lineas = (ficha.hechos || []).map((h) => `- [${h.tema} · ${h.periodo} · fuente: ${h.fuente}] ${h.texto}`).join("\n");
  emitir({ tipo: "estado", texto: "Redactando con los datos del observatorio" });
  const stream = client.beta.messages.stream({
    ...PARAMS_COMUNES,
    output_config: { effort: ESFUERZO_BOTONES },
    system: SISTEMA_BOTONES,
    messages: [{ role: "user", content: `FICHA DE DATOS DEL OBSERVATORIO (actualizada el ${ficha.generado}):\n${lineas}\n\nENCARGO:\n${encargo}` }],
  });
  stream.on("text", (t) => emitir({ tipo: "texto", texto: t }));
  const final = await stream.finalMessage();
  avisoFinal(final, emitir);
  return ficha.generado;
}

async function responderAgente(client, pregunta, emitir) {
  const almacen = await leerJson("series_ia.json");
  const SERIES = almacen.series || {};
  const messages = [{ role: "user", content: `Fecha de los datos del observatorio: ${almacen.generado}.\n\nPregunta: ${pregunta}` }];
  emitir({ tipo: "estado", texto: "Analizando la pregunta" });
  let reintentosJson = 0;

  for (let vuelta = 0; vuelta < MAX_VUELTAS; vuelta++) {
    const stream = client.beta.messages.stream({
      ...PARAMS_COMUNES,
      output_config: { effort: ESFUERZO_AGENTE },
      cache_control: { type: "ephemeral" },
      system: SISTEMA_AGENTE,
      tools: HERRAMIENTAS,
      messages,
    });
    stream.on("text", (t) => emitir({ tipo: "texto", texto: t }));
    let msg;
    try {
      msg = await stream.finalMessage();
      reintentosJson = 0;
    } catch (e) {
      // Con eager_input_streaming, una entrada de herramienta ilegible rechaza aquí;
      // solo ese caso se reintenta. Los errores de la API se propagan.
      if (e instanceof Anthropic.APIError || reintentosJson++ >= 2) throw e;
      continue;
    }

    if (msg.stop_reason === "refusal") { avisoFinal(msg, emitir); return almacen.generado; }
    if (msg.stop_reason === "pause_turn") { messages.push({ role: "assistant", content: msg.content }); continue; }
    const usos = msg.content.filter((b) => b.type === "tool_use");
    if (!usos.length) { avisoFinal(msg, emitir); return almacen.generado; }   // respuesta final
    if (msg.stop_reason === "max_tokens") throw new Error("respuesta truncada");

    messages.push({ role: "assistant", content: msg.content });
    const resultados = usos.map((u) => {
      const ent = u.input && typeof u.input === "object" ? u.input : {};
      emitir({ tipo: "estado", texto: frase(u.name, ent, SERIES) });
      try {
        return { type: "tool_result", tool_use_id: u.id, content: ejecutarHerramienta(u.name, ent, SERIES) };
      } catch (e) {
        return { type: "tool_result", tool_use_id: u.id, is_error: true, content: String(e.message || e) };
      }
    });
    messages.push({ role: "user", content: resultados });
    emitir({ tipo: "estado", texto: "Interpretando los datos" });
  }
  emitir({ tipo: "texto", texto: "\n\n[He llegado al límite de consultas para una sola pregunta. Prueba a formularla de forma más concreta.]" });
  return almacen.generado;
}

function avisoFinal(final, emitir) {
  if (final.stop_reason === "refusal")
    emitir({ tipo: "texto", texto: "\n\n[El asistente no ha podido completar esta petición. Prueba a formularla de otra manera.]" });
  else if (final.stop_reason === "max_tokens")
    emitir({ tipo: "texto", texto: "\n\n[Texto recortado por longitud.]" });
}

export default {
  async fetch(request, env, ctx) {
    const origen = request.headers.get("Origin") || "";
    const cors = cabecerasCors(origen);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return error(405, "Usa POST.", cors);
    if (!ORIGENES.includes(origen)) return error(403, "Origen no autorizado.", cors);
    if (!env.ANTHROPIC_API_KEY) return error(503, "El asistente aún no tiene configurada la clave de la API.", cors);

    // Límite por IP (binding de rate limiting de Cloudflare, ver wrangler.toml).
    if (env.LIMITADOR) {
      const { success } = await env.LIMITADOR.limit({ key: request.headers.get("CF-Connecting-IP") || "anon" });
      if (!success) return error(429, "Demasiadas consultas seguidas. Espera un minuto y vuelve a probar.", cors);
    }

    let cuerpo;
    try { cuerpo = await request.json(); } catch { return error(400, "Petición no válida.", cors); }
    const modo = String(cuerpo.modo || "libre");
    const pregunta = String(cuerpo.pregunta || "").trim();
    if (modo === "libre") {
      if (!pregunta) return error(400, "Escribe una pregunta.", cors);
      if (pregunta.length > MAX_PREGUNTA) return error(400, `La pregunta no puede pasar de ${MAX_PREGUNTA} caracteres.`, cors);
    } else if (!ENCARGOS[modo]) {
      return error(400, "Modo desconocido.", cors);
    }

    const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
    const { readable, writable } = new TransformStream();
    const escritor = writable.getWriter();
    const cod = new TextEncoder();
    const emitir = (ev) => escritor.write(cod.encode(JSON.stringify(ev) + "\n")).catch(() => {});

    const tarea = (async () => {
      try {
        const fecha = modo === "libre"
          ? await responderAgente(client, pregunta, emitir)
          : await responderBoton(client, ENCARGOS[modo], emitir);
        emitir({ tipo: "fin", datos: fecha || "" });
      } catch (e) {
        let msg = "Error al generar el texto. Inténtalo de nuevo en unos segundos.";
        if (e instanceof Anthropic.RateLimitError) msg = "El servicio de IA está saturado. Espera un momento y vuelve a probar.";
        else if (e instanceof Anthropic.AuthenticationError) msg = "La clave de la API no es válida: revisa el secreto ANTHROPIC_API_KEY del Worker.";
        else if (e instanceof Anthropic.APIConnectionError) msg = "No se ha podido contactar con el servicio de IA.";
        console.error(e);
        emitir({ tipo: "error", texto: msg });
      } finally {
        await escritor.close().catch(() => {});
      }
    })();
    ctx.waitUntil(tarea);

    return new Response(readable, {
      headers: { ...cors, "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store" },
    });
  },
};
