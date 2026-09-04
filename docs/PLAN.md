# Plan de desarrollo de Voziris v1

**Fecha:** 4 de septiembre de 2026
**Base:** `docs/ENCARGO.md`, `docs/ARQUITECTURA.md`, `docs/BACKLOG.md` y el esqueleto de `src/voziris`.
**Estado:** análisis terminado, plan listo para aprobar. No se ha tocado código.

Este documento no repite el encargo: lo completa. Fija **cómo se comporta** la
aplicación en cada situación (máquina de estados, hilos, fallos), **cómo se
construye** cada módulo con las bibliotecas verificadas, **cómo se prueba** y
**en qué orden** se hace. Al final van las decisiones que necesito del cliente.

---

## 1. Veredicto del análisis

El encargo es implementable tal como está. Las ocho decisiones cerradas
(Python, Parakeet vía onnx-asr, Groq, idioma fijo, onedir, TOML junto al
ejecutable, bandeja + Tkinter, MIT + CC-BY) se sostienen tras verificar las
bibliotecas reales. No propongo cambiar ninguna.

Sí hay **cuatro contradicciones** entre documentos y esqueleto que hay que
resolver antes de escribir código, y **once huecos** del esqueleto que amplío
con una propuesta concreta. Van en la sección 2. Para cada una doy la
resolución por defecto que aplicaré si no se dice lo contrario.

### Lo verificado contra las bibliotecas reales

| Suposición del encargo | Resultado | Fuente |
|---|---|---|
| `onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")` | Correcto. Firma real: `load_model(model, path=None, *, quantization=None, sess_options=None, providers=None, ...)` | referencia de onnx-asr 0.12 |
| ¿`recognize()` acepta `ndarray`? (duda abierta en VOZ-11) | **Sí**: `recognize(waveform: NDArray[float32], *, sample_rate=16_000)`. No hace falta WAV temporal | ídem |
| Silero VAD «sin dependencias nuevas» | **Sí**: `onnx_asr.load_vad("silero", path=...)` lo descarga y lo sirve con el mismo onnxruntime | ídem |
| Número de hilos configurable | Sí, vía `sess_options` de onnxruntime (`intra_op_num_threads`) | ídem |
| Python 3.11–3.13 | onnx-asr soporta 3.10–3.14. En el equipo hay 3.13 (Store) y 3.14; `pyproject` exige `<3.14`, así que **3.13** | `py -0` |
| Groq: endpoint y precio | `POST /openai/v1/audio/transcriptions`, `whisper-large-v3-turbo` a 0,04 $/h, parámetros `model`, `language`, `response_format`, `prompt`, `temperature`. Límite de archivo 25 MB en el nivel gratuito (un dictado de 60 s en WAV son 1,9 MB) | docs de Groq |
| LLM en Groq para C2/C3 | Candidatos en producción: `llama-3.3-70b-versatile`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `llama-3.1-8b-instant`. Ninguno verificado aún en castellano: es la tarea de VOZ-42 | docs de Groq |
| pystray + Tkinter | pystray exige hilo principal salvo en Windows, donde «calling `run()` from a thread other than the main thread is safe». Tkinter exige hilo principal. Luego: **Tk en el principal, pystray en un hilo** | docs de pystray |
| `ctrl+win` como atajo «mantener» | **`RegisterHotKey` no puede registrarlo**: exige una tecla virtual además de los modificadores. Confirma que el hook de bajo nivel es obligatorio, y además obliga a enmascarar la tecla Win (ver 2.1) | API Win32 |

---

## 2. Contradicciones y huecos, con resolución propuesta

### 2.1 Contradicciones (hay que decidir)

**C-1 · El primer arranque no puede pasar la validación.** VOZ-01 exige dos
cosas incompatibles: «si falta `config.toml`, se copia el de ejemplo y la
aplicación arranca» y «se rechaza carpeta del Markdown inexistente». El
ejemplo trae `ruta = "C:/Users/CAMBIAME/vault/entrada.md"`, así que el primer
arranque fallaría siempre.
*Propuesta:* la ruta del Markdown se valida **al usarla**, no al arrancar. Al
arrancar, si la carpeta no existe, se registra un aviso y el atajo `markdown`
queda activo pero responde con tono de error y notificación «ruta no
configurada». `ConfigInvalida` por esta causa solo la lanza `guardar()` desde
el panel de ajustes. Todo lo demás de VOZ-01 sigue igual.

**C-2 · `mantener = "ctrl+win"` y la tecla Win.** Al soltar Win sin haber
pulsado otra tecla, Windows abre el menú Inicio. Con `ctrl+win` como atajo,
cada dictado abriría Inicio al soltar. Es un problema conocido: PowerToys y
AutoHotkey lo resuelven inyectando una pulsación de una tecla virtual «vacía»
(VK 0xFF) antes de que Win se suelte.
*Propuesta:* el hook inyecta VK 0xFF al detectar el fin de una combinación
que incluya Win. Se documenta en `atajos.py`. Si el cliente prefiere evitar el
truco, basta con cambiar el atajo por defecto a uno sin Win (por ejemplo
`ctrl+alt`); es una línea del TOML.

**C-3 · `cancelar = "esc"` es una tecla de uso universal.** Un hook global
que capture Esc siempre rompería todos los diálogos del sistema.
*Propuesta:* Esc solo se intercepta (y se consume) mientras hay un dictado en
curso, en los estados GRABANDO y PROCESANDO. En REPOSO el hook la deja pasar
sin tocarla. Lo mismo vale para cualquier combinación que se configure como
`cancelar`.

