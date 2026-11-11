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
- [ ] Con `guardar_audio = true`, el WAV queda al lado. Por defecto, no se guarda nada de audio. *(Superado por VOZ-80: `conservar_audio`, activado, guarda la grabación de los últimos dictados para volver a transcribirlos.)*
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

### VOZ-80 · Ningún dictado se pierde, todos se ven, y se pueden volver a transcribir
**Archivos:** `historial.py`, `orquestador.py`, `ui/bandeja.py`, `__main__.py`, `config.py`

Lo que se vio en el equipo del cliente el 23/09: 87 dictados guardados en `dictados.jsonl`, ninguno visible en «Últimos dictados»; ninguno con su grabación; y el texto se guardaba después de pegar.

**Criterios de aceptación**
- [x] «Últimos dictados» enseña el dictado de hace un momento. pystray en Windows construía el menú una vez y lo reutilizaba; tras un dictado nadie pedía rehacerlo. Ahora se rehace al abrirlo, en el hilo de la bandeja. Probado sobre el HMENU nativo, no sobre el menú de Python (que es donde miraba el test anterior, y por eso no lo vio).
- [x] El texto entra en el historial ANTES de pegar, sin entregar, y se marca al pegar bien. Si el pegado se cuelga o el proceso muere a media entrega, lo dicho ya está a salvo.
- [x] La grabación de los últimos dictados se conserva (`historial/audio/<indice>.f32`) moviendo el archivo que se escribía mientras se hablaba: sin copiar nada. Poda sola: 20 dictados, 7 días, 1 GB como mucho; la más reciente nunca.
- [x] «Volver a transcribir la grabación» en cada dictado que la tenga. El texto nuevo sustituye al viejo y se deja copiado; si no sale nada, el de antes se queda.
- [x] «Copiar el último dictado» a un clic, para cuando se pegó con el foco fuera del campo de texto.
- [x] Opción nueva `conservar_audio` (activada). La antigua `guardar_audio` no la apaga: la plantilla la escribía a false en todas las instalaciones.
- [x] Cada fase del dictado en el log (grabando, grabación cerrada, transcribiendo, entregando), sin lo dicho.
- [x] Vigía: si un dictado pasa de 60 s más su duración procesándose, se vuelca dónde está cada hilo en `voziris-fallos.log` y se avisa. No corta nada.
- [x] Cada arranque deja su fecha en `voziris-fallos.log`: los volcados de faulthandler no la llevan.
- [x] Historial con cerrojo: el hilo de trabajo escribe mientras la bandeja lee y el usuario borra o copia.

**Tras la revisión adversarial** (cuatro lentes, un escéptico por hallazgo que debía reproducirlo):
- [x] «Recuperar» ya no borra un dictado largo si el motor vuelve a no sacar texto: el archivo se queda y se dice.
- [x] Si la línea del historial no se puede escribir, la grabación vuelve a «Dictados sin transcribir» en vez de quedarse huérfana (regresión de la primera versión).
- [x] Una línea cortada a medias (corte de luz) no se come el dictado siguiente; las ilegibles se apartan a `dictados.ilegibles.jsonl` al reescribir, en vez de perderse.
- [x] Volver a transcribir conserva el texto de antes y dice sus avisos; no escribe encima de otro dictado que haya heredado el número.
- [x] El menú se construye antes de destruir el viejo, y un segundo clic derecho con el menú abierto no lo destruye. Lee el historial una vez por apertura.
- [x] El dictado que se está transcribiendo no sale como «sin transcribir».
- [x] Pegar y copiar comparten un cerrojo del portapapeles.
- [x] Un dictado largo sin texto pone el icono en error y avisa en la bandeja; Esc en una grabación larga la guarda.
- [x] Poda después de pegar; con `conservar_audio = false` se borra lo ya guardado.
- [x] Al guardar ajustes, un `guardar_audio = false` de la plantilla vieja se quita, y `conservar_audio` entra con su comentario.
- [x] pystray fijada a 0.19.x: el enganche toca sus internos.

