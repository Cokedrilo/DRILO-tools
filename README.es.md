# DRILO-tools

Dos nodos de ComfyUI para trabajar con audio generado y con prompts.

**🐊 DRILO AudioMultiExport** convierte un batch de audio en una lista de filas,
cada una con su propio reproductor y un checkbox. Marcas una y esa muestra
concreta se escribe en `output/`. La desmarcas y el archivo se borra. El resto
del batch nunca ensucia la carpeta. Opcionalmente normaliza al guardar y manda
las tomas directamente al media pool de DaVinci Resolve, etiquetadas con la seed
y el prompt que las generó.

**🐊 DRILO MultiPrompt** te da varias cajas de texto y emite la siguiente en cada
Run, para que puedas comparar prompts con una seed fija.

*[Documentation in English](README.md)*

---

## Instalación

Clona (o copia) la carpeta dentro del `custom_nodes/` de tu ComfyUI:

```bash
git clone https://github.com/Cokedrilo/DRILO-tools.git ComfyUI/custom_nodes/DRILO-tools
```

Reinicia ComfyUI. No hay `pip install`: los nodos solo usan lo que ComfyUI ya
trae (`torch`, `torchaudio`, `aiohttp`, `folder_paths`, `server.PromptServer` y
PyAV).

Los nodos aparecen como **audio → 🐊 DRILO AudioMultiExport** y
**utils → 🐊 DRILO MultiPrompt**. Sus identificadores internos, los que se
guardan en el JSON del workflow, son `DRILO_AudioMultiExport` y
`DRILO_MultiPrompt`.

> **Nota sobre PyAV.** Desde torchaudio 2.9, tanto `torchaudio.load` como
> `torchaudio.save` exigen el paquete aparte `torchcodec`, que el ComfyUI
> portable no incluye. Cada lectura y escritura intenta torchaudio primero y cae
> a PyAV, que ComfyUI sí trae. En la práctica, en una instalación portable PyAV
> hace todo el trabajo de codificación.

---

## 🐊 DRILO AudioMultiExport

### Entradas

| Entrada | Tipo | Por defecto | Qué hace |
| --- | --- | --- | --- |
| `audio` | `AUDIO` | — | El batch a revisar. Funciona con `B=1` y con `B>1`. |
| `output_folder` | `STRING` | `audio` | Carpeta dentro de `output/`. Vacío = la raíz de `output/`. Admite rutas anidadas (`audio/tomas/buenas`). |
| `filename` | `STRING` | `pick` | Nombre base del archivo. ComfyUI le añade `_00001_` y la extensión. |
| `format` | combo | `flac` | `flac`, `mp3` u `opus`. Solo afecta a lo que se guarda. |
| `accumulate` | `BOOLEAN` | `True` | Apila las muestras de cada ejecución en vez de reemplazar la lista. |
| `resolve_bin` | `STRING` | `sfx` | Bin de Resolve al que importa el botón `→ Resolve`. Admite bins anidados (`sfx/comfyui`). |
| `resolve_placement` | combo | `bin` | `bin` = solo importar. `playhead` = pegar donde está el cabezal. `end` = al final de la timeline. |
| `resolve_audio_track` | `INT` | `1` | Pista de audio destino, se crea si no existe. |
| `normalize` | combo | `off` | `peak` / `rms` en dBFS, `lufs` = LUFS integrado BS.1770-4 con gating. |
| `normalize_target_db` | `FLOAT` | `-1.0` | Objetivo: dBFS para peak/rms, LUFS para lufs. |

Carpeta y nombre son campos separados por comodidad; internamente se juntan en el
único `filename_prefix` que espera `folder_paths.get_save_image_path`. Da igual
si escribes `audio`, `audio/`, `/audio` o `audio\`. Si dejas `filename` vacío,
cae a `pick`.

Tiene salida `AUDIO`, así que el nodo puede quedarse en medio del grafo sin
romperlo.

### Uso

Conecta tu generador de audio al nodo y dale a **Run** con un *Batch count* de 4.
Ya ejecutado, el nodo se ve así:

```
┌─ 🐊 DRILO AudioMultiExport ────────────────────────────────────────────┐
│ audio           ●                                                       │
│ output_folder    [ audio                                         ]      │
│ filename         [ pick                                          ]      │
│ format           [ flac                                        ▾ ]      │
│ accumulate       [ ✔ ]                                                  │
│ resolve_bin      [ sfx                                           ]      │
│                                                                         │
│ 4 samples                                              [ Clear list ]   │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ ☐ #0  seed: 884120391  6.0s  ▶ ────●──── [→ Resolve]               │ │
│ │ ☑ #1  seed: 884120392  6.0s  ▶ ───────── [✓ Resolve] sfx · pick_00001
│ │ ☐ #2  seed: 884120393  6.0s  ▶ ───────── [→ Resolve]               │ │
│ │ ☐ #3  seed: 884120394  6.0s  ▶ ───────── [→ Resolve]               │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                       audio ●           │
└─────────────────────────────────────────────────────────────────────────┘
```

*(La interfaz del nodo está en inglés; esta documentación no.)*

Escuchas las cuatro. La #1 te gusta: marcas su checkbox, la fila muestra
`saving...` un instante y luego se pone verde con la ruta final
`audio/pick_00001_.flac`. Si te arrepientes, al desmarcar se borra de `output/`.

Vuelves a darle a **Run**. Con `accumulate` activado, las cuatro muestras nuevas
se apilan debajo (`#4`…`#7`) en vez de reemplazar la lista, y la #1 sigue
marcada. A partir de 8 filas el nodo deja de crecer y la lista hace scroll.
**Clear list** vacía la vista sin tocar nada de lo que ya guardaste.

