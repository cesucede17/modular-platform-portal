"""
El recibidor: quien entra, que ve, y a donde va si no ha entrado.
"""

import os

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "clave-de-prueba-solo-para-tests")
    monkeypatch.setenv("SSO_ISSUER", "http://auth.sge.local:8080/realms/sge")
    monkeypatch.setenv("SSO_CLIENT_ID", "dashboard")
    monkeypatch.setenv("SSO_CLIENT_SECRET", "secreto-de-prueba")
    monkeypatch.setenv("SSO_REDIRECT_URI", "http://sge.local:8080/sso/callback")
    monkeypatch.setenv("SSO_POST_LOGOUT_URI", "http://sge.local:8080/")
    monkeypatch.setenv("SGE_CONTEXTO_TOKEN", "secreto-de-contexto-de-prueba")
    import app as modulo_app

    return TestClient(modulo_app.app)


def test_health_responde_sin_sesion(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_las_cabeceras_de_seguridad_van_en_toda_respuesta(cliente):
    """El recibidor es la puerta de entrada de la plataforma: las mismas
    tres cabeceras que ya pone PALBE, y antes de esto era la unica pieza
    sin ellas."""
    r = cliente.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_sin_sesion_la_raiz_manda_a_keycloak(cliente):
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/sso/login"


def test_con_el_rol_se_ve_la_tarjeta(cliente):
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"])
        assert r.status_code == 200
        assert "PALBE" in r.text
        assert "http://palbe.sge.local:8080/sso/login" in r.text


def test_sin_el_rol_se_ve_el_estado_vacio(cliente):
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["tecnico"])
        assert r.status_code == 200
        assert "PALBE" not in r.text
        # El estado vacio explica a quien pedir acceso, no deja la pagina en blanco
        assert "acceso" in r.text.lower()


def test_el_nombre_del_usuario_aparece(cliente):
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"], nombre="César Suela")
        assert "César" in r.text


def test_un_nombre_con_html_no_se_ejecuta(cliente):
    """El nombre viene del perfil de Keycloak, que cualquiera puede editar.

    Sin escapar, un nombre como este se ejecutaria en el origen del
    recibidor: autoataque, pero con la cookie de sesion accesible via
    peticiones autenticadas -- no es de los que se dejan pasar."""
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"], nombre="<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in r.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text


def test_un_nombre_normal_con_acentos_se_sigue_viendo_bien(cliente):
    """Escapar no debe romper lo que ya funcionaba."""
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"], nombre="César Suela")
        assert "César Suela" in r.text
        assert "CS" in r.text  # iniciales


@pytest.fixture
def modulo(monkeypatch):
    """El modulo app, para probar _session_max_age sin levantar la app.

    La funcion lee el entorno cada vez que se la llama, asi que monkeypatch
    basta y no hace falta recargar el modulo -- que es lo fragil, por el cache
    de sys.modules que explica _entrar mas abajo."""
    monkeypatch.setenv("SECRET_KEY", "clave-de-prueba-solo-para-tests")
    import app as modulo_app

    return modulo_app


def test_una_cookie_caducada_manda_a_keycloak(cliente):
    """La razon de ser del TTL corto: al caducar la cookie se vuelve a pasar
    por /sso/login, y ahi es donde los roles se releen del token. Sin esta
    prueba, la caducidad no estaba cubierta por ningun test."""
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"], antiguedad=1801)
        assert r.status_code == 303
        assert r.headers["location"] == "/sso/login"


def test_una_cookie_recien_firmada_sigue_valiendo(cliente):
    """La otra mitad del limite: 30 min es el tope, no un maximo estricto que
    se lleve por delante una sesion de hace un minuto."""
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"], antiguedad=1700)
        assert r.status_code == 200
        assert "PALBE" in r.text


def test_el_ttl_por_defecto_son_30_min(modulo, monkeypatch):
    monkeypatch.delenv("SESSION_MAX_AGE", raising=False)
    assert modulo._session_max_age() == 1800


