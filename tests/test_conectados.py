"""
Quien esta trabajando ahora: la logica pura.

La regla es una y no tiene letra pequena: **si lo estas usando, sales en
verde**. En pantalla es verde o nada.

Hace falta una VENTANA DE TIEMPO, y no es una salvedad: cerrar la pestana es
invisible y ninguna herramienta puede avisar de que te has ido. Cinco minutos,
el numero que PALBE ya usaba para su "Online ahora".
"""

from datetime import datetime, timedelta, timezone

import conectados

AHORA = datetime(2026, 9, 18, 17, 0, 0, tzinfo=timezone.utc)


def _hace(**kw):
    return (AHORA - timedelta(**kw)).isoformat()


def test_quien_ha_hecho_algo_hace_poco_sale():
    activos = conectados.normalizar(
        "palbe",
        [
            {"sub": "s-1", "nombre": "ilasierra", "visto_en": _hace(minutes=2)},
        ],
        ahora=AHORA,
    )

    assert [a.nombre for a in activos] == ["ilasierra"]
    assert activos[0].herramienta == "palbe"


def test_quien_no_ha_hecho_nada_en_la_ventana_no_sale():
    """Cinco minutos. Por eso y no treinta: lo que apaga el verde es la
    ventana, no un cierre de sesion -- nadie cierra sesion, se cierra la
    pestana."""
    activos = conectados.normalizar(
        "palbe",
        [
            {"sub": "s-1", "nombre": "dentro", "visto_en": _hace(minutes=4)},
            {"sub": "s-2", "nombre": "fuera", "visto_en": _hace(minutes=7)},
        ],
        ahora=AHORA,
    )

    assert [a.nombre for a in activos] == ["dentro"]


def test_una_fecha_en_el_futuro_se_descarta():
    """Mismo fallo que ya aparecio en los companeros: con solo el tope
    superior, un reloj adelantado colaba a alguien PARA SIEMPRE."""
    activos = conectados.normalizar(
        "palbe",
        [
            {
                "sub": "s-1",
                "nombre": "del-futuro",
                "visto_en": (AHORA + timedelta(hours=1)).isoformat(),
            },
        ],
        ahora=AHORA,
    )

    assert activos == ()


def test_una_respuesta_rara_no_rompe_nada():
    """Igual que el contexto: una herramienta que contesta mal no aporta
    nadie, y nada mas. El bloque no puede tumbar la portada."""
    for basura in (
        None,
        {},
        "texto",
        42,
        [None],
        ["x"],
        [{"sub": "s"}],
        [{"nombre": "sin sub", "visto_en": _hace(minutes=1)}],
    ):
        assert conectados.normalizar("palbe", basura, ahora=AHORA) == (), basura


def test_el_sub_no_se_pinta_nunca():
    """Regla 7 del contrato, tambien aqui: el sub es la CLAVE para agrupar a
    la misma persona en varias herramientas, no algo que se ensene. Un UUID en
    crudo es inutil para quien lo lee e identificador para quien lo recoja."""
    activos = conectados.normalizar(
        "palbe",
        [
            {"sub": "8f14e45f-ea3b-4d2c", "nombre": None, "visto_en": _hace(minutes=1)},
        ],
        ahora=AHORA,
    )

    assert activos[0].nombre == conectados.SIN_NOMBRE
    assert "8f14e45f" not in activos[0].nombre


def test_la_misma_persona_en_dos_herramientas_sale_una_vez():
    """Se agrupa por sub, y se queda la herramienta donde estuvo MAS
    RECIENTEMENTE: si acabas de pasar de PALBE a Bartolo, lo que interesa es
    que estas en Bartolo."""
    gente = conectados.fusionar(
        [
            conectados.Activo(
                "s-1", "ilasierra", "palbe", AHORA - timedelta(minutes=4)
            ),
            conectados.Activo(
                "s-1", "ilasierra", "bartolo", AHORA - timedelta(minutes=1)
            ),
            conectados.Activo("s-2", "jmorales", "palbe", AHORA - timedelta(minutes=2)),
        ]
    )

    assert [(p.nombre, p.herramienta) for p in gente] == [
        ("ilasierra", "bartolo"),
        ("jmorales", "palbe"),
    ]


