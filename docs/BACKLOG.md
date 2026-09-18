# Backlog de Voziris v1

25 tareas en 7 hitos. Cada hito deja algo que ya se puede usar.

Las estimaciones son en **jornadas de 8 horas** y salen de comparar con
proyectos equivalentes en GitHub. Son aproximadas: sirven para ordenar, no para
facturar. Total: **30 jornadas**.

`docs/issues.csv` trae lo mismo en formato importable a GitHub Issues.

---

## H0 · Medir antes de decidir — 1 jornada

### VOZ-00 · Ejecutar el banco de pruebas del motor local
**Estimación:** 1 j · **Bloquea:** VOZ-11

El script ya está escrito (`tools/banco_h0.py`). Hay que ejecutarlo y decidir.

**Criterios de aceptación**
- [ ] 10 muestras grabadas de 10–20 s, dictadas en castellano, con contenido real (nombres propios y jerga técnica incluidos).
- [ ] `resultados-h0.md` generado, con RTF por muestra, mediana, tiempo de carga y RAM de los dos motores.
- [ ] El veredicto del script se aplica: si el RTF mediano supera 0,25, se propone cambio de motor antes de tocar VOZ-11.
- [ ] Las transcripciones de los dos motores comparadas a ojo, con una nota sobre cuál acierta más con nombres propios.
- [ ] Resultados enviados al cliente antes de seguir.

---

## H1 · El bucle mínimo — 7 jornadas
Al terminar este hito, **la aplicación ya sustituye a Wispr Flow para dictado simple.**

### VOZ-01 · Carga y validación de la configuración
**Estimación:** 1 j · **Archivo:** `config.py`

**Criterios de aceptación**
- [ ] `Config` tiene un campo por cada opción de `config.ejemplo.toml`, con el mismo nombre.
- [ ] Las rutas relativas se resuelven desde la carpeta del ejecutable, y funciona tanto congelado (`sys.frozen`) como en desarrollo.
- [ ] Si falta `config.toml`, se copia el de ejemplo y la aplicación arranca con los valores por defecto. Un primer arranque no falla.
- [ ] Un TOML inválido lanza `ConfigInvalida` con un mensaje que dice **qué** opción está mal y **qué** valores admite.
- [ ] `guardar()` conserva comentarios y orden (verificado con un test que compara el archivo antes y después de un cambio).
- [ ] Se rechaza: motor fuera de {local, api, auto}, nivel fuera de los tres, atajos repetidos entre sí, carpeta del Markdown inexistente.

### VOZ-02 · Atajos globales
**Estimación:** 2 j · **Archivo:** `atajos.py` · **Bloquea:** VOZ-04

Lo más arriesgado del hito: `RegisterHotKey` no informa de cuándo se suelta la
tecla, así que «mantener» necesita un hook de bajo nivel con su propio bombeo
de mensajes.

**Criterios de aceptación**
- [ ] Las cuatro combinaciones del TOML quedan registradas.
- [ ] «Mantener» dispara al pulsar y al **soltar**, con la duración real de la pulsación.
- [ ] «Clavar», «markdown» y «cancelar» disparan al pulsar.
- [ ] Los callbacks devuelven el control de inmediato: se encola el trabajo, no se hace en el hilo del hook. Verificado con un callback que tarda 2 s — el teclado del sistema no se bloquea.
- [ ] Si una combinación está tomada por otra aplicación, el error dice **cuál**.
- [ ] `liberar()` suelta el hook. Tras cerrar la aplicación, las combinaciones vuelven a estar libres (verificado a mano).
- [ ] Probado con el foco en: Notepad, Chrome, Word, VS Code y una terminal.

### VOZ-03 · Bandeja del sistema y arranque con Windows
**Estimación:** 1 j · **Archivo:** `ui/bandeja.py`

