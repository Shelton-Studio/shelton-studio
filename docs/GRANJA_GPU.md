# Granja de GPUs y calidad máxima

Todo esto **se suma** a lo que ya existe: los operadores siguen abriendo la modelo y apretando **«Entrenar»** como siempre.
Shelton decide solo dónde entrena cada una.

## 1. Calidad máxima (con la 3090 de hoy)

Arriba a la derecha: botón **✨ Calidad**.

| Opción | Qué hace |
|---|---|
| Calidad máxima | Activa lo de abajo para las fotos locales (modo Local + LoRA) |
| Segunda pasada de detalle | Genera 1,5x más grande y afina suave: piel, pelo y telas más nítidos |
| Mejora de rostro | Rehace la cara en alta resolución con la LoRA. Necesita el Impact Pack (ver abajo) |
| Fotos por intento | Genera varias y el Juez se queda con la mejor |
| Descripciones detalladas al entrenar | Describe cada foto (pose, ropa, luz, fondo) para que la LoRA aprenda a la persona y no la ropa |

**Instalar la mejora de rostro** en la placa (root), sin nada entrenando:

```bash
bash /root/shelton-studio/scripts/quality_nodes.sh /root/ComfyUI
```

```bash
systemctl restart shelton-studio-comfy
```

(`gpu_setup.sh` ya lo hace solo de ahora en adelante.)

## 2. Comparar versiones de una LoRA

Banco de modelos → la modelo → **🔍 Comparar versiones**.

- Shelton hace las mismas 4 fotos (misma escena y semilla) con cada versión elegida y, si quieren, con varias fuerzas (0,7 / 0,8 / 0,9 / 1,0).
- Se ven lado a lado. **«Usar esta»** deja esa versión activa, con esa fuerza, y el próximo «seguir entrenando» sigue desde ahí.
- No borra nada. Si la GPU está entrenando, la comparación espera su turno.

## 3. Entrenando ahora

Arriba del Banco aparece qué está entrenando, con su porcentaje y el tiempo que falta, y quién está en fila.

## 4. Más 3090 en la placa

1. En `/root/shelton-studio/.env` agregar `LORA_GPUS=0,1,2`, una entrada por GPU.
2. Si cada GPU tiene su propio ComfyUI, agregar cada uno en **Conexiones → ComfyUI** con el campo **«GPU de la placa»** (0, 1, 2).
3. Reiniciar Shelton: `systemctl restart shelton-studio`.

Desde ahí, la fila entrena una modelo por GPU a la vez. Mientras una GPU entrena, solo se pausa el ComfyUI de esa misma GPU.

## 5. Tarjetas alquiladas (RunPod / Vast)

**Recomendado:** RunPod → *Secure Cloud* → **RTX PRO 6000** + **Network Volume** montado en `/workspace`. Así los ~20-30 GB de modelos se bajan una sola vez.

1. **Tailscale:** en https://login.tailscale.com → Settings → Keys → *Generate auth key* (reutilizable). No la pegues en ningún chat.
2. **En la terminal de la tarjeta alquilada (root):**

```bash
export WORKER_TOKEN="inventa-una-clave-larga"
```

```bash
export TS_AUTHKEY="tskey-auth-..."
```

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juancamp7777/alligator-AI/main/scripts/node_setup.sh)
```

   Opcionales, antes del último comando:
   - `export WORKER_GPUS="MIG-...,MIG-...,MIG-..."`: una RTX PRO 6000 dividida en 3 (MIG). Sin esto, un entrenamiento por GPU.
   - `export NODE_COMFY=1`: además instala ComfyUI con los modelos, para que la tarjeta también genere fotos.

3. Al final muestra la **URL del trabajador** (`http://100.x.x.x:8299`).
4. En Shelton: **Conexiones → Agregar → «Nodo de GPU (trabajador)»**:
   - URL y token;
   - **Entrenamientos a la vez:** 1, o 3 si está dividida;
   - **Perfil:** `pro` para las tarjetas grandes (LoRA rango 32, hasta 1280 px, sin comprimir, descripciones detalladas);
   - **Costo por hora**, para que aparezca en Consumo.

   Si pusiste `NODE_COMFY=1`, agrega también su ComfyUI como conexión **ComfyUI**.
5. Revisa **Granja de GPUs** (menú, solo administradores): tiene que decir **conectado**.

**Cómo trabaja:**
- La fila usa primero la placa y después los nodos con lugar libre.
- Sube las fotos y el punto de partida, sigue el avance, trae la LoRA a la placa (respaldo incluido), la copia a los nodos que generan y **borra las fotos del nodo**.
- Las LoRA se entrenan siempre sobre **Z-Image Turbo**, así generan en las 3090 de la placa cuando devuelvan la tarjeta alquilada.

**Al terminar:** apaga la tarjeta en RunPod (cobra mientras está prendida). Si la vuelves a prender con el mismo volumen:

```bash
bash /workspace/start_worker.sh &
```

## 6. Seguridad
- Todo pasa por Tailscale; el trabajador pide token.
- Las fotos se borran del nodo al terminar cada entrenamiento.
- Claves (Tailscale, RunPod, tokens) nunca en el chat ni en el repositorio.
