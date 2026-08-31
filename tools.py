"""Genuine real-time tool definitions and handlers for Pipecat Voice Agent.

Tools include:
1. Real-time Global Weather (Live Open-Meteo API)
2. Real-time Global Time & Timezone Conversion (Live Geocoding + zoneinfo)
3. Real-time Persistent Appointment Booking (Real SQLite Database)
4. Real-time Knowledge & Web Search (DuckDuckGo Instant Knowledge API)
"""

import datetime
import os
import sqlite3
import urllib.parse
from typing import Any, Dict, List, Optional
import zoneinfo
import aiohttp
from loguru import logger
from pipecat.adapters.schemas.tools_schema import FunctionSchema

# SQLite database setup for genuine persistent appointments
DB_PATH = os.path.join(os.path.dirname(__file__), "appointments.db")


def init_db():
    """Initialize SQLite database for real appointment bookings."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_code TEXT UNIQUE NOT NULL,
                customer_name TEXT NOT NULL,
                service_name TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# Initialize database on module load
init_db()

# WMO Weather interpretation codes mapping
WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


# ==============================================================================
# Real-Time Tool 1: Live Global Weather (Open-Meteo API)
# ==============================================================================
async def execute_get_current_weather(
    location: str, unit: str = "celsius"
) -> Dict[str, Any]:
    """Fetches real live weather data for any location in the world."""
    logger.info(f"[Live Tool] Fetching real-time weather for: {location}")
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Geocode location to get exact latitude and longitude
            geo_url = (
                f"https://geocoding-api.open-meteo.com/v1/search?"
                f"name={urllib.parse.quote(location)}&count=1&language=en&format=json"
            )
            async with session.get(geo_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return {"error": f"Geocoding service unavailable (status {resp.status})"}
                geo_data = await resp.json()
                if not geo_data.get("results"):
                    return {"error": f"Could not find location '{location}'."}

                loc_info = geo_data["results"][0]
                lat = loc_info["latitude"]
                lon = loc_info["longitude"]
                resolved_name = loc_info.get("name", location)
                country = loc_info.get("country", "")
                admin1 = loc_info.get("admin1", "")

            # 2. Fetch real-time weather metrics
            temp_unit_param = "fahrenheit" if unit.lower() == "fahrenheit" else "celsius"
            weather_url = (
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
                f"&temperature_unit={temp_unit_param}"
            )
            async with session.get(weather_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return {"error": "Weather data service unavailable."}
                weather_data = await resp.json()
                current = weather_data.get("current", {})

                w_code = current.get("weather_code", 0)
                condition = WMO_WEATHER_CODES.get(w_code, "Clear")
                temperature = current.get("temperature_2m")
                apparent_temp = current.get("apparent_temperature")
                humidity = current.get("relative_humidity_2m")
                wind_speed = current.get("wind_speed_10m")

                display_location = f"{resolved_name}, {admin1 + ', ' if admin1 else ''}{country}"
                unit_symbol = "°F" if temp_unit_param == "fahrenheit" else "°C"

                return {
                    "status": "success",
                    "location": display_location,
                    "temperature": f"{temperature}{unit_symbol}",
                    "feels_like": f"{apparent_temp}{unit_symbol}",
                    "condition": condition,
                    "humidity": f"{humidity}%",
                    "wind_speed": f"{wind_speed} km/h",
                }
    except Exception as e:
        logger.error(f"Error fetching weather for {location}: {e}")
        return {"error": f"Failed to retrieve weather for {location}: {str(e)}"}


# ==============================================================================
# Real-Time Tool 2: Live Global Time & Timezone Conversion
# ==============================================================================
async def execute_get_current_time(location_or_timezone: str = "UTC") -> Dict[str, Any]:
    """Calculates the exact real-time clock and date for any city or timezone."""
    logger.info(f"[Live Tool] Calculating real-time for: {location_or_timezone}")
    resolved_tz_str = "UTC"
    display_name = location_or_timezone

    try:
        # Check if the input is already a valid IANA timezone identifier
        try:
            tz = zoneinfo.ZoneInfo(location_or_timezone)
            resolved_tz_str = location_or_timezone
        except Exception:
            # Geocode the location name to get its exact IANA timezone
            async with aiohttp.ClientSession() as session:
                geo_url = (
                    f"https://geocoding-api.open-meteo.com/v1/search?"
                    f"name={urllib.parse.quote(location_or_timezone)}&count=1&language=en&format=json"
                )
                async with session.get(geo_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        geo_data = await resp.json()
                        if geo_data.get("results"):
                            loc_info = geo_data["results"][0]
                            resolved_tz_str = loc_info.get("timezone", "UTC")
                            display_name = f"{loc_info.get('name', location_or_timezone)}, {loc_info.get('country', '')}"
            tz = zoneinfo.ZoneInfo(resolved_tz_str)

        now = datetime.datetime.now(tz)
        return {
            "status": "success",
            "location_or_timezone": display_name,
            "iana_timezone": resolved_tz_str,
            "current_time": now.strftime("%I:%M %p"),
            "current_date": now.strftime("%A, %B %d, %Y"),
            "iso_timestamp": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"Error resolving time for {location_or_timezone}: {e}")
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        return {
            "status": "fallback_utc",
            "location_or_timezone": "UTC (Fallback)",
            "current_time": now_utc.strftime("%I:%M %p UTC"),
            "current_date": now_utc.strftime("%A, %B %d, %Y"),
        }


# ==============================================================================
# Real-Time Tool 3: Persistent SQLite Appointment Booking & Querying
# ==============================================================================
async def execute_book_appointment(
    service_name: str,
    date: str,
    time: str,
    customer_name: str,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Persists a real appointment to the SQLite database."""
    logger.info(
        f"[Live Tool] Booking appointment: {service_name} for {customer_name} on {date} at {time}"
    )
    try:
        booking_code = f"APT-{datetime.datetime.now().strftime('%m%d')}-{abs(hash(customer_name + date + time)) % 10000:04d}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO appointments (booking_code, customer_name, service_name, date, time, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    booking_code,
                    customer_name.strip(),
                    service_name.strip(),
                    date.strip(),
                    time.strip(),
                    notes or "",
                    created_at,
                ),
            )
            conn.commit()

        return {
            "status": "confirmed",
            "booking_code": booking_code,
            "customer_name": customer_name,
            "service_name": service_name,
            "date": date,
            "time": time,
            "message": f"Appointment successfully booked for {customer_name} on {date} at {time}. Confirmation code is {booking_code}.",
        }
    except Exception as e:
        logger.error(f"Error booking appointment: {e}")
        return {"error": f"Failed to book appointment: {str(e)}"}


