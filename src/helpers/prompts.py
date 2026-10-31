"""The prompts the model is given, read from the locale templates (`stores/llm/templates/locales/<lang>/`).

The language is PRIMARY_LANG; a prompt that language has not translated falls back to DEFAULT_LANG.
"""
from helpers.config import get_settings
from stores.llm.templates.template_parser import TemplateParser


def render(group: str, key: str, **values) -> str:
    """The template `key` of `group` with `$name` placeholders filled from `values`."""
    settings = get_settings()
    text = TemplateParser(settings.PRIMARY_LANG, settings.DEFAULT_LANG).get(group, key, values)
    if text is None:
        raise LookupError(f"No prompt template {group}.{key}")
    return text
