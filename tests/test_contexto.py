"""
Tests de la logica pura del contexto de usuario.

Aqui no hay red: se prueba lo que DECIDE el recibidor sobre la respuesta de
una herramienta. El abanico de peticiones se prueba aparte.

Las fechas van fijas y "ahora" se inyecta, nunca datetime.now(): un test de
umbral que dependa del reloj pasa o falla segun la hora a la que se ejecute.
"""

from datetime import datetime

import contexto

AHORA = datetime(2026, 9, 18, 17, 0, 0)


def _respuesta(**cambios):
    """Una respuesta bien formada, para que cada test cambie solo lo suyo."""
    ctx = {
        "titulo": "Ontinar",
        "detalle": "iteracion 7, paso 5",
        "url": "/step/5",
        "visto_en": "2026-09-18T09:12:04",
        "companeros": [],
    }
    ctx.update(cambios)
    return {"herramienta": "palbe", "contexto": ctx}


def test_una_respuesta_bien_formada_da_un_contexto():
    ctx = contexto.normalizar("palbe", _respuesta())

    assert ctx is not None
    assert ctx.titulo == "Ontinar"
    assert ctx.detalle == "iteracion 7, paso 5"
    assert ctx.url == "/step/5"


def test_sin_contexto_no_hay_pastilla():
    """ "No tengo nada tuyo" es una respuesta correcta, no un error: la tiene
    cualquier usuario nuevo y hoy tres personas reales con cero proyectos."""
    assert (
        contexto.normalizar("palbe", {"herramienta": "palbe", "contexto": None}) is None
    )


def test_el_detalle_puede_faltar():
    """Es opcional por contrato: una herramienta contesta lo que sabe."""
    ctx = contexto.normalizar("palbe", _respuesta(detalle=None))

    assert ctx is not None
    assert ctx.detalle == ""


def test_una_url_que_no_sea_relativa_se_rechaza():
    """Una url absoluta convertiria la franja en un redirector abierto: el
    recibidor pintaria un enlace a donde dijera la herramienta. Y "//x" y
    "/\\x" empiezan por "/" pero el navegador los resuelve como
    protocol-relative, que es la base del phishing con dominio legitimo --
    la misma trampa que ya cubre app_palbe_4.py:1623."""
    for mala in ("https://evil.com/x", "//evil.com", "/\\evil.com", "", "step/5"):
        assert contexto.normalizar("palbe", _respuesta(url=mala)) is None, mala


def test_una_respuesta_rara_no_rompe_nada():
    """Cualquier fallo significa que esa herramienta no aporta pastilla. Nunca
    una excepcion: la franja no puede tumbar la portada."""
    for basura in (
        None,
        [],
        "texto",
        42,
        {},
        {"contexto": "no-es-un-mapeo"},
        {"contexto": {"url": "/x"}},
    ):
        assert contexto.normalizar("palbe", basura) is None, basura


def test_el_titulo_es_obligatorio():
    """Sin titulo no hay nada que ensenar, asi que no se ensena una pastilla
    muda con un enlace."""
    for sin_titulo in ("", None, "   "):
        assert contexto.normalizar("palbe", _respuesta(titulo=sin_titulo)) is None


def test_una_herramienta_no_puede_hacer_pesada_la_portada():
    """Los textos se recortan. La franja es de la plataforma, y una
    herramienta no decide cuanto ocupa en la portada de las demas."""
    datos = _respuesta(
        titulo="T" * 500,
        detalle="D" * 500,
        companeros=[{"nombre": "N" * 500, "visto_en": "2026-09-18T16:00:00"}],
    )

    ctx = contexto.normalizar("palbe", datos, ahora=AHORA)

    assert len(ctx.titulo) == contexto.MAX_TITULO
    assert len(ctx.detalle) == contexto.MAX_DETALLE
    assert len(ctx.companeros[0].nombre) == contexto.MAX_NOMBRE


# --- La antiguedad, redactada en el servidor ------------------------------