**Criterios de aceptación**
- [ ] Icono con los cuatro estados, distinguibles **también en escala de grises**.
- [ ] Menú completo: Dictar ahora · Últimos dictados · Motor · Ajustes · Acerca de · Salir.
- [ ] «Acerca de» muestra la atribución a NVIDIA. Es obligación de la licencia CC-BY-4.0.
- [ ] El arranque con Windows se activa con un acceso directo en la carpeta Startup del usuario. **Sin tocar HKLM ni tareas programadas** (pediría administrador y rompe F1).
- [ ] Activarlo y desactivarlo funciona en los dos sentidos, verificado reiniciando sesión.
- [ ] «Salir» cierra de verdad: libera atajos, cierra el flujo de audio, y no deja proceso huérfano.

### VOZ-10 · Captura de audio con búfer previo
**Estimación:** 1,5 j · **Archivo:** `audio/captura.py`

**Criterios de aceptación**
- [ ] Flujo a 16 kHz mono float32, abierto al arrancar y cerrado al salir. No se abre en cada dictado.
- [ ] El anillo conserva `buffer_previo_ms` y el audio devuelto **empieza antes de la pulsación** (verificado dictando una palabra inmediatamente al pulsar: aparece completa).
- [ ] El callback de audio solo copia al anillo. Sin logging, sin bloqueos, sin asignaciones grandes.
- [ ] `dispositivos()` lista los micrófonos disponibles con índice y nombre.
- [ ] La ganancia se aplica con recorte, sin saturar.
- [ ] `nivel_actual()` es consultable 30 veces por segundo sin afectar a la captura.
- [ ] Si el micrófono desaparece a media grabación (desconectar unos auriculares USB), no se cuelga: aviso y dictado descartado.

### VOZ-11 · Motor local (Parakeet)
**Estimación:** 1,5 j · **Archivo:** `motores/local.py` · **Depende de:** VOZ-00

**Criterios de aceptación**
- [ ] `precalentar()` carga el modelo y, si falta, lo descarga a la carpeta configurada **con progreso visible**.
- [ ] La descarga interrumpida a media no deja un modelo corrupto que impida el siguiente arranque.
- [ ] `transcribir()` devuelve texto **con puntuación y mayúsculas** dictando en español (es lo que cubre C1: verificar en la práctica, no dar por hecho).
- [ ] `ms_proceso` mide solo la inferencia, para que el RTF sea comparable con el del banco de pruebas.
- [ ] Documentado en el código si `recognize()` acepta `ndarray` o exige archivo. Si exige archivo, el temporal va en la carpeta temporal del sistema y se borra siempre, también si hay excepción.
- [ ] `disponible()` es False antes de precalentar y no depende de la red.
- [ ] Sin red: funciona igual. Verificado desconectando la red.

### VOZ-12 · Inserción en la aplicación activa y portapapeles
**Estimación:** 1,5 j · **Archivo:** `destinos/app_activa.py`

**Criterios de aceptación**
- [ ] Con `metodo = "portapapeles"`, el texto aparece donde está el cursor en: Notepad, Chrome, Word, VS Code y Slack o Teams.
- [ ] **El portapapeles anterior se restaura.** Copiar algo, dictar, y `Ctrl+V` devuelve lo copiado antes, no el dictado.
- [ ] El retardo de restauración está medido, es constante y está comentado con el valor mínimo que resultó fiable.
- [ ] Con `metodo = "tecleo"`, el texto se escribe carácter a carácter, con acentos y eñes correctos.
- [ ] Los caracteres fuera del teclado latino (comillas tipográficas, guion largo, emoji si aparece) se insertan bien o se degradan de forma documentada.
- [ ] `app_en_primer_plano()` devuelve el nombre del ejecutable, y `None` sin reventar cuando no se puede averiguar.
- [ ] Comprobado y **documentado en el README** el comportamiento sobre una ventana elevada (R3).

### VOZ-04 · Orquestación del dictado
**Estimación:** 1 j · **Archivo:** `__main__.py` · **Depende de:** VOZ-01, 02, 03, 10, 11, 12

