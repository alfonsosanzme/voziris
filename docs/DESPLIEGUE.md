# Desplegar una versión de Voziris

Este documento explica cómo publicar una versión nueva de Voziris y cómo hacer que llegue a quien lo usa. Se escribió después de publicar la 0.1.1, el 7 de octubre de 2026 (PR #40, con `main` en `149936c`). Está comprobado contra el código de esa versión y sus órdenes son las que funcionaron entonces. Lo que no se ha ejecutado nunca lleva la marca **(sin probar)**.

Las rutas cambiaron el 30 de octubre de 2026, antes de la 0.1.2: el 21 de octubre el código salió de OneDrive a `C:\dev\voziris`, y las del 7 de octubre ya no existen. Con ellas se publicó la 0.1.2 el 2 de noviembre de 2026 (PR #48, con `main` en `43d7cf8`): los pasos 1 a 9 y el 11 están comprobados con esa versión, y otra vez con la 0.1.3, el 3 de noviembre de 2026.

El 11 de noviembre de 2026 se añadió el instalador MSI (VOZ-88). Todavía no se ha publicado ninguna versión con él: lo que se dice del MSI en los pasos 5 y 6.1 bis está ejecutado con el paquete de la 0.1.3, y lo demás lleva la marca **(sin probar)** hasta la primera release que lo lleve.

En todo el documento, `<X.Y.Z>` es la versión nueva y `<anterior>` la que está publicada ahora (por ejemplo, 0.1.2 y 0.1.1).

## Qué significa desplegar

Desplegar Voziris es llegar a tres sitios. Con el primero no basta.

1. **La release `v<X.Y.Z>` de GitHub**, en `alfonsosanzme/voziris`, que es público. Lleva el instalador MSI, el ZIP, `SHA256SUMS.txt` y las notas. Todo lo demás enlaza aquí.
2. **El blog de kairis.es.** El botón de descarga de https://kairis.es/blog/voziris.html apunta a un archivo de una release concreta. Mientras no se cambie, sigue ofreciendo la versión anterior.
3. **Quien ya lo usa.** Hasta la 0.1.1, Voziris no busca versiones nuevas: cada equipo se actualiza a mano y a los demás hay que avisarles. Para pasar el ZIP a mano está `voziris-entrega/compartir/`. Desde la 0.1.2 (VOZ-82), las copias cuyo usuario aceptó la pregunta del primer arranque consultan `releases/latest` una vez al día y avisan; la instalada se actualiza desde la bandeja, con «Actualizar a la X.Y.Z…».

Hay reglas que no cambian:

- **Nunca se sobrescribe un asset publicado ni se reutiliza un número de versión.** Si algo sale mal, se publica la versión siguiente. Con el mismo nombre de archivo, el hash publicado y las cachés dejarían de cuadrar.
- **La release anterior se queda.** Es el camino de vuelta para quien lo necesite.
- **Publicar como «Latest» es repartir.** Desde la 0.1.2, en menos de un día las Voziris con la comprobación activada avisan de la versión nueva. Lo que no esté probado no se publica como «Latest»: un borrador o una *prerelease* no se ofrece a nadie. Para que la puedan instalar desde la bandeja, la etiqueta es `v<X.Y.Z>` y los assets se llaman exactamente `voziris-<X.Y.Z>-win64.zip` y `SHA256SUMS.txt`. Con otros nombres, avisan pero mandan a la página.
- **«Actualizar» sigue usando el ZIP.** El MSI (`voziris-<X.Y.Z>-win64.msi`) es para quien lo descarga a mano. Una release sin ZIP dejaría sin actualización a todas las instaladas, aunque lleve el MSI. `SHA256SUMS.txt` lleva las dos líneas, y la del ZIP es la que buscan las instaladas, por su nombre.
- **La web solo se despliega con el permiso explícito de Alfonso.** Tampoco se despliega si hay trabajo de otros sin subir (paso 10).
- **Una sesión de Claude no cierra ni relanza la Voziris de la bandeja.** Tampoco toca `%LOCALAPPDATA%\Programs\Voziris`, el menú Inicio ni el registro. La excepción es el paso 12, y solo con el sí de Alfonso. Ojo: `voziris.exe --instalar` y `voziris.exe --salir` cierran la Voziris que esté abierta, sea cual sea.

## Antes de empezar

Todas las órdenes son de **Git Bash**, salvo dos de PowerShell que van marcadas.

| Requisito | Cómo comprobarlo | Por qué |
|---|---|---|
| `gh` con la sesión de `alfonsosanzme` | `gh auth status` | Crea el PR y la release. |
| El Python 3.13 del venv del proyecto, no un 3.14 | `"$PY" --version` → `3.13.x`; `"$PY" -m PyInstaller --version` → `6.x` | `pyproject.toml` excluye la 3.14. Además, `empaquetar.py` se niega si la `python3XX.dll` del paquete no es la del Python que lo ejecuta. |
| WiX 5 con su extensión de interfaz | `wix --version` → `5.x`; `wix extension list --global` → `WixToolset.UI.wixext 5.x` | Hace el MSI. Se instala sin administrador: `dotnet tool install --global wix --version 5.0.2` y `wix extension add --global WixToolset.UI.wixext/5.0.2`. `empaquetar.py` lo comprueba antes de empezar. |
| `node` | `node --version` | Genera el blog. |
| Unos 1,5 GB libres en `C:` | — | La compilación ocupa unos 370 MB en `C:\dev\voziris-release`. La prueba de actualización extrae paquetes de unos 230 MB en `%TEMP%` y los instala. |
| Trabajar **fuera de OneDrive y de `%LOCALAPPDATA%`** | Las rutas de abajo, todas en `C:\dev` | OneDrive impide borrar `build/dist`, y PyInstaller necesita vaciar esa carpeta. La app de Claude virtualiza `%LOCALAPPDATA%`: lo que crean ahí sus herramientas no lo ve el Python del venv, que es el de la Microsoft Store (ver la primera trampa). |

Hay tres trampas, las tres comprobadas:

- **El venv vive en la copia principal y arrastra su código.**
  - El venv es `C:\dev\voziris\.venv` y tiene Voziris instalado en modo editable, apuntando a `C:\dev\voziris\src`, el de la copia principal.
  - Esa copia puede ir por detrás de `origin/main`. Sin `PYTHONPATH`, Python y PyInstaller cogen ese código.
  - El 7 de octubre, `import voziris` sin `PYTHONPATH` daba la 0.1.0.
  - En el registro de PyInstaller de la 0.1.1, el `pathex` del `.spec` aparece **detrás** del `src` de la copia principal.
  - Por eso todas las órdenes de Python de este documento llevan `PYTHONPATH="$PWD/src"` y se ejecutan desde el worktree.
  - Con `PYTHONPATH` en una carpeta que ese Python no ve, `import voziris` vuelve a caer en la copia principal, y sin avisar. Pasa con lo que crea la app de Claude en `%LOCALAPPDATA%`: en octubre dio tres fallos falsos al probar #43 y #44. Por eso el paso 1 imprime `voziris.__file__`.
  - Pasa también al juntar rutas: en Git Bash, `PYTHONPATH="$PWD/src;otra"` no se traduce, y `$PWD` llega como `/c/dev/...`. Con `;`, escribe las rutas enteras (`C:/dev/...`). Le pasó a la 0.1.3 con una suite entera, y se vio porque imprimía `voziris.__file__`.
- **PyInstaller empaqueta lo que hay en disco, no lo que hay en git.** Hay que compilar desde un worktree limpio, nunca desde la copia principal.
- **Desde la app de Claude, el registro también está virtualizado.** El 2 de noviembre, sus herramientas leían en `HKCU` una copia privada y vieja: `DisplayVersion` 0.1.0, con la 0.1.2 instalada.
  - Para ver el valor real, usa el Python del venv (`winreg`) o WMI (`StdRegProv`).
  - Por eso, los instaladores de la Voziris de verdad los lanza Alfonso desde el Explorador, no una sesión de Claude.

Estas son las variables que usan las órdenes. Un Git Bash abierto a mano las conserva de una orden a otra. **Una sesión de Claude Code empieza cada llamada con un shell nuevo**, así que tiene que poner al principio de cada bloque las variables que use.

```bash
V=<X.Y.Z>                       # la versión nueva: solo dígitos y puntos
ANT=<anterior>                  # la publicada ahora
PY="C:/dev/voziris/.venv/Scripts/python.exe"   # el venv del proyecto
REL="C:/dev/voziris-release-$V"     # worktree limpio de la rama release-<X.Y.Z>
BUILD="C:/dev/voziris-release/$V"   # salida de PyInstaller y del empaquetado
CREATICS="$HOME/OneDrive - Creatics Mundo Educa"
WEB="$CREATICS/KAIRIS/Web Kairis_Claude"   # el repositorio de kairis.es
COMPARTIR="C:/dev/voziris-entrega/compartir"
```

Como cualquier otra rama, el worktree va en `C:\dev\voziris-<rama>`. Las compilaciones van juntas en `C:\dev\voziris-release`, una carpeta por versión. `C:\dev\voziris-entrega` es un enlace (*junction*) a la carpeta `KAIRIS\Studio\Voziris\voziris-entrega` de OneDrive.

La web también se mudó el 21 de octubre. La carpeta de antes, `LABIAS\WEB - KAIRIS - Claude\Nueva KAIRIS`, es una copia congelada que no llega ni a GitHub ni a kairis.es: lo dice su `MUDADO.md`.

## Paso a paso

### 1. Rama y versión

Esto se hace en Git Bash, desde cualquier copia del repositorio. `git fetch` y `git worktree add` solo escriben en `.git`: no tocan los archivos de nadie ni sus cambios sin commit.

```bash
cd C:/dev/voziris
git fetch origin --tags
git worktree add --no-track -b "release-$V" "$REL" origin/main
cd "$REL" && git status -sb                  # "## release-<X.Y.Z>", sin cambios
git log --oneline "v$ANT"..origin/main       # lo que entra en esta versión
```

`--no-track` evita que la rama nueva quede siguiendo a `origin/main`. Su rama remota la fija el `git push -u` del paso 7.

La versión se cambia en dos sitios: `pyproject.toml` (`version = "<X.Y.Z>"`) y `src/voziris/__init__.py` (`__version__ = "<X.Y.Z>"`). Para comprobarlo:

```bash
cd "$REL"
grep -n '^version' pyproject.toml; grep -n '__version__ =' src/voziris/__init__.py
PYTHONPATH="$PWD/src" "$PY" -B -c "import voziris; print(voziris.__version__, voziris.__file__)"
```

Tiene que salir `<X.Y.Z>` con una ruta dentro de `C:\dev\voziris-release-<X.Y.Z>`. Si sale `C:\dev\voziris\src`, el `PYTHONPATH` no ha servido: no sigas. La única prueba que fijaba la versión ya usa `__version__` (commit `3997d10`). Si una prueba falla solo por el número de versión, es por eso.

Mira también si `pyproject.toml` trae dependencias nuevas, con `git diff "v$ANT" -- pyproject.toml`. Si las trae, instálalas en el venv con `"$PY" -m pip install "<paquete>"`. No uses `pip install -e .` desde el worktree: reengancharía al worktree el venv de la copia principal.

### 2. Notas de la release

`docs/notas-release.md` es el texto de la release, tal cual. Se parte de las notas de `<anterior>` y se cambia:

- el título, `# Voziris <X.Y.Z>`;
- «Novedades en <X.Y.Z>», con lo que salió en el `git log` del paso 1, contado para quien usa el programa;
- «Instalación → Si ya tenías la …», para cada versión que alguien pueda tener instalada (hoy, de la 0.1.0 a la última);
- «Limitaciones conocidas», si algo ha cambiado;
- las líneas de los hashes: el nombre nuevo y 64 ceros, una para el MSI y otra para el ZIP, dentro del bloque de código de la sección «Verificación del archivo» (las notas de la 0.1.3 solo traen la del ZIP: hay que añadir la del MSI).

```text
SHA-256  voziris-<X.Y.Z>-win64.msi
0000000000000000000000000000000000000000000000000000000000000000

SHA-256  voziris-<X.Y.Z>-win64.zip
0000000000000000000000000000000000000000000000000000000000000000
```

`empaquetar.py` busca exactamente `SHA-256`, dos espacios, `voziris-<dígitos y puntos>-win64.zip` (o `.msi`) y un salto de línea LF. Después sustituye los 64 caracteres de la línea siguiente. Hay que tener en cuenta dos cosas:

- **Si no encuentra el patrón, no avisa.** El paso 5 lo comprueba.
- **No cambia el nombre del archivo.** Si se queda el de la versión anterior, el hash nuevo sale con el nombre viejo.

Las notas tienen que llevar lo que pide `docs/RELEASE.md` §5:

- qué hay de nuevo;
- el hash;
- el aviso sobre el antivirus y las ventanas elevadas;
- la atribución a NVIDIA (CC-BY-4.0).

Desde que hay MSI, «Instalación» empieza por él: descargar el `.msi` y abrirlo, con el aviso de «Windows protegió su PC». El ZIP queda como la forma portable.

En la 0.1.1, la verificación encontró frases falsas en las notas, como «mientras baja el modelo puedes usar la API». Por eso el paso 6 contrasta cada frase con el código.

Si vas a cambiar `docs/LEEME-instalar.html`, `docs/GUIA-api.html`, `CONDICIONES.md`, `ATRIBUCIONES.md`, `LICENSE`, `config.ejemplo.toml` o `assets/`, hazlo ahora: todos van dentro del paquete. Tres cosas más:

- **Las condiciones están en dos sitios**, `CONDICIONES.md` y el apartado 6 de la guía, y tienen que decir lo mismo: lo vigila `tests/test_instalador_msi.py`. El asistente del MSI enseña las de `CONDICIONES.md`, que hay que aceptar para instalar. Si cambian, cambia también su fecha de revisión.
- **Las condiciones dicen qué sale del equipo y qué se guarda.** Si el código cambia algo de eso (una conexión nueva, un dato más en disco), se cambian antes de publicar. Es lo primero que revisa la verificación de textos del paso 6.2.
- **La guía de la API nombra botones y pestañas** de Ajustes y de la consola de Groq. Si cambian, se cambian sus textos y sus dibujos.

```bash
cd "$REL"
git add pyproject.toml src/voziris/__init__.py docs/notas-release.md   # y lo que hayas cambiado
git commit -m "Versión $V: notas de la release"
```

### 3. Suite y comprobaciones

```bash
cd "$REL"
PYTHONPATH="$PWD/src" "$PY" -m pytest -q --ignore=tests/test_atajos.py --ignore=tests/test_app_activa.py
PYTHONPATH="$PWD/src" "$PY" -m ruff check src tests tools
PYTHONPATH="$PWD/src" "$PY" -m mypy
```

Las dos pruebas que se saltan chocan con una Voziris abierta en la bandeja. Solo puede haber un hook de teclado de bajo nivel y un único dueño del portapapeles, y la instalada tiene los dos. Por separado, pasan. Para ejecutarlas hay que pedirle a Alfonso que cierre la suya: no se cierra por él.

### 4. Compilar, fuera de OneDrive

El árbol tiene que estar limpio, porque lo que no está en un commit no debe entrar en el paquete.

```bash
cd "$REL"
test -z "$(git status --porcelain)" && echo limpio      # si no dice «limpio», no sigas
mkdir -p "$BUILD"
git rev-parse HEAD > "$BUILD/commit-compilado.txt"
PYTHONPATH="$PWD/src" "$PY" -m PyInstaller build/voziris.spec --noconfirm \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" > "$BUILD/pyinstaller.log" 2>&1
echo "salida: $?"; tail -n 1 "$BUILD/pyinstaller.log"
grep -A3 "Module search paths" "$BUILD/pyinstaller.log"
```

Tienen que salir `salida: 0` y `Build complete!`. Las dos primeras rutas de búsqueda tienen que ser `C:\dev\voziris-release-<X.Y.Z>` y `C:\dev\voziris-release-<X.Y.Z>\src`. Si aparece antes `C:\dev\voziris\src`, faltaba `PYTHONPATH` y el paquete lleva el código de la copia principal.

### 5. Empaquetar

```bash
cd "$REL"
PYTHONPATH="$PWD/src" "$PY" tools/empaquetar.py --dist "$BUILD/dist/voziris"
```

El script hace esto, en este orden:

- **Se niega a hacer el ZIP** si:
  - faltan los runtimes de Visual C++;
  - alguna ruta interna pasa de 100 caracteres;
  - la DLL de Python no es la del Python que lo ejecuta;
  - `voziris.exe --comprobar` falla. Esta comprobación puede tardar hasta 5 minutos;
  - faltan las guías en `_internal/docs/` (las mete `build/voziris.spec`);
  - no está WiX 5 con su extensión de interfaz (salvo con `--sin-msi`, que hace solo el ZIP).
- **Escribe dentro del paquete:**
  - `Instalar Voziris.cmd`;
  - la guía `LÉEME - Instalar Voziris.html`, que es `docs/LEEME-instalar.html` con sus enlaces a la otra guía apuntando a `_internal/docs/`;
  - `_internal/voziris-versiones.txt`;
  - el manifiesto, `_internal/voziris-manifiesto.txt`.
- **Hace el MSI, `voziris-<X.Y.Z>-win64.msi`, con `tools/hacer_msi.py`** (VOZ-88). Extrae el ZIP recién hecho y empaqueta eso con la receta `build/voziris.wxs`, así que lleva lo mismo. Tarda un par de minutos. Antes de darlo por bueno lee sus tablas: mismos archivos y bytes que el ZIP, sin las acciones que lo registrarían en Windows, y con la que arranca `voziris.exe --instalar`. Con el paquete de la 0.1.3 salió de 76 MB, frente a los 99 MB del ZIP.
- **Deja `voziris-<X.Y.Z>-win64.zip`, el `.msi` y `SHA256SUMS.txt` en `$BUILD/dist`.** `SHA256SUMS.txt` lleva las dos líneas, con finales LF. Con CRLF, `sha256sum -c` falla aunque el hash sea el bueno.
- **Pone los dos hashes en `docs/notas-release.md`.**

Cada ejecución da hashes distintos, porque `voziris-versiones.txt` lleva la fecha y la hora, y cada MSI lleva un código de producto nuevo. Por eso los hashes se guardan en un commit después del último empaquetado.

```bash
cd "$BUILD/dist" && sha256sum -c SHA256SUMS.txt     # el .zip y el .msi: OK
"$BUILD/dist/voziris/voziris.exe" --version         # voziris <X.Y.Z>
stat -c %s "$BUILD/dist/voziris-$V-win64.zip"       # menos de 200 MB (la 0.1.1: 99.873.409 bytes)
stat -c %s "$BUILD/dist/voziris-$V-win64.msi"       # menos que el ZIP
cd "$REL" && git status --short                     # solo docs/notas-release.md
grep -A1 "^SHA-256  voziris-$V-win64" docs/notas-release.md   # los mismos hashes que SHA256SUMS.txt
git add docs/notas-release.md && git commit -m "Notas $V: SHA-256 de los paquetes"
```

`voziris.exe` no tiene consola, pero si se lanza desde Git Bash escribe por la tubería. `--version` termina sin abrir nada y sin tocar la Voziris que esté abierta.

### 6. Comprobar el paquete antes de publicar

**6.1. Instalación real en carpetas temporales**, con `tools/probar_instalacion.py`. Usa el instalador de verdad y el ZIP de verdad, pero lo escribe todo en carpetas temporales y en una clave de pruebas de HKCU que borra al terminar. No cierra la Voziris abierta ni toca el menú Inicio. Sin `--anterior`, prueba una instalación desde cero. Con `--anterior <ZIP>`, simula esa versión instalada con `config.toml` y un historial de 500 líneas, instala la nueva encima y comprueba que esos datos siguen igual. La carpeta extraída de la nueva trae además su propio historial de una línea: es lo que hacía perder el de la instalada hasta la 0.1.1. Sale con 0 si todo cuadra y con 1 si algo no.

Ejecútalo desde cero y encima de cada versión que alguien pueda tener instalada. Hoy son todas las publicadas (añade cada versión nueva a la lista):

```bash
cd "$REL"
PYTHONPATH="$PWD/src" "$PY" tools/probar_instalacion.py "$BUILD/dist/voziris-$V-win64.zip"
for A in 0.1.0 0.1.1 0.1.2 "$ANT"; do
  gh release download "v$A" -R alfonsosanzme/voziris -p "voziris-$A-win64.zip" -D "$BUILD/anteriores" --skip-existing
  PYTHONPATH="$PWD/src" "$PY" tools/probar_instalacion.py "$BUILD/dist/voziris-$V-win64.zip" \
    --anterior "$BUILD/anteriores/voziris-$A-win64.zip" || echo "FALLA desde $A"
done
```

No prueba `Instalar Voziris.cmd` ni `voziris.exe --instalar`: ni la ventana de progreso, ni cerrar y reabrir la Voziris abierta, ni el arranque por el acceso de Inicio. Eso va en la máquina limpia (6.3).

**6.1 bis. El MSI, sin instalar nada**, con `tools/probar_msi.py` (VOZ-88). Abre una copia del MSI en silencio (`msiexec /qn LANZAR=0`), marcada como recién descargada. Windows Installer deja el paquete en `%TEMP%\voziris-actualizacion\msi-<X.Y.Z>-<PID>` y no arranca el instalador de Voziris: no sale ninguna ventana y no se tocan la Voziris abierta, el menú Inicio ni «Aplicaciones instaladas». Comprueba que el paquete está entero (manifiesto con hashes), que ningún archivo hereda la marca de Internet, que `voziris.exe --comprobar` carga todo desde ahí y que Windows Installer no ha apuntado el producto. Al terminar borra la carpeta. Sale con 0 si todo cuadra.

```bash
cd "$REL"
PYTHONPATH="$PWD/src" "$PY" tools/probar_msi.py "$BUILD/dist/voziris-$V-win64.msi"
```

Con el paquete de la 0.1.3: 4 segundos de `msiexec`, 1183 archivos del manifiesto, y todo cuadró.

Hay dos cosas que esta prueba no ve, y son las que hay que mirar a mano antes de publicar un MSI por primera vez **(sin probar)**:

- **El asistente con sus ventanas.** Doble clic en el `.msi`: las condiciones se leen enteras y con sus tildes, no deja pulsar «Instalar» sin aceptarlas, la barra avanza y el último diálogo dice que la instalación sigue en otra ventana.
- **El relevo al instalador.** Al acabar el asistente tiene que abrirse sola la ventana «Instalando Voziris», terminar con «Voziris instalado» y, sin clave de API, preguntar si se abre la guía. El registro queda en `%LOCALAPPDATA%\Programs\Voziris\voziris-instalar.log`, también en un equipo que nunca ha tenido Voziris.

Esto instala de verdad: cierra la Voziris abierta y escribe en el registro del usuario. Se hace en un equipo o usuario de pruebas, o en el de Alfonso solo con su sí, y lo lanza él desde el Explorador (ver la tercera trampa de «Antes de empezar»). Vale el mismo truco de 6.2 bis: un paquete compilado con `__version__` en `0.0.1` deja luego volver a la publicada con «Actualizar».

**6.2. Verificación independiente.** En la 0.1.1 se hicieron tres revisiones por separado, sin que ninguna viera el trabajo de las otras. Encontraron un fallo real (el instalador pisaba el historial) y frases falsas en las notas. Una sesión de Claude las lanza como tres subagentes en paralelo; Alfonso se las pide a una sesión de Claude.

1. **El ZIP y el MSI tal como los recibe alguien.** Comprueba que:
   - los hashes cuadran con `SHA256SUMS.txt`;
   - en una carpeta nueva con el ZIP extraído, `./voziris.exe --comprobar` da una línea «bien» por cada prueba y sale con 0;
   - el ZIP trae `Instalar Voziris.cmd`, el LÉEME y el manifiesto, y dentro de `_internal/docs/`, las dos guías;
   - no trae `config.toml`, `modelos/`, `historial/` ni registros;
   - el MSI pasa `tools/probar_msi.py` (6.1 bis).
2. **La actualización** desde cada versión que pueda estar instalada, con 6.1 o con una prueba equivalente escrita a partir de `src/voziris/instalador.py`.
3. **Las notas contra el código.** Cada frase de `docs/notas-release.md`, y de `docs/LEEME-instalar.html`, `docs/GUIA-api.html` y `CONDICIONES.md` si han cambiado, se contrasta con el código y con `git diff v<anterior>..HEAD`. Lo que no se sostenga, se quita. En las condiciones, lo primero: qué sale del equipo y qué se guarda.

**6.2 bis. La actualización desde la bandeja (desde la 0.1.2).** Es la única parte que no se puede arreglar después de publicar. Si `--actualizar` de una versión falla, publicar otra no lo arregla, porque es la versión instalada la que descarga. Se prueba contra la release viva sin publicar nada:

1. Compila la versión nueva con `__version__` cambiado **solo en una copia de compilación** a una anterior a la publicada (por ejemplo `0.0.1`). Es un worktree desechable: `empaquetar.py` también escribe en sus notas, y da igual.

   ```bash
   PRUEBA="C:/dev/voziris-release/$V-prueba-0.0.1"; mkdir -p "$PRUEBA"
   cd C:/dev/voziris && git worktree add --detach "C:/dev/voziris-prueba-bandeja-$V" "release-$V"
   cd "C:/dev/voziris-prueba-bandeja-$V"
   sed -i 's/^__version__ = .*/__version__ = "0.0.1"/' src/voziris/__init__.py
   PYTHONPATH="$PWD/src" "$PY" -m PyInstaller build/voziris.spec --noconfirm \
     --distpath "$PRUEBA/dist" --workpath "$PRUEBA/work" > "$PRUEBA/pyinstaller.log" 2>&1
   PYTHONPATH="$PWD/src" "$PY" tools/empaquetar.py --dist "$PRUEBA/dist/voziris"
   ```

2. Extrae ese ZIP en una carpeta nueva e instálalo con su `Instalar Voziris.cmd`, en un equipo o usuario de Windows de pruebas, **nunca en el de Alfonso sin su sí**: la actualización cierra la Voziris abierta y escribe en el registro de su usuario. Pulsa Aceptar en el aviso final. Contesta «Sí» a la tarjeta y deja algún dictado en el historial.
3. Bandeja → «Buscar actualizaciones» → «Actualizar a la <publicada>…».
4. Comprueba lo siguiente:
   - sale la ventana de progreso, y Cancelar no deja nada;
   - si no cancelas, la bandeja se cierra y se abre la versión publicada, con el aviso «Voziris actualizado… (antes, la 0.0.1)»;
   - `config.toml`, `modelos/` e `historial/` siguen intactos;
   - `voziris-actualizar.log` y `voziris-instalar.log` están en la carpeta de la instalación;
   - en `voziris-instalar.log` no hay ningún «reintento»: `--actualizar` ya no retenía `_internal`;
   - con el aviso final de `Instalar Voziris.cmd` abierto (el del paso 2, que no hace falta cerrar antes), «Actualizar» no contesta que ya hay una en marcha.

Así se prueba el código de `--actualizar` de la versión nueva. Lo del paso 2 prueba además su instalador con una bandeja real. El instalador que ejecuta «Actualizar» es el de la **publicada**, y tiene que ser la 0.1.2 o posterior. Contra la 0.1.1 no puede terminar: su instalador no toma el mutex de instalar, así que `--actualizar` sigue vivo durante el cambio (hasta `ESPERA_INSTALADOR_S`) y le bloquea `_internal`. Esto último se dedujo del código y de lo medido; no se ejecutó. Lo único que no se puede probar antes de publicar es el cruce de los dos: el `--actualizar` de la publicada con el instalador nuevo. Se ve al actualizar el primer equipo, después de publicar.

Hay dos cosas que confunden si el instalador es el de la 0.1.2 o anterior. VOZ-85 las arregla desde la 0.1.3:

- **Una Voziris viva de la carpeta instalada impide cambiar `_internal`** (`WinError 5`, «otro programa tiene abierto un archivo»). Pasa desde unos 200 ms después de arrancar, porque queda abierto `_internal\base_library.zip`. El 31 de octubre lo provocó un duplicado sin ventana, parado en el aviso «Voziris ya está abierto». Antes de instalar, mira en el Administrador de tareas que no quede otra `voziris.exe`. Desde VOZ-85, el instalador cierra lo que siga vivo desde esa carpeta (si está transcribiendo, pregunta antes). Si otro programa lo retiene, dice cuál es, con su PID, y deja reintentar.
- **El instalador retiene su mutex hasta que se pulsa Aceptar en el aviso final**, y ese aviso puede abrirse detrás de otras ventanas. Mientras tanto, «Actualizar» contesta que ya hay una actualización en marcha y no descarga nada. Búscalo con Alt+Tab. Desde VOZ-85, el mutex se suelta antes del aviso.

**Cómo fue con la 0.1.2 (2 de noviembre).** No se podía probar antes de publicar, porque la publicada era la 0.1.1. Por decisión de Alfonso, se publicó primero y se probó justo después, en su propio equipo, de la 0.0.1 a la 0.1.2.
- Funcionó de punta a punta: descarga, SHA-256 y manifiesto correctos, y el instalador cerró la 0.0.1, cambió el programa sin reintentos y abrió la nueva.
- El programa quedó idéntico al ZIP, y `config.toml`, el modelo y el historial, intactos.
- Cancelar no se llegó a pulsar: solo lo cubren sus tests.

**Cómo fue con la 0.1.3 (3 de noviembre).** Antes de publicar, en el equipo de Alfonso y con su sí, de su 0.1.2 a la 0.0.1 de prueba y de vuelta a la 0.1.2 publicada.
- `Instalar Voziris.cmd` cerró su bandeja y abrió la nueva, sin reintentos. Con su aviso final abierto, «Actualizar» arrancó a la primera. El programa quedó idéntico al ZIP de la 0.1.2, y de sus datos solo cambió `actualizaciones.json`.
- No se pulsó Cancelar. El duplicado que se abrió a propósito ya no estaba cuando llegó el instalador.
- **El aviso final no se quedó con `WS_EX_TOPMOST`,** aunque se lo pide: ver VOZ-85 en `docs/BACKLOG.md`. Las notas ya no prometen «encima de las demás ventanas». Para medirlo desde el primer momento, hay que mirar sus ventanas antes de que salga: `GetWindowLong` con `GWL_EXSTYLE`, solo sobre las de `voziris.exe`.
- La revisión de las notas destapó un efecto de VOZ-86: con lo de serie, cada dictado enseñaba «Sin clave de API: transcrito en local». Se arregló antes de publicar (VOZ-87). **Cuando un arreglo hace visible algo que no se veía, mira qué más se ve.**

**6.3. Máquina limpia.** La lista a mano está en `docs/RELEASE.md` §4 y no se repite aquí. Incluye Windows sin Python, un USB, el arranque en menos de 3 segundos y Control inteligente de aplicaciones. Más detalles en el último apartado de este documento.

Si algo falla, el arreglo va en la rama: commit y vuelta al paso 3. Hay que volver a compilar y a empaquetar, y el hash cambia.

Si solo cambia el LÉEME, bastaba con repetir el paso 5 sobre el mismo build, porque lo copia `empaquetar.py`. Así se hizo en la 0.1.2. **Desde VOZ-88 ya no basta:** las guías van también dentro de `_internal/docs/`, y esas las pone PyInstaller. Si cambia una guía, se vuelve a compilar (paso 4).

### 7. PR y fusión

```bash
cd "$REL"
git push -u origin "release-$V"
gh pr create --base main --head "release-$V" --title "Versión $V" --body-file "$BUILD/pr.md"
```

El cuerpo del PR se escribe en `$BUILD/pr.md`, con las mismas secciones que el #40: «Qué es» y «Verificación». Va en un archivo porque el filtro de órdenes del entorno de Claude Code ha bloqueado cuerpos en línea que tenían barras sueltas.

Antes de fusionar, comprueba que `main` no se ha movido y que el ZIP sale del último código:

```bash
cd "$REL" && git fetch origin
git log --oneline HEAD..origin/main          # vacío; si no, rebase sobre origin/main y vuelta al paso 3
git diff --stat "$(cat "$BUILD/commit-compilado.txt")" HEAD -- . ':(exclude)docs/notas-release.md'   # vacío
gh pr merge "release-$V" --squash
```

`main` no está protegida, así que GitHub fusiona aunque la rama vaya por detrás. En ese caso, el commit fusionado llevaría código que el ZIP no tiene.

La segunda orden detecta los cambios hechos después de compilar. Si solo tocan cosas que no van en el ZIP (el README, `docs/` salvo el LÉEME, `tests/`), no hace falta recompilar. Si toca el LÉEME, comprueba que el del ZIP es el de la rama.

Igual que en la 0.1.1, la fusión se hace sin `--delete-branch`.

### 8. Crear la release

```bash
cd "$REL" && git fetch origin
git diff --stat "$(cat "$BUILD/commit-compilado.txt")" origin/main -- . ':(exclude)docs/notas-release.md'   # vacío
git diff --stat "release-$V" origin/main      # vacío
git ls-remote --tags origin "v$V"             # vacío: la etiqueta aún no existe
MAIN=$(git rev-parse origin/main)
{ cat docs/notas-release.md; printf '\nCompilado desde `main` en el commit `%s`.\n' "${MAIN:0:7}"; } > "$BUILD/notas-v$V.md"
gh release create "v$V" "$BUILD/dist/voziris-$V-win64.msi" "$BUILD/dist/voziris-$V-win64.zip" \
  "$BUILD/dist/SHA256SUMS.txt" \
  -R alfonsosanzme/voziris --target "$MAIN" --title "Voziris v$V" \
  --notes-file "$BUILD/notas-v$V.md" --latest
```

- **Los tres archivos, siempre.** El MSI es lo que descarga quien llega de nuevas; el ZIP y `SHA256SUMS.txt` son los que usan las instaladas para actualizarse.

- **`--target` lleva el SHA completo.** Con el SHA abreviado, GitHub contesta 422 «target_commitish is invalid», como pasó en la 0.1.1.
- **`gh` crea la etiqueta `v<X.Y.Z>` sobre ese commit.** Las etiquetas de la 0.1.0 y de la 0.1.1 se crearon así, y son ligeras, no anotadas.
- **Las notas publicadas son `docs/notas-release.md` más una línea**, «Compilado desde `main` en el commit `…`.». Así se publicaron las de la 0.1.1.

### 9. Comprobar la descarga pública

```bash
URL="https://github.com/alfonsosanzme/voziris/releases/download/v$V/voziris-$V-win64.zip"
curl -sIL "$URL" | grep -i -E '^HTTP|^content-length'   # 302 y, al final, 200 con el tamaño del paso 5
curl -sL "$URL" | sha256sum                              # el hash de SHA256SUMS.txt
gh release view "v$V" -R alfonsosanzme/voziris --json assets --jq '.assets[] | "\(.name) \(.size) \(.digest)"'
gh release list -R alfonsosanzme/voziris                 # v<X.Y.Z> como «Latest»; las anteriores siguen
```

`digest` es el SHA-256 que calcula GitHub del asset subido, y también tiene que coincidir.

Lo mismo con el MSI **(sin probar)**:

```bash
URL_MSI="https://github.com/alfonsosanzme/voziris/releases/download/v$V/voziris-$V-win64.msi"
curl -sIL "$URL_MSI" | grep -i -E '^HTTP|^content-length'
curl -sL "$URL_MSI" | sha256sum                          # el hash de SHA256SUMS.txt
```

Comprueba también que Voziris la ve como la verán los equipos (VOZ-82). Desde el worktree, con `PYTHONPATH=src`:

```bash
PYTHONPATH=src "$PY" -c "from voziris import actualizaciones as a; n = a.consultar('$ANT'); print(n, n.instalable if n else '')"
```

Tiene que salir `Novedad(version='<X.Y.Z>', …)` con las URL de esta release, y `True`. Con `False`, a la release le falta el ZIP o `SHA256SUMS.txt` con su nombre exacto. Comprobado con la 0.1.2, preguntando desde la 0.1.1.

**Opcional: mandar los archivos a Microsoft para que los analice (sin probar).** Voziris no está firmado, y Defender marca a veces como sospechosos los `.exe` de PyInstaller. Microsoft admite envíos de desarrolladores en https://www.microsoft.com/en-us/wdsi/filesubmission («Software developer»): se sube el archivo y, si lo había marcado, se indica el nombre de la detección. No tiene coste ni plazo garantizado. Lo hace Alfonso con su cuenta; una sesión de Claude no sube nada por él.

### 10. La web: el enlace de kairis.es

El cambio es pequeño: en `data/blog.json`, post `voziris`, objeto `descarga`, se cambian tres campos. El resto del post no se toca, salvo que Alfonso lo pida.

```json
"descarga": {
  "url": "https://github.com/alfonsosanzme/voziris/releases/download/v<X.Y.Z>/voziris-<X.Y.Z>-win64.zip",
  "version": "v<X.Y.Z>",
  "peso": "<N> MB",
  …
}
```

`peso` va en MiB redondeados: 99.873.409 bytes son «95 MB». Esta orden lo calcula:

```bash
echo $(( ( $(stat -c %s "$BUILD/dist/voziris-$V-win64.zip") + 524288 ) / 1048576 )) MB
```

Revisa también la `nota` del mismo objeto. `grep -n "0\.1\." "$WEB/data/blog.json"` encuentra las versiones que se mencionan en el texto.

**Desde la primera versión con MSI, el botón apunta al `.msi` (sin probar):** es lo que tiene que descargar quien llega de nuevas, porque no hay nada que extraer. La `url` y el `peso` son los del MSI, y la `nota` y el texto del post dejan de hablar de extraer un ZIP: dicen que se abre con doble clic y cómo pasar el aviso de «Windows protegió su PC». El ZIP sigue en la página de la release para quien lo quiera portable. Es un cambio de texto del post, no solo del enlace: enséñaselo a Alfonso antes.

**Quién lo hace.** Tú o una sesión que abras en `$WEB`, siguiendo el `CLAUDE.md` de ese repositorio. La web no tiene sesión fija: «WEB Kairis», que cambió el enlace de la 0.1.1 (commit `a3229f7`), está archivada y su carpeta es la de antes, así que no le pases encargos. El enlace de la 0.1.2 (`cd886a5`) se cambió desde la sesión principal de Voziris, también con el OK de Alfonso para desplegar.

Si abres una sesión en `$WEB`, este es el encargo:

> Sigue la skill `anadir-blog`. En `data/blog.json`, post `voziris`, objeto `descarga`, pon url `<URL>`, version `v<X.Y.Z>` y peso `<N> MB`. No toques nada más del post. Antes, comprueba que la URL responde 200 con `<bytes>` bytes y SHA-256 `<hash>`. Después:
>
> 1. Ejecuta `node scripts/build-blog.mjs` y haz commit solo de `data/blog.json` y `blog/voziris.html`.
> 2. Ejecuta `node scripts/build-sitemap.mjs`. Si cambia `sitemap.xml`, haz otro commit.
> 3. `git push`.
>
> Despliega solo con el sí de Alfonso.

Si lo haces tú:

```bash
cd "$WEB"
git fetch origin && git status -sb      # anota lo que ya está modificado: no es tuyo, ni se añade ni se descarta
# … edita data/blog.json …
node scripts/build-blog.mjs
git status --short                      # lo nuevo respecto a lo anotado: data/blog.json y blog/voziris.html
git add data/blog.json blog/voziris.html
git commit -m "Blog: Voziris $V"
node scripts/build-sitemap.mjs
git status --short sitemap.xml          # si ha cambiado:
git add sitemap.xml && git commit -m "Sitemap: lastmod de blog/voziris.html"
git push
```

- **Solo debe cambiar `blog/voziris.html`.** Según `build-blog.mjs`, el bloque de descarga solo aparece en esa página. Si cambian más archivos del blog, es que algo ya estaba desincronizado: para y pregunta.
- **El sitemap va después del commit.** Su `lastmod` es la fecha del último commit de cada página, así que no cambia si el commit es del mismo día que el último cambio de la página.
- **Nunca `git add -A`.** El 7 de octubre había modificado un `listadecompra/index.php` que no es de este trabajo.

**El deploy, solo con el permiso explícito de Alfonso en el chat:**

```bash
cd "$WEB"
git log --oneline -10        # ¿puede salir a producción todo lo que hay en HEAD?
bash .deploy/deploy.sh
```

Antes de lanzarlo, conviene saber cómo funciona `deploy.sh`:

- **Sube `git archive HEAD` entero, no solo tu commit.** Si HEAD lleva commits de otra sesión que aún no se han desplegado o no se han subido a `origin`, tu deploy también los publica. No despliegues hasta que su autor o Alfonso digan que pueden salir.
- **Antes de subir, valida** el blog, el sitemap, las imágenes, la cabecera y el pie, las reseñas y el versionado de CSS y JS. Si se para por algo que no es tuyo, detente y avisa a Alfonso.
- **No borra nada en el servidor.** Copia antes cada archivo que va a sobrescribir en `~/tmp/predeploy-<fecha>`.

Después del deploy, comprueba la página publicada:

```bash
curl -sI https://kairis.es/blog/voziris.html | head -1                              # 200
curl -s https://kairis.es/blog/voziris.html | grep -o 'releases/download/[^"]*'     # v<X.Y.Z>/voziris-<X.Y.Z>-win64.zip
```

### 11. compartir/

Lo anterior pasa a una subcarpeta `anterior-<fecha>`, con la fecha del ZIP que se aparta (así están nombradas las dos que hay). Nunca se borra.

```bash
cd "$COMPARTIR"
DEST="anterior-$(date -r "voziris-$ANT-win64.zip" +%F)"
mkdir "$DEST"                 # si ya existe, añade un sufijo (hay una «anterior-2026-10-07-prueba-voz81»)
mv voziris-*-win64.* SHA256SUMS.txt "LÉEME - Instalar Voziris.html" "$DEST/"
cp "$BUILD/dist/voziris-$V-win64.msi" "$BUILD/dist/voziris-$V-win64.zip" "$BUILD/dist/SHA256SUMS.txt" \
  "$BUILD/dist/voziris/LÉEME - Instalar Voziris.html" .
sha256sum -c SHA256SUMS.txt   # el .msi y el .zip: OK
```

Para pasarlo a mano a alguien, basta el `.msi`. El LÉEME de esta carpeta enlaza la guía de la API dentro del ZIP (`_internal/docs/`): suelto, ese enlace no abre nada, y a quien instala con el MSI las dos guías le salen en el menú de la bandeja → Ayuda.

### 12. Actualizar los equipos

Hay que actualizar el tuyo y avisar a quien lo use: está en el apartado siguiente. Una sesión de Claude no ejecuta `Instalar Voziris.cmd` ni `--instalar` en el equipo de Alfonso sin su sí, porque cierran su Voziris y escriben en su registro.

Al terminar, puedes quitar el worktree con `git worktree remove "$REL"`. `$BUILD` ya no hace falta cuando el ZIP está en la release y en `compartir/`.

## Actualizar los equipos

### Con el instalador (desde la primera versión con MSI)

Descarga `voziris-<X.Y.Z>-win64.msi`, de la release o de `compartir/`, y ábrelo con doble clic. No hay que desbloquear ni extraer nada. Tras aceptar las condiciones, deja el paquete en una carpeta temporal y arranca `voziris.exe --instalar`: desde ahí todo es igual que en «Si Voziris está instalada», más abajo, y se conservan `config.toml`, `modelos/` e `historial/`.

- Vale para instalar de nuevas y para actualizar cualquier versión instalada, porque quien instala es el instalador de la versión nueva.
- **No aparece en «Aplicaciones instaladas» como algo aparte**, ni Windows Installer lo recuerda: abrirlo otra vez es instalar otra vez. La entrada que hay es la de Voziris.
- La carpeta temporal (`%TEMP%\voziris-actualizacion\msi-…`, unos 230 MB) la borra la siguiente Voziris que arranque, pasada una hora, igual que la de «Actualizar».
- Si Windows enseña «Windows protegió su PC»: «Más información» → «Ejecutar de todas formas». Con Control inteligente de aplicaciones activado puede bloquearlo sin dar opción: entonces, el ZIP sin instalar (más abajo).

### Con el ZIP

Hay tres pasos comunes a todos los casos:

1. **Consigue el ZIP**, de la release o de `compartir/`. Si el antivirus protesta, comprueba el hash. En PowerShell, `Get-FileHash .\voziris-<X.Y.Z>-win64.zip` tiene que dar el de las notas, en mayúsculas.
2. **Desbloquéalo antes de extraer:** botón derecho → Propiedades → Desbloquear. Así Windows no pregunta por cada archivo al ejecutarlo.
3. **Extráelo entero en una carpeta nueva y de ruta corta**, nunca encima de la versión anterior. Son unos 1.200 archivos, así que espera a que termine.
   - Si «Extraer todo» dice que una ruta es demasiado larga, pulsa Cancelar, no Omitir. Omitir deja el programa a medias.
   - Si la ruta de la carpeta de `voziris.exe` pasa de unos 180 caracteres, Windows deja de cargar sus archivos.
   - En tu equipo, mejor fuera de OneDrive (por ejemplo, en Descargas). OneDrive retiene archivos (por eso se compila fuera de él) y sincronizaría el millar sin necesidad.

### Si Voziris está instalada (menú Inicio y «Aplicaciones instaladas»)

Haz doble clic en el `Instalar Voziris.cmd` de la carpeta nueva. Da igual que vengas de la 0.1.0 o de la 0.1.1, porque se ejecuta el instalador de la versión nueva. Según `instalador.py` y `_instalar_cli`, esto es lo que hace:

1. **Comprueba la carpeta extraída** contra el manifiesto. Si falta algo, no toca nada y dice qué archivo es.
2. **Prepara una copia aparte** en `%LOCALAPPDATA%\Programs\Voziris.nuevo`. Le quita la marca de Internet y la arranca con `--comprobar`.
3. **Solo entonces cambia el programa:** cierra la Voziris abierta y sustituye `voziris.exe` y `_internal`. Si algo falla, la versión anterior se queda como estaba y se vuelve a abrir. Desde VOZ-85, también cierra lo que siga vivo desde esa carpeta y, si hay algo transcribiendo, pregunta antes. Si otro programa lo impide, dice cuál es y deja reintentar sin volver a copiar.
4. **Conserva los datos.** Mantiene `config.toml`, `modelos/` e `historial/` de la instalación, y de la carpeta extraída solo copia lo que falte.
5. **Rehace lo demás:**
   - el acceso del menú Inicio, y el de inicio de sesión si existía;
   - el botón derecho «Transcribir con Voziris»;
   - la versión que aparece en «Aplicaciones instaladas».

   Después abre la instalada y avisa con «Voziris instalado». Desde la 0.1.2, si había otra versión instalada, el aviso es «Voziris actualizado».

Desde la 0.1.2, bandeja → «Actualizar a la X.Y.Z…» hace lo mismo sin tocar nada a mano. Descarga el ZIP y `SHA256SUMS.txt` de la release, comprueba el hash y el manifiesto, lo descomprime en `%TEMP%\voziris-actualizacion` y ejecuta el `--instalar` de la versión nueva. La carpeta temporal la borra la siguiente Voziris que arranque, pasada una hora. Lo que pasa queda en `voziris-actualizar.log` y en `voziris-instalar.log`, junto al `voziris.log` de la instalación, y el diagnóstico recoge los dos.

No hace falta cerrar Voziris antes, porque el instalador la cierra justo antes del cambio. Con el instalador de la 0.1.2 o anterior, si dice «otro programa tiene abierto un archivo», mira en el Administrador de tareas si queda otra `voziris.exe`: basta una sin ventana para bloquearlo. Su aviso final, además, puede abrirse detrás de otras ventanas. Para comprobar la versión, en Git Bash: `"$LOCALAPPDATA/Programs/Voziris/voziris.exe" --version`. También se ve en Configuración → Aplicaciones → Voziris. Después, la carpeta extraída ya no hace falta.

### Si se usa sin instalar y va a seguir así

1. Cierra la versión vieja: bandeja → Salir.
2. **Antes de abrir la nueva**, copia `config.toml`, `modelos/` e `historial/` de la carpeta vieja junto al `voziris.exe` nuevo. Si abres antes la nueva, se crea su propio `config.toml` a partir del ejemplo.
3. Abre el `voziris.exe` nuevo.
4. Si arrancaba con Windows, ve a Ajustes, desmarca «Arrancar con Windows» y pulsa Guardar; luego márcalo y pulsa Guardar otra vez. El acceso de inicio de sesión sigue apuntando a la carpeta vieja, y Voziris solo lo crea si no existe (`Bandeja.configurar_arranque`). Sin este paso, al iniciar sesión se abriría la versión vieja.
5. Si usabas el botón derecho del Explorador, ejecuta `voziris.exe --menu-contextual` desde la carpeta nueva. Así se registra de nuevo con la ruta nueva.

### Si se usa sin instalar y se quiere instalar

Copia esas tres cosas junto al `voziris.exe` nuevo y ejecuta `Instalar Voziris.cmd`. La instalación se las lleva, y el arranque con Windows pasa a la copia instalada.

### Con Control inteligente de aplicaciones (Windows 11)

- **Cómo saber si está activado.** En PowerShell: `Get-ItemPropertyValue HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy VerifiedAndReputablePolicyState`. Los valores son 1 (activado), 2 (evaluación: solo observa) y 0 (desactivado). Es el mismo valor que lee `integridad.control_inteligente()` y que recoge el diagnóstico.
- **Una versión nueva puede quedar bloqueada aunque la anterior no lo estuviera.** Windows decide archivo por archivo según su reputación, y cada compilación son archivos que nunca ha visto. El paquete no está firmado.
- **Si lo bloquea al instalar,** el instalador lo detecta y no instala nada. Dice «Windows no deja ejecutar Voziris desde la carpeta de instalación…», y la versión anterior sigue funcionando. A partir de ahí, la carpeta extraída es su Voziris: se usa sin instalar, como en los casos de arriba. Para tenerla a mano, botón derecho en `voziris.exe` → Anclar a Inicio.
- **Si la instalada es una 0.1.0 y se bloquea:** esa versión copiaba la marca de Internet. El LÉEME, en «Si Windows lo bloquea», punto 2, trae las dos órdenes `Unblock-File` de PowerShell.
- **Desactivarlo también lo resuelve,** pero rebaja la protección de todo el equipo. Lo decide quien lo usa.

### Tu equipo

Cuando hayas comprobado la descarga pública (paso 9), usa el ZIP de la release y no el de `$BUILD`. Así pruebas lo mismo que van a recibir los demás. Sigue el caso que te toque y comprueba la versión.

### El equipo de los demás

Quien tenga la 0.1.2 o posterior y aceptara la comprobación recibe el aviso en menos de un día. Quien tenga la 0.1.1 o anterior, o dijera que no, no se entera solo. A quien sepas que lo usa, mándale algo como esto (el mensaje lo envías tú):

> Hay versión nueva de Voziris, la <X.Y.Z>: <una línea con lo que trae>. Se descarga aquí: <URL de la release> (el SHA-256 está en las notas).
>
> - Si lo tienes instalado: descarga el archivo `.msi` y ábrelo con doble clic. Se conservan tu configuración, el modelo y el historial.
> - Si lo usas sin instalar: descarga el ZIP, extráelo en una carpeta nueva, cierra el viejo, copia `config.toml`, `modelos` e `historial` a la carpeta nueva y abre el nuevo.
> - Si Windows lo bloquea, mira el apartado «Si Windows lo bloquea» del LÉEME.
>
> Si algo falla, mándame el diagnóstico (bandeja → Guardar diagnóstico…).

## Si algo sale mal

### Antes de publicar

No hay nada que deshacer fuera de la rama: arreglo, commit y vuelta al paso 3. Si el PR ya se fusionó pero aún no hay release, el arreglo va en otro PR y se repite desde el paso 3 con la misma versión, porque con ese número todavía no se ha publicado nada.

Si falla `gh release create`, hay dos casos:

- **Con el SHA abreviado** (error 422), no crea nada.
- **Si falla a mitad de la subida**, mira qué ha quedado con `gh release list` y `git ls-remote --tags origin "v$V"`. **(Sin probar)**: un borrador no es público, así que se puede borrar con `gh release delete "v$V" -R alfonsosanzme/voziris --yes` antes de repetir. Una release ya publicada no se borra (ver abajo).

### En GitHub, con la release ya publicada

**(Sin probar: aún no ha hecho falta.)** Las opciones están comprobadas en `gh release edit --help` (gh 2.96). En este orden:

1. **Primero la web** (ver abajo), para que el blog vuelva a ofrecer la versión anterior.
2. `gh release edit "v$ANT" -R alfonsosanzme/voziris --latest`: la anterior vuelve a ser «Latest». El README manda a la página de releases.
3. `gh release edit "v$V" -R alfonsosanzme/voziris --prerelease --notes-file <las notas con un aviso arriba>`. La release se marca, no se borra: hay quien ya la ha descargado y necesita el hash y las notas.
4. **El arreglo sale como la versión siguiente.** Nunca se vuelve a subir el ZIP ni se mueve la etiqueta.

Con las actualizaciones desde la bandeja (0.1.2 en adelante), los pasos 2 y 3 son además los que paran el reparto. `releases/latest` deja de dar la versión mala, así que nadie más la descarga. Si alguien ya había recibido el aviso, la entrada del menú le dura hasta la consulta del día siguiente. Si la pulsa antes, `--actualizar` vuelve a preguntar, ve la anterior y dice «Voziris está al día». A quien ya la instaló no se le baja de versión: tiene que instalar a mano el ZIP de la anterior (ver «En un equipo»).

### En la web

- **La vía rápida, en el servidor:** restaurar solo `blog/voziris.html` desde la copia que hizo ese deploy. La copia guarda todos los archivos que el deploy sobrescribió, también los de otros: restaurarla entera desharía también su trabajo. Esto se hace con permiso de Alfonso, desde `$WEB`. **(Sin probar.)** Las rutas salen de `deploy.sh`: el docroot es `htdocs/kairis.es` y la copia está en `~/tmp/predeploy-<fecha>`, con las mismas rutas relativas.

  ```bash
  cd "$WEB"
  eval "$(grep -E '^(KEY|HOST)=' .deploy/deploy.sh)"     # la clave y el servidor del deploy; no los copies a ningún sitio
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'ls -1d ~/tmp/predeploy-* | tail -3'
  ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" 'cp -p ~/tmp/predeploy-<STAMP>/blog/voziris.html ~/htdocs/kairis.es/blog/voziris.html'
  ```

- **Y siempre, en git,** como explica `.deploy/README.md` en «Rollback»:
  1. `git revert` de los commits del paso 10 (el del blog y el del sitemap).
  2. `node scripts/build-sitemap.mjs`, y commit si cambia.
  3. `git push`.
  4. Deploy, con permiso.

  Como el deploy no borra nada en el servidor, al revertir se reescriben los mismos archivos con el contenido anterior.

### En un equipo

Volver a la versión anterior es instalar su ZIP, que sigue en su release.

- **Nunca con «Desinstalar».** Borra la carpeta de la instalación entera, con el modelo y el historial (`desinstalar()`; también lo dice el LÉEME en §5).
- **Si estaba instalada:** descarga el ZIP de `v<anterior>`, desbloquéalo, extráelo en una carpeta nueva y ejecuta su `Instalar Voziris.cmd` **sin abrir antes su `voziris.exe`**. Ningún instalador comprueba la versión, así que se puede instalar una anterior. Lo que se conserva depende del instalador de esa versión:
  - **0.1.1** (la vuelta desde la 0.1.2): hace lo mismo que al actualizar. Cambia solo `voziris.exe` y `_internal`, comprueba la copia antes del cambio y conserva `config.toml`, `modelos/` e `historial/`.
  - **0.1.0** (está en `git show v0.1.0:src/voziris/instalador.py`):
    - Cierra la Voziris abierta al empezar.
    - Borra `_internal` y lo vuelve a copiar sin comprobar nada. Si falla a medias, la instalada queda rota y hay que repetir.
    - Conserva el `config.toml` instalado.
    - Copia `modelos/` e `historial/` de la carpeta extraída **encima** de los instalados y pisa los archivos con el mismo nombre. Una carpeta recién extraída no los tiene. Pero si antes se abrió su `voziris.exe`, su `historial/dictados.jsonl` de una línea sustituye al de meses: es el fallo que corrigió la 0.1.1.
    - Copia la marca de «descargado de Internet». Con Control inteligente de aplicaciones activado, hay que usar `Unblock-File`, como explica el LÉEME.
- **Si se usa sin instalar:** cierra la nueva y abre la vieja, si se guardó. Después, el arranque con Windows (desmarcar y volver a marcar) y `--menu-contextual` desde la carpeta vieja, igual que al actualizar.
- **Datos:**
  - Un `config.toml` con opciones que la versión vieja no conoce no la rompe: las ignora con un aviso (`_desconocidas` en `config.py`).
  - Antes de mandar a alguien a una versión anterior, revisa `git diff --stat v<anterior> v<X.Y.Z> -- src/voziris/config.py src/voziris/historial.py src/voziris/pendientes.py src/voziris/grabaciones.py`. Si alguno cambia, comprueba que la anterior lee lo que escribe la nueva.
  - Entre la 0.1.0 y la 0.1.1 no cambió ninguno de esos archivos.

## Lista de comprobación final

- [ ] La versión <X.Y.Z> está en `pyproject.toml` y en `__init__.py`, y `import voziris` desde el worktree la devuelve.
- [ ] Las notas llevan:
  - [ ] novedades;
  - [ ] «Si ya tenías…» para cada versión publicada;
  - [ ] limitaciones;
  - [ ] el aviso del antivirus y de las ventanas elevadas;
  - [ ] la atribución a NVIDIA.

  Cada frase está contrastada con el código.
- [ ] La suite (sin las dos pruebas de la bandeja), `ruff` y `mypy` están en verde.
- [ ] El paquete está compilado desde un árbol limpio, en `C:\dev` (fuera de OneDrive y de `%LOCALAPPDATA%`), y el worktree es lo primero en las rutas de PyInstaller.
- [ ] `empaquetar.py` ha terminado sin errores y ha dejado el ZIP y el MSI, `sha256sum -c` da OK en los dos y `--version` dice <X.Y.Z>.
- [ ] Los dos hashes están en un commit, en las notas, cada uno junto al nombre de su paquete.
- [ ] `tools/probar_instalacion.py` pasa desde cero y encima de cada versión publicada, `tools/probar_msi.py` pasa con el MSI, y las tres verificaciones independientes no dejan nada pendiente.
- [ ] El asistente del MSI y su relevo al instalador se han visto con ventanas (6.1 bis), o Alfonso ha decidido no exigirlo.
- [ ] Las condiciones (`CONDICIONES.md` y el apartado 6 de la guía) siguen siendo verdad con el código de esta versión.
- [ ] La prueba de «Actualizar» desde la bandeja (6.2 bis) ha salido bien contra la versión publicada.
- [ ] La lista a mano de `docs/RELEASE.md` §4 está hecha, o Alfonso ha decidido no exigirla.
- [ ] El PR está fusionado con squash, y `main` coincide con lo compilado (salvo las notas).
- [ ] La release `v<X.Y.Z>`:
  - [ ] tiene el MSI, el ZIP y `SHA256SUMS.txt`;
  - [ ] apunta al SHA completo de `main`;
  - [ ] es «Latest»;
  - [ ] la anterior sigue publicada.
- [ ] La descarga pública da 200, el tamaño y el SHA-256 son correctos, y el `digest` de GitHub coincide.
- [ ] El blog:
  - [ ] url, version y peso cambiados;
  - [ ] commit solo de esas rutas;
  - [ ] sitemap;
  - [ ] push;
  - [ ] deploy con el OK de Alfonso;
  - [ ] en producción ya sale el enlace nuevo.
- [ ] `compartir/` tiene el ZIP, `SHA256SUMS.txt` y el LÉEME nuevos, lo anterior está en `anterior-<fecha>` y `sha256sum -c` da OK.
- [ ] Tu equipo está en <X.Y.Z> y se ha avisado a quien lo usa.

## Relación con `docs/RELEASE.md`

`docs/RELEASE.md` sigue siendo la referencia para la verificación a mano en una máquina limpia (§4), que aquí no se repite. En lo demás contradice este documento en varios puntos, y en caso de duda manda este:

- **§1 (y también la sección «Empaquetado» del README y la cabecera de `build/voziris.spec`)** compila con `pyinstaller … --distpath build/dist --workpath build/work`.
  - Eso compila dentro del repositorio, en la copia principal. Esa copia puede ir por detrás de `origin/main`, y PyInstaller empaqueta lo que hay en disco. Además, tiene que quedarse en `main` y sin cambios, porque las sesiones de Claude se abren en ella.
  - `pyinstaller` a secas, sin `PYTHONPATH`, coge el `src/` de la copia principal (ver «Antes de empezar»).
  - Aquí se usa `"$PY" -m PyInstaller`, con salida en `$BUILD`.
- **§2** tiene tres problemas:
  - Usa `banco\muestra-08.wav` y `prueba de voz\nota.m4a`, que están en `.gitignore` y no existen en un worktree. Las grabaciones de prueba están en `voziris-entrega\datos-pruebas`, fuera del repositorio.
  - `voziris.exe --salir` cierra la Voziris que esté abierta, también la que Alfonso tiene instalada.
  - `voziris.exe --config …` sin `--archivo` no arranca si ya hay otra Voziris abierta, porque solo admite una instancia.
- **§3** ejecuta `python tools/empaquetar.py` sin `--dist`, que da por hecho `build/dist/voziris`, y dice que `SHA256SUMS.txt` queda en `build/dist`. Con `--dist`, queda junto a la carpeta indicada, en `$BUILD/dist`.
- **§5** crea primero una etiqueta anotada (`git tag -a` y `git push`) y después ejecuta `gh release create` sin `--target`, con `docs/notas-release.md` tal cual.
  - Con la fusión con squash, el HEAD de la rama de la release no está en `main`, así que la etiqueta quedaría fuera de `main`.
  - En la 0.1.1, la etiqueta la creó `gh` sobre el SHA completo de `main`, y es ligera.
  - Las notas llevaron además la línea «Compilado desde `main`…», y se usó `--latest`.
- **§5 dice «Solo después del punto 4».** De la 0.1.1 consta lo que hacen aquí los pasos 3 a 9, pero no consta la verificación en máquina limpia. Alfonso decide si se exige antes de cada release.
