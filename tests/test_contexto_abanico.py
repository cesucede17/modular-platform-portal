"""
El abanico: el recibidor preguntando a las tres herramientas.

Se usa httpx.MockTransport, o sea el httpx de verdad con un transporte de
prueba, en vez de parchear nuestro propio codigo: asi lo que se prueba es lo
que pasara de verdad, incluidas las cabeceras y la cadena de consulta.

Lo que vigilan estos tests es una sola propiedad, la mas valiosa de la pieza:
una herramienta que falle NO puede quitarle la pastilla a las demas, y las
tres fallando no pueden tumbar la portada.
"""

import asyncio
from datetime import datetime

import httpx

import catalogo
import contexto

AHORA = datetime(2026, 9, 18, 17, 0, 0)
TOKEN = "un-secreto-de-prueba"

TRES = [
    catalogo.Herramienta(
        id=svc,
        nombre=svc.upper(),
        descripcion="d",
        icono="i",
        url=f"http://{svc}.dominio/sso/login",
        rol=svc,
        base_interna=f"http://{svc}:{puerto}",
        ofrece=("contexto", "activos"),
    )
    for svc, puerto in (("palbe", 8000), ("tambora", 8501), ("bartolo", 8502))
]


def _cuerpo(herramienta, titulo):
    return {
        "herramienta": herramienta,
        "contexto": {
            "titulo": titulo,
            "detalle": "",
            "url": "/x",
            "visto_en": "2026-09-18T16:00:00",
            "companeros": [],
        },
    }


def _cliente(responder):
    return httpx.AsyncClient(transport=httpx.MockTransport(responder))


def _consultar(herramientas, responder, sub="sub-de-cesar"):
    async def corre():
        async with _cliente(responder) as client:
            return await contexto.consultar(
                client, herramientas, sub=sub, token=TOKEN, ahora=AHORA
            )

    return asyncio.run(corre())


def test_las_tres_contestan_y_salen_las_tres_en_orden_de_catalogo():
    def responder(peticion):
        svc = peticion.url.host
        return httpx.Response(200, json=_cuerpo(svc, f"lo de {svc}"))

    ctxs = _consultar(TRES, responder)

    assert [c.herramienta for c in ctxs] == ["palbe", "tambora", "bartolo"]


def test_una_herramienta_caida_no_le_quita_la_pastilla_a_las_demas():
    """Es la propiedad que justifica todo el diseno. Se prueban de golpe las
    cuatro formas de fallar, porque para el recibidor son la misma cosa."""
    fallos = {
        "palbe": lambda p: (_ for _ in ()).throw(
            httpx.ConnectError("caido", request=p)
        ),
        "tambora": lambda p: httpx.Response(500, text="boom"),
        "bartolo": lambda p: httpx.Response(200, text="esto-no-es-json"),
    }

    for roto, fallo in fallos.items():

        def responder(peticion, roto=roto, fallo=fallo):
            if peticion.url.host == roto:
                return fallo(peticion)
            return httpx.Response(200, json=_cuerpo(peticion.url.host, "bien"))

        ctxs = _consultar(TRES, responder)

        assert [c.herramienta for c in ctxs] == [h.id for h in TRES if h.id != roto], (
            roto
        )


def test_con_las_tres_caidas_no_hay_franja_y_no_se_lanza_nada():
    def responder(peticion):
        raise httpx.ConnectError("todo caido", request=peticion)

    assert _consultar(TRES, responder) == []


def test_una_herramienta_sin_url_interna_no_se_le_pregunta():
    """El campo es opcional: si esta vacio, no se le pregunta. Sin esto, una
    herramienta futura sin contexto costaria una peticion fallida por carga."""
    preguntadas = []

    def responder(peticion):
        preguntadas.append(peticion.url.host)
        return httpx.Response(200, json=_cuerpo(peticion.url.host, "bien"))

    muda = catalogo.Herramienta(
        id="muda",
        nombre="Muda",
        descripcion="d",
        icono="i",
        url="http://muda.dominio/",
        rol="muda",
        base_interna="",
        ofrece=(),
    )

    ctxs = _consultar(TRES + [muda], responder)

    assert "muda" not in preguntadas
    assert "muda" not in [c.herramienta for c in ctxs]


def test_el_secreto_va_en_cabecera_y_nunca_en_la_url():
    """Traefik corre con --accesslog=true: una URL con secreto acaba en un log
    persistente. El sub si va en la cadena de consulta -- no es un secreto."""
    vistas = []

    def responder(peticion):
        vistas.append(peticion)
        return httpx.Response(200, json=_cuerpo(peticion.url.host, "bien"))

    _consultar(TRES, responder)

    for p in vistas:
        assert p.headers["X-SGE-Plataforma"] == TOKEN
        assert TOKEN not in str(p.url)
        assert p.url.params["sub"] == "sub-de-cesar"


def test_se_pregunta_a_la_ruta_del_contrato():
    """La ruta es parte del contrato: si cambia aqui y no en las herramientas,
    la franja no sale y no hay ningun error que lo explique."""
    rutas = set()

    def responder(peticion):
        rutas.add(peticion.url.path)
        return httpx.Response(200, json=_cuerpo(peticion.url.host, "bien"))

    _consultar(TRES, responder)

    assert rutas == {"/api/plataforma/contexto"}


def test_una_herramienta_que_no_termina_no_cuelga_la_franja():
    """Hallazgo de la revision: `ESPERA` fija un tiempo por OPERACION
    (read=1.2), no un total. Una herramienta que fuera soltando un trozo por
    segundo mantendria /api/contexto abierto indefinidamente, y con el la
    peticion del navegador.

    El techo va POR HERRAMIENTA y no sobre el gather: un wait_for sobre el
    gather cancela todas las tareas al vencer, asi que la lenta se llevaba
    por delante las pastillas de las que ya habian contestado. Se vio con
    este mismo test.
    """
    import asyncio as _asyncio
    import time

    async def responder(peticion):
        if peticion.url.host == "palbe":
            await _asyncio.sleep(30)
        return httpx.Response(200, json=_cuerpo(peticion.url.host, "bien"))

    t0 = time.monotonic()
    ctxs = _consultar(TRES, responder)
    tardado = time.monotonic() - t0

    assert [c.herramienta for c in ctxs] == ["tambora", "bartolo"]
    assert tardado < 10, f"tardo {tardado:.1f}s: no hay techo duro"


def test_una_respuesta_enorme_no_aporta_pastilla():
    """El tope de tamano se comprobaba sobre `respuesta.content`, que solo
    existe cuando httpx YA ha traido el cuerpo entero a memoria. Una
    herramienta que devolviera 200 MB los metia en el recibidor -- que corre
    con mem_limit 200m -- antes de que el tope de 8 KiB los rechazara."""
    enorme = b'{"herramienta":"palbe","contexto":null}' + b" " * 40000

    def responder(peticion):
        if peticion.url.host == "palbe":
            return httpx.Response(
                200, content=enorme, headers={"Content-Type": "application/json"}
            )
        return httpx.Response(200, json=_cuerpo(peticion.url.host, "bien"))

    ctxs = _consultar(TRES, responder)

    assert [c.herramienta for c in ctxs] == ["tambora", "bartolo"]
