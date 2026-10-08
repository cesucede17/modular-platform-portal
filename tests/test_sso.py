"""
La capa OIDC del recibidor.

A diferencia de PALBE, aqui no hay mapeo de identidades: quien autentica en
Keycloak entra. Lo que decide que ve son sus roles.
"""

import pytest


@pytest.fixture
def entorno_sso(monkeypatch):
    monkeypatch.setenv("SSO_ISSUER", "http://auth.sge.local:8080/realms/sge")
    monkeypatch.setenv("SSO_CLIENT_ID", "dashboard")
    monkeypatch.setenv("SSO_CLIENT_SECRET", "secreto-de-prueba")
    monkeypatch.setenv("SSO_REDIRECT_URI", "http://sge.local:8080/sso/callback")
    monkeypatch.setenv("SSO_POST_LOGOUT_URI", "http://sge.local:8080/")


def test_sin_variables_no_hay_configuracion(monkeypatch):
    import sso

    for v in (
        "SSO_ISSUER",
        "SSO_CLIENT_ID",
        "SSO_CLIENT_SECRET",
        "SSO_REDIRECT_URI",
        "SSO_POST_LOGOUT_URI",
    ):
        monkeypatch.delenv(v, raising=False)
    assert sso.load_config() is None


def test_falta_una_variable_y_no_hay_configuracion(monkeypatch, entorno_sso):
    """Todo o nada: una configuracion a medias falla a mitad del baile OIDC,
    con un error que no dice nada."""
    import sso

    monkeypatch.delenv("SSO_CLIENT_SECRET", raising=False)
    assert sso.load_config() is None


def test_con_todo_hay_configuracion(entorno_sso):
    import sso

    cfg = sso.load_config()
    assert cfg is not None
    assert cfg.client_id == "dashboard"
    assert cfg.metadata_url.endswith("/.well-known/openid-configuration")


def test_los_roles_salen_de_realm_access():
    import sso

    claims = {"sub": "x", "realm_access": {"roles": ["palbe", "tecnico"]}}
    assert sso.roles_de(claims) == ["palbe", "tecnico"]


def test_sin_realm_access_no_hay_roles():
    """Un token sin roles no revienta: da lista vacia, y el recibidor
    ensena el estado vacio."""
    import sso

    assert sso.roles_de({"sub": "x"}) == []
    assert sso.roles_de({"sub": "x", "realm_access": {}}) == []
    assert sso.roles_de({}) == []


def test_el_registro_anade_las_tres_rutas(entorno_sso):
    import sso
    from fastapi import FastAPI

    app = FastAPI()
    assert sso.registrar_rutas(app) is True
    rutas = {
        r.path
        for r in app.routes
        if getattr(r, "path", "").startswith(("/sso", "/logout"))
    }
    assert rutas == {"/sso/login", "/sso/callback", "/logout"}


def test_sin_configuracion_no_registra_nada(monkeypatch):
    import sso
    from fastapi import FastAPI

    for v in (
        "SSO_ISSUER",
        "SSO_CLIENT_ID",
        "SSO_CLIENT_SECRET",
        "SSO_REDIRECT_URI",
        "SSO_POST_LOGOUT_URI",
    ):
        monkeypatch.delenv(v, raising=False)
    app = FastAPI()
    assert sso.registrar_rutas(app) is False
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/sso")]


def test_sso_login_con_keycloak_caido_devuelve_503_no_500(entorno_sso, monkeypatch):
    """authorize_redirect() descarga el documento de descubrimiento -- con
    Keycloak caido la excepcion suge y, sin un try aqui, FastAPI responde un
    500 pelado. Y como la raiz SIEMPRE manda a /sso/login a quien no tiene
    sesion, es el UNICO sitio por el que todo el mundo pasa: el callback ya
    tiene su red (_ERROR_IDP), pero solo se llega a el si Keycloak ya
    funciono."""
    import sso
    from authlib.integrations.starlette_client.apps import StarletteOAuth2App
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware
    from starlette.testclient import TestClient

    async def _revienta(self, request, redirect_uri=None, **kwargs):
        raise RuntimeError("Connection refused: auth.sge.local:8080")

    monkeypatch.setattr(StarletteOAuth2App, "authorize_redirect", _revienta)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="clave-de-prueba")
    assert sso.registrar_rutas(app) is True

    client = TestClient(app)
    resp = client.get("/sso/login", follow_redirects=False)

    assert resp.status_code == 503
    assert "no esta disponible" in resp.text
    assert "no es un problema de permisos" in resp.text.lower()


def test_logout_sin_id_token_incluye_client_id(entorno_sso):
    """Keycloak exige id_token_hint O client_id para validar
    post_logout_redirect_uri. Sin sesion (o sin id_token guardado en ella),
    si no fuera client_id el usuario quedaria varado en la pantalla de
    Keycloak."""
    import sso
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware
    from starlette.testclient import TestClient

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="clave-de-prueba")
    assert sso.registrar_rutas(app) is True

    client = TestClient(app)
    resp = client.get("/logout", follow_redirects=False)

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "client_id=dashboard" in location
    assert "id_token_hint" not in location
