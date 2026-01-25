#!/bin/bash

# ANSI colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Setting up PDF-XLS-Converter locally...${NC}\n"

# Clean up any existing workers and Redis cache first
echo -e "${YELLOW}🧹 Cleaning up old workers and cache...${NC}"
pkill -9 -f celery 2>/dev/null && echo -e "${GREEN}✓ Killed old celery workers${NC}" || echo -e "${YELLOW}  No celery workers running${NC}"

# Flush Redis cache only if Redis is running
if redis-cli ping &> /dev/null; then
    redis-cli FLUSHALL > /dev/null && echo -e "${GREEN}✓ Flushed Redis cache${NC}"
fi

echo ""

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo -e "${RED}❌ Error: Homebrew is not installed. Please install it first from https://brew.sh${NC}"
    exit 1
fi

# Function to check and install a package
install_if_missing() {
    local package=$1
    local command=${2:-$1}
    
    if ! command -v "$command" &> /dev/null; then
        echo -e "${BLUE}📦 Installing $package via Homebrew...${NC}"
        brew install "$package"
    else
        echo -e "${GREEN}✓ $package is already installed${NC}"
    fi
}

# Check and install dependencies
install_if_missing "redis" "redis-server"
install_if_missing "tesseract"
install_if_missing "poppler" "pdftoppm"

# Setup Python Virtual Environment
if [ ! -d "venv" ]; then
    echo -e "\n${BLUE}🐍 Creating virtual environment...${NC}"
    python3 -m venv venv
else
    echo -e "${GREEN}✓ Virtual environment exists${NC}"
fi

# Activate virtual environment
echo -e "${BLUE}🔌 Activating virtual environment...${NC}"
source venv/bin/activate

# Install Python dependencies
echo -e "\n${BLUE}⬇️  Installing/Updating Python dependencies...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Check if Redis is already running
echo -e "\n${BLUE}🔍 Checking Redis status...${NC}"
if redis-cli ping &> /dev/null; then
    echo -e "${GREEN}✓ Redis is already running${NC}"
else
    echo -e "${BLUE}🔄 Starting Redis server...${NC}"
    redis-server --daemonize yes
    sleep 1
    if redis-cli ping &> /dev/null; then
        echo -e "${GREEN}✓ Redis started successfully${NC}"
    else
        echo -e "${RED}❌ Failed to start Redis${NC}"
        exit 1
    fi
fi

# Function to kill processes on exit
cleanup() {
    echo -e "\n${YELLOW}🛑 Shutting down services...${NC}"
    pkill -f "celery -A celery_config.celery_app worker"
    echo -e "${GREEN}✓ Services stopped${NC}"
    exit
}

trap cleanup SIGINT SIGTERM

# Start Celery Worker in background
echo -e "\n${BLUE}👷 Starting Celery worker in background...${NC}"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES # Fix for macOS forking safety issues
# Use solo pool to avoid fork() SIGSEGV with PaddleOCR/OpenCV native libraries
celery -A celery_config.celery_app worker --loglevel=info --pool=solo &
CELERY_PID=$!

# Give worker a moment to start
sleep 2

# Check if worker started successfully
if ps -p $CELERY_PID > /dev/null; then
    echo -e "${GREEN}✓ Celery worker started (PID: $CELERY_PID)${NC}"
else
    echo -e "${RED}❌ Failed to start Celery worker${NC}"
    exit 1
fi

# Start Flask App
echo -e "\n${GREEN}✨ Starting Flask application...${NC}"
echo -e "${GREEN}🌐 Open http://localhost:5001 in your browser${NC}\n"
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}\n"

python app.py