**C-4 · `arranque_con_windows = true` por defecto.** Con el ejemplo tal cual,
el primer arranque de una copia «portable» crea un acceso directo en Startup
sin preguntar. Es coherente con el uso del cliente, pero sorprende si se prueba
desde un USB en otro equipo.
*Propuesta:* mantener `true` (es lo decidido) pero crear el acceso directo solo
cuando el valor **cambia** respecto al estado real de la carpeta Startup, y
mostrar una notificación la primera vez. Alternativa: `false` en el ejemplo y
que se active desde ajustes. Decisión del cliente.

### 2.2 Huecos del esqueleto (los amplío, no cambian los contratos)

Los tres protocolos (`MotorSTT`, `PostProceso`, `Destino`) y los tests de
contrato quedan intactos. Lo que cambia son constructores y algún método de
las clases concretas.

| # | Hueco | Propuesta |
|---|---|---|
| H-1 | `Historial.reintentar()` no tiene acceso a los destinos | El constructor recibe `destinos: dict[str, Destino]` y `contexto_actual: Callable[[], Contexto]` |
| H-2 | `Selector` no permite cambiar la preferencia en caliente (lo exige VOZ-31) | Propiedad `preferencia` con setter protegido por lock |
| H-3 | `Bandeja.__init__` solo recibe `al_salir`; el menú necesita más callbacks | Constructor con `acciones: AccionesBandeja` (dataclass de callables: dictar_ahora, abrir_ajustes, cambiar_motor, reintentar, borrar_entrada, salir) y `ultimas: Callable[[], list[EntradaHistorial]]` |
| H-4 | `Hud` no tiene constructor ni raíz Tk | Recibe el `tk.Tk` raíz; todos sus métodos son seguros desde cualquier hilo (encolan con `after`) |
| H-5 | Nadie calcula `Contexto.hay_red` | Módulo nuevo `red.py`: `hay_red()` con caché de 10 s, comprobación por socket TCP al host de `base_url`, sin peticiones HTTP |
| H-6 | El LLM no tiene `base_url` ni clave propias en el TOML | Reutiliza `[motor.api].base_url` y `clave`; solo `proceso.llm_modelo` es propio. Timeout fijo de 6 s |
| H-7 | `guardar_audio = true` exige el audio, pero `EntradaHistorial` no lo lleva | `Historial.registrar(entrada, audio: Audio | None = None)` |
| H-8 | `dictar(modo: str, destino: str)` usa cadenas donde hay `Modo` | `dictar(modo: Modo, destino: str)`; `destino` es `"app_activa" \| "markdown"` (tipo `Literal`) |
| H-9 | `build/voziris.spec` referencia `assets/voziris.ico`, que no existe | VOZ-03 genera los cuatro iconos de estado con Pillow y exporta el `.ico`; `assets/` entra en el repositorio |
| H-10 | Dos instancias de Voziris (Startup + doble clic) competirían por el micrófono y los atajos | Mutex Win32 con nombre (`CreateMutex`) en `main()`; la segunda instancia avisa y sale. **Tarea nueva VOZ-05**, 0,25 j |
| H-11 | `tools/banco_h0.py` carga Parakeet sin `quantization="int8"` | Con la firma real, `quantization=None` descarga el modelo fp32 (~2,4 GB) y mide otra cosa. Se corrige antes de ejecutar H0 |

### 2.3 Precisiones de comportamiento que el encargo deja abiertas

Las fijo aquí para no decidirlas a mitad de commit:

- **«Dictar ahora» en el menú de bandeja** inicia un dictado en modo `clavar`
  hacia `app_activa` (no hay tecla que mantener).
- **Pulsación accidental de `mantener`**: si dura menos de 250 ms, se
  descarta sin transcribir ni sonar el tono de fin. Constante comentada en
  `__main__.py`.
- **Posición del HUD**: centrado en el borde inferior del monitor donde está
  la ventana en primer plano. «Cerca del cursor» del docstring se cambia por
  esto: el cursor del ratón no tiene relación con dónde se dicta.
- **Portapapeles**: se conserva y restaura solo el formato texto Unicode. Si
  el usuario tenía una imagen o archivos copiados, se pierden. Documentado en
  el README como limitación de la v1.
- **`Transcripcion.motor`**: `"local"` o `"api:<proveedor>"` donde proveedor
  es la etiqueta de segundo nivel del host (`api.groq.com` → `groq`).
- **El nombre del atajo determina modo y destino**: `mantener` →
  (MANTENER, app_activa) · `clavar` → (CLAVAR, app_activa) · `markdown` →
  (MANTENER, markdown). No existe «clavar hacia markdown» en la v1.

---

## 3. Cómo funciona: comportamiento definido

### 3.1 Máquina de estados del dictado

Un único objeto `Orquestador` (en `__main__.py`) posee el estado. Toda
transición pasa por su lock. Los demás módulos no conocen el estado.

