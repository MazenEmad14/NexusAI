import google.generativeai as genai
from helpers.config import get_settings

def test():
    app_settings = get_settings()
    genai.configure(api_key=app_settings.GEMINI_API_KEY)
    for m in genai.list_models():
        print(m.name)

if __name__ == "__main__":
    test()
