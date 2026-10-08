"""Fixtures del recibidor. Sin red, sin base de datos: aqui solo hay logica."""

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "backend"))


@pytest.fixture
def catalogo_real():
    """El herramientas.yaml de verdad, no uno inventado: si alguien lo rompe,
    los tests deben enterarse."""
    import catalogo

    return catalogo.cargar_catalogo(RAIZ / "herramientas.yaml")


@pytest.fixture
def catalogo_real_cargado():
    """Como catalogo_real, pero se carga cuando se LLAMA, no al inyectarse.

    Hace falta para los tests del dominio: catalogo_real se resuelve antes de
    que monkeypatch.setenv haya corrido, asi que leeria el entorno viejo y el
    test pasaria por el motivo equivocado."""
    import catalogo

    def cargar():
        return catalogo.cargar_catalogo(RAIZ / "herramientas.yaml")

    return cargar
