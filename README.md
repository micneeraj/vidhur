# Vidhur

A multi-agent hackathon app for **Open Agent Hackathon 2026**.

Vidhur uses synthetic purchase data for a fictional UK AI server company, **Ganga AI Solutions**, and talks to an OpenAI-compatible LLM through Nightshift / Meterless.

## What is in this folder

- `data/` — synthetic suppliers, parts, and purchases (CSV)
- `agents/` — place for agents (empty on Day 1)
- `core/` — shared code, including the LLM helper
- `tests/` — checks that the LLM connection works
- `main.py` — app entry point

## Setup

1. Copy `.env.example` to `.env` and put in your real token and URLs.
2. Create a virtual environment and install packages (see commands below).

All numbers and company names in `data/` are made up. See `data/README_DATA.md`.
