# Voziris 0.1.1

Dictado por voz para Windows 10 y 11 (64 bits) con dos motores: **local**
(NVIDIA Parakeet TDT 0.6B v3 en ONNX, sin tarjeta gráfica y sin conexión) y
**por API** (Groq, con tu propia clave). Sin cuentas, sin suscripción y sin
servidor propio.

La historia de por qué existe: https://kairis.es/blog/voziris.html

## Novedades en 0.1.1

Esta versión no trae funciones nuevas. Trata de que la instalación no falle en
silencio y de que, cuando algo falla, el aviso diga qué hacer.

- **El aviso dice qué archivo falta.** Si el antivirus pone en cuarentena un
  archivo del programa, o «Extraer todo» se salta alguno, el aviso ya no dice
  «Falta PyAV». Ahora nombra el archivo: «Falta `_internal\av.libs\…dll`.
  Suele ser el antivirus…». El programa lo comprueba también al arrancar.
- **El instalador comprueba antes de cambiar nada.**
  - Revisa que la carpeta extraída esté completa.
  - Copia el programa aparte y comprueba que esa copia arranca.
  - Solo entonces sustituye la versión anterior. Si algo falla por el camino,
    la anterior se queda como estaba y se vuelve a abrir.
  - Ya no arrastra la marca de «descargado de Internet» a la copia instalada.
  - Al actualizar, no pisa la configuración, el modelo ni el historial que
    ya tengas instalados.
  - Muestra una ventana con el progreso. Tarda algo más que antes, porque
    comprueba cada archivo.
- **Control inteligente de aplicaciones de Windows 11.** Si esa protección
  bloquea Voziris, el instalador lo detecta y explica qué hacer. La guía del
  ZIP tiene un apartado nuevo, «Si Windows lo bloquea».
- **Al transcribir un archivo sin conexión la primera vez**, el aviso dice que
  falta descargar el modelo de voz. Antes decía «ni la API ni el motor local
  están disponibles».
- **Transcribir abre el archivo antes de cargar el modelo.** Si no se puede
  abrir, o a la instalación le falta PyAV, se sabe al momento y no tras esperar
  a la carga.
- **Registro aparte para las transcripciones de archivos.** Su registro está en
  `voziris-transcribir.log`, junto a `voziris.log`.
- **El diagnóstico de la bandeja** comprueba el paquete, recoge los bloqueos
  que haya anotado Windows e incluye ese registro de transcripciones.

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
  tampoco los pide.

## Instalación

Descomprime el ZIP **entero**, en una carpeta nueva, y ejecuta `voziris.exe`.
El primer arranque descarga el modelo local (640 MB) a `modelos/`. Hasta que
termina, los dictados esperan, también con el motor por API. Dentro va
`LÉEME - Instalar Voziris.html`, con la instalación en el equipo y cómo
conectar una clave de Groq.

**Si ya tenías la 0.1.0:**

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
SHA-256  voziris-0.1.1-win64.zip
e1edd782aa0fc837c5b98531f141c85a7c6e838bd6dc151c2623dabba93d3351
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
- Si el primer arranque no tiene conexión, la bandeja solo dice que el motor de
  voz no está disponible. Conéctate y vuelve a abrir Voziris.
- Sobre una ventana que corre como administrador, Windows no entrega la
  pulsación sintética: el dictado queda en el historial y en el portapapeles.
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
