FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-24.04
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN sudo apt update && sudo apt upgrade -y
RUN sudo apt install -y python3 python3-pip git gpg tmux


COPY pyproject.toml .
RUN sudo uv pip install --system --break-system-packages -r pyproject.toml
