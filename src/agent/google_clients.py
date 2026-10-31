"""The caller's own Gmail and Calendar clients, built per call and never shared between users.

The token comes from the request context (helpers/google_context.py), not from graph state: graph
state is checkpointed to the database and a refresh token must not be stored there.
"""
from helpers.exceptions import GoogleAuthRequiredException
from helpers.google_context import get_google_token, update_google_token
from stores.calendar.CalendarService import CalendarService
from stores.gmail.GmailService import GmailService

GOOGLE_CONNECT_MESSAGE = "Please connect your Google account from the Integrations panel to perform this action."


def gmail_for_request() -> GmailService:
    """A Gmail client for this request's user; raises GoogleAuthRequiredException without a token."""
    token = get_google_token()
    if not token:
        raise GoogleAuthRequiredException("No Google account connected.")
    service = GmailService()
    refreshed = service.authenticate_from_dict(token)
    if refreshed:
        update_google_token(refreshed)
    return service


def calendar_for_request() -> CalendarService:
    """A Calendar client for this request's user (see gmail_for_request)."""
    token = get_google_token()
    if not token:
        raise GoogleAuthRequiredException("No Google account connected.")
    service = CalendarService()
    refreshed = service.authenticate_from_dict(token)
    if refreshed:
        update_google_token(refreshed)
    return service
