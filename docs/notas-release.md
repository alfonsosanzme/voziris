# Voziris 0.1.2

Dictado por voz para Windows 10 y 11 (64 bits) con dos motores: **local**
(NVIDIA Parakeet TDT 0.6B v3 en ONNX, sin tarjeta gráfica y sin conexión) y
**por API** (Groq, con tu propia clave). Sin cuentas, sin suscripción y sin
servidor propio.

La historia de por qué existe: https://kairis.es/blog/voziris.html

## Novedades en 0.1.2

- **Ctrl solo ya no arranca el dictado.** A veces, al pulsar solo Ctrl, o
  Ctrl+C o Ctrl+V, empezaba un dictado corto. Pasaba sobre todo al poco de
  arrancar Voziris, mientras carga el modelo: si se le escapaba que habías
  soltado la tecla Windows, la seguía creyendo apretada hasta que la volvías a
  pulsar. Ahora, con cada tecla que pulsas, comprueba con Windows cuáles
  siguen apretadas de verdad. Por lo mismo, una tecla que se quedaba
  «atascada» ya no deja `Ctrl+Win` sin responder.
- **Si no hablas, no se escribe nada.** Al pulsar el atajo sin decir nada, el
  motor por API escribía «Gracias por ver el video»: Whisper se inventa frases
  así con cualquier audio sin voz. Ahora, antes de transcribir, el detector de
  voz (en cuanto ha cargado, al arrancar) mira si alguien ha hablado. Si nadie
  ha hablado, no se manda el audio a ningún motor ni se pega nada. Con 5
  segundos o más sin voz, avisa con el icono de error y la grabación se queda
  en «Dictados sin transcribir», por si el detector se ha equivocado.
- **Avisa de las versiones nuevas y se actualiza desde la bandeja.** Unos 20
  segundos después de arrancar por primera vez, pregunta si quieres que mire
  una vez al día si hay una versión nueva. Sin un sí, no consulta GitHub por
  su cuenta. Si la hay, te avisa, y en el menú de la bandeja aparece
  «Actualizar a la …». Al pulsarlo, descarga la versión nueva de su release de
  GitHub, comprueba su SHA-256 y que no le falta ningún archivo, y la instala
  sin tocar tu configuración, tus dictados ni el modelo.
  - «Buscar actualizaciones», en la bandeja, mira en el momento.
  - La respuesta se cambia en Ajustes → Acerca de.
  - Si usas Voziris sin instalar, la entrada se llama «Descargar la …» y abre
    la página de la versión.
- **Ajustes ya guarda con la configuración de serie.** En la 0.1.0 y la 0.1.1,
  mientras la carpeta del Markdown siguiera con el valor de ejemplo, Ajustes
  no dejaba guardar nada, tampoco la clave de Groq, aunque no usaras ese
  destino. Lo que se cambiaba en la bandeja («Motor», «Mientras dicto» y
  «Cortar al callar») solo duraba hasta reiniciar. Ahora esa carpeta solo se
  comprueba si la cambias.
- **Al instalar encima de una versión anterior**, el aviso final dice
  «Voziris actualizado» y qué versión tenías antes. El diagnóstico de la
  bandeja recoge también los registros de las actualizaciones desde la
  bandeja.

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

**Si ya tenías la 0.1.0 o la 0.1.1:** esas versiones no buscan versiones
nuevas, así que esta vez se actualiza a mano. Desde la 0.1.2, si aceptas que
mire las versiones nuevas (o con «Buscar actualizaciones»), la bandeja te
ofrecerá las siguientes.

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
SHA-256  voziris-0.1.2-win64.zip
8aa5215eb57d728e95166aba59d4a837d25cee63e9edae4c61f8830fb1a07b54
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