**Criterios de aceptación**
- [ ] El orden de arranque es el documentado, y el precalentado va en un hilo aparte: la bandeja aparece antes de que el modelo esté cargado.
- [ ] Dictar antes de que termine el precalentado espera con el indicador en «procesando», no falla.
- [ ] `dictar()` corre en un hilo de trabajo, nunca en el del hook de teclado.
- [ ] Un dictado nuevo mientras hay otro en curso se ignora y suena el tono de error. No se solapan.
- [ ] «Cancelar» descarta el dictado en curso sin entregar nada.
- [ ] Una excepción en cualquier etapa deja la aplicación viva y el icono en estado de error. Se registra en un log junto al ejecutable.

---

## H2 · Ergonomía — 4,5 jornadas

### VOZ-20 · Detección de silencio (VAD)
**Estimación:** 1,5 j · **Archivo:** `audio/vad.py`

**Criterios de aceptación**
- [ ] Silero VAD en ONNX, sirviéndose del mismo onnxruntime que ya está. Sin dependencias nuevas.
- [ ] En modo «clavar», el dictado se cierra tras `silencio_corte_ms` de silencio **continuo**.
- [ ] Una pausa de 700 ms a media frase **no** corta el dictado. Verificado dictando con pausas para pensar.
- [ ] En modo «mantener» el VAD no interviene: manda la tecla.
- [ ] `reiniciar()` limpia el estado entre dictados (no se arrastra el silencio del anterior).

### VOZ-21 · Señales acústicas
**Estimación:** 0,5 j · **Archivo:** `audio/sonidos.py`

**Criterios de aceptación**
- [ ] Tres tonos sintetizados, sin archivos de audio distribuidos: inicio ascendente, fin descendente, error grave.
- [ ] Menos de 120 ms cada uno, y no bloquean el dictado.
- [ ] Se distinguen entre sí a volumen bajo.
- [ ] `sonidos = false` los silencia todos.

### VOZ-22 · Indicador flotante con nivel
**Estimación:** 1,5 j · **Archivo:** `ui/hud.py`

**Criterios de aceptación**
- [ ] Ventana sin bordes, siempre encima, y **que no roba el foco nunca** — si lo roba, el texto se pega en el propio indicador. Es el fallo que hay que descartar explícitamente.
- [ ] No aparece en Alt+Tab ni en la barra de tareas.
- [ ] Barra de nivel a ~30 fps mientras graba.
- [ ] Cambia a «procesando» al soltar y desaparece al entregar.
- [ ] Si hubo aviso, se queda ~1,5 s mostrándolo.
- [ ] Con `prefers-reduced-motion` del sistema activado, sin animaciones.

### VOZ-23 · Selección de micrófono y ganancia
**Estimación:** 0,5 j · **Archivos:** `audio/captura.py`, ajustes

**Criterios de aceptación**
- [ ] El micrófono se elige por nombre en el TOML y el cambio se aplica sin reiniciar.
- [ ] Si el dispositivo configurado no existe, se cae al predeterminado con aviso, no se falla.
- [ ] La ganancia se aplica de -20 a +20 dB con recorte.

### VOZ-24 · Modo «clavar»
**Estimación:** 0,5 j · **Archivos:** `atajos.py`, `__main__.py` · **Depende de:** VOZ-20

**Criterios de aceptación**
- [ ] El atajo de «clavar» inicia el dictado y lo cierra al volver a pulsarlo.
- [ ] También lo cierra el VAD.
- [ ] Los dos atajos conviven: usar uno no interfiere con el otro (H2 verificado).
- [ ] El indicador distingue visualmente los dos modos.

---

## H3 · Los dos motores — 3 jornadas

### VOZ-30 · Motor por API
**Estimación:** 1,5 j · **Archivo:** `motores/api.py`

**Criterios de aceptación**
- [ ] `POST {base_url}/audio/transcriptions` con el audio como WAV multipart y `language` fijado.
- [ ] La clave se lee de la configuración y, si está vacía, de `GROQ_API_KEY`. **No hay ninguna clave en el repositorio ni en el binario** (verificar con un grep antes del PR).
- [ ] Timeout, error HTTP y respuesta vacía lanzan `MotorNoDisponible`, nunca propagan la excepción de httpx.
- [ ] La clave **no aparece en los logs**, ni en los mensajes de error, ni truncada.
- [ ] `disponible()` no añade latencia perceptible: la comprobación de red va cacheada unos segundos.
- [ ] Probado contra Groq de verdad, con el coste real de una prueba anotado en el PR.

