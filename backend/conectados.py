"""
Quien esta trabajando ahora.

Incremento 2 del contrato de contexto, e independiente del 1: no necesita la
franja y no la bloquea. Contrato completo en
docs/runbooks/RUNBOOK_SERVIDOR_LINUX.md, seccion "Quien esta trabajando ahora".

La regla es una y no tiene letra pequena: **si lo estas usando, sales en
verde**. En pantalla es verde o nada.

Y NO se hace con Keycloak, aunque sea lo primero que parece: del realm,
`ssoSessionIdleTimeout` son 1800 s y las cookies de las herramientas 8 h, y
como cada herramienta habla con Keycloak UNA sola vez -- al canjear el codigo
-- la sesion del IdP caduca por inactividad a los 30 minutos mientras la
persona sigue trabajando toda la tarde. El verde se apagaria a media tarde de
alguien que esta dentro: mentiria en la direccion tranquilizadora.

Asi que lo dice cada herramienta, que es la unica que sabe cuando hiciste
algo. Esta mitad es logica pura, como contexto.py: se prueba entera sin
levantar nada.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

_log = logging.getLogger(__name__)

RUTA = "/api/plataforma/activos"
CABECERA_TOKEN = "X-SGE-Plataforma"

# Cinco minutos, el mismo numero que PALBE ya usaba para su "Online ahora".
#
# La ventana no es una salvedad, es mecanica: cerrar la pestana es invisible y
# ninguna herramienta puede avisar de que te has ido. Lo que apaga el verde es
# esto y no un cierre de sesion, porque nadie cierra sesion. Y por eso cinco
# minutos y no treinta.
VENTANA = timedelta(minutes=5)

# Cuanta gente se ensena. Son siete personas en el equipo; el tope es por si
# una herramienta contesta una lista absurda.
MAX_GENTE = 12

# Lo que se pinta si una herramienta solo conoce el sub. Igual que en el
# contexto: un UUID en crudo no se pinta nunca.
SIN_NOMBRE = "otra persona"

MAX_NOMBRE = 40

# Mismos tiempos que el contexto y por los mismos motivos.
ESPERA = httpx.Timeout(connect=0.3, read=1.2, write=0.3, pool=0.3)
TOPE_DURO = 3.0


@dataclass(frozen=True)
class Activo:
    sub: str
    nombre: str
    herramienta: str
    visto_en: datetime


def _texto(valor: object, tope: int | None = None) -> str:
    if not isinstance(valor, str):
        return ""
    limpio = valor.strip()
    return limpio[:tope] if tope else limpio


def _con_zona(momento: datetime) -> datetime:
    """Consciente de su zona. Hace falta porque las herramientas no escriben
    la fecha igual -- PALBE sin zona, Bartolo en UTC -- y restar una naive de
    una aware lanza TypeError. Mismo arreglo, y por el mismo motivo, que en
    contexto.py."""
    return momento.astimezone() if momento.tzinfo is None else momento


def _fecha(valor: object) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        return _con_zona(datetime.fromisoformat(valor))
    except ValueError:
        return None


def normalizar(
    herramienta_id: str, datos: object, ahora: datetime | None = None
) -> tuple[Activo, ...]:
    """Lo que contesta UNA herramienta, filtrado por la ventana.

    Nunca lanza: una herramienta que contesta mal no aporta a nadie, y nada
    mas. Este bloque no puede tumbar la portada."""
    if not isinstance(datos, list):
        return ()
    ahora = _con_zona(ahora or datetime.now())

    salida = []
    for entrada in datos[:MAX_GENTE]:
        if not isinstance(entrada, dict):
            continue
        sub = _texto(entrada.get("sub"))
        visto_en = _fecha(entrada.get("visto_en"))
        if not sub or visto_en is None:
            continue
        antiguedad = ahora - visto_en
        # El futuro se descarta. Con solo el tope superior, un reloj
        # adelantado colaba a alguien PARA SIEMPRE: la diferencia negativa
        # nunca supera la ventana. Es el mismo fallo que ya aparecio en los
        # companeros del contexto.
        if antiguedad < timedelta(0) or antiguedad > VENTANA:
            continue
        nombre = _texto(entrada.get("nombre"), MAX_NOMBRE) or SIN_NOMBRE
        salida.append(Activo(sub, nombre, herramienta_id, visto_en))
    return tuple(salida)


def fusionar(activos) -> tuple[Activo, ...]:
    """Una entrada por persona, la mas reciente.

    Se agrupa por `sub` y se queda la herramienta donde estuvo MAS
    RECIENTEMENTE: si acabas de pasar de PALBE a Bartolo, lo que interesa es
    que estas en Bartolo.

    Tenia un argumento `excepto` para dejar fuera a quien pregunta. Se retira
    el 2026-09-18: quien pregunta SI sale, y el argumento no lo usaba nadie
    mas -- mantenerlo seria dejar un parametro muerto."""
    por_sub: dict[str, Activo] = {}
    for a in activos:
        previo = por_sub.get(a.sub)
        if previo is None or a.visto_en > previo.visto_en:
            por_sub[a.sub] = a
    return tuple(sorted(por_sub.values(), key=lambda a: a.visto_en, reverse=True))


# --- El recibidor se lleva a si mismo ------------------------------------
#
# Cuatro sitios, no tres. El recibidor cuenta como uno mas y para saberlo no
# necesita preguntar a nadie: ya tiene la sesion y ya atiende su portada. Dos
# consecuencias buenas: es lo primero que funciona, y el bloque sigue saliendo
# aunque las tres herramientas esten caidas.
#
# En memoria, como el `_USER_LAST_SEEN` de PALBE. Es por proceso: con mas de
# un worker cada uno veria a los suyos, y la salida esta escrita en el propio
# codigo de PALBE -- una tabla o Redis. Hoy es un worker; queda como
# CONDICION antes de tocar ese numero, no como supuesto.

NOMBRE_PROPIO = "plataforma"


def apuntar(
    registro: dict, sub: str, nombre: str, ahora: datetime | None = None
) -> None:
    """Deja constancia de que esta persona esta en el recibidor."""
    if not sub:
        return
    registro[sub] = (
        _texto(nombre, MAX_NOMBRE) or SIN_NOMBRE,
        _con_zona(ahora or datetime.now()),
    )


def propios(registro: dict, ahora: datetime | None = None) -> tuple[Activo, ...]:
    """Quien esta en el recibidor ahora, limpiando de paso lo caducado.

    La limpieza va AQUI y no en un proceso aparte: el diccionario vive en
    memoria de un contenedor con 200 MB, y leerlo es el momento natural para
    tirar lo que ya no vale. Sin esto creceria una entrada por persona y por
    siempre."""
    ahora = _con_zona(ahora or datetime.now())
    for sub in [
        s
        for s, (_, visto) in registro.items()
        if not timedelta(0) <= ahora - visto <= VENTANA
    ]:
        del registro[sub]
    return tuple(
        Activo(sub, nombre, NOMBRE_PROPIO, visto)
        for sub, (nombre, visto) in registro.items()
    )


# --- La unica mitad que toca la red --------------------------------------


async def _preguntar(client, herramienta, token: str, ahora: datetime):
    """Le pregunta a UNA herramienta quien anda por ahi. Nunca propaga nada.

    Sin `sub` en la peticion, al contrario que el contexto: aqui la pregunta
    no es sobre una persona sino sobre quien esta dentro."""
    try:
        r = await client.get(
            herramienta.base_interna + RUTA,
            headers={CABECERA_TOKEN: token},
            timeout=ESPERA,
        )
        if r.status_code != 200:
            _log.warning("activos de %s: contesto %s", herramienta.id, r.status_code)
            return ()
        return normalizar(herramienta.id, r.json().get("activos"), ahora=ahora)
    except Exception as exc:
        # Amplio a proposito, igual que en el contexto: la respuesta correcta a
        # todo lo que pueda salir mal es la misma -- esta herramienta no aporta
        # a nadie. Solo el tipo, nunca el cuerpo ni los nombres.
        _log.warning("activos de %s: %s", herramienta.id, type(exc).__name__)
        return ()


async def consultar(
    client,
    herramientas,
    token: str,
    registro: dict,
    nombres: dict | None = None,
    ahora: datetime | None = None,
) -> tuple[Activo, ...]:
    """Quien esta trabajando ahora, en las herramientas y en el recibidor.

    Los propios se anaden SIEMPRE, incluso sin token y sin red: es lo que hace
    que el bloque salga aunque las tres herramientas esten caidas.

    Si se pasa `nombres`, se aprende de lo que cuenta cada herramienta y se
    aplica al resultado. El aprendizaje va sobre las respuestas EN CRUDO y no
    sobre las ya fusionadas, y eso no es un detalle: fusionar se queda una
    entrada por persona -- la mas reciente -- asi que si dos herramientas la
    reportan en el mismo instante, gana la primera del catalogo. Si PALBE es
    la primera y manda el usuario, aprender despues de fusionar aprendia
    justo el nombre que se queria corregir. Medido con un test."""
    ahora = _con_zona(ahora or datetime.now())
    todos = list(propios(registro, ahora=ahora))

    candidatas = [h for h in herramientas if h.base_interna and "activos" in h.ofrece]
    if candidatas and token:
        resultados = await asyncio.gather(
            *(
                asyncio.wait_for(_preguntar(client, h, token, ahora), TOPE_DURO)
                for h in candidatas
            ),
            return_exceptions=True,
        )
        for r in resultados:
            if isinstance(r, tuple):
                todos.extend(r)

    if nombres is not None:
        for a in todos:
            recordar_nombre(nombres, a.sub, a.nombre)
        return con_nombres(fusionar(todos), nombres)
    return fusionar(todos)


# --- El nombre, el mismo en todas partes ---------------------------------
#
# Fallo visto en una prueba con dos personas: la misma persona salia con
# nombre completo o con su usuario segun DONDE estuviera. El recibidor la ve
# como "Cesar Suela" -- el claim `name` de Keycloak -- y las herramientas como
# "csuela", que es su `users.username`.
#
# El recibidor es la puerta de entrada de la plataforma, asi que es el unico
# que conoce el nombre de Keycloak de TODO el mundo. Se guarda lo que ve y
# manda el suyo cuando lo sabe.

# Tope del directorio. No lo poda la ventana de cinco minutos -- un nombre
# sigue valiendo cuando la persona se va -- asi que necesita su propio limite.
# Son siete personas; 200 es holgura para años.
MAX_NOMBRES = 200


def recordar_nombre(directorio: dict, sub: str, nombre: str) -> None:
    """Apunta como se llama esta persona, para que salga igual en todas
    partes."""
    if not sub:
        return
    limpio = _texto(nombre, MAX_NOMBRE)
    if not limpio:
        return
    if len(directorio) >= MAX_NOMBRES and sub not in directorio:
        # Se vacia en vez de ir echando de uno en uno: llegar aqui significa
        # que algo va raro (son siete personas), y un directorio a medias
        # daria nombres mezclados sin que nadie entienda por que.
        directorio.clear()
    directorio[sub] = limpio


def con_nombres(activos, directorio: dict):
    """Cambia el nombre que dio cada herramienta por el que conoce el
    recibidor.

    Si no lo conoce -- el directorio se llena cuando cada persona pasa por
    aqui, asi que tras reiniciar el contenedor esta vacio -- se queda el de la
    herramienta: mejor su usuario que "otra persona"."""
    return tuple(
        a
        if a.sub not in directorio
        else Activo(a.sub, directorio[a.sub], a.herramienta, a.visto_en)
        for a in activos
    )
