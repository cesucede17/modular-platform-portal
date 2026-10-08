"""
El catalogo y el filtrado por rol.

La regla que estos tests protegen: se ve lo que tu rol permite ver, ni una
tarjeta mas. Y el estado vacio es un estado legitimo, no un fallo.
"""

import pytest


def test_el_catalogo_real_carga(catalogo_real):
    assert len(catalogo_real) >= 1
    ids = [h.id for h in catalogo_real]
    assert "palbe" in ids


def test_palbe_apunta_al_sitio_correcto(catalogo_real):
    palbe = next(h for h in catalogo_real if h.id == "palbe")
    # /sso/login, no la raiz: la raiz cae detras del login propio de PALBE
    # (redirige a /login sin sesion de PALBE), mientras que /sso/login es
    # ruta publica y, con la sesion de Keycloak ya viva, entra directo sin
    # pantalla intermedia. Lo que este test protege es la RUTA, no el
    # dominio: el dominio es de entorno y tiene sus dos tests debajo.
    assert palbe.url.endswith(":8080/sso/login")
    assert palbe.url.startswith("http://palbe.")
    assert palbe.rol == "palbe"


def test_la_url_lleva_el_dominio_del_entorno(monkeypatch, catalogo_real_cargado):
    """El dominio vive en una sola variable. Este test es el que hace que
    cambiarlo el dia que IT de el nombre bueno cueste una linea."""
    monkeypatch.setenv("SGE_BASE_DOMAIN", "sge.local")
    palbe = next(h for h in catalogo_real_cargado() if h.id == "palbe")
    assert palbe.url == "http://palbe.sge.local:8080/sso/login"


def test_sin_la_variable_la_url_usa_el_dominio_historico(
    monkeypatch, catalogo_real_cargado
):
    """Un portatil de desarrollo sin .env sigue funcionando igual que antes
    de existir la variable. Es lo que permite que este cambio no toque el
    resto de los tests."""
    monkeypatch.delenv("SGE_BASE_DOMAIN", raising=False)
    palbe = next(h for h in catalogo_real_cargado() if h.id == "palbe")
    assert palbe.url == "http://palbe.sge.local:8080/sso/login"


def test_la_expansion_no_toca_los_demas_campos(monkeypatch, catalogo_real_cargado):
    """Solo el campo url se expande. El catalogo es datos, no una plantilla."""
    monkeypatch.setenv("SGE_BASE_DOMAIN", "otro.dominio")
    palbe = next(h for h in catalogo_real_cargado() if h.id == "palbe")
    assert "${" not in palbe.url
    for campo in (palbe.id, palbe.nombre, palbe.descripcion, palbe.rol):
        assert "otro.dominio" not in campo


def test_el_compose_pasa_el_dominio_al_contenedor():
    """El catalogo expande ${SGE_BASE_DOMAIN} DENTRO del contenedor, asi que
    la variable tiene que estar en el bloque environment del servicio
    dashboard. Si falta, el recibidor arranca, /health dice "ok", la tarjeta
    se pinta -- y al pulsarla da un 404, porque apunta al dominio historico.

    Este test existe porque es la TERCERA vez que este proyecto se deja una
    variable fuera de ese bloque: la Fase 4c con cuatro de SSO, la ronda del
    recibidor con PALBE_SSO_POST_LOGOUT_URI, y esta. Ningun test de codigo
    puede verlo -- todos fijan el entorno con monkeypatch, que es justo lo
    que oculta el hueco. Hay que leer el compose."""
    import re
    from pathlib import Path

    compose = (
        Path(__file__).resolve().parents[3] / "platform" / "compose.yml"
    ).read_text(encoding="utf-8")

    # El bloque del servicio dashboard, hasta el siguiente servicio o clave
    # de primer nivel. Sin yaml.safe_load a proposito: aqui interesa el texto
    # con sus ${...} sin resolver, que es lo que se quiere comprobar.
    bloque = re.search(r"^  dashboard:\n(?:(?:    .*|\s*)\n)*", compose, re.MULTILINE)
    assert bloque, "no se encontro el servicio dashboard en platform/compose.yml"
    # Una ASIGNACION, no la simple aparicion del texto: el propio compose
    # lleva un comentario que nombra la variable, asi que buscar la cadena
    # suelta daba verde con la linea borrada. Se comprobo borrandola.
    asignada = re.search(r"^\s+SGE_BASE_DOMAIN:\s*\S", bloque.group(0), re.MULTILINE)
    assert asignada, (
        "SGE_BASE_DOMAIN no llega al contenedor del recibidor: la tarjeta de "
        "PALBE apuntara al dominio historico y dara 404 al pulsarla"
    )