def test_se_ordena_por_quien_estuvo_hace_menos():
    gente = conectados.fusionar(
        [
            conectados.Activo("s-1", "vieja", "palbe", AHORA - timedelta(minutes=4)),
            conectados.Activo("s-2", "nueva", "palbe", AHORA - timedelta(minutes=1)),
        ]
    )

    assert [p.nombre for p in gente] == ["nueva", "vieja"]


def test_yo_tambien_salgo_en_la_lista():
    """Este test afirmaba lo contrario hasta el 2026-09-18, con el argumento
    de que "ya sabes que estas". Cambio por decision del usuario, y el motivo
    practico le da la razon: excluirse hacia que el bloque desapareciera
    cuando eras el unico dentro, que con siete personas es la mayor parte del
    dia."""
    gente = conectados.fusionar(
        [
            conectados.Activo("yo", "Cesar", "palbe", AHORA),
            conectados.Activo("s-2", "otra", "palbe", AHORA - timedelta(minutes=1)),
        ]
    )

    assert [p.nombre for p in gente] == ["Cesar", "otra"]


# --- El recibidor se lleva a si mismo ------------------------------------


def test_el_recibidor_apunta_a_quien_pasa_por_su_portada():
    """Cuatro sitios, no tres: el recibidor cuenta como uno mas y se lleva a
    si mismo. No necesita red para saberlo, asi que el bloque sale aunque las
    tres herramientas esten caidas."""
    registro = {}

    conectados.apuntar(registro, "s-1", "ilasierra", ahora=AHORA)

    assert conectados.propios(registro, ahora=AHORA)[0].nombre == "ilasierra"
    assert conectados.propios(registro, ahora=AHORA)[0].herramienta == "plataforma"


def test_el_registro_propio_tambien_caduca():
    registro = {}
    conectados.apuntar(registro, "s-1", "quien-sea", ahora=AHORA - timedelta(minutes=7))

    assert conectados.propios(registro, ahora=AHORA) == ()


def test_el_registro_propio_no_crece_sin_limite():
    """Vive en memoria y el contenedor tiene 200 MB. Una entrada por persona,
    y las caducadas se van solas al leerlas."""
    registro = {}
    for i in range(50):
        conectados.apuntar(
            registro, f"s-{i}", f"n-{i}", ahora=AHORA - timedelta(minutes=30)
        )
    conectados.apuntar(registro, "s-vivo", "vivo", ahora=AHORA)

    assert conectados.propios(registro, ahora=AHORA)[0].nombre == "vivo"
    assert len(registro) == 1, "las caducadas no se limpiaron"


# --- El abanico ----------------------------------------------------------


def _consultar(responder, registro=None):
    import asyncio as _a

    import httpx

    import catalogo

    tres = [
        catalogo.Herramienta(
            id=svc,
            nombre=svc,
            descripcion="d",
            icono="i",
            url=f"http://{svc}.dominio/",
            rol=svc,
            base_interna=f"http://{svc}:8000",
            ofrece=("contexto", "activos"),
        )
        for svc in ("palbe", "tambora", "bartolo")
    ]

    async def corre():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as c:
            return await conectados.consultar(
                c, tres, "tok", registro if registro is not None else {}, ahora=AHORA
            )

    return _a.run(corre())


def test_se_juntan_los_de_las_tres_herramientas():
    import httpx

    def responder(p):
        svc = p.url.host
        return httpx.Response(
            200,
            json={
                "activos": [
                    {
                        "sub": f"s-{svc}",
                        "nombre": f"quien-{svc}",
                        "visto_en": _hace(minutes=1),
                    },
                ]
            },
        )

    gente = _consultar(responder)

    assert sorted(a.herramienta for a in gente) == ["bartolo", "palbe", "tambora"]


