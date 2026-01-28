#!/bin/bash
set -eox pipefail

# Test Cluster Deployment - Bash wrapper script for CAI
# This script:
# 1. Activates the virtual environment
# 2. Ensures we're in the project root directory
# 3. Calls the test deployment script
#
# Usage: bash build/shell_scripts/test_cluster.sh
# With options: bash build/shell_scripts/test_cluster.sh --workers 2 --cpu 32 --gpu 4

# Get the project root directory (parent of build/shell_scripts)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "="
echo "🧪 Testing Cluster Deployment"
echo "="
echo "Project root: $PROJECT_ROOT"
echo ""

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    echo "📦 Activating virtual environment..."
    source .venv/bin/activate
    echo "✅ Virtual environment activated"
    echo ""
else
    echo "❌ Virtual environment not found at .venv/bin/activate"
    echo "Please run setup_environment.py first"
    exit 1
fi

# Run the Python test script
echo "🔧 Starting cluster deployment test..."
python tests/test_cluster_deployment.py "$@"
