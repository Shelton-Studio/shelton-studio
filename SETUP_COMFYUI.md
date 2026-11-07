# ComfyUI Setup Guide for Shelton

This guide helps you set up ComfyUI with Z-Image Turbo and Qwen-Image-Edit models.

## Prerequisites

- NVIDIA GPU with 8GB+ VRAM (recommended: 12GB+)
- 50GB+ free disk space
- Python 3.10+
- Git

## Installation Steps

### 1. Install ComfyUI

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt
```

### 2. Download Z-Image Turbo Models

```bash
cd ComfyUI/models/diffusion_models/
# Download z_image_turbo_bf16.safetensors (or fp8 for smaller VRAM)
# From: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo

cd ../text_encoders/
# Download qwen_3_4b
# From: https://huggingface.co/Tongyi-MAI/Z-Image-Turbo
```

### 3. Download Qwen-Image-Edit Models

```bash
cd ComfyUI/models/diffusion_models/
# Download qwen_image_edit_2511.safetensors (bf16 or fp8)
# From: https://huggingface.co/QwenTeam/Qwen-Image-Edit-2511

cd ../text_encoders/
# Download qwen_2.5_vl_7b_fp8_scaled
# From: https://huggingface.co/Qwen/Qwen-VL
```

### 4. Install Custom Nodes (Optional)

For enhanced ComfyUI workflows:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Curated-NODE-LIST/custom_nodes.git
```

### 5. Start ComfyUI

```bash
cd ComfyUI
python main.py
```

ComfyUI will start on `http://localhost:8188`

## Memory Requirements

| Model Version | VRAM | Notes |
|--------------|------|-------|
| Z-Image Turbo BF16 | 14-16 GB | Full quality |
| Z-Image Turbo FP8 | 8 GB | Nearly same quality, smaller |
| Qwen-Image-Edit BF16 | 40 GB | Requires pro GPU |
| Qwen-Image-Edit FP8 | 16-20 GB | Recommended |
| Qwen-Image-Edit GGUF Q4 | 13 GB | Good quality |

## Configuration in Shelton

In your `.env` file:

```env
COMFYUI_LOCAL_URL=http://localhost:8188
```

## Testing the Setup

```bash
# Test ComfyUI is running
curl http://localhost:8188/system_stats

# Test Shelton integration
python cli/shelton_cli.py health
```

## Troubleshooting

### Out of Memory
- Use FP8 instead of BF16
- Use GGUF quantized models
- Reduce batch size in workflows

### Model Not Found
- Verify files are in correct directories
- Check exact filenames match
- Restart ComfyUI after adding models

### Slow Generation
- Check GPU is being used (nvidia-smi)
- Reduce number of steps
- Use Lightning accelerator

## Advanced: Cloud ComfyUI

For cloud ComfyUI (Replicate, RunPod, etc.):

1. Set up cloud instance
2. Get API URL and token
3. In `.env`:
   ```env
   COMFYUI_CLOUD_URL=https://your-cloud-instance.com
   COMFYUI_CLOUD_TOKEN=your_api_token
   ```
4. In Shelton, choose `cloud` location when generating

## Resources

- [ComfyUI GitHub](https://github.com/comfyanonymous/ComfyUI)
- [Z-Image Turbo HF](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)
- [Qwen-Image-Edit HF](https://huggingface.co/QwenTeam/Qwen-Image-Edit-2511)
- [ComfyUI Wiki](https://github.com/comfyanonymous/ComfyUI/wiki)
