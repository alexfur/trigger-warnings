"""Small, local-only client for the official DoesTheDogDie API v3.

The client never caches responses and never writes an API key. It converts only
timestamped ratings to the tool's provider-neutral :class:`core.Event` objects;
the core renderer therefore remains independent of any remote source.
"""

import json
from collections import namedtuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__
from .core import Event, TriggerWarningsError


API_BASE_URL = "https://www.doesthedogdie.com/api/v3"
ATTRIBUTION = "Powered by DoesTheDogDie.com"
#: Derived from the package version rather than written out, because the
#: literal that used to sit here said 0.2 for the whole of 0.3: a stale
#: version is invisible locally and only ever wrong at the far end.
USER_AGENT = "trigger-warnings/{}".format(__version__)
_TIME_FIELDS = ("position1", "position2", "position3")
_SAFE_TIME_FIELDS = ("safePosition1", "safePosition2", "safePosition3")

__all__ = [
    "API_BASE_URL",
    "ATTRIBUTION",
    "USER_AGENT",
    "DddApiError",
    "DddEvents",
    "load_item_events",
    "search_items",
]


class DddApiError(TriggerWarningsError):
    """An official API request or response could not produce safe local events."""


DddEvents = namedtuple("DddEvents", "events notes scene_alerts community")


def _require_key(api_key):
    if not isinstance(api_key, str) or not api_key.strip():
        raise DddApiError("--ddd-api-key or DDD_API_KEY must be a non-empty API key")
    return api_key.strip()


def _require_item_id(item_id):
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
        raise DddApiError("--ddd-item must be a positive API item id")
    return item_id


def _require_search_text(text):
    if not isinstance(text, str) or not text.strip():
        raise DddApiError("--ddd-search must not be empty")
    return text.strip()


def _require_year(year):
    if year is None:
        return None
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise DddApiError("--ddd-year must be a four-digit year")
    return year


