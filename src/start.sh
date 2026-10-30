#!/bin/bash
pip install -r src/requirements.txt -c src/requirements.lock
uvicorn src.main:app --reload --host 0.0.0.0 --port 5000
