# Shelton Studio — ecosistema multiagente para contenido de redes

Le hablas a **Shelton** como a una persona ("hazme 50 posts de esta semana", "crea a Valentina y haz un reel de ella en la playa con voz cálida", "ponle sonido de motor V8 a este video") y su equipo de agentes lo deja **listo para publicar**: imagen o video en el formato exacto de cada red, texto, hashtags, voz y sonido.

**Local primero.** Imagen, edición, video, voz y efectos de sonido corren en tu GPU (ComfyUI + servidor de voz). Lo único externo es el cerebro (Claude). Las APIs en la nube son enchufes opcionales.

**Decide una persona.** Los agentes proponen, el juez opina, pero nada se aprueba, publica ni borra sin una persona. Todo lo que un agente quiera hacer "hacia afuera" pasa por la bandeja de **Aprobaciones**.

---

## El equipo

| Agente | Qué hace |
|---|---|
| **Shelton** (jefe) | Conversa contigo, decide y dirige a todos. Lee la memoria de cada subagente, detecta contradicciones entre ellos y propone soluciones |
| Director creativo | Convierte ideas en prompts (foto, óleo, anime, 3D, naves, familias, productos…) |
| Editor | Traduce "cámbiale el fondo" en instrucciones precisas sin estropear el resto |
| Juez de calidad | *Mira* cada imagen y video (3 cuadros), puntúa y pide reintentos. Compara ediciones con el original. **Solo opina** |
| Copywriter | Caption, hashtags y texto alternativo por red |
| Trends y Video | Dirige el movimiento de cada clip (Wan 2.2) |
| Locutor | Escribe el guion y diseña la voz: grave, aguda, femenina, sensual, adulta, anciana, robotizada, épica… |
| Diseñador de sonido | Efectos y ambientes: motor rugiendo, moto, olas, ciudad |
| Planificador | Campañas de 10, 50 o 200 posts distintos |
| Diseñador de modelos | Crea personajes, autos, naves, lugares, productos o estilos del banco |
| Investigador | Busca en internet y te da opciones con fuentes |
| Scout de tendencias / de tools | 24/7: tendencias para replicar con tus modelos y herramientas locales nuevas |

## Qué incluye

- **Chat** con Shelton o directo con cualquier agente, selector de **motor** por conversación (con ayuda "?").
- **Cadena en una orden**: "haz un reel de 3 escenas" → imagen → juez → video → juez → montaje → texto → voz.
- **Banco de modelos**: personajes, autos, naves, lugares, productos y estilos con nombre propio, fotos de referencia y **voz propia** (clonada). Shelton los reconoce por su nombre.
- **Galería** con versiones (nada se borra), intentos que el juez reprobó (puedes elegirlos), edición, animación, voz, sonido, aprobación, publicación y descarga ZIP + CSV.
- **Campañas** y **piloto automático** (N posts por día a una hora).
- **Tareas** (tablero En cola / En curso / Esperando aprobación / Hecha / Fallida) y **Aprobaciones**.
- **Agentes**: ficha editable (instrucciones, modelo, esfuerzo, versión) y **entrenamiento humano**.
- **Memoria**: lecciones con fecha y hora en `data/training/<agente>/`, aprendizaje automático de tus decisiones, **detección de contradicciones** (activar igual bajo tu responsabilidad / reemplazar / descartar) y **revisión diaria del equipo**.
- **Tendencias**: hallazgos del scout con ideas listas para replicar con un clic.
- **Voces**: banco de voces y laboratorio para escuchar antes de usar.
- **Conexiones**: varias GPUs (reparte al más libre), fal.ai, Replicate, OpenAI, Gemini, cualquier API HTTP, ElevenLabs, Telegram, Instagram, Facebook, Discord, webhooks a n8n/Make/Zapier y avisos de eventos. Cada una con botón **Probar**.
- **Flujos**: sube tus workflows de ComfyUI.
- **Inicio** con métricas (ejecuciones, fallos, tokens y costo, posts por día, nota del juez, tasa de aprobación, tiempo por post), **Actividad**, buscador **Ctrl+K** y avisos en vivo.
- **Marca blanca**: nombre, logo, colores y nombre del jefe desde Ajustes, para revender o replicar.

---

## Instalación (local)

### 1. ComfyUI y modelos (máquina con GPU)

