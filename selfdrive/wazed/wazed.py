#!/usr/bin/env python3
import json
import math
import time
import threading
import requests
import logging
from datetime import datetime, timedelta

import cereal.messaging as messaging
from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.events import Alert, AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert, EventName, Events

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wazed")

# Constants for the Waze alerts module
WAZE_API_UPDATE_INTERVAL = 120  # Update every 2 minutes (in seconds)
BOUNDING_BOX_WIDTH = 0.07  # About 5-7 km depending on latitude
ALERT_DISTANCE_THRESHOLD = 200  # Meters, distance to trigger alert
CHECK_ALERTS_INTERVAL = 1.0  # Check for alerts every 1 second

# Add a mock alert for testing
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

# Global variables - Initialize with mock alert to make it immediately available
alerts_cache = [MOCK_ALERT]
last_api_call_time = 0
last_alerted_uuids = set()  # To prevent showing the same alert multiple times in succession
api_is_busy = False  # Flag to prevent concurrent API calls

def haversine_distance(lat1, lon1, lat2, lon2):
  """Calculate the great circle distance between two points on the earth."""
  # Convert decimal degrees to radians
  lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

  # Haversine formula
  dlon = lon2 - lon1
  dlat = lat2 - lat1
  a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
  c = 2 * math.asin(math.sqrt(a))
  r = 6371000  # Radius of Earth in meters
  return c * r

def is_approaching(car_lat, car_lon, car_bearing, alert_lat, alert_lon, threshold=ALERT_DISTANCE_THRESHOLD):
  """Determine if car is approaching the alert within the threshold distance."""
  distance = haversine_distance(car_lat, car_lon, alert_lat, alert_lon)

  # If we're already too far, return False immediately
  if distance > threshold:
    return False, distance

  # Calculate bearing to alert
  lat1, lon1, lat2, lon2 = map(math.radians, [car_lat, car_lon, alert_lat, alert_lon])
  dlon = lon2 - lon1
  y = math.sin(dlon) * math.cos(lat2)
  x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
  bearing_to_alert = math.degrees(math.atan2(y, x)) % 360

  # Check if we're heading toward the alert (within 90 degrees)
  angle_diff = min(abs(bearing_to_alert - car_bearing), abs(bearing_to_alert - car_bearing + 360),
                  abs(bearing_to_alert - car_bearing - 360))

  return angle_diff < 90 and distance <= threshold, distance

def fetch_waze_alerts(lat, lon):
  """Fetch alerts from the Waze API using the specified coordinates as the center of the bounding box."""
  global last_api_call_time, alerts_cache, api_is_busy

  current_time = time.time()

  # Only update once every WAZE_API_UPDATE_INTERVAL and ensure we're not already making an API call
  if current_time - last_api_call_time < WAZE_API_UPDATE_INTERVAL or api_is_busy:
    return alerts_cache  # Return cache even if empty (will be an empty list, not None)

  api_is_busy = True  # Set flag to prevent concurrent API calls

  # Calculate bounding box
  half_width = BOUNDING_BOX_WIDTH / 2
  top = lat + half_width
  bottom = lat - half_width
  left = lon - half_width
  right = lon + half_width

  # Construct the API URL
  url = f"https://www.waze.com/live-map/api/georss?top={top}&bottom={bottom}&left={left}&right={right}&env=row&types=alerts"

  try:
    logger.info(f"Wazed: Making API request to {url}")
    last_api_call_time = current_time
    response = requests.get(url, timeout=10)
    logger.info(f"Wazed: Response status: {response.status_code}")

    if response.status_code == 200:
      response_text = response.text
      logger.info(f"Wazed: Response content: {response_text[:500]}..." if len(response_text) > 500 else response_text)

      try:
        data = response.json()
        if "alerts" in data:
          # Get real alerts from API
          alerts_cache = data["alerts"]
          cloudlog.info(f"Wazed: Fetched {len(alerts_cache)} alerts from Waze API (including mock alert)")
          logger.info(f"Fetched {len(alerts_cache)} alerts from Waze API (including mock alert)")
        else:
          cloudlog.warning(f"Wazed: No alerts field in Waze API response. Response keys: {data.keys()}")
          logger.warning(f"No alerts field in Waze API response. Response keys: {data.keys()}")
      except json.JSONDecodeError as je:
        cloudlog.error(f"Wazed: JSON parsing error: {je}")
        logger.error(f"JSON parsing error: {je}")
    else:
      cloudlog.warning(f"Wazed: Non-200 response from API: {response.status_code}")
      logger.warning(f"Non-200 response from API: {response.status_code}")
  except Exception as e:
    cloudlog.error(f"Wazed: Error fetching Waze alerts: {e}")
    logger.error(f"Error fetching Waze alerts: {e}")
  finally:
    api_is_busy = False  # Clear flag regardless of success/failure

  # Make sure the mock alert is always in the cache, even if API response was empty
  if not alerts_cache:
    alerts_cache = [MOCK_ALERT]
    logger.info("No alerts from API, using only mock POLICE alert for testing")
  elif MOCK_ALERT not in alerts_cache:
    alerts_cache.append(MOCK_ALERT)
    logger.info("Added mock POLICE alert to the cache after API error")

  # Return cached data
  return alerts_cache

