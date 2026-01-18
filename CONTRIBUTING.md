# Contributing to RipForge

Thanks for your interest in contributing to RipForge!

## Ways to Contribute

- **Bug Reports** - Open an issue with steps to reproduce
- **Feature Requests** - Open an issue describing the use case
- **Pull Requests** - Fork, make changes, submit PR
- **Documentation** - Improve README, add examples

## Development Setup

```bash
git clone https://github.com/paul-tastic/ripforge.git
cd ripforge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Code Style

- Python code follows PEP 8
- Use meaningful variable names
- Add docstrings to functions
- Keep functions focused and small

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Test with your optical drive if possible
5. Commit with a clear message
6. Push and open a PR

## Project Structure

```
app/
├── config.py    # Configuration, hardware detection
├── routes.py    # Web routes and API endpoints
├── ripper.py    # MakeMKV wrapper, rip pipeline
├── identify.py  # Smart identification (TMDB matching)
├── email.py     # Email notifications
└── activity.py  # Activity logging
```

## Questions?

Open an issue or start a discussion on GitHub.