### VOZ-31 · Selector y respaldo
**Estimación:** 1 j · **Archivo:** `motores/selector.py`

**Criterios de aceptación**
- [ ] Las tres preferencias se comportan como está documentado.
- [ ] Con `motor = "api"` y sin red, **cae al local** y el aviso llega al usuario.
- [ ] El respaldo **nunca va del local a la API**: quien elige local no manda audio fuera por un fallo. Con un test que lo verifique.
- [ ] Cambiar de motor desde el menú de bandeja se aplica al dictado siguiente, sin reiniciar.

### VOZ-32 · Modo offline verificable
**Estimación:** 0,5 j · **Transversal**

**Criterios de aceptación**
- [ ] Con `motor = "local"` y `nivel = "literal"`, la aplicación **no hace ninguna petición de red**. Verificado con un monitor de red durante 10 dictados.
- [ ] No hay telemetría de ningún tipo. Ni anónima, ni opcional.
- [ ] Documentado en el README qué sale del equipo en cada modo.

---

## H4 · Calidad del texto — 4 jornadas

### VOZ-40 · Diccionario personal
**Estimación:** 1,5 j · **Archivo:** `proceso/diccionario.py`

**Criterios de aceptación**
- [ ] «Creatics», «Kairis» y «Voziris» se corrigen bien dictándolos en frases reales.
- [ ] **No** convierte «creaticidad» en «Creatics»: se respetan los límites de palabra.
- [ ] Un umbral restrictivo por defecto, con el valor elegido justificado en un comentario.
- [ ] Test con 20 frases: 10 que deben corregirse y 10 que **no deben tocarse**. Los 10 negativos son la mitad importante.
- [ ] Funciona sin red.

### VOZ-41 · Sustituciones de texto
**Estimación:** 0,5 j · **Archivo:** `proceso/sustituciones.py`

**Criterios de aceptación**
- [ ] «punto y aparte» produce un doble salto de línea.
- [ ] Sin distinguir mayúsculas y respetando límites de palabra.
- [ ] Sin expresiones regulares: son frases literales.
- [ ] Una regla que no encaja no altera el texto ni lanza nada.

### VOZ-42 · Limpieza y formato con LLM
**Estimación:** 2 j · **Archivo:** `proceso/llm.py`

**Criterios de aceptación**
- [ ] Los tres niveles funcionan: «literal» no llama a nada, «limpio» quita muletillas y resuelve autocorrecciones, «reescritura» además aplica formato.
- [ ] **Verificado en castellano**, con al menos 10 dictados reales. No vale probarlo en inglés.
- [ ] «el martes, no, el jueves» sale como «el jueves».
- [ ] **El LLM no añade contenido inventado.** Test con 10 dictados donde se comprueba que no aparece información que no se dijo. Este criterio es el que decide si la función es publicable.
- [ ] Sin red, sin clave o con timeout: devuelve el texto de entrada más un aviso. Nunca pierde el dictado.
- [ ] El timeout es corto y justificado: más vale texto crudo ya que texto pulido dos segundos tarde.
- [ ] El prompt final queda en el código como constante, no construido a trozos.

---

## H5 · Los flujos de Alfon — 3 jornadas

### VOZ-50 · Destino Markdown
**Estimación:** 1 j · **Archivo:** `destinos/archivo_md.py`

**Criterios de aceptación**
- [ ] Tres dictados seguidos producen tres líneas, con sello de tiempo y separador, en el formato configurado.
- [ ] **Funciona con el archivo abierto en Obsidian al mismo tiempo**, y Obsidian ve los cambios. Este es el criterio principal.
- [ ] Se abre en append, se escribe y se cierra en cada dictado.
- [ ] UTF-8 sin BOM, saltos `\n`, y se garantiza el salto final antes de añadir.
- [ ] Si el archivo está bloqueado: un reintento a los 200 ms y, si falla, `EntregaFallida` con el texto a salvo en el historial.
- [ ] Si el archivo no existe, se crea. **Si la carpeta no existe, no se crea**: se avisa de que la ruta está mal.
- [ ] El texto multilínea se aplana para no romper la lista de Markdown.