def get_alert_text(alert):
  """Generate alert text based on the Waze alert data."""
  alert_type = alert.get("type", "UNKNOWN")
  street = alert.get("street", "")
  description = alert.get("reportDescription", "")

  # Format title based on alert type
  title_by_type = {
    "ACCIDENT": "Accident Ahead",
    "JAM": "Traffic Jam Ahead",
    "POLICE": "Police Ahead",
    "HAZARD": "Hazard Ahead",
    "ROAD_CLOSED": "Road Closed Ahead"
  }

  title = title_by_type.get(alert_type, f"{alert_type} Ahead")

  # Format the description
  text = ""
  if street:
    text += f"On {street}"

  if description and len(description) > 0:
    # Truncate description if too long
    if len(description) > 100:
      description = description[:97] + "..."
    text += f"\n{description}"

  return title, text

def wazed_thread():
  """Background thread to fetch Waze alerts."""
  global alerts_cache

  pm = messaging.PubMaster(['wazeAlerts'])
  sm = messaging.SubMaster(['gpsLocationExternal'])

  # For tracking position changes
  current_lat = 0.0
  current_lon = 0.0
  current_bearing = 0.0

  # For periodic GPS logging
  last_gps_log_time = 0
  GPS_LOG_INTERVAL = 60  # Log GPS position every minute

  while True:
    sm.update()

    if sm.updated['gpsLocationExternal']:
      gps = sm['gpsLocationExternal']

      # Get the car's position and bearing
      current_lat = gps.latitude
      current_lon = gps.longitude
      current_bearing = gps.bearingDeg if gps.bearingDeg > 0.0 else 0.0  # Use GPS bearing when available

      # Log GPS position periodically
      current_time = time.time()
      if current_time - last_gps_log_time > GPS_LOG_INTERVAL:
        cloudlog.info(f"Wazed: Current position: lat={current_lat:.6f}, lon={current_lon:.6f}, bearing={current_bearing:.1f}°")
        logger.info(f"Current position: lat={current_lat:.6f}, lon={current_lon:.6f}, bearing={current_bearing:.1f}°")
        last_gps_log_time = current_time

      # Only attempt to fetch Waze alerts every WAZE_API_UPDATE_INTERVAL
      elapsed_since_last_call = current_time - last_api_call_time
      if elapsed_since_last_call >= WAZE_API_UPDATE_INTERVAL and not api_is_busy:
        # Fetch Waze alerts
        waze_alerts = fetch_waze_alerts(current_lat, current_lon)
      else:
        waze_alerts = alerts_cache
      if waze_alerts:
        # Send the alerts to our subscribers
        waze_alert_msg = messaging.new_message('wazeAlerts')
        waze_alert_msg.wazeAlerts.position.latitude = current_lat
        waze_alert_msg.wazeAlerts.position.longitude = current_lon
        waze_alert_msg.wazeAlerts.bearing = current_bearing

        alerts_to_check = []
        for alert in waze_alerts:
          if 'location' in alert and 'x' in alert['location'] and 'y' in alert['location']:
            alert_lat = alert['location']['y']
            alert_lon = alert['location']['x']
            is_ahead, distance = is_approaching(current_lat, current_lon, current_bearing, alert_lat, alert_lon)

            if is_ahead:
              alert_data = {
                'uuid': alert.get('uuid', ''),
                'type': alert.get('type', 'UNKNOWN'),
                'latitude': alert_lat,
                'longitude': alert_lon,
                'distance': distance,
                'street': alert.get('street', ''),
                'description': alert.get('reportDescription', ''),
                'subtype': alert.get('subtype', '')
              }
              alerts_to_check.append(alert_data)

        waze_alert_msg.wazeAlerts.alertsCount = len(alerts_to_check)
        pm.send('wazeAlerts', waze_alert_msg)

    time.sleep(CHECK_ALERTS_INTERVAL)

