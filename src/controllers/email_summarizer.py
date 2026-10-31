from .BaseController import BaseController
from .llm_schemas import EmailSummary
from helpers.exceptions import LLMOutputError
from helpers.llm_json import llm_json_sync
import logging
from helpers.prompts import render

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

        system_prompt = render("email", "summary_system")
        email_text = render("email", "summary_email", sender=email.get('sender', 'Unknown'), subject=email.get('subject', 'No Subject'), date=email.get('date', 'Unknown'), body=email.get('body', email.get('snippet', 'No content')))

        chat_history = [
            self.generation_client.construct_prompt(
                prompt=system_prompt,
                role=self.generation_client.enums.SYSTEM.value,
            )
        ]

        fallback = {
            "sender": email.get('sender', 'Unknown'),
            "subject": email.get('subject', 'No Subject'),
            "date": email.get('date', ''),
            "summary": email.get('snippet', 'Could not summarize'),
            "action_items": []
        }

        try:
            parsed = llm_json_sync(
                self.generation_client,
                render("email", "summary_user", email_text=email_text),
                EmailSummary,
                chat_history=chat_history,
                max_output_tokens=300,
                temperature=0.1,
                json_mode=True,
            )
        except LLMOutputError as exc:
            logger.error(f"Email summary reply unusable: {exc}")
            return fallback
        except Exception as e:
            logger.error(f"Error summarizing email: {e}")
            return fallback

        summary = parsed.model_dump()
        summary["sender"] = fallback["sender"]
        summary["subject"] = fallback["subject"]
        summary["date"] = fallback["date"]
        return summary