def test_el_ttl_se_puede_configurar(modulo, monkeypatch):
    """Para poder recuperar las 8 h de antes (28800) sin reconstruir la
    imagen, y para poder bajarlo a 60 s y comprobar la caducidad a mano."""
    monkeypatch.setenv("SESSION_MAX_AGE", "28800")
    assert modulo._session_max_age() == 28800


def test_un_ttl_que_no_es_un_numero_no_arranca(modulo, monkeypatch):
    """Falla al arrancar en vez de caer al valor por defecto: una errata
    silenciosa dejaria los roles congelados mas tiempo del previsto, que es
    justo lo que esta variable viene a evitar. El mensaje nombra la variable y
    el valor recibido, porque el que lo lea estara mirando un contenedor que
    no levanta."""
    monkeypatch.setenv("SESSION_MAX_AGE", "media hora")
    with pytest.raises(RuntimeError, match="SESSION_MAX_AGE"):
        modulo._session_max_age()


def test_un_ttl_de_cero_no_arranca(modulo, monkeypatch):
    """Cero hace caducar la cookie al instante: el login entraria en bucle."""
    monkeypatch.setenv("SESSION_MAX_AGE", "0")
    with pytest.raises(RuntimeError, match="SESSION_MAX_AGE"):
        modulo._session_max_age()


def _firmar_sesion(c, roles, nombre="Alguien", antiguedad=0):
    """Inyecta una sesion ya autenticada sin hacer el baile OIDC.

    DESVIACION respecto al plan original: la idea era una ruta de pruebas
    "/__test__/sesion" que la propia app solo expusiera con
    DASHBOARD_TEST_LOGIN=1 (mismo patron que PALBE_SEED_DEMO_USERS). Se probo
    y resulto ser fragil de verdad, no solo en teoria: `app` se importa una
    vez por proceso de pytest (cache de sys.modules), y la comprobacion de la
    variable de entorno se evalua en ESE primer import -- que sucede en el
    primer test del fichero, antes de que ningun test haya podido fijar la
    variable. La ruta nunca llegaba a registrarse y el intento de entrar
    acababa en un redirect a /sso/login que golpeaba la red de verdad
    (ConnectError resolviendo auth.sge.local).

    En su lugar se firma la cookie de sesion a mano, con el mismo
    itsdangerous.TimestampSigner y el mismo formato (JSON en base64, firmado)
    que usa starlette.middleware.sessions.SessionMiddleware. Esto ya NO
    ejercita el codigo del callback de sso.py (tampoco lo hacia la ruta de
    pruebas: esa vivia en app.py, no en el callback) -- lo que se prueba aqui
    es el recibidor a partir de una sesion valida, que es lo que estos tests
    necesitan."""
    import json
    from base64 import b64encode

    import itsdangerous

    secret = os.environ["SECRET_KEY"]
    payload = {"sub": "test-sub", "nombre": nombre, "roles": roles}
    data = b64encode(json.dumps(payload).encode("utf-8"))

    # `antiguedad` en segundos fabrica una cookie vieja sin tocar el reloj del
    # sistema: el sello de tiempo va DENTRO de la firma, asi que no se puede
    # editar la cookie a posteriori -- hay que firmarla con el reloj
    # desplazado. Es lo que permite probar la caducidad de verdad.
    class _SignerDesplazado(itsdangerous.TimestampSigner):
        def get_timestamp(self):
            return super().get_timestamp() - antiguedad

    signer = (
        _SignerDesplazado(secret)
        if antiguedad
        else itsdangerous.TimestampSigner(secret)
    )
    cookie = signer.sign(data).decode("utf-8")
    c.cookies.set("session", cookie)


def _entrar(c, roles, nombre="Alguien", antiguedad=0):
    """Firma la sesion y carga la portada, que es lo que hace casi todo test.

    Se separo de _firmar_sesion cuando llego /api/contexto: esa ruta necesita
    una sesion valida SIN pasar por la portada."""
    _firmar_sesion(c, roles, nombre, antiguedad)
    return c.get("/", follow_redirects=False)