**Tras la verificación de la segunda ronda** (los arreglos aguantan con el icono real, su bucle de mensajes real y la captura real; esto es lo que no):
- [x] Copiar desde la bandeja espera como mucho 0,5 s a un pegado: sin límite, un pegado colgado congelaba la bandeja entera, «Salir» incluido (regresión de la segunda ronda).
- [x] Una grabación que ninguna entrada nombra (la línea no llegó a disco) se rescata a «Dictados sin transcribir» en vez de borrarse; y si la línea no se escribe y el audio venía de memoria, va también ahí, nunca a la papelera.
- [x] El historial se lee con BOM, y una línea JSON que no es una entrada no tumba la lectura.
- [x] Volver a transcribir guarda el texto original, no solo el anterior: dos intentos malos no se llevan el bueno.
- [x] «Copiar el último dictado» copia el que dice la etiqueta aunque entre otro con el menú abierto.
- [x] El rehacer seguro vale también cuando pystray rehace el menú tras pulsar un elemento.
- [x] Recuperar poda la grabación; el mismo audio no se recupera dos veces a la vez.
- [x] Cada proceso (también «Transcribir» del Explorador) deja su marca en voziris-fallos.log, y el diagnóstico busca volcados en todo el archivo y solo enseña las marcas que tienen uno detrás.

### VOZ-83 · Ctrl solo arrancaba el dictado
**Archivos:** `atajos.py`, `winapi.py`

Lo vio el cliente en sus dos equipos: a veces, al pulsar solo Ctrl, se activaba el dictado. Sin voz, Groq lo transcribía como «Gracias por ver el vídeo», y eso se pegaba (lo arregla VOZ-84). El 30/10, en su equipo, hubo 7 dictados de 0,3 a 1 s en cinco minutos. El primero llegó 41 s después de arrancar Voziris, y pararon al reiniciarla.

**Causa.** El `Detector` solo sabía lo que le contaba el hook. Si el hook ve bajar Win y no lo ve subir, Win se queda apretada para él. Desde ese momento, cada Ctrl completa `ctrl+win`, sea solo o en Ctrl+C o Ctrl+V, hasta que se vuelve a pulsar Win.
- Windows espera al hook unos 312 ms por tecla (`LowLevelHooksTimeout`). Si el callback está esperando el GIL, le llega tarde la primera tecla y se pierden las siguientes.
- Al arrancar, la carga del modelo local retiene el GIL 0,7 s y 1,5 s seguidos, con el hook ya instalado. Medido en el equipo del cliente.
- También se pierden subidas en el escritorio seguro (Win+L) y sobre ventanas elevadas.

**Criterios de aceptación**
- [x] Antes de cada pulsación nueva, lo que el hook cree apretado se contrasta con `GetAsyncKeyState`, y se olvida lo que ya no lo está. Ctrl solo, Ctrl+C y Ctrl+V ya no arrancan el dictado aunque se haya perdido la subida de Win.
- [x] El síntoma contrario también está arreglado: una tecla atascada (la «l» de Win+L, la Ñ) ya no deja `ctrl+win` sin responder.
- [x] La autorrepetición se fía del hook, sin preguntar a Windows.
  - Si otra aplicación se traga la tecla después del hook (Samsung Multi Control o TeamViewer manejando otro equipo), Windows la da por suelta. Preguntarle olvidaba la tecla en cada repetición, unas 30 veces por segundo.
  - Volver a pulsar una tecla atascada pasa por autorrepetición, pero el atajo funciona: para entonces Windows ya la ve apretada.
- [x] Un dictado mantenido cuyas teclas ya no están apretadas se cierra con la siguiente pulsación.
- [x] Encadenar dictados sin soltar Ctrl sigue funcionando:
  - el Ctrl que Voziris suelta al pegar no se olvida;
  - el Ctrl del Ctrl+V del propio Voziris no hace pasar por apretado un Ctrl atascado.
- [x] Mantener apretado «clavar» no lo repite, y su tecla no se olvida al pulsar otra. Windows no ve la tecla que consume Voziris: de esa solo sabe el hook.
- [x] El registro dice con qué tecla se completa cada atajo y si esa pulsación la inyectó otra aplicación.
- [x] El registro avisa de cada tecla que el hook no vio soltarse, con el tiempo que la creyó apretada.
  - Como mucho, un aviso por minuto. El siguiente dice cuántos se callaron.
  - Si una aplicación se traga el teclado, cada pulsación nueva olvida las teclas que siguen apretadas, y sin límite se llenaría el registro.
- [x] Probado de punta a punta con el hook real y el GIL retenido 1,5 s:
  - en `main`, la subida de Shift se pierde y Ctrl solo dispara «ctrl+shift»;
  - con el arreglo, Ctrl solo no hace nada y el registro avisa de la tecla olvidada.

