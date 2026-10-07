"""Tests del historial con reintento (VOZ-52). Archivos en tmp_path, destinos falsos."""

from __future__ import annotations

import json
import os
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from voziris.errores import EntregaFallida
from voziris.historial import Historial
from voziris.tipos import SAMPLE_RATE, Audio, Contexto, EntradaHistorial, Entrega, Modo, Nivel


class DestinoFalso:
    nombre = "app_activa"

    def __init__(self) -> None:
        self.entregas: list[str] = []
        self.falla = False

    def entregar(self, texto: str, ctx: Contexto) -> Entrega:
        if self.falla:
            raise EntregaFallida("ventana elevada")
        self.entregas.append(texto)
        return Entrega(ok=True, detalle="pegado en notepad.exe")


def _entrada(texto: str, entregado: bool = True, destino: str = "app_activa") -> EntradaHistorial:
    return EntradaHistorial(
        momento=datetime(2026, 9, 4, 19, 42, 5), texto=texto, motor="local", destino=destino,
        entregado=entregado, ms_total=900, duracion_audio_s=4.0,
    )


def _reciente(texto: str) -> EntradaHistorial:
    """Como `_entrada`, pero de hoy: la grabación se poda por días y la otra es de septiembre."""
    entrada = _entrada(texto)
    entrada.momento = datetime.now()
    return entrada


def _ctx(destino: str) -> Contexto:
    return Contexto(destino, Modo.MANTENER, None, False, Nivel.LITERAL)


def _historial(tmp_path: Path, **kwargs: object) -> tuple[Historial, DestinoFalso]:
    destino = DestinoFalso()
    h = Historial(
        tmp_path / "historial" / "dictados.jsonl", destinos={"app_activa": destino},
        contexto=_ctx, **kwargs,  # type: ignore[arg-type]
    )
    return h, destino


def test_jsonl_junto_al_ejecutable_con_indices(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("uno"))
    h.registrar(_entrada("dos"))
    lineas = h.ruta.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    primera = json.loads(lineas[0])
    assert primera["texto"] == "uno" and primera["indice"] == 1
    assert primera["momento"] == "2026-09-04T19:42:05" and primera["audio"] is None
    assert [e.texto for e in h.ultimas()] == ["dos", "uno"]  # la más nueva primero
    assert h.ultimas(1)[0].indice == 2


def test_recorte_a_entradas(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, maximo=5)
    for n in range(12):
        h.registrar(_entrada(f"d{n}"))
    todas = h.todas()
    assert len(todas) <= 10 and todas[-1].texto == "d11"
    assert len(h.ultimas(10)) <= 10
    assert h.ultimas(1)[0].indice == 12  # los índices no se reutilizan


def test_archivo_corrupto_no_impide_arrancar(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("buena"))
    with open(h.ruta, "a", encoding="utf-8") as f:
        f.write('{"texto": "a medias", "mom')  # corte de luz
        f.write("\n\nno es json\n")
    h.registrar(_entrada("después"))
    assert [e.texto for e in h.ultimas()] == ["después", "buena"]


def test_reintentar_reentrega_el_texto_procesado_sin_transcribir(tmp_path: Path) -> None:
    h, destino = _historial(tmp_path)
    h.registrar(_entrada("Texto ya limpio.", entregado=False))
    indice = h.ultimas(1)[0].indice
    entrega = h.reintentar(indice)
    assert entrega.ok and entrega.detalle == "pegado en notepad.exe"
    assert destino.entregas == ["Texto ya limpio."]
    assert h.ultimas(1)[0].entregado is True  # queda marcada como entregada


def test_reintentar_fallido_o_inexistente(tmp_path: Path) -> None:
    h, destino = _historial(tmp_path)
    h.registrar(_entrada("hola", entregado=False))
    destino.falla = True
    entrega = h.reintentar(1)
    assert not entrega.ok and "elevada" in entrega.detalle
    assert h.ultimas(1)[0].entregado is False
    assert not h.reintentar(99).ok
    h.registrar(_entrada("md", destino="markdown"))
    assert "markdown" in h.reintentar(2).detalle  # sin ese destino configurado


def test_borrar_dictado_sensible(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("normal"))
    h.registrar(_entrada("mi contraseña es patata"))
    assert h.borrar(2)
    assert [e.texto for e in h.todas()] == ["normal"]
    assert "patata" not in h.ruta.read_text(encoding="utf-8")
    assert not h.borrar(2)