# --- /api/contexto: la franja "Sigue donde lo dejaste" ---------------------


def _cliente_falso(responder):
    """Un httpx de verdad con transporte de prueba: asi la ruta ejercita el
    camino completo -- cabeceras, cadena de consulta y normalizacion -- sin
    tocar la red."""
    import httpx

    return httpx.AsyncClient(transport=httpx.MockTransport(responder))


def _contesta(titulo="Ontinar"):
    import httpx

    def responder(peticion):
        return httpx.Response(
            200,
            json={
                "herramienta": peticion.url.host,
                "contexto": {
                    "titulo": f"{titulo} en {peticion.url.host}",
                    "detalle": "",
                    "url": "/x",
                    "visto_en": "2026-09-18T16:00:00",
                    "companeros": [],
                },
            },
        )

    return responder


def _no_contesta(peticion):
    import httpx

    raise httpx.ConnectError("caido", request=peticion)


def test_la_franja_sin_sesion_da_401_y_no_un_303(cliente):
    """Un fetch que siguiera el 303 acabaria en Keycloak con un error de CORS
    con toda la pinta de ser un fallo del codigo. Y la cookie dura 30 min, asi
    que una pestana olvidada da este caso a menudo."""
    r = cliente.get("/api/contexto", follow_redirects=False)

    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


def test_sin_nada_que_reanudar_no_hay_franja(cliente, monkeypatch):
    """204: el JS no toca el hueco y la pagina queda EXACTAMENTE como hoy.
    Es el estado de cualquier usuario nuevo, no un error."""
    import app as modulo_app

    with cliente as c:
        c.cookies.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(_no_contesta))
        _firmar_sesion(c, roles=["palbe"])

        r = c.get("/api/contexto")

    assert r.status_code == 204
    assert not r.content


def test_con_contexto_la_franja_trae_las_pastillas(cliente, monkeypatch):
    import app as modulo_app

    with cliente as c:
        c.cookies.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(_contesta()))
        _firmar_sesion(c, roles=["palbe", "bartolo"])

        r = c.get("/api/contexto")

    assert r.status_code == 200
    pastillas = r.json()["contextos"]
    # Solo las herramientas cuyo rol tiene: el mismo filtrado que la rejilla.
    assert [p["herramienta"] for p in pastillas] == ["palbe", "bartolo"]
    assert pastillas[0]["titulo"] == "Ontinar en palbe"


def test_el_sub_sale_de_la_cookie_y_no_de_la_peticion(cliente, monkeypatch):
    """Aceptar un ?sub= del cliente convertiria esto en "lee el contexto de
    cualquiera con solo tener sesion"."""
    import httpx

    import app as modulo_app

    vistos = []

    def responder(peticion):
        vistos.append(peticion.url.params["sub"])
        return httpx.Response(200, json={"herramienta": "x", "contexto": None})

    with cliente as c:
        c.cookies.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(responder))
        _firmar_sesion(c, roles=["palbe"])

        c.get("/api/contexto?sub=el-sub-de-otra-persona")

    assert vistos, "no se pregunto a ninguna herramienta"
    assert set(vistos) == {"test-sub"}


def test_la_portada_sigue_funcionando_con_la_red_rota(cliente, monkeypatch):
    """El test que protege la propiedad mas valiosa de esta pieza: GET / no
    llama a nadie. Si algun dia alguien mueve la consulta al render, este es
    el test que se pone rojo."""
    import app as modulo_app

    with cliente as c:
        c.cookies.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(_no_contesta))

        r = _entrar(c, roles=["palbe"])

    assert r.status_code == 200
    assert "PALBE" in r.text


