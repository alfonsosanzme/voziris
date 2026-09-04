# Voziris

Dictado por voz para Windows. Mantienes un atajo, hablas, sueltas, y el texto
aparece donde tengas el cursor.

Dos motores a elegir: **local**, gratis y sin conexión, que funciona en
portátiles sin GPU; o **por API**, con tu propia clave, por unos céntimos al
mes. Sin cuentas, sin servidor propio, sin suscripción.

![Indicador flotante mientras graba](docs/capturas/hud-grabando.png)

## Por qué existe

Wispr Flow cuesta 144 $/año, procesa todo en la nube —no tiene modo local en
ningún plan— y su ajuste de tono y estilo solo funciona en inglés. Voziris
cubre esos tres huecos. Con Groq, transcribir media hora de audio al día sale
por unos 0,60 $ al mes; en local, por cero.

## Qué hace

| | |
|---|---|
| **Cuatro atajos** | `Ctrl+Win` graba mientras lo mantienes, `Ctrl+Shift+Espacio` deja el micrófono clavado hasta que vuelves a pulsar o te callas, `Ctrl+Alt+M` manda el dictado a un archivo Markdown mientras lo mantienes y `Ctrl+Shift+M` lo hace con el micrófono clavado. El corte al callar se puede apagar desde la bandeja («Cortar al callar») |
| **Puntuación de serie** | El modelo emite puntuación y mayúsculas en español, también sin conexión |
| **Limpia lo que dices** | Quita muletillas y resuelve tus autocorrecciones al hablar («el martes, no, el jueves»), con un LLM opcional |
| **No te toca el portapapeles** | Lo guarda antes de escribir y lo devuelve después |
| **Historial con reintento** | Si el pegado falla, el texto no se pierde: se reentrega desde el menú de la bandeja |
| **Diccionario propio** | Para los nombres propios y la jerga que el modelo falla siempre |
| **Portable** | Se copia y funciona. Sin instalador, sin registro, sin permisos de administrador |

## Primer arranque

1. Descarga el ZIP de la última [release](https://github.com/alfonsosanzme/voziris/releases)
   y descomprímelo donde quieras (un USB vale). Son unos 1 100 archivos:
   espera a que la extracción termine del todo antes de ejecutar nada. Si
   `voziris.exe` se lanza a medias, falla con «No module named
   numpy._core…» y basta con esperar y volver a abrirlo.
2. Comprueba el hash SHA-256 que viene en las notas de la release si tu
   antivirus protesta (ver «Limitaciones conocidas»).
3. Ejecuta `voziris.exe`. Aparece el icono del micrófono en la bandeja y
   **empieza a descargar el modelo local** (640 MB, una sola vez, a la
   carpeta `modelos/` junto al ejecutable). Con una conexión normal tarda
   entre uno y tres minutos; la bandeja avisa del progreso.
4. Mientras baja, o si prefieres no esperar, puedes usar el motor por API:
   menú de la bandeja → Motor → API (necesita una clave, ver abajo).
5. Pon el cursor en cualquier aplicación, mantén `Ctrl+Win`, habla, suelta.

El primer arranque también copia `config.ejemplo.toml` a `config.toml` y,
como el ejemplo trae `arranque_con_windows = true`, deja un acceso directo en
tu carpeta de Inicio. Se quita desde Ajustes o poniendo `false`.

## Ajustes

Menú de la bandeja → Ajustes. Seis pestañas: atajos (se configuran pulsando
la combinación), audio (micrófono, ganancia con medidor en vivo, sonidos),
motor (local, API o automático, clave con botón «probar»), texto (nivel de
limpieza, diccionario, sustituciones), destinos (método de inserción,
auto-Enter, ruta del Markdown) y acerca de.

![Pestaña de atajos](docs/capturas/ajustes-atajos.png)

Todo lo que se guarda va a `config.toml`, junto al ejecutable, con sus
comentarios intactos: se puede editar a mano igualmente.

## Clave de Groq (motor por API y limpieza con LLM)

Con `motor = "local"` no hace falta ninguna clave. La clave solo se usa para
dos cosas opcionales: transcribir en la nube (más precisión, sobre todo con
jerga) y limpiar el texto con un LLM.

### Conseguirla (cinco minutos)

1. Entra en https://console.groq.com y crea una cuenta (vale la de Google o
   GitHub). El plan gratuito ya permite probar; para uso diario conviene
   añadir un método de pago, porque el nivel gratuito tiene límites por
   minuto y el de pago cuesta céntimos.
2. En el menú de la izquierda, **API Keys** → **Create API Key**. Ponle un
   nombre («Voziris») y copia la clave que aparece: empieza por `gsk_` y
   **solo se muestra una vez**. Si la pierdes, se crea otra y se borra la
   vieja.
3. Guárdala en un gestor de contraseñas. No la pegues en chats ni en
   capturas de pantalla: quien la tenga puede gastar con tu cuenta.

### Ponerla en Voziris

Hay dos sitios; con uno basta:

- **En la aplicación (lo más cómodo).** Menú de la bandeja → Ajustes →
  pestaña **Motor** → campo **Clave** (se muestra con puntos). Pulsa
  **Probar la clave** para ver «✓ La clave es válida» y luego **Guardar**.
  Queda escrita en `config.toml`, junto al ejecutable, en texto plano: si
  la carpeta va en un USB, la clave va con ella.
- **En una variable de entorno (si prefieres no escribirla en el archivo).**
  Con el campo Clave vacío, Voziris lee `GROQ_API_KEY`. En Windows:
  *Configuración → Sistema → Información → Configuración avanzada del
  sistema → Variables de entorno → Variables de usuario → Nueva*, o desde
  PowerShell (solo para tu usuario, sin administrador):

  ```powershell
  [Environment]::SetEnvironmentVariable("GROQ_API_KEY", "gsk_…", "User")
  ```

  Cierra y vuelve a abrir Voziris para que la vea.