def _pendiente(tmp_path: Path, segundos: float = 1.0, valor: float = 0.1) -> Path:
    """Un archivo como el que escribe la captura mientras se habla: float32 crudo a 16 kHz."""
    ruta = tmp_path / "pendientes" / f"20260923-0954{int(segundos * 10):02d}.f32"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    np.full(int(SAMPLE_RATE * segundos), valor, dtype="<f4").tofile(ruta)
    return ruta


def test_la_grabacion_del_pendiente_se_mueve_al_historial(tmp_path: Path) -> None:
    """Se mueve, no se copia: es el archivo que ya se escribió mientras se hablaba."""
    h, _ = _historial(tmp_path, conservar_audio=True)
    pendiente = _pendiente(tmp_path, 1.5)
    crudo = pendiente.read_bytes()

    entrada = h.registrar(_reciente("con grabación"), pendiente=pendiente)

    assert entrada.indice == 1 and entrada.audio == "000001.f32"
    assert not pendiente.exists()
    assert (h.carpeta_audio / "000001.f32").read_bytes() == crudo
    assert h.ultimas(1)[0].audio == "000001.f32"  # también en el archivo, no solo en memoria
    audio = h.cargar_audio(1)
    assert audio is not None and audio.sr == SAMPLE_RATE
    assert len(audio.muestras) == int(SAMPLE_RATE * 1.5)


def test_sin_pendiente_se_guarda_el_audio_que_hay_en_memoria(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("de memoria"), Audio(muestras=np.full(SAMPLE_RATE, 0.2, np.float32)))
    audio = h.cargar_audio(1)
    assert audio is not None and len(audio.muestras) == SAMPLE_RATE


def test_sin_conservar_audio_no_se_toca_ni_el_pendiente(tmp_path: Path) -> None:
    """Qué hacer con el pendiente es entonces cosa del orquestador, no del historial."""
    h, _ = _historial(tmp_path)
    pendiente = _pendiente(tmp_path)
    h.registrar(_reciente("sin audio"), Audio(muestras=np.zeros(100, np.float32)), pendiente)
    assert h.ultimas(1)[0].audio is None and h.cargar_audio(1) is None
    assert pendiente.exists()
    assert not h.carpeta_audio.exists()


def test_se_guarda_sin_entregar_y_luego_se_marca(tmp_path: Path) -> None:
    """El orden de VOZ-80: primero a salvo, luego pegar, luego quitar la marca."""
    h, _ = _historial(tmp_path)
    h.registrar(_entrada("a salvo antes de pegar", entregado=False))
    assert h.buscar(1) is not None and h.buscar(1).entregado is False  # type: ignore[union-attr]
    assert h.marcar_entregado(1) is True
    assert h.buscar(1).entregado is True  # type: ignore[union-attr]
    assert h.marcar_entregado(99) is False


def test_volver_a_transcribir_cambia_el_texto_y_conserva_la_grabacion(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("texto malo"), pendiente=_pendiente(tmp_path))
    assert h.cambiar_texto(1, "texto bueno", "api:groq") is True
    entrada = h.buscar(1)
    assert entrada is not None
    assert (entrada.texto, entrada.motor) == ("texto bueno", "api:groq")
    assert entrada.audio == "000001.f32"


def test_poda_por_numero_y_por_dias_pero_nunca_la_ultima(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, conservar_audio=True, audio_maximo=2, audio_dias=7)
    for i in range(4):
        h.registrar(_reciente(f"dictado {i}"), pendiente=_pendiente(tmp_path, 1.0 + i / 10))
        h.podar_audio()  # lo que hace el orquestador tras cada dictado, ya pegado
    con_audio = [e.indice for e in h.todas() if e.audio]
    assert con_audio == [3, 4]
    assert sorted(f.name for f in h.carpeta_audio.iterdir()) == ["000003.f32", "000004.f32"]

    viejo = _reciente("de hace diez días")
    viejo.momento = datetime(2020, 1, 1, 9, 0)
    h.registrar(viejo, pendiente=_pendiente(tmp_path, 1.9))
    h.podar_audio()
    # Es la más reciente: aunque sea «vieja» no se quita, es la que se acaba de dictar.
    assert [e.indice for e in h.todas() if e.audio] == [4, 5]


