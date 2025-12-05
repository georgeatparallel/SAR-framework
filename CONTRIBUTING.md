# Contributing to SAR Framework

Thank you for your interest in contributing to the SAR Framework! This document provides guidelines and instructions for contributing.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)

---

## Getting Started

### Prerequisites

- Python 3.11+
- Git
- A Google Cloud account (for API keys)

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/SAR-framework.git
   cd SAR-framework
   ```

---

## Development Setup

### 1. Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure Environment

Create a `.env` file with your API keys:

```env
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_API_KEY=your_google_api_key
SEARCH_ENGINE_ID=your_search_engine_id
```

### 4. Verify Setup

```bash
./run_test_server.sh
# In another terminal:
curl http://localhost:$(cat .port)/
./kill_server.sh
```

---

## Code Style

### Python Guidelines

- Follow [PEP 8](https://pep8.org/) style guidelines
- Use meaningful variable and function names
- Add docstrings to all functions and classes
- Keep functions focused and single-purpose

### Documentation

- Update relevant documentation when making changes
- Use clear, concise language
- Include code examples where helpful

### Logging

- Use the `logging` module, not `print()` statements
- Include appropriate log levels (DEBUG, INFO, WARNING, ERROR)
- Provide context in log messages

### Example

```python
import logging

logger = logging.getLogger(__name__)

def my_function(query: str) -> dict:
    """
    Brief description of what the function does.
    
    Args:
        query: The search query string.
        
    Returns:
        A dictionary containing the results.
        
    Raises:
        ValueError: If query is empty.
    """
    logger.info(f"Processing query: {query}")
    
    if not query:
        logger.error("Empty query provided")
        raise ValueError("Query cannot be empty")
    
    # Implementation...
    return {"results": []}
```

---

## Making Changes

### Branch Naming

Use descriptive branch names:

- `feature/add-caching`
- `fix/pdf-parsing-error`
- `docs/update-readme`
- `refactor/agent-service`

### Commit Messages

Follow conventional commit format:

```
type(scope): brief description

Longer explanation if needed.
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

Example:
```
feat(analyzer): add citation validation

Added validation to ensure all citations have valid URLs
before submitting the report. This prevents broken links
in the final output.
```

---

## Testing

### Running the Server

```bash
# Start test server
./run_test_server.sh

# Stop test server
./kill_server.sh
```

### Manual Testing

Test the API endpoint:

```bash
curl -X POST http://localhost:$(cat .port)/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test query"}'
```

### Checking Logs

Monitor logs during testing:

```bash
tail -f logs/app.log
```

---

## Submitting Changes

### Pull Request Process

1. **Update your fork**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature
   ```

3. **Make your changes**
   - Write clear, documented code
   - Follow the code style guidelines
   - Test your changes

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat(scope): description"
   ```

5. **Push to your fork**
   ```bash
   git push origin feature/your-feature
   ```

6. **Open a Pull Request**
   - Provide a clear title and description
   - Reference any related issues
   - Include screenshots if relevant

### PR Checklist

- [ ] Code follows project style guidelines
- [ ] Self-reviewed code changes
- [ ] Added/updated documentation as needed
- [ ] Tested changes locally
- [ ] No new warnings or errors
- [ ] Commit messages follow conventions

---

## Project Structure Reference

```
SAR-framework/
├── app/
│   ├── __init__.py          # App factory
│   ├── main/                 # Main blueprint
│   │   ├── __init__.py
│   │   └── routes.py         # API endpoints
│   └── services/             # Business logic
│       ├── agent_service.py  # Agent orchestration
│       └── search_service.py # Search operations
├── docs/                     # Documentation
├── logs/                     # Application logs
├── config.py                 # Configuration
├── main.py                   # Entry point
└── requirements.txt          # Dependencies
```

---

## Need Help?

- Check existing issues and documentation
- Open an issue for bugs or feature requests
- Review the [Blueprint](docs/Blueprint.md) for architecture guidance

Thank you for contributing! 🚀
