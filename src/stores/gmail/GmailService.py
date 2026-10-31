import os
import base64
import json
import logging
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Optional, Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from helpers.exceptions import ExternalServiceError, GoogleAuthRequiredException, ValidationFailed
from stores.google_errors import translate_google_error
from helpers.validation import has_header_injection, parse_recipients
import httplib2
import google_auth_httplib2

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
    
    Supports two authentication modes:
    1. authenticate_from_dict(token_dict) — per-user token stored in MongoDB (preferred)
    2. authenticate(allow_interactive)   — legacy shared token.json file
    
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
        self.lock = threading.Lock()

    def authenticate_from_dict(self, token_dict: Dict[str, Any]) -> bool:
        """
        Authenticate using a per-user token dict stored in MongoDB.
        This is the preferred per-user authentication method.
        
        Returns True on success, raises GoogleAuthRequiredException if token is invalid.
        Returns the (possibly refreshed) token_dict so the caller can persist it back.
        """
        if not token_dict:
            raise GoogleAuthRequiredException("No Google token found. Please connect your Google account.")

        try:
            creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
        except Exception as e:
            logger.error(f"Gmail: Failed to load token from dict: {e}")
            raise GoogleAuthRequiredException("Invalid Google token. Please reconnect your Google account.")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Gmail: Token from DB expired, refreshing...")
                try:
                    creds.refresh(Request())
                    logger.info("Gmail: Token refreshed successfully.")
                except Exception as e:
                    logger.error(f"Gmail: Token refresh failed: {e}")
                    raise GoogleAuthRequiredException("Google token expired and refresh failed. Please reconnect.")
            else:
                raise GoogleAuthRequiredException("Google token is invalid. Please reconnect your Google account.")

        http = httplib2.Http(timeout=30.0)
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
        self.service = build('gmail', 'v1', http=authed_http)
        logger.info("Gmail API authenticated successfully (per-user DB token)")

        # Return refreshed token dict so caller can save it back to DB
        return json.loads(creds.to_json())

    def authenticate(self, allow_interactive: bool = True):
        """Authenticate with Gmail API using OAuth2 (legacy shared token.json)."""
        creds = None

        # Load existing token
        if os.path.exists(self.token_path):
            logger.info("Gmail: Token loaded from file.")
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception as e:
                logger.error(f"Gmail: Failed to load token: {e}")

        # Refresh or get new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Gmail: Token expired, attempting to refresh...")
                try:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                    logger.info("Gmail: Token refreshed successfully.")
                except Exception as e:
                    logger.error(f"Gmail: Token refresh failed: {e}. Re-auth required.")
                    creds = None # Force re-auth
            
            if not creds or not creds.valid:
                logger.warning("Gmail: Re-auth required, raising GoogleAuthRequiredException.")
                raise GoogleAuthRequiredException("Please connect your Google account from the Integrations panel to perform this action.")

            # Save token for next run
            try:
                with open(self.token_path, 'w') as token:
                    token.write(creds.to_json())
            except Exception as e:
                logger.error(f"Gmail: Failed to save token: {e}")

        http = httplib2.Http(timeout=30.0)
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
        self.service = build('gmail', 'v1', http=authed_http)
        logger.info("Gmail API authenticated successfully")
        return True

    def logout_gmail(self):
        """Logout by deleting the token file and resetting credentials."""
        if os.path.exists(self.token_path):
            os.remove(self.token_path)
            logger.info("Deleted Gmail token.json")
        self.service = None
        logger.info("Gmail credentials reset.")

    def is_authenticated(self) -> bool:
        """Check if the service is authenticated."""
        return self.service is not None

    def search_emails(self, query: str, max_results: int = 10) -> List[Dict]:
        """
        Search emails matching a query string.
        Query examples: "from:ahmed", "subject:project update", "is:unread"

        Raises on API errors: an empty list must mean "no matching emails", not "Gmail failed".
        """
        if not self.service:
            logger.error("Gmail service not authenticated")
            return []

        try:
            with self.lock:
                results = self.service.users().messages().list(
                    userId='me', q=query, maxResults=max_results
                ).execute()
        except Exception as exc:
            raise translate_google_error(exc, "Gmail") from exc

        messages = results.get('messages', [])
        if not messages:
            return []

        email_list = []
        for msg_info in messages:
            email_data = self.get_email(msg_info['id'])
            if email_data:
                email_list.append(email_data)

        if not email_list:
            raise ExternalServiceError("Gmail listed messages but none could be read", service="Gmail")
        return email_list

    def get_email(self, message_id: str) -> Optional[Dict]:
        """Fetch a single email by its message ID."""
        if not self.service:
            return None

        try:
            with self.lock:
                message = self.service.users().messages().get(
                    userId='me', id=message_id, format='full'
                ).execute()

            return self._parse_email(message)

        except Exception as e:
            logger.error(f"Error fetching email {message_id}: {e}")
            return None

    def send_email(self, to: str, subject: str, body: str,
                   html: bool = False) -> Optional[Dict]:
        """Send an email via Gmail API.

        Sending is not retried: if a request times out after Gmail accepted the
        message, a retry would deliver a duplicate.

        Raises ValidationFailed for an unusable recipient or subject, GoogleAuthRequiredException if
        Google rejects the token, and ExternalServiceError for any other Gmail failure.
        """
        if not self.service:
            raise GoogleAuthRequiredException("No Google account connected.")

        recipients = parse_recipients(to)
        if recipients is None or has_header_injection(to, subject):
            logger.error("Refusing to send email: invalid recipient or header injection attempt")
            raise ValidationFailed("That email address is not valid, so nothing was sent.")
        to = ", ".join(recipients)

        try:
            if html:
                message = MIMEMultipart('alternative')
                message.attach(MIMEText(body, 'html'))
            else:
                message = MIMEText(body)

            message['to'] = to
            message['subject'] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            with self.lock:
                sent_message = self.service.users().messages().send(
                    userId='me', body={'raw': raw}
                ).execute()

            logger.info(f"Email sent successfully. Message ID: {sent_message['id']}")
            return {
                "message_id": sent_message['id'],
                "to": to,
                "subject": subject,
                "status": "sent"
            }

        except Exception as exc:
            logger.error(f"Error sending email: {exc}")
            raise translate_google_error(exc, "Gmail") from exc

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