def test_la_antiguedad_se_redacta_en_palabras():
    """Se redacta aqui, en Python, y no en el navegador: asi no hay desfase
    de reloj del cliente y no se manda ninguna marca de tiempo al front."""
    casos = [
        (datetime(2026, 9, 18, 16, 59, 40), "ahora mismo"),
        (datetime(2026, 9, 18, 16, 55, 0), "hace 5 min"),
        (datetime(2026, 9, 18, 16, 0, 0), "hace 1 h"),
        (datetime(2026, 9, 18, 6, 0, 0), "hace 11 h"),
    ]
    for visto_en, esperado in casos:
        assert contexto.redactar_antiguedad(visto_en, AHORA) == esperado


# --- Los companeros: el aviso de que alguien puede pisarte -----------------


def test_los_companeros_de_hoy_salen_y_los_de_ayer_no():
    """El umbral es la jornada, 12 h, porque esto es un aviso de colision y
    no un muro de novedades. Alguien de hace tres semanas no te va a pisar."""
    datos = _respuesta(
        companeros=[
            {"nombre": "aurora", "visto_en": "2026-09-18T15:00:00"},  # hace 2 h
            {"nombre": "ana", "visto_en": "2026-09-17T23:00:00"},  # hace 18 h
        ]
    )

    ctx = contexto.normalizar("palbe", datos, ahora=AHORA)

    assert [c.nombre for c in ctx.companeros] == ["aurora"]


def test_como_maximo_tres_companeros_y_el_mas_reciente_primero():
    datos = _respuesta(
        companeros=[
            {"nombre": "aurora", "visto_en": "2026-09-18T09:00:00"},
            {"nombre": "ana", "visto_en": "2026-09-18T16:30:00"},
            {"nombre": "carlos", "visto_en": "2026-09-18T12:00:00"},
            {"nombre": "marta", "visto_en": "2026-09-18T15:00:00"},
        ]
    )

    ctx = contexto.normalizar("palbe", datos, ahora=AHORA)

    assert [c.nombre for c in ctx.companeros] == ["ana", "marta", "carlos"]


def test_un_companero_sin_nombre_no_ensena_su_sub():
    """Regla 7 del contrato: un UUID en crudo no se pinta nunca. Es inutil
    para quien lo lee e identificador para quien lo recoja."""
    datos = _respuesta(
        companeros=[
            {
                "nombre": None,
                "sub": "8f14e45f-ea3b-4d2c-9c1a-000000000000",
                "visto_en": "2026-09-18T16:00:00",
            },
        ]
    )

    ctx = contexto.normalizar("palbe", datos, ahora=AHORA)

    assert [c.nombre for c in ctx.companeros] == ["otra persona"]


def test_companeros_malformados_se_descartan_sin_tumbar_la_pastilla():
    """La pastilla es lo importante; los companeros son un extra opcional."""
    datos = _respuesta(
        companeros=[
            "no-es-un-mapeo",
            {"nombre": "aurora"},  # sin fecha
            {"nombre": "ana", "visto_en": "no-es-una-fecha"},
            {"nombre": "marta", "visto_en": "2026-09-18T16:00:00"},
        ]
    )

    ctx = contexto.normalizar("palbe", datos, ahora=AHORA)

    assert ctx is not None
    assert [c.nombre for c in ctx.companeros] == ["marta"]


def test_sin_companeros_sigue_habiendo_pastilla():
    """Una herramienta sin trabajo compartido no tiene a nadie que pueda
    pisarte, asi que la lista vacia es cumplimiento del contrato y no una
    carencia. El recibidor tiene que pintar la pastilla igual."""
    for vacio in ([], None, "cualquier-cosa"):
        ctx = contexto.normalizar("palbe", _respuesta(companeros=vacio), ahora=AHORA)
        assert ctx is not None
        assert ctx.companeros == ()


def test_el_contexto_propio_no_caduca_nunca():
    """ "Continua por donde lo dejaste" sigue valiendo tres semanas despues.
    Filtrarlo por antiguedad seria quitarle el enlace a alguien justo cuando
    mas falta le hace recordar donde estaba. El umbral es SOLO de companeros."""
    ctx = contexto.normalizar(
        "palbe", _respuesta(visto_en="2026-08-25T09:00:00"), ahora=AHORA
    )

    assert ctx is not None
    assert ctx.titulo == "Ontinar"