### Detalles de comportamiento

- **Los previews viven en `temp/`.** Cada muestra se escribe ahí como FLAC con
  nombre único. Ese no es el archivo definitivo; el definitivo solo aparece
  cuando marcas el checkbox.
- **La numeración nunca sobrescribe.** El guardado pasa por
  `folder_paths.get_save_image_path`, respeta el prefijo y coge el primer hueco
  libre (`_00001_`, `_00002_`, …).
- **Reencodado bajo demanda.** Elegir `mp3` u `opus` reencodea al guardar. Con
  `flac` es una copia directa.
- **Seed y prompt.** Los dos se encuentran recorriendo el grafo del prompt hacia
  arriba hasta el sampler más cercano. Si no hay ninguno, la fila muestra
  `seed: -` y no falla nada.
- **Sin caché.** `IS_CHANGED` devuelve `NaN`, así que el nodo se reejecuta en
  cada Run de un batch en vez de reutilizar el resultado anterior.
- **Previews caducados.** `temp/` se limpia al reiniciar ComfyUI. Al recargar un
  workflow guardado, las filas cuyo audio ya no existe se marcan como
  `preview expired` y su checkbox se deshabilita, en vez de romper el widget.
- **El estado sobrevive.** Los checkboxes y las rutas guardadas viven en las
  propiedades del nodo, así que aguantan repintados y colapsar/expandir.

---

## Envío a DaVinci Resolve

Cada fila tiene un botón `→ Resolve` que importa el archivo al media pool, al
bin indicado en `resolve_bin`, y lo etiqueta:

| Campo en Resolve | Contenido |
| --- | --- |
| Clip Name | `pick_00003 - seed 70408609449185` |
| Description | el prompt positivo del sampler ascendente (recortado a 300 caracteres) |
| Comments | `seed … \| flac \| 19.969s \| 44100 Hz \| ComfyUI DRILO AudioMultiExport` |
| Keywords | `comfyui, drilo, audiomultiexport` |

Esa trazabilidad es el punto: un bin con 40 sfx generados es, si no,
indistinguible.

**El botón está deshabilitado hasta que marcas el checkbox.** No es un capricho
de interfaz: el media pool referencia los archivos por ruta, así que importar un
preview de `temp/` dejaría un clip roto en el bin en cuanto ComfyUI se
reiniciara. Solo se envía lo que ya está en `output/`.

Por el mismo motivo, **desmarcar una fila que ya enviaste avisa en ámbar**: el
archivo se borra pero el clip sigue en el media pool apuntando a la nada. No se
quita automáticamente porque podrías tenerlo ya en una timeline, y quitarte un
clip de debajo es peor que un aviso.

### Pegado en la timeline

Con `resolve_placement = playhead` el clip va a la pista `resolve_audio_track` en
la posición del cabezal. Con `end` se pega al final. La pista se crea si no
existe.

**Cuidado: `playhead` solo funciona si ese tramo de la pista está libre.** Medido
contra Resolve 21, cuando `recordFrame` cae sobre material existente,
`AppendToTimeline` hace dos cosas distintas y ninguna buena: en un caso no colocó
nada y **devolvió un objeto que parecía válido, con un `GetStart()` que repetía
el frame pedido**; en otro colocó un clip de 20 s **recortado a 5 s**.

Así que el puente no se cree el valor de retorno. Fotografía la pista antes y
después de pegar y compara. Si no apareció nada, avisa en ámbar de que el clip
está en el bin pero no en la timeline. Si entró recortado, lo dice con los frames
reales. La posición que se muestra es siempre la real, leída de la pista, nunca
la que se pidió.

Si la pista está ocupada, usa `end` o apunta a una pista vacía.

El cálculo de timecode a frame está verificado en 24 fps non-drop (ida y vuelta
exacta, y una hora de drop-frame da los 107892 frames canónicos). La rama
drop-frame (29.97/59.94) **no está probada contra una timeline real**.