### VOZ-84 · Sin voz no se escribe nada
**Archivos:** `audio/vad.py`, `orquestador.py`

Lo que vio el cliente el 30/10: si pulsaba Ctrl+Win y no decía nada, Groq escribía siempre «Gracias por ver el video». Eran 8 de los 73 dictados del historial, de 0,8 a 1,5 s. Su micrófono entrega silencio digital (−97 dBFS) cuando nadie habla, y Whisper se inventa esa frase con cualquier audio sin voz. Medido contra Groq: pasa sin normalizar, con ceros, con la ganancia limitada a +30 dB y sin el vocabulario («Gracias.»); el modelo turbo escribe «¡Suscríbete al canal!». `no_speech_prob` no sirve para filtrarlo: da 0 en esa última y 0,95 en una palabra real.

**Criterios de aceptación**
- [x] Al cerrar cualquier dictado en vivo, en cualquier modo y destino, Silero (el VAD que ya se cargaba para «clavar») mira si alguien habló. Si nadie habló, no se llama a ningún motor ni se pega nada, y el indicador dice «Nada que escribir: no se oyó voz». *(Ese aviso no llegó a verse hasta VOZ-86.)*
- [x] Con 5 s o más sin voz pasa lo mismo que cuando el motor no saca texto: icono de error, y el audio se queda en «Dictados sin transcribir», por si el VAD se equivocó.
- [x] Recuperar y volver a transcribir no preguntan al VAD: son peticiones explícitas.
- [x] Sin el modelo del VAD, antes de que cargue o si falla, se transcribe como antes.
- [x] Con voz no cambia nada: ni la normalización, ni el audio que se manda, ni la petición.
- [x] Sin opciones nuevas: el `config.toml` de las versiones anteriores vale tal cual.
- [x] Medido sobre el audio normalizado:
  - el silencio del cliente no pasa de 0,05, y ningún ruido de prueba llega a 0,35 (ceros, ruido de sala, zumbido, teclas, ventilador). El umbral es 0,5;
  - pasan los 21 dictados reales con voz, también bajados 40 dB;
  - pasan el 97–99 % de las palabras sueltas de 350 a 800 ms. Las que no ve, trozos de menos de 250 ms, Groq tampoco las acertó.
- [x] Cuesta unos milisegundos por dictado: 0,12 ms por cada 32 ms de audio, y para en cuanto oye voz. Ahorra una llamada a Groq por cada pulsación vacía.

### VOZ-85 · Lo invisible que bloqueaba instalar y actualizar
**Archivos:** `winapi.py`, `instalador.py`, `__main__.py`, `build/voziris.spec`, `tools/empaquetar.py`

Dos cosas que no se veían. El 31/10, la instalación falló dos veces en el equipo de Alfonso por un duplicado sin ventana, abierto por otro programa y parado en el aviso «Voziris ya está abierto». El aviso de error decía «Espera un minuto», y con un proceso invisible esperar no arregla nada. El 2/11, «Actualizar» contestaba «La actualización ya está en marcha» durante minutos, porque el instalador retenía su mutex hasta que se pulsara Aceptar en un aviso abierto detrás de otras ventanas.

**Causa de lo primero.** Mientras viva un `voziris.exe` de la carpeta, `_internal\base_library.zip` queda abierto, y NTFS no deja renombrar una carpeta con un archivo abierto debajo. Una DLL cargada no cuenta.
- `cffi` menciona `setuptools` en lo que usa para compilar, y por eso PyInstaller lo mete en el paquete, con su hook `pyi_rth_setuptools`.
- El hook lo importa en cada arranque, y `setuptools` pregunta su versión a `importlib.metadata`.
- `importlib.metadata` abre el zip y lo guarda en una caché (`FastPath`, con `lru_cache`), así que no se cierra hasta que el proceso sale.
- Medido con la 0.1.2: desde unos 200 ms después de arrancar. Los «50 ms» que constaban eran un bloqueo pasajero.
- La bandeja y `--transcribir` lo seguirán reteniendo sin `setuptools`, porque `onnx_asr` hace la misma llamada al importarse. Emulado en el venv; sin medir en el .exe.

