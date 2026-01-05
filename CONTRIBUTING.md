# Contributing to Hamid's Pulse Auto News

Thank you for considering contributing to this project! 🎉

## Getting Started

1. **Fork the repository**
2. **Clone your fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/hamidspulse-auto-news.git
   cd hamidspulse-auto-news
   ```

3. **Set up development environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Create a branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Guidelines

### Code Style

- Follow PEP 8 for Python code
- Use type hints where appropriate
- Keep functions focused and small
- Add docstrings to classes and complex functions

### Testing

Before submitting a PR:
1. Test the full workflow (triage → post → digest)
2. Verify the web UI works
3. Check logs for errors
4. Test with real Telegram channels

### Commit Messages

Use clear, descriptive commit messages:
```
feat: add sentiment analysis to triage
fix: resolve rate limiting bug
docs: update README installation steps
refactor: simplify GPT prompt structure
```

## What to Contribute

### Ideas Welcome

- 🎨 **UI Improvements** - Better dashboard design
- 🧠 **AI Enhancements** - Smarter triage logic
- 📊 **Analytics** - Statistics and insights
- 🌐 **i18n** - Multi-language support
- 🔧 **Features** - New functionality

### Bug Reports

Please include:
- Description of the bug
- Steps to reproduce
- Expected vs actual behavior
- Logs (remove sensitive info)
- Environment (OS, Python version)

### Pull Requests

1. Ensure code follows guidelines
2. Update documentation if needed
3. Test thoroughly
4. Reference any related issues

## Questions?

Feel free to open an issue for discussion!

---

Happy coding! 🚀
