"""Tests de VOZ-75: bajar el audio de otras apps al dictar y devolverlo como estaba.

La política se prueba entera con un control falso; la capa COM tiene su propio
test marcado `win`, que toca el audio de verdad y lo deja como estaba.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from voziris.audio.mezclador import VOLUMEN_ATENUADO, Sesion
from voziris.audio.mezclador import Mezclador as _Mezclador


def Mezclador(  # noqa: N802 — fábrica con el retraso a cero, que es lo que quieren los tests
    modo: str = "silenciar",
    control: object | None = None,
    rescate: Path | None = None,
    volumen_atenuado: float = VOLUMEN_ATENUADO,
    retraso_s: float = 0.0,
    revision_s: float = 0.05,
) -> _Mezclador:
    return _Mezclador(modo, control, rescate, volumen_atenuado, retraso_s, revision_s)


class ControlFalso:
    """Un sistema de audio de mentira, con las sesiones que se le digan."""

    def __init__(self, *sesiones: Sesion) -> None:
        self.estado = {s.clave: s for s in sesiones}
        self.aplicados: list[tuple[str, bool, float | None]] = []
        self.cerrado = False
        self.desaparecidas: set[str] = set()

    def sesiones(self) -> list[Sesion]:
        return [s for c, s in self.estado.items() if c not in self.desaparecidas]

    def aplicar(
        self, clave: str, mute: bool, volumen: float | None, nombre: str | None = None
    ) -> bool:
        self.aplicados.append((clave, mute, volumen))
        sesion = self.estado.get(clave)
        if sesion is None or clave in self.desaparecidas:
            return False
        sesion.mute = mute
        if volumen is not None:
            sesion.volumen = volumen
        return True

    def cerrar(self) -> None:
        self.cerrado = True


def _sesion(
    clave: str, mute: bool = False, volumen: float = 1.0, activa: bool = True
) -> Sesion:
    return Sesion(
        clave=clave, nombre=f"{clave}.exe",
        pid=int(clave[-1]) if clave[-1].isdigit() else 0,
        mute=mute, volumen=volumen, activa=activa,
    )


def _hasta(condicion, timeout: float = 8.0, que: str = "") -> None:  # type: ignore[no-untyped-def]
    """Espera a que se cumpla algo, sin depender de cuánto tarde la máquina."""
    limite = time.monotonic() + timeout
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.01)
    assert condicion(), f"no se cumplió a tiempo: {que}"


def _esperar(mezclador: _Mezclador, silenciado: bool, timeout: float = 8.0) -> None:
    limite = time.monotonic() + timeout
    while mezclador.silenciado is not silenciado and time.monotonic() < limite:
        time.sleep(0.005)
    assert mezclador.silenciado is silenciado, f"esperaba silenciado={silenciado}"


# --- silenciar y devolver ---------------------------------------------------------------


def test_silenciar_y_devolver_cada_una_a_su_estado(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1"), _sesion("c2", volumen=0.4))
    m = Mezclador("silenciar", control, tmp_path / "rescate.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    assert all(s.mute for s in control.sesiones())
    m.restaurar()
    _esperar(m, False)
    assert [(s.clave, s.mute, s.volumen) for s in control.sesiones()] == [
        ("c1", False, 1.0), ("c2", False, 0.4),
    ]
    m.cerrar()
    assert control.cerrado


def test_atenuar_baja_el_volumen_y_lo_devuelve(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1", volumen=0.8))
    m = Mezclador("atenuar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    assert control.estado["c1"].volumen == VOLUMEN_ATENUADO
    assert control.estado["c1"].mute is False  # atenuar no silencia
    assert [m for _c, m, _v in control.aplicados] == [False]  # ni toca el mute
    m.restaurar()
    _esperar(m, False)
    assert control.estado["c1"].volumen == 0.8
    m.cerrar()


def test_modo_nada_no_toca_nada(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1"))
    m = Mezclador("nada", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    time.sleep(0.2)
    assert control.aplicados == [] and not m.silenciado
    m.cerrar()


def test_lo_que_ya_estaba_callado_se_queda_callado(tmp_path: Path) -> None:
    """Si el usuario ya tenía esa app silenciada, al terminar no se le desilencia."""
    control = ControlFalso(_sesion("muda", mute=True), _sesion("suena"))
    m = Mezclador("silenciar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    assert [c for c, _m, _v in control.aplicados] == ["suena"]
    m.restaurar()
    _esperar(m, False)
    assert control.estado["muda"].mute is True
    assert control.estado["suena"].mute is False
    m.cerrar()


def test_si_el_usuario_mueve_el_volumen_mientras_esta_atenuado_manda_el(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1", volumen=0.9))
    m = Mezclador("atenuar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    control.estado["c1"].volumen = 0.5  # el usuario lo sube a mano
    m.restaurar()
    _esperar(m, False)
    assert control.estado["c1"].volumen == 0.5  # no se le pisa
    assert control.estado["c1"].mute is False
    m.cerrar()


def test_una_sesion_que_desaparece_no_rompe_la_restauracion(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1"), _sesion("c2"))
    m = Mezclador("silenciar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    control.desaparecidas.add("c1")  # se cerró el reproductor
    m.restaurar()
    _esperar(m, False)
    assert control.estado["c2"].mute is False
    m.cerrar()


def test_ordenes_seguidas_se_quedan_con_la_ultima(tmp_path: Path) -> None:
    """Dictados encadenados: silenciar/restaurar/silenciar no deja el audio a medias."""
    control = ControlFalso(_sesion("c1"))
    m = Mezclador("silenciar", control, tmp_path / "r.json")
    m.arrancar()
    for _ in range(20):
        m.silenciar()
        m.restaurar()
    m.silenciar()
    _esperar(m, True)
    assert control.estado["c1"].mute is True
    m.cerrar()
    assert control.estado["c1"].mute is False  # cerrar siempre devuelve el sonido


def test_silenciar_dos_veces_no_pierde_el_estado_previo(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1", volumen=0.7))
    m = Mezclador("atenuar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    m.silenciar()  # un segundo dictado sin haber restaurado
    time.sleep(0.15)
    m.restaurar()
    _esperar(m, False)
    assert control.estado["c1"].volumen == 0.7  # el 0.7 original, no el atenuado
    m.cerrar()


# --- rescate tras una caída ----------------------------------------------------------------


def test_el_rescate_se_escribe_antes_de_tocar_y_se_borra_al_devolver(tmp_path: Path) -> None:
    rescate = tmp_path / "rescate.json"
    control = ControlFalso(_sesion("c1", volumen=0.6))
    m = Mezclador("silenciar", control, rescate)
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    assert rescate.is_file() and "0.6" in rescate.read_text(encoding="utf-8")
    m.restaurar()
    _esperar(m, False)
    assert not rescate.exists()
    m.cerrar()


def test_al_arrancar_se_deshace_lo_que_dejo_una_ejecucion_anterior(tmp_path: Path) -> None:
    """Voziris murió con la música silenciada: al abrirla otra vez, vuelve el sonido."""
    rescate = tmp_path / "rescate.json"
    # Lo que deja una caída: el archivo escrito y las sesiones tocadas.
    escritor = ControlFalso(_sesion("c1", volumen=0.6))
    m = Mezclador("silenciar", escritor, rescate)
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    assert escritor.estado["c1"].mute is True
    quedo_escrito = rescate.read_text(encoding="utf-8")
    m._hilo = None  # el proceso se fue sin pasar por cerrar(): nadie restauró
    assert quedo_escrito == rescate.read_text(encoding="utf-8")

    otro_control = ControlFalso(_sesion("c1", mute=True, volumen=0.6))
    otro = Mezclador("silenciar", otro_control, rescate)
    otro.arrancar()
    _hasta(lambda: not rescate.exists(), que="deshacer lo de la ejecución anterior")
    assert otro_control.estado["c1"].mute is False
    assert otro_control.estado["c1"].volumen == 0.6
    assert not rescate.exists()
    otro.cerrar()
    m.cerrar()


def test_un_rescate_ilegible_se_tira_sin_romper(tmp_path: Path) -> None:
    rescate = tmp_path / "rescate.json"
    rescate.write_text("{esto no es json", encoding="utf-8")
    control = ControlFalso(_sesion("c1"))
    m = Mezclador("silenciar", control, rescate)
    m.arrancar()
    time.sleep(0.2)
    assert not rescate.exists()
    m.cerrar()


def test_sin_control_no_revienta(tmp_path: Path) -> None:
    """Fuera de Windows, o si COM no arranca: el dictado sigue funcionando igual."""
    m = Mezclador("silenciar", None, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    m.restaurar()
    m.cerrar()


def test_un_control_que_lanza_no_tumba_el_hilo(tmp_path: Path) -> None:
    class ControlRoto(ControlFalso):
        def sesiones(self) -> list[Sesion]:
            raise OSError("COM se ha caído")

    m = Mezclador("silenciar", ControlRoto(_sesion("c1")), tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _hasta(lambda: m.ultimo_error is not None, que="registrar el fallo del control")
    assert not m.silenciado
    m.restaurar()  # el hilo sigue vivo y sigue atendiendo
    time.sleep(0.2)
    m.cerrar()


def test_modo_desconocido_cae_en_silenciar(tmp_path: Path) -> None:
    assert Mezclador("lo que sea").modo == "silenciar"
    assert Mezclador("atenuar").modo == "atenuar"


# --- enganchado al dictado ----------------------------------------------------------------


class MezcladorFalso:
    """Registra las órdenes que le da el orquestador, en orden."""

    def __init__(self) -> None:
        self.modo = "silenciar"
        self.ordenes: list[str] = []

    def silenciar(self) -> None:
        self.ordenes.append("silenciar")

    def restaurar(self) -> None:
        self.ordenes.append("restaurar")


def _orquestador_con(mezclador: MezcladorFalso, motor: object | None = None) -> object:
    """Un orquestador mínimo, con las piezas falsas de test_orquestador."""
    import numpy as np

    from voziris.orquestador import Orquestador
    from voziris.tipos import SAMPLE_RATE, Audio, Entrega, Transcripcion

    class Captura:
        oyente_bloques = None

        def __init__(self) -> None:
            self.falla_al_terminar = False

        def empezar_dictado(self) -> None: ...

        def terminar_dictado(self) -> Audio:
            if self.falla_al_terminar:
                from voziris.errores import MicrofonoNoDisponible

                raise MicrofonoNoDisponible("los auriculares se fueron")
            return Audio(muestras=np.zeros(SAMPLE_RATE, dtype=np.float32))

        def cancelar_dictado(self) -> None: ...

    class Motor:
        nombre = "local"
        requiere_red = False

        def precalentar(self) -> None: ...

        def disponible(self) -> bool:
            return True

        def transcribir(self, audio: Audio, idioma: str) -> Transcripcion:
            return Transcripcion("Hola.", idioma, "local", 1, audio.duracion_s)

    class DestinoOk:
        nombre = "app_activa"

        def entregar(self, texto: str, ctx: object) -> Entrega:
            return Entrega(ok=True, detalle="pegado")

    orq = Orquestador(
        Captura(), motor or Motor(), {"app_activa": DestinoOk()}, [], mezclador=mezclador
    )
    orq.arrancar()
    assert orq.motor_listo.wait(3)
    return orq


def _hasta_reposo(orq: object, timeout: float = 3.0) -> None:
    from voziris.orquestador import Estado

    limite = time.monotonic() + timeout
    while orq.estado is Estado.PROCESANDO and time.monotonic() < limite:  # type: ignore[attr-defined]
        time.sleep(0.01)


def test_el_dictado_baja_el_audio_al_grabar_y_lo_sube_al_dejar_de_grabar() -> None:
    from voziris.tipos import Modo

    m = MezcladorFalso()
    orq = _orquestador_con(m)
    orq.empezar(Modo.MANTENER, "app_activa")  # type: ignore[attr-defined]
    assert m.ordenes == ["silenciar"]
    orq.terminar(1500)  # type: ignore[attr-defined]
    # Se sube al pasar a PROCESANDO: quien dicta ya ha terminado de hablar.
    assert m.ordenes == ["silenciar", "restaurar"]
    _hasta_reposo(orq)
    assert m.ordenes == ["silenciar", "restaurar"]
    orq.parar()  # type: ignore[attr-defined]


def test_cancelar_tambien_sube_el_audio() -> None:
    from voziris.tipos import Modo

    m = MezcladorFalso()
    orq = _orquestador_con(m)
    orq.empezar(Modo.CLAVAR, "app_activa")  # type: ignore[attr-defined]
    orq.cancelar()  # type: ignore[attr-defined]
    assert m.ordenes == ["silenciar", "restaurar"]
    orq.parar()  # type: ignore[attr-defined]


def test_una_pulsacion_de_roce_sube_el_audio() -> None:
    """Se descarta por corta y no suena tono de fin, pero la música tiene que volver."""
    from voziris.tipos import Modo

    m = MezcladorFalso()
    orq = _orquestador_con(m)
    orq.empezar(Modo.MANTENER, "app_activa")  # type: ignore[attr-defined]
    orq.terminar(duracion_ms=80)  # type: ignore[attr-defined]
    assert m.ordenes == ["silenciar", "restaurar"]
    orq.parar()  # type: ignore[attr-defined]


def test_si_el_microfono_desaparece_el_audio_vuelve() -> None:
    from voziris.orquestador import Estado
    from voziris.tipos import Modo

    m = MezcladorFalso()
    orq = _orquestador_con(m)
    orq.empezar(Modo.MANTENER, "app_activa")  # type: ignore[attr-defined]
    orq._captura.falla_al_terminar = True  # type: ignore[attr-defined]
    orq.terminar(1500)  # type: ignore[attr-defined]
    assert orq.estado is Estado.ERROR  # type: ignore[attr-defined]
    assert m.ordenes == ["silenciar", "restaurar"]
    orq.parar()  # type: ignore[attr-defined]


@pytest.mark.win
@pytest.mark.audio
def test_core_audio_de_verdad_lista_y_restaura() -> None:
    """Toca el audio real de este equipo: lista sesiones, silencia y deja todo como estaba."""
    from voziris.audio.mezclador import SesionesCoreAudio

    control = SesionesCoreAudio()
    try:
        antes = control.sesiones()
        if not antes:
            pytest.skip("no hay ninguna aplicación reproduciendo audio")
        objetivo = antes[0]
        assert objetivo.clave and 0.0 <= objetivo.volumen <= 1.0
        assert control.aplicar(objetivo.clave, True, None)
        despues = {s.clave: s for s in control.sesiones()}
        assert despues[objetivo.clave].mute is True
    finally:
        for sesion in antes:
            control.aplicar(sesion.clave, sesion.mute, sesion.volumen)
        control.cerrar()
    final = {s.clave: s for s in SesionesCoreAudio().sesiones()}
    for sesion in antes:
        if sesion.clave in final:
            assert final[sesion.clave].mute == sesion.mute


# --- lo aprendido de la revisión (VOZ-75) ---------------------------------------------


def test_solo_se_toca_lo_que_esta_sonando(tmp_path: Path) -> None:
    """Chrome con una pestaña abierta pero callada no se toca: solo lo que suena."""
    control = ControlFalso(_sesion("suena"), _sesion("callada", activa=False))
    m = Mezclador("silenciar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    assert [c for c, _m, _v in control.aplicados] == ["suena"]
    assert control.estado["callada"].mute is False
    m.cerrar()


def test_un_roce_del_atajo_no_llega_a_tocar_el_audio(tmp_path: Path) -> None:
    """Una pulsación que se descarta por corta no debe abrir un agujero en la música."""
    control = ControlFalso(_sesion("c1"))
    m = _Mezclador("silenciar", control, tmp_path / "r.json", retraso_s=0.25)
    m.arrancar()
    m.silenciar()
    time.sleep(0.05)
    m.restaurar()  # el dictado se descartó a los 50 ms
    time.sleep(0.5)
    assert control.aplicados == [], "tocó el audio de una pulsación descartada"
    assert not m.silenciado
    m.cerrar()


def test_lo_que_empieza_a_sonar_a_media_grabacion_tambien_se_calla(tmp_path: Path) -> None:
    control = ControlFalso(_sesion("c1"))
    m = Mezclador("silenciar", control, tmp_path / "r.json")
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    control.estado["tarde"] = _sesion("tarde")  # el usuario le da al play
    _hasta(lambda: control.estado["tarde"].mute, que="callar lo que empezó a sonar")
    m.restaurar()
    _esperar(m, False)
    assert control.estado["tarde"].mute is False
    m.cerrar()


def test_si_no_se_puede_anotar_el_rescate_no_se_toca_nada(tmp_path: Path) -> None:
    """Un mute que no se sabría deshacer es peor que dictar con música."""
    rescate = tmp_path / "carpeta-ocupada" / "r.json"
    rescate.parent.write_text("soy un archivo, no una carpeta", encoding="utf-8")
    control = ControlFalso(_sesion("c1"))
    m = Mezclador("silenciar", control, rescate)
    m.arrancar()
    m.silenciar()
    time.sleep(0.4)
    assert control.aplicados == [] and not m.silenciado
    m.cerrar()


def test_lo_que_no_se_pudo_devolver_se_queda_apuntado(tmp_path: Path) -> None:
    """Si una sesión no se deja devolver, el rescate NO se borra: se conserva."""
    rescate = tmp_path / "r.json"

    class NoDevuelve(ControlFalso):
        def __init__(self, *sesiones: Sesion) -> None:
            super().__init__(*sesiones)
            self.devolviendo = False

        def aplicar(
            self, clave: str, mute: bool, volumen: float | None, nombre: str | None = None
        ) -> bool:
            if self.devolviendo:
                return False
            return super().aplicar(clave, mute, volumen, nombre)

    control = NoDevuelve(_sesion("c1"))
    m = Mezclador("silenciar", control, rescate)
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    control.devolviendo = True
    m.restaurar()
    # Tras los reintentos, lo que no se pudo devolver sigue apuntado.
    _hasta(lambda: rescate.is_file() and m.hay_sonido_bajado(),
           que="conservar la anotación de lo que quedó callado")
    control.devolviendo = False
    m.devolver_el_sonido()
    _hasta(lambda: not rescate.exists(), que="borrar el rescate al devolverlo todo")
    assert control.estado["c1"].mute is False
    m.cerrar()


def test_el_rescate_se_suma_y_no_se_pisa(tmp_path: Path) -> None:
    """Lo que se calla después se añade a lo ya anotado, no lo sustituye."""
    import json

    rescate = tmp_path / "r.json"
    control = ControlFalso(_sesion("c1"))
    m = Mezclador("silenciar", control, rescate)
    m.arrancar()
    m.silenciar()
    _esperar(m, True)
    control.estado["c2"] = _sesion("c2")
    _hasta(lambda: control.estado["c2"].mute, que="callar la que empezó después")
    _hasta(lambda: {e["clave"] for e in json.loads(rescate.read_text(encoding="utf-8"))}
           == {"c1", "c2"}, que="sumar la nueva al rescate")
    m.cerrar()
    assert not rescate.exists()


def test_un_rescate_ilegible_se_aparta_no_se_borra(tmp_path: Path) -> None:
    rescate = tmp_path / "r.json"
    rescate.write_text("{esto no es json", encoding="utf-8")
    m = Mezclador("silenciar", ControlFalso(_sesion("c1")), rescate)
    m.arrancar()
    _hasta(lambda: rescate.with_suffix(".corrupto").is_file(), que="apartar el rescate ilegible")
    assert not rescate.exists(), "se tiró la única pista de qué se tocó"
    m.cerrar()


# --- el menú de la bandeja ------------------------------------------------------------


def test_menu_mientras_dicto_y_devolver_el_sonido() -> None:
    from voziris.ui.bandeja import AccionesBandeja, Bandeja

    llamadas: list[tuple[str, object]] = []
    bajado = [False]
    modo = ["silenciar"]
    acciones = AccionesBandeja(
        dictar_ahora=lambda: None, dictar_markdown=lambda: None, alternar_corte=lambda: None,
        abrir_ajustes=lambda: None, cambiar_motor=lambda m: None, reintentar=lambda i: None,
        borrar_entrada=lambda i: None, salir=lambda: None,
        cambiar_al_dictar=lambda m: modo.__setitem__(0, m),
        al_dictar_actual=lambda: modo[0],
        devolver_sonido=lambda: llamadas.append(("devolver", None)),
        hay_sonido_bajado=lambda: bajado[0],
    )
    items = {str(i.text): i for i in Bandeja(acciones).construir_menu().items}
    opciones = list(items["Mientras dicto"].submenu.items)
    assert [str(o.text) for o in opciones] == [
        "Silenciar lo que suene", "Bajarle el volumen", "No tocar nada"
    ]
    assert opciones[0].checked and not opciones[2].checked
    opciones[2](None)
    assert modo[0] == "nada"
    # El salvavidas solo aparece cuando hay algo callado.
    assert not items["Devolver el sonido"].visible
    bajado[0] = True
    items = {str(i.text): i for i in Bandeja(acciones).construir_menu().items}
    assert items["Devolver el sonido"].visible
    items["Devolver el sonido"](None)
    assert llamadas == [("devolver", None)]