Instala [ComfyUI](https://github.com/comfyanonymous/ComfyUI) actualizado. Descarga los modelos (Hugging Face) en `ComfyUI/models/`:

| Para | Archivo | Carpeta | Repositorio |
|---|---|---|---|
| Imagen rápida | `z_image_turbo_bf16.safetensors` | `diffusion_models` | Comfy-Org/z_image_turbo |
| | `qwen_3_4b.safetensors` | `text_encoders` | Comfy-Org/z_image_turbo |
| | `ae.safetensors` | `vae` | Comfy-Org/z_image_turbo |
| Imagen calidad máxima | `qwen_image_fp8_e4m3fn.safetensors` | `diffusion_models` | Comfy-Org/Qwen-Image_ComfyUI |
| | `Qwen-Image-Lightning-8steps-V1.0.safetensors` | `loras` | lightx2v/Qwen-Image-Lightning |
| Edición y banco | `qwen_image_edit_2509_fp8_e4m3fn.safetensors` | `diffusion_models` | Comfy-Org/Qwen-Image-Edit_ComfyUI |
| | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders` | Comfy-Org/Qwen-Image_ComfyUI |
| | `qwen_image_vae.safetensors` | `vae` | Comfy-Org/Qwen-Image_ComfyUI |
| | `Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors` | `loras` | lightx2v/Qwen-Image-Lightning |
| Video cine | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` y `..._low_noise_...` | `diffusion_models` | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| | `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` y `..._low_noise...` | `loras` | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `text_encoders` | Comfy-Org/Wan_2.1_ComfyUI_repackaged |
| | `wan_2.1_vae.safetensors` | `vae` | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| Video rápido | `wan2.2_ti2v_5B_fp16.safetensors`, `wan2.2_vae.safetensors` | `diffusion_models`, `vae` | Comfy-Org/Wan_2.2_ComfyUI_Repackaged |
| Efectos de sonido | `stable_audio_open_1.0.safetensors` | `checkpoints` | stabilityai/stable-audio-open-1.0 |
| | `t5_base.safetensors` | `text_encoders` | Comfy-Org/stable-audio-open-1.0_repackaged |

Los nombres se cambian en `.env`. **Ajustes → Estado** te dice exactamente qué modelo o nodo falta en tu ComfyUI. Si en Hugging Face un archivo cambió de nombre o de repositorio, descarga el equivalente y ajusta su nombre en `.env`.

- **RTX 3090 (24 GB):** todo con los valores por defecto (Qwen-Image y Wan 14B en fp8).
- **GPU de 6-8 GB (3050):** `GEN_PRESET=lite` (Z-Image GGUF, requiere el nodo ComfyUI-GGUF) y `GEN_SCALE=0.75`. Edición, Qwen-Image y video 14B: mejor en la 3090.
- **Varias GPUs:** agrega cada ComfyUI en **Conexiones** con los mismos roles; Shelton reparte.

### 2. Voz local (opcional, recomendado)

```bash
cd tts_server
python -m venv venv
```

```bash
venv/bin/pip install -r requirements.txt
```

```bash
venv/bin/python server.py
```

(En Windows: `venv\Scripts\pip` y `venv\Scripts\python`). Chatterbox Multilingual: español y 22 idiomas más, clona voces con 5-10 s de muestra. La primera vez descarga el modelo.

### 3. Shelton

```bash
cp .env.example .env
```

```bash
./start.sh
```

En Windows: doble clic en `start.bat`. Abre http://127.0.0.1:8000

### Cerebro: Anthropic u OpenRouter

Pon `ANTHROPIC_API_KEY` (recomendado) o, si no tienes, `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` (por ejemplo `anthropic/claude-sonnet-4.5`). También se configuran en **Conexiones → Cerebro**, con lista de modelos y botón Probar. Con OpenRouter la búsqueda web de scouts e investigador usa la de OpenRouter (cobro aparte) y el JSON entre agentes se valida y reintenta si hace falta.

### Modo demo (sin GPU ni API key)

```bash
./demo.sh
```

En Windows: `demo.bat`. Interfaz completa con imágenes y respuestas simuladas, para conocerlo o mostrarlo.

---

## Replicarlo en otro servidor (guía rápida)

1. **Servidor Linux** (Ubuntu/Debian; un droplet sirve). Instala con un solo comando. Volver a correrlo actualiza:

   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/juancamp7777/alligator-AI/main/install.sh)
   ```

   - Se instala en `~/shelton-studio` sin tocar otras apps (otra carpeta con `SHELTON_DIR=...`).
   - Elige un puerto libre desde el 8010.
   - Pide la clave sin mostrarla.
   - Queda como servicio `shelton-studio`.
2. **Acceso privado con Tailscale** (recomendado): instala Tailscale en el servidor y en tu equipo. Si el instalador detecta Tailscale, escucha solo en esa red. Abre `http://<ip-tailscale>:<puerto>`.
3. **Clave de OpenRouter**: con ella funcionan el cerebro, las imágenes y el video en la nube. Va en `.env` o en la web (**APIs** / **Conexiones**). No la pegues en el chat.
4. **Modelos**:
   - En **Auto**, Shelton ve todos los modelos de imagen y video disponibles (`/api/models-catalog`) y elige el que más conviene a cada pedido: Nano Banana Pro para máximo realismo, Veo 3.1 para video con audio, uno económico para pruebas…
   - Si no elige ninguno, se usa el más realista disponible. Puedes fijar uno en **🎬 Video**, en **APIs** o con `OPENROUTER_IMAGE_MODEL` / `OPENROUTER_VIDEO_MODEL` en `.env` (`auto` = el mejor).