Después, en Ajustes → Motor elige **API** o **Automático** (API si hay red,
local si no). Y en Ajustes → Texto, para la limpieza, escribe un modelo en
«Modelo de LLM», por ejemplo `llama-3.3-70b-versatile`.

### Qué cuesta de verdad

`whisper-large-v3-turbo` cuesta 0,04 $ por hora de audio: dictando 30
minutos al día, unos 0,60 $ al mes. La limpieza con LLM añade una cantidad
marginal (unos 200 tokens por minuto de dictado). El consumo se ve en
https://console.groq.com bajo **Usage**. La clave nunca sale de tu equipo
salvo hacia Groq, y no aparece en el log ni en los mensajes de error.

## Qué sale de tu equipo

Voziris no tiene telemetría de ningún tipo: ni anónima, ni opcional, ni
comprobación de versiones. Lo único que puede salir depende de dos opciones
de `config.toml`, `general.motor` y `proceso.nivel`:

| Modo | Sale del equipo |
|---|---|
| `motor = "local"` y `nivel = "literal"` | **nada**. Ni siquiera se comprueba si hay red. |
| `motor = "local"` y `nivel = "limpio"` o `"reescritura"` | el **texto** transcrito, a la API del LLM configurado, para limpiarlo |
| `motor = "api"` o `"auto"` con red | el **audio** del dictado, a la API de transcripción; y el texto al LLM si el nivel no es `literal` |
| Primer arranque, o modelo que falta | el modelo se descarga de **Hugging Face** una vez a `modelos/` |

Está verificado con un test (`tests/test_offline.py`) que bloquea toda
conexión saliente del proceso y transcribe diez dictados en modo local
literal: ningún intento de conexión.

## Limitaciones conocidas

1. **Ventanas elevadas.** Si la aplicación en primer plano corre como
   administrador y Voziris no, Windows no le entrega la pulsación sintética.
   El dictado queda en el historial con aviso y el texto en el portapapeles,
   para pegarlo a mano. No hay solución que no sea ejecutar Voziris elevado,
   y eso choca con no pedir permisos de administrador.
2. **Antivirus.** Un `.exe` de PyInstaller con un hook global de teclado es una
   firma clásica de falso positivo. Los hashes SHA-256 de cada versión están
   en las notas de la release: si el archivo coincide, es el que se publicó.
3. **Portapapeles.** Voziris guarda lo que tenías copiado antes de dictar y lo
   devuelve después, pero solo si era texto. Una imagen o unos archivos
   copiados se pierden al dictar: en su lugar queda el texto dictado.
4. **Aplicaciones que rechazan el pegado.** Algunas terminales y programas
   con protección de entrada ignoran el Ctrl+V sintético. Para ellos,
   `metodo = "tecleo"` en `[destino.app_activa]`: más lento, pero entra.
5. **Historial del portapapeles de Windows (Win+V).** Si está activado, lee
   cada dictado unos milisegundos después de copiarlo; Voziris espera 20 ms
   antes de pegar para no chocar con él. Los dictados aparecerán en ese
   historial como cualquier otro texto copiado.

## Desarrollo

Requiere Python 3.11–3.13 y Windows 10 (1903+) o 11, 64 bits.

```bash
git clone https://github.com/alfonsosanzme/voziris && cd voziris
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,banco,vad]"

copy config.ejemplo.toml config.toml    # y editar la ruta del Markdown

pytest                                  # tests (los marcados win roban el foco un instante)
ruff check src tests tools              # linter
mypy                                    # tipos (estricto)
python -m voziris                       # arrancar
python -m voziris --archivo x.wav       # modo consola: transcribe un WAV y sale
python -m voziris --salir               # cierra la instancia abierta
```

Antes de tocar el motor local, el hito 0 mide si aguanta en el equipo real:

```bash
python tools/banco_h0.py grabar --n 10 --segundos 15
python tools/banco_h0.py medir
```

El veredicto de ese hito en el portátil del cliente está en `docs/H0.md`.

### Empaquetado

```bash
pyinstaller build/voziris.spec --noconfirm --distpath build/dist --workpath build/work
```

Produce `build/dist/voziris/`. Se comprime y se distribuye tal cual: es la
carpeta portable. El modelo (~640 MB) no va dentro, se descarga en el primer
arranque. Ver `docs/RELEASE.md` para la lista de verificación y los pasos de
la release.

### Documentación

| Documento | Para qué |
|---|---|
| `docs/ENCARGO.md` | El encargo: qué construir, qué está decidido, qué no tocar |
| `docs/BACKLOG.md` | Las 25 tareas con criterios de aceptación |
| `docs/ARQUITECTURA.md` | Módulos, contratos y por qué están así |
| `docs/PLAN.md` | El plan de desarrollo: comportamiento, hilos, fallos, pruebas |
| `docs/H0.md` | Veredicto del hito 0: medidas del motor local en el equipo real |
| `docs/RELEASE.md` | Cómo verificar el paquete y publicar una versión |
| `docs/issues.csv` | El backlog, importable a GitHub Issues |

## Licencia

Código bajo licencia MIT (ver `LICENSE`).

El motor local usa **NVIDIA Parakeet TDT 0.6B v3**, © NVIDIA Corporation,
distribuido bajo **CC-BY-4.0**:
https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3

Ver `ATRIBUCIONES.md`. Esa atribución es una obligación de la licencia y debe
seguir apareciendo en el repositorio, en el binario y en la ventana «Acerca de».