def test_sin_secreto_no_se_pregunta_a_nadie(cliente, monkeypatch):
    """Vacio = la franja no sale, y la plataforma funciona igual. A diferencia
    de SECRET_KEY esto NO impide arrancar: perder la reanudacion es molesto,
    no levantar es una averia."""
    import app as modulo_app

    preguntadas = []

    def responder(peticion):
        preguntadas.append(peticion.url.host)
        raise AssertionError("no se deberia preguntar sin secreto")

    with cliente as c:
        c.cookies.clear()
        monkeypatch.setenv("SGE_CONTEXTO_TOKEN", "")
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(responder))
        _firmar_sesion(c, roles=["palbe"])

        r = c.get("/api/contexto")

    assert r.status_code == 204
    assert preguntadas == []


def test_la_url_de_reanudacion_se_compone_con_el_origen_de_la_tarjeta(
    cliente, monkeypatch
):
    """La herramienta manda una ruta relativa; el origen lo pone el recibidor
    desde la url de la tarjeta, que ya lleva el dominio del entorno."""
    import httpx

    import app as modulo_app

    def responder(peticion):
        return httpx.Response(
            200,
            json={
                "herramienta": "palbe",
                "contexto": {
                    "titulo": "Ontinar",
                    "detalle": "",
                    "url": "/step/5",
                    "visto_en": "2026-09-18T16:00:00",
                    "companeros": [],
                },
            },
        )

    with cliente as c:
        c.cookies.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(responder))
        _firmar_sesion(c, roles=["palbe"])

        r = c.get("/api/contexto")

    assert r.json()["contextos"][0]["url"] == "http://palbe.sge.local:8080/step/5"


# --- Los iconos como imagen ------------------------------------------------


def test_un_icono_que_es_una_imagen_se_pinta_como_imagen(cliente):
    """El campo `icono` admitia solo texto (un emoji). Ahora, si el valor
    parece una ruta de /static/, se pinta un <img>; si no, se sigue pintando
    como texto.

    La regla es por el VALOR y no por un campo nuevo, para que una herramienta
    pueda seguir entrando con un emoji y sin imagen -- y para no tocar el
    dataclass congelado por una cuestion de presentacion."""
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"])

    assert '<img class="icono"' in r.text
    assert "/static/iconos/palbe.png" in r.text


def test_el_icono_lleva_texto_alternativo_y_no_lo_lee_dos_veces(cliente):
    """La tarjeta ya dice el nombre justo debajo, asi que el icono es
    decorativo: alt vacio y aria-hidden. Si no, un lector de pantalla diria
    "PALBE PALBE"."""
    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"])

    bloque = r.text.split('<img class="icono"')[1].split(">")[0]

    assert 'alt=""' in bloque
    assert 'aria-hidden="true"' in bloque


def test_un_icono_de_texto_sigue_funcionando(cliente, monkeypatch):
    """Una herramienta sin imagen no puede quedarse sin icono."""
    import app as modulo_app
    import catalogo as modulo_catalogo

    solo_emoji = [
        modulo_catalogo.Herramienta(
            id="x",
            nombre="X",
            descripcion="d",
            icono="\U0001f4c8",
            url="http://x/",
            rol="palbe",
        )
    ]
    monkeypatch.setattr(modulo_app, "CATALOGO", solo_emoji)

    with cliente as c:
        c.cookies.clear()
        r = _entrar(c, roles=["palbe"])

    assert "\U0001f4c8" in r.text
    assert '<img class="icono"' not in r.text


# --- Quien esta trabajando ahora -------------------------------------------


def test_los_conectados_sin_sesion_dan_401_en_json(cliente):
    r = cliente.get("/api/conectados", follow_redirects=False)

    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


def test_yo_solo_en_la_plataforma_ya_da_bloque(cliente, monkeypatch):
    """Cambio del 2026-09-18: antes devolvia 204 y el bloque no aparecia si el
    unico dentro eras tu. Con siete personas eso era la mayor parte del dia, y
    el bloque parecia no funcionar. Ahora sales tu."""
    import app as modulo_app

    with cliente as c:
        c.cookies.clear()
        modulo_app._EN_EL_RECIBIDOR.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(_no_contesta))
        _firmar_sesion(c, roles=["palbe"])

        r = c.get("/api/conectados")

    assert r.status_code == 200
    assert r.json()["gente"] == [{"nombre": "Alguien", "donde": "Plataforma SGE"}]