**Criterios de aceptación**
- [x] `setuptools` no va en el paquete. `tools/empaquetar.py` se niega a empaquetar si vuelve a aparecer. `--version` baja de unos 280 ms a unos 175 ms.
- [x] El aviso de un duplicado se cierra solo a los 10 s y el proceso termina, aunque nadie vea la ventana. Es `MessageBoxTimeoutW`, que no está documentada: si faltara, no sale ventana y queda en el registro.
- [x] Antes de cambiar el programa, el instalador mira qué procesos siguen vivos con su ejecutable en esa carpeta, sin esperar a que algo falle:
  - si uno está transcribiendo, o sigue vivo y no se puede leer su orden, pregunta antes de cerrar nada, también la bandeja, con el PID, la ruta y la grabación. El botón por defecto es «No», y con «No» no se cambia nada;
  - lo demás lo cierra sin preguntar y lo anota en el registro: `WM_CLOSE` a sus ventanas y, si a los 5 s sigue vivo, `TerminateProcess`. `Local\Voziris.Salir` solo lo atiende la instancia que tiene el mutex;
  - después de cerrar la bandeja y antes de cerrar nada más, vuelve a sacar la lista y a leer la orden de todos: lo que empezó a transcribir con la pregunta abierta o mientras se cerraba la bandeja también se pregunta, todo junto, y lo ya aceptado no. Con «No» no se ha terminado nada. Lo que arranque después de sacar esa lista no se cierra.
- [x] Cancelar en la ventana de progreso cuenta también con una pregunta abierta, porque los cuadros no la bloquean. Si se pulsó, al contestar no se cierra nada más ni se cambia el programa, sea cual sea la respuesta. Lo mismo si se pulsa mientras se cierra la bandeja u otro proceso: lo que queda por cerrar sigue vivo.
- [x] Si aun así no se puede cambiar el programa, busca quién lo retiene (Restart Manager para los archivos, `NtQueryInformationFile` para quien tiene una carpeta como directorio de trabajo) y lo dice con **Reintentar** y **Cancelar**, sin volver a copiar. Lo que ya se había movido vuelve a `Voziris.nuevo`, y la instalación anterior sigue en su sitio mientras se pregunta.
- [x] Ningún aviso dice «Espera un minuto». Dice qué programa es, con su PID y su ruta, y qué hacer; si no se sabe, qué suele ser. A una ventana del Explorador no se le propone «Finalizar tarea», porque se llevaría por delante el escritorio.
- [x] Si no se instala, por el motivo que sea, y se había cerrado la bandeja, se vuelve a abrir. También al cancelar.
- [x] `MUTEX_INSTALAR` y `MUTEX_ACTUALIZAR` se sueltan antes de cualquier aviso final: bien, cancelado o con error.
- [x] Por eso, la bandeja da por en marcha un `--actualizar` vivo y sin mutex solo durante sus primeros 10 s. Si con el antivirus tarda más y llega otro clic, de los dos solo sigue el que tome el mutex.
- [x] `_mensaje` y `_error_fatal` son cuadros nativos con `MB_TOPMOST | MB_SETFOREGROUND`. Windows puede negarles el foco. *(«Siempre encima» no se sostuvo en la prueba del 3/11: ver abajo.)*
- [x] Probado con el `.exe` compilado, en un escritorio oculto y con carpetas y claves de prueba:
  - un duplicado de la 0.1.2 parado en su aviso impide renombrar `_internal`. `_instalar_cli` lo cierra con `WM_CLOSE` y cambia el programa sin reintentos;
  - un duplicado compilado con esto sale a los 10 s con código 0, y mientras vive deja renombrar `_internal`;
  - con una consola con el directorio de trabajo dentro, el cuadro dice `cmd.exe`, su PID y su ruta, y «Reintentar» funciona en cuanto se cierra;
  - con «Cancelar», sigue la versión anterior, sin restos, y se pide reabrir la bandeja;
  - mientras se pregunta, el mutex sigue tomado; en el aviso final, ya está suelto.
- [x] Probado con la ventana de progreso y los cuadros de verdad, en un escritorio oculto y con un paquete falso:
  - pulsar Cancelar con una pregunta abierta y contestar «Sí» o «Reintentar» no cierra ni cambia nada;
  - pulsar Cancelar mientras se cierra la bandeja deja viva la transcripción ya aceptada;
  - una transcripción que empieza mientras se cierra la bandeja se pregunta: con «No» sigue, también la que ya se había aceptado, y con «Sí» se cierra e instala.

