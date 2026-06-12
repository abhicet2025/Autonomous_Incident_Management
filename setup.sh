#!/bin/bash
echo "=== Restoring environment ==="
pip install chromadb langchain langchain-community langchain-core \
  sentence-transformers streamlit scikit-learn pandas numpy \
  prometheus-client python-dotenv httpx pydantic colorlog \
  langgraph --ignore-installed blinker --quiet
echo "=== All packages ready ==="

# Reinstall Ollama binary if missing
if ! command -v ollama &> /dev/null; then
    echo "=== Reinstalling Ollama ==="
    apt-get update -q && apt-get install -y zstd
    curl -fsSL https://ollama.com/install.sh | sh
    echo "=== Ollama reinstalled ==="
else
    echo "=== Ollama already installed ==="
fi

# Start Ollama server if not running
echo "=== Starting Ollama server ==="
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "=== Ollama already running ==="
else
    ollama serve > /workspace/shared/ollama.log 2>&1 &
    sleep 5
    echo "=== Ollama server started ==="
fi

# Pull model if not available
echo "=== Checking LLM model ==="
if ! ollama list | grep -q "qwen2.5:32b"; then
    echo "=== Pulling qwen2.5:32b ==="
    ollama pull qwen2.5:32b
    echo "=== Model ready ==="
else
    echo "=== qwen2.5:32b already available ==="
fi

# Start HITL Portal
echo "=== Starting HITL Portal ==="
pkill -f "streamlit run hitl" 2>/dev/null
sleep 2
cd /workspace/shared/incident_agent
mkdir -p ~/.streamlit
cat > ~/.streamlit/config.toml << 'TOML'
[server]
port = 8501
address = "0.0.0.0"
enableCORS = false
enableXsrfProtection = false
headless = true

[browser]
serverAddress = "notebooks.amd.com"
serverPort = 443
gatherUsageStats = false
TOML
streamlit run hitl/hitl_portal.py > /workspace/shared/hitl_portal.log 2>&1 &
sleep 3
echo "=== HITL Portal started ==="

# Start Dashboard
echo "=== Starting Dashboard ==="
pkill -f "streamlit run dashboard" 2>/dev/null
sleep 2
streamlit run dashboard.py --server.port 8502 > /workspace/shared/dashboard.log 2>&1 &
sleep 3
echo "=== Dashboard started ==="

# Verify everything
echo ""
echo "========================================"
echo "=== Environment Status ==="
python3 -c "import chromadb, langchain, streamlit, sklearn, langgraph; print('✅ Packages: OK')"
echo "✅ Ollama: $(ollama list | grep qwen2.5 | awk '{print $1}')"
echo "✅ HITL Portal: http://localhost:8501"
echo ""
echo "=== HITL Portal URL ==="
INSTANCE=$(hostname)
echo "https://notebooks.amd.com/${INSTANCE}/proxy/8501/"
echo ""
echo "=== Dashboard URL ==="
echo "https://notebooks.amd.com/${INSTANCE}/proxy/8502/"
echo "========================================"
echo "=== Ready to go! ==="
echo ""
echo "To run incidents:"
echo "  Option 1: Use the Dashboard Control Panel (recommended)"
echo "  Option 2: Run manually via terminal:"
echo "    cd /workspace/shared/incident_agent"
echo "    python3 main_runner.py --scenario DB_CONN_EXHAUSTION"
echo "    python3 main_runner.py --continuous"
echo "    python3 main_runner.py --all"
echo ""
echo "IMPORTANT: For HITL approvals to auto-trigger remediation, run in a"
echo "separate terminal:"
echo "  python3 hitl_processor.py"