def test_tambora_esta_en_el_catalogo(catalogo_real):
    """La segunda herramienta. Hasta que entro, el filtrado por rol no
    filtraba nada: habia una sola tarjeta y todo el mundo la veia."""
    tambora = next((h for h in catalogo_real if h.id == "tambora"), None)
    assert tambora is not None
    assert tambora.rol == "tambora"
    # A /sso/login, no a la raiz: Tambora no tiene login propio.
    assert tambora.url.endswith(":8080/sso/login")
    assert tambora.url.startswith("http://tambora.")


def test_bartolo_esta_en_el_catalogo(catalogo_real):
    """La tercera herramienta."""
    bartolo = next((h for h in catalogo_real if h.id == "bartolo"), None)
    assert bartolo is not None
    assert bartolo.rol == "bartolo"
    assert bartolo.url.endswith(":8080/sso/login")
    assert bartolo.url.startswith("http://bartolo.")


def test_cae_esta_en_el_catalogo(catalogo_real):
    """La cuarta herramienta, y la primera que no es Python.

    Node 24 + Express en vez de FastAPI, pero desde fuera se comporta igual:
    el catalogo no sabe --ni debe saber-- con que esta escrita cada una."""
    cae = next((h for h in catalogo_real if h.id == "cae"), None)
    assert cae is not None
    assert cae.rol == "cae"
    assert cae.url.endswith(":8080/sso/login")
    assert cae.url.startswith("http://cae.")


def test_las_cuatro_tienen_icono_normalizado(catalogo_real):
    """Las cuatro con imagen, y la imagen EXISTE.

    CAE entro el 2026-09-21 con un emoji --el campo lo admite: lo que no
    empieza por /static/ se pinta como texto-- y hubo un test que lo fijaba
    para obligar a acordarse. Duro unas horas: esa misma tarde llego su marca.
    Este es el que lo sustituye, y comprueba lo que de verdad importa, que es
    que el fichero este donde dice el catalogo. Una ruta a un PNG que no
    existe da una tarjeta con la imagen rota, y eso no lo ve ningun test que
    solo mire la cadena."""
    from pathlib import Path

    frontend = Path(__file__).resolve().parent.parent / "frontend"
    for h in catalogo_real:
        assert h.icono.startswith("/static/iconos/"), h.id
        fichero = frontend / "iconos" / Path(h.icono).name
        assert fichero.is_file(), f"{h.id}: no existe {fichero}"
        assert fichero.stat().st_size > 1024, f"{h.id}: {fichero} esta vacio"


def test_todas_las_herramientas_entran_por_sso(catalogo_real):
    """Ninguna tarjeta apunta a la raiz de su herramienta: todas van a
    /sso/login, para aterrizar dentro y no en un formulario. Es
    contraintuitivo y hay que protegerlo de quien lo "arregle"."""
    for h in catalogo_real:
        assert h.url.endswith("/sso/login"), h.id


def test_cada_herramienta_tiene_su_propio_rol(catalogo_real):
    """Si dos herramientas comparten rol, el filtrado deja de separar quien
    ve que -- y el recibidor pasa a ser una lista fija."""
    roles = [h.rol for h in catalogo_real]
    assert len(roles) == len(set(roles)), roles


def test_con_el_rol_se_ve_la_tarjeta(catalogo_real):
    import catalogo

    visibles = catalogo.filtrar_por_roles(catalogo_real, ["palbe", "tecnico"])
    assert [h.id for h in visibles] == ["palbe"], (
        "con el rol de palbe solo se ve palbe: 'tecnico' no es rol de "
        "ninguna herramienta"
    )

    # Y con los dos roles, las dos tarjetas -- lo que no se podia comprobar
    # cuando el catalogo tenia una sola herramienta.
    visibles = catalogo.filtrar_por_roles(catalogo_real, ["palbe", "tambora"])
    assert sorted(h.id for h in visibles) == ["palbe", "tambora"]


