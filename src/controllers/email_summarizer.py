from .BaseController import BaseController
import logging

logger = logging.getLogger('uvicorn.error')


class EmailSummarizerController(BaseController):
    """
    Uses the existing LLM provider to summarize email content.
    Extracts: sender, subject, key points, action items.
    """

    def __init__(self, generation_client):
        super().__init__()
        self.generation_client = generation_client

    def summarize_emails(self, emails: list) -> list:
        """
        Summarize a list of email dicts.
        Each email dict should have: sender, subject, body, date
        Returns list of summarized email dicts.
        """
        summaries = []
        for email in emails:
            summary = self.summarize_single_email(email)
            summaries.append(summary)
        return summaries

    def summarize_single_email(self, email: dict) -> dict:
        """Summarize a single email using LLM."""

        system_prompt = """You are an email summarizer. Given an email, provide a brief summary.
Return ONLY a JSON object with these fields:
{"sender": "who sent it", "subject": "email subject", "date": "when", "summary": "2-3 sentence summary of the key points", "action_items": ["any action items mentioned"]}"""

        email_text = f"""
From: {email.get('sender', 'Unknown')}
Subject: {email.get('subject', 'No Subject')}
Date: {email.get('date', 'Unknown')}

{email.get('body', email.get('snippet', 'No content'))}
"""

        chat_history = [
            self.generation_client.construct_prompt(
                prompt=system_prompt,
                role=self.generation_client.enums.SYSTEM.value,
            )
        ]

        try:
            response = self.generation_client.generate_text(
                prompt=f"Summarize this email:\n{email_text}",
                chat_history=chat_history,
                max_output_tokens=300,
                temperature=0.1
            )

            if not response:
                return {
                    "sender": email.get('sender', 'Unknown'),
                    "subject": email.get('subject', 'No Subject'),
                    "date": email.get('date', ''),
                    "summary": email.get('snippet', 'Could not summarize'),
                    "action_items": []
                }

            import json
            response_text = response.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("\n", 1)[-1]
                response_text = response_text.rsplit("```", 1)[0]
            response_text = response_text.strip()

            return json.loads(response_text)

        except Exception as e:
            logger.error(f"Error summarizing email: {e}")
            return {
                "sender": email.get('sender', 'Unknown'),
                "subject": email.get('subject', 'No Subject'),
                "date": email.get('date', ''),
                "summary": email.get('snippet', 'Could not summarize'),
                "action_items": []
            }
