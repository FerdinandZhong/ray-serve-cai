#!/bin/bash
set -eox pipefail

# Launch Ray Cluster - Bash wrapper script for CAI
# This script:
# 1. Activates the virtual environment
# 2. Ensures we're in the project root directory
# 3. Calls the Python launcher script
#
# Usage: bash cai_integration/launch_ray_cluster.sh

# Get the project root directory (parent of cai_integration)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "="
echo "🚀 Launching Ray Cluster on CAI"
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

# Run the Python launcher
echo "🔧 Starting Ray cluster launcher..."
python cai_integration/launch_ray_cluster.py "$@"