def _request_json(path, api_key, opener=urlopen):
    """Fetch one JSON endpoint without putting credentials in a URL or error."""
    request = Request(
        API_BASE_URL + path,
        headers={
            "X-API-KEY": api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with opener(request, timeout=15) as response:
            payload = response.read()
    except HTTPError as error:
        raise DddApiError(
            "DoesTheDogDie API returned HTTP {}. Check the item id, API tier, "
            "and API key without sharing the key.".format(error.code)
        )
    except (URLError, OSError, ValueError) as error:
        raise DddApiError("Could not reach the DoesTheDogDie API: {}".format(error))
    try:
        decoded = payload.decode("utf-8")
        return json.loads(decoded)
    except (UnicodeDecodeError, ValueError) as error:
        raise DddApiError("DoesTheDogDie API returned invalid JSON: {}".format(error))


def _integer(value, field, rating_id):
    if isinstance(value, bool) or not isinstance(value, int):
        raise DddApiError(
            "DoesTheDogDie rating {} has an invalid {} timestamp field".format(
                rating_id, field
            )
        )
    return value


def _rating_time_ms(rating, fields, rating_id):
    """Return a rating timestamp, or ``None`` when the timestamp is absent.

    A partially supplied timestamp is not treated as absent. That would silently
    alter a supplied warning, so it fails the whole import instead.
    """
    values = [rating.get(field) for field in fields]
    if all(value is None for value in values):
        return None
    # The live API uses ``-1`` as its absent-position sentinel on some rating
    # records. Treat only the complete triplet as absent; a mixed triplet is a
    # malformed timestamp rather than a value we can safely guess at.
    if values == [-1, -1, -1]:
        return None
    if any(value is None for value in values):
        raise DddApiError(
            "DoesTheDogDie rating {} has a partial timestamp".format(rating_id)
        )
    hours, minutes, seconds = [
        _integer(value, field, rating_id) for field, value in zip(fields, values)
    ]
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise DddApiError(
            "DoesTheDogDie rating {} has an out-of-range timestamp".format(rating_id)
        )
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _topic_names(data):
    if not isinstance(data, list):
        raise DddApiError("DoesTheDogDie API returned invalid topic data")
    names = {}
    for topic in data:
        if not isinstance(topic, dict):
            raise DddApiError("DoesTheDogDie API returned an invalid topic")
        topic_id = topic.get("id")
        name = topic.get("name")
        if isinstance(topic_id, bool) or not isinstance(topic_id, int):
            raise DddApiError("DoesTheDogDie API returned a topic without an integer id")
        if not isinstance(name, str) or not name.strip():
            raise DddApiError("DoesTheDogDie API returned a topic without a name")
        names[topic_id] = name.strip()
    return names


def search_items(api_key, title, year=None, opener=urlopen):
    """Search official items without choosing one on the user's behalf."""
    key = _require_key(api_key)
    name = _require_search_text(title)
    release_year = _require_year(year)
    query = {"name": name}
    if release_year is not None:
        query["releaseYear"] = str(release_year)
    data = _request_json("/items?" + urlencode(query), key, opener)
    if not isinstance(data, list):
        raise DddApiError("DoesTheDogDie API returned invalid search results")

    candidates = []
    for item in data:
        if not isinstance(item, dict):
            raise DddApiError("DoesTheDogDie API returned an invalid search result")
        item_id = item.get("id")
        item_name = item.get("name")
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
            raise DddApiError("DoesTheDogDie API returned a result without a valid id")
        if not isinstance(item_name, str) or not item_name.strip():
            raise DddApiError("DoesTheDogDie API returned a result without a name")
        candidate = {
            "id": item_id,
            "name": item_name.strip(),
            "releaseYear": item.get("releaseYear"),
            "itemType": item.get("itemTypeName"),
            "imdbId": item.get("imdbId"),
            "tmdbId": item.get("tmdbId"),
        }
        candidates.append(candidate)
    return candidates


def load_item_events(api_key, item_id, opener=urlopen):
    """Return timestamped official-API ratings as provider-neutral events.

    Untimestamped community ratings are expected and reported as omitted. A
    malformed timestamp is an error, not an omitted rating. Scene Alerts, when
    the account is separately entitled to them, are marked in metadata only.
    """
    key = _require_key(api_key)
    item = _require_item_id(item_id)
    topics = _topic_names(_request_json("/topics", key, opener))
    ratings = _request_json("/items/{}/ratings".format(item), key, opener)
    if not isinstance(ratings, list):
        raise DddApiError("DoesTheDogDie API returned invalid ratings data")

    events = []
    omitted = 0
    scene_alerts = 0
    community = 0
    for ordinal, rating in enumerate(ratings, start=1):
        if not isinstance(rating, dict):
            raise DddApiError("DoesTheDogDie API returned an invalid rating")
        rating_id = rating.get("id", ordinal)
        start_ms = _rating_time_ms(rating, _TIME_FIELDS, rating_id)
        if start_ms is None:
            omitted += 1
            continue
        topic_id = rating.get("topicId")
        if isinstance(topic_id, bool) or not isinstance(topic_id, int):
            raise DddApiError(
                "DoesTheDogDie rating {} has no valid topic id".format(rating_id)
            )
        if topic_id not in topics:
            raise DddApiError(
                "DoesTheDogDie rating {} refers to an unknown topic {}".format(
                    rating_id, topic_id
                )
            )
        end_ms = _rating_time_ms(rating, _SAFE_TIME_FIELDS, rating_id)
        if end_ms is not None and end_ms < start_ms:
            raise DddApiError(
                "DoesTheDogDie rating {} ends before it starts".format(rating_id)
            )
        is_scene_alert = rating.get("isSceneAlert") is True
        if is_scene_alert:
            scene_alerts += 1
        else:
            community += 1
        events.append(
            Event(
                start_ms,
                end_ms,
                topics[topic_id],
                "scene-alert" if is_scene_alert else "community",
                ordinal,
            )
        )

    if not events:
        raise DddApiError(
            "DoesTheDogDie returned no timestamped ratings for item {}. This is "
            "not evidence that the title has no triggers.".format(item)
        )
    notes = []
    if omitted:
        notes.append(
            "DoesTheDogDie returned {} rating{} without timestamps; they were not "
            "turned into warnings.".format(omitted, "" if omitted == 1 else "s")
        )
    return DddEvents(events, notes, scene_alerts, community)
