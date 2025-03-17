#!/usr/bin/env python3

import json
import math
import time
import threading
import requests
import logging
import queue

from datetime import datetime, timedelta

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.events import Events, EventName, Alert, AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert

################################################################################
# Constants and Alert Mappings
################################################################################

WAZE_API_UPDATE_INTERVAL = 120  # Update every 2 minutes
BOUNDING_BOX_WIDTH = 0.07       # About 5-7 km depending on latitude
ALERT_DISTANCE_THRESHOLD = 200  # Meters
CHECK_ALERTS_INTERVAL = 1.0     # Check for alerts every 1 second

# Custom Waze alert event name -- this must be declared in events.py
WAZE_EVENT_NAME = EventName.wazeAlert  # (Requires definition in events.py)

# Example alert types for Waze; you may add more if needed
WAZE_ALERT_HAZARD = 10
WAZE_ALERT_JAM = 11
WAZE_ALERT_ACCIDENT = 12
WAZE_ALERT_POLICE = 13
WAZE_ALERT_ROAD_CLOSED = 14
WAZE_ALERT_SPEED_CAMERA = 15
WAZE_ALERT_REDLIGHT_CAMERA = 16

# Mock alert for testing
MOCK_ALERT = {
  "type": "POLICE",
  "location": {
    "x": -117.191503,  # longitude
    "y": 32.746869     # latitude
  },
  "street": "I-5 South",
  "reportDescription": "Mock police alert for testing purposes",
  "uuid": "mock-test-alert-12345",
  "subtype": ""
}

# Global state
alerts_cache = [MOCK_ALERT]
last_api_call_time = 0
last_alerted_uuids = set()
api_is_busy = False

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wazed")

################################################################################
# Utility Functions
################################################################################

def haversine_distance(lat1, lon1, lat2, lon2):
  """Compute distance in meters between two lat/lon points on Earth."""
  lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
  dlon = lon2 - lon1
  dlat = lat2 - lat1
  a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
  c = 2 * math.asin(math.sqrt(a))
  return 6371000 * c  # Earth radius in meters

def is_approaching(car_lat, car_lon, car_bearing, alert_lat, alert_lon,
                   threshold=ALERT_DISTANCE_THRESHOLD):
  """
  Return (approaching_bool, distance).
  approaching_bool is True if we are within 'threshold' distance and heading roughly toward alert.
  """
  distance = haversine_distance(car_lat, car_lon, alert_lat, alert_lon)
  if distance > threshold:
    return False, distance

  lat1, lon1, lat2, lon2 = map(math.radians, [car_lat, car_lon, alert_lat, alert_lon])
  dlon = lon2 - lon1
  y = math.sin(dlon) * math.cos(lat2)
  x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
  bearing_to_alert = math.degrees(math.atan2(y, x)) % 360

  angle_diff = min(
    abs(bearing_to_alert - car_bearing),
    abs(bearing_to_alert - car_bearing + 360),
    abs(bearing_to_alert - car_bearing - 360)
  )

  return (angle_diff < 90 and distance <= threshold), distance

