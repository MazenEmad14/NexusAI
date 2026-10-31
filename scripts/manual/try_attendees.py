import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers.config import get_settings
from stores.llm.LLMProviderFactory import LLMProviderFactory
from controllers.classifier import ClassifierController

settings = get_settings()
llm_provider_factory = LLMProviderFactory(settings)
generation_client = llm_provider_factory.create(provider=settings.GENERATION_BACKEND)
generation_client.set_generation_model(model_id=settings.GENERATION_MODEL_ID)

classifier = ClassifierController(generation_client)

query = "Schedule a meeting with the development team on 2026-06-23 at 16:00 to review the AI Agent logic. Invite mazen7ektr@gmail.com"
details = classifier.extract_action_details(query, "calendar")

print("Extracted details:")
print(details)