5. **Switch Local / Auto / Nube**:
   - **Local** usa tus GPUs (ComfyUI, sección 1).
   - **Nube** usa las APIs.
   - **Auto** deja que Shelton decida.

   Cada GPU nueva se agrega en **Conexiones → ComfyUI**, y el trabajo se reparte a la menos ocupada.
6. **Servidor chico**: si la web se pone lenta mientras arma videos, deja `FFMPEG_PARALLEL=1` (por defecto) y `FFMPEG_PRESET=veryfast`.

Comandos útiles:

```bash
sudo systemctl status shelton-studio
```

```bash
journalctl -u shelton-studio -n 100 --no-pager
```

### Shelton mini, voz, audios y copiar videos

- **Shelton mini** (el robot de abajo a la derecha):
  - Tócalo y dile lo que quieres, escribiendo o hablando (🎙️).
  - Trabaja en paralelo mientras lo demás se procesa. Si le pides varias cosas seguidas, cada una va en su propia conversación.
  - Sugiere cómo aprovechar el tiempo: otro modelo, más fotos del mismo, revisar la galería…
  - Si le hablaste, te contesta en voz alta, como Siri.
  - Es el mismo Shelton, con todas sus herramientas, en una conversación paralela.
  - Muestra **en vivo** qué procesos están corriendo: qué agente o motor, con qué modelo y desde hace cuánto. También muestra lo que está en cola, una línea de actividad de la última hora, los trabajos listos y los errores.
- **Revisión de la red cada 2 horas**:
  - Shelton mini sale con el scout de tendencias y el de tools a buscar novedades afuera para traerlas acá.
  - Recalcula la **mejor hora para publicar** por red y por modelo del banco, mezclando lo de adentro (tu historial: qué publicas y qué apruebas de cada modelo) con lo de afuera (investigación web actual).
  - Se cambia con `NETWORK_REVIEW_HOURS` (0 = apagada). Cada revisión usa búsqueda web, que tiene costo.
  - La zona horaria se configura con `STUDIO_TIMEZONE`.
- **Dictado por voz** (botón 🎙️ del chat y de Shelton mini):
  - Grabas, se transcribe y se envía solo.
  - Si llegó muy bajo o no se entendió, te lo dice ("habla un poco más alto", "no te entendí bien, ¿lo repites?") y no manda nada.
  - Transcribe con `faster-whisper` si lo instalas en el servidor (local, gratis) o, si no, con el modelo de audio de OpenRouter (`OPENROUTER_AUDIO_MODEL`).
  - El navegador solo deja usar el micrófono con **https** o **localhost**. Con Tailscale, publica Shelton en https dentro de tu red privada:

    ```bash
    sudo tailscale serve --bg 8010
    ```

    Y abre `https://<nombre-de-la-máquina>.<tu-red>.ts.net`.
- **Generador de voz con OpenRouter** (la misma clave):
  - En Nube, y en Auto cuando el estudio trabaja con OpenRouter, el Locutor habla con el modelo de voz más pro disponible: ElevenLabs v4 primero, luego MAI-Voice, Gemini TTS…
  - Elige la voz que encaja con lo que pides (femenina, grave, anciana, joven…) y cada post muestra "modelo · voz".
  - Para fijar un modelo, usa `OPENROUTER_TTS_MODEL` o agrega la conexión «OpenRouter (voz)» con una voz fija.
  - En Local se usa la voz de tu GPU (Chatterbox).
  - La clonación de voz de los modelos del banco hoy funciona con Chatterbox local.
