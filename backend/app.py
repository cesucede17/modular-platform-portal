"""
El recibidor de la Plataforma SGE.

Entras con tu identidad de la plataforma, ves las herramientas a las que
tienes acceso, eliges una. Nada mas: sin base de datos, y sin saber que
ocurre dentro de las herramientas.
"""

from __future__ import annotations

import html
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import catalogo
import conectados
import contexto
import sso

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

_SECRET = os.environ.get("SECRET_KEY", "")
if not _SECRET:
    raise RuntimeError(
        "Falta SECRET_KEY. Firma la cookie de sesion; sin ella no se arranca, "
        "porque generarla al vuelo invalidaria todas las sesiones en cada "
        "reinicio del contenedor."
    )

CATALOGO = catalogo.cargar_catalogo(RAIZ / "herramientas.yaml")

_SESSION_MAX_AGE_POR_DEFECTO = 1800  # 30 min


def _session_max_age() -> int:
    """La vida de la cookie de sesion, en segundos.

    Antes eran 8 h cableadas, el tope de jornada acordado para la plataforma.
    Se baja a 30 min porque el recibidor congela los roles al entrar (sso.py,
    donde se fija request.session["roles"]) y los lee de la cookie en cada
    peticion: hasta que la cookie no caduca, un rol nuevo no se ve. Al
    caducar, / manda a /sso/login y los roles se vuelven a leer del token; si
    la sesion de Keycloak sigue viva, la vuelta es transparente.

    Bajarla NO echa a nadie de una herramienta: PALBE, Tambora y Bartolo
    emiten su propia cookie de 8 h (JWT_EXPIRE_HOURS). Esto solo afecta a la
    pagina de tarjetas.

    A 28800 se recupera el comportamiento anterior, sin reconstruir la imagen.
    """
    crudo = os.environ.get("SESSION_MAX_AGE", "").strip()
    if not crudo:
        return _SESSION_MAX_AGE_POR_DEFECTO
    try:
        segundos = int(crudo)
    except ValueError:
        raise RuntimeError(
            f"SESSION_MAX_AGE={crudo!r} no es un numero entero de segundos. "
            "Se falla al arrancar a proposito, en vez de caer al valor por "
            "defecto: una errata silenciosa dejaria los roles congelados mas "
            "tiempo del previsto, que es justo lo que esta variable evita."
        ) from None
    if segundos <= 0:
        raise RuntimeError(
            f"SESSION_MAX_AGE={segundos} no es util: cero o negativo hace que "
            "la cookie caduque al instante y el login entra en bucle."
        )
    return segundos


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Las mismas tres cabeceras que ya pone PALBE (app_palbe_4.py,
    _AuthMiddleware.dispatch), copiadas para que coincidan. El recibidor es
    la puerta de entrada de la plataforma y, antes de esto, era la unica
    pieza sin ellas."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        return response


def _contexto_token() -> str:
    """El secreto con el que se le pregunta el contexto a las herramientas.

    Vacio = no se pregunta a nadie y la franja no sale. Eso es preferible a
    un secreto por defecto, y a diferencia de SECRET_KEY no impide arrancar:
    la plataforma funciona igual, solo falta la reanudacion.

    Se lee del entorno en cada llamada, como _session_max_age, y no al
    importar el modulo: leerlo en el import lo congela en el PRIMER import del
    proceso de pytest, que es exactamente la fragilidad que documenta
    _firmar_sesion en los tests."""
    return os.environ.get("SGE_CONTEXTO_TOKEN", "").strip()


# Un solo cliente para toda la vida del proceso, creado en el lifespan: abrir
# uno por peticion tiraria el pool de conexiones y triplicaria el coste de
# cada carga de la portada. Los tests lo sustituyen por uno con transporte de
# prueba, que es la razon de que sea un global y no una variable local.
_CLIENTE: httpx.AsyncClient | None = None

# Quien ha pasado por el recibidor y cuando. En memoria, como el
# `_USER_LAST_SEEN` de PALBE: es lo que permite que el bloque "Trabajando
# ahora" salga aunque las tres herramientas esten caidas, porque para saber
# quien esta AQUI no hay que preguntar a nadie.
#
# Se limpia al leerlo (conectados.propios), asi que no crece. Es POR PROCESO:
# con mas de un worker cada uno veria a los suyos. Hoy es uno.
_EN_EL_RECIBIDOR: dict = {}

