# Voziris 0.1.4

Dictado por voz para Windows 10 y 11 (64 bits) con dos motores: **local**
(NVIDIA Parakeet TDT 0.6B v3 en ONNX, sin tarjeta gráfica y sin conexión) y
**por API** (Groq, con tu propia clave). Sin cuentas, sin suscripción y sin
servidor propio.

La historia de por qué existe: https://kairis.es/blog/voziris.html

## Novedades en 0.1.4

- **Se instala con un solo archivo.** Además del ZIP, ahora hay un instalador,
  `voziris-0.1.4-win64.msi`: se descarga, se abre con doble clic y no hay nada
  que extraer. Se instala solo para tu usuario, sin permisos de administrador.
  Tras aceptar las condiciones copia el programa a una carpeta temporal y abre
  la ventana «Instalando Voziris», que es el instalador de siempre: comprueba
  que no falta ningún archivo y que el programa arranca antes de cambiar nada.
  Lo que no cambia: Windows sigue avisando de que el programa no está firmado
  (ver «Limitaciones conocidas»).
- **Condiciones de uso y privacidad, por escrito.** Voziris no recoge ningún
  dato: no tiene telemetría ni servidor propio. Qué guarda en tu equipo, cuándo
  se conecta a internet y a qué, y por qué es gratis, está en
  https://github.com/alfonsosanzme/voziris/blob/main/CONDICIONES.md y en el
  apartado 6 de la guía. El instalador las enseña antes de instalar.
- **Conectar la API de Groq, con dibujos.** El motor local funciona sin
  configurar nada, pero es el más limitado; lo recomendable es conectar la API,
  que tiene plan gratuito. Hay una guía nueva, paso a paso:
  - está en el menú de la bandeja → «Ayuda», junto a la guía de uso;
  - en Ajustes → Motor, junto a la clave, el botón «Cómo conseguir la clave…»;
  - una instalación nueva, si todavía no hay clave, termina preguntando si la
    abres. Al actualizar no pregunta.
  Al guardar la clave hay que cerrar y volver a abrir Voziris: lo dice el propio
  aviso y lo dice la guía.

## Qué hay

- `Ctrl+Win` graba mientras lo mantienes; `Ctrl+Shift+Espacio` deja el
  micrófono clavado hasta que vuelves a pulsar o te callas; `Ctrl+Alt+M`
  manda el dictado a un archivo Markdown (antes, elígelo en Ajustes → Destinos).
- Puntuación y mayúsculas de serie en español, también sin conexión.
- Búfer previo de 500 ms: no se pierde la primera sílaba.
- Indicador flotante con nivel de audio que nunca roba el foco; tonos de
  inicio, fin y error; icono de bandeja con cuatro estados.
- Diccionario personal y sustituciones sin red; limpieza de muletillas y
  formato con un LLM opcional.
- El portapapeles vuelve a lo que tenías copiado. Historial con reentrega y
  borrado desde la bandeja.
- **Si algo falla, el dictado no se pierde**: el audio se guarda en disco antes
  de transcribir y se puede volver a transcribir desde el historial.
- Transcribe grabaciones y **vídeos** enteros (m4a, mp3, wav, mp4…) a
  Markdown, con marcas de tiempo y quién habla en cada momento. Se hace desde
  la bandeja, con el botón derecho del Explorador («Transcribir con Voziris»)
  o con `voziris.exe --transcribir`.
- Sin permisos de administrador. Se instala por usuario (menú Inicio y
  «Aplicaciones instaladas») con el `.msi`, o se usa portable desde el ZIP. La
  copia instalada se actualiza desde la bandeja.

## Instalación

Descarga `voziris-0.1.4-win64.msi` y ábrelo con doble clic. Si Windows enseña
«Windows protegió su PC», pulsa *Más información → Ejecutar de todas formas*.
Acepta las condiciones, pulsa «Instalar» y espera a la ventana «Instalando
Voziris», que avisa al terminar. El primer arranque descarga el modelo local
(640 MB). Hasta que termina, los dictados esperan, también con el motor por
API. La guía de uso y la de la API están en el menú de la bandeja → «Ayuda».

**Portable, sin instalar:** descomprime `voziris-0.1.4-win64.zip` **entero**, en
una carpeta nueva, y ejecuta `voziris.exe`. El modelo se descarga a `modelos/`,
junto al ejecutable. Dentro va `LÉEME - Instalar Voziris.html`.

**Si ya tenías la 0.1.2 o la 0.1.3 instalada:** bandeja → «Buscar
actualizaciones» → «Actualizar a la 0.1.4…». Si aceptaste que mire las versiones
nuevas, también te avisará sola cuando le toque mirar, una vez al día. Descarga
el ZIP de la versión nueva, comprueba su SHA-256 y que no le falta ningún
archivo, y la instala sin tocar tu configuración, tus dictados ni el modelo.
También vale abrir el `.msi`: hace la misma instalación.

**Si ya tenías la 0.1.0 o la 0.1.1 instalada:** esas versiones no buscan
versiones nuevas. Descarga el `.msi` y ábrelo: cierra la Voziris abierta solo
cuando va a sustituirla, y se quedan tu configuración, el modelo descargado y
el historial.

**Si usas Voziris sin instalar:** la copia portable no se cambia sola.

- **Quieres instalarla:** copia `config.toml`, `modelos/` e `historial/` de la
  carpeta vieja junto al `voziris.exe` del ZIP nuevo y ejecuta
  `Instalar Voziris.cmd`. La instalación se los lleva y el arranque con Windows
  pasa a la instalada.
- **Quieres seguir en portable:**
  - Cierra la vieja (bandeja → Salir).
  - Copia esas tres cosas a la carpeta nueva.
  - Si arrancaba con Windows, abre los Ajustes de la nueva: desmarca
    «Arrancar con Windows» y pulsa Guardar, y luego márcalo y Guardar otra vez.
    Si no, al iniciar sesión seguiría abriéndose la vieja.
  - Si usabas el botón derecho del Explorador, ejecuta
    `voziris.exe --menu-contextual` desde la carpeta nueva.

## Verificación del archivo

```
SHA-256  voziris-0.1.4-win64.msi
29cdc82eb33e04f55f5cb9e7e5ba5ab2f017122e09d00fddb15a083f59d944d4

SHA-256  voziris-0.1.4-win64.zip
835781fc0a2b290fe504698b640802c23666a2046e2a955e36786d2d958f9239
```

Un `.exe` de PyInstaller sin firmar que escucha el teclado para detectar el
atajo es un perfil clásico de falso positivo de antivirus. Si el archivo que
has descargado tiene este hash, es el que se publicó aquí.

## Limitaciones conocidas

- **El programa no está firmado.**
  - Windows puede enseñar «Windows protegió su PC»: pulsa *Más información →
    Ejecutar de todas formas*. El instalador `.msi` evita extraer el ZIP,
    pero no ese aviso: solo lo quitaría una firma.
  - En equipos con **Control inteligente de aplicaciones** activado, Windows
    puede bloquear la copia instalada. Esto pasa aunque deje abrir la carpeta
    extraída. La guía explica qué hacer.
  - Al actualizar desde la bandeja, el SHA-256 sale de la misma release que el
    ZIP: detecta una descarga cortada o dañada, no una release falsa.
- Si el primer arranque no tiene conexión, solo dice que el motor de voz no está
  disponible. Conéctate y vuelve a abrir Voziris.
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
