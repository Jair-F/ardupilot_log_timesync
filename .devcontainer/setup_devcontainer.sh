#!/bin/bash

sudo uv pip install --system --break-system-packages -e .[dev]

pre-commit install
pre-commit run
