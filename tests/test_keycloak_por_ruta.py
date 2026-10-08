"""Keycloak colgado de una ruta, y el hilo que no se puede cortar.

Cuando las cinco herramientas comparten un solo nombre --`sge.example.com`--
Keycloak tambien tiene que colgar de una ruta, `/auth`. Y eso cambia el
**emisor** de los tokens, que es el dato mas delicado de la plataforma:

    antes   http://auth.<dominio>:8080/realms/sge
    despues http://<dominio>:8080/auth/realms/sge

Ese emisor viaja dentro de cada token y las cinco piezas lo comparan con su
`*_SSO_ISSUER`. Si una se queda atras, su login falla con un error de emisor
que no dice donde esta el problema.

Dos cosas se vigilan aqui, y las dos son de las que no avisan:

 1. **KC_HOSTNAME y el alias de red salen de la MISMA variable.** Son los dos
    extremos del mismo hilo: el alias es por donde llegan los contenedores, y
    KC_HOSTNAME es lo que Keycloak escribe en el token. Si se separan, el
    token se emite con un nombre que dentro de la red no resuelve.

 2. **Las llamadas entre piezas van por nombre de SERVICIO.** Cuando Keycloak
    se llame `<dominio>` dentro de la red, ese nombre dejara de apuntar al
    recibidor. Es inocuo hoy porque nadie llama al recibidor y porque
    `herramientas.yaml` usa `http://palbe:8000` y no el nombre publico. El dia
    que alguien cambie eso, este test salta.
"""

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[3]
COMPOSE = (RAIZ / "platform" / "compose.yml").read_text(encoding="utf-8")
HERRAMIENTAS = (RAIZ / "platform" / "portal" / "herramientas.yaml").read_text(
    encoding="utf-8"
)

# El bloque del servicio keycloak, con sus ${...} sin resolver: es el texto lo
# que interesa, no el valor que tomaria hoy.
BLOQUE_KC = re.search(r"^  keycloak:\n(?:(?:    .*|\s*)\n)*", COMPOSE, re.MULTILINE)


def test_el_bloque_de_keycloak_existe():
    assert BLOQUE_KC, "no se encontro el servicio keycloak en platform/compose.yml"


def test_la_ruta_relativa_es_configurable_y_por_defecto_la_raiz():
    """Sin la variable, `/`: exactamente como ha funcionado siempre."""
    assert re.search(
        r"^\s+KC_HTTP_RELATIVE_PATH:\s*\$\{KC_RUTA_BASE:-/\}",
        BLOQUE_KC.group(0),
        re.MULTILINE,
    ), "KC_HTTP_RELATIVE_PATH no sale de KC_RUTA_BASE con `/` por defecto"


def test_el_nombre_de_keycloak_sale_de_una_sola_variable():
    """KC_HOSTNAME y el alias de red, del mismo sitio.

    Este es el test que de verdad importa. Con dos variables distintas, un
    cambio de dominio a medias emite tokens con un nombre que los
    contenedores no resuelven, y el sintoma --"invalid issuer"-- no apunta
    a ninguno de los dos ficheros que hay que tocar.
    """
    bloque = BLOQUE_KC.group(0)
    nombre = r"\$\{KC_NOMBRE:-auth\.\$\{SGE_BASE_DOMAIN:-sge\.local\}\}"

    assert re.search(r"KC_HOSTNAME:.*" + nombre, bloque), (
        "KC_HOSTNAME no deriva de KC_NOMBRE"
    )
    assert re.search(r"^\s+- " + nombre + r"\s*$", bloque, re.MULTILINE), (
        "el alias de red no deriva de KC_NOMBRE"
    )


