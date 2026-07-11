#!/bin/bash

sudo uv pip install --system --break-system-packages -e .[dev]

pre-commit install
pre-commit run

echo 'eval "$(register-python-argcomplete /workspaces/read_bin/sync.py)"' >> ~/.bashrc