# --- Las dos herramientas no escriben la fecha igual ----------------------


def test_una_fecha_con_zona_horaria_no_revienta():
    """Bartolo escribe `datetime.now(timezone.utc).isoformat()` -- CON zona --
    y PALBE `datetime.now().isoformat()` -- SIN zona. Restar una naive de una
    aware lanza TypeError, y el except del abanico se lo tragaria: la pastilla
    no saldria NUNCA y el log solo diria "TypeError".

    Es el mismo genero de trampa que ya esta medida en este proyecto: las
    fechas de dos modulos distintos no tienen por que venir en el mismo
    formato, y el contrato solo pide ISO-8601."""
    datos = _respuesta(
        companeros=[
            {"nombre": "bartolo-aware", "visto_en": "2026-09-18T15:00:00+00:00"},
        ]
    )

    ctx = contexto.normalizar("bartolo", datos, ahora=AHORA)

    assert [c.nombre for c in ctx.companeros] == ["bartolo-aware"]


def test_los_dos_formatos_conviven_en_la_misma_respuesta():
    """Y el umbral sigue valiendo para los dos: 2 h entra, 18 h no, con o sin
    zona horaria."""
    datos = _respuesta(
        companeros=[
            {"nombre": "con-zona", "visto_en": "2026-09-18T15:00:00+00:00"},
            {"nombre": "sin-zona", "visto_en": "2026-09-18T15:00:00"},
            {"nombre": "vieja-con-zona", "visto_en": "2026-09-17T23:00:00+00:00"},
        ]
    )

    ctx = contexto.normalizar("bartolo", datos, ahora=AHORA)

    assert sorted(c.nombre for c in ctx.companeros) == ["con-zona", "sin-zona"]


def test_la_antiguedad_tambien_con_zona_horaria():
    """Los dos momentos van en el MISMO marco, y eso no es un detalle del
    test: el primer intento comparaba un "ahora" sin zona (que se interpreta
    como hora local) con una fecha en UTC, y esperaba 5 minutos. Solo saldrian
    5 minutos si la maquina estuviera en UTC -- en Madrid en septiembre son
    dos horas de diferencia, y el resultado correcto era otro.

    O sea que el test estaba mal y el codigo bien. Se deja escrito porque es
    exactamente la confusion que _con_zona existe para evitar."""
    from datetime import timezone

    ahora = datetime(2026, 9, 18, 17, 0, 0, tzinfo=timezone.utc)
    visto_en = datetime(2026, 9, 18, 16, 55, 0, tzinfo=timezone.utc)

    assert contexto.redactar_antiguedad(visto_en, ahora) == "hace 5 min"


def test_los_offsets_se_respetan_de_verdad():
    """La otra mitad: la misma hora de pared en dos zonas distintas NO es el
    mismo momento. Si _con_zona usara replace(tzinfo=...) en vez de
    astimezone(), esto pasaria por "ahora mismo"."""
    from datetime import timedelta, timezone

    ahora = datetime(2026, 9, 18, 17, 0, 0, tzinfo=timezone.utc)
    # Las 16:55 en UTC+5 son las 11:55 UTC: hace 5 horas, no 5 minutos.
    visto_en = datetime(2026, 9, 18, 16, 55, 0, tzinfo=timezone(timedelta(hours=5)))

    assert contexto.redactar_antiguedad(visto_en, ahora) == "hace 5 h"


