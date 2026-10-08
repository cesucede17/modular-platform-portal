"""
La capa OIDC del recibidor -- el mismo patron que modules/palbe/palbe_sso.py.

Diferencia importante con PALBE: aqui NO hay mapeo de identidades ni usuarios
propios. Quien autentica contra Keycloak, entra; lo que decide que ve son sus
roles de realm. El recibidor no tiene nada que proteger por si mismo -- las
herramientas guardan sus propias puertas.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSOConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    post_logout_uri: str

    @property
    def metadata_url(self) -> str:
        return f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"

    @property
    def end_session_url(self) -> str:
        """Ruta fija de Keycloak, construida a mano -- NO sale del documento
        de descubrimiento (metadata_url). Evita tener que completar todo el
        baile OIDC solo para averiguar donde cerrar sesion; funciona porque
        el proveedor de identidad de esta plataforma siempre es Keycloak."""
        return f"{self.issuer.rstrip('/')}/protocol/openid-connect/logout"


def load_config() -> SSOConfig | None:
    """Todo o nada: si falta cualquier variable, None.

    Una configuracion a medias falla a mitad del baile OIDC con un error que
    no dice nada. Asi falla al arrancar."""
    v = {
        k: os.environ.get(k, "").strip()
        for k in (
            "SSO_ISSUER",
            "SSO_CLIENT_ID",
            "SSO_CLIENT_SECRET",
            "SSO_REDIRECT_URI",
            "SSO_POST_LOGOUT_URI",
        )
    }
    if not all(v.values()):
        return None
    return SSOConfig(
        v["SSO_ISSUER"],
        v["SSO_CLIENT_ID"],
        v["SSO_CLIENT_SECRET"],
        v["SSO_REDIRECT_URI"],
        v["SSO_POST_LOGOUT_URI"],
    )


def roles_de(claims: dict) -> list[str]:
    """Los roles de realm del token. Sin ellos, lista vacia -- el recibidor
    ensena su estado vacio, que es legitimo."""
    return list(((claims or {}).get("realm_access") or {}).get("roles") or [])


_ERROR_IDP = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Identidad no disponible</title></head>
<body style="font-family:system-ui;max-width:34rem;margin:4rem auto;line-height:1.6">
<h1>El sistema de identidad no responde</h1>
<p>No se ha podido completar el inicio de sesion porque el servicio de
identidad de la plataforma no esta disponible ahora mismo.</p>
<p>No es un problema de permisos: vuelve a intentarlo en un momento, y si
persiste avisa a quien administre la plataforma.</p>
<p><a href="/">Reintentar</a></p>
</body></html>"""


def registrar_rutas(app) -> bool:
    """Anade /sso/login, /sso/callback y /logout. False si falta configuracion."""
    cfg = load_config()
    if cfg is None:
        return False

    oauth = OAuth()
    oauth.register(
        name="keycloak",
        server_metadata_url=cfg.metadata_url,
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        client_kwargs={"scope": "openid profile email"},
    )

    @app.get("/sso/login")
    async def sso_login(request: Request):
        try:
            return await oauth.keycloak.authorize_redirect(request, cfg.redirect_uri)
        except Exception as exc:
            # authlib descarga aqui el documento de descubrimiento
            # (server_metadata_url) -- con Keycloak caido, la excepcion
            # sube y sin este try FastAPI responde un 500 pelado. Y como la
            # raiz del recibidor SIEMPRE manda aqui a quien no tiene
            # sesion, /sso/login es el unico sitio por el que todo el mundo
            # pasa: el unico que necesitaba la red puesta, no solo el
            # callback (al que solo se llega si Keycloak ya funciono).
            # Mismo patron que sso_callback: nunca el token ni el code,
            # solo el tipo de excepcion y su mensaje, truncado a 500
            # caracteres porque es un mensaje de una libreria de terceros
            # yendo a un log persistente.
            _log.warning(
                "fallo en sso_login: %s: %s", type(exc).__name__, str(exc)[:500]
            )
            return HTMLResponse(_ERROR_IDP, status_code=503)

    @app.get("/sso/callback")
    async def sso_callback(request: Request):
        try:
            token = await oauth.keycloak.authorize_access_token(request)
        except Exception as exc:
            # El fallo tecnico se distingue del de permisos a proposito:
            # son problemas de personas distintas. Pero que no haya base de
            # datos no significa que no quede ningun rastro: un state
            # manipulado o un code reutilizado no dejan otra huella que
            # esta. Se registra el tipo y el mensaje de la excepcion,
            # truncado a 500 caracteres porque es un mensaje de una libreria
            # de terceros yendo a un log persistente, sin cota no es de
            # fiar. NUNCA el token ni el code.
            _log.warning(
                "fallo en sso_callback: %s: %s", type(exc).__name__, str(exc)[:500]
            )
            return HTMLResponse(_ERROR_IDP, status_code=503)
        claims = token.get("userinfo") or {}
        request.session.clear()
        request.session["sub"] = claims.get("sub", "")
        request.session["nombre"] = (
            claims.get("name") or claims.get("preferred_username") or ""
        )
        request.session["roles"] = roles_de(claims)
        # El id_token va en la cookie de sesion: firmada, pero NO cifrada --
        # cualquiera con la cookie puede leer este JWT con datos personales.
        # Decision consciente: sin base de datos, es el unico sitio donde
        # queda el id_token_hint que /logout necesita para el cierre de
        # sesion unico; sin el, Keycloak muestra una pantalla de
        # confirmacion de cierre de sesion en vez de cerrarla directamente.
        request.session["id_token"] = token.get("id_token", "")
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    async def logout(request: Request):
        """Cierre de sesion UNICO: borra la cookie y cierra tambien la sesion
        de Keycloak. Si solo borrasemos la cookie, volver a entrar pasaria de
        largo sin pedir nada -- y en un equipo compartido eso sorprende."""
        id_token = request.session.get("id_token", "")
        request.session.clear()
        # client_id siempre va: Keycloak exige id_token_hint O client_id
        # para validar post_logout_redirect_uri contra las URIs registradas
        # (post.logout.redirect.uris). Con id_token_hint presente es
        # redundante e inofensivo; sin el -- sesion sin id_token guardado --
        # es lo que evita que Keycloak ignore la vuelta y deje a quien
        # cierra sesion varado en su pantalla.
        params = {
            "post_logout_redirect_uri": cfg.post_logout_uri,
            "client_id": cfg.client_id,
        }
        if id_token:
            params["id_token_hint"] = id_token
        return RedirectResponse(
            f"{cfg.end_session_url}?{urlencode(params)}", status_code=303
        )

    return True
