# Arquitectura de Voziris

Este documento explica **por qué** el código está partido así. El qué está en
los docstrings de cada módulo; aquí van las razones, que es lo que no se puede
deducir leyendo el código.

---

## El pipeline

```
┌─ A1/A2/H2 ─────────┐   ┌─ B1/B2/B3 ─┐   ┌─ C1..C4, E1, E3 ─┐   ┌─ D1..D4, H1 ─┐
│  Atajo             │   │   Motor    │   │   Post-proceso   │   │   Destino    │
│  ↓                 │   │            │   │                  │   │              │
│  Búfer previo 500ms│ → │  local  ó  │ → │  diccionario     │ → │ app activa   │
│  Captura 16 kHz    │   │  API       │   │  sustituciones   │   │   ó          │
│  VAD (modo clavar) │   │            │   │  LLM (opcional)  │   │ archivo .md  │
└────────────────────┘   └────────────┘   └──────────────────┘   └──────────────┘
                                                                         ↓
                                                                  D3 historial
```

Tres protocolos sostienen todo el diseño: `MotorSTT`, `PostProceso` y
`Destino`. `__main__.py` es el único módulo que conoce todas las piezas; los
demás dependen solo de los protocolos y de `tipos.py`.

Módulos auxiliares sin dependencias del proyecto, aparte de `tipos.py`:

- `teclas.py` — nombres de teclas, alias y códigos virtuales de Windows, y el
  análisis de combinaciones («ctrl+shift+space»). Lo comparten `config.py`
  (validar los atajos sin tocar Win32, así corre en CI) y `atajos.py`
  (traducir cada nombre a lo que ve el hook).
- `winapi.py` — lo que se necesita de Win32 (`SendInput`, ventana en primer
  plano, portapapeles) con las firmas de `ctypes` declaradas en un solo sitio.
  Importable en cualquier sistema; sus funciones fallan con `OSError` fuera
  de Windows. Lo usan `destinos/app_activa.py` y `atajos.py`.

## Las cinco decisiones que explican el resto

### 1. Ninguna etapa propaga excepciones hacia arriba

Un post-proceso que falla devuelve su entrada intacta y añade un aviso. Un
motor que falla se lo dice al selector para que pruebe el otro. Solo
`ConfigInvalida` detiene la aplicación, y solo al arrancar.

**Por qué.** El usuario acaba de hablar treinta segundos. Perder eso porque el
LLM dio timeout sería el peor fallo posible del producto, mucho peor que
entregar texto sin pulir. Y si todo lo demás falla, el dictado sigue en el
historial y se puede reintentar.

### 2. La salida tiene destinos, no un único camino

`app_activa` y `archivo_md` son dos implementaciones de la misma interfaz, cada
una con su atajo.

**Por qué.** H1 —el volcado a Markdown— no es una función lateral. Si se
insertara siempre en la aplicación activa y el volcado se añadiera después,
habría que partir en dos el punto donde se concentran D2 (portapapeles), D4
(auto-Enter) y el historial. Diseñado desde el principio, es una clase; añadido
después, es una refactorización del sitio más delicado del programa.

### 3. Dos atajos, no una doble pulsación

`mantener` y `clavar` son combinaciones distintas.

**Por qué.** Es petición explícita del cliente, y además hay una razón técnica
que la confirma: detectar una doble pulsación obliga a esperar la ventana de
tiempo antes de decidir qué hacer, y esa espera se come el búfer previo, que es
justo lo que evita perder la primera sílaba.

**Consecuencia.** `RegisterHotKey` de Win32 no informa de cuándo se suelta una
tecla, así que `mantener` necesita un hook de bajo nivel (`WH_KEYBOARD_LL`) con
su propio bombeo de mensajes. Ese hook es también el origen de los dos riesgos
conocidos: los falsos positivos de antivirus y el fallo sobre ventanas
elevadas.

### 4. El micrófono graba siempre

`Captura` abre el flujo al arrancar y mantiene un anillo de 500 ms girando
mientras la aplicación vive.