**Con una bandeja real, el 3/11 (0.1.3 con el número 0.0.1, en el equipo de Alfonso; `docs/DESPLIEGUE.md` §6.2 bis):**
- `--instalar` cerró la 0.1.2 y abrió la nueva, sin reintentos, con el mutex suelto en el aviso final.
- Con ese aviso abierto, «Actualizar» arrancó a la primera.

**Sin aclarar:** el aviso final no se quedó con `WS_EX_TOPMOST`. A los 18 s de salir tenía el estilo `0x00010101`, sin nadie que se lo pidiera. El mismo cuadro sí lo tiene en un escritorio oculto, también desde el `.exe` y tras la ventana de progreso, y 3 s en la pantalla de verdad, aunque pierda el primer plano. No se sabe si lo pierde o no llega a tenerlo, ni por qué. Esa prueba solo lo midió a partir de los 18 s. No bloquea nada, porque el mutex ya no depende de ese aviso.

**Sin probar con una bandeja real:** cerrar un duplicado (se cerró antes de llegar el instalador), y Cancelar.

**Fuera de esto:** `--desinstalar` tiene el mismo punto ciego: si algo retiene la carpeta, no la borra entera y no lo dice.

### VOZ-86 · Los avisos de un dictado se ven
**Archivos:** `ui/hud.py`

Lo encontró la verificación de las notas de la 0.1.2. El orquestador avisa y pasa a reposo al momento: las dos órdenes llegan al indicador en la misma tanda de la cola de la interfaz, y la segunda quitaba el aviso antes de que se pintara. Al pulsar sin hablar no se escribía nada, pero tampoco se decía nada en pantalla. VOZ-84 prometía ese aviso, y sus tests solo miraban que se mandara.

**Criterios de aceptación**
- [x] Un aviso que llega al acabar un dictado se queda ~1,5 s a la vista: pasar a reposo ya no lo quita. Vale para «Nada que escribir: …» y para los avisos de la transcripción («…: transcrito en local», «Sin red: …»), que estaban igual.
- [x] Un dictado nuevo quita el aviso al momento, y al acabar se oculta sin esperar a ningún temporizador de antes.
- [x] Sin aviso, el indicador se oculta al momento, como siempre.
- [x] Sin opciones nuevas ni cambios fuera del indicador.
- [x] Test sin ventanas con el orquestador de verdad y la cola vaciada de una vez: falla con el código de antes.
- [x] Comprobado con la ventana de verdad: Windows dice que está a la vista ~1,5 s, y en la captura de la pantalla se lee el aviso.

**Fuera de esto, y sigue igual**
- Si llegan dos avisos seguidos, solo se ve el último.
- Un aviso de más de unos 45 caracteres se corta por el ancho del indicador (268 px útiles): «La API falló (…): transcrito en local» se ve cortado. Los que ya se veían, igual.

### VOZ-87 · Lo de serie no avisa en cada dictado
**Archivos:** `motores/selector.py`, `motores/api.py`, `orquestador.py`

Lo encontró la verificación de las notas de la 0.1.3. VOZ-86 hizo visibles los avisos de un dictado, y entre ellos había dos que salían en todos:
- con lo de serie (motor automático, sin clave), «Sin clave de API: transcrito en local», cuando el LÉEME dice que la API no hace falta para nada;
- con motor local, nivel literal y un modelo de IA puesto, «Sin red: «llm» omitido», aunque hubiera red: con ese motor y ese nivel no se mira.

**Criterios de aceptación**
- [x] En automático y sin clave se transcribe en local sin avisar.
- [x] Con el motor en API y sin clave, el aviso sigue: se ha pedido algo que no se cumple.
- [x] En automático con clave y sin conexión, el aviso sigue: el dictado sale con el otro motor.
- [x] Con nivel literal, un paso que necesita red no avisa. Con otro nivel y sin red, sigue avisando.

### VOZ-88 · Instalar con un solo archivo, saber qué pasa con tus datos y conectar la API
**Archivos:** `build/voziris.wxs`, `build/voziris.es-es.wxl`, `tools/hacer_msi.py`, `tools/probar_msi.py`, `tools/empaquetar.py`, `build/voziris.spec`, `CONDICIONES.md`, `docs/GUIA-api.html`, `docs/LEEME-instalar.html`, `__main__.py`, `ui/bandeja.py`, `ui/ajustes.py`, `instalador.py`, `integridad.py`