### VOZ-51 · Auto-Enter
**Estimación:** 0,5 j · **Archivo:** `destinos/app_activa.py`

**Criterios de aceptación**
- [ ] Desactivado por defecto, en el TOML y en el panel de ajustes.
- [ ] Activado, manda Enter tras insertar.
- [ ] En los ajustes lleva una advertencia visible de lo que implica: un Enter en el chat equivocado no se deshace.

### VOZ-52 · Historial con reintento
**Estimación:** 1,5 j · **Archivo:** `historial.py`

**Criterios de aceptación**
- [ ] JSON Lines junto al ejecutable, recortado a `entradas`.
- [ ] Un archivo corrupto o a medio escribir no impide arrancar: se ignoran las líneas ilegibles.
- [ ] Los últimos 10 dictados aparecen en el menú de bandeja, con las primeras palabras de cada uno.
- [ ] Reintentar reentrega **el texto ya procesado**, sin volver a transcribir ni a llamar al LLM.
- [ ] Con `guardar_audio = true`, el WAV queda al lado. Por defecto, no se guarda nada de audio.
- [ ] Los dictados con información sensible se pueden borrar desde el menú.

---

## H6 · Publicable — 5 jornadas

### VOZ-60 · Panel de ajustes
**Estimación:** 2,5 j · **Archivo:** `ui/ajustes.py`

**Criterios de aceptación**
- [ ] Las seis pestañas, en el orden documentado.
- [ ] Los atajos se configuran **pulsando la combinación**, no escribiéndola.
- [ ] La pestaña de audio trae medidor de nivel en vivo, para probar el micrófono ahí mismo.
- [ ] El botón «probar» del motor de API dice si la clave vale, sin revelarla.
- [ ] Guardar reescribe el TOML conservando comentarios.
- [ ] Lo que se aplica en caliente, se aplica; lo que exige reiniciar, lo dice.
- [ ] «Acerca de» incluye versión, licencia MIT y la atribución a NVIDIA.
- [ ] Se recorre con el teclado, con foco visible.

### VOZ-61 · Empaquetado portable
**Estimación:** 1,5 j · **Archivo:** `build/voziris.spec`

**Criterios de aceptación**
- [ ] `pyinstaller build/voziris.spec` produce una carpeta que funciona en un Windows limpio, **sin Python instalado**.
- [ ] Verificado en una máquina virtual recién hecha, no solo en el equipo de desarrollo.
- [ ] Arranca en menos de 3 segundos desde el doble clic.
- [ ] Copiada a un USB y ejecutada desde otro equipo, mantiene su configuración.
- [ ] **No pide permisos de administrador** en ningún momento.
- [ ] El ZIP pesa menos de 120 MB (sin el modelo).
- [ ] `ATRIBUCIONES.md` viaja dentro del paquete.

### VOZ-62 · Documentación y primera release
**Estimación:** 1 j

**Criterios de aceptación**
- [ ] README con capturas reales de la aplicación funcionando.
- [ ] Las dos limitaciones de Windows (elevación y antivirus) documentadas donde el usuario las va a buscar.
- [ ] Instrucciones de primer arranque, incluida la descarga del modelo y lo que tarda.
- [ ] Cómo conseguir una clave de Groq y qué cuesta de verdad.
- [ ] Release en GitHub con el ZIP y **el hash SHA-256**, por lo de los antivirus.
- [ ] La atribución a NVIDIA presente en el repositorio, en el binario y en «Acerca de». Las tres.

## H7 · Grabaciones a Markdown — 3 jornadas

Pedido después de la primera entrega: «que Voziris convierta grabaciones
(m4a) a un Markdown, y que autodetecte los interlocutores».

### VOZ-70 · Transcribir un archivo de audio
**Estimación:** 1 j · **Archivos:** `archivos.py`, `grabaciones.py`

