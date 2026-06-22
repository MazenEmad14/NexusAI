import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger('uvicorn.error')

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
]


class CalendarService:
    """
    Google Calendar API wrapper — create, list, and manage calendar events.
    
    Shares OAuth2 credentials with GmailService. If Gmail is already authenticated,
    the same token can be extended with Calendar scopes.
    """

    def __init__(self, credentials_path: str = None, token_path: str = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.credentials_path = credentials_path or os.path.join(base_dir, "credentials.json")
        self.token_path = token_path or os.path.join(base_dir, "calendar_token.json")
        self.service = None

    def authenticate(self):
        """Authenticate with Google Calendar API using OAuth2."""
        creds = None

        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

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
                creds = flow.run_local_server(port=0, open_browser=False)

            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        self.service = build('calendar', 'v3', credentials=creds)
        logger.info("Google Calendar API authenticated successfully")
        return True

    def is_authenticated(self) -> bool:
        """Check if the service is authenticated."""
        return self.service is not None

    def create_event(self, title: str, date: str, time: str,
                     attendees: List[str] = None,
                     duration_hours: int = 1,
                     description: str = "") -> Optional[Dict]:
        """
        Create a calendar event.
        
        Args:
            title: Event title
            date: Date string (YYYY-MM-DD)
            time: Time string (HH:MM)
            attendees: List of email addresses
            duration_hours: Duration in hours (default 1)
            description: Event description
            
        Returns:
            Dict with event details or None on error
        """
        if not self.service:
            logger.error("Calendar service not authenticated")
            return None

        try:
            # Provide fallbacks if LLM fails to extract date or time
            date_str = date.strip() if date and date.strip() else datetime.now().strftime("%Y-%m-%d")
            time_str = time.strip() if time and time.strip() else "12:00"

            # Parse date and time
            start_datetime = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            end_datetime = start_datetime + timedelta(hours=duration_hours)

            event_body = {
                'summary': title,
                'description': description,
                'start': {
                    'dateTime': start_datetime.isoformat(),
                    'timeZone': 'Africa/Cairo',
                },
                'end': {
                    'dateTime': end_datetime.isoformat(),
                    'timeZone': 'Africa/Cairo',
                },
            }

            if attendees:
                event_body['attendees'] = [
                    {'email': email} for email in attendees
                    if '@' in email
                ]

            event = self.service.events().insert(
                calendarId='primary', 
                body=event_body,
                sendUpdates='all'
            ).execute()

            logger.info(f"Calendar event created: {event.get('id')}")

            return {
                "event_id": event.get('id', ''),
                "title": title,
                "date": date_str,
                "time": time_str,
                "link": event.get('htmlLink', ''),
                "status": "created"
            }

        except ValueError as e:
            logger.error(f"Invalid date/time format: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating calendar event: {e}")
            return None

    def list_events(self, max_results: int = 10, 
                    days_ahead: int = 7) -> List[Dict]:
        """List upcoming calendar events."""
        if not self.service:
            return []

        try:
            now = datetime.utcnow().isoformat() + 'Z'
            time_max = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat() + 'Z'

            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            return [
                {
                    "event_id": event.get('id', ''),
                    "title": event.get('summary', 'No Title'),
                    "start": event.get('start', {}).get('dateTime', event.get('start', {}).get('date', '')),
                    "end": event.get('end', {}).get('dateTime', event.get('end', {}).get('date', '')),
                    "link": event.get('htmlLink', ''),
                }
                for event in events
            ]

        except Exception as e:
            logger.error(f"Error listing calendar events: {e}")
            return []

    def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event by ID."""
        if not self.service:
            return False

        try:
            self.service.events().delete(
                calendarId='primary', eventId=event_id
            ).execute()
            logger.info(f"Calendar event deleted: {event_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting calendar event: {e}")
            return False