```
             mantener↓ · clavar↓ · markdown↓ · «Dictar ahora»
  REPOSO ───────────────────────────────────────────────────► GRABANDO
    ▲                                                            │
    │ cancelar                                                   │ mantener↑ (modo MANTENER)
    │ micrófono perdido                                          │ clavar↓  (modo CLAVAR)
    │ pulsación < 250 ms                                         │ corte por VAD (modo CLAVAR)
    │                                                            ▼
    │◄──── entrega ok ─────────────────────────────────── PROCESANDO
    │◄──── cancelar (antes de entregar) ─────────────────────────┤
    │                                                            │ fallo en cualquier etapa
    │◄──── siguiente evento ──────────────── ERROR ◄─────────────┘
```

| Estado | Icono | HUD | Qué acepta |
|---|---|---|---|
| REPOSO | contorno | oculto | empezar (cualquiera de los tres atajos o el menú) |
| GRABANDO | relleno | barra de nivel a 30 fps, etiqueta del modo | terminar (según modo), cancelar, pérdida de micrófono |
| PROCESANDO | relleno con marca | «procesando…» | cancelar (descarta si aún no se entregó) |
| ERROR | contorno con marca | aviso 1,5 s | cualquier evento lo devuelve a REPOSO |

Reglas de concurrencia:

- Un `empezar` fuera de REPOSO se **ignora** y suena el tono de error. Nunca
  se solapan dos dictados.
- `mantener↑` solo termina si el dictado en curso es de modo MANTENER;
  `clavar↓` solo si es CLAVAR. Cruzados, se ignoran con tono de error.
- `cancelar` en PROCESANDO marca el dictado como cancelado; el hilo de trabajo
  comprueba la marca **justo antes de entregar**. Si ya se entregó, no hace
  nada. El texto cancelado no entra en el historial.
- Un dictado antes de terminar el precalentado entra en PROCESANDO y el hilo
  de trabajo espera al `Event` de motor listo (con el HUD en «procesando»).

### 3.2 Secuencia de un dictado, con tiempos

Para 15 s de audio, motor local, nivel `limpio`:

| Paso | Quién | Dónde corre | Tiempo objetivo |
|---|---|---|---|
| 1. Pulsación detectada | hook LL | hilo teclado | < 1 ms (solo encola) |
| 2. Marca de inicio en el anillo | `Captura.empezar_dictado()` | hilo teclado | < 1 ms (guarda índice) |
| 3. Tono de inicio, HUD, icono | `Sonidos`, `Hud`, `Bandeja` | hilos propios / Tk | no bloquea |
| 4. Acumulación | callback PortAudio | hilo audio | continuo |
| 5. Soltar / segunda pulsación / VAD | hook LL o hilo VAD | | < 1 ms |
| 6. `terminar_dictado()` → `Audio` | `Captura` | hilo trabajo | < 5 ms |
| 7. `Selector.transcribir()` | motor | hilo trabajo | RTF × 15 s (H0 lo mide) |
| 8. Diccionario, sustituciones | `proceso.*` | hilo trabajo | < 5 ms |
| 9. LLM (si nivel ≠ literal y hay red) | `LimpiezaLLM` | hilo trabajo | típico 0,5–1,5 s; **corte a 6 s** |
| 10. Entrega | `Destino` | hilo trabajo | ~150 ms (pegado + retardo de restauración) |
| 11. Historial | `Historial` | hilo trabajo | < 5 ms |
| 12. HUD oculto, icono a reposo | Tk | hilo principal | |

Objetivo total desde soltar hasta texto insertado: **< 1,5 s en local sin LLM**,
**< 3 s con LLM**. Si H0 da un RTF que no lo permite, se aplica el criterio
del banco de pruebas.

### 3.3 Arranque y cierre

Arranque, en este orden (el del docstring de `__main__`, con dos añadidos):

1. Mutex de instancia única (H-10). Si ya hay otra, notificación y salida 0.
2. Log rotativo `voziris.log` junto al ejecutable (3 × 1 MB).
3. `config.cargar()`. Único fallo fatal: mensaje en un cuadro de diálogo Tk
   (no hay consola) y salida 2.
4. `Captura.abrir()`. Si el micrófono configurado no existe, cae al
   predeterminado con aviso. Si no hay ninguno, arranca igual con icono de
   error y aviso: la aplicación no muere por un micrófono.
5. Raíz Tk oculta, `Hud`, cola de interfaz.
6. Hilo de precalentado: `Selector.precalentar()`. Descarga con progreso en
   una notificación de bandeja cada 10 % y en el HUD.
7. Hilo de teclado: `Atajos.registrar()`. Si una combinación no se puede
   registrar, aviso con **cuál** y la aplicación sigue con las demás (no es
   fatal: se puede corregir desde ajustes).
8. Hilo de pystray: `Bandeja.mostrar()`.
9. `tk.mainloop()` en el hilo principal.

Cierre («Salir» o excepción no controlada en `main`): se ejecuta siempre en
este orden, en un `finally`: cancelar dictado en curso → `Atajos.liberar()` →
`Captura.cerrar()` → detener pystray → destruir Tk → liberar mutex. Verificado
con el criterio de VOZ-03: sin proceso huérfano y atajos libres.

### 3.4 Qué pasa cuando algo falla