def test_una_herramienta_caida_no_borra_a_los_demas():
    import httpx

    def responder(p):
        if p.url.host == "palbe":
            raise httpx.ConnectError("caida", request=p)
        return httpx.Response(
            200,
            json={
                "activos": [
                    {
                        "sub": f"s-{p.url.host}",
                        "nombre": "alguien",
                        "visto_en": _hace(minutes=1),
                    },
                ]
            },
        )

    gente = _consultar(responder)

    assert sorted(a.herramienta for a in gente) == ["bartolo", "tambora"]


def test_con_las_tres_caidas_sigue_saliendo_quien_esta_en_la_plataforma():
    """La razon de que el recibidor se lleve a si mismo: el bloque no depende
    de que ninguna herramienta conteste."""
    import httpx

    def responder(p):
        raise httpx.ConnectError("todas caidas", request=p)

    registro = {}
    conectados.apuntar(registro, "s-yo", "Cesar", ahora=AHORA)

    gente = _consultar(responder, registro=registro)

    assert [(a.nombre, a.herramienta) for a in gente] == [("Cesar", "plataforma")]


def test_sin_secreto_no_se_pregunta_pero_los_propios_siguen():
    import asyncio as _a

    import catalogo
    import httpx

    preguntadas = []

    def responder(p):
        preguntadas.append(p.url.host)
        return httpx.Response(200, json={"activos": []})

    tres = [
        catalogo.Herramienta(
            id="palbe",
            nombre="p",
            descripcion="d",
            icono="i",
            url="http://p/",
            rol="palbe",
            base_interna="http://palbe:8000",
            ofrece=("contexto", "activos"),
        )
    ]
    registro = {}
    conectados.apuntar(registro, "s-yo", "Cesar", ahora=AHORA)

    async def corre():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as c:
            return await conectados.consultar(c, tres, "", registro, ahora=AHORA)

    gente = _a.run(corre())

    assert preguntadas == []
    assert [a.nombre for a in gente] == ["Cesar"]


# --- El nombre: el mismo para todos -------------------------------------


def test_el_nombre_completo_manda_sobre_el_usuario_de_la_herramienta():
    """Fallo visto en una prueba con dos personas: la misma persona salia con
    nombre completo o con su usuario segun DONDE estuviera. El recibidor la
    ve como "Cesar Suela" (el claim `name` de Keycloak) y las herramientas
    como "csuela" (su `users.username`).

    El recibidor es la puerta de entrada, asi que es el unico que conoce el
    nombre de Keycloak de todo el mundo: manda el suyo cuando lo sabe."""
    directorio = {"s-1": "Marta Modelo García"}
    activos = (conectados.Activo("s-1", "ilasierra", "palbe", AHORA),)

    assert conectados.con_nombres(activos, directorio)[0].nombre == "Marta Modelo García"


def test_si_el_recibidor_no_conoce_a_alguien_se_queda_el_de_la_herramienta():
    """Degradacion honesta: el directorio se llena cuando cada persona pasa
    por el recibidor, asi que tras reiniciar el contenedor esta vacio. Mejor
    el usuario de la herramienta que "otra persona"."""
    activos = (conectados.Activo("s-9", "jmorales", "bartolo", AHORA),)

    assert conectados.con_nombres(activos, {})[0].nombre == "jmorales"


def test_el_directorio_de_nombres_no_crece_sin_limite():
    """No lo poda la ventana de cinco minutos -- un nombre sigue valiendo
    cuando la persona se va -- asi que necesita su propio tope."""
    directorio = {}
    for i in range(conectados.MAX_NOMBRES + 50):
        conectados.recordar_nombre(directorio, f"s-{i}", f"n-{i}")

    assert len(directorio) <= conectados.MAX_NOMBRES
