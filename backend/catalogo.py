"""
El catalogo de herramientas del recibidor.

Logica pura: lee un YAML y filtra por rol. Ni red, ni framework, ni estado --
por eso se puede probar entero sin levantar nada.

OJO, y esto importa: filtrar_por_roles decide que tarjetas SE VEN, no a que
se puede entrar. Cada herramienta guarda su propia puerta. Si algun dia
alguien piensa en relajar las comprobaciones de una herramienta "porque el
recibidor ya filtra", esto es lo que tiene que leer primero.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_CAMPOS = ("id", "nombre", "descripcion", "icono", "url", "rol")

# Campos que pueden faltar, con su valor por defecto. Van aparte porque para
# los obligatorios "ausente" y "vacio" son el mismo fallo (ver abajo), y aqui
# hace falta lo contrario: una herramienta puede no ofrecer contexto, y
# entonces el recibidor simplemente no le pregunta.
_CAMPOS_OPCIONALES = {"base_interna": ""}

# El dominio de la plataforma, para expandirlo en las URLs del catalogo.
# Mismo nombre y mismo default que en los dos ficheros de compose: un solo
# sitio donde cambiarlo cuando el dominio cambie (hoy nip.io en el servidor,
# manana el nombre que de IT).
_DOMINIO_POR_DEFECTO = "sge.local"


def _por_rutas() -> bool:
    """Si la plataforma va por rutas en vez de por subdominios.

    Un solo nombre para las cinco herramientas --`<dominio>/palbe`-- es lo
    que permite un solo certificado el dia que haya HTTPS, sin comodin y sin
    seis SAN. Mientras esto este apagado, las tarjetas apuntan a los
    subdominios de siempre.

    Sale del entorno y no del YAML porque es una propiedad del DESPLIEGUE, no
    del catalogo: el mismo fichero de datos sirve para las dos formas, y es
    la maquina la que dice en cual esta.
    """
    return os.environ.get("SGE_RUTAS", "0") == "1"


def _expandir_dominio(url: str) -> str:
    """Sustituye ${SGE_BASE_DOMAIN} por el dominio del entorno.

    Solo en el campo "url", y solo esta variable: el catalogo es un fichero
    de datos, no una plantilla, y expandir cualquier cosa invitaria a meter
    logica aqui. Si la variable no esta puesta se usa "sge.local", el valor
    historico -- asi un portatil de desarrollo sigue funcionando sin .env,
    igual que hace el default de los compose."""
    dominio = os.environ.get("SGE_BASE_DOMAIN") or _DOMINIO_POR_DEFECTO
    return url.replace("${SGE_BASE_DOMAIN}", dominio)


@dataclass(frozen=True)
class Herramienta:
    id: str
    nombre: str
    descripcion: str
    icono: str
    url: str
    rol: str
    # Como llegar a esta herramienta DENTRO de sge_net. Es un NOMBRE DE
    # SERVICIO ("http://palbe:8000"), no el host publico: la publica daria la
    # vuelta por Traefik y ataria la llamada interna al dominio de fuera.
    # Vacia = no se le pregunta nada.
    base_interna: str = ""
    # QUE de la plataforma implementa. Son dos cosas independientes y se vio
    # en cuanto hubo tres herramientas: Tambora no ofrece "contexto" -- no
    # tiene nada que reanudar -- pero si "activos", porque saber quien esta
    # dentro lo sabe perfectamente. Antes esto era un solo campo y Tambora se
    # quedaba fuera de las dos cosas por estar fuera de una.
    ofrece: tuple[str, ...] = ()


def cargar_catalogo(ruta: Path) -> list[Herramienta]:
    """Lee el catalogo. Lanza ValueError con un mensaje util si esta mal.

    Se falla al arrancar a proposito: un recibidor vacio por un YAML roto
    parece un problema de permisos y manda a quien lo diagnostique al sitio
    equivocado."""
    try:
        datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"El catalogo {ruta} no es YAML valido: {exc}") from exc

    if not isinstance(datos, dict) or "herramientas" not in datos:
        raise ValueError(f"El catalogo {ruta} debe tener una clave 'herramientas'")

    salida: list[Herramienta] = []
    for i, entrada in enumerate(datos["herramientas"] or [], start=1):
        if not isinstance(entrada, dict):
            raise ValueError(
                f"La herramienta #{i} del catalogo {ruta} debe ser un mapeo, "
                f"no {entrada!r}"
            )
        # Un campo ausente y un campo vacio son el mismo fallo: un rol vacio
        # ("") coincidiria con cualquier claim vacio de una lista de roles
        # (p.ej. un split(",") con coma final), y la tarjeta se enseñaria a
        # cualquiera. Enseñar de menos es molesto; enseñar de mas, no.
        faltan = [c for c in _CAMPOS if not entrada.get(c)]
        if faltan:
            raise ValueError(
                f"A la herramienta #{i} del catalogo {ruta} le faltan campos: "
                f"{', '.join(faltan)}"
            )
        campos = {c: str(entrada[c]) for c in _CAMPOS}
        # La puerta por RUTA, si el despliegue va asi y la herramienta la
        # declara. Se exige que la declare en vez de derivarla de `id` a
        # proposito: `http://<dominio>:8080/<id>/sso/login` seria adivinar, y
        # el dia que una herramienta no siga ese patron la tarjeta llevaria a
        # un 404 sin que nada lo avisara. El YAML sigue siendo la fuente de
        # verdad de las URLs, que es su cometido.
        if _por_rutas():
            por_ruta = entrada.get("url_ruta")
            if not por_ruta:
                raise ValueError(
                    f"SGE_RUTAS=1 pero la herramienta '{entrada.get('id')}' del "
                    f"catalogo {ruta} no declara url_ruta: su tarjeta llevaria "
                    f"al subdominio, que en este despliegue no existe"
                )
            campos["url"] = str(por_ruta)
        campos["url"] = _expandir_dominio(campos["url"])
        # Los opcionales NO pasan por _expandir_dominio: base_interna es un
        # nombre de la red interna, no un host publico, y expandirle el
        # dominio es justo la clase de "coherencia" que alguien anadiria.
        for campo, defecto in _CAMPOS_OPCIONALES.items():
            valor = entrada.get(campo)
            campos[campo] = str(valor) if valor else defecto
        # `ofrece` es una lista en el YAML y una tupla aqui, para que
        # Herramienta siga siendo inmutable.
        ofrece = entrada.get("ofrece") or []
        campos["ofrece"] = (
            tuple(str(x) for x in ofrece) if isinstance(ofrece, list) else ()
        )
        salida.append(Herramienta(**campos))
    return salida


def filtrar_por_roles(
    catalogo: list[Herramienta], roles: list[str]
) -> list[Herramienta]:
    """Las herramientas cuyo rol esta entre los del usuario.

    Sin roles, lista vacia -- y esa lista vacia es un estado legitimo del
    recibidor, no un error: cualquier usuario nuevo de Keycloak la tendra
    hasta que alguien le asigne roles."""
    tiene = set(roles or [])
    return [h for h in catalogo if h.rol in tiene]