def test_sin_el_rol_no_se_ve_nada(catalogo_real):
    """Estado vacio: legitimo, no un error. Cualquier usuario nuevo lo tendra."""
    import catalogo

    assert catalogo.filtrar_por_roles(catalogo_real, ["tecnico"]) == []


def test_sin_ningun_rol_no_se_ve_nada(catalogo_real):
    import catalogo

    assert catalogo.filtrar_por_roles(catalogo_real, []) == []


def test_un_rol_desconocido_no_abre_nada(catalogo_real):
    import catalogo

    assert catalogo.filtrar_por_roles(catalogo_real, ["inventado", "admin"]) == []


def test_un_yaml_sin_el_campo_rol_falla_al_cargar(tmp_path):
    """Fallar al arrancar es mejor que servir un recibidor vacio que parece
    un problema de permisos."""
    import catalogo

    malo = tmp_path / "malo.yaml"
    malo.write_text(
        "herramientas:\n  - id: x\n    nombre: X\n    descripcion: d\n"
        "    icono: i\n    url: http://x\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        catalogo.cargar_catalogo(malo)
    assert "rol" in str(exc.value)


def test_un_yaml_ilegible_falla_con_mensaje_claro(tmp_path):
    import catalogo

    malo = tmp_path / "roto.yaml"
    malo.write_text("herramientas: [esto: no: es: yaml", encoding="utf-8")
    with pytest.raises(ValueError):
        catalogo.cargar_catalogo(malo)


def test_un_rol_vacio_falla_al_cargar(tmp_path):
    """Un rol vacio coincidiria con un claim vacio (p.ej. un split(",") con
    coma final) y enseñaria la tarjeta a cualquiera. Debe fallar igual que
    un campo ausente."""
    import catalogo

    malo = tmp_path / "rol_vacio.yaml"
    malo.write_text(
        "herramientas:\n  - id: x\n    nombre: X\n    descripcion: d\n"
        '    icono: i\n    url: http://x\n    rol: ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        catalogo.cargar_catalogo(malo)
    assert "rol" in str(exc.value)


def test_una_entrada_que_no_es_un_mapeo_falla_al_cargar(tmp_path):
    import catalogo

    malo = tmp_path / "entrada_no_mapeo.yaml"
    malo.write_text("herramientas:\n  - solo-una-cadena\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        catalogo.cargar_catalogo(malo)
    assert "#1" in str(exc.value)


# --- La url interna de contexto (campo opcional) ---------------------------


def test_base_interna_es_opcional(tmp_path):
    """Una herramienta puede no ofrecer contexto, y entonces el recibidor
    simplemente no le pregunta. Es lo que permite que exista una cuarta
    herramienta sin tocar nada."""
    import catalogo

    bueno = tmp_path / "sin_contexto.yaml"
    bueno.write_text(
        "herramientas:\n"
        "  - id: x\n    nombre: X\n    descripcion: d\n    icono: i\n"
        "    url: http://x/\n    rol: x\n",
        encoding="utf-8",
    )

    herramientas = catalogo.cargar_catalogo(bueno)

    assert herramientas[0].base_interna == ""


def test_el_dominio_no_se_expande_en_base_interna(monkeypatch, catalogo_real_cargado):
    """base_interna es un nombre de servicio de la red interna, no un host
    publico. Expandir el dominio aqui es justo la clase de "coherencia" que
    alguien anadiria, y rompe la llamada dentro de sge_net."""
    monkeypatch.setenv("SGE_BASE_DOMAIN", "otro.dominio")

    for h in catalogo_real_cargado():
        assert "otro.dominio" not in h.base_interna
        assert "${" not in h.base_interna


def test_quien_ofrece_contexto_lo_hace_por_nombre_de_servicio(catalogo_real):
    """Un nombre de servicio, no un host publico: sin puntos en el host. Si
    alguien lo cambia por la URL publica, la llamada da la vuelta por Traefik
    y ata la peticion interna al dominio de fuera."""
    ofrecen = [h for h in catalogo_real if h.base_interna]
    assert ofrecen, "ninguna herramienta ofrece contexto: la franja no saldria nunca"

    for h in ofrecen:
        assert h.base_interna.startswith("http://"), h.id
        host = h.base_interna.removeprefix("http://").split(":")[0]
        assert "." not in host, f"{h.id}: {host} parece un host publico"


def test_tambora_ofrece_activos_pero_no_contexto():
    """Decision del 2026-09-18, y este test existe para que se lea como
    decision y no como olvido.

    Tambora no tiene nada que reanudar: `/chat` no acepta una conversacion
    concreta y su front arranca con currentChatId a null, asi que una pastilla
    llevaria a la aplicacion EN BLANCO. Pero saber quien esta dentro lo sabe
    perfectamente, asi que si sale en "Trabajando ahora".

    Por eso el catalogo tiene DOS campos y no uno: `base_interna` es como
    llegar, `ofrece` es que implementa. Con un campo solo, Tambora se quedaba
    fuera de las dos cosas por estar fuera de una -- y eso fue un fallo real,
    no una hipotesis."""
    import catalogo

    herramientas = {h.id: h for h in catalogo.cargar_catalogo("herramientas.yaml")}
    tambora = herramientas["tambora"]

    assert tambora.base_interna, "sin base interna no se le puede preguntar nada"
    assert "activos" in tambora.ofrece
    assert "contexto" not in tambora.ofrece

    # Y TODAS las demas ofrecen las dos cosas. Se recorre el catalogo en vez
    # de una lista escrita a mano: con la lista, una herramienta nueva se
    # quedaba fuera de esta comprobacion sin que nada fallara, que es justo el
    # descuido que este fichero existe para evitar.
    for otra in (h for h in herramientas.values() if h.id != "tambora"):
        assert set(otra.ofrece) == {"contexto", "activos"}, otra.id


def test_el_puerto_interno_coincide_con_la_etiqueta_de_traefik(catalogo_real):
    """El acoplamiento existe de verdad: si un modulo cambia de puerto y el
    catalogo no se entera, la franja muere EN SILENCIO -- 200 y sin pastillas.
    Se lee el compose de cada modulo, no se confia en la memoria."""
    import re
    from pathlib import Path

    modulos = Path(__file__).resolve().parents[3] / "modules"
    for h in (h for h in catalogo_real if h.base_interna):
        compose = (modulos / h.id / "docker-compose.yml").read_text(encoding="utf-8")
        etiqueta = re.search(r"loadbalancer\.server\.port=(\d+)", compose)
        assert etiqueta, f"{h.id}: no se encontro el puerto en su compose"
        puerto_catalogo = h.base_interna.rsplit(":", 1)[-1]
        assert puerto_catalogo == etiqueta.group(1), (
            f"{h.id}: el catalogo dice {puerto_catalogo} y su compose "
            f"{etiqueta.group(1)} -- la franja se quedaria muda sin avisar"
        )


def test_el_compose_pasa_el_secreto_de_contexto_al_recibidor():
    """Misma leccion que SGE_BASE_DOMAIN, y van cinco veces. Sin el secreto
    en el bloque environment, el recibidor arranca, /health dice "ok", y la
    franja no sale nunca -- con las tres herramientas contestando 401."""
    import re
    from pathlib import Path

    compose = (
        Path(__file__).resolve().parents[3] / "platform" / "compose.yml"
    ).read_text(encoding="utf-8")

    bloque = re.search(r"^  dashboard:\n(?:(?:    .*|\s*)\n)*", compose, re.MULTILINE)
    assert bloque, "no se encontro el servicio dashboard en platform/compose.yml"
    asignada = re.search(r"^\s+SGE_CONTEXTO_TOKEN:\s*\S", bloque.group(0), re.MULTILINE)
    assert asignada, (
        "SGE_CONTEXTO_TOKEN no llega al contenedor del recibidor: la franja "
        "no saldra nunca y las tres herramientas contestaran 401"
    )