def test_una_fecha_en_el_futuro_se_descarta():
    """Hallazgo de la revision. El filtro era `ahora - visto_en > VENTANA`,
    que es falso para cualquier diferencia NEGATIVA. Asi que una herramienta
    con el reloj adelantado -- o cuya fecha naive se interprete en otra zona
    -- colaba un companero que pasaba el filtro PARA SIEMPRE, salia primero
    por el orden descendente, y se pintaba como "ahora mismo" porque la
    redaccion recorta en cero. Un aviso de colision permanente y falso."""
    datos = _respuesta(
        companeros=[
            {"nombre": "del-futuro", "visto_en": "2026-09-19T09:00:00"},
            {"nombre": "de-verdad", "visto_en": "2026-09-18T16:00:00"},
        ]
    )

    ctx = contexto.normalizar("palbe", datos, ahora=AHORA)

    assert [c.nombre for c in ctx.companeros] == ["de-verdad"]


def test_el_tope_de_tamano_se_mira_en_la_cabecera_y_no_en_el_cuerpo():
    """Hallazgo de la revision. El tope se comprobaba sobre
    `respuesta.content`, que solo existe cuando httpx YA ha traido el cuerpo
    entero a memoria: una herramienta que devolviera 200 MB los metia en el
    recibidor -- con mem_limit 200m -- antes de que el tope los rechazara.

    Se prueba la decision como funcion pura porque el transporte de prueba de
    httpx no puede simular un cuerpo que gotea."""
    assert contexto.demasiado_grande({"content-length": str(contexto.MAX_BYTES + 1)})
    assert not contexto.demasiado_grande({"content-length": str(contexto.MAX_BYTES)})
    # Sin cabecera, o con basura, no se rechaza: ahi la red es el tope total
    # del abanico y la comprobacion sobre el cuerpo ya traido.
    assert not contexto.demasiado_grande({})
    assert not contexto.demasiado_grande({"content-length": "ni-idea"})


# --- La fecha del contexto propio ----------------------------------------


def test_el_contexto_lleva_su_propia_fecha():
    """Desde el 2026-09-18 las dos herramientas registran cuando estuviste,
    asi que la pastilla puede decirlo. Significa lo MISMO en las dos: PALBE lo
    escribe al navegar (`active_context_user.visto_en`) y Bartolo al abrir una
    auditoria (`audit_last_seen`)."""
    ctx = contexto.normalizar(
        "palbe", _respuesta(visto_en="2026-09-18T16:00:00"), ahora=AHORA
    )

    assert ctx.visto_en is not None


def test_sin_fecha_la_pastilla_sale_igual():
    """Quien no haya navegado desde el despliegue no tiene fecha, y eso es un
    estado normal durante los primeros dias. La pastilla es lo importante."""
    for sin_fecha in (None, "", "no-es-una-fecha", 42):
        ctx = contexto.normalizar("palbe", _respuesta(visto_en=sin_fecha), ahora=AHORA)
        assert ctx is not None, sin_fecha
        assert ctx.visto_en is None, sin_fecha


def test_la_fecha_del_contexto_no_caduca_aunque_sea_vieja():
    """El umbral de 12 h es SOLO de companeros. "Estuviste hace tres semanas"
    es informacion util; ocultarla no lo es."""
    ctx = contexto.normalizar(
        "palbe", _respuesta(visto_en="2026-08-25T09:00:00"), ahora=AHORA
    )

    assert ctx.visto_en is not None


def test_la_antiguedad_sabe_de_dias_y_semanas():
    """Hueco que aparecio al usar la redaccion para la fecha del contexto.

    Se escribio pensando solo en companeros, que estan topados a 12 h, asi
    que paraba en horas. La fecha del contexto NO caduca -- "estuviste hace
    tres semanas" es informacion util -- y sin esto habria dicho "hace 500 h".
    """
    casos = [
        (datetime(2026, 9, 18, 6, 0, 0), "hace 11 h"),
        (datetime(2026, 9, 17, 17, 0, 0), "ayer"),
        (datetime(2026, 9, 15, 17, 0, 0), "hace 3 dias"),
        (datetime(2026, 9, 10, 17, 0, 0), "hace 1 semana"),
        (datetime(2026, 8, 25, 17, 0, 0), "hace 3 semanas"),
        (datetime(2026, 6, 18, 17, 0, 0), "hace 3 meses"),
    ]
    for visto_en, esperado in casos:
        assert contexto.redactar_antiguedad(visto_en, AHORA) == esperado, visto_en
