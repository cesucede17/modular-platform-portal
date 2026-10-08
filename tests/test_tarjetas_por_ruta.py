"""Las tarjetas del recibidor cuando la plataforma va por rutas.

El recibidor es la unica pieza que NO cambia al pasar a rutas: se queda en la
raiz. Pero sus cinco tarjetas si, porque apuntan a las herramientas -- y si
se quedan apuntando a `palbe.<dominio>`, el portal carga perfectamente y los
cinco enlaces llevan a un nombre que en ese despliegue no existe.

Es el fallo mas facil de dejarse de toda la migracion: no rompe nada visible
en el propio portal, que es lo que uno mira para comprobar que ha ido bien.
"""

import re
from pathlib import Path

import pytest
import yaml

from catalogo import cargar_catalogo

RAIZ = Path(__file__).resolve().parents[3]
CATALOGO = RAIZ / "platform" / "portal" / "herramientas.yaml"
COMPOSE = (RAIZ / "platform" / "compose.yml").read_text(encoding="utf-8")
DATOS = yaml.safe_load(CATALOGO.read_text(encoding="utf-8"))["herramientas"]


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    monkeypatch.setenv("SGE_BASE_DOMAIN", "sge.ejemplo.invalido")
    monkeypatch.delenv("SGE_RUTAS", raising=False)


def test_por_defecto_las_tarjetas_van_a_los_subdominios():
    """Sin la variable, todo como hoy. Es lo que permite desplegar esto sin
    cambiar el comportamiento."""
    for h in cargar_catalogo(CATALOGO):
        assert h.url.startswith(f"http://{h.id}.sge.ejemplo.invalido:8080/")


def test_con_SGE_RUTAS_las_tarjetas_van_a_las_rutas(monkeypatch):
    monkeypatch.setenv("SGE_RUTAS", "1")
    for h in cargar_catalogo(CATALOGO):
        assert h.url == f"http://sge.ejemplo.invalido:8080/{h.id}/sso/login", (
            f"la tarjeta de {h.id} no apunta a su ruta"
        )


def test_todas_declaran_su_url_ruta():
    """La que evita el fallo silencioso.

    Si una herramienta nueva entra al catalogo sin `url_ruta`, en un
    despliegue por rutas su tarjeta llevaria al subdominio -- y ahi no hay
    nada. `cargar_catalogo` revienta en ese caso, pero solo si alguien
    arranca con SGE_RUTAS=1; esto lo afirma siempre.
    """
    sin = [h["id"] for h in DATOS if not h.get("url_ruta")]
    assert sin == [], f"sin url_ruta: {sin}"


def test_falta_url_ruta_revienta_al_arrancar(tmp_path, monkeypatch):
    """Y revienta al arrancar, no al pulsar la tarjeta.

    Un recibidor que arranca con cinco enlaces rotos es peor que uno que no
    arranca: el segundo se diagnostica en un minuto.
    """
    monkeypatch.setenv("SGE_RUTAS", "1")
    copia = dict(DATOS[0])
    copia.pop("url_ruta")
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({"herramientas": [copia]}), encoding="utf-8")
    with pytest.raises(ValueError, match="url_ruta"):
        cargar_catalogo(p)


@pytest.mark.parametrize("h", DATOS, ids=lambda h: h["id"])
def test_las_dos_formas_son_la_misma_puerta(h):
    """`url` y `url_ruta` tienen que llevar al mismo sitio por dos caminos.

    Estan escritas a mano las dos, asi que pueden separarse: aqui se
    comprueba que lo que cuelga del host es identico, y que la de ruta lleva
    el id de la herramienta delante.
    """
    resto = re.sub(r"^http://[^/]+", "", h["url"])
    resto_ruta = re.sub(r"^http://[^/]+", "", h["url_ruta"])
    assert resto_ruta == f"/{h['id']}{resto}", (
        f"{h['id']}: '{resto_ruta}' no es '/{h['id']}' + '{resto}'"
    )


def test_SGE_RUTAS_llega_al_contenedor_del_recibidor():
    """Misma leccion que con SGE_BASE_DOMAIN, y van seis veces: una variable
    que el codigo lee y el compose no pasa es una variable que en produccion
    vale su default, en silencio."""
    bloque = re.search(r"^  dashboard:\n(?:(?:    .*|\s*)\n)*", COMPOSE, re.MULTILINE)
    assert bloque, "no se encontro el servicio dashboard"
    assert re.search(r"^\s+SGE_RUTAS:\s*\S", bloque.group(0), re.MULTILINE), (
        "SGE_RUTAS no llega al contenedor del recibidor: las tarjetas "
        "apuntarian a los subdominios aunque el despliegue vaya por rutas"
    )