class WazedMonitor:
  def __init__(self):
    self.sm = messaging.SubMaster(['wazeAlerts'])
    self.pm = messaging.PubMaster(['controlsState'])
    self.events = Events()
    self.frame = 0
    self.current_lat = 0.0
    self.current_lon = 0.0
    self.last_fetch_time = 0
    self.fetch_interval = 1.0

  def update(self):
    """Main update loop."""
    self.frame += 1
    self.sm.update()

    if self.sm.updated['wazeAlerts']:
      # Get the car's position
      self.current_lat = self.sm['wazeAlerts'].position.latitude
      self.current_lon = self.sm['wazeAlerts'].position.longitude
      self.bearing = self.sm['wazeAlerts'].bearing

    current_time = time.time()
    if current_time - self.last_fetch_time >= self.fetch_interval:
      print(f"\nTime to fetch alerts (last fetch was {current_time - self.last_fetch_time:.1f}s ago)")
      self.check_alerts()
      self.last_fetch_time = current_time

    # Process events and create alerts
    alerts = self.events.create_alerts(['warning'])
    if alerts:
      cs = messaging.new_message('controlsState')
      cs.valid = True
      alert = alerts[0]
      cs.controlsState = {
        'alertText1': alert.alert_text_1,
        'alertText2': alert.alert_text_2,
        'alertSize': alert.alert_size,
        'alertStatus': alert.alert_status,
        'alertBlinkingRate': alert.alert_rate,
        'alertType': alert.alert_type,
        'alertSound': alert.audible_alert,
        'enabled': True,
        'active': True,
        'cumLagMs': 0.0
      }
      self.pm.send('controlsState', cs)

    # Clear events for next iteration
    self.events.clear()

  def check_alerts(self):
    """Check each alert in the cache and add events if needed."""
    # Check each alert in the cache
    for alert in alerts_cache:
      if 'location' not in alert or 'x' not in alert['location'] or 'y' not in alert['location']:
        continue

      alert_lat = alert['location']['y']
      alert_lon = alert['location']['x']
      alert_uuid = alert.get('uuid', '')

      is_ahead, distance = is_approaching(self.current_lat, self.current_lon, self.bearing,
                                         alert_lat, alert_lon)

      # If we're approaching this alert and haven't alerted about it recently
      if is_ahead and alert_uuid not in last_alerted_uuids:
        self.events.add(EventName.wazeAlert)

        # Custom title/text for this specific alert
        title, text = get_alert_text(alert)
        alert_type = alert.get("type", "UNKNOWN")

        # Add to alerted set to prevent repeat alerts
        last_alerted_uuids.add(alert_uuid)

        # Print alert info
        alert_message = f"{title} - {text}"
        cloudlog.warning(f"Wazed: ALERT TRIGGERED: {alert_message} - Distance: {distance:.1f}m - Type: {alert_type}")
        logger.warning(f"ALERT TRIGGERED: {alert_message} - Distance: {distance:.1f}m - Type: {alert_type}")
        print(f"WAZE ALERT: {alert_message}")

        # Clean up old UUIDs occasionally (keep max 20)
        if len(last_alerted_uuids) > 20:
          last_alerted_uuids.pop()

def main():
  logger.info("Wazed: Starting Waze Alerts extension")

  # Start the background thread for fetching alerts
  wazed_fetch_thread = threading.Thread(target=wazed_thread, daemon=True)
  wazed_fetch_thread.start()

  # Start the monitor in the main thread
  monitor = WazedMonitor()
  while True:
    monitor.update()
    time.sleep(0.1)  # Run at 10Hz

if __name__ == "__main__":
  main()