def fetch_waze_alerts(lat, lon):
  """
  Fetch alerts from the Waze API, stored in global 'alerts_cache'.
  Rate-limited by WAZE_API_UPDATE_INTERVAL, concurrency-limited by 'api_is_busy'.
  """
  global last_api_call_time, alerts_cache, api_is_busy

  current_time = time.time()
  if current_time - last_api_call_time < WAZE_API_UPDATE_INTERVAL or api_is_busy:
    return alerts_cache

  api_is_busy = True

  half_width = BOUNDING_BOX_WIDTH / 2
  top = lat + half_width
  bottom = lat - half_width
  left = lon - half_width
  right = lon + half_width

  url = (f"https://www.waze.com/live-map/api/georss?"
         f"top={top}&bottom={bottom}&left={left}&right={right}&env=row&types=alerts")

  try:
    logger.info(f"Wazed: Making API request to {url}")
    last_api_call_time = current_time
    response = requests.get(url, timeout=10)
    logger.info(f"Wazed: Response status: {response.status_code}")

    if response.status_code == 200:
      try:
        data = response.json()
        if "alerts" in data:
          alerts_cache = data["alerts"]
          cloudlog.info(f"Wazed: {len(alerts_cache)} alerts from Waze (plus mock).")
          logger.info(f"Fetched {len(alerts_cache)} alerts from Waze API.")
        else:
          cloudlog.warning("Wazed: No 'alerts' field in response.")
          logger.warning("No 'alerts' field in Waze API response.")
      except json.JSONDecodeError as je:
        cloudlog.error(f"Wazed: JSON parse error: {je}")
        logger.error(f"JSON parse error: {je}")
    else:
      cloudlog.warning(f"Wazed: Non-200 response: {response.status_code}")
      logger.warning(f"Non-200 response from Waze: {response.status_code}")

  except Exception as e:
    cloudlog.error(f"Wazed: API error: {e}")
    logger.error(f"Error fetching Waze alerts: {e}")

  finally:
    api_is_busy = False

  # Ensure mock alert is present
  if not alerts_cache:
    alerts_cache = [MOCK_ALERT]
    logger.info("Wazed: Using only mock POLICE alert.")
  elif MOCK_ALERT not in alerts_cache:
    alerts_cache.append(MOCK_ALERT)
    logger.info("Wazed: Added mock POLICE alert to the list.")

  return alerts_cache

def get_alert_text(alert):
  """Return a (title, text) tuple for the alert."""
  alert_type = alert.get("type", "UNKNOWN")
  street = alert.get("street", "")
  description = alert.get("reportDescription", "")

  title_map = {
    "ACCIDENT": "Accident Ahead",
    "JAM": "Traffic Jam Ahead",
    "POLICE": "Police Ahead",
    "HAZARD": "Hazard Ahead",
    "ROAD_CLOSED": "Road Closed Ahead"
  }
  title = title_map.get(alert_type, f"{alert_type} Ahead")

  # Build text
  text = ""
  if street:
    text += f"On {street}"
  if description:
    if len(description) > 100:
      description = description[:97] + "..."
    text += f"\n{description}"

  return title, text

def get_waze_alert_sound(alert):
  """
  Return an integer representing which sound to play, if any.
  Not directly used by the openpilot UI (which might use the event's audibleAlert).
  """
  alert_type = alert.get("type", "UNKNOWN")
  subtype = alert.get("subtype", "")

  if alert_type == "ACCIDENT":
    return WAZE_ALERT_ACCIDENT
  elif alert_type == "POLICE":
    return WAZE_ALERT_POLICE
  elif alert_type == "HAZARD":
    return WAZE_ALERT_HAZARD
  elif alert_type == "JAM":
    return WAZE_ALERT_JAM
  elif alert_type == "ROAD_CLOSED":
    return WAZE_ALERT_ROAD_CLOSED
  elif subtype == "SPEED_CAMERA":
    return WAZE_ALERT_SPEED_CAMERA
  elif subtype == "REDLIGHT_CAMERA":
    return WAZE_ALERT_REDLIGHT_CAMERA
  else:
    return 0

################################################################################
# Main Waze Monitoring
################################################################################

# We'll queue GPS data from openpilot, or we can simulate it if needed
gps_queue = queue.Queue()

