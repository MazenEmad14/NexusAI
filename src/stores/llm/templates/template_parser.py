import importlib
import os
from typing import Optional


class TemplateParser:
    """Finds prompt templates in `locales/<language>/<group>.py`.

    A prompt missing in the chosen language falls back to the default language, key by key, so a language
    can be translated gradually.
    """

    def __init__(self, language: Optional[str] = None, default_language: str = 'en'):
        self.current_path = os.path.dirname(os.path.abspath(__file__))
        self.default_language = default_language
        self.language = default_language

        self.set_language(language)

    def set_language(self, language: Optional[str]):
        if language and os.path.isdir(os.path.join(self.current_path, "locales", language)):
            self.language = language
        else:
            self.language = self.default_language

    def _load(self, language: str, group: str):
        """The module holding `group` in `language`, or None if that language has no such file."""
        if not os.path.exists(os.path.join(self.current_path, "locales", language, f"{group}.py")):
            return None
        return importlib.import_module(f"stores.llm.templates.locales.{language}.{group}")

    def get(self, group: str, key: str, vars: Optional[dict] = None) -> Optional[str]:
        """Render template `key` of `group`. A `$name` placeholder without a value raises KeyError."""
        if not group or not key:
            return None

        for language in dict.fromkeys((self.language, self.default_language)):
            module = self._load(language, group)
            template = getattr(module, key, None) if module else None
            if template is not None:
                return template.substitute(vars or {})
        return None
