# Voziris 0.1.3

Dictado por voz para Windows 10 y 11 (64 bits) con dos motores: **local**
(NVIDIA Parakeet TDT 0.6B v3 en ONNX, sin tarjeta gráfica y sin conexión) y
**por API** (Groq, con tu propia clave). Sin cuentas, sin suscripción y sin
servidor propio.

La historia de por qué existe: https://kairis.es/blog/voziris.html

## Novedades en 0.1.3

- **Si pulsas sin hablar, ahora te lo dice.** Desde la 0.1.2, si nadie habla no
  se escribe nada, pero el aviso se iba antes de llegar a verse. Ahora el
  indicador enseña «Nada que escribir: no se oyó voz» durante un segundo y
  medio. Con 5 segundos o más sin voz sigue como estaba: icono de error, y la
  grabación se queda en «Dictados sin transcribir». También se ven los demás
  avisos que llegan al acabar un dictado, como «…: transcrito en local» o «Sin
  red: entregado sin limpiar».
- **Una Voziris sin ventana ya no impide instalar ni actualizar.** Bastaba una
  copia parada en el aviso «Voziris ya está abierto», por ejemplo abierta por
  otro programa sin que nadie la viera, para que la instalación fallara con
  «otro programa tiene abierto un archivo». Decía que esperases un minuto, y
  esperar no lo arreglaba. Ahora:
  - ese aviso se cierra solo a los 10 segundos, y esa copia ya no estorba
    mientras tanto;
  - al cambiar el programa, el instalador cierra lo que siga abierto desde su
    carpeta. Si algo está transcribiendo, pregunta antes: si contestas que no,
    no se cambia nada y la transcripción sigue;
  - si aun así otro programa lo impide, dice cuál es, con su PID, y deja
    reintentar sin volver a copiar. La versión anterior sigue en su sitio;
  - si al final no se instala y se había cerrado la bandeja, se vuelve a abrir,
    también si cancelas.
- **El aviso final de la instalación ya no se queda escondido.** Los avisos del
  instalador y de «Actualizar» salen encima de las demás ventanas. Y mientras
  el aviso final sigue abierto, «Actualizar» ya no contesta que hay una
  actualización en marcha.

## Qué hay

- `Ctrl+Win` graba mientras lo mantienes; `Ctrl+Shift+Espacio` deja el
  micrófono clavado hasta que vuelves a pulsar o te callas; `Ctrl+Alt+M`
  manda el dictado a un archivo Markdown.
- Puntuación y mayúsculas de serie en español, también sin conexión.
- Búfer previo de 500 ms: no se pierde la primera sílaba.
- Indicador flotante con nivel de audio que nunca roba el foco; tonos de
  inicio, fin y error; icono de bandeja con cuatro estados.
- Diccionario personal y sustituciones sin red; limpieza de muletillas y
  formato con un LLM opcional.
- El portapapeles vuelve a lo que tenías copiado. Historial con reentrega y
  borrado desde la bandeja.
- **Ningún dictado se pierde**: el audio se guarda en disco antes de
  transcribir y, si algo falla, se puede volver a transcribir desde el
  historial.
- Transcribe grabaciones y **vídeos** enteros (m4a, mp3, wav, mp4…) a
  Markdown, con marcas de tiempo y quién habla en cada momento. Se hace desde
  la bandeja, con el botón derecho del Explorador («Transcribir con Voziris»)
  o con `voziris.exe --transcribir`.
- Portable y sin permisos de administrador. Instalación opcional por usuario
  (menú Inicio y «Aplicaciones instaladas») con `Instalar Voziris.cmd`, que
  tampoco los pide. La copia instalada se actualiza desde la bandeja.

## Instalación

Descomprime el ZIP **entero**, en una carpeta nueva, y ejecuta `voziris.exe`.
El primer arranque descarga el modelo local (640 MB) a `modelos/`. Hasta que
termina, los dictados esperan, también con el motor por API. Dentro va
`LÉEME - Instalar Voziris.html`, con la instalación en el equipo y cómo
conectar una clave de Groq.

