/**
 * Asistente de IA del Observatorio Económico de Marbella.
 *
 * Cloudflare Worker que recibe una pregunta (o un botón predefinido) desde el panel,
 * descarga la ficha de datos que genera fetch_data.py (data/contexto_ia.json) y pide
 * a Claude un texto que SOLO use esas cifras. La respuesta vuelve en streaming, como
 * texto plano, para que en la web se vea escribiéndose.
 *
 * La clave de Anthropic es un secreto del Worker (npx wrangler secret put
 * ANTHROPIC_API_KEY): nunca llega al navegador ni está en el repositorio.
 */
import Anthropic from "@anthropic-ai/sdk";

const MODELO = "claude-opus-5-5";
// Esfuerzo bajo: los datos ya vienen calculados y el trabajo es de redacción; así el
// texto empieza a salir en pocos segundos, que en una demostración importa.
const ESFUERZO = "low";
const MAX_PREGUNTA = 600;
const MAX_TOKENS = 4000;

// Orígenes que pueden usar el asistente: la web publicada y la prueba en local.
const ORIGENES = [
  "https://josehino.github.io",
  "http://localhost:8765",
  "http://127.0.0.1:8765",
];
const FICHA_URL = "https://josehino.github.io/observatorio-economico-marbella/data/contexto_ia.json";

const SISTEMA = `Eres el analista del Observatorio Económico de Marbella y redactas para la Delegación de Fomento Económico y Pymes del Ayuntamiento de Marbella.

Reglas que no puedes saltarte:
- Usa EXCLUSIVAMENTE las cifras de la ficha de datos que se te da. No añadas datos de tu memoria, ni de otros municipios, ni estimaciones propias. Si te piden algo que no está en la ficha, dilo con claridad ("el observatorio no dispone de ese dato") en lugar de suponerlo.
- Cita siempre el periodo de cada dato (mes, trimestre o año) y, cuando aporte credibilidad, la fuente oficial que figura en la ficha.
- Copia las cifras tal y como aparecen en la ficha, con su formato español (punto de miles y coma decimal). No redondees ni recalcules porcentajes que ya vienen dados.
- No inventes declaraciones ni citas de ninguna persona. Si el formato pide una cita, deja el hueco así: [Declaraciones del concejal de Fomento Económico y Pymes].
- Los datos tienen periodos distintos (unos son del mes pasado y otros de hace uno o dos años). No los presentes como simultáneos.
- Escribe en español de España, con tono institucional, claro y positivo pero riguroso: no exageres ni ocultes los datos desfavorables.
- Formato: texto plano. Puedes usar **negrita** para titulares o cifras clave y guiones para listas. No uses tablas ni encabezados con #.`;

// Encargos de los botones: viven aquí, no en el navegador.
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

function cabecerasCors(origen) {
  const permitido = ORIGENES.includes(origen) ? origen : ORIGENES[0];
  return {
    "Access-Control-Allow-Origin": permitido,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function error(estado, mensaje, cors) {
  return new Response(mensaje, {
    status: estado,
    headers: { ...cors, "Content-Type": "text/plain; charset=utf-8" },
  });
}

async function leerFicha() {
  // La caché del borde evita descargar la ficha en cada pregunta; se renueva cada 10 min.
  const r = await fetch(FICHA_URL, { cf: { cacheTtl: 600, cacheEverything: true } });
  if (!r.ok) throw new Error(`ficha de datos: HTTP ${r.status}`);
  const ficha = await r.json();
  const lineas = (ficha.hechos || []).map(
    (h) => `- [${h.tema} · ${h.periodo} · fuente: ${h.fuente}] ${h.texto}`,
  );
  return { generado: ficha.generado, texto: lineas.join("\n") };
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
      const ip = request.headers.get("CF-Connecting-IP") || "anon";
      const { success } = await env.LIMITADOR.limit({ key: ip });
      if (!success) return error(429, "Demasiadas consultas seguidas. Espera un minuto y vuelve a probar.", cors);
    }

    let cuerpo;
    try {
      cuerpo = await request.json();
    } catch {
      return error(400, "Petición no válida.", cors);
    }
    const modo = String(cuerpo.modo || "libre");
    let encargo;
    if (modo === "libre") {
      const pregunta = String(cuerpo.pregunta || "").trim();
      if (!pregunta) return error(400, "Escribe una pregunta.", cors);
      if (pregunta.length > MAX_PREGUNTA) return error(400, `La pregunta no puede pasar de ${MAX_PREGUNTA} caracteres.`, cors);
      encargo = `Petición del usuario: ${pregunta}\n\nResponde de forma directa, con las cifras de la ficha que vengan al caso. Máximo 300 palabras salvo que pida otra cosa.`;
    } else if (ENCARGOS[modo]) {
      encargo = ENCARGOS[modo];
    } else {
      return error(400, "Modo desconocido.", cors);
    }

    let ficha;
    try {
      ficha = await leerFicha();
    } catch (e) {
      return error(502, "No se ha podido leer la ficha de datos del observatorio.", cors);
    }

    const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
    const { readable, writable } = new TransformStream();
    const escritor = writable.getWriter();
    const cod = new TextEncoder();

    // Se responde ya con el stream y se va rellenando en segundo plano.
    const tarea = (async () => {
      try {
        const stream = client.beta.messages.stream({
          model: MODELO,
          max_tokens: MAX_TOKENS,
          output_config: { effort: ESFUERZO },
          // Si un clasificador de seguridad rechazara la petición, la API la reintenta
          // sola en el modelo de respaldo que Anthropic recomienda para ese caso.
          betas: ["server-side-fallback-2026-07-01"],
          fallbacks: "default",
          system: SISTEMA,
          messages: [{
            role: "user",
            content: `FICHA DE DATOS DEL OBSERVATORIO (actualizada el ${ficha.generado}):\n${ficha.texto}\n\nENCARGO:\n${encargo}`,
          }],
        });
        for await (const ev of stream) {
          if (ev.type === "content_block_delta" && ev.delta.type === "text_delta") {
            await escritor.write(cod.encode(ev.delta.text));
          }
        }
        const final = await stream.finalMessage();
        if (final.stop_reason === "refusal") {
          await escritor.write(cod.encode("\n\n[El asistente no ha podido completar esta petición. Prueba a formularla de otra manera.]"));
        } else if (final.stop_reason === "max_tokens") {
          await escritor.write(cod.encode("\n\n[Texto recortado por longitud.]"));
        }
      } catch (e) {
        let msg = "Error al generar el texto. Inténtalo de nuevo en unos segundos.";
        if (e instanceof Anthropic.RateLimitError) msg = "El servicio de IA está saturado. Espera un momento y vuelve a probar.";
        else if (e instanceof Anthropic.AuthenticationError) msg = "La clave de la API no es válida: revisa el secreto ANTHROPIC_API_KEY del Worker.";
        console.error(e);
        await escritor.write(cod.encode(`\n\n[${msg}]`));
      } finally {
        await escritor.close();
      }
    })();

    // En Workers, la tarea en segundo plano debe registrarse para que no se corte.
    ctx.waitUntil(tarea);
    return new Response(readable, {
      headers: {
        ...cors,
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Datos-Actualizados": ficha.generado || "",
        "Access-Control-Expose-Headers": "X-Datos-Actualizados",
      },
    });
  },
};