### Requisitos y fragilidad

Resolve tiene que estar **abierto**, con un proyecto cargado, y
*Preferences > System > General > External scripting using* en **Local**. Si no,
la fila informa del motivo y el resto del nodo sigue funcionando con normalidad.

Lo delicado: `fusionscript` es una extensión C, así que solo carga en la versión
de Python para la que se compiló. Verificado funcionando: **Resolve 21.0.3 con
Python 3.13** (el que trae el ComfyUI portable). El **Python 3.11 del sistema
crashea el proceso** con violación de acceso. Si actualizas Resolve o ComfyUI
cambia de Python, esto puede dejar de cargar — por eso el puente reporta los
fallos de import como texto en vez de dejar que se propaguen.

---

## Normalización

Se aplica **al guardar**, no al preview: la fila reproduce el audio crudo y el
archivo en `output/` es el normalizado. La fila muestra qué se midió y qué
ganancia se aplicó, por ejemplo `(-19.19 LUFS → -3.81 dB)`.

El medidor LUFS es BS.1770-4 con K-weighting y los dos gates (absoluto a
-70 LUFS, relativo a -10 LU). Calibrado contra la referencia de la norma: un seno
de 1 kHz a -20 dBFS en un solo canal de un par estéreo mide **-23,004 LUFS** (la
norma dice -23,000). Los coeficientes del filtro solo valen a 48 kHz, así que la
medición resamplea ahí; la ganancia se aplica al audio original.

Dos guardas que importan:

- **Nunca clipea.** Si el objetivo exige más ganancia de la que cabe, se recorta
  para que el pico quede en -0,1 dBFS y la fila avisa en ámbar.
- **No amplifica silencio.** Por debajo de -60 dB no toca nada. Sin esa guarda,
  normalizar un clip mudo pedía +239 dB.

En la práctica, con audio generado que ya viene pegado al techo (-0,5 dBFS es lo
típico), pedir `-14 LUFS` casi no hace nada porque no hay sitio: la ganancia se
recorta a unos +0,4 dB. Para ese material `-23 LUFS` sí atenúa de verdad, y
`peak` a -3 dBFS es lo más predecible para conseguir headroom uniforme en un
reel.

---

## 🐊 DRILO MultiPrompt

Varias cajas de texto; cada Run emite la siguiente.

```
┌─ 🐊 DRILO MultiPrompt ───────────────────┐
│ index            [ 3 ]                   │
│ control_after_g. [ increment          ▾ ] │
│ skip_empty       [ ✔ ]                   │
│                                          │
│ 3 boxes                        [ − ] [ + ]│
│  1 ┌────────────────────────────────┐ ×  │
│    │ deep sub bass drone, slow      │     │
│    │ pulse, dark cinematic          │     │
│  2 ├────────────────────────────────┤ ×  │  ← borde verde = la caja que salió
│    │ bright glass bells, sparkling  │     │
│  3 ├────────────────────────────────┤ ×  │
│    │ gritty analog saw lead         │     │
│    └────────────────────────────────┘     │
│ index 3 -> box 1 - 3 with text           │
│         prompt ● box_index ● info ●      │
└──────────────────────────────────────────┘
```

| Entrada | Por defecto | Qué hace |
| --- | --- | --- |
| `index` | `0` | Qué caja sale. Con `control_after_generate` en `increment` avanza en cada Run. |
| `skip_empty` | `True` | Salta las cajas vacías en vez de emitir texto vacío. |

Salidas: `prompt` (STRING, al input `text` del CLIPTextEncode), `box_index` (INT)
e `info` (STRING, tipo `box 2/3`).

### Cómo usarlo

Conecta `prompt` al `text` de tu CLIPTextEncode (convierte el widget en input),
pon `index` en `increment`, la seed del KSampler en `fixed`, y lanza con
*Batch count* = número de prompts. Cada toma usa un prompt distinto sobre el
mismo ruido de partida.

Verificado: 3 cajas y 4 generaciones dieron una sola seed (12345), cuatro índices
(0-3), y **tres audios únicos de cuatro archivos** — las tomas 1 y 4 compartían
caja y salieron byte a byte idénticas. Eso confirma las dos mitades a la vez: el
prompt cambia el resultado y la seed no se movió.

### Detalles

Los botones `+` y `−` añaden y quitan cajas (de 1 a 32), y la `×` de cada fila
borra esa caja concreta. A partir de 5 cajas el nodo deja de crecer y la lista
hace scroll.

El texto no vive en un widget por caja: se guarda como un array JSON en un widget
`prompts_json` que se oculta en el canvas. Es a propósito — los
`widgets_values` de ComfyUI son posicionales, así que añadir y quitar widgets
reales desalinearía cualquier workflow guardado. Con un solo campo, cambiar el
número de cajas no rompe nada. Si ese JSON llegara corrupto, su contenido se
trata como una única caja en vez de descartar lo que hubieras escrito.