**Criterios de aceptación**
- [x] Abre lo que abre ffmpeg (m4a, mp3, wav, ogg, opus, flac, mp4…) sin instalar nada: PyAV va dentro del paquete.
- [x] Trocea en ventanas de ≤ 30 s cortadas en el punto más silencioso; funciona con el motor local y con la API (25 MB por petición).
- [x] Cada trozo se normaliza por su cuenta: la voz de enfrente en una llamada no se pierde.
- [x] `reunion.m4a` → `reunion.md` al lado, con cabecera (fecha, duración, motor) y marcas de tiempo; no pisa un `.md` que ya exista.

### VOZ-71 · Quién habla cuándo
**Estimación:** 1,5 j · **Archivo:** `hablantes.py`

**Criterios de aceptación**
- [x] Modelos pequeños (pyannote segmentation 3.0 + TitaNet small, 44 MB) descargados a `modelos/hablantes/` la primera vez; en CPU con sherpa-onnx.
- [x] Con el número de hablantes conocido, los grupos residuales (< 3 % del habla) no cuentan como persona.
- [x] Automático por agrupación espectral con eigengap (NME-SC): el enlace medio fundía a una persona que habla 25 min con otra que habla 8. Validado con una llamada de teléfono y una reunión presencial, las dos de dos personas.
- [x] «Hablante 1, 2, 3…» por orden de aparición; intervenciones del mismo hablante separadas por menos de 1 s se funden.
- [ ] Validar con una reunión de tres o más personas (pendiente de grabación).

### VOZ-72 · Tres formas de pedirlo
**Estimación:** 0,5 j · **Archivos:** `__main__.py`, `ui/transcripcion.py`, `instalador.py`

**Criterios de aceptación**
- [x] `voziris.exe --transcribir x.m4a --hablantes auto|1|N [--salida y.md]`, sin mutex: convive con la Voziris de la bandeja.
- [x] Bandeja → «Transcribir una grabación…»: selector de archivo y pregunta de hablantes; corre en otro proceso con ventana de progreso y botón Cancelar.
- [x] Botón derecho en el Explorador → «Transcribir con Voziris» → «Una sola voz» / «Varios hablantes». Con la instalación; en portable, `--menu-contextual`. Solo HKCU.
- [x] Al terminar se abre el `.md`; los errores salen en un cuadro y en `voziris.log`.

## H8 · Que lo dicho no se pierda — 1 jornada

Pedido tras dos «se ha quedado pillado» (la API sin contestar 15 s): «mantener
la grabación de lo que se ha hecho mientras se estaba escuchando, por si luego
no se transcribe», y poder copiar desde «Últimos dictados».

### VOZ-74 · Audio a salvo, API con paciencia, historial que se puede copiar
**Archivos:** `pendientes.py`, `audio/captura.py`, `orquestador.py`, `motores/selector.py`, `ui/bandeja.py`

**Criterios de aceptación**
- [x] Cada dictado se escribe a `historial/pendientes/*.f32` según llega del micrófono (búfer previo incluido), volcando cada bloque: un cierre de golpe pierde 32 ms.
- [x] El archivo se borra solo cuando el texto está entregado o en el historial; si el motor falla o hay una excepción, se queda y se avisa.
- [x] Bandeja → «Dictados sin transcribir (N)» → «Transcribir y copiar» / «Borrar el audio». Aviso al arrancar si hay alguno.
- [x] Limpieza sola: 7 días, 20 archivos, y los de menos de 1 s.
- [x] La API espera 3 s + 0,1 s por segundo de audio; pasado eso, local con aviso. La respuesta tardía se tira.
- [x] Cancelar en «procesando» guarda el texto en el historial sin entregarlo.
- [x] «Últimos dictados»: «Copiar al portapapeles» y reentrega a los 3 s para poder elegir la ventana.

## H9 · Dictar sin ruido de fondo — 0,5 jornadas

Pedido por el cliente: «que se baje el volumen a mute de todo lo que hay y que
se proceda con el dictado».