Quien lo recibía tenía que extraer un ZIP de mil archivos, y por el camino Windows o el antivirus le quitaban alguno. Se pidió un MSI, condiciones que digan muy claro que no se recoge ningún dato y por qué es gratis, y una guía muy visual para conectar la API, dejando claro que el motor local es el más limitado.

**Lo que se decidió y por qué**
- **El MSI lleva el paquete, no lo instala.** Lo deja en una carpeta temporal y arranca `voziris.exe --instalar`: una sola forma de instalar, la que ya comprueba, prueba la copia y sabe deshacerlo. Un MSI corriente tendría que cerrar la Voziris abierta, sustituir archivos en uso y convivir con «Actualizar»; habría dos instaladores sobre la misma carpeta.
- **No se registra en Windows Installer.** Registrado, habría dos entradas en «Aplicaciones instaladas», y al abrirlo otra vez tras desinstalar no instalaría nada. Medido: no deja producto, ni componentes, ni copia en caché.
- **Cada ejecución, su carpeta**, dentro de la que ya limpia la siguiente Voziris. Medido: sobre una carpeta con los archivos de antes, Windows Installer apunta en HKLM cada archivo con versión que se salta, aun sin registro.
- **Lo que se gana y lo que no.** Medido en Windows 11: lo que escribe Windows Installer no hereda la marca de «descargado de Internet» del `.msi`, y no hay extracción que se quede a medias. El aviso «Windows protegió su PC» y el bloqueo de Control inteligente de aplicaciones siguen igual: solo los quita una firma, y la decisión de no firmar (7 de octubre) no cambia aquí.
- **El motor local sigue siendo el de serie**, y sigue sin avisar en cada dictado (VOZ-87). La API se recomienda una vez al terminar de instalar, y queda a mano en «Ayuda» y en Ajustes → Motor.

**Criterios de aceptación**
- [x] `tools/empaquetar.py` deja, junto al ZIP, `voziris-<versión>-win64.msi`, con los mismos archivos, y `SHA256SUMS.txt` con las dos líneas. Las versiones instaladas siguen leyendo la del ZIP.
- [x] El MSI se instala por usuario, sin administrador, y hay que aceptar las condiciones para seguir.
- [x] `tools/probar_msi.py`, sin ventanas y sin instalar: el paquete queda entero (manifiesto con hashes), sin marca de Internet, `voziris.exe --comprobar` carga todo desde ahí y Windows Installer no apunta nada. Pasa con el paquete de la 0.1.3.
- [x] El registro del instalador queda en la carpeta de la instalación, también en un equipo que nunca ha tenido Voziris.
- [x] `CONDICIONES.md`: sin telemetría ni servidor propio, qué se guarda en el equipo, cuándo se conecta y a qué, por qué es gratis, sin garantía. Cada frase, contrastada con el código. La guía lleva el mismo texto, y un test lo vigila.
- [x] `docs/GUIA-api.html`: siete pasos con dibujos, sin nada externo, con las etiquetas de Ajustes y de la consola de Groq tal como son.
- [x] Las dos guías van dentro del programa: bandeja → «Ayuda». En Ajustes → Motor, junto a la clave, la recomendación y el botón de la guía.
- [x] Una instalación nueva sin clave termina preguntando si se abre la guía de la API. Con clave, o al actualizar, no pregunta.
- [x] Los avisos de paquete incompleto y de bloqueo valen para quien viene del MSI: no mandan a «la carpeta donde extrajiste el ZIP» a quien no ha extraído nada.
- [ ] El asistente y su relevo al instalador, vistos con ventanas en un equipo de pruebas (`docs/DESPLIEGUE.md`, 6.1 bis).
- [ ] Publicado en una release, con el botón del blog apuntando al `.msi`.

**Fuera de esto**
- Firmar. Con Azure Artifact Signing a nombre de una empresa de la UE, o SignPath Foundation para proyectos de código abierto, o la Microsoft Store: es lo único que quita los avisos de Windows.
- «Actualizar» desde la bandeja sigue descargando el ZIP.
- Instalar en silencio para muchos equipos: el MSI admite `/qn`, pero el instalador de Voziris enseña su ventana.
- El filtro que tacha la clave en el registro (`_TacharClave`) está puesto en el registrador raíz, donde no ve lo que anotan los demás. Ningún mensaje lleva la clave, pero la red de seguridad no hace lo que dice.
- `ATRIBUCIONES.md` nombra un motor Whisper local que no existe y no nombra Silero VAD, que sí se descarga.
