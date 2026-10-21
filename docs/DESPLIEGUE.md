# Desplegar una versión de Voziris

Este documento explica cómo publicar una versión nueva de Voziris y cómo hacer que llegue a quien lo usa. Se escribió después de publicar la 0.1.1, el 7 de octubre de 2026 (PR #40, con `main` en `149936c`). Está comprobado contra el código de esa versión y sus órdenes son las que funcionaron entonces. Lo que no se ha ejecutado nunca lleva la marca **(sin probar)**.

En todo el documento, `<X.Y.Z>` es la versión nueva y `<anterior>` la que está publicada ahora (por ejemplo, 0.1.2 y 0.1.1).

## Qué significa desplegar

Desplegar Voziris es llegar a tres sitios. Con el primero no basta.

1. **La release `v<X.Y.Z>` de GitHub**, en `alfonsosanzme/voziris`, que es público. Lleva el ZIP, `SHA256SUMS.txt` y las notas. Todo lo demás enlaza aquí.
2. **El blog de kairis.es.** El botón de descarga de https://kairis.es/blog/voziris.html apunta al ZIP de una release concreta. Mientras no se cambie, sigue ofreciendo la versión anterior.
3. **Quien ya lo usa.** Hasta la 0.1.1, Voziris no busca versiones nuevas: cada equipo se actualiza a mano y a los demás hay que avisarles. Para pasar el ZIP a mano está `voziris-entrega/compartir/`. Desde la 0.1.2 (VOZ-82), las copias cuyo usuario aceptó la pregunta del primer arranque consultan `releases/latest` una vez al día y avisan; la instalada se actualiza desde la bandeja, con «Actualizar a la X.Y.Z…».

Hay reglas que no cambian:

- **Nunca se sobrescribe un asset publicado ni se reutiliza un número de versión.** Si algo sale mal, se publica la versión siguiente. Con el mismo nombre de archivo, el hash publicado y las cachés dejarían de cuadrar.
- **La release anterior se queda.** Es el camino de vuelta para quien lo necesite.
- **Publicar como «Latest» es repartir.** Desde la 0.1.2, en menos de un día las Voziris con la comprobación activada avisan de la versión nueva. Lo que no esté probado no se publica como «Latest»: un borrador o una *prerelease* no se ofrece a nadie. Para que la puedan instalar desde la bandeja, la etiqueta es `v<X.Y.Z>` y los assets se llaman exactamente `voziris-<X.Y.Z>-win64.zip` y `SHA256SUMS.txt`. Con otros nombres, avisan pero mandan a la página.
- **La web solo se despliega con el permiso explícito de Alfonso.** Tampoco se despliega si hay trabajo de otros sin subir (paso 10).
- **Una sesión de Claude no cierra ni relanza la Voziris de la bandeja.** Tampoco toca `%LOCALAPPDATA%\Programs\Voziris`, el menú Inicio ni el registro. La excepción es el paso 12, y solo con el sí de Alfonso. Ojo: `voziris.exe --instalar` y `voziris.exe --salir` cierran la Voziris que esté abierta, sea cual sea.

## Antes de empezar

Todas las órdenes son de **Git Bash**, salvo dos de PowerShell que van marcadas.

| Requisito | Cómo comprobarlo | Por qué |
|---|---|---|
| `gh` con la sesión de `alfonsosanzme` | `gh auth status` | Crea el PR y la release. |
| El Python 3.13 del venv del proyecto, no un 3.14 | `"$PY" --version` → `3.13.x`; `"$PY" -m PyInstaller --version` → `6.x` | `pyproject.toml` excluye la 3.14. Además, `empaquetar.py` se niega si la `python3XX.dll` del paquete no es la del Python que lo ejecuta. |
| `node` | `node --version` | Genera el blog. |
| Unos 1,5 GB libres en `%LOCALAPPDATA%\Temp` | — | La compilación ocupa unos 370 MB. La prueba de actualización extrae paquetes de unos 230 MB y los instala. |
| Compilar **fuera de OneDrive** | Las rutas de abajo | OneDrive impide borrar `build/dist`, y PyInstaller necesita vaciar esa carpeta. |

Hay dos trampas, las dos comprobadas:

- **El venv vive en la copia principal y arrastra su código.**
  - El venv está en `voziris-entrega/voziris/.venv` y tiene Voziris instalado en modo editable, apuntando al `src/` de la copia principal.
  - Esa copia puede ir por detrás de `main` o tener cambios sin commit de otra sesión. Sin `PYTHONPATH`, Python y PyInstaller cogen ese código.
  - El 7 de octubre, `import voziris` sin `PYTHONPATH` daba la 0.1.0.
  - En el registro de PyInstaller de la 0.1.1, el `pathex` del `.spec` aparece **detrás** del `src` de la copia principal.
  - Por eso todas las órdenes de Python de este documento llevan `PYTHONPATH="$PWD/src"` y se ejecutan desde el worktree.
- **PyInstaller empaqueta lo que hay en disco, no lo que hay en git.** Hay que compilar desde un worktree limpio, nunca desde la copia principal.

Estas son las variables que usan las órdenes. Un Git Bash abierto a mano las conserva de una orden a otra. **Una sesión de Claude Code empieza cada llamada con un shell nuevo**, así que tiene que poner al principio de cada bloque las variables que use.

```bash
V=<X.Y.Z>                       # la versión nueva: solo dígitos y puntos
ANT=<anterior>                  # la publicada ahora
CREATICS="$HOME/OneDrive - Creatics Mundo Educa"
PY="$CREATICS/KAIRIS/Studio/Voziris/voziris-entrega/voziris/.venv/Scripts/python.exe"
REL="$LOCALAPPDATA/Temp/voziris-rel-$V"       # worktree limpio, fuera de OneDrive
BUILD="$LOCALAPPDATA/Temp/voziris-build-$V"   # salida de PyInstaller y del empaquetado
WEB="$CREATICS/LABIAS/WEB - KAIRIS - Claude/Nueva KAIRIS"
COMPARTIR="$CREATICS/KAIRIS/Studio/Voziris/voziris-entrega/compartir"
```

## Paso a paso

### 1. Rama y versión

Esto se hace en Git Bash, desde cualquier copia del repositorio. `git fetch` y `git worktree add` solo escriben en `.git`: no tocan los archivos de nadie ni sus cambios sin commit.

```bash
cd "$CREATICS/KAIRIS/Studio/Voziris/voziris-entrega/voziris"
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

Tiene que salir `<X.Y.Z>` con una ruta dentro de `voziris-rel-<X.Y.Z>`. La única prueba que fijaba la versión ya usa `__version__` (commit `3997d10`). Si una prueba falla solo por el número de versión, es por eso.

Mira también si `pyproject.toml` trae dependencias nuevas, con `git diff "v$ANT" -- pyproject.toml`. Si las trae, instálalas en el venv con `"$PY" -m pip install "<paquete>"`. No uses `pip install -e .` desde el worktree: reengancharía al worktree el venv de la copia principal.

### 2. Notas de la release

`docs/notas-release.md` es el texto de la release, tal cual. Se parte de las notas de `<anterior>` y se cambia:

- el título, `# Voziris <X.Y.Z>`;
- «Novedades en <X.Y.Z>», con lo que salió en el `git log` del paso 1, contado para quien usa el programa;
- «Instalación → Si ya tenías la …», para cada versión que alguien pueda tener instalada (hoy, la 0.1.0 y la 0.1.1);
- «Limitaciones conocidas», si algo ha cambiado;
- la línea del hash: el nombre nuevo y 64 ceros, dentro del bloque de código que ya tiene la sección «Verificación del archivo».

```text
SHA-256  voziris-<X.Y.Z>-win64.zip
0000000000000000000000000000000000000000000000000000000000000000
```

`empaquetar.py` busca exactamente `SHA-256`, dos espacios, `voziris-<dígitos y puntos>-win64.zip` y un salto de línea LF. Después sustituye los 64 caracteres de la línea siguiente. Hay que tener en cuenta dos cosas:

- **Si no encuentra el patrón, no avisa.** El paso 5 lo comprueba.
- **No cambia el nombre del archivo.** Si se queda el de la versión anterior, el hash nuevo sale con el nombre viejo.

Las notas tienen que llevar lo que pide `docs/RELEASE.md` §5:

- qué hay de nuevo;
- el hash;
- el aviso sobre el antivirus y las ventanas elevadas;
- la atribución a NVIDIA (CC-BY-4.0).

En la 0.1.1, la verificación encontró frases falsas en las notas, como «mientras baja el modelo puedes usar la API». Por eso el paso 6 contrasta cada frase con el código.

Si vas a cambiar `docs/LEEME-instalar.html`, `ATRIBUCIONES.md`, `LICENSE`, `config.ejemplo.toml` o `assets/`, hazlo ahora: todos van dentro del ZIP.

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

Tienen que salir `salida: 0` y `Build complete!`. Las dos primeras rutas de búsqueda tienen que ser `…\voziris-rel-<X.Y.Z>` y `…\voziris-rel-<X.Y.Z>\src`. Si aparece antes `…\voziris-entrega\voziris\src`, faltaba `PYTHONPATH` y el paquete lleva el código de la copia principal.

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
  - `voziris.exe --comprobar` falla. Esta comprobación puede tardar hasta 5 minutos.
- **Escribe dentro del paquete:**
  - `Instalar Voziris.cmd`;
  - la guía `LÉEME - Instalar Voziris.html`, que es una copia de `docs/LEEME-instalar.html`;
  - `_internal/voziris-versiones.txt`;
  - el manifiesto, `_internal/voziris-manifiesto.txt`.
- **Deja `voziris-<X.Y.Z>-win64.zip` y `SHA256SUMS.txt` en `$BUILD/dist`.** `SHA256SUMS.txt` lleva finales de línea LF. Con CRLF, `sha256sum -c` falla aunque el hash sea el bueno.
- **Pone el hash en `docs/notas-release.md`.**

Cada ejecución da un hash distinto, porque `voziris-versiones.txt` lleva la fecha y la hora. Por eso el hash se guarda en un commit después del último empaquetado.

```bash
cd "$BUILD/dist" && sha256sum -c SHA256SUMS.txt     # voziris-<X.Y.Z>-win64.zip: OK
"$BUILD/dist/voziris/voziris.exe" --version         # voziris <X.Y.Z>
stat -c %s "$BUILD/dist/voziris-$V-win64.zip"       # menos de 200 MB (la 0.1.1: 99.873.409 bytes)
cd "$REL" && git status --short                     # solo docs/notas-release.md
grep -A1 "^SHA-256  voziris-$V-win64.zip" docs/notas-release.md   # el mismo hash que SHA256SUMS.txt
git add docs/notas-release.md && git commit -m "Notas $V: SHA-256 del ZIP"
```

`voziris.exe` no tiene consola, pero si se lanza desde Git Bash escribe por la tubería. `--version` termina sin abrir nada y sin tocar la Voziris que esté abierta.

### 6. Comprobar el paquete antes de publicar

**6.1. Instalación real en carpetas temporales**, con `tools/probar_instalacion.py`. Usa el instalador de verdad y el ZIP de verdad, pero lo escribe todo en carpetas temporales y en una clave de pruebas de HKCU que borra al terminar. No cierra la Voziris abierta ni toca el menú Inicio. Sin `--anterior`, prueba una instalación desde cero. Con `--anterior <ZIP>`, simula esa versión instalada con `config.toml` y un historial de 500 líneas, instala la nueva encima y comprueba que esos datos siguen igual. La carpeta extraída de la nueva trae además su propio historial de una línea: es lo que hacía perder el de la instalada hasta la 0.1.1. Sale con 0 si todo cuadra y con 1 si algo no.

Ejecútalo desde cero y encima de cada versión que alguien pueda tener instalada. Hoy son la 0.1.0 y la 0.1.1:

```bash
cd "$REL"
PYTHONPATH="$PWD/src" "$PY" tools/probar_instalacion.py "$BUILD/dist/voziris-$V-win64.zip"
for A in 0.1.0 "$ANT"; do
  gh release download "v$A" -R alfonsosanzme/voziris -p "voziris-$A-win64.zip" -D "$BUILD/anteriores" --skip-existing
  PYTHONPATH="$PWD/src" "$PY" tools/probar_instalacion.py "$BUILD/dist/voziris-$V-win64.zip" \
    --anterior "$BUILD/anteriores/voziris-$A-win64.zip" || echo "FALLA desde $A"
done
```

No prueba `Instalar Voziris.cmd` ni `voziris.exe --instalar`: ni la ventana de progreso, ni cerrar y reabrir la Voziris abierta, ni el arranque por el acceso de Inicio. Eso va en la máquina limpia (6.3).

**6.2. Verificación independiente.** En la 0.1.1 se hicieron tres revisiones por separado, sin que ninguna viera el trabajo de las otras. Encontraron un fallo real (el instalador pisaba el historial) y frases falsas en las notas. Una sesión de Claude las lanza como tres subagentes en paralelo; Alfonso se las pide a una sesión de Claude.

1. **El ZIP tal como lo recibe alguien.** Comprueba que:
   - el hash cuadra con `SHA256SUMS.txt`;
   - en una carpeta nueva con el ZIP extraído, `./voziris.exe --comprobar` da una línea «bien» por cada prueba y sale con 0;
   - el ZIP trae `Instalar Voziris.cmd`, el LÉEME y el manifiesto;
   - no trae `config.toml`, `modelos/`, `historial/` ni registros.
2. **La actualización** desde cada versión que pueda estar instalada, con 6.1 o con una prueba equivalente escrita a partir de `src/voziris/instalador.py`.
3. **Las notas contra el código.** Cada frase de `docs/notas-release.md`, y de `docs/LEEME-instalar.html` si ha cambiado, se contrasta con el código y con `git diff v<anterior>..HEAD`. Lo que no se sostenga, se quita.

**6.2 bis. La actualización desde la bandeja (desde la 0.1.2) (sin probar).** Es la única parte que no se puede arreglar después de publicar. Si `--actualizar` de una versión falla, publicar otra no lo arregla, porque es la versión instalada la que descarga. Se prueba contra la release viva sin publicar nada:

1. Compila la versión nueva con `__version__` cambiado **solo en la copia de compilación** a una anterior a la publicada (por ejemplo `0.0.1`).
2. Instálala en un equipo o usuario de Windows de pruebas, **nunca en el de Alfonso**: la actualización cierra la Voziris abierta y escribe en el registro de su usuario. Contesta «Sí» a la tarjeta y deja algún dictado en el historial.
3. Bandeja → «Buscar actualizaciones» → «Actualizar a la <publicada>…».
4. Comprueba lo siguiente:
   - sale la ventana de progreso, y Cancelar no deja nada;
   - si no cancelas, la bandeja se cierra y se abre la versión publicada;
   - `config.toml`, `modelos/` e `historial/` siguen intactos;
   - `voziris-actualizar.log` está en la carpeta de la instalación.

Así se prueba el código de `--actualizar` de la versión nueva. El instalador que se ejecuta es el de la **publicada**, y lo que se ve al final depende de cuál sea:

- **Si la publicada es la 0.1.1** (el caso de la primera prueba, con la 0.1.2): su instalador termina con «Voziris instalado» y no con «Voziris actualizado». Escribe su registro en `voziris.log` y no en `voziris-instalar.log`, y no toma el mutex de instalar. Por eso la ventana de `--actualizar` sigue abierta unos 3 minutos (`ESPERA_INSTALADOR_S`) antes de cerrarse sola. Todo eso es lo esperado.
- **Si la publicada es la 0.1.2 o posterior:** el aviso dice «Voziris actualizado», el registro del instalador está en `voziris-instalar.log` junto a la instalación, y `--actualizar` se cierra en cuanto el instalador arranca.

**6.3. Máquina limpia.** La lista a mano está en `docs/RELEASE.md` §4 y no se repite aquí. Incluye Windows sin Python, un USB, el arranque en menos de 3 segundos y Control inteligente de aplicaciones. Más detalles en el último apartado de este documento.

Si algo falla, el arreglo va en la rama: commit y vuelta al paso 3. Hay que volver a compilar y a empaquetar, y el hash cambia.

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

La segunda orden detecta los cambios hechos después de compilar. Si solo tocan cosas que no van en el ZIP (el README, `docs/` salvo el LÉEME, `tests/`), no hace falta recompilar.

Igual que en la 0.1.1, la fusión se hace sin `--delete-branch`.

### 8. Crear la release

```bash
cd "$REL" && git fetch origin
git diff --stat "$(cat "$BUILD/commit-compilado.txt")" origin/main -- . ':(exclude)docs/notas-release.md'   # vacío
git diff --stat "release-$V" origin/main      # vacío
git ls-remote --tags origin "v$V"             # vacío: la etiqueta aún no existe
MAIN=$(git rev-parse origin/main)
{ cat docs/notas-release.md; printf '\nCompilado desde `main` en el commit `%s`.\n' "${MAIN:0:7}"; } > "$BUILD/notas-v$V.md"
gh release create "v$V" "$BUILD/dist/voziris-$V-win64.zip" "$BUILD/dist/SHA256SUMS.txt" \
  -R alfonsosanzme/voziris --target "$MAIN" --title "Voziris v$V" \
  --notes-file "$BUILD/notas-v$V.md" --latest
```

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

Comprueba también que Voziris la ve como la verán los equipos (VOZ-82). Desde el worktree, con `PYTHONPATH=src`:

```bash
PYTHONPATH=src "$PY" -c "from voziris import actualizaciones as a; n = a.consultar('$ANT'); print(n, n.instalable if n else '')"
```

Tiene que salir `Novedad(version='<X.Y.Z>', …)` con las URL de esta release, y `True`. Con `False`, a la release le falta el ZIP o `SHA256SUMS.txt` con su nombre exacto. **(Sin probar con una release nueva:** se comprobó contra la 0.1.1, preguntando desde la 0.1.0.)

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

**Quién lo hace.** En `Nueva KAIRIS` trabaja otra sesión, «WEB Kairis». El enlace de la 0.1.1 lo cambió ella, en el commit `a3229f7`, y desplegó con el OK de Alfonso. Lo más limpio es pasarle este encargo:

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
- **Antes de subir, valida** el blog, el sitemap, las imágenes, la cabecera y el pie, las reseñas y el versionado de CSS y JS. Si se para por algo que no es tuyo, detente y avisa a «WEB Kairis».
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
mv voziris-*-win64.zip SHA256SUMS.txt "LÉEME - Instalar Voziris.html" "$DEST/"
cp "$BUILD/dist/voziris-$V-win64.zip" "$BUILD/dist/SHA256SUMS.txt" "$BUILD/dist/voziris/LÉEME - Instalar Voziris.html" .
sha256sum -c SHA256SUMS.txt   # voziris-<X.Y.Z>-win64.zip: OK
```

### 12. Actualizar los equipos

Hay que actualizar el tuyo y avisar a quien lo use: está en el apartado siguiente. Una sesión de Claude no ejecuta `Instalar Voziris.cmd` ni `--instalar` en el equipo de Alfonso sin su sí, porque cierran su Voziris y escriben en su registro.

Al terminar, puedes quitar el worktree con `git worktree remove "$REL"`. `$BUILD` ya no hace falta cuando el ZIP está en la release y en `compartir/`.

## Actualizar los equipos

Hay tres pasos comunes a todos los casos:

1. **Consigue el ZIP**, de la release o de `compartir/`. Si el antivirus protesta, comprueba el hash. En PowerShell, `Get-FileHash .\voziris-<X.Y.Z>-win64.zip` tiene que dar el de las notas, en mayúsculas.
2. **Desbloquéalo antes de extraer:** botón derecho → Propiedades → Desbloquear. Así Windows no pregunta por cada archivo al ejecutarlo.
3. **Extráelo entero en una carpeta nueva y de ruta corta**, nunca encima de la versión anterior. Son unos 1.100 archivos, así que espera a que termine.
   - Si «Extraer todo» dice que una ruta es demasiado larga, pulsa Cancelar, no Omitir. Omitir deja el programa a medias.
   - Si la ruta de la carpeta de `voziris.exe` pasa de unos 180 caracteres, Windows deja de cargar sus archivos.
   - En tu equipo, mejor fuera de OneDrive (por ejemplo, en Descargas). OneDrive retiene archivos (por eso se compila fuera de él) y sincronizaría el millar sin necesidad.

### Si Voziris está instalada (menú Inicio y «Aplicaciones instaladas»)

Haz doble clic en el `Instalar Voziris.cmd` de la carpeta nueva. Da igual que vengas de la 0.1.0 o de la 0.1.1, porque se ejecuta el instalador de la versión nueva. Según `instalador.py` y `_instalar_cli`, esto es lo que hace:

1. **Comprueba la carpeta extraída** contra el manifiesto. Si falta algo, no toca nada y dice qué archivo es.
2. **Prepara una copia aparte** en `%LOCALAPPDATA%\Programs\Voziris.nuevo`. Le quita la marca de Internet y la arranca con `--comprobar`.
3. **Solo entonces cambia el programa:** cierra la Voziris abierta y sustituye `voziris.exe` y `_internal`. Si algo falla, la versión anterior se queda como estaba y se vuelve a abrir.
4. **Conserva los datos.** Mantiene `config.toml`, `modelos/` e `historial/` de la instalación, y de la carpeta extraída solo copia lo que falte.
5. **Rehace lo demás:**
   - el acceso del menú Inicio, y el de inicio de sesión si existía;
   - el botón derecho «Transcribir con Voziris»;
   - la versión que aparece en «Aplicaciones instaladas».

   Después abre la instalada y avisa con «Voziris instalado». Desde la 0.1.2, si había otra versión instalada, el aviso es «Voziris actualizado».

Desde la 0.1.2, bandeja → «Actualizar a la X.Y.Z…» hace lo mismo sin tocar nada a mano. Descarga el ZIP y `SHA256SUMS.txt` de la release, comprueba el hash y el manifiesto, lo descomprime en `%TEMP%\voziris-actualizacion` y ejecuta el `--instalar` de la versión nueva. La carpeta temporal la borra la siguiente Voziris que arranque, pasada una hora. Lo que pasa queda en `voziris-actualizar.log` y en `voziris-instalar.log`, junto al `voziris.log` de la instalación, y el diagnóstico recoge los dos.

No hace falta cerrar Voziris antes, porque el instalador la cierra justo antes del cambio. Para comprobar la versión, en Git Bash: `"$LOCALAPPDATA/Programs/Voziris/voziris.exe" --version`. También se ve en Configuración → Aplicaciones → Voziris. Después, la carpeta extraída ya no hace falta.

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
> - Si lo tienes instalado: extrae el ZIP entero en una carpeta nueva y abre «Instalar Voziris.cmd». Se conservan tu configuración, el modelo y el historial.
> - Si lo usas sin instalar: cierra el viejo, copia `config.toml`, `modelos` e `historial` a la carpeta nueva y abre el nuevo.
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
- [ ] El paquete está compilado desde un árbol limpio, fuera de OneDrive, y el worktree es lo primero en las rutas de PyInstaller.
- [ ] `empaquetar.py` ha terminado sin errores, `sha256sum -c` da OK y `--version` dice <X.Y.Z>.
- [ ] El hash está en un commit, en las notas, junto al nombre del ZIP nuevo.
- [ ] `tools/probar_instalacion.py` pasa desde cero y encima de cada versión publicada, y las tres verificaciones independientes no dejan nada pendiente.
- [ ] La lista a mano de `docs/RELEASE.md` §4 está hecha, o Alfonso ha decidido no exigirla.
- [ ] El PR está fusionado con squash, y `main` coincide con lo compilado (salvo las notas).
- [ ] La release `v<X.Y.Z>`:
  - [ ] tiene el ZIP y `SHA256SUMS.txt`;
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
  - Eso compila dentro del repositorio. La copia principal está en OneDrive, que impide vaciar `build/dist`, y además tiene cambios sin commit de otra sesión.
  - `pyinstaller` a secas, sin `PYTHONPATH`, coge el `src/` de la copia principal (ver «Antes de empezar»).
  - Aquí se usa `"$PY" -m PyInstaller`, con salida en `$BUILD`.
- **§2** tiene tres problemas:
  - Usa `banco\muestra-08.wav` y `prueba de voz\nota.m4a`, que están en `.gitignore` y solo existen en la copia principal, no en un worktree.
  - `voziris.exe --salir` cierra la Voziris que esté abierta, también la que Alfonso tiene instalada.
  - `voziris.exe --config …` sin `--archivo` no arranca si ya hay otra Voziris abierta, porque solo admite una instancia.
- **§3** ejecuta `python tools/empaquetar.py` sin `--dist`, que da por hecho `build/dist/voziris`, y dice que `SHA256SUMS.txt` queda en `build/dist`. Con `--dist`, queda junto a la carpeta indicada, en `$BUILD/dist`.
- **§5** crea primero una etiqueta anotada (`git tag -a` y `git push`) y después ejecuta `gh release create` sin `--target`, con `docs/notas-release.md` tal cual.
  - Con la fusión con squash, el HEAD de la rama de la release no está en `main`, así que la etiqueta quedaría fuera de `main`.
  - En la 0.1.1, la etiqueta la creó `gh` sobre el SHA completo de `main`, y es ligera.
  - Las notas llevaron además la línea «Compilado desde `main`…», y se usó `--latest`.
- **§5 dice «Solo después del punto 4».** De la 0.1.1 consta lo que hacen aquí los pasos 3 a 9, pero no consta la verificación en máquina limpia. Alfonso decide si se exige antes de cada release.