### VOZ-75 · Bajar el audio de las demás aplicaciones al dictar
**Archivos:** `audio/mezclador.py`, `orquestador.py`, `config.py`, `ui/bandeja.py`, `ui/ajustes.py`

**Criterios de aceptación**
- [x] Tres modos en `[audio].al_dictar`: `nada`, `atenuar` (15 %) y `silenciar` (por defecto).
- [x] Se actúa por aplicación (sesiones de audio de Windows), nunca sobre el volumen maestro, y nunca sobre la propia Voziris: sus tonos se siguen oyendo.
- [x] Solo se toca lo que está sonando (sesiones activas), y nunca una aplicación que esté grabando por el micrófono: una llamada de Teams o Zoom se queda como está.
- [x] Se baja al empezar a grabar y se sube al dejar de grabar, sin esperar a que se entregue el texto. Cubre también cancelar, la pulsación demasiado corta y el micrófono que desaparece.
- [x] Un cuarto de segundo de margen antes de tocar nada: una pulsación descartada por corta no abre un agujero en la música.
- [x] Mientras se graba se revisa cada 1,5 s: lo que empiece a sonar también se calla.
- [x] Lo que ya estaba silenciado se queda como estaba; si el usuario mueve el volumen mientras está bajado, no se le pisa. En modo «atenuar» no se toca el mute de nadie.
- [x] Archivo de rescate atómico y acumulativo: si el proceso muere con el audio bajado, al arrancar se devuelve. Si no se puede escribir, no se baja nada. Si queda algo sin devolver, el rescate se conserva y la bandeja ofrece «Devolver el sonido».
- [x] Nada de esto corre en el hilo del atajo ni fuera del hilo del mezclador, que es el único que habla con COM: el tono de inicio no se retrasa y no se sueltan punteros desde otro hilo.
- [x] Se cambia desde la bandeja («Mientras dicto») y desde Ajustes → Audio.

## H10 · Vídeos — 0,5 jornadas

Pedido por el cliente: «añadir transcripción como en las grabaciones pero con
vídeo. Vídeos en mp4».

### VOZ-77 · Transcribir vídeos, y elegir bien la pista de audio
**Archivos:** `archivos.py`, `grabaciones.py`, `__main__.py`, `ui/transcripcion.py`, `ui/bandeja.py`

**Criterios de aceptación**
- [x] Un vídeo entra igual que una grabación: se le saca la pista de sonido y sigue el mismo camino. El .mp4 ya funcionaba; lo que faltaba era la lista y las pistas.
- [x] Formatos de vídeo ofrecidos: mp4, mkv, mov, avi, webm, m4v, wmv, mpg, mpeg, mts, m2ts, 3gp. Comprobado que la rueda de PyAV del proyecto los abre todos.
- [x] Fuera a propósito: las imágenes (ffmpeg las abre como vídeo de un fotograma y meterían Voziris en el botón derecho de todas las fotos) y .ts, que para quien programa es TypeScript.
- [x] Con varias pistas se transcribe UNA, nunca la suma: se descartan comentario y audiodescripción, manda el idioma configurado si hay doblaje, luego la marcada como principal, luego la primera.
- [x] El Markdown avisa de cuántas pistas había y de cuál se usó, y dice cómo repetirlo con otra.
- [x] `--pista auto|N|todas`. `todas` suma y avisa de que si las voces se solapan el texto saldrá entremezclado.
- [x] Un vídeo sin sonido lo dice claro, no falla con un error técnico.
- [x] Vídeos largos, medido con el más largo del equipo del cliente (3 h 18 min):
  - el audio se reserva de una vez (727 MB en vez de 1455);
  - `normalizar` y el cálculo de energía trabajan por trozos, con salida idéntica bit a bit y el doble de rápidos (1,7 GB menos con 4 h);
  - la separación de hablantes pide unas 11 veces el audio de golpe (medido: 4,9 GB con 2 h), así que se comprueba contra la memoria libre y, si no cabe, se transcribe sin separar en lugar de morir a media hora de trabajo;
  - la ventana de progreso dice cuánto dura y cuánto va a tardar.
