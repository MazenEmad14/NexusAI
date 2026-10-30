import os
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from helpers.exceptions import GoogleAuthRequiredException
from helpers.timezones import local_now, resolve_timezone
from helpers.validation import parse_recipients
import httplib2
import google_auth_httplib2

logger = logging.getLogger('uvicorn.error')

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
]


class CalendarService:
    """
    Google Calendar API wrapper — create, list, and manage calendar events.
    
    Supports two authentication modes:
    1. authenticate_from_dict(token_dict) — per-user token stored in MongoDB (preferred)
    2. authenticate(allow_interactive)    — legacy shared token.json file

    Shares OAuth2 credentials with GmailService. If Gmail is already authenticated,
    the same token can be extended with Calendar scopes.
    """

    def __init__(self, credentials_path: str = None, token_path: str = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.credentials_path = credentials_path or os.path.join(base_dir, "credentials.json")
        # Use shared token.json for all Google services
        self.token_path = token_path or os.path.join(base_dir, "token.json")
        self.service = None
        self.lock = threading.Lock()

    def authenticate_from_dict(self, token_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Authenticate using a per-user token dict stored in MongoDB.
        This is the preferred per-user authentication method.

        Returns the (possibly refreshed) token_dict so the caller can persist it back.
        Raises GoogleAuthRequiredException if the token is invalid or expired.
        """
        if not token_dict:
            raise GoogleAuthRequiredException("No Google token found. Please connect your Google account.")

        try:
            creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
        except Exception as e:
            logger.error(f"Calendar: Failed to load token from dict: {e}")
            raise GoogleAuthRequiredException("Invalid Google token. Please reconnect your Google account.")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Calendar: Token from DB expired, refreshing...")
                try:
                    creds.refresh(Request())
                    logger.info("Calendar: Token refreshed successfully.")
                except Exception as e:
                    logger.error(f"Calendar: Token refresh failed: {e}")
                    raise GoogleAuthRequiredException("Google token expired and refresh failed. Please reconnect.")
            else:
                raise GoogleAuthRequiredException("Google token is invalid. Please reconnect your Google account.")

        http = httplib2.Http(timeout=30.0)
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
        self.service = build('calendar', 'v3', http=authed_http)
        logger.info("Google Calendar API authenticated successfully (per-user DB token)")

        # Return refreshed token dict so caller can save it back to DB
        return json.loads(creds.to_json())

    def authenticate(self, allow_interactive: bool = True):
        """Authenticate with Google Calendar API using OAuth2 (legacy shared token.json)."""
        creds = None

        if os.path.exists(self.token_path):
            logger.info("Calendar: Token loaded from file.")
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception as e:
                logger.error(f"Calendar: Failed to load token: {e}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Calendar: Token expired, attempting to refresh...")
                try:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                    logger.info("Calendar: Token refreshed successfully.")
                except Exception as e:
                    logger.error(f"Calendar: Token refresh failed: {e}. Re-auth required.")
                    creds = None # Force re-auth
            
            if not creds or not creds.valid:
                logger.warning("Calendar: Re-auth required, raising GoogleAuthRequiredException.")
                raise GoogleAuthRequiredException("Please connect your Google account from the Integrations panel to perform this action.")

            try:
                with open(self.token_path, 'w') as token:
                    token.write(creds.to_json())
            except Exception as e:
                logger.error(f"Calendar: Failed to save token: {e}")

        http = httplib2.Http(timeout=30.0)
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
        self.service = build('calendar', 'v3', http=authed_http)
        logger.info("Google Calendar API authenticated successfully")
        return True

    def is_authenticated(self) -> bool:
        """Check if the service is authenticated."""
        return self.service is not None

    def create_event(self, title: str, date: str, time: str,
                     attendees: List[str] = None,
                     duration_hours: int = 1,
                     description: str = "",
                     time_zone: Optional[str] = None) -> Optional[Dict]:
        """
        Create a calendar event.
        
        Args:
            title: Event title
            date: Date string (YYYY-MM-DD)
            time: Time string (HH:MM)
            attendees: List of email addresses
            duration_hours: Duration in hours (default 1)
            description: Event description
            time_zone: IANA zone the date and time are in (the user's zone; default if not given)
            
        Returns:
            Dict with event details or None on error
        """
        if not self.service:
            logger.error("Calendar service not authenticated")
            return None

        try:
            # Provide fallbacks if LLM fails to extract date or time
            zone = resolve_timezone(time_zone)
            date_str = date.strip() if date and date.strip() else local_now(time_zone).strftime("%Y-%m-%d")
            time_str = time.strip() if time and time.strip() else "12:00"

            # Parse date and time
            start_datetime = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            end_datetime = start_datetime + timedelta(hours=duration_hours)

            event_body = {
                'summary': title,
                'description': description,
                'start': {
                    'dateTime': start_datetime.isoformat(),
                    'timeZone': zone.key,
                },
                'end': {
                    'dateTime': end_datetime.isoformat(),
                    'timeZone': zone.key,
                },
            }

            if attendees:
                # Only well-formed addresses: sendUpdates='all' emails every attendee.
                valid = [a for a in attendees if parse_recipients([a])]
                event_body['attendees'] = [{'email': email.strip()} for email in valid]

            with self.lock:
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
        """List upcoming calendar events, from now until `days_ahead` days from now.

        Raises on API errors: an empty list must mean "no events", not "Calendar failed".
        """
        if not self.service:
            return []

        now_utc = datetime.now(timezone.utc)
        with self.lock:
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now_utc.isoformat(),
                timeMax=(now_utc + timedelta(days=days_ahead)).isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

        return [
            {
                "event_id": event.get('id', ''),
                "title": event.get('summary', 'No Title'),
                "start": event.get('start', {}).get('dateTime', event.get('start', {}).get('date', '')),
                "end": event.get('end', {}).get('dateTime', event.get('end', {}).get('date', '')),
                "link": event.get('htmlLink', ''),
            }
            for event in events_result.get('items', [])
        ]

    def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event by ID."""
        if not self.service:
            return False

        try:
            with self.lock:
                self.service.events().delete(
                    calendarId='primary', eventId=event_id
                ).execute()
            logger.info(f"Calendar event deleted: {event_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting calendar event: {e}")
            return False