# Como se llama cada persona, segun Keycloak. Separado del registro de arriba
# porque NO caduca: un nombre sigue valiendo cuando la persona se va, y la
# ventana de cinco minutos lo borraria.
#
# Existe porque el recibidor es la puerta de entrada y es el unico que conoce
# el nombre de Keycloak de todo el mundo -- las herramientas solo saben su
# `users.username`. Sin esto, la misma persona salia como "Cesar Suela" o como
# "csuela" segun donde estuviera. Lo vio una prueba con dos personas.
_NOMBRES: dict = {}


@asynccontextmanager
async def _vida(app: FastAPI):
    global _CLIENTE
    _CLIENTE = httpx.AsyncClient(limits=httpx.Limits(max_connections=6))
    try:
        yield
    finally:
        await _CLIENTE.aclose()
        _CLIENTE = None


app = FastAPI(title="Plataforma SGE", lifespan=_vida)
app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET,
    max_age=_session_max_age(),
    https_only=os.environ.get("HTTPS_ONLY", "0") == "1",
)
app.add_middleware(_SecurityHeadersMiddleware)

_SSO_ACTIVO = sso.registrar_rutas(app)

app.mount("/static", StaticFiles(directory=RAIZ / "frontend"), name="static")

_PLANTILLA = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return JSONResponse(
        {"status": "ok", "sso": _SSO_ACTIVO, "herramientas": len(CATALOGO)}
    )


@app.get("/", response_class=HTMLResponse)
async def recibidor(request: Request):
    if not request.session.get("sub"):
        return RedirectResponse("/sso/login", status_code=303)

    nombre = request.session.get("nombre") or ""
    # Pasar por aqui es estar en la plataforma. Y de paso se apunta como se
    # llama, para que salga igual esté donde esté.
    conectados.apuntar(_EN_EL_RECIBIDOR, request.session["sub"], nombre)
    conectados.recordar_nombre(_NOMBRES, request.session["sub"], nombre)
    visibles = catalogo.filtrar_por_roles(CATALOGO, request.session.get("roles") or [])

    if visibles:
        # Todo lo que viene del catalogo o de la sesion se escapa antes de
        # interpolarlo: el nombre sale del perfil de Keycloak, que cualquiera
        # puede editar sobre si mismo, y una URL con una comilla en el YAML
        # rompe el atributo href igual de bien. quote=True porque algunos
        # valores (la url) van dentro de un atributo, no solo en texto.
        tarjetas = "\n".join(
            f'''<a class="tarjeta" href="{html.escape(h.url, quote=True)}" data-id="{html.escape(h.id, quote=True)}">
        {_icono(h.icono)}
        <p class="nombre">{html.escape(h.nombre, quote=True)}</p>
        <p class="descripcion">{html.escape(h.descripcion, quote=True)}</p>
        <div class="cta"><div class="rueda"><span>Abrir &rarr;</span><span>Vamos &rarr;</span></div></div>
      </a>'''
            for h in visibles
        )
    else:
        # Estado vacio: legitimo, no un error. Cualquier usuario nuevo de
        # Keycloak lo vera hasta que alguien le asigne roles de herramienta.
        tarjetas = """<div class="vacio">
        <p class="nombre">Todavia no tienes acceso a ninguna herramienta</p>
        <p class="descripcion">Pide acceso a quien administre la Plataforma SGE
        y aparecera aqui la proxima vez que entres.</p>
      </div>"""

    # Iniciales y saludo se calculan sobre el nombre crudo -- se escapan
    # despues, junto con el nombre, justo antes de meterlos en la plantilla.
    saludo = nombre.split()[0] if nombre else "de nuevo"
    iniciales = "".join(p[0] for p in nombre.split()[:2]).upper() or "?"

    pagina = (
        _PLANTILLA.replace("{{NOMBRE}}", html.escape(nombre, quote=True))
        .replace("{{INICIALES}}", html.escape(iniciales, quote=True))
        .replace("{{SALUDO}}", html.escape(saludo, quote=True))
        .replace("{{TARJETAS}}", tarjetas)
    )
    return HTMLResponse(pagina, headers={"Cache-Control": "no-store"})


