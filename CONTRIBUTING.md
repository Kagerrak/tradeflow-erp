# Contributing to TradeFlow ERP

Thank you for your interest in TradeFlow ERP. This document covers how to propose changes, report issues, and set up a local development environment.

## Getting started

1. Fork and clone the repository.
2. Install prerequisites:
   - [Node.js](https://nodejs.org/) (see `package.json` `engines`)
   - [pnpm](https://pnpm.io/)
   - [uv](https://docs.astral.sh/uv/)
   - Docker and Docker Compose (for PostgreSQL, Redis, MinIO)
3. Copy environment templates:
   ```bash
   cp .env.example .env
   cp .env.demo.example .env.demo
   ```
4. Start infrastructure and run migrations:
   ```bash
   docker compose -f infra/compose.yaml up -d
   pnpm migrate
   ```
5. Install dependencies:
   ```bash
   pnpm install
   uv sync --all-packages --dev
   ```

## Proposing changes

1. Open an issue first for significant changes (new bounded contexts, API contract changes, dependency upgrades).
2. Create a feature branch from `main`.
3. Follow the existing code style. The repository enforces formatting and linting in CI.
4. Add or update tests for changed behavior.
5. Update relevant documentation in `docs/` and ADRs if the change involves architectural decisions.
6. Ensure the full quality gate passes locally:
   ```bash
   pnpm format
   pnpm lint
   pnpm typecheck
   pnpm test
   uv run pytest
   pnpm build
   ```
7. Open a pull request using the provided template.

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `style:` formatting, no code change
- `refactor:` code change that neither fixes a bug nor adds a feature
- `test:` adding or updating tests
- `chore:` tooling, dependencies, or housekeeping

## Reporting bugs

Use the bug report issue template and include:

- Steps to reproduce
- Expected vs. actual behavior
- Environment details (OS, Node version, browser if applicable)
- Logs or screenshots

## Domain-driven contributions

TradeFlow ERP is organized around business domains in `contexts/` and `packages/`. If your change touches sales, inventory, fulfillment, finance, or another domain, read the corresponding `contexts/<domain>/CONTEXT.md` before starting.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