**Si ya tenías la 0.1.2 instalada:** bandeja → «Buscar actualizaciones» →
«Actualizar a la 0.1.3…». Si aceptaste que mire las versiones nuevas, también te
avisará sola cuando le toque mirar, una vez al día. Descarga la versión nueva,
comprueba su SHA-256 y que no le falta ningún archivo, y la instala sin tocar tu
configuración, tus dictados ni el modelo.

**Si ya tenías la 0.1.0 o la 0.1.1, o usas Voziris sin instalar:** se actualiza a
mano, porque esas versiones no buscan versiones nuevas y la copia sin instalar
no se cambia sola (en la 0.1.2, «Descargar la 0.1.3…» abre la página de la
versión). Después, una copia instalada de esta versión te ofrecerá las
siguientes en la bandeja. Lo que trajo la 0.1.2 está en sus notas:
https://github.com/alfonsosanzme/voziris/releases/tag/v0.1.2

- **Instalada:** extrae este ZIP en una carpeta nueva y ejecuta su
  `Instalar Voziris.cmd`. Cierra la Voziris abierta solo cuando va a
  sustituirla. Se quedan tu configuración, el modelo descargado y el historial.
- **Portable, y quieres instalarla:** copia `config.toml`, `modelos/` e
  `historial/` de la carpeta vieja junto al `voziris.exe` nuevo y ejecuta
  `Instalar Voziris.cmd`. La instalación se los lleva y el arranque con Windows
  pasa a la instalada.
- **Portable, y quieres seguir en portable:**
  - Cierra la vieja (bandeja → Salir).
  - Copia esas tres cosas a la carpeta nueva.
  - Si arrancaba con Windows, abre los Ajustes de la nueva: desmarca
    «Arrancar con Windows» y pulsa Guardar, y luego márcalo y Guardar otra vez.
    Si no, al iniciar sesión seguiría abriéndose la vieja.
  - Si usabas el botón derecho del Explorador, ejecuta
    `voziris.exe --menu-contextual` desde la carpeta nueva.

## Verificación del archivo

```
SHA-256  voziris-0.1.3-win64.zip
0000000000000000000000000000000000000000000000000000000000000000
```

Un `.exe` de PyInstaller sin firmar que escucha el teclado para detectar el
atajo es un perfil clásico de falso positivo de antivirus. Si el archivo que
has descargado tiene este hash, es el que se publicó aquí.

## Limitaciones conocidas

- **El programa no está firmado.**
  - Windows puede enseñar «Windows protegió su PC»: pulsa *Más información →
    Ejecutar de todas formas*.
  - En equipos con **Control inteligente de aplicaciones** activado, Windows
    puede bloquear la copia instalada. Esto pasa aunque deje abrir la carpeta
    extraída. La guía explica qué hacer.
  - Al actualizar desde la bandeja, el SHA-256 sale de la misma release que el
    ZIP: detecta una descarga cortada o dañada, no una release falsa.
- Si el primer arranque no tiene conexión, la bandeja solo dice que el motor de
  voz no está disponible. Conéctate y vuelve a abrir Voziris.
- Una sola palabra muy corta, dicha sola, puede tomarse por silencio, y
  entonces no se escribe nada. Vuelve a dictarla.
- Un aviso del indicador de más de unos 45 caracteres se ve cortado.
- Con una ventana que corre como administrador en primer plano, Windows no
  deja a Voziris ver el atajo ni pegar en ella. Si un dictado tenía que ir a
  esa ventana, queda en el historial y en el portapapeles.
- Solo se conserva el portapapeles si contenía texto.
- Algunas terminales rechazan el pegado sintético: `metodo = "tecleo"`.
- La separación de voces acierta con dos personas en una reunión. En una
  llamada grabada desde un lado, alguna frase corta puede acabar atribuida a
  quien no es.

## Licencias

Código bajo MIT. El motor local usa **NVIDIA Parakeet TDT 0.6B v3**,
© NVIDIA Corporation, distribuido bajo **CC-BY-4.0**:
https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3. La atribución viaja en
`ATRIBUCIONES.md` dentro del paquete y en la ventana «Acerca de».