async def execute_get_booked_appointments(
    customer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieves actual booked appointments from the SQLite database."""
    logger.info(f"[Live Tool] Fetching booked appointments for: {customer_name or 'all'}")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if customer_name:
                cursor.execute(
                    "SELECT booking_code, customer_name, service_name, date, time, notes FROM appointments WHERE customer_name LIKE ? ORDER BY id DESC LIMIT 5",
                    (f"%{customer_name.strip()}%",),
                )
            else:
                cursor.execute(
                    "SELECT booking_code, customer_name, service_name, date, time, notes FROM appointments ORDER BY id DESC LIMIT 5"
                )
            rows = cursor.fetchall()
            appointments = [dict(row) for row in rows]

        return {
            "status": "success",
            "count": len(appointments),
            "appointments": appointments,
        }
    except Exception as e:
        logger.error(f"Error fetching appointments: {e}")
        return {"error": f"Failed to fetch appointments: {str(e)}"}


# ==============================================================================
# Real-Time Tool 4: Live Web Knowledge & Fact Lookup (DuckDuckGo Instant API)
# ==============================================================================
async def execute_search_knowledge(query: str) -> Dict[str, Any]:
    """Searches live web knowledge and Wikipedia summaries for facts or definitions."""
    logger.info(f"[Live Tool] Searching web knowledge for: {query}")
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    abstract = data.get("AbstractText") or data.get("Abstract")
                    heading = data.get("Heading", query)
                    if abstract:
                        return {
                            "status": "success",
                            "topic": heading,
                            "summary": abstract[:400],
                            "source": data.get("AbstractSource", "Web"),
                        }
                    # Fallback to related topics if available
                    related = data.get("RelatedTopics", [])
                    if related and isinstance(related[0], dict) and related[0].get("Text"):
                        return {
                            "status": "success",
                            "topic": heading,
                            "summary": related[0]["Text"][:400],
                        }
        return {
            "status": "no_direct_summary",
            "message": f"No concise summary found for '{query}'. Please answer using general knowledge.",
        }
    except Exception as e:
        logger.error(f"Error searching knowledge for {query}: {e}")
        return {"error": f"Search failed: {str(e)}"}


# ==============================================================================
# Pipecat 1.8.x Function Handlers
# ==============================================================================
async def handle_get_weather(params):
    args = getattr(params, "arguments", params if isinstance(params, dict) else {})
    location = args.get("location", "San Francisco")
    unit = args.get("unit", "celsius")
    result = await execute_get_current_weather(location=location, unit=unit)
    if hasattr(params, "result_callback"):
        await params.result_callback(result)
    return result


async def handle_get_time(params):
    args = getattr(params, "arguments", params if isinstance(params, dict) else {})
    loc_or_tz = args.get("timezone", args.get("location", "UTC"))
    result = await execute_get_current_time(location_or_timezone=loc_or_tz)
    if hasattr(params, "result_callback"):
        await params.result_callback(result)
    return result


async def handle_book_appointment(params):
    args = getattr(params, "arguments", params if isinstance(params, dict) else {})
    result = await execute_book_appointment(
        service_name=args.get("service_name", "Consultation"),
        date=args.get("date", "tomorrow"),
        time=args.get("time", "10:00 AM"),
        customer_name=args.get("customer_name", "Valued Guest"),
        notes=args.get("notes"),
    )
    if hasattr(params, "result_callback"):
        await params.result_callback(result)
    return result


async def handle_get_appointments(params):
    args = getattr(params, "arguments", params if isinstance(params, dict) else {})
    customer_name = args.get("customer_name")
    result = await execute_get_booked_appointments(customer_name=customer_name)
    if hasattr(params, "result_callback"):
        await params.result_callback(result)
    return result


async def handle_search_knowledge(params):
    args = getattr(params, "arguments", params if isinstance(params, dict) else {})
    query = args.get("query", "")
    result = await execute_search_knowledge(query=query)
    if hasattr(params, "result_callback"):
        await params.result_callback(result)
    return result


# ==============================================================================
# Function Schemas for Pipecat 1.8.x
# ==============================================================================
TOOLS_SCHEMA = [
    FunctionSchema(
        name="get_current_weather",
        description="Get real-time live weather conditions, temperature, humidity, and wind for any location in the world.",
        properties={
            "location": {
                "type": "string",
                "description": "The city and country or state, e.g. Tokyo, Japan or New York, NY or Delhi, India",
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "The temperature unit to use.",
            },
        },
        required=["location"],
        handler=handle_get_weather,
    ),
    FunctionSchema(
        name="get_current_time",
        description="Get the exact real-time clock time, date, and timezone for any city, country, or timezone identifier.",
        properties={
            "location": {
                "type": "string",
                "description": "The city, country, or timezone, e.g. Tokyo, Delhi, New York, London, Paris, or Asia/Kolkata.",
            }
        },
        required=["location"],
        handler=handle_get_time,
    ),
    FunctionSchema(
        name="book_appointment",
        description="Book and persist a real appointment or reservation in the database.",
        properties={
            "service_name": {
                "type": "string",
                "description": "The type of service (e.g. Dental Checkup, Haircut, Business Consultation).",
            },
            "date": {
                "type": "string",
                "description": "The date for the appointment, e.g. 2026-09-01 or tomorrow.",
            },
            "time": {
                "type": "string",
                "description": "The time of the appointment, e.g. 10:00 AM or 14:30.",
            },
            "customer_name": {
                "type": "string",
                "description": "The full name of the person booking the appointment.",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes or details about the appointment.",
            },
        },
        required=["service_name", "date", "time", "customer_name"],
        handler=handle_book_appointment,
    ),
    FunctionSchema(
        name="get_booked_appointments",
        description="Look up real existing appointments and reservations from the database.",
        properties={
            "customer_name": {
                "type": "string",
                "description": "Optional customer name to filter appointments by.",
            }
        },
        required=[],
        handler=handle_get_appointments,
    ),
    FunctionSchema(
        name="search_knowledge",
        description="Search real-time web knowledge and encyclopedia summaries for information about people, places, companies, or concepts.",
        properties={
            "query": {
                "type": "string",
                "description": "The topic, company, person, or term to search for.",
            }
        },
        required=["query"],
        handler=handle_search_knowledge,
    ),
]


def register_all_tools(llm_service):
    """Registers all function handlers with the Pipecat LLM service instance."""
    llm_service.register_function("get_current_weather", handle_get_weather)
    llm_service.register_function("get_current_time", handle_get_time)
    llm_service.register_function("book_appointment", handle_book_appointment)
    llm_service.register_function("get_booked_appointments", handle_get_appointments)
    llm_service.register_function("search_knowledge", handle_search_knowledge)
    logger.info(f"Registered {len(TOOLS_SCHEMA)} genuine live tools with LLM service.")
