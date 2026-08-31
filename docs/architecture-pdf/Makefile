# Build the architecture PDF. `make diagrams` also re-renders the mermaid sources.
PY ?= uv run --with pillow --with pymupdf --no-project python

pdf:
	$(PY) build.py

diagrams:
	$(PY) build.py --diagrams

.PHONY: pdf diagrams