| Fallo | Comportamiento | Estado final |
|---|---|---|
| Micrófono desaparece grabando | `Captura` marca error; el orquestador descarta el dictado, tono de error, notificación | REPOSO |
| Modelo local no cargado aún | espera en PROCESANDO hasta `Event` listo (sin límite: el usuario ve «procesando») | sigue |
| Modelo local no se pudo cargar (descarga rota, disco) | `MotorLocal.disponible()` = False; con `motor=local`, `MotorNoDisponible` → ERROR con aviso; con `auto`/`api`, se usa la API | según motor |
| API sin red, timeout, HTTP ≠ 200, respuesta vacía | `MotorNoDisponible` → el selector cae al local y añade aviso «Sin conexión: transcrito en local» | REPOSO |
| Local falla y la preferencia es `local` | **no** se manda audio a la API. ERROR + aviso | ERROR |
| Transcripción vacía (silencio) | `TranscripcionFallida` → aviso «no se oyó nada», sin entrega ni historial | REPOSO |
| Diccionario o sustituciones lanzan | capturado dentro de `aplicar()`; devuelve entrada + aviso | sigue |
| LLM: sin red, sin clave, timeout, respuesta vacía o sospechosa | devuelve el texto de entrada + aviso «entregado sin limpiar» | sigue |
| Pegado falla (`EntregaFallida`) | historial con `entregado=False`, tono de error, notificación «reintentar desde el menú» | ERROR |
| Markdown bloqueado | un reintento a 200 ms; luego `EntregaFallida` como arriba | ERROR |
| Excepción no prevista en el hilo de trabajo | capturada en el bucle del hilo, log con traza, ERROR + aviso genérico. El hilo sigue vivo | ERROR |
| Excepción en el hilo de teclado | log; se intenta reinstalar el hook una vez; si vuelve a fallar, aviso «atajos caídos, reinicia» | sigue |

Principio: **la única excepción que llega a `main()` es `ConfigInvalida`**.
Cada hilo tiene su propio `try/except` de última instancia que registra y
continúa.

---

## 4. Ingeniería de software

### 4.1 Modelo de hilos

| Hilo | Crea | Vive | Hace | Nunca hace |
|---|---|---|---|---|
| **principal** | intérprete | toda la ejecución | Tk (`Hud`, `Ajustes`), procesa la cola de interfaz con `after(16)` | trabajo de red, inferencia, E/S de disco |
| **teclado** | `Atajos.registrar()` | hasta `liberar()` | `SetWindowsHookEx(WH_KEYBOARD_LL)` + bucle `GetMessage`; sondeo de conflictos con `RegisterHotKey` | nada que dure > 1 ms; solo llama a `Orquestador.evento()` |
| **audio** | PortAudio | hasta `Captura.cerrar()` | callback: copia al anillo, RMS, empuja bloques a la cola del VAD si toca | logging, asignaciones grandes, locks largos |
| **trabajo** | `Orquestador` | toda la ejecución | consume `queue.Queue[Trabajo]`: transcribir, post-procesar, entregar, historial | tocar Tk directamente |
| **vad** | `Orquestador` | toda la ejecución, dormido si no hay dictado CLAVAR | consume bloques de 512 muestras y decide el corte | |
| **precalentado** | `main()` | hasta terminar la carga | `Selector.precalentar()`; señala un `threading.Event` | |
| **bandeja** | `Bandeja.mostrar()` | hasta `icon.stop()` | bucle de mensajes de pystray; callbacks del menú | trabajo largo: reenvía al orquestador |
| **sonido** | `Sonidos` | efímero por tono | `sounddevice.play()` de un array sintetizado | |

Comunicación:

- Hacia el hilo de trabajo: `queue.Queue` de objetos `Trabajo` (dataclass:
  `procesar`, `reintentar(indice)`, `cancelar`).
- Hacia Tk: `queue.Queue` de callables que el hilo principal vacía en
  `after(16)`. `Hud.*` y `Bandeja.estado()` encolan en vez de tocar widgets.
  pystray permite cambiar `icon.icon` desde otro hilo; se hace igualmente vía
  su propio hilo para no depender de ese detalle.
- Estado compartido del orquestador: un `threading.Lock` y un `Enum` de estado.
  Las transiciones son O(1) y ocurren dentro del lock; el trabajo pesado,
  fuera.
- Cancelación: `threading.Event` por dictado, comprobado antes de entregar.

### 4.2 Diseño por módulo

**`config.py`** — dataclasses anidadas que reflejan el TOML sección a sección:
`General`, `Atajos`, `AudioCfg`, `MotorLocalCfg`, `MotorApiCfg`, `Proceso`,
`DestinoAppActiva`, `DestinoMarkdown`, `HistorialCfg`, y `Config` que las
agrupa. Carga con `tomlkit`, valores por defecto tomados del propio
`config.ejemplo.toml` (así una clave nueva en una versión futura no rompe un
TOML viejo). Validación en una función por sección que acumula errores y
lanza un solo `ConfigInvalida` con todos, cada uno con «opción, valor recibido,
valores admitidos». `guardar()` reabre el documento tomlkit original, sustituye
solo los valores cambiados y escribe: así se conservan comentarios y orden.
Rutas relativas resueltas con `carpeta_base()`. Análisis sintáctico de atajos
en `config`, no en `atajos`: es validación.

