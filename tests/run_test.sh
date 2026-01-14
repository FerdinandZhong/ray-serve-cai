#!/bin/bash
#
# Convenience wrapper for running CAI cluster deployment test
#
# Usage:
#   bash tests/run_test.sh [options]
#
# Options are passed directly to test_cai_deployment.py
#
# Examples:
#   bash tests/run_test.sh
#   bash tests/run_test.sh --workers 2
#   bash tests/run_test.sh --gpu 1 --cpu 16 --memory 64

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}CAI Cluster Deployment Test Runner${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check required environment variables
if [ -z "$CML_HOST" ]; then
    echo -e "${RED}❌ Error: CML_HOST environment variable not set${NC}"
    echo ""
    echo "Set it with:"
    echo '  export CML_HOST="https://ml.example.cloudera.site"'
    exit 1
fi

if [ -z "$CML_API_KEY" ]; then
    echo -e "${RED}❌ Error: CML_API_KEY environment variable not set${NC}"
    echo ""
    echo "Set it with:"
    echo '  export CML_API_KEY="your-api-key"'
    exit 1
fi

if [ -z "$CML_PROJECT_ID" ]; then
    echo -e "${RED}❌ Error: CML_PROJECT_ID environment variable not set${NC}"
    echo ""
    echo "Set it with:"
    echo '  export CML_PROJECT_ID="your-project-id"'
    exit 1
fi

echo -e "${GREEN}✅ Environment variables configured${NC}"
echo "   CML_HOST: $CML_HOST"
echo "   CML_API_KEY: ***${CML_API_KEY: -4}"
echo "   CML_PROJECT_ID: $CML_PROJECT_ID"
echo ""

# Optional: Activate conda environment
USE_CONDA="${USE_CONDA:-false}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-ray-serve-env}"

if [ "$USE_CONDA" = "true" ]; then
    echo -e "${YELLOW}🐍 Activating conda environment: $CONDA_ENV_NAME${NC}"

    # Source conda
    if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
        source "$HOME/miniconda3/etc/profile.d/conda.sh"
    elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
        source "$HOME/anaconda3/etc/profile.d/conda.sh"
    else
        echo -e "${RED}❌ Conda not found${NC}"
        exit 1
    fi

    # Activate environment
    conda activate "$CONDA_ENV_NAME"
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to activate conda environment: $CONDA_ENV_NAME${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Conda environment activated${NC}"
    echo ""
fi

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
echo "🐍 Python version: $PYTHON_VERSION"
echo ""

# Check if caikit is available
echo "🔍 Checking for caikit library..."
python3 -c "import sys; sys.path.insert(0, '../caikit'); import caikit; print('✅ caikit library found')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}⚠️  caikit library not found in default path${NC}"
    echo "   The test will attempt to import it during execution"
    echo "   Make sure it's in PYTHONPATH or installed"
    echo ""
fi

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "📂 Project directory: $PROJECT_DIR"
echo ""

# Run the test
echo -e "${GREEN}🚀 Running test...${NC}"
echo ""

cd "$PROJECT_DIR"
python3 tests/test_cai_deployment.py "$@"

EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ Test completed successfully${NC}"
else
    echo -e "${RED}❌ Test failed with exit code: $EXIT_CODE${NC}"
fi

exit $EXIT_CODE
