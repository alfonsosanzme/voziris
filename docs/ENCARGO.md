# Encargo: Voziris v1

**Cliente:** Alfonso Sanz López
**Fecha:** 3 de septiembre de 2026
**Repositorio:** este mismo, con el esqueleto y los contratos ya escritos

---

## 1. Qué hay que construir

Una aplicación de bandeja para Windows que convierte voz en texto. El usuario
mantiene un atajo, habla, suelta, y el texto aparece en la aplicación que tenga
delante. Un segundo atajo deja el micrófono clavado hasta volver a pulsar. Un
tercero, en lugar de escribir en pantalla, anexa lo dictado a un archivo
Markdown.

Dos motores de transcripción intercambiables: uno local que funciona en CPU sin
GPU, y otro por API con la clave del propio usuario.

Es un reemplazo de Wispr Flow (144 $/año) para un usuario que dicta en
castellano en portátiles sin GPU y quiere poder trabajar sin conexión.

## 2. Lo que ya está decidido — no hay que volver a discutirlo

Estas decisiones están tomadas y razonadas en `docs/ARQUITECTURA.md` y en el
análisis previo. Se implementan como están:

| | Decisión |
|---|---|
| **Lenguaje** | Python 3.11–3.13 |
| **Motor local** | Parakeet TDT 0.6B v3 en ONNX int8, vía `onnx-asr` |
| **Motor API** | Groq `whisper-large-v3-turbo`, cliente compatible con el esquema de OpenAI |
| **Idioma** | Fijo en configuración. **Sin detección automática** |
| **Empaquetado** | PyInstaller en modo `onedir`, comprimido en ZIP |
| **Configuración** | `config.toml` junto al ejecutable, no en `%APPDATA%` |
| **Interfaz** | Bandeja del sistema + Tkinter para los ajustes. Sin ventana principal |
| **Licencia** | MIT para el código; atribución obligatoria a NVIDIA por CC-BY-4.0 |

Si algo de esto resulta inviable al implementarlo, **se avisa antes de
cambiarlo**, no después. Cada una arrastra consecuencias en otras partes.

## 3. Alcance

### Entra en la v1 (20 funciones)

**Críticas** — sin esto no hay producto:

| Id | Función | Issue |
|---|---|---|
| A1 | Atajo global push-to-talk | VOZ-02 |
| B1 | Motor local sin GPU | VOZ-11 |
| B2 | Motor por API | VOZ-30 |
| C1 | Puntuación y mayúsculas | *(la trae el motor)* |
| D1 | Inserción en cualquier aplicación | VOZ-12 |
| D2 | Preservar y restaurar el portapapeles | VOZ-12 |
| D3 | Historial con reintento | VOZ-52 |

**Importantes** — también entran: A2 manos libres · A3 búfer previo · A4
indicador de nivel · A5 selección de micrófono · A6 señales acústicas · B3
conmutar local↔nube · C2 limpieza de muletillas · C3 formato automático · C4
niveles de intervención · E1 diccionario personal · F2 bandeja y arranque · F3
modo offline.

**Requisitos propios**, no copiados de Wispr Flow:

- **H1** — volcado a un archivo Markdown, con su propio atajo.
- **H2** — atajos separados para «mantener» y «clavar». **No una doble
  pulsación**: detectarla obliga a esperar la ventana de tiempo antes de
  empezar a grabar, y eso se come el búfer previo.
- **B4** — el idioma se elige en los ajustes.
- **D4** — el auto-Enter existe, pero desactivado por defecto.
- **F1** — portable: sin instalación ni permisos de administrador.

### Queda fuera de la v1

No implementar, ni «dejarlo preparado»: texto parcial en streaming,
aprendizaje automático del vocabulario, actualizaciones automáticas,
estadísticas de uso, snippets por frase, modo comando por voz, tono según la
aplicación de destino, lectura del contexto en pantalla, transcripción de
reuniones, sincronización entre dispositivos.