**`atajos.py`** — **un solo mecanismo para las cuatro combinaciones**: hook
`WH_KEYBOARD_LL` con `ctypes` (no pywin32, que no expone el hook). El hilo
mantiene el conjunto de teclas físicamente pulsadas y evalúa cada combinación
como «todas sus teclas están pulsadas y ninguna otra tecla modificadora
extra». Transiciones: una combinación pasa a activa → `al_empezar`; deja de
estar completa → `al_terminar` con la duración. `RegisterHotKey` se usa solo
como **sondeo** al arrancar para `clavar` y `markdown` (registrar, y si falla
con `ERROR_HOTKEY_ALREADY_REGISTERED`, avisar de cuál; desregistrar
inmediatamente después). Las combinaciones activas se consumen (el hook
devuelve 1) para que Ctrl+Alt+M no llegue a la aplicación de destino; Esc solo
se consume en curso (C-3). Máscara VK 0xFF para Win (C-2). `liberar()` envía
`WM_QUIT` con `PostThreadMessage` y hace `join` con timeout de 2 s.

**`audio/captura.py`** — `sounddevice.InputStream(samplerate=16000,
channels=1, dtype="float32", blocksize=512, callback=...)`. 512 muestras = 32
ms, que es exactamente el bloque que espera Silero VAD, así el mismo bloque
sirve para ambos. Anillo: `np.ndarray` preasignado de `buffer_previo_ms` +
margen, con índice de escritura; en `empezar_dictado()` se guarda el índice y
a partir de ahí los bloques van también a una lista de acumulación. `nivel_actual()`
devuelve el último RMS calculado en el callback (un float atómico en CPython).
Ganancia: `np.clip(muestras * 10**(db/20), -1, 1)` al terminar, no en el
callback. Pérdida de dispositivo: el `status` del callback y una excepción de
PortAudio marcan `self._error`; `terminar_dictado()` lanza y el orquestador
descarta. Cambio de micrófono en caliente: `cerrar()` + `abrir()` fuera de un
dictado.

**`audio/vad.py`** — `onnx_asr.load_vad("silero", path=carpeta_modelos)`.
Primera opción: usar el objeto `Vad` de onnx-asr si expone inferencia por
bloque con estado. Segunda opción, si solo sirve para segmentar audio
completo: sesión onnxruntime directa sobre el `silero_vad.onnx` que ya
descargó, con entradas `input[1,512]`, `state[2,1,128]`, `sr` y salida de
probabilidad. Umbral de voz 0,5; el corte solo tras `silencio_corte_ms`
**continuos** de probabilidad < 0,35 (histéresis). `reiniciar()` pone el
estado a cero.

**`audio/sonidos.py`** — tres arrays sintetizados una vez en el constructor:
inicio (barrido 440→660 Hz, 90 ms), fin (660→440 Hz, 90 ms), error (220 Hz con
segundo armónico, 110 ms), con envolvente para que no chasquen.
`sounddevice.play()` en hilo aparte. Con `activos=False`, no hacen nada.

**`motores/local.py`** — `onnx_asr.load_model(modelo, path=carpeta,
quantization="int8", sess_options=opts)` con `opts.intra_op_num_threads =
hilos` si `hilos > 0`. `transcribir()` llama a `recognize(audio.muestras,
sample_rate=16000)` y mide solo esa llamada. Descarga: onnx-asr usa
`huggingface_hub`, que descarga a `.incomplete` y renombra al terminar, lo que
cubre el criterio de «descarga interrumpida no deja modelo corrupto»; se
verifica matando el proceso a medias. Progreso: `huggingface_hub` admite una
clase `tqdm` propia; se pasa una que reporta a un callback. Si onnx-asr no
deja inyectarla, se sondea el tamaño de la carpeta contra el tamaño conocido.
`disponible()` es `self._modelo is not None`.

**`motores/api.py`** — `httpx.Client(base_url, timeout, headers={"Authorization":
"Bearer ..."})` creado en `precalentar()`. `transcribir()` serializa el audio a
WAV PCM 16 bits en memoria (`io.BytesIO` + `wave`), lo manda como `files`
multipart con `model`, `language`, `response_format="json"`,
`temperature=0`. Cualquier `httpx.HTTPError`, código ≠ 200 o JSON sin `text`
→ `MotorNoDisponible` con un mensaje que **nunca** incluye cabeceras ni la
clave. `disponible()` = hay clave **y** `red.hay_red()`. La clave se resuelve
en `config` (TOML o `GROQ_API_KEY`), nunca aquí.

**`motores/selector.py`** — las tres reglas del docstring en un `match`.
Respaldo únicamente api→local. `preferencia` cambiable en caliente (H-2).
`precalentar()` lanza un hilo por motor y expone `listo: threading.Event` que
se activa cuando el motor de la preferencia actual está disponible o falló.

**`red.py`** — `hay_red(host, puerto=443, ttl_s=10)`: `socket.create_connection`
con timeout 1,5 s, resultado cacheado `ttl_s`. Sin peticiones HTTP, sin DNS
externo cuando la caché está caliente. Es la única comprobación de red fuera
de los propios motores.

**`proceso/diccionario.py`** — sin dependencias nuevas: `difflib.SequenceMatcher`
sobre tokens normalizados (minúsculas, sin acentos). Un token candidato solo
se compara si tiene longitud ≥ 4 y difiere de la palabra del diccionario en
como mucho 2 caracteres de longitud. Se sustituye solo si `ratio ≥ 0,84` **y**
el token no es una palabra más larga que contenga la del diccionario (evita
«creaticidad» → «Creatics»). Umbral inicial 0,84, a ajustar con el test de 20
frases. Se conserva la capitalización del diccionario. La expresión regular
interna es `\w+` con `re.UNICODE`; el usuario no escribe regex.

