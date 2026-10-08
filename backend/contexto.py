"""
El contexto de usuario: que ensena la franja "Sigue donde lo dejaste".

Dos mitades, como catalogo.py. Arriba, logica pura: valida y normaliza lo que
contesta una herramienta, sin red ni framework ni estado, asi que se prueba
entera sin levantar nada. Abajo, `consultar`: la unica funcion que toca la
red, fina a proposito.

La propiedad que sostiene todo esto: una herramienta que falle no puede
quitarle la pastilla a las demas, y las tres fallando no pueden tumbar la
portada. `GET /` del recibidor no llama a nadie.

Contrato completo en docs/runbooks/RUNBOOK_SERVIDOR_LINUX.md, seccion "El contexto de
usuario: lo que debe cumplir cada herramienta".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

_log = logging.getLogger(__name__)

# La ruta del contrato. Parte del contrato, no un detalle: si cambia aqui y no
# en las herramientas, la franja no sale y no hay ningun error que lo explique.
RUTA = "/api/plataforma/contexto"

# El secreto va en cabecera y JAMAS en la cadena de consulta: Traefik corre
# con --accesslog=true y una URL con secreto acaba en un log persistente.
CABECERA_TOKEN = "X-SGE-Plataforma"

# Por herramienta, y las tres en paralelo: el reloj de pared es la mas lenta.
# Corto a proposito -- esto es un adorno de la portada, y mas vale una franja
# incompleta que una portada lenta.
ESPERA = httpx.Timeout(connect=0.3, read=1.2, write=0.3, pool=0.3)

# Tope de lectura por respuesta. Una herramienta no debe poder hacer pesada
# la portada, ni por descuido ni a proposito.
MAX_BYTES = 8192

# Techo duro POR HERRAMIENTA, por encima de ESPERA. Hace falta porque ESPERA
# fija tiempos por OPERACION: una herramienta que fuera soltando un trozo por
# segundo nunca agota el `read` y mantendria /api/contexto abierto
# indefinidamente, con la peticion del navegador esperando detras.
#
# Por herramienta y NO sobre el gather: un wait_for sobre el gather cancela
# TODAS las tareas al vencer, asi que una herramienta lenta se llevaba por
# delante las pastillas de las que ya habian contestado. Como van en paralelo,
# un techo por herramienta acota igual el conjunto.
TOPE_DURO = 3.0

# La jornada. El umbral es SOLO para companeros: es un aviso de colision, y
# alguien que estuvo ahi hace tres semanas no te va a pisar. El contexto
# propio no caduca nunca -- filtrarlo por antiguedad seria quitarle el enlace
# a alguien justo cuando mas falta le hace recordar donde estaba.
VENTANA_COMPANEROS = timedelta(hours=12)

# Cuantos companeros se ensenan. Son un aviso, no un listado: con mas de tres
# deja de leerse de un vistazo, que es lo unico que se le pide.
MAX_COMPANEROS = 3

# Lo que se pinta cuando la herramienta solo conoce el sub y no el nombre.
# Regla 7 del contrato: un UUID en crudo no se pinta nunca -- es inutil para
# quien lo lee e identificador para quien lo recoja.
SIN_NOMBRE = "otra persona"

# Topes de longitud. La franja es de la plataforma: una herramienta no decide
# cuanto ocupa en la portada de las demas, ni puede hacerla pesada.
MAX_TITULO = 120
MAX_DETALLE = 160
MAX_NOMBRE = 40


@dataclass(frozen=True)
class Companero:
    nombre: str
    visto_en: datetime


@dataclass(frozen=True)
class Contexto:
    herramienta: str
    titulo: str
    detalle: str
    url: str
    companeros: tuple[Companero, ...] = ()
    # Cuando estuviste tu ahi por ultima vez. Significa lo MISMO en las dos
    # herramientas que la ofrecen: PALBE la escribe al navegar y Bartolo al
    # abrir una auditoria. None mientras esa persona no haya vuelto a pasar
    # desde el despliegue del 2026-09-18, y el contrato la admite vacia.
    #
    # NO le aplica el umbral de 12 h: ese es solo de companeros. "Estuviste
    # hace tres semanas" es informacion util; ocultarla no lo es.
    visto_en: datetime | None = None


def _texto(valor: object, tope: int | None = None) -> str:
    """Un campo de texto del contrato, o cadena vacia. Nunca lanza."""
    if not isinstance(valor, str):
        return ""
    limpio = valor.strip()
    return limpio[:tope] if tope else limpio


def redactar_antiguedad(visto_en: datetime, ahora: datetime) -> str:
    """ "hace 5 min". Se redacta aqui, en el servidor, y no en el navegador:
    asi no hay desfase de reloj del cliente y no se manda ninguna marca de
    tiempo al front. Quien lee juzga; el sistema no decide por el."""
    segundos = max(0, int((_con_zona(ahora) - _con_zona(visto_en)).total_seconds()))
    if segundos < 60:
        return "ahora mismo"
    minutos = segundos // 60
    if minutos < 60:
        return f"hace {minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"hace {horas} h"
    # De aqui para abajo hizo falta al usar esto para la fecha del contexto,
    # que NO caduca: escrito solo para companeros (topados a 12 h) paraba en
    # horas y habria dicho "hace 500 h".
    dias = horas // 24
    if dias == 1:
        return "ayer"
    if dias < 7:
        return f"hace {dias} dias"
    semanas = dias // 7
    if semanas < 5:
        return f"hace {semanas} semana" + ("s" if semanas > 1 else "")
    meses = dias // 30
    return f"hace {meses} mes" + ("es" if meses > 1 else "")


def _url_relativa(valor: object) -> str:
    """La url de reanudacion, o cadena vacia si no es de fiar.

    Tiene que ser relativa. Una absoluta convertiria la franja en un
    redirector abierto: el recibidor pintaria un enlace a donde dijera la
    herramienta. Y "//x" y "/\\x" empiezan por "/" pero el navegador los
    resuelve como protocol-relative -> https://x, que es la base del phishing
    con el dominio corporativo legitimo. Es la misma comprobacion que ya hay
    en app_palbe_4.py:1623, y por el mismo motivo."""
    url = _texto(valor)
    if not url.startswith("/") or url.startswith("//") or url.startswith("/\\"):
        return ""
    return url


def _con_zona(momento: datetime) -> datetime:
    """La misma marca, siempre consciente de su zona.

    Hace falta porque las herramientas NO escriben la fecha igual, y el
    contrato solo les pide ISO-8601: Bartolo usa
    `datetime.now(timezone.utc).isoformat()` -- con zona -- y PALBE
    `datetime.now().isoformat()` -- sin ella. Restar una naive de una aware
    lanza TypeError, el except del abanico se lo tragaria, y la pastilla no
    apareceria NUNCA con un log que solo dice "TypeError".

    Una fecha sin zona se interpreta como hora local, que es lo que quiso
    decir quien la escribio con datetime.now(). Y se usa astimezone() en vez
    de replace(tzinfo=...) porque replace fija el offset a ciegas: es la
    trampa de comparar dos aware con el mismo tzinfo, que ignora los offsets
    de verdad."""
    return momento.astimezone() if momento.tzinfo is None else momento


def _fecha(valor: object) -> datetime | None:
    """Una marca ISO-8601 del contrato, o None. Nunca lanza."""
    if not isinstance(valor, str):
        return None
    try:
        return _con_zona(datetime.fromisoformat(valor))
    except ValueError:
        return None


def _companeros(valor: object, ahora: datetime) -> tuple[Companero, ...]:
    """Quien mas anda en ESE proyecto y podria pisarte.

    Lista vacia es un resultado legitimo: una herramienta sin trabajo
    compartido no tiene a nadie que pueda pisarte, y eso es cumplimiento del
    contrato. Una entrada rara se descarta y no tumba la pastilla -- los
    companeros son un extra, la pastilla es lo importante."""
    if not isinstance(valor, list):
        return ()

    ahora = _con_zona(ahora)
    salida: list[Companero] = []
    for entrada in valor:
        if not isinstance(entrada, dict):
            continue
        visto_en = _fecha(entrada.get("visto_en"))
        if visto_en is None:
            continue
        antiguedad = ahora - visto_en
        # El futuro se descarta. Con solo el tope superior, una herramienta
        # con el reloj adelantado -- o una fecha naive interpretada en otra
        # zona -- colaba un companero que pasaba el filtro PARA SIEMPRE (la
        # diferencia negativa nunca supera la ventana), salia primero por el
        # orden descendente, y se pintaba como "ahora mismo" porque la
        # redaccion recorta en cero. Un aviso de colision permanente y falso.
        if antiguedad < timedelta(0) or antiguedad > VENTANA_COMPANEROS:
            continue
        nombre = _texto(entrada.get("nombre"), MAX_NOMBRE) or SIN_NOMBRE
        salida.append(Companero(nombre, visto_en))

    salida.sort(key=lambda c: c.visto_en, reverse=True)
    return tuple(salida[:MAX_COMPANEROS])


def normalizar(
    herramienta_id: str, datos: object, ahora: datetime | None = None
) -> Contexto | None:
    """La respuesta de una herramienta, o None si no hay nada que ensenar.

    Nunca lanza. Cualquier fallo -- respuesta rara, campos ausentes, tipos
    que no tocan, url que no es relativa -- significa que esa herramienta no
    aporta pastilla, y nada mas: la franja no puede tumbar la portada.

    Devolver None NO es un error. Es el estado normal de un usuario nuevo, de
    quien no tiene fila en sso_user_mapping, y hoy de tres personas reales con
    cero proyectos en PALBE."""
    if not isinstance(datos, dict):
        return None
    ctx = datos.get("contexto")
    if not isinstance(ctx, dict):
        return None

    titulo = _texto(ctx.get("titulo"), MAX_TITULO)
    url = _url_relativa(ctx.get("url"))
    if not titulo or not url:
        return None

    return Contexto(
        herramienta=herramienta_id,
        titulo=titulo,
        detalle=_texto(ctx.get("detalle"), MAX_DETALLE),
        url=url,
        companeros=_companeros(ctx.get("companeros"), ahora or datetime.now()),
        visto_en=_fecha(ctx.get("visto_en")),
    )


# --- La unica mitad que toca la red ---------------------------------------


def demasiado_grande(cabeceras) -> bool:
    """True si la respuesta se declara mas grande que el tope.

    Se mira el `content-length` ANTES de tocar el cuerpo. Comprobarlo sobre
    `respuesta.content`, como se hacia, llega tarde: ese atributo solo existe
    cuando httpx YA ha traido el cuerpo entero a memoria, asi que una
    herramienta que devolviera 200 MB los metia en el recibidor -- que corre
    con mem_limit 200m -- antes de que el tope los rechazara."""
    try:
        return int(cabeceras.get("content-length", 0)) > MAX_BYTES
    except TypeError, ValueError:
        return False


async def _preguntar(
    client: httpx.AsyncClient, herramienta, sub: str, token: str, ahora: datetime
) -> Contexto | None:
    """Le pregunta su contexto a UNA herramienta. Nunca propaga un fallo.

    Cualquier cosa que salga mal -- conexion, codigo distinto de 200, cuerpo
    que no es JSON, respuesta enorme -- significa que esta herramienta no
    aporta pastilla. Se registra una vez, y NUNCA el sub, ni el cuerpo, ni
    nombres de proyecto: la franja lleva datos de cliente."""
    try:
        respuesta = await client.get(
            herramienta.base_interna + RUTA,
            params={"sub": sub},
            headers={CABECERA_TOKEN: token},
            timeout=ESPERA,
        )
        if respuesta.status_code != 200:
            _log.warning(
                "contexto de %s: la herramienta contesto %s",
                herramienta.id,
                respuesta.status_code,
            )
            return None
        # Las dos comprobaciones: la declarada y la real. La segunda cubre a
        # quien no manda content-length (respuesta con codificacion en
        # trozos), y ahi el tope total del abanico es la otra red.
        if demasiado_grande(respuesta.headers) or len(respuesta.content) > MAX_BYTES:
            _log.warning("contexto de %s: respuesta demasiado grande", herramienta.id)
            return None
        return normalizar(herramienta.id, respuesta.json(), ahora=ahora)
    except Exception as exc:
        # Amplio a proposito: la lista de lo que httpx y json pueden lanzar es
        # larga y crece, y aqui la respuesta correcta a todo es la misma --
        # esta herramienta no aporta pastilla. Solo el tipo, no el mensaje.
        _log.warning("contexto de %s: %s", herramienta.id, type(exc).__name__)
        return None


async def consultar(
    client: httpx.AsyncClient,
    herramientas: list,
    sub: str,
    token: str,
    ahora: datetime | None = None,
) -> list[Contexto]:
    """Pregunta a todas las herramientas que ofrezcan contexto, en paralelo.

    Devuelve las pastillas en el orden del catalogo, no en el de llegada: la
    franja no debe bailar segun cual conteste antes.

    Sin cache, y es deliberado: el sentido de los companeros es avisar de que
    alguien esta en ese proyecto AHORA, asi que una respuesta de hace diez
    minutos es justo la equivocada. Con siete personas y unas pocas cargas al
    dia, no hay nada que optimizar."""
    ahora = ahora or datetime.now()
    # Solo a quien lo ofrezca: `ofrece` y `base_interna` son campos distintos
    # porque saber reanudar y saber quien esta dentro son cosas
    # independientes (ver catalogo.py).
    candidatas = [h for h in herramientas if h.base_interna and "contexto" in h.ofrece]
    if not candidatas or not token:
        return []

    resultados = await asyncio.gather(
        *(
            asyncio.wait_for(_preguntar(client, h, sub, token, ahora), TOPE_DURO)
            for h in candidatas
        ),
        return_exceptions=True,
    )
    return [r for r in resultados if isinstance(r, Contexto)]
