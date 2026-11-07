# Shelton — estudio de contenido multiagente para redes

Le dices a Shelton lo que quieres ("hazme 50 posts de esta semana para Instagram y TikTok", "crea a Valentina y ponla en la playa") y su equipo de agentes lo deja **listo para publicar**: imagen en el formato exacto de cada red, caption, hashtags y texto alternativo. Corre en local con tu GPU.

## Cómo funciona

**Shelton es el jefe.** Conversa contigo y dirige a sus subagentes con herramientas:

| Subagente | Qué hace |
|---|---|
| Director creativo | Convierte tu idea en el prompt ideal para el modelo de imagen |
| Motor de imagen | **Z-Image Turbo** (fotorrealista, ~8 pasos) y **Qwen-Image-Edit 2509** (ediciones y escenas con modelos del banco), vía ComfyUI |
| Juez de calidad | *Mira* cada imagen (visión), la puntúa y pide reintento si tiene defectos |
| Redactor | Caption, hashtags y alt text según la red y el tono de tu marca |
| Planificador | Diseña lotes grandes de ideas distintas (campañas de 10, 50, 200) |
| Diseñador del banco | Crea personajes, autos, lugares o productos recurrentes con foto de referencia |

```
pedido → Shelton → director creativo → motor de imagen → juez (reintenta si < nota) → recorte al formato → redactor → "Para revisar"
```

**Decide una persona.** El juez solo opina: nunca borra ni aprueba nada. Los intentos que reprueba quedan guardados y en el detalle del post puedes elegir cualquiera ("Usar esta"). Todo termina en *Para revisar* hasta que tú lo apruebes.

**Banco de modelos.** Crea un personaje, auto o lugar con nombre propio, o sube tus fotos. Cuando lo nombres en un pedido, Shelton lo busca y usa su foto como referencia en Qwen-Image-Edit para mantener la misma cara o el mismo auto en escenas nuevas.

**Volumen.** Las campañas se planifican en tandas y se procesan en cola: la GPU genera de a una imagen mientras Claude escribe y juzga en paralelo. Con el **piloto automático** (sección *Marca*) se lanza un lote diario de N posts a la hora que elijas.

## Instalación

### 1. ComfyUI + modelos (en la máquina con GPU)

Instala [ComfyUI](https://github.com/comfyanonymous/ComfyUI) actualizado y descarga los modelos en `ComfyUI/models/`:

| Archivo | Carpeta | Fuente (Hugging Face) |
|---|---|---|
| `z_image_turbo_bf16.safetensors` | `diffusion_models/` | `Comfy-Org/z_image_turbo` → `split_files/diffusion_models/` |
| `qwen_3_4b.safetensors` | `text_encoders/` | `Comfy-Org/z_image_turbo` → `split_files/text_encoders/` |
| `ae.safetensors` | `vae/` | `Comfy-Org/z_image_turbo` → `split_files/vae/` |
| `qwen_image_edit_2509_fp8_e4m3fn.safetensors` | `diffusion_models/` | `Comfy-Org/Qwen-Image-Edit_ComfyUI` |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders/` | `Comfy-Org/Qwen-Image_ComfyUI` |
| `qwen_image_vae.safetensors` | `vae/` | `Comfy-Org/Qwen-Image_ComfyUI` |
| `Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors` | `loras/` | `lightx2v/Qwen-Image-Lightning` |

Los nombres se pueden cambiar en `.env`. La pantalla **Estado** de Shelton te dice exactamente qué modelo o nodo falta en tu ComfyUI.

**RTX 3090 (24 GB):** todo funciona con los valores por defecto.
**GPU de 6-8 GB (p. ej. 3050):** usa Z-Image en GGUF (nodo [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)) con `WORKFLOW_GENERATE=z_image_turbo_gguf.json`, `GEN_UNET=z_image_turbo-Q4_K_M.gguf` y `GEN_SCALE=0.75`. Qwen-Image-Edit (20B) será muy lento ahí, o directamente no correrá: para la edición y el banco de modelos usa la 3090.
**Sin GPU:** `IMAGE_ENGINE=mock` genera imágenes de prueba para trabajar la interfaz.

### 2. Shelton

```bash
cp .env.example .env      # pon tu ANTHROPIC_API_KEY
./start.sh                # Windows: doble clic en start.bat
```

Abre http://127.0.0.1:8000

## Uso

- **Chat con Shelton:** pide lo que quieras en lenguaje natural. Puedes adjuntar fotos para editarlas o guardarlas en el banco.
- **Galería:** todo lo generado, con filtros por estado. Selecciona varios para aprobarlos o descargarlos en un ZIP (imágenes + textos + `posts.csv` para Metricool, Buffer, Meta Business Suite…).
- **Detalle del post:** edita el texto, pide cambios a la imagen (se crea una versión nueva; la original se conserva), revisa los intentos que el juez reprobó, aprueba o publica en Telegram.
- **Campañas:** lotes grandes desde un brief.
- **Banco de modelos:** personajes, autos, lugares y productos recurrentes.
- **Marca:** tono, estilo visual, pilares, hashtags fijos y piloto automático diario.

CLI (con el servidor corriendo):

```bash
python cli/shelton_cli.py chat
python cli/shelton_cli.py campaign "lanzamiento menú de otoño" -n 50
python cli/shelton_cli.py export --status approved -o semana.zip
```

## Formatos

Instagram feed 4:5 (1080×1350) · Story/Reels 9:16 (1080×1920) · TikTok 9:16 · Telegram 4:5 · Facebook 4:5 · X 16:9 · Pinterest 2:3 · Miniatura YouTube 16:9 · Cuadrado 1:1. Cada imagen se genera en su proporción nativa (~1,3 MP) y se recorta al tamaño exacto en JPEG de alta calidad.

## Usar tus propios workflows

Exporta cualquier workflow de ComfyUI con **Save (API Format)**, pon marcadores `{{prompt}}`, `{{width}}`, `{{height}}`, `{{seed}}`, `{{steps}}`, `{{unet}}`, `{{clip}}`, `{{vae}}`, `{{lora}}`, `{{image}}` o `{{instruction}}` donde correspondan y apunta `WORKFLOW_GENERATE` / `WORKFLOW_EDIT` / `WORKFLOW_SUBJECT` a ese archivo.

## Estructura

```
app/
  main.py              API y servidor web
  agents/shelton.py    Shelton (jefe): chat + herramientas
  agents/specialists.py subagentes: director creativo, juez, redactor, planificador, banco
  pipeline.py          cola de trabajos y cadena de producción
  engines/             motores de imagen (comfyui, mock)
  comfyui/             cliente de ComfyUI y plantillas de workflow
  publishers/          publicación (Telegram) y exportación ZIP/CSV
  platforms.py         formatos por red social
  db.py                base de datos (SQLite en data/)
web/                   interfaz
tests/                 prueba de la cadena completa sin GPU ni API
```

Pruebas: `pip install pytest && python -m pytest -q`