**Por qué.** Dos motivos. Uno, abrir el flujo en cada dictado cuesta cientos de
milisegundos y a veces falla si otra aplicación tiene el micrófono. Dos, y más
importante: sin audio anterior a la pulsación se pierde la primera sílaba de
cada dictado, y el usuario aprende a hacer una pausa antes de hablar — que es
exactamente la fricción que la herramienta venía a quitar.

Nada de ese audio se escribe a disco.

### 5. Configuración junto al ejecutable, no en %APPDATA%

**Por qué.** Es lo que hace que «portable» signifique algo: se copia la carpeta
a un USB y se lleva con los ajustes dentro. Como contrapartida, todas las rutas
relativas deben resolverse desde la carpeta del ejecutable y nunca desde el
directorio de trabajo — Voziris arranca con Windows y ese directorio no es
predecible.

## Por qué Parakeet y no Whisper

| | Parakeet TDT 0.6B v3 | Whisper small int8 |
|---|---|---|
| RTF en CPU | 0,033 | 0,13 |
| WER | 6,34 % | — (large-v3: 7,44 %) |
| Puntuación | **sí, de serie** | sí |
| Español | sí (25 idiomas europeos) | sí (99 idiomas) |
| RAM | ~1,5 GB | ~850 MB |
| Licencia | CC-BY-4.0 (atribución) | MIT |

Cuatro veces más rápido y más preciso. Lo decisivo es la tercera fila: **el
modelo emite puntuación y mayúsculas**, lo que resuelve C1 sin red y sin LLM.

Esto importa más de lo que parece. Si el motor local no puntuara, no habría de
dónde sacarlo: sherpa-onnx solo publica modelos de puntuación para inglés y
chino. El modo offline habría entregado texto sin puntuar, que es prácticamente
inservible para dictar correos o notas.

Whisper small int8 queda como plan B documentado, por si el hito 0 dice que
Parakeet no rinde en el equipo real.

## Por qué el post-proceso va en ese orden

```
diccionario → sustituciones → LLM
```

El diccionario arregla nombres propios **antes** de que el LLM los vea. Al
revés, el LLM «corregiría» Kairis a otra cosa y el diccionario ya no
reconocería lo que quedó.

Los dos primeros funcionan sin red; el tercero no. Por eso el modo offline
sigue siendo útil: se pierde la limpieza de muletillas y el formato, pero el
texto llega puntuado y con el vocabulario propio corregido.

## Por qué onedir y no onefile

`--onefile` produce un solo `.exe`, que era el requisito literal. Pero
descomprime todo su contenido en `%TEMP%` **en cada arranque**, y con
onnxruntime y sus DLLs dentro son varios segundos. Voziris arranca con Windows
y tiene que responder a un atajo al instante.

`--onedir` arranca de inmediato y sigue siendo portable en todo lo que importa:
se copia, va en un USB, no toca el registro, no pide administrador. Son varios
archivos en una carpeta en lugar de uno.

El cambio a `onefile` está documentado en `build/voziris.spec` y son cinco
líneas, a propósito: la decisión sigue siendo del cliente.

El modelo (~680 MB) no cabe dentro en ninguno de los dos modos. Se descarga a
`./modelos/` en el primer arranque.

## Lo que este diseño deja fácil para después

- **Un destino nuevo** (una nota de Notion, un archivo de texto plano): una
  clase que cumpla `Destino`.
- **Otro proveedor de API**: cambiar `base_url` y `modelo` en el TOML, siempre
  que sea compatible con el esquema de OpenAI.
- **Otro motor local**: una clase que cumpla `MotorSTT`. El plan B de Whisper
  cabe aquí sin tocar nada más.
- **Un post-proceso nuevo**: una clase que cumpla `PostProceso`, insertada en la
  cadena en la posición que corresponda.

## Lo que este diseño deja difícil, y es a propósito

- **Streaming de texto parcial** (B5). Todo el pipeline asume audio completo y
  una sola transcripción. Hacerlo incremental obligaría a cambiar los tres
  protocolos. Está fuera del alcance por decisión, no por descuido.
- **Multiusuario o instalación desatendida.** La configuración junto al
  ejecutable lo impide por diseño. Es el precio de la portabilidad, y el
  proyecto es de un solo usuario.
