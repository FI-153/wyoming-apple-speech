# Code Style — Wyoming Apple STT

## Swift (CLI tool)

- **Naming**: camelCase for variables/functions, PascalCase for types/protocols
- **Indentation**: 4 spaces
- **Line length**: 100 characters soft limit
- **Documentation**: Use `///` doc comments on public types and functions
- **Error handling**: Use Swift's `throw`/`catch` — avoid force unwraps (`!`) except in `main.swift`
  where a crash on unexpected state is acceptable
- **Imports**: Group Apple frameworks first, then third-party (currently none)

## Python (Wyoming server)

- **Naming**: snake_case for variables/functions/modules, PascalCase for classes
- **Indentation**: 4 spaces
- **Line length**: 100 characters soft limit
- **Docstrings**: Google style on all public functions and classes
- **Type hints**: Use type annotations on all function signatures
- **Imports**: Group standard library, third-party (`wyoming`), then local — separated by blank lines
- **Async**: Use `async`/`await` throughout — the Wyoming server is asyncio-based