**Descartado del todo:** apps móviles y cumplimiento SOC 2 / HIPAA / SSO.

## 4. Definición de hecho

Una tarea está terminada cuando cumple **todo** esto:

1. Los criterios de aceptación de su issue se verifican uno a uno.
2. `pytest` pasa, con tests nuevos para lo añadido. Los tests de contrato de
   `tests/test_contratos.py` siguen pasando.
3. `ruff check src tests` sin avisos.
4. `mypy` sin errores. El proyecto está en modo estricto: no se relaja, se
   añade la anotación que falte.
5. Los `NotImplementedError` de lo implementado han desaparecido.
6. Probado a mano en Windows, no solo en tests. Esto va de teclado, audio y
   ventanas: hay cosas que solo se ven usándolas.
7. Si el comportamiento se aparta de lo documentado en el docstring, el
   docstring se actualiza en el mismo commit.

## 5. Cómo empezar

**El hito 0 va antes de escribir código de la aplicación.** No es opcional:

```bash
pip install -e ".[dev,banco]"
python tools/banco_h0.py grabar --n 10 --segundos 15
python tools/banco_h0.py medir
```

Los RTF que maneja la especificación (0,033 para Parakeet) están medidos en un
i7-12700KF de sobremesa. En un portátil sin GPU serán peores y nadie sabe
cuánto. El script trae el criterio de decisión escrito de antemano, para no
racionalizar el resultado a posteriori. Manda `resultados-h0.md` antes de
seguir.

Después, el orden del backlog. Cada hito deja algo usable:

| Hito | Qué se consigue |
|---|---|
| **H0** | Saber si el motor local sirve en el equipo real |
| **H1** | Ya sustituye a Wispr Flow para dictado simple |
| **H2** | Se usa cómodo todo el día |
| **H3** | Los dos motores, conmutables |
| **H4** | Alcanza a Wispr Flow en calidad de texto |
| **H5** | Captura rápida al vault de Obsidian |
| **H6** | Publicable en GitHub |

## 6. Lo que sí hay que decidir sobre la marcha

Tres cosas que no están cerradas porque dependen de lo que se vea al implementar:

1. **El retardo de restauración del portapapeles** (`destinos/app_activa.py`).
   Hay que medir el mínimo que funcione de forma fiable y dejarlo constante y
   comentado. Demasiado corto y algunas aplicaciones pegan el contenido
   restaurado; demasiado largo y se nota.
2. **El umbral de similitud del diccionario** (`proceso/diccionario.py`). Un
   umbral flojo destroza texto correcto, y eso molesta más que un nombre propio
   mal escrito. Empezar restrictivo.
3. **El modelo de LLM para C2/C3/C4** (`proceso/llm.py`). Groq es lo natural
   por tener ya la clave, pero hay que verificar cómo se porta **en
   castellano** antes de cerrar el prompt. Casi todo lo publicado sobre esto
   está probado en inglés.

## 7. Cómo reportar

- Una rama por issue: `voz-11-motor-local`.
- El mensaje de commit empieza por el identificador: `VOZ-11: carga el modelo`.
- Un PR por issue, con las capturas o el vídeo corto de la prueba manual.
- Si una issue resulta estar mal planteada, se dice en el PR. La especificación
  se escribió sin tocar código y habrá cosas que no encajen.

## 8. Contexto que ayuda a decidir

- Usuario único, que es además el cliente y publica el resultado en GitHub. No
  hay que diseñar para multiusuario ni para instalación desatendida.
- Dicta **en castellano**. Cualquier ajuste probado solo en inglés hay que
  verificarlo en español.
- El destino Markdown alimenta un vault de Obsidian que se procesa después. Por
  eso el formato es una línea por dictado y por eso el archivo se abre y se
  cierra en cada escritura: Obsidian lo tiene abierto al mismo tiempo.
- Prefiere que se le avise de lo que no cuadra a que se resuelva por
  suposición.
