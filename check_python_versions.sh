#!/bin/bash

# Script to check Python versions in virtual environment
echo "=== Python Version Check Script ==="
echo ""

# Activate virtual environment (assuming it's in .virtualenvs/toolboxweb)
source ~/.virtualenvs/toolboxweb/bin/activate

echo "1. Checking default Python 3 version:"
python3 --version
echo ""

echo "2. Checking Python 2 version (if available):"
python --version 2>/dev/null || echo "Python 2 not available"
echo ""

echo "3. Checking for Python 3.13:"
which python3.13 2>/dev/null || echo "Python 3.13 not found"
echo ""

echo "4. Listing all Python binaries in /usr/bin/:"
ls /usr/bin/python* 2>/dev/null | head -20
echo ""

echo "=== Check Complete ==="