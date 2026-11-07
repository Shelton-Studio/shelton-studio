#!/bin/bash

# Shelton Quick Start Script

echo "🤖 Shelton - Multi-Agent Content Generation Platform"
echo "=================================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed"
    exit 1
fi

echo "✓ Python found: $(python3 --version)"

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
echo "✓ Activating virtual environment..."
source venv/bin/activate 2>/dev/null || . venv/Scripts/activate

# Install requirements
echo "📥 Installing dependencies..."
pip install -q -r requirements.txt

# Check .env
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Creating from template..."
    cp .env.example .env
    echo "   Please edit .env with your API keys"
fi

# Check ComfyUI
echo ""
echo "🔍 Checking ComfyUI connection..."
if curl -s http://localhost:8188/system_stats > /dev/null 2>&1; then
    echo "✓ ComfyUI is running on http://localhost:8188"
else
    echo "⚠️  ComfyUI not found on http://localhost:8188"
    echo "   Make sure ComfyUI is running in another terminal:"
    echo "   cd ComfyUI && python main.py"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📝 Next steps:"
echo "   1. Make sure ComfyUI is running (http://localhost:8188)"
echo "   2. Start Shelton server:"
echo "      python app/main.py"
echo ""
echo "   3. Open dashboard in browser:"
echo "      http://localhost:8000"
echo ""
echo "   4. Or use CLI:"
echo "      python cli/shelton_cli.py 'Your prompt here'"
echo ""