def _icono(valor: str) -> str:
    """El icono de una tarjeta: una imagen si lo parece, texto si no.

    La regla va por el VALOR y no por un campo nuevo del catalogo. Dos
    motivos: una herramienta puede seguir entrando con un emoji y sin imagen
    -- que es como entraron las tres primeras --, y no hace falta tocar el
    dataclass de Herramienta por una cuestion de presentacion.

    El icono es DECORATIVO: la tarjeta dice el nombre justo debajo, asi que va
    con alt vacio y aria-hidden para que un lector de pantalla no lo lea dos
    veces."""
    seguro = html.escape(valor, quote=True)
    if valor.startswith("/static/"):
        return (
            f'<img class="icono" src="{seguro}" alt="" aria-hidden="true" '
            f'loading="lazy" decoding="async">'
        )
    return f'<div class="icono">{seguro}</div>'


def _absoluta(herramienta_id: str, ruta: str) -> str:
    """La url publica de reanudacion: el origen de la tarjeta mas la ruta.

    La herramienta devuelve una ruta RELATIVA (regla 5 del contrato) porque
    dentro del contenedor no conoce su dominio publico, y porque una absoluta
    convertiria la franja en un redirector abierto. El origen sale de la url
    de su tarjeta, que ya lleva el dominio del entorno expandido.

    LIMITACION conocida, comprobada en las tres herramientas: ninguna acepta
    un destino tras el SSO -- el callback de PALBE aterriza en "/", el de
    Tambora en "/chat" y el de Bartolo en "/auditorias". Asi que este enlace
    profundo aterriza en el sitio exacto SOLO si la cookie propia de la
    herramienta (8 h) sigue viva. Si no, se acaba en su portada.

    En la practica es el caso bueno la mayoria de las veces: si tienes
    contexto es porque estuviste ahi hace poco, y la cookie dura 8 h. Pero es
    una limitacion real y esta anotada como pendiente, no disimulada."""
    tarjeta = next((h for h in CATALOGO if h.id == herramienta_id), None)
    if tarjeta is None:
        return ruta
    partes = urlsplit(tarjeta.url)
    return urlunsplit((partes.scheme, partes.netloc, ruta, "", ""))


@app.get("/api/conectados")
async def api_conectados(request: Request):
    """Quien esta trabajando ahora, en las herramientas y en el recibidor.

    Misma forma que /api/contexto y por los mismos motivos: peticion aparte
    para que la portada no espere, 401 en JSON y no un 303 con la sesion
    caducada, y 204 cuando no hay nadie -- entonces el bloque no aparece y la
    pagina queda como si no existiera."""
    sub = request.session.get("sub")
    if not sub:
        return JSONResponse({"error": "sin sesion"}, status_code=401)

    nombre = request.session.get("nombre") or ""
    conectados.apuntar(_EN_EL_RECIBIDOR, sub, nombre)
    conectados.recordar_nombre(_NOMBRES, sub, nombre)

    gente = ()
    if _CLIENTE is not None:
        # Quien pregunta SI sale en su propia lista, por decision del usuario
        # el 2026-09-18. Antes se excluia con el argumento de que "ya sabes que
        # estas", y eso hacia que el bloque desapareciera cuando eras el unico
        # dentro -- que con siete personas es la mayor parte del dia. Verte en
        # la lista es ademas la senal de que el bloque funciona.
        # `nombres` hace dos cosas: aprender de lo que cuenta cada herramienta
        # y aplicar lo aprendido. Va dentro de consultar porque el
        # aprendizaje tiene que ocurrir sobre las respuestas EN CRUDO -- ver
        # su docstring, que explica por que hacerlo despues aprendia justo el
        # nombre equivocado.
        gente = await conectados.consultar(
            _CLIENTE,
            CATALOGO,
            _contexto_token(),
            _EN_EL_RECIBIDOR,
            nombres=_NOMBRES,
        )

    if not gente:
        return Response(status_code=204)

    return JSONResponse(
        {
            "gente": [
                # El sub NO se manda al navegador: es la clave para agrupar a la
                # misma persona en varias herramientas, y ya se ha usado aqui.
                {"nombre": a.nombre, "donde": _donde(a.herramienta)}
                for a in gente
            ]
        },
        headers={"Cache-Control": "no-store"},
    )


