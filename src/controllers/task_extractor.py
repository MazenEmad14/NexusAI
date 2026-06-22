from stores.llm.LLMInterface import LLMInterface
import logging
import json

logger = logging.getLogger('uvicorn.error')

class TaskExtractorController:
    def __init__(self, generation_client: LLMInterface):
        self.generation_client = generation_client

    def extract_tasks(self, text: str) -> list[dict]:
        """
        Extract tasks and deadlines from summarized emails or text.
        Returns a list of dicts: [{"description": "task description", "deadline": "YYYY-MM-DD HH:MM" or null}]
        """
        system_prompt = """You are a Task Extraction AI.
Your job is to read the provided text (like email summaries) and extract actionable tasks that the user needs to do.
For each task, extract the description and the deadline if mentioned.
If no deadline is mentioned, return null for deadline.
If there are no actionable tasks, return an empty list [].

Respond ONLY with a raw, valid JSON array of objects. Do not use markdown blocks.
Example:
[
  {
    "description": "Submit the Q3 report",
    "deadline": "2026-06-25 17:00"
  }
]
"""
        try:
            chat_history = [
                self.generation_client.construct_prompt(
                    prompt=system_prompt,
                    role=self.generation_client.enums.SYSTEM.value,
                )
            ]

            response = self.generation_client.generate_text(
                prompt=f"Text to extract from:\n{text}",
                chat_history=chat_history,
                max_output_tokens=300,
                temperature=0.0
            )

            if not response:
                return []

            response_text = response.strip()
            
            # Robust JSON array extraction
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx+1]
                tasks = json.loads(json_str)
                if isinstance(tasks, list):
                    return tasks
                return []
            else:
                return []

        except Exception as e:
            logger.error(f"Error extracting tasks: {e}")
            return []
