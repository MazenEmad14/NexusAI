import sys
import os

# Add src to python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from controllers.classifier import ClassifierController

class MockClient:
    def __init__(self):
        class Enums:
            class SYSTEM: value = 'system'
            class USER: value = 'user'
            class ASSISTANT: value = 'assistant'
        self.enums = Enums()
    def construct_prompt(self, prompt, role): return None
    def generate_text(self, *args, **kwargs): return '{"request_type": "unknown"}'

classifier = ClassifierController(generation_client=MockClient())
queries = ['what is your name', 'who are you', 'مين انت', 'انت اسمك ايه', 'what is your name?']
for q in queries:
    res = classifier.classify_query(query=q)
    print(f"Q: {q} -> {res}")