**`proceso/sustituciones.py`** — para cada regla, un patrón compilado desde
`re.escape(clave)` con límites de palabra y `re.IGNORECASE`. «Sin expresiones
regulares» se refiere a la configuración, no a la implementación. Orden de
aplicación: reglas más largas primero, para que «punto y aparte» gane a
«punto».

**`proceso/llm.py`** — `POST {base_url}/chat/completions` con `temperature=0`,
`max_tokens` = 2 × tokens estimados de entrada, timeout 6 s. `PROMPT_LIMPIO`
existe; `PROMPT_REESCRITURA` se escribe en VOZ-42. Defensa contra invención:
si la salida supera en más del 40 % la longitud de la entrada, o contiene
más de N palabras «nuevas» de ≥ 5 letras que no estaban en la entrada
(N = 3, a calibrar), se descarta y se devuelve la entrada con aviso. Es la
comprobación que hace publicable la función según el backlog. Modelo por
defecto propuesto para probar primero: `llama-3.3-70b-versatile`; segundo,
`openai/gpt-oss-20b` por velocidad. La elección se cierra con los 10 dictados
en castellano.

**`destinos/app_activa.py`** — método portapapeles: `OpenClipboard` con
reintento (otra aplicación puede tenerlo abierto), leer `CF_UNICODETEXT`,
`SetClipboardText`, cerrar; liberar modificadores físicos que sigan pulsados
(inyectar key-up de Ctrl/Shift/Alt/Win si `GetAsyncKeyState` los ve
pulsados; si no, el Ctrl+V sintético llega como Ctrl+Win+V); `SendInput` de
Ctrl+V; esperar `RETARDO_RESTAURACION_MS` (constante, valor a medir, punto de
partida 150 ms); restaurar. Método tecleo: `SendInput` con `KEYEVENTF_UNICODE`
por carácter, con pares subrogados para emoji, y `\n` como tecla Enter. Auto-
Enter: un `SendInput` de VK_RETURN tras la inserción. `app_en_primer_plano()`:
`GetForegroundWindow` → `GetWindowThreadProcessId` → `OpenProcess` +
`QueryFullProcessImageNameW`; `None` ante cualquier error (procesos elevados
lo darán).

**`destinos/archivo_md.py`** — tal cual el docstring. Aplanado: `" ".join(
texto.split())` salvo que `formato` contenga `\n`. Garantía de salto final:
abrir en `r+b`, leer el último byte, y si no es `\n`, escribirlo antes de la
entrada. Formato con marcador desconocido: `str.format_map` con un dict que
devuelve `{clave}` literal para lo desconocido, más aviso.

**`historial.py`** — `historial/dictados.jsonl` bajo `carpeta_base()`. Una
línea por entrada con los campos de `EntradaHistorial` más `indice` (entero
creciente) y `audio` (nombre del WAV si se guardó). Escritura: append + flush.
Recorte: cuando el archivo supera `2 × entradas` líneas, se reescribe a
`entradas` vía archivo temporal + `os.replace` (atómico). Lectura tolerante:
línea que no parsea → se salta y se registra una vez. Reintento (H-1):
reentrega al destino guardado con un `Contexto` nuevo. Borrado: reescritura
sin esa línea, y borrado del WAV si existe.