**Los dos nodos se entienden.** El prompt activo no aparece en ninguna parte del
grafo de ComfyUI, porque se calcula a partir del array y el índice, así que el
rastreo de prompts del AudioMultiExport le pregunta al nodo directamente. El
resultado es que una muestra guardada llega a Resolve con el prompt real que la
generó en su campo Description, en vez de vacío.

---

## Por qué el contenido del widget se ancla al nodo a mano

El elemento host que ComfyUI entrega a un widget DOM **es más ancho que el nodo**
(medido: 1272 px de host para un nodo de 320 px). Un `width: 100%` se resuelve
contra ese host, no contra el nodo, y el contenido se sale por la derecha: el
cuerpo del nodo se pinta en el canvas y el widget es una capa DOM encima, así que
nada recorta el desbordamiento.

Por eso cada repintado fija `max-width` en píxeles a partir de `node.size[0]`
menos el margen lateral, y `onResize` lo recalcula al arrastrar la esquina. Va en
unidades de grafo, así que aguanta cualquier zoom. Si algún día ComfyUI
dimensiona el host al nodo, el clamp no molesta: `width: 100%` será menor y gana.

Además, las filas usan grid de CSS con `minmax(0, 1fr)` en vez de flex. Un hijo
flex tiene `min-width: auto` por defecto y se niega a encogerse por debajo de su
ancho de contenido; el textarea propio de ComfyUI lleva `min-width: 0` justo por
eso. Las dos piezas hacen falta: el clamp fija el ancho disponible y el grid
reparte dentro sin que ninguna columna pueda empujar.

---

## El emoji y el nombre de la carpeta

El 🐊 va en los **nombres visibles** de los nodos, no en sus identificadores
internos, que siguen siendo `DRILO_AudioMultiExport` y `DRILO_MultiPrompt`. Un
emoji en el identificador acabaría en el JSON de cada workflow guardado y en el
buscador de nodos, sin ganar nada.

El badge del pack que ComfyUI dibuja sobre el nodo (`#60 DRILO-tools`) sale de
`python_module`, es decir **del nombre de la carpeta**. Un `pyproject.toml` con
`[tool.comfy] DisplayName` no lo cambia en el frontend actual.

Renombrar la carpeta a `🐊 DRILO-tools` **no funciona**: probado, el Python carga
bien y los nodos se registran, pero ComfyUI publica el web dir como
`/extensions/%F0%9F%90%8A%20DRILO-tools/…` y ese path devuelve 404 con todas las
codificaciones probadas (cruda, simple, doble, `+` por espacio). El resultado es
que los dos `.js` no cargan y los nodos se quedan sin widget. De ahí que la
carpeta siga siendo `DRILO-tools`.

---

## Endpoints HTTP

Todos POST con cuerpo JSON:

| Endpoint | Cuerpo | Respuesta |
| --- | --- | --- |
| `/audio_picker/save` | `filename`, `subfolder`, `node_id`, `index`, `uid`, `filename_prefix`, `format` | `{saved_path, normalize}` |
| `/audio_picker/unsave` | `node_id`, `index`, `uid` | `{ok, removed, warning}` |
| `/audio_picker/clear` | `node_id` | `{ok, cleared}` |
| `/audio_picker/to_resolve` | `node_id`, `index`, `uid`, `bin`, `placement`, `track_index` | `{clip_name, bin, project, metadata_applied, placed, warnings}` |

Más un `GET /audio_picker/resolve_status`, que devuelve
`{available, version, project}` o `{available: false, error}`.

Las rutas de los endpoints conservan un nombre interno antiguo; renombrarlas
invalidaría el estado del widget ya serializado dentro de workflows guardados.

### Seguridad de rutas

`save` rechaza cualquier `filename`/`subfolder` que resuelva fuera de `temp/`, y
`unsave` solo borra cuando la ruta registrada está dentro de `output/`. El
traversal por prefijo (`..`) lo rechaza el propio `get_save_image_path` de
ComfyUI, y el error se muestra en la fila.

---

## Limitaciones conocidas

- El registro de lo que se ha guardado vive en la memoria del servidor. Tras un
  reinicio de ComfyUI, desmarcar una fila ya no puede borrar su archivo. En la
  práctica queda tapado: tras un reinicio el preview de `temp/` tampoco está, así
  que la fila sale como caducada con su checkbox deshabilitado.
- Los clips de Resolve nunca se quitan automáticamente, solo se avisa.
- La rama de timecode drop-frame no está verificada contra una timeline real.
- El pesado de canales del medidor de loudness cubre mono y estéreo; no hay
  soporte de surround.

## Licencia

MIT.