def test_KC_HOSTNAME_lleva_la_ruta_dentro():
    """Esto costó una caída, el 2026-09-30.

    Con `KC_HTTP_RELATIVE_PATH=/auth`, Keycloak sirve bajo `/auth` pero **no
    lo añade al emisor** que escribe en el token: emitía
    `http://<dominio>:8080/realms/sge` mientras el endpoint real era
    `http://<dominio>:8080/auth/realms/sge`. Las cinco piezas validan el
    emisor contra su `*_SSO_ISSUER`, así que ninguna podía entrar -- con un
    «invalid issuer» que no señala dónde está el problema.

    Por eso `KC_RUTA_BASE` tiene que aparecer en los DOS sitios: uno para que
    Keycloak sirva ahí, y otro para que lo diga en el emisor.
    """
    bloque = BLOQUE_KC.group(0)
    linea = next(
        (
            fila
            for fila in bloque.splitlines()
            if fila.strip().startswith("KC_HOSTNAME:")
        ),
        "",
    )
    assert linea, "no hay linea de KC_HOSTNAME"
    assert "KC_RUTA_BASE" in linea, (
        "KC_HOSTNAME no incluye KC_RUTA_BASE: Keycloak serviria bajo la ruta "
        "pero emitiria el emisor sin ella, y no podria entrar nadie"
    )


def test_hay_camino_por_ruta_y_sigue_el_de_nombre():
    bloque = BLOQUE_KC.group(0)
    assert "routers.keycloak.rule=" in bloque, "se ha perdido el camino por nombre"
    assert "routers.keycloak-ruta.rule=" in bloque, "no hay camino por ruta"
    assert "PathPrefix(`/auth`)" in bloque


def test_el_camino_por_ruta_NO_lleva_stripprefix():
    """A diferencia de las cuatro herramientas.

    Keycloak sirve bajo /auth el mismo, por KC_HTTP_RELATIVE_PATH, asi que
    espera recibir el prefijo. Quitarselo da 404 en todo -- incluido el
    descubrimiento OIDC, con lo que ninguna herramienta puede arrancar su
    login.
    """
    # Solo las ETIQUETAS: la palabra aparece en los comentarios de al lado,
    # explicando precisamente por que no esta. Buscarla en el bloque entero
    # hacia que el test se pillara los dedos con su propia documentacion.
    etiquetas = [
        fila.lower()
        for fila in BLOQUE_KC.group(0).splitlines()
        if not fila.lstrip().startswith("#")
    ]
    assert not any("stripprefix" in fila for fila in etiquetas)
    assert not any("keycloak-strip" in fila for fila in etiquetas)


def test_los_dos_routers_nombran_su_servicio():
    """Con dos routers sobre el mismo contenedor Traefik ya no puede
    deducirlo, y uno de los dos se quedaria sin servicio."""
    bloque = BLOQUE_KC.group(0)
    assert "routers.keycloak.service=keycloak" in bloque
    assert "routers.keycloak-ruta.service=keycloak" in bloque


@pytest.mark.parametrize("herramienta", yaml.safe_load(HERRAMIENTAS)["herramientas"])
def test_las_llamadas_internas_van_por_nombre_de_servicio(herramienta):
    """La otra mitad del contrato, y la que protege de la trampa del alias.

    Cuando Keycloak se llame `<dominio>` dentro de la red, ese nombre dejara
    de apuntar al recibidor. Da igual mientras las piezas se llamen por su
    nombre de servicio Docker. El dia que un `base_interna` pase a ser el
    nombre publico, esa llamada acabaria en Keycloak sin que nadie lo
    entienda -- asi que salta aqui.
    """
    base = herramienta.get("base_interna")
    if base is None:
        pytest.skip(f"{herramienta['id']} no declara base_interna")
    assert "${SGE_BASE_DOMAIN}" not in base, (
        f"{herramienta['id']}: base_interna usa el dominio publico. Las llamadas "
        "internas van por nombre de servicio Docker (http://palbe:8000)."
    )
    anfitrion = base.split("//", 1)[1].split(":")[0]
    assert "." not in anfitrion, (
        f"{herramienta['id']}: base_interna apunta a '{anfitrion}', que no es un "
        "nombre de servicio Docker."
    )