**`ui/bandeja.py`** — pystray en su hilo. Cuatro imágenes de 64 px generadas
con Pillow en `ui/iconos.py` (contorno, relleno, relleno con marca, contorno
con marca) y el `.ico` de `assets/` exportado desde ellas. Menú con callables
para «Últimos dictados» (submenú dinámico) y estado del motor (radio).
`notify()` para avisos. Startup: acceso directo `.lnk` en
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` creado con
`IShellLink` vía `pythoncom` (pywin32), apuntando al ejecutable con directorio
de trabajo en su carpeta. En desarrollo apunta a `pythonw.exe -m voziris`.

**`ui/hud.py`** — `tk.Toplevel` con `overrideredirect(True)`,
`attributes("-topmost", True)`, y tras crearse, `SetWindowLongW(hwnd,
GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)` para que no active ni
aparezca en Alt+Tab. Se muestra con `ShowWindow(SW_SHOWNOACTIVATE)`, nunca
con `deiconify()` a secas (activa). Barra de nivel en un `Canvas` refrescado
desde `after(33)`. `prefers-reduced-motion` se lee de
`SystemParametersInfo(SPI_GETCLIENTAREAANIMATION)`.

**`ui/ajustes.py`** — `ttk.Notebook` con las seis pestañas. Captura de atajos:
un `Entry` de solo lectura que, con foco, escucha `<KeyPress>`/`<KeyRelease>`
y muestra la combinación en la sintaxis del TOML. Medidor de nivel: reutiliza
`Captura.nivel_actual()`. «Probar clave»: `GET {base_url}/models` con la
clave, en un hilo, mostrando solo «válida / no válida / sin red». Al guardar:
`config.guardar()`, y luego aplica en caliente lo que se pueda (atajos,
micrófono, ganancia, sonidos, nivel, diccionario, sustituciones, destino
Markdown, motor preferido); lo demás (modelo local, hilos, `base_url`) muestra
«requiere reiniciar».

### 4.3 Registro y privacidad

- `voziris.log` con `RotatingFileHandler`, nivel INFO en producción, DEBUG con
  `--debug`. Un filtro de logging sustituye cualquier aparición de la clave de
  API por `***`. Nunca se registra texto dictado en INFO; en DEBUG se registra
  la longitud, no el contenido.
- Qué sale del equipo, por modo (irá al README, VOZ-32):
  - `motor=local`, `nivel=literal`: **nada**. Ni comprobación de red.
  - `motor=local`, `nivel≠literal`: el texto transcrito, al LLM.
  - `motor=api` o `auto` con red: el audio, a la API de transcripción; y el
    texto, al LLM si `nivel≠literal`.
  - Descarga del modelo: una vez, a Hugging Face.
- Sin telemetría, sin comprobación de versiones, sin ping de arranque.

### 4.4 Empaquetado

Cambios sobre `build/voziris.spec` en VOZ-61: añadir `hiddenimports` para
`huggingface_hub`, `pythoncom`, `win32com.shell`, `tkinter`, `PIL._tkinter_finder`;
incluir `assets/` en `datas`; verificar que onnxruntime arrastra sus DLLs
(`onnxruntime/capi/*.dll`). Prueba en máquina virtual limpia como exige el
backlog. Entorno de construcción: **siempre desde un venv** (PyInstaller con
el Python de la Store fuera de un venv da problemas conocidos); si el
ejecutable resultante falla en la VM por rutas de la Store, se pasa a un Python
de python.org. Esto se decide en VOZ-61, no antes.

---

## 5. Estrategia de pruebas

### 5.1 Pirámide

| Nivel | Qué cubre | Cómo corre | Marcador pytest |
|---|---|---|---|
| Contrato | firmas y atributos de los tres protocolos | siempre | ninguno |
| Unitario puro | `tipos`, `config` (con TOML en `tmp_path`), `sustituciones`, `diccionario` (20 frases), `archivo_md` (archivo en `tmp_path`, simulación de bloqueo con `msvcrt.locking`), `historial` (corrupción, recorte, reintento con destino falso), `selector` (motores falsos: verifica que **nunca** cae local→api), `api` y `llm` (`httpx.MockTransport`: timeout, 500, JSON vacío, clave no aparece en el mensaje), `red` (socket parcheado), orquestador (máquina de estados con captura, motores y destinos falsos) | siempre, también en CI | ninguno |
| Con modelo | `local` y `vad` con el modelo real sobre WAVs de `tests/audio/` | si `modelos/` existe | `modelo` |
| Windows | `atajos` (hook real + `SendInput` sobre una ventana Tk propia), `app_activa` (pegado en un `tk.Text` propio y restauración del portapapeles), `hud` (comprobar con `GetForegroundWindow` que no roba el foco), `bandeja.configurar_arranque` (carpeta Startup redirigida a `tmp_path`) | solo en Windows con sesión interactiva | `win` |
| Hardware | `captura` con micrófono real | si hay dispositivo de entrada | `audio` |
| Red real | `api` y `llm` contra Groq | si existe `GROQ_API_KEY`; coste anotado en el PR | `red` |
| Manual | los criterios del backlog que dicen «probado a mano»: cinco aplicaciones de destino, ventana elevada, Obsidian abierto, USB, VM limpia | lista de comprobación por issue en el PR | — |

`pytest` por defecto ejecuta todo lo que puede en la máquina actual; CI
ejecuta `-m "not audio and not red and not modelo"` en `windows-latest`.

### 5.2 Dobles de prueba

En `tests/dobles.py`: `MotorFalso(texto, falla=False, tarda_s=0)`,
`DestinoFalso(falla=False)` que guarda lo entregado, `CapturaFalsa(audio)`,
`RedFalsa(hay=True)`. Cumplen los protocolos, así que también pasan por
`test_contratos.py`.

### 5.3 Herramienta de desarrollo: modo consola

Tarea añadida a VOZ-04: `python -m voziris --archivo muestra.wav [--destino
markdown] [--nivel literal]` ejecuta el pipeline completo sobre un WAV, sin
atajos ni bandeja, e imprime la transcripción, los avisos y los tiempos por
etapa. Sirve para probar motores, post-proceso y destinos sin tocar el
teclado, y para reproducir fallos del historial. Es la misma función
`dictar()`, alimentada de otra forma.

### 5.4 Calidad estática

`ruff check` (reglas ya fijadas), `mypy --strict` sobre `src/voziris`. Para
`ctypes` se declaran las firmas (`argtypes`/`restype`) en un módulo
`win32api_ll.py` con tipos explícitos, en vez de esparcir `# type: ignore`.
Los tests también pasan por ruff; mypy sobre tests queda opcional.

---

## 6. Convenciones de trabajo

- `git init` en `voziris/` antes de la primera línea de código (hoy no hay
  repositorio y el encargo pide una rama por issue). `main` protegida solo por
  costumbre: todo entra por rama `voz-NN-descripcion` y PR.
- Commits `VOZ-NN: verbo en presente`. Un PR por issue con la lista de
  criterios marcada y capturas de la prueba manual.
- Identificadores y docstrings en castellano, como el esqueleto. Nombres de
  API de terceros, en su idioma.
- Toda constante «a medir» (retardo de restauración, umbral del diccionario,
  timeout del LLM, pulsación mínima) va en mayúsculas al principio de su
  módulo con un comentario que diga cómo se midió.
- Un módulo nuevo (`red.py`, `ui/iconos.py`, `win32api_ll.py`,
  `tests/dobles.py`) se registra en `docs/ARQUITECTURA.md` en el mismo PR.
- Definición de hecho: la del encargo, sin recortes.

---

## 7. Orden de ejecución

Respeta los hitos del backlog. Dentro de cada hito reordeno para que lo
verificable con tests vaya antes que lo que solo se prueba a mano, y para que
cada issue pueda probarse aislada con el modo consola.

### Sesión 0 · Preparación (antes de H0, sin código de la aplicación)

1. `git init`, primer commit con el esqueleto tal cual.
2. `py -3.13 -m venv .venv` e `pip install -e ".[dev,banco,vad]"`.
3. Corregir H-11 en `tools/banco_h0.py` (`quantization="int8"`) y añadir
   `path=` para que el modelo caiga en `./modelos/` y sirva luego a VOZ-11.
4. Comprobar que `pytest`, `ruff` y `mypy` pasan sobre el esqueleto (mypy
   estricto sobre stubs con `NotImplementedError` debería pasar; si no, se
   arregla aquí).
5. **El cliente graba las 10 muestras** (`banco_h0.py grabar`). Esto no lo
   puedo hacer yo: necesita su voz y su micrófono.
6. `banco_h0.py medir` y `resultados-h0.md` al cliente. Veredicto.

### H1 · El bucle mínimo (7,25 j)

Orden: **VOZ-01** config → **VOZ-10** captura → **VOZ-11** motor local →
**VOZ-12** inserción → **VOZ-05** instancia única (nueva) → **VOZ-02** atajos
→ **VOZ-03** bandeja e iconos → **VOZ-04** orquestación + modo consola.

Razón del orden: 01 lo leen todos; 10, 11 y 12 se prueban sueltos con el modo
consola antes de que exista un atajo; 02 y 03 son los de más prueba manual y
van cuando ya hay pipeline que disparar. Al cerrar 04, la aplicación dicta a
la app activa con el atajo `mantener`.

### H2 · Ergonomía (4,5 j)

**VOZ-21** sonidos → **VOZ-23** micrófono y ganancia → **VOZ-20** VAD →
**VOZ-24** modo clavar → **VOZ-22** HUD. El HUD al final porque es el que más
depende de ver todo lo demás funcionando.

### H3 · Los dos motores (3 j)

**VOZ-30** API → **VOZ-31** selector → **VOZ-32** verificación offline.
Requiere que el cliente tenga `GROQ_API_KEY` en su entorno; yo no la necesito
ver.

### H4 · Calidad del texto (4 j)

**VOZ-41** sustituciones → **VOZ-40** diccionario → **VOZ-42** LLM. Los dos
primeros son puros y con tests cerrados; el LLM va último porque exige los 10
dictados reales en castellano del cliente para cerrar prompt y modelo.

### H5 · Los flujos propios (3 j)

**VOZ-50** Markdown → **VOZ-52** historial → **VOZ-51** auto-Enter.

### H6 · Publicable (5 j)

**VOZ-60** ajustes → **VOZ-61** empaquetado → **VOZ-62** documentación y
release. VOZ-61 necesita una VM limpia del cliente para la verificación.

Total: 30,25 jornadas con la tarea nueva. Las estimaciones del backlog se
mantienen; no las reviso hasta tener H1 hecho, que es donde se ve si estaban
bien calibradas.

### Qué necesita al cliente y cuándo

| Momento | Qué | Por qué no puedo hacerlo yo |
|---|---|---|
| Sesión 0 | Grabar 10 muestras | su voz, su micrófono |
| Sesión 0 | Decidir C-1 a C-4 | son decisiones de producto |
| VOZ-02, 12, 22 | Prueba manual en Notepad, Chrome, Word, VS Code, terminal, Slack/Teams; ventana elevada | requiere sesión interactiva y esas aplicaciones |
| VOZ-03 | Reiniciar sesión para verificar Startup | ídem |
| H3 | `GROQ_API_KEY` en el entorno | la clave es suya y no debe pasar por el chat |
| VOZ-42 | 10 dictados reales en castellano para calibrar el LLM | su forma de hablar |
| VOZ-50 | Obsidian abierto sobre `entrada.md` | su vault |
| VOZ-61 | Máquina virtual limpia y USB | hardware |

---

## 8. Decisiones que necesito antes de programar

1. **C-1** — validar la ruta del Markdown al usarla, no al arrancar. *(Propuesta: sí.)*
2. **C-2** — máscara VK 0xFF para `ctrl+win`, o cambiar el atajo por defecto. *(Propuesta: máscara, y se prueba en VOZ-02; si molesta, `ctrl+alt`.)*
3. **C-3** — Esc solo se captura con dictado en curso. *(Propuesta: sí.)*
4. **C-4** — `arranque_con_windows = true` en el ejemplo, con notificación la primera vez. *(Propuesta: sí.)*
5. **H-10** — añadir VOZ-05 instancia única (0,25 j). *(Propuesta: sí.)*
6. **Hook único** para las cuatro combinaciones, con `RegisterHotKey` solo como sondeo. *(Propuesta: sí; es consecuencia de que `ctrl+win` no se pueda registrar.)*
7. **Modo consola** como parte de VOZ-04. *(Propuesta: sí; es media jornada y ahorra más en pruebas.)*

Si estas siete se aprueban como están, la sesión 0 empieza con `git init`.