def test_otra_persona_en_una_herramienta_sale_con_su_nombre(cliente, monkeypatch):
    import httpx

    import app as modulo_app

    from datetime import datetime

    ahora = datetime.now().isoformat()

    def responder(peticion):
        if peticion.url.host != "palbe":
            return httpx.Response(200, json={"activos": []})
        return httpx.Response(
            200,
            json={
                "activos": [
                    {"sub": "sub-de-otra", "nombre": "ilasierra", "visto_en": ahora},
                ]
            },
        )

    with cliente as c:
        c.cookies.clear()
        modulo_app._EN_EL_RECIBIDOR.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(responder))
        _firmar_sesion(c, roles=["palbe"])

        r = c.get("/api/conectados")

    assert r.status_code == 200
    gente = r.json()["gente"]
    # Se compara como CONJUNTO y no como lista: desde que quien pregunta
    # tambien sale, hay dos entradas y las dos se registran en el mismo
    # instante -- afirmar el orden seria un test que falla segun el reloj.
    assert {(p["nombre"], p["donde"]) for p in gente} == {
        ("ilasierra", "PALBE"),
        ("Alguien", "Plataforma SGE"),
    }


def test_el_sub_no_llega_al_navegador(cliente, monkeypatch):
    """Es la clave para agrupar a la misma persona en varias herramientas, y
    se usa en el servidor. Mandarlo al front no aporta nada y reparte un
    identificador."""
    import httpx

    import app as modulo_app

    from datetime import datetime

    ahora = datetime.now().isoformat()

    def responder(peticion):
        return httpx.Response(
            200,
            json={
                "activos": [
                    {
                        "sub": "8f14e45f-ea3b-4d2c",
                        "nombre": "alguien",
                        "visto_en": ahora,
                    },
                ]
            },
        )

    with cliente as c:
        c.cookies.clear()
        modulo_app._EN_EL_RECIBIDOR.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(responder))
        _firmar_sesion(c, roles=["palbe"])

        r = c.get("/api/conectados")

    assert "8f14e45f" not in r.text
    assert "sub" not in r.json()["gente"][0]


def test_el_recibidor_aprende_los_nombres_que_le_cuentan(cliente, monkeypatch):
    """Si una herramienta manda un nombre de persona, el recibidor se lo
    queda. Asi quien luego aparezca solo en una herramienta que no lo sepa
    -- PALBE hasta que esa persona vuelva a entrar por SSO -- sigue saliendo
    con su nombre y no con su usuario."""
    import httpx

    import app as modulo_app
    from datetime import datetime

    ahora = datetime.now().isoformat()

    def responder(peticion):
        # Bartolo sabe el nombre; PALBE solo el usuario.
        if peticion.url.host == "bartolo":
            return httpx.Response(
                200,
                json={
                    "activos": [
                        {
                            "sub": "sub-y",
                            "nombre": "Carlos Ejemplo Pérez",
                            "visto_en": ahora,
                        }
                    ]
                },
            )
        if peticion.url.host == "palbe":
            return httpx.Response(
                200,
                json={
                    "activos": [{"sub": "sub-y", "nombre": "cejemplo", "visto_en": ahora}]
                },
            )
        return httpx.Response(200, json={"activos": []})

    with cliente as c:
        c.cookies.clear()
        modulo_app._EN_EL_RECIBIDOR.clear()
        modulo_app._NOMBRES.clear()
        monkeypatch.setattr(modulo_app, "_CLIENTE", _cliente_falso(responder))
        _firmar_sesion(c, roles=["palbe", "bartolo"])

        r = c.get("/api/conectados")

    nombres = {p["nombre"] for p in r.json()["gente"]}

    assert "Carlos Ejemplo Pérez" in nombres
    assert "cejemplo" not in nombres, "se quedo el usuario en vez del nombre aprendido"
