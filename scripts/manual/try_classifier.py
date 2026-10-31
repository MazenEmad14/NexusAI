import asyncio
from controllers.classifier import ClassifierController
from helpers.config import get_settings
from stores.llm.providers.GroqProvider import GroqProvider

async def test_classifier():
    settings = get_settings()
    provider = GroqProvider(api_key=settings.GROQ_API_KEY)
    provider.set_generation_model(settings.GENERATION_MODEL_ID)
        
    classifier = ClassifierController(generation_client=provider)
    query = "حلل ملف البيانات وارسم مخطط بياني للرواتب"
    prompt = classifier._build_classification_prompt(query)
    response = provider.generate_text(prompt)
    print("RAW LLM OUTPUT:", response)
    
    res = classifier.classify_query(query)
    print("CLASSIFIED AS:", res)

if __name__ == "__main__":
    asyncio.run(test_classifier())