- **Audios**: sube WAV/MP3 (o un video y se guarda su audio) en **Audios** o con 📎 en el chat. Shelton se los pone a cualquier post ("ponle el audio *Tema verano* al reel de Valentina"). Desde la ficha de un video puedes guardar su audio en la biblioteca.
- **Copiar videos**:
  - Pega el link de un TikTok, Reel o Short (o sube el video) y pide "hazme algo igual con mi modelo".
  - El Analista de videos lo desarma escena por escena y guarda su audio.
  - Cada escena se recrea con tu modelo del banco: mismo fondo, encuadre y movimiento, con otra cara. Luego se monta el reel, con el audio original si quieres.
  - También puedes **pegar el link directo en el chat** (o en Shelton mini) de cualquier red: TikTok, Instagram, YouTube, X, Facebook, Pinterest, Threads, Kwai… Shelton lo detecta solo. Si ese link ya se analizó, lo reutiliza.
  - Usa solo modelos con permiso. Si el audio es música de otra persona, revisa los derechos antes de publicarlo fuera de esa red.
- **Memoria por modelo**:
  - Cada modelo del banco tiene su propia memoria (🧠 en el Banco).
  - Las correcciones ("el lunar va en la mejilla izquierda") se aplican cada vez que se usa ese modelo.
  - Shelton pasa cada lección, adaptada, a los demás agentes que la necesitan.
- **Exportar todo** (Ajustes): un ZIP con contenido, banco, audios, voces, memoria, referencias y un respaldo para restaurar. Cada modelo y cada post también se pueden exportar por separado.

### Mover tus datos a otra máquina

Todo lo tuyo vive en dos lugares:

- `data/`: base de datos, imágenes, videos, voces, banco de modelos y memoria de los agentes.
- `.env`: claves y ajustes.

Para migrar:

1. Para el servicio.
2. Copia `data/` y `.env` a la nueva instalación.
3. Arranca el servicio otra vez.

```bash
sudo systemctl stop shelton-studio
```

```bash
rsync -a ~/shelton-studio/data ~/shelton-studio/.env usuario@nuevo-servidor:~/shelton-studio/
```

```bash
sudo systemctl start shelton-studio
```

Para una copia de seguridad basta con comprimir esas dos cosas. La memoria de los agentes también está en `data/training/<agente>/` como archivos Markdown con fecha, legibles a mano.

### Trabajar en el código

- Rama por cambio y PR a `main`.
- Pruebas con `python -m pytest -q tests`: usan un servidor de demo, sin GPU ni claves.
- Estructura en la sección **Estructura**.

---

## Formatos

Instagram feed 4:5 (1080×1350) · Story/Reels 9:16 (1080×1920) · TikTok 9:16 · Telegram 4:5 · Facebook 4:5 · X 16:9 · Pinterest 2:3 · Miniatura YouTube 16:9 · Cuadrado 1:1 · **Original** (fotos subidas: sin recortes). Videos en H.264, 30 fps, vertical.

## Usar tus propios workflows

En ComfyUI: **Save (API Format)**. Pon marcadores donde correspondan (`{{prompt}}`, `{{width}}`, `{{height}}`, `{{seed}}`, `{{steps}}`, `{{image}}`, `{{instruction}}`, `{{unet}}`, `{{clip}}`, `{{vae}}`, `{{lora}}`, `{{length}}`, `{{fps}}`, `{{negative}}`) y súbelo en **Flujos**. Luego indícalo en tu conexión ComfyUI o en `.env`.

## Estructura

```
app/
  main.py             servidor y API principal      api.py          panel (métricas, agentes, conexiones, memoria…)
  agents/shelton.py   Shelton (jefe) + herramientas  agents/specialists.py  subagentes
  agents/registry.py  fichas de agentes              agents/training.py     entrenamiento, contradicciones, revisiones
  pipeline.py         cola, cadena de producción, video, voz, scout, aprendizaje
  engines/            ComfyUI (local), nube, demo    comfyui/workflows/     flujos incluidos
  voice.py, video.py  audio y video (ffmpeg incluido)
  publishers/         publicación, aprobaciones, exportación
  connections.py      catálogo de conexiones         conn_tests.py          botón "Probar"
web/                  interfaz (sin dependencias)
tts_server/           servidor de voz local
tests/                pruebas de punta a punta + servidor demo
data/                 tu información (BD, imágenes, videos, voces, entrenamiento) — no se sube a git
```

## Pruebas

```bash
pip install pytest
```

```bash
python -m pytest -q
```

Recorren toda la cadena y cada pantalla de la API en modo demo: chat y herramientas de Shelton, banco de modelos, ediciones, alternativas del juez, video, reels, voz, sonido, campañas, aprobaciones, conexiones, webhooks, flujos, scout, entrenamiento, contradicciones, aprendizaje, revisiones, métricas, marca blanca y exportación.
