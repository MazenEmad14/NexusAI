import os
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger('uvicorn.error')

# If modifying scopes, delete token.json
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
]


class GmailService:
    """
    Gmail API wrapper — search, read, and send emails using OAuth2.
    
    Setup instructions:
    1. Go to Google Cloud Console → APIs & Services → Credentials
    2. Create OAuth 2.0 Client ID (Desktop App)
    3. Download credentials.json and place it in the src/ directory
    4. First run will open browser for OAuth consent
    """

    def __init__(self, credentials_path: str = None, token_path: str = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.credentials_path = credentials_path or os.path.join(base_dir, "credentials.json")
        self.token_path = token_path or os.path.join(base_dir, "token.json")
        self.service = None

    def authenticate(self):
        """Authenticate with Gmail API using OAuth2."""
        creds = None

        # Load existing token
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        # Refresh or get new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    logger.error(
                        f"credentials.json not found at {self.credentials_path}. "
                        "Please download it from Google Cloud Console."
                    )
                    return False

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save token for next run
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        self.service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail API authenticated successfully")
        return True

    def is_authenticated(self) -> bool:
        """Check if the service is authenticated."""
        return self.service is not None

    def search_emails(self, query: str, max_results: int = 10) -> List[Dict]:
        """
        Search emails matching a query string.
        Query examples: "from:ahmed", "subject:project update", "is:unread"
        """
        if not self.service:
            logger.error("Gmail service not authenticated")
            return []

        try:
            results = self.service.users().messages().list(
                userId='me', q=query, maxResults=max_results
            ).execute()

            messages = results.get('messages', [])
            if not messages:
                return []

            email_list = []
            for msg_info in messages:
                email_data = self.get_email(msg_info['id'])
                if email_data:
                    email_list.append(email_data)

            return email_list

        except Exception as e:
            logger.error(f"Error searching Gmail: {e}")
            return []

    def get_email(self, message_id: str) -> Optional[Dict]:
        """Fetch a single email by its message ID."""
        if not self.service:
            return None

        try:
            message = self.service.users().messages().get(
                userId='me', id=message_id, format='full'
            ).execute()

            return self._parse_email(message)

        except Exception as e:
            logger.error(f"Error fetching email {message_id}: {e}")
            return None

    def send_email(self, to: str, subject: str, body: str, 
                   html: bool = False) -> Optional[Dict]:
        """Send an email via Gmail API."""
        if not self.service:
            logger.error("Gmail service not authenticated")
            return None

        try:
            if html:
                message = MIMEMultipart('alternative')
                message.attach(MIMEText(body, 'html'))
            else:
                message = MIMEText(body)

            message['to'] = to
            message['subject'] = subject

            raw_message = base64.urlsafe_b64encode(
                message.as_bytes()
            ).decode('utf-8')

            sent = self.service.users().messages().send(
                userId='me', body={'raw': raw_message}
            ).execute()

            logger.info(f"Email sent successfully. Message ID: {sent['id']}")
            return {
                "message_id": sent['id'],
                "to": to,
                "subject": subject,
                "status": "sent"
            }

        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return None

    def _parse_email(self, message: dict) -> Dict:
        """Parse a raw Gmail message into a clean dictionary."""
        headers = message.get('payload', {}).get('headers', [])

        header_map = {}
        for header in headers:
            name = header['name'].lower()
            if name in ('from', 'to', 'subject', 'date'):
                header_map[name] = header['value']

        # Extract body text
        body = self._get_body_text(message.get('payload', {}))

        return {
            "message_id": message.get('id', ''),
            "sender": header_map.get('from', ''),
            "receiver": header_map.get('to', ''),
            "subject": header_map.get('subject', ''),
            "date": header_map.get('date', ''),
            "body": body,
            "snippet": message.get('snippet', ''),
        }

    def _get_body_text(self, payload: dict) -> str:
        """Recursively extract plain text from email payload."""
        body = ""

        if 'body' in payload and payload['body'].get('data'):
            body = base64.urlsafe_b64decode(
                payload['body']['data']
            ).decode('utf-8', errors='ignore')
            return body

        parts = payload.get('parts', [])
        for part in parts:
            mime_type = part.get('mimeType', '')
            if mime_type == 'text/plain':
                data = part.get('body', {}).get('data', '')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    return body
            elif 'parts' in part:
                body = self._get_body_text(part)
                if body:
                    return body

        return body