def _donde(herramienta_id: str) -> str:
    """El nombre bonito de donde esta esa persona.

    Sale del catalogo para no repetir los nombres en dos sitios; si el id no
    esta -- el caso del propio recibidor -- se usa tal cual."""
    if herramienta_id == conectados.NOMBRE_PROPIO:
        # El nombre de la plataforma, igual que en la cabecera y en el titulo
        # de la pagina. `conectados.NOMBRE_PROPIO` sigue siendo el
        # identificador interno con el que el recibidor se distingue de una
        # herramienta; lo que se pinta es esto.
        return "Plataforma SGE"
    h = next((h for h in CATALOGO if h.id == herramienta_id), None)
    return h.nombre if h else herramienta_id


@app.get("/api/contexto")
async def api_contexto(request: Request):
    """La franja "Sigue donde lo dejaste", en una peticion aparte.

    Aparte a proposito, y es la decision que sostiene la pieza: la rejilla de
    tarjetas NUNCA espera. Si esto se moviera al render de "/", la portada
    tardaria hasta un segundo y medio en pintar y un hipo de PALBE haria que
    la plataforma entera pareciese rota.

    El recibidor hace de intermediario: el navegador le pregunta a el (mismo
    origen, cero CORS, y las herramientas viven en subdominios distintos) y es
    el quien consulta a las tres por dentro de sge_net. Asi el secreto no sale
    del servidor."""
    sub = request.session.get("sub")
    if not sub:
        # 401 y NO un 303. Un fetch que siguiera el redirect acabaria en
        # Keycloak con un error de CORS con toda la pinta de ser un fallo del
        # codigo. Y con la cookie de 30 min, una pestana olvidada da este caso
        # a menudo.
        return JSONResponse({"error": "sin sesion"}, status_code=401)

    # El sub sale de la cookie, JAMAS de la peticion: aceptar un ?sub= del
    # cliente convertiria esto en "lee el contexto de cualquiera con solo
    # tener sesion".
    visibles = catalogo.filtrar_por_roles(CATALOGO, request.session.get("roles") or [])

    contextos = []
    if _CLIENTE is not None:
        contextos = await contexto.consultar(
            _CLIENTE, visibles, sub=sub, token=_contexto_token()
        )

    if not contextos:
        # 204: el JS no toca el hueco y la pagina queda EXACTAMENTE como hoy.
        # No es un error -- es el estado de cualquier usuario nuevo.
        return Response(status_code=204)

    ahora = datetime.now()
    return JSONResponse(
        {
            "contextos": [
                {
                    "herramienta": c.herramienta,
                    "titulo": c.titulo,
                    "detalle": c.detalle,
                    "url": _absoluta(c.herramienta, c.url),
                    # Redactada en el servidor, como la de los companeros: asi no
                    # hay desfase de reloj del cliente y no se manda ninguna marca
                    # de tiempo al navegador. Vacia si la herramienta no la sabe.
                    "hace": (
                        contexto.redactar_antiguedad(c.visto_en, ahora)
                        if c.visto_en
                        else ""
                    ),
                    "companeros": [
                        {
                            "nombre": p.nombre,
                            "hace": contexto.redactar_antiguedad(p.visto_en, ahora),
                        }
                        for p in c.companeros
                    ],
                }
                for c in contextos
            ]
        },
        # Lleva nombres de proyecto de cliente y nombres de companeros.
        headers={"Cache-Control": "no-store"},
    )


# Deliberadamente NO hay una ruta de apoyo para los tests (un
# "/__test__/sesion" al estilo PALBE_SEED_DEMO_USERS). Se probo esa via y
# resulto ser fragil: el modulo `app` se importa una sola vez por proceso de
# pytest (cache de sys.modules), y la condicion `if DASHBOARD_TEST_LOGIN=="1"`
# se evalua en ese primer import -- que ocurre en el primer test del fichero,
# antes de que ningun test haya tenido ocasion de fijar la variable. Los
# tests que necesitan una sesion ya autenticada firman la cookie a mano con
# el mismo itsdangerous.TimestampSigner que usa SessionMiddleware (ver
# tests/test_app.py), en vez de depender de una ruta que en la practica
# nunca llegaba a registrarse.
