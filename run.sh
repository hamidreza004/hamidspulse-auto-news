#!/bin/bash

# Hamid's Pulse Auto News - Quick Start Script

echo "🔭 Starting Hamid's Pulse Auto News..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found!"
    echo "Please run: python3 -m venv venv"
    exit 1
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "❌ .env file not found!"
    echo "Please copy .env.example to .env and configure it"
    exit 1
fi

# Check if session exists
if [ ! -f "secrets/telegram.session" ]; then
    echo "⚠️  Telegram session not found!"
    echo "Running setup script first..."
    source venv/bin/activate
    python setup_telegram.py
    echo ""
fi

# Activate virtual environment and run
source venv/bin/activate
python main.py