def test_poda_por_tamano(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from voziris import historial as modulo

    monkeypatch.setattr(modulo, "AUDIO_MAXIMO_MB", 0.1)  # ~100 KB: cabe menos de un segundo más
    h, _ = _historial(tmp_path, conservar_audio=True)
    for i in range(3):
        h.registrar(_reciente(f"dictado {i}"), pendiente=_pendiente(tmp_path, 1.0 + i / 10))
    h.podar_audio()
    assert [e.indice for e in h.todas() if e.audio] == [3]


def test_borrar_y_recortar_se_llevan_la_grabacion(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, maximo=2, conservar_audio=True, audio_maximo=50)
    h.registrar(_reciente("sensible"), pendiente=_pendiente(tmp_path, 1.1))
    assert h.borrar(1) is True
    assert not (h.carpeta_audio / "000001.f32").exists()
    for i in range(5):  # con maximo=2 se recorta al pasar de 4
        h.registrar(_reciente(f"dictado {i}"), pendiente=_pendiente(tmp_path, 1.2 + i / 10))
    quedan = {e.audio for e in h.todas() if e.audio}
    assert {f.name for f in h.carpeta_audio.iterdir()} == quedan


def test_los_archivos_que_nadie_nombra_se_borran_al_arrancar(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("con grabación"), pendiente=_pendiente(tmp_path))
    suelto_viejo = h.carpeta_audio / "000777.f32"
    suelto_nuevo = h.carpeta_audio / "000778.f32"
    ajeno = h.carpeta_audio / "notas.txt"
    for ruta in (suelto_viejo, suelto_nuevo, ajeno):
        ruta.write_bytes(b"\0" * 64)
    hace_una_hora = datetime.now().timestamp() - 3600
    os.utime(suelto_viejo, (hace_una_hora, hace_una_hora))
    os.utime(ajeno, (hace_una_hora, hace_una_hora))

    h.limpiar_audio()

    assert not suelto_viejo.exists()
    assert suelto_nuevo.exists()  # recién escrito: puede ser de un registro a medias
    assert ajeno.exists()  # no es audio: no es cosa nuestra
    assert (h.carpeta_audio / "000001.f32").exists()


def test_el_wav_de_antes_tambien_se_puede_volver_a_transcribir(tmp_path: Path) -> None:
    """Los que dejaba `guardar_audio` antes de VOZ-80 siguen valiendo."""
    h, _ = _historial(tmp_path)
    h.carpeta_audio.mkdir(parents=True)
    with wave.open(str(h.carpeta_audio / "000001.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.full(SAMPLE_RATE, 0.25) * 32767).astype("<i2").tobytes())
    entrada = _reciente("de antes")
    entrada.audio = "000001.wav"
    h.ruta.write_text(json.dumps(h._a_dict(entrada)) + "\n", encoding="utf-8")
    audio = h.cargar_audio(0)
    assert audio is not None and len(audio.muestras) == SAMPLE_RATE


def test_varios_hilos_a_la_vez_no_pierden_ni_rompen_nada(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """El hilo de trabajo escribe mientras la bandeja lee y el usuario borra o copia."""
    import threading

    h, _ = _historial(tmp_path, maximo=1000, conservar_audio=True, audio_maximo=1000)
    fallos: list[BaseException] = []

    def escribir(hilo: int) -> None:
        try:
            for i in range(15):
                h.registrar(_reciente(f"hilo {hilo} dictado {i}"), pendiente=_pendiente(
                    tmp_path / f"h{hilo}", 1.0 + i / 100))
        except BaseException as e:  # noqa: BLE001
            fallos.append(e)

    def leer() -> None:
        try:
            for _ in range(60):
                for entrada in h.ultimas(10):
                    h.marcar_entregado(entrada.indice)
        except BaseException as e:  # noqa: BLE001
            fallos.append(e)

    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(4)]
    hilos.append(threading.Thread(target=leer))
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)

    assert fallos == []
    todas = h.todas()
    assert len(todas) == 60
    assert len({e.indice for e in todas}) == 60  # ningún índice repetido
    assert all(e.audio and (h.carpeta_audio / e.audio).exists() for e in todas)
    assert "ilegible" not in caplog.text


def test_nada_registrado(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    assert h.ultimas() == [] and h.buscar(1) is None


# --- segunda revisión de VOZ-80 -------------------------------------------------------------------


def test_registrar_no_poda_para_no_retrasar_el_pegado(tmp_path: Path) -> None:
    """Va antes de pegar: lo mínimo. La poda la pide el orquestador después."""
    h, _ = _historial(tmp_path, conservar_audio=True, audio_maximo=1)
    for i in range(3):
        h.registrar(_reciente(f"dictado {i}"), pendiente=_pendiente(tmp_path, 1.0 + i / 10))
    assert [e.indice for e in h.todas() if e.audio] == [1, 2, 3]
    assert h.podar_audio() == 2
    assert [e.indice for e in h.todas() if e.audio] == [3]


def test_poda_por_dias_de_verdad(tmp_path: Path) -> None:
    """Con sitio de sobra por número: la de hace diez días se va por los días."""
    from datetime import timedelta

    h, _ = _historial(tmp_path, conservar_audio=True, audio_maximo=50, audio_dias=7)
    viejo = _reciente("de hace diez días")
    viejo.momento = datetime.now() - timedelta(days=10)
    h.registrar(viejo, pendiente=_pendiente(tmp_path, 1.1))
    h.registrar(_reciente("de hoy"), pendiente=_pendiente(tmp_path, 1.2))
    h.podar_audio()
    assert [e.indice for e in h.todas() if e.audio] == [2]


def test_si_la_linea_no_se_puede_escribir_el_pendiente_vuelve_a_su_sitio(tmp_path: Path) -> None:
    """Nunca una grabación sin el texto que la nombra: la borraría la limpieza."""
    import stat

    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("uno"), pendiente=_pendiente(tmp_path, 1.1))
    pendiente = _pendiente(tmp_path, 1.2)
    crudo = pendiente.read_bytes()
    os.chmod(h.ruta, stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            h.registrar(_reciente("dos"), pendiente=pendiente)
    finally:
        os.chmod(h.ruta, stat.S_IREAD | stat.S_IWRITE)
    assert pendiente.read_bytes() == crudo
    assert sorted(f.name for f in h.carpeta_audio.iterdir()) == ["000001.f32"]


def test_un_corte_de_luz_a_media_linea_no_se_come_el_dictado_siguiente(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_reciente("antes del corte"))
    with open(h.ruta, "a", encoding="utf-8") as archivo:
        archivo.write('{"momento": "2026-09-23T09:54:00", "texto": "a medi')  # sin salto
    h.registrar(_reciente("después del corte"))
    assert [e.texto for e in h.todas()] == ["antes del corte", "después del corte"]


def test_las_lineas_ilegibles_se_apartan_en_vez_de_perderse(tmp_path: Path) -> None:
    """Al reescribir (marcar entregado, podar…) la línea rota puede ser la única copia."""
    h, _ = _historial(tmp_path)
    h.registrar(_reciente("uno"))
    rota = '{"momento": "2026-09-23T09:54:00", "texto": "lo dictado justo antes del cor'
    with open(h.ruta, "a", encoding="utf-8") as archivo:
        archivo.write(rota + "\n" + "\x00" * 40 + "\n")
    h.registrar(_reciente("dos"))
    h.marcar_entregado(2)  # reescribe el archivo entero
    apartado = h.ruta.with_name("dictados.ilegibles.jsonl")
    assert apartado.read_text(encoding="utf-8").splitlines() == [rota]  # los ceros, no
    assert [e.texto for e in h.todas()] == ["uno", "dos"]
    assert rota not in h.ruta.read_text(encoding="utf-8")


def test_cambiar_texto_solo_si_la_entrada_sigue_siendo_la_misma(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    entrada = h.registrar(_reciente("el de siempre"))
    assert h.cambiar_texto(1, "otro", "local", momento=datetime(2001, 1, 1)) is False
    assert h.cambiar_texto(1, "nuevo", "local", momento=entrada.momento.replace(microsecond=0))
    actual = h.buscar(1)
    assert actual is not None
    assert (actual.texto, actual.texto_anterior) == ("nuevo", "el de siempre")


def test_si_el_pendiente_quedo_corto_se_guarda_el_audio_de_memoria(tmp_path: Path) -> None:
    """El escritor se atascó y cerró a medias: la grabación completa está en memoria."""
    h, _ = _historial(tmp_path, conservar_audio=True)
    corto = _pendiente(tmp_path, 1.0)
    completo = Audio(muestras=np.full(SAMPLE_RATE * 6, 0.2, np.float32))
    h.registrar(_reciente("seis segundos"), completo, corto)
    audio = h.cargar_audio(1)
    assert audio is not None and len(audio.muestras) == SAMPLE_RATE * 6
    assert corto.exists()  # el corto lo borra el orquestador, que sabe si está a salvo


def test_con_la_grabacion_apagada_al_arrancar_se_borra_la_que_hubiera(tmp_path: Path) -> None:
    """Quien apaga conservar_audio no quiere que siga ahí siete días más."""
    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("con grabación"), pendiente=_pendiente(tmp_path))
    apagado, _ = _historial(tmp_path, conservar_audio=False)
    apagado.limpiar_audio()
    assert [e.audio for e in apagado.todas()] == [None]
    assert list(apagado.carpeta_audio.iterdir()) == []


def test_recortar_con_una_sola_entrada_tambien_se_lleva_la_grabacion(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path, maximo=1, conservar_audio=True, audio_maximo=50)
    for i in range(3):
        h.registrar(_reciente(f"dictado {i}"), pendiente=_pendiente(tmp_path, 1.0 + i / 10))
    quedan = {e.audio for e in h.todas() if e.audio}
    assert {f.name for f in h.carpeta_audio.iterdir()} == quedan


# --- tercera ronda de VOZ-80 ----------------------------------------------------------------------


def test_un_historial_guardado_con_bom_se_sigue_leyendo(tmp_path: Path) -> None:
    """El Bloc de notas antiguo o PowerShell 5 ponen BOM al guardar."""
    h, _ = _historial(tmp_path)
    h.registrar(_reciente("uno"))
    h.ruta.write_bytes(b"\xef\xbb\xbf" + h.ruta.read_bytes())
    assert [e.texto for e in h.todas()] == ["uno"]
    assert h.registrar(_reciente("dos")).indice == 2  # sin reutilizar el índice del primero


def test_una_linea_json_que_no_es_una_entrada_no_tumba_el_historial(tmp_path: Path) -> None:
    h, _ = _historial(tmp_path)
    h.registrar(_reciente("uno"))
    with open(h.ruta, "a", encoding="utf-8") as archivo:
        archivo.write("0\nnull\n[]\n\"texto suelto\"\n")
    h.registrar(_reciente("dos"))
    assert [e.texto for e in h.todas()] == ["uno", "dos"]


def test_una_grabacion_sin_texto_se_rescata_a_pendientes_en_vez_de_borrarse(
    tmp_path: Path,
) -> None:
    """La línea no llegó a disco (un apagón): la grabación es un dictado sin transcribir."""
    from voziris.pendientes import Pendientes

    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("con su línea"), pendiente=_pendiente(tmp_path, 1.1))
    huerfana = h.carpeta_audio / "000002.f32"
    np.full(SAMPLE_RATE * 3, 0.1, dtype="<f4").tofile(huerfana)
    viejo_wav = h.carpeta_audio / "000003.wav"
    viejo_wav.write_bytes(b"RIFF")
    hace_una_hora = datetime.now().timestamp() - 3600
    for ruta in (huerfana, viejo_wav):
        os.utime(ruta, (hace_una_hora, hace_una_hora))

    h.limpiar_audio()

    assert not huerfana.exists() and not viejo_wav.exists()
    [rescatado] = Pendientes(h.carpeta_pendientes).listar()
    assert abs(rescatado.duracion_s - 3.0) < 0.01
    assert rescatado.momento.hour == datetime.fromtimestamp(hace_una_hora).hour


def test_sin_pendiente_y_sin_poder_escribir_la_linea_la_grabacion_va_a_pendientes(
    tmp_path: Path,
) -> None:
    """Se escribió desde memoria: era la única copia y no se puede borrar."""
    import stat

    from voziris.pendientes import Pendientes

    h, _ = _historial(tmp_path, conservar_audio=True)
    h.registrar(_reciente("uno"))
    os.chmod(h.ruta, stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            h.registrar(_reciente("dos"), Audio(muestras=np.full(SAMPLE_RATE * 2, 0.1,
                                                                 np.float32)))
    finally:
        os.chmod(h.ruta, stat.S_IREAD | stat.S_IWRITE)
    assert len(Pendientes(h.carpeta_pendientes).listar()) == 1
    assert list(h.carpeta_audio.iterdir()) == []  # se fue a pendientes, no se borró


def test_las_ilegibles_no_se_duplican_si_falla_la_reescritura(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voziris import historial as modulo

    h, _ = _historial(tmp_path)
    h.registrar(_reciente("uno"))
    with open(h.ruta, "a", encoding="utf-8") as archivo:
        archivo.write('{"roto\n')
    replace = os.replace
    fallos = [1]

    def replace_que_falla_una_vez(origen: object, destino: object) -> None:
        if fallos:
            fallos.pop()
            raise PermissionError("otro programa tiene dictados.jsonl abierto")
        replace(origen, destino)  # type: ignore[arg-type]

    monkeypatch.setattr(modulo.os, "replace", replace_que_falla_una_vez)
    with pytest.raises(PermissionError):
        h.marcar_entregado(1)
    h.marcar_entregado(1)
    apartado = h.ruta.with_name("dictados.ilegibles.jsonl")
    assert apartado.read_text(encoding="utf-8").splitlines() == ['{"roto']