class WazedMonitor:
  """
  The main class that receives GPS updates, fetches Waze alerts,
  and adds an event if user is approaching a hazard. The event will
  be handled by openpilot's UI. We rely on self.events to push a
  custom event (EventName.wazeAlert).
  """
  def __init__(self):
    self.current_lat = 0.0
    self.current_lon = 0.0
    self.bearing = 0.0
    self.events = Events()
    self.frame = 0

  def update(self):
    """Call this periodically to process new GPS and check for approaching alerts."""
    self.frame += 1
    # Non-blocking attempt to get latest GPS data
    while not gps_queue.empty():
      gps_data = gps_queue.get_nowait()
      self.current_lat = gps_data['latitude']
      self.current_lon = gps_data['longitude']
      self.bearing = gps_data['bearing']

    # Only do checks at about 1 Hz
    if self.frame % 10 == 0:
      self.check_alerts()

  def check_alerts(self):
    """
    If we're approaching an alert we haven't alerted for, add the wazeAlert event.
    The actual text/visual is defined in events.py / onroad UI code.
    """
    if abs(self.current_lat) < 0.0001 and abs(self.current_lon) < 0.0001:
      return

    for alert in alerts_cache:
      if 'location' not in alert or 'x' not in alert['location'] or 'y' not in alert['location']:
        continue

      alert_lat = alert['location']['y']
      alert_lon = alert['location']['x']
      alert_uuid = alert.get('uuid', '')

      if alert_lat == 0.0 and alert_lon == 0.0:
        continue

      try:
        is_ahead, distance = is_approaching(
          self.current_lat, self.current_lon, self.bearing, alert_lat, alert_lon
        )
        if is_ahead and alert_uuid not in last_alerted_uuids:
          # We'll add the event, so the UI can display it
          title, text = get_alert_text(alert)
          # Example of how you might define a custom event
          # We'll rely on events.py to define exactly how it displays.

          # Clear existing events to ensure no duplication
          self.events.clear()
          self.events.add(WAZE_EVENT_NAME)

          # Log it
          logger.warning(f"Wazed: TRIGGER ALERT - {title}, Dist={distance:.1f}m, uuid={alert_uuid}")
          cloudlog.warning(f"Wazed: TRIGGER ALERT - {title}, Dist={distance:.1f}m, uuid={alert_uuid}")

          # Mark as alerted so we don't spam the same alert
          last_alerted_uuids.add(alert_uuid)
          if len(last_alerted_uuids) > 50:
            last_alerted_uuids.pop()

          # The UI code in alerts.cc or QML can fetch the necessary data from CarState or custom structs
          # For now, we just rely on the event alone (which must be configured)
      except Exception as e:
        logger.exception(f"Wazed: Error checking alert: {e}")


def waze_fetch_thread():
  """
  A background thread that:

  • Reads real or simulated GPS from messaging (or a queue).
  • Periodically calls fetch_waze_alerts().
  """
  while True:
    try:
      # If we have some GPS data, fetch the alerts if the interval is up
      if not gps_queue.empty():
        gps_data = gps_queue.queue[-1]  # look at the most recent
        lat = gps_data['latitude']
        lon = gps_data['longitude']
        fetch_waze_alerts(lat, lon)
    except Exception as e:
      logger.exception(f"Wazed: Error in fetch thread: {e}")
    time.sleep(1.0)


################################################################################
# openpilot integration
################################################################################

def main():
  """
  Main entry point.
  1) Start a background thread that fetches Waze alerts periodically.
  2) Read GPS from openpilot's messaging or other source.
  3) Initiate WazedMonitor to trigger events as needed.
  4) Publish events so openpilot's UI can handle them.
  """
  logger.info("Wazed: Starting Waze extension with openpilot events")

  # The real openpilot approach: read GPS from a SubMaster
  sm = messaging.SubMaster(['gpsLocationExternal'])

  # Start the Waze fetch thread
  t = threading.Thread(target=waze_fetch_thread, daemon=True)
  t.start()

  # Create monitor
  monitor = WazedMonitor()

  # Loop updating
  while True:
    sm.update()

    if sm.updated['gpsLocationExternal']:
      gps = sm['gpsLocationExternal']
      lat = gps.latitude
      lon = gps.longitude
      bearing = gps.bearingDeg if gps.bearingDeg > 0.0 else 0.0

      # push to queue for the monitor
      gps_data = {
        'latitude': lat,
        'longitude': lon,
        'bearing': bearing,
        'timestamp': time.time()
      }
      gps_queue.put(gps_data)

    # Monitor checks alerts, triggers events
    monitor.update()

    # If there's a wazeAlert event, do something with it (log, etc.)
    # The actual UI display is handled in onroad code (C++ or QML).
    # We'll show a quick example of how you might publish an event log:
    if monitor.events.contains(WAZE_EVENT_NAME):
      # For demonstration, just log it. Real use: add logic that onroad code can read.
      logger.info("Wazed: wazeAlert event triggered!")
      # The UI onroad code (alerts.cc or QML) must be updated to display this event.
      # Clear it after reading or it stays active
      monitor.events.clear()

    time.sleep(0.1)

if __name__ == "__main__":
  main()
