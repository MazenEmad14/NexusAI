import logging

logger = logging.getLogger('uvicorn.error')


class ResponseFormatter:
    """
    Converts agent results into WhatsApp-style clean, emoji-rich responses.
    """

    @staticmethod
    def format_retrieval_answer(answer: str, source: str = "knowledge_base") -> str:
        """Format a RAG-generated answer."""
        if source == "knowledge_base":
            return f"📄 *From Knowledge Base:*\n\n{answer}"
        return f"🤖 {answer}"

    @staticmethod
    def format_email_results(emails: list) -> str:
        """Format Gmail search results."""
        if not emails:
            return "❌ No emails found matching your query."

        count = len(emails)
        header = f"📩 *Found {count} email{'s' if count > 1 else ''}:*\n"
        
        lines = []
        for i, email in enumerate(emails, 1):
            sender = email.get('sender', 'Unknown')
            subject = email.get('subject', 'No Subject')
            summary = email.get('summary', email.get('snippet', ''))
            date = email.get('date', '')
            
            line = f"\n{'─' * 30}\n"
            line += f"*{i}. {subject}*\n"
            line += f"   👤 From: {sender}\n"
            if date:
                line += f"   📅 Date: {date}\n"
            if summary:
                line += f"   💬 {summary}\n"
            lines.append(line)

        return header + "".join(lines)

    @staticmethod
    def format_email_sent(details: dict) -> str:
        """Format email sent confirmation."""
        to = details.get('to', details.get('recipient', 'recipient'))
        subject = details.get('subject', '')
        return (
            f"✅ *Email Sent Successfully!*\n\n"
            f"📧 To: {to}\n"
            f"📋 Subject: {subject}\n"
            f"📤 Status: Delivered"
        )

    @staticmethod
    def format_event_created(details: dict) -> str:
        """Format calendar event creation confirmation."""
        title = details.get('title', 'Meeting')
        date = details.get('date', '')
        time = details.get('time', '')
        link = details.get('link', '')

        response = (
            f"📅 *Meeting Scheduled!*\n\n"
            f"📌 Title: {title}\n"
            f"📆 Date: {date}\n"
            f"🕐 Time: {time}\n"
        )
        if link:
            response += f"🔗 Link: {link}\n"
        return response

    @staticmethod
    def format_reminder_set(details: dict) -> str:
        """Format reminder creation confirmation."""
        task = details.get('task', 'Reminder')
        date = details.get('date', '')
        notes = details.get('notes', '')

        response = f"🔔 *Reminder Set!*\n\n📝 Task: {task}\n"
        if date:
            response += f"📅 Date: {date}\n"
        if notes:
            response += f"📋 Notes: {notes}\n"
        return response

    @staticmethod
    def format_no_results() -> str:
        """Format no results found message."""
        return (
            "❌ *No information found regarding this topic.*\n\n"
            "💡 Try rephrasing your query or check with a different keyword."
        )

    @staticmethod
    def format_error(error_msg: str = "") -> str:
        """Format error response."""
        response = "⚠️ *Something went wrong.*\n\n"
        if error_msg:
            response += f"Error: {error_msg}\n"
        response += "Please try again or rephrase your request."
        return response
